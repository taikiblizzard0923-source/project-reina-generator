"""Hugging Face リポジトリの中身（ファイル一覧・サイズ・README）を表示する。

どの量子化を選ぶか、そもそも使えるファイルがあるかを、
ダウンロードする前に確認するために使う。標準ライブラリのみ。

usage: hf_info.py REPO [--readme-lines N]
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hf_api import HfError, read_text_file, repo_files  # noqa: E402

WEIGHTS = (".gguf", ".safetensors")


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

    try:
        files = repo_files(repo, with_size=True)
    except HfError as exc:
        print(f"{repo} を取得できません: {exc}", file=sys.stderr)
        return 1

    print(f"=== {repo} ===")
    weights = sorted(f for f in files if f[0].endswith(WEIGHTS))
    for path, size in weights:
        print(f"  {human(size):>8}  {path}")
    if not weights:
        print("  （.gguf / .safetensors が見つかりません）")
    others = [p for p, _ in sorted(files) if not p.endswith(WEIGHTS)]
    if others:
        print(f"  その他: {', '.join(others[:15])}")

    try:
        text = read_text_file(repo, "README.md").splitlines()
        print(f"\n=== README（先頭 {readme_lines} 行）===")
        print("\n".join(text[:readme_lines]))
    except HfError as exc:
        print(f"\n(README を取得できません: {exc})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
