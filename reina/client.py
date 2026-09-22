"""ComfyUI の HTTP / WebSocket API クライアント。

ローカル (http://127.0.0.1:8188) と RunPod のプロキシ
(https://<POD_ID>-8188.proxy.runpod.net) の両方を同じコードで扱えるよう、
スキーム付きの URL を基準にする。
"""

from __future__ import annotations

import json
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Iterator

import requests
import websocket


class ComfyError(RuntimeError):
    pass


def normalize_url(value: str) -> str:
    """'127.0.0.1:8188' や 'xxx-8188.proxy.runpod.net' を正規の URL にする。

    スキーム省略時は、ローカルアドレスなら http、それ以外は https を補う。
    """
    value = value.strip().rstrip("/")
    if "://" in value:
        return value
    host = value.split(":", 1)[0]
    local = host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.startswith("192.168.")
    return f"{'http' if local else 'https'}://{value}"


class ComfyClient:
    def __init__(
        self,
        url: str,
        client_id: str | None = None,
        timeout: int = 900,
        auth: dict[str, Any] | None = None,
        verify: bool = True,
    ):
        self.base_url = normalize_url(url)
        self.client_id = client_id or str(uuid.uuid4())
        self.timeout = timeout
        self.verify = verify

        parsed = urllib.parse.urlparse(self.base_url)
        self.host = parsed.netloc
        self.ws_url = urllib.parse.urlunparse(
            (("wss" if parsed.scheme == "https" else "ws"), parsed.netloc, parsed.path + "/ws", "", "", "")
        )

        self._object_info_cache: dict[str, dict[str, Any]] = {}
        self.session = requests.Session()
        self.session.verify = verify
        auth = auth or {}
        if auth.get("bearer"):
            self.session.headers["Authorization"] = f"Bearer {auth['bearer']}"
        if auth.get("username"):
            self.session.auth = (auth["username"], auth.get("password", ""))

    # -- 基本 ---------------------------------------------------------------

    def ping(self) -> dict[str, Any]:
        """/system_stats を叩いて疎通と GPU 情報を返す。"""
        try:
            resp = self.session.get(f"{self.base_url}/system_stats", timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ComfyError(f"ComfyUI に接続できません ({self.base_url}): {exc}") from exc
        try:
            return resp.json()
        except ValueError as exc:
            raise ComfyError(
                f"{self.base_url} は ComfyUI ではないようです（JSON が返りません）。"
                "RunPod ならポート 8188 の Proxy URL か、Pod がまだ起動中でないか確認してください。"
            ) from exc

    def object_info(self, node_class: str | None = None) -> dict[str, Any]:
        """利用可能なノード定義。カスタムノードの有無確認に使う。"""
        if node_class is not None and node_class in self._object_info_cache:
            return self._object_info_cache[node_class]
        url = f"{self.base_url}/object_info"
        if node_class:
            url += f"/{urllib.parse.quote(node_class)}"
        resp = self.session.get(url, timeout=60)
        if resp.status_code == 404:
            info: dict[str, Any] = {}
        else:
            resp.raise_for_status()
            info = resp.json()
        if node_class is not None:
            self._object_info_cache[node_class] = info
        return info

    def fill_missing_inputs(self, graph: dict[str, Any]) -> list[str]:
        """未設定の必須入力を、ComfyUI が持つ既定値で埋める。

        ComfyUI の更新でノードに必須入力が増えると
        「Required input is missing」で投入が弾かれる。
        接続が必要な入力（MODEL/CONDITIONING など）は既定値を持たないので触らない。
        """
        filled: list[str] = []
        for node in graph.values():
            node_class = node.get("class_type")
            if not node_class:
                continue
            spec = (
                self.object_info(node_class)
                .get(node_class, {})
                .get("input", {})
                .get("required", {})
            )
            inputs = node.setdefault("inputs", {})
            for name, entry in spec.items():
                if name in inputs or not isinstance(entry, list) or not entry:
                    continue
                kind = entry[0]
                options = entry[1] if len(entry) > 1 and isinstance(entry[1], dict) else {}
                if isinstance(kind, list):  # 選択肢型
                    value = options.get("default", kind[0] if kind else None)
                else:
                    value = options.get("default")
                if value is None:  # 既定値なし = 他ノードからの接続が必要な入力
                    continue
                inputs[name] = value
                filled.append(f"{node_class}.{name}={value}")
        return filled

    def has_node(self, node_class: str) -> bool:
        return bool(self.object_info(node_class))

    def node_options(self, node_class: str, input_name: str) -> list[str]:
        """ノードの選択肢一覧を返す。

        ComfyUI が実際に受け付ける値そのものなので、models/ のフォルダ名を
        推測するより確実（例: GGUF は標準の unet フォルダには出てこない）。
        """
        spec = self.object_info(node_class).get(node_class, {}).get("input", {})
        for section in ("required", "optional"):
            entry = spec.get(section, {}).get(input_name)
            if isinstance(entry, list) and entry and isinstance(entry[0], list):
                return [str(v) for v in entry[0]]
        return []

    def model_list(self, folder: str) -> list[str]:
        """models/<folder> に置かれているファイル一覧（ComfyUI が認識しているもの）。"""
        resp = self.session.get(
            f"{self.base_url}/models/{urllib.parse.quote(folder)}", timeout=60
        )
        if resp.status_code != 200:
            return []
        try:
            return list(resp.json())
        except ValueError:
            return []

    # -- 入力画像 -----------------------------------------------------------

    def upload_image(self, path: str | Path, subfolder: str = "reina", overwrite: bool = True) -> str:
        """参照画像を ComfyUI の input/ に送り、LoadImage で使える名前を返す。"""
        path = Path(path)
        if not path.exists():
            raise ComfyError(f"参照画像が見つかりません: {path}")
        with path.open("rb") as fh:
            resp = self.session.post(
                f"{self.base_url}/upload/image",
                files={"image": (path.name, fh, "application/octet-stream")},
                data={"overwrite": str(overwrite).lower(), "subfolder": subfolder},
                timeout=300,
            )
        resp.raise_for_status()
        info = resp.json()
        sub = info.get("subfolder") or ""
        name = info["name"]
        return f"{sub}/{name}" if sub else name

    # -- 実行 ---------------------------------------------------------------

    def queue_prompt(self, workflow: dict[str, Any]) -> str:
        payload = {"prompt": workflow, "client_id": self.client_id}
        resp = self.session.post(f"{self.base_url}/prompt", json=payload, timeout=120)
        if resp.status_code != 200:
            raise ComfyError(f"ワークフロー投入に失敗しました: {resp.status_code} {resp.text[:2000]}")
        return resp.json()["prompt_id"]

    def wait(self, prompt_id: str, on_progress=None) -> None:
        """完了まで待つ。WebSocket が張れない環境では /history ポーリングに落ちる。"""
        try:
            self._wait_ws(prompt_id, on_progress)
        except ComfyError:
            raise
        except Exception as exc:  # noqa: BLE001 - WS が使えない環境向けフォールバック
            self._wait_poll(prompt_id, note=str(exc), on_progress=on_progress)

    def _wait_ws(self, prompt_id: str, on_progress=None) -> None:
        ws = websocket.WebSocket()
        ws.connect(
            f"{self.ws_url}?clientId={self.client_id}",
            timeout=self.timeout,
            sslopt=None if self.verify else {"cert_reqs": 0},
        )
        deadline = time.time() + self.timeout
        try:
            while True:
                if time.time() > deadline:
                    raise ComfyError(f"タイムアウト ({self.timeout}s): prompt_id={prompt_id}")
                message = ws.recv()
                if not isinstance(message, str):
                    continue  # プレビュー画像のバイナリフレーム
                data = json.loads(message)
                mtype, payload = data.get("type"), data.get("data", {})
                if payload.get("prompt_id") not in (None, prompt_id):
                    continue
                if mtype == "progress" and on_progress:
                    on_progress(payload.get("value", 0), payload.get("max", 0))
                elif mtype == "execution_error":
                    raise ComfyError(
                        "ComfyUI 実行エラー: "
                        f"{payload.get('node_type')} / {payload.get('exception_message')}"
                    )
                elif mtype == "executing" and payload.get("node") is None:
                    return
        finally:
            ws.close()

    def _wait_poll(
        self, prompt_id: str, interval: float = 3.0, note: str = "", on_progress=None
    ) -> None:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if on_progress:
                # ステップ数は取れないので、経過時間だけ更新させる
                on_progress(0, 0)
            hist = self.history(prompt_id)
            status = hist.get("status") or {}
            if status.get("completed") or hist.get("outputs"):
                return
            if status.get("status_str") == "error":
                messages = status.get("messages") or []
                raise ComfyError(f"ComfyUI 実行エラー: {messages[-1] if messages else 'unknown'}")
            time.sleep(interval)
        suffix = f" (WebSocket 接続不可: {note})" if note else ""
        raise ComfyError(f"タイムアウト ({self.timeout}s): prompt_id={prompt_id}{suffix}")

    def history(self, prompt_id: str) -> dict[str, Any]:
        resp = self.session.get(f"{self.base_url}/history/{prompt_id}", timeout=60)
        resp.raise_for_status()
        return resp.json().get(prompt_id, {})

    def outputs(self, prompt_id: str) -> Iterator[tuple[str, bytes]]:
        """完了済みプロンプトの画像を (ファイル名, バイト列) で返す。"""
        hist = self.history(prompt_id)
        for node_output in hist.get("outputs", {}).values():
            for image in node_output.get("images", []):
                if image.get("type") == "temp":
                    continue
                params = {
                    "filename": image["filename"],
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                }
                resp = self.session.get(f"{self.base_url}/view", params=params, timeout=300)
                resp.raise_for_status()
                yield image["filename"], resp.content

    def run(self, workflow: dict[str, Any], on_progress=None) -> list[tuple[str, bytes]]:
        prompt_id = self.queue_prompt(workflow)
        self.wait(prompt_id, on_progress=on_progress)
        return list(self.outputs(prompt_id))
