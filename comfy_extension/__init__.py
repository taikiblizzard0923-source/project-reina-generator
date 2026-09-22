"""ComfyUI に画像生成用の簡易 Web UI を追加するカスタムノード。

ComfyUI 本体と同じポート（既定 8188）で配信するので、
RunPod で新しくポートを開ける必要がない。
スマホのブラウザだけで、生成 → 閲覧 → ZIP ダウンロードまで完結する。

    <ComfyUI の URL>/reina
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
SAVED = re.compile(r"->\s*(\S+\.png)\s*$")
DONE = re.compile(r"完了:\s*(\S+)")
JOB = re.compile(r"\[(\d+)/(\d+)\]")
STEP = re.compile(r"\[[#.]+\]\s*(\d+)/(\d+)")

RUNS: dict[str, dict] = {}
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


@routes.get("/reina/options")
async def options(request: web.Request) -> web.Response:
    presets = sorted(
        {
            p.name.replace(".example.yaml", "").replace(".yaml", "")
            for p in (REPO / "presets").glob("*.yaml")
            if not p.name.startswith("character")
        }
    )
    images = sorted(
        str(p.relative_to(REPO))
        for p in (REPO / "input").glob("*")
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    )
    return web.json_response({"presets": presets, "images": images})


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


@routes.post("/reina/run")
async def run(request: web.Request) -> web.Response:
    body = await request.json()

    args = [_python(), "-m", "reina"]
    if body.get("prompt"):
        args += ["generate", body["prompt"], "--name", "web"]
    else:
        args += ["batch"]
        if body.get("scenes"):
            args += ["--scenes", body["scenes"]]

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

    asyncio.create_task(_pump(RUNS[run_id], proc))
    return web.json_response({"id": run_id})


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


@routes.get("/reina")
async def index(request: web.Request) -> web.StreamResponse:
    return web.FileResponse(WEB / "index.html")


NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}

print(f"[reina] Web UI: /reina  (repo: {REPO})")
