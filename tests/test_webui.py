"""comfy_extension の Web UI を、ComfyUI 無しで検証する。

    python tests/mock_comfyui.py &      # 別ターミナル、または事前に起動
    python tests/test_webui.py

ComfyUI の `server` モジュールはルート登録にしか使われていないので、
最小のスタブを作って差し替える。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.environ.setdefault("REINA_REPO", str(REPO))
sys.path.insert(0, str(REPO))

# ComfyUI の server モジュールを、ルートテーブルだけ持つスタブで代用する
_stub = Path(tempfile.mkdtemp())
(_stub / "server.py").write_text(
    textwrap.dedent(
        """
        from aiohttp import web

        class _Prompt:
            def __init__(self):
                self.routes = web.RouteTableDef()

        class PromptServer:
            instance = _Prompt()
        """
    ),
    encoding="utf-8",
)
sys.path.insert(0, str(_stub))

spec = importlib.util.spec_from_file_location(
    "reina_webui", REPO / "comfy_extension" / "__init__.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402


async def main():
    app = web.Application()
    app.add_routes(mod.routes)
    client = TestClient(TestServer(app))
    await client.start_server()

    print("=== GET /reina ===")
    r = await client.get("/reina")
    body = await r.text()
    print(f"  status={r.status}  html={len(body)}bytes  title={'reina generator' in body}")

    print("=== GET /reina/options ===")
    r = await client.get("/reina/options")
    opts = await r.json()
    print("  presets:", opts["presets"])
    print("  images :", opts["images"])

    print("=== POST /reina/run ===")
    r = await client.post("/reina/run", json={
        "scenes": "beach", "ref": opts["images"][:1],
        "steps": 6, "width": 832, "height": 1216,
    })
    run_id = (await r.json())["id"]
    print("  id:", run_id)

    for _ in range(40):
        await asyncio.sleep(0.5)
        s = await (await client.get(f"/reina/status?id={run_id}")).json()
        if s["finished"]:
            break
    print(f"  finished={s['finished']} rc={s['returncode']} images={len(s['images'])} total={s['total']}")
    print("  images:", s["images"])

    print("=== GET /reina/file（画像）===")
    r = await client.get("/reina/file", params={"path": s["images"][0]})
    print(f"  status={r.status}  bytes={len(await r.read())}")

    print("=== パス脱出の拒否 ===")
    r = await client.get("/reina/file", params={"path": "../../etc/passwd"})
    print("  status:", r.status)

    print("=== GET /reina/zip ===")
    z = await (await client.get(f"/reina/zip?id={run_id}")).json()
    print("  ", z)
    r = await client.get("/reina/file", params={"path": z["path"], "download": "1"})
    print(f"  ダウンロード status={r.status} bytes={len(await r.read())} "
          f"header={r.headers.get('Content-Disposition')}")

    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
