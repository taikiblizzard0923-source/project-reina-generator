"""壊れた/欠けた Python パッケージを検出して入れ直す。

ディスク不足などで pip install が中断されると、中身の無いディレクトリだけが残り、
`import x` は成功するのに `from x import y` が ImportError になる
（namespace package 化）。これは通常の `pip install`（既に入っている扱い）では
直らないので、`--force-reinstall` が要る。

usage: fix_deps.py [--requirements PATH] [--include-torch]
  対象の venv の python で実行すること:
      /workspace/ComfyUI/venv/bin/python scripts/fix_deps.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys

# import 名 -> pip 名
MODULES = {
    "sqlalchemy": "sqlalchemy",
    "alembic": "alembic",
    "aiohttp": "aiohttp",
    "yaml": "PyYAML",
    "PIL": "pillow",
    "safetensors": "safetensors",
    "numpy": "numpy",
    "tqdm": "tqdm",
    "huggingface_hub": "huggingface_hub[cli]",
    "requests": "requests",
    "websocket": "websocket-client",
}
HEAVY = {"torch", "torchvision", "torchaudio"}


def state(name: str) -> str:
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError):
        return "壊れている"
    if spec is None:
        return "欠落"
    if spec.origin is None:
        # __init__.py の無いディレクトリ = 中断されたインストールの残骸
        return "壊れている"
    return "OK"


def pip(*args: str) -> int:
    cmd = [sys.executable, "-m", "pip", *args]
    print(f"  $ {' '.join(cmd[2:])}")
    return subprocess.run(cmd).returncode


def main() -> int:
    requirements = None
    if "--requirements" in sys.argv:
        requirements = sys.argv[sys.argv.index("--requirements") + 1]
    include_torch = "--include-torch" in sys.argv

    targets = dict(MODULES)
    if include_torch:
        targets.update({name: name for name in HEAVY})

    print(f"python: {sys.executable}")
    print("=== 状態 ===")
    broken: list[str] = []
    missing: list[str] = []
    for module, package in sorted(targets.items()):
        status = state(module)
        print(f"  {status:8} {module}")
        if status == "壊れている":
            broken.append(package)
        elif status == "欠落":
            missing.append(package)

    for name in sorted(HEAVY):
        status = state(name)
        if not include_torch and status != "OK":
            print(f"  !! {name}: {status}。再取得は数GBになるため既定では触りません"
                  f"（直すなら --include-torch）")

    if not broken and not missing:
        print("\n問題は見つかりませんでした。")
    if broken:
        print("\n=== 壊れたものを入れ直す ===")
        pip("install", "-U", "--force-reinstall", "--no-cache-dir", *broken)
    if missing:
        print("\n=== 欠けているものを入れる ===")
        pip("install", "-U", *missing)

    if requirements:
        print(f"\n=== {requirements} ===")
        pip("install", "-r", requirements)

    print("\n=== 再確認 ===")
    remaining = 0
    for module in sorted(targets):
        status = state(module)
        if status != "OK":
            remaining += 1
            print(f"  {status:8} {module}")
    print("残っている問題:", remaining)
    return 1 if remaining else 0


if __name__ == "__main__":
    raise SystemExit(main())
