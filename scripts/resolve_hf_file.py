"""Hugging Face リポジトリから実在するファイル名を解決する。

配布者ごとにファイル名がバラバラなので、決め打ちせず repo の中身を見て選ぶ。

usage: resolve_hf_file.py REPO[,REPO...] EXT [PREFER ...]
  stdout: "<repo>\t<path>"   （見つからなければ終了コード 1）
  stderr: 候補一覧（* が選ばれたもの）
"""

from __future__ import annotations

import sys

from huggingface_hub import list_repo_files
from huggingface_hub.utils import HfHubHTTPError, RepositoryNotFoundError


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2

    repos = [r.strip() for r in sys.argv[1].split(",") if r.strip()]
    ext = sys.argv[2].lower()
    prefer = [p.lower() for p in sys.argv[3:] if p]

    candidates: list[tuple[str, str]] = []
    for repo in repos:
        try:
            files = list_repo_files(repo)
        except (RepositoryNotFoundError, HfHubHTTPError) as exc:
            print(f"  ! {repo}: {type(exc).__name__}", file=sys.stderr)
            continue
        candidates += [(repo, f) for f in files if f.lower().endswith(ext)]

    if not candidates:
        print(f"{'/'.join(repos)} に {ext} が見つかりません", file=sys.stderr)
        return 1

    def score(item: tuple[str, str]) -> tuple:
        repo, path = item
        low = path.lower()
        hits = sum(p in low for p in prefer)
        # 同点なら repo の並び順を優先し、パスの短いものを選ぶ
        return (hits, -repos.index(repo), -len(path))

    best = max(candidates, key=score)

    print("  候補:", file=sys.stderr)
    for repo, path in sorted(candidates):
        mark = "*" if (repo, path) == best else " "
        print(f"   {mark} {repo}/{path}", file=sys.stderr)

    print(f"{best[0]}\t{best[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
