"""Hugging Face リポジトリの中身（ファイル一覧・サイズ・README）を表示する。

どの量子化を選ぶか、そもそも使えるファイルがあるかを、
ダウンロードする前に確認するために使う。

usage: hf_info.py REPO [--readme-lines N]
"""

from __future__ import annotations

import sys

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.utils import HfHubHTTPError, RepositoryNotFoundError


def human(size: int | None) -> str:
    if not size:
        return "?"
    value = float(size)
    for unit in ("B", "K", "M", "G", "T"):
        if value < 1024:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}P"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    repo = sys.argv[1]
    readme_lines = 60
    if "--readme-lines" in sys.argv:
        readme_lines = int(sys.argv[sys.argv.index("--readme-lines") + 1])

    api = HfApi()
    try:
        info = api.model_info(repo, files_metadata=True)
    except (RepositoryNotFoundError, HfHubHTTPError) as exc:
        print(f"{repo} を取得できません: {exc}", file=sys.stderr)
        return 1

    print(f"=== {repo} ===")
    files = sorted(info.siblings, key=lambda s: s.rfilename)
    for sibling in files:
        if sibling.rfilename.endswith((".gguf", ".safetensors")):
            print(f"  {human(sibling.size):>8}  {sibling.rfilename}")
    others = [s.rfilename for s in files if not s.rfilename.endswith((".gguf", ".safetensors"))]
    if others:
        print(f"  その他: {', '.join(others[:15])}")

    try:
        path = hf_hub_download(repo, "README.md")
        text = open(path, encoding="utf-8").read().splitlines()
        print(f"\n=== README（先頭 {readme_lines} 行）===")
        print("\n".join(text[:readme_lines]))
    except Exception as exc:  # noqa: BLE001 - README が無い repo もある
        print(f"\n(README を取得できません: {type(exc).__name__})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
