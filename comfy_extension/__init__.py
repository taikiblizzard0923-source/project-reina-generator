"""ComfyUI に画像生成用の簡易 Web UI を追加するカスタムノード。

ComfyUI 本体と同じポート（既定 8188）で配信するので、
RunPod で新しくポートを開ける必要がない。
スマホのブラウザだけで、生成 → 閲覧 → ZIP ダウンロードまで完結する。

    <ComfyUI の URL>/reina        静止画の生成・編集
    <ComfyUI の URL>/reina/clip   静止画 → 確認・編集 → 動画
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid
import zipfile
from pathlib import Path

from aiohttp import web
from server import PromptServer

REPO = Path(os.environ.get("REINA_REPO", "/workspace/project-reina-generator"))
WEB = Path(__file__).resolve().parent / "web"

# CLI の出力から進捗を読み取る
SAVED = re.compile(r"->\s*(\S+\.(?:png|mp4|webm))\s*$")
DONE = re.compile(r"完了:\s*(\S+)")
JOB = re.compile(r"\[(\d+)/(\d+)\]")
STEP = re.compile(r"\[[#.]+\]\s*(\d+)/(\d+)")

RUNS: dict[str, dict] = {}
# 実行中の CLI プロセス（キャンセル用。RUNS は JSON で返すので別に持つ）
PROCS: dict[str, asyncio.subprocess.Process] = {}
MAX_RUNS = 20

routes = PromptServer.instance.routes


def _python() -> str:
    return sys.executable


def _safe(path: str) -> Path | None:
    """REPO 配下のパスだけを許可する（外のファイルを配信しないため）。"""
    try:
        resolved = (REPO / path).resolve() if not os.path.isabs(path) else Path(path).resolve()
        resolved.relative_to(REPO.resolve())
    except (ValueError, OSError):
        return None
    return resolved if resolved.exists() else None


# -- 一覧 -------------------------------------------------------------------


def _preset_kind(path: Path) -> str:
    """scenes: を持つか axes: を持つかで種類を判定する。

    取り違えると「0件で成功」になって何も生成されないので、
    UI 側で選ばせる時点で分けておく。
    """
    try:
        head = path.read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    if "\nscenes:" in f"\n{head}":
        return "scenes"
    if "\naxes:" in f"\n{head}":
        return "axes"
    return "unknown"


@routes.get("/reina/options")
async def options(request: web.Request) -> web.Response:
    by_kind: dict[str, set[str]] = {"scenes": set(), "axes": set()}
    for path in (REPO / "presets").glob("*.yaml"):
        if path.name.startswith("character"):
            continue
        kind = _preset_kind(path)
        if kind in by_kind:
            by_kind[kind].add(path.name.replace(".example.yaml", "").replace(".yaml", ""))
    presets = sorted(by_kind["scenes"])
    images = sorted(
        str(p.relative_to(REPO))
        for p in (REPO / "input").glob("*")
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    )
    return web.json_response(
        {"presets": presets, "axes": sorted(by_kind["axes"]), "images": images}
    )


@routes.get("/reina/scenes")
async def scenes(request: web.Request) -> web.Response:
    """シーン定義（presets/<name>.yaml の scenes:）の中身を返す。1件ずつ呼び出して使う用。"""
    import yaml

    name = Path(request.query.get("name", "")).name  # ディレクトリ部分は捨てる
    path = next(
        (p for p in (REPO / "presets" / f"{name}.yaml", REPO / "presets" / f"{name}.example.yaml") if p.is_file()),
        None,
    )
    if not name or path is None:
        return web.json_response({"error": f"presets/{name}.yaml がありません"}, status=404)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return web.json_response({"error": f"{path.name} を読めません: {exc}"}, status=400)
    items = []
    for index, item in enumerate(raw.get("scenes") or []):
        if isinstance(item, str):
            item = {"prompt": item}
        if not isinstance(item, dict) or not item.get("prompt"):
            continue
        items.append({
            "id": str(item.get("id") or f"scene{index:02d}"),
            "prompt": " ".join(str(item["prompt"]).split()),
            "width": item.get("width"),
            "height": item.get("height"),
        })
    return web.json_response({"name": name, "scenes": items})


# -- 実行 -------------------------------------------------------------------


async def _pump(run: dict, proc: asyncio.subprocess.Process) -> None:
    pending = ""
    assert proc.stdout is not None
    while True:
        chunk = await proc.stdout.read(256)
        if not chunk:
            break
        text = chunk.decode("utf-8", "replace")
        pending += text
        run["log"] = (run["log"] + text)[-8000:]
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            line = line.replace("\r", "")
            if m := JOB.search(line):
                run["current"], run["total"] = int(m.group(1)), int(m.group(2))
            if m := STEP.search(line):
                run["step"], run["steps"] = int(m.group(1)), int(m.group(2))
            if m := SAVED.search(line):
                path = _safe(m.group(1))
                if path:
                    run["images"].append(str(path.relative_to(REPO.resolve())))
            if m := DONE.search(line):
                run["dir"] = m.group(1)
        # 進捗行は改行を伴わないので、末尾も見る
        if m := STEP.search(pending.replace("\r", "")):
            run["step"], run["steps"] = int(m.group(1)), int(m.group(2))
    run["returncode"] = await proc.wait()
    run["finished"] = True


@routes.get("/reina/recent")
async def recent(request: web.Request) -> web.Response:
    """編集する画像を選ぶための、最近生成した画像の一覧（新しい順）。"""
    try:
        limit = max(1, min(int(request.query.get("limit", 24)), 100))
    except ValueError:
        limit = 24
    root = REPO.resolve()
    files = sorted(
        (root / "output").glob("*/*.png"), key=lambda p: p.stat().st_mtime, reverse=True
    )[:limit]
    return web.json_response({"images": [str(p.relative_to(root)) for p in files]})


def _edit_args(body: dict) -> list[str] | None:
    image = _safe(str(body.get("image") or ""))
    instruction = str(body.get("instruction") or "").strip()
    if not image or not image.is_file() or not instruction:
        return None
    args = ["edit", instruction, "--image", str(image)]
    if body.get("raw"):
        args.append("--raw")
    for key in ("repeat", "seed"):
        if body.get(key) not in (None, ""):
            args += [f"--{key}", str(body[key])]
    return args


def _video_args(body: dict) -> list[str] | None:
    """静止画（0秒目）を動かす。画像は REPO 配下のものだけ。"""
    image = _safe(str(body.get("image") or ""))
    action = str(body.get("action") or "").strip()
    if not image or not image.is_file() or not action:
        return None
    args = ["video", action, "--image", str(image)]
    for key in ("seconds", "say", "voice", "sound", "music", "lang", "seed"):
        value = body.get(key)
        if value not in (None, ""):
            args += [f"--{key}", str(value).strip()]
    return args


@routes.post("/reina/run")
async def run(request: web.Request) -> web.Response:
    body = await request.json()

    args = [_python(), "-m", "reina"]
    if body.get("mode") == "video":
        video_args = _video_args(body)
        if video_args is None:
            return web.json_response({"error": "動かす画像と動きの指示を指定してください"}, status=400)
        args += video_args
    elif body.get("mode") == "edit":
        edit_args = _edit_args(body)
        if edit_args is None:
            return web.json_response({"error": "編集する画像と指示を指定してください"}, status=400)
        args += edit_args
    elif body.get("prompt"):
        args += ["generate", body["prompt"], "--name", "web"]
    elif body.get("mode") == "mix":
        args += ["mix", "--limit", str(body.get("limit") or 12)]
        if body.get("axes"):
            args += ["--axes", body["axes"]]
    elif body.get("mode") == "scenes":
        args += ["batch"]
        if body.get("scenes"):
            args += ["--scenes", body["scenes"]]
    else:
        # 空の依頼で一括生成が走らないよう、何を作るかが分からないものは断る
        return web.json_response(
            {"error": "prompt（静止画）か mode（edit / video / scenes / mix）を指定してください"}, status=400
        )

    if body.get("mode") not in ("edit", "video"):
        for path in body.get("ref") or []:
            args += ["-r", path]
        for path in body.get("scene_ref") or []:
            args += ["-s", path]
        for key in ("steps", "cfg", "width", "height", "repeat", "batch_size", "seed"):
            if body.get(key) not in (None, ""):
                args += [f"--{key.replace('_', '-')}", str(body[key])]
        if body.get("keep_pose"):
            args += ["--keep-pose"]

    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(REPO),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    run_id = uuid.uuid4().hex[:12]
    RUNS[run_id] = {
        "id": run_id,
        "started": time.time(),
        "log": "",
        "images": [],
        "current": 0,
        "total": 0,
        "step": 0,
        "steps": 0,
        "finished": False,
        "returncode": None,
        "dir": None,
        "zip": None,
    }
    for stale in list(RUNS)[:-MAX_RUNS]:
        RUNS.pop(stale, None)
        PROCS.pop(stale, None)

    PROCS[run_id] = proc
    asyncio.create_task(_pump(RUNS[run_id], proc))
    return web.json_response({"id": run_id})


def _stop_comfy_work() -> None:
    """ComfyUI の実行中の処理を中断し、待ち行列を空にする。

    CLI を止めても、送り済みのワークフローは ComfyUI 側で走り続けるため。
    モデルの読み込み中は、読み込みが終わった時点で止まる。
    """
    try:
        PromptServer.instance.prompt_queue.wipe_queue()
    except Exception:  # noqa: BLE001 - ComfyUI の版で場所が変わっても UI は止めない
        pass
    try:
        import comfy.model_management

        comfy.model_management.interrupt_current_processing(True)
    except Exception:  # noqa: BLE001
        pass


@routes.post("/reina/cancel")
async def cancel(request: web.Request) -> web.Response:
    run_id = request.query.get("id", "")
    run, proc = RUNS.get(run_id), PROCS.get(run_id)
    if run and proc and not run["finished"]:
        proc.terminate()
        run["cancelled"] = True
    _stop_comfy_work()
    return web.json_response({"ok": True, "known": bool(run)})


@routes.get("/reina/status")
async def status(request: web.Request) -> web.Response:
    run = RUNS.get(request.query.get("id", ""))
    if not run:
        return web.json_response({"error": "不明な実行 ID"}, status=404)
    payload = dict(run)
    payload["elapsed"] = round(time.time() - run["started"], 1)
    return web.json_response(payload)


# -- 配信 -------------------------------------------------------------------


@routes.get("/reina/file")
async def file(request: web.Request) -> web.StreamResponse:
    path = _safe(request.query.get("path", ""))
    if not path or not path.is_file():
        raise web.HTTPNotFound()
    headers = {}
    if request.query.get("download"):
        headers["Content-Disposition"] = f'attachment; filename="{path.name}"'
    return web.FileResponse(path, headers=headers)


@routes.get("/reina/zip")
async def zip_run(request: web.Request) -> web.Response:
    run = RUNS.get(request.query.get("id", ""))
    if not run or not run.get("dir"):
        return web.json_response({"error": "出力がありません"}, status=404)

    target = _safe(run["dir"])
    if not target or not target.is_dir():
        return web.json_response({"error": "出力フォルダが見つかりません"}, status=404)

    downloads = REPO / "downloads"
    downloads.mkdir(exist_ok=True)
    archive = downloads / f"reina-{target.name}.zip"

    if not archive.exists():
        lines = [f"生成: {target.name}", ""]
        for meta_path in sorted(target.glob("*.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            params = meta.get("params", {})
            lines += [
                f"[{meta_path.stem}]",
                f"  scene : {meta.get('scene')}",
                f"  seed  : {params.get('seed')}",
                f"  size  : {params.get('width')}x{params.get('height')}"
                f" steps={params.get('steps')} cfg={params.get('cfg')}",
                f"  prompt: {meta.get('prompt', '')}",
                "",
            ]
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in sorted(target.iterdir()):
                if item.is_file():
                    zf.write(item, f"{target.name}/{item.name}")
            zf.writestr(f"{target.name}/summary.txt", "\n".join(lines))

    run["zip"] = str(archive.relative_to(REPO.resolve()))
    return web.json_response(
        {"path": run["zip"], "size": archive.stat().st_size, "count": len(run["images"])}
    )


# -- 移行対応（アップロード / バックアップ） --------------------------------

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
ARCHIVE_SUFFIXES = {".tgz", ".gz", ".tar"}


def _run_backup(*args: str) -> tuple[int, str]:
    import subprocess

    proc = subprocess.run(
        [_python(), str(REPO / "scripts" / "backup.py"), *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


@routes.post("/reina/upload")
async def upload(request: web.Request) -> web.Response:
    """参照画像、またはバックアップ tgz を受け取る。

    Pod を作り直すと input/ と自分用の設定は消えるので、
    復元もこの口から行えるようにしてある。
    """
    reader = await request.multipart()
    saved: list[str] = []
    restored: list[str] = []
    errors: list[str] = []

    (REPO / "input").mkdir(exist_ok=True)
    uploads = REPO / "downloads"
    uploads.mkdir(exist_ok=True)

    while True:
        part = await reader.next()
        if part is None:
            break
        if not part.filename:
            continue

        name = Path(part.filename).name  # ディレクトリ部分は捨てる
        suffix = Path(name).suffix.lower()

        if suffix in IMAGE_SUFFIXES:
            target = REPO / "input" / name
        elif suffix in ARCHIVE_SUFFIXES:
            target = uploads / name
        else:
            errors.append(f"{name}: 対応していない形式です")
            await part.read()
            continue

        with target.open("wb") as fh:
            while chunk := await part.read_chunk():
                fh.write(chunk)

        if suffix in IMAGE_SUFFIXES:
            saved.append(str(target.relative_to(REPO)))
        else:
            code, output = _run_backup("restore", str(target))
            (restored if code == 0 else errors).append(f"{name}: {output.splitlines()[-1]}")

    return web.json_response({"saved": saved, "restored": restored, "errors": errors})


@routes.get("/reina/backup")
async def backup(request: web.Request) -> web.Response:
    """設定と参照画像を1ファイルにまとめる（Pod 移行用）。"""
    code, output = _run_backup("create")
    if code != 0:
        return web.json_response({"error": output}, status=500)

    first = output.splitlines()[0].split("  ")[0]
    path = Path(first)
    if not path.exists():
        return web.json_response({"error": output}, status=500)
    return web.json_response(
        {"path": str(path.relative_to(REPO.resolve())), "size": path.stat().st_size}
    )


# 更新後に古い画面がブラウザに残って新しい機能が出ない、を防ぐ
NO_CACHE = {"Cache-Control": "no-cache"}


@routes.get("/reina")
async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(WEB / "index.html", headers=NO_CACHE)


@routes.get("/reina/clip")
async def clip_page(request: web.Request) -> web.StreamResponse:
    """プロンプト → 0秒目の静止画 → 確認・編集 → 動画、を1画面で行う。"""
    return web.FileResponse(WEB / "clip.html", headers=NO_CACHE)


NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}

print(f"[reina] Web UI: /reina  (repo: {REPO})")
