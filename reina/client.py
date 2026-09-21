"""ComfyUI の HTTP / WebSocket API クライアント。"""

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


class ComfyClient:
    def __init__(self, server_address: str, client_id: str | None = None, timeout: int = 900):
        self.server_address = server_address
        self.client_id = client_id or str(uuid.uuid4())
        self.timeout = timeout
        self.base_url = f"http://{server_address}"

    # -- 基本 ---------------------------------------------------------------

    def ping(self) -> dict[str, Any]:
        """/system_stats を叩いて疎通と GPU 情報を返す。"""
        try:
            resp = requests.get(f"{self.base_url}/system_stats", timeout=10)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ComfyError(f"ComfyUI に接続できません ({self.base_url}): {exc}") from exc
        return resp.json()

    def object_info(self, node_class: str | None = None) -> dict[str, Any]:
        """利用可能なノード定義。カスタムノードの有無確認に使う。"""
        url = f"{self.base_url}/object_info"
        if node_class:
            url += f"/{urllib.parse.quote(node_class)}"
        resp = requests.get(url, timeout=30)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()

    def has_node(self, node_class: str) -> bool:
        return bool(self.object_info(node_class))

    def model_list(self, folder: str) -> list[str]:
        """models/<folder> に置かれているファイル一覧（ComfyUI が認識しているもの）。"""
        resp = requests.get(
            f"{self.base_url}/models/{urllib.parse.quote(folder)}", timeout=30
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
            resp = requests.post(
                f"{self.base_url}/upload/image",
                files={"image": (path.name, fh, "application/octet-stream")},
                data={"overwrite": str(overwrite).lower(), "subfolder": subfolder},
                timeout=120,
            )
        resp.raise_for_status()
        info = resp.json()
        sub = info.get("subfolder") or ""
        name = info["name"]
        return f"{sub}/{name}" if sub else name

    # -- 実行 ---------------------------------------------------------------

    def queue_prompt(self, workflow: dict[str, Any]) -> str:
        payload = {"prompt": workflow, "client_id": self.client_id}
        resp = requests.post(f"{self.base_url}/prompt", json=payload, timeout=60)
        if resp.status_code != 200:
            raise ComfyError(f"ワークフロー投入に失敗しました: {resp.status_code} {resp.text[:2000]}")
        return resp.json()["prompt_id"]

    def wait(self, prompt_id: str, on_progress=None) -> None:
        """WebSocket で完了まで待つ。"""
        ws = websocket.WebSocket()
        ws.connect(
            f"ws://{self.server_address}/ws?clientId={self.client_id}",
            timeout=self.timeout,
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

    def history(self, prompt_id: str) -> dict[str, Any]:
        resp = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=30)
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
                resp = requests.get(f"{self.base_url}/view", params=params, timeout=120)
                resp.raise_for_status()
                yield image["filename"], resp.content

    def run(self, workflow: dict[str, Any], on_progress=None) -> list[tuple[str, bytes]]:
        prompt_id = self.queue_prompt(workflow)
        self.wait(prompt_id, on_progress=on_progress)
        return list(self.outputs(prompt_id))
