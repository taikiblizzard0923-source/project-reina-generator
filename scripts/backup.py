"""Git 管理外の設定と参照画像をまとめて退避・復元する。

Pod を作り直すと /workspace ごと消えるが、リポジトリに入っていないもの
（自分用の presets/*.yaml、config.yaml、input/ の参照画像）は
GitHub からは戻らない。それを1ファイルにまとめる。

usage:
    backup.py create [OUT.tgz] [--with-output]
    backup.py restore ARCHIVE.tgz [--dry-run]
    backup.py list ARCHIVE.tgz
"""

from __future__ import annotations

import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 退避する対象。output/ は大きいので既定では入れない
INCLUDE = [
    "config.yaml",
    "presets/character.yaml",
    "presets/scenes.yaml",
    "presets/axes.yaml",
    "input",
]
OPTIONAL = ["output"]
SKIP_NAMES = {".gitkeep", ".DS_Store"}


def _members(with_output: bool) -> list[Path]:
    targets = INCLUDE + (OPTIONAL if with_output else [])
    found: list[Path] = []
    for name in targets:
        path = ROOT / name
        if path.is_file():
            found.append(path)
        elif path.is_dir():
            found += [
                p for p in sorted(path.rglob("*")) if p.is_file() and p.name not in SKIP_NAMES
            ]
    # 自分用の presets は名前が自由なので、*.example.yaml 以外を全部拾う
    for path in sorted((ROOT / "presets").glob("*.yaml")):
        if not path.name.endswith(".example.yaml") and path not in found:
            found.append(path)
    return found


def create(out: Path, with_output: bool = False) -> Path:
    files = _members(with_output)
    if not files:
        print("退避するものがありません", file=sys.stderr)
        return out

    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        for path in files:
            tar.add(path, arcname=str(path.relative_to(ROOT)))

    size = out.stat().st_size / 1e6
    print(f"{out}  ({len(files)}ファイル / {size:.1f}MB)")
    for path in files[:20]:
        print(f"  {path.relative_to(ROOT)}")
    if len(files) > 20:
        print(f"  ... 他 {len(files) - 20} ファイル")
    return out


def _safe_members(tar: tarfile.TarFile):
    """リポジトリの外に書き出そうとするメンバーを弾く。"""
    for member in tar.getmembers():
        if not member.isfile():
            continue
        target = (ROOT / member.name).resolve()
        try:
            target.relative_to(ROOT.resolve())
        except ValueError:
            print(f"  ! 範囲外なので飛ばします: {member.name}", file=sys.stderr)
            continue
        yield member


def restore(archive: Path, dry_run: bool = False) -> int:
    if not archive.exists():
        print(f"{archive} がありません", file=sys.stderr)
        return 1
    with tarfile.open(archive, "r:*") as tar:
        members = list(_safe_members(tar))
        for member in members:
            print(f"  {'(確認のみ) ' if dry_run else ''}{member.name}")
        if not dry_run:
            tar.extractall(ROOT, members=members)
    print(f"{len(members)} ファイルを{'確認' if dry_run else '復元'}しました")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        return 2

    command = args[0]
    flags = {a for a in args if a.startswith("--")}
    positional = [a for a in args[1:] if not a.startswith("--")]

    if command == "create":
        default = ROOT / "downloads" / f"reina-backup-{time.strftime('%Y%m%d-%H%M%S')}.tgz"
        create(Path(positional[0]) if positional else default, "--with-output" in flags)
        return 0
    if command == "restore":
        if not positional:
            print("復元するファイルを指定してください", file=sys.stderr)
            return 2
        return restore(Path(positional[0]), "--dry-run" in flags)
    if command == "list":
        if not positional:
            return 2
        with tarfile.open(positional[0], "r:*") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    print(f"  {member.size:>10,}  {member.name}")
        return 0

    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
