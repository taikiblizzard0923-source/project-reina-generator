"""JupyterLab のセルから生成して、その場で画像を表示するためのヘルパー。

使い方（セルに貼る）:
    %run /workspace/project-reina-generator/nb.py

    gen("walking on a Tokyo street at night, black leather jacket")
    gen("sitting in a cafe", ref="input/me.jpg")     # 参照画像つき（顔だけ引き継ぎ）
    gen("sitting in a cafe", ref="input/me.jpg", keep_pose=True)  # ポーズ・構図も引き継ぐ
    gen("portrait", n=4)                             # 4枚
    batch(ref="input/me.jpg")                        # presets/scenes.yaml を一括
    mix(ref="input/me.jpg", limit=12)                # presets/axes.yaml の組み合わせ
    show(4)                                          # 直近4枚を表示し直す
"""

from __future__ import annotations

import glob
import os
import re
import shlex
import subprocess
import sys

# %run でも exec でも動くように __file__ 不在に備える
# CLI が保存ごとに出す "  -> /path/to/image.png" の行
SAVED_LINE = re.compile(r"->\s*(\S+\.png)\s*$")

ROOT = (
    os.path.dirname(os.path.abspath(__file__))
    if "__file__" in dir()
    else "/workspace/project-reina-generator"
)
WIDTH = 420  # スマホで見やすい表示幅


def _display(paths: list[str]) -> None:
    try:
        from IPython.display import Image, display
    except ImportError:  # ノートブック外
        print("\n".join(paths))
        return
    for path in paths:
        display(Image(path, width=WIDTH))


def show(n: int = 1) -> list[str]:
    """直近 n 枚を生成順（古い順）で表示する。"""
    files = sorted(glob.glob(f"{ROOT}/output/*/*.png"), key=os.path.getmtime)
    picked = files[-n:] if n else files
    if not picked:
        print("output/ に画像がありません")
    for path in picked:
        print(os.path.relpath(path, ROOT))
        _display([path])
    return picked


def _run(args: list[str], expect: int) -> None:
    """CLI を実行し、進捗を流しながら、1枚保存されるたびにその場で表示する。

    行単位で読むと \r による進捗更新が改行まで出てこないので、
    バイト列のまま少しずつ読み、改行が来たところで保存行を拾う。
    """
    cmd = f"cd {shlex.quote(ROOT)} && {shlex.join([sys.executable, '-m', 'reina', *args])}"
    proc = subprocess.Popen(
        cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0
    )
    assert proc.stdout is not None

    pending = ""
    shown: list[str] = []
    while True:
        chunk = proc.stdout.read(128)
        if not chunk:
            break
        text = chunk.decode("utf-8", "replace")
        sys.stdout.write(text)
        sys.stdout.flush()

        pending += text
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            match = SAVED_LINE.search(line.replace("\r", ""))
            if match and os.path.exists(match.group(1)):
                shown.append(match.group(1))
                _display([match.group(1)])

    code = proc.wait()
    if code != 0:
        print(f"\n[失敗] 終了コード {code}")
    if not shown and code == 0:
        # 保存行を拾えなかったときの保険
        show(expect)


def _reference_args(ref: str | list[str] | None) -> list[str]:
    args: list[str] = []
    for path in [ref] if isinstance(ref, str) else (ref or []):
        args += ["-r", path]
    return args


def gen(prompt: str, ref: str | list[str] | None = None, n: int = 1, name: str = "shot", **opts) -> None:
    """1つのプロンプトから n 枚生成して表示する。

    opts は CLI のオプションにそのまま渡る（steps=30, cfg=3.0, width=1024 ...）。
    """
    args = ["generate", prompt, "--name", name, "--repeat", str(n)]
    args += _reference_args(ref)
    if opts.pop("keep_pose", False):
        args.append("--keep-pose")
    for key, value in opts.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    _run(args, n)


def batch(ref: str | list[str] | None = None, only: list[str] | None = None, n: int = 1, **opts) -> None:
    """presets/scenes.yaml のシーンをまとめて生成して表示する。"""
    args = ["batch", "--repeat", str(n)]
    args += _reference_args(ref)
    if opts.pop("keep_pose", False):
        args.append("--keep-pose")
    if only:
        args += ["--only", *only]
    for key, value in opts.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    expect = (len(only) if only else 8) * n
    _run(args, expect)


def mix(
    ref: str | list[str] | None = None,
    limit: int = 12,
    n: int = 1,
    mix_seed: int | None = None,
    **opts,
) -> None:
    """presets/axes.yaml の軸（服装×場所×光×構図）を掛け合わせて生成する。

    シーンを1つずつ書くより、当たりの組み合わせを広く探すのに向く。
    """
    args = ["mix", "--limit", str(limit), "--repeat", str(n)]
    args += _reference_args(ref)
    if mix_seed is not None:
        args += ["--mix-seed", str(mix_seed)]
    if opts.pop("keep_pose", False):
        args.append("--keep-pose")
    for key, value in opts.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    _run(args, limit * n)


print(
    "準備できました。"
    '  gen("プロンプト", ref="input/me.jpg")'
    '  /  batch(ref="input/me.jpg")'
    '  /  mix(ref="input/me.jpg", limit=12)'
    "  /  show(4)"
)
