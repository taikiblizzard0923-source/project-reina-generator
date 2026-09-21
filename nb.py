"""JupyterLab のセルから生成して、その場で画像を表示するためのヘルパー。

使い方（セルに貼る）:
    %run /workspace/project-reina-generator/nb.py

    gen("walking on a Tokyo street at night, black leather jacket")
    gen("sitting in a cafe", ref="input/me.jpg")     # 参照画像つき
    gen("portrait", n=4)                             # 4枚
    batch(ref="input/me.jpg")                        # presets/scenes.yaml を一括
    show(4)                                          # 直近4枚を表示し直す
"""

from __future__ import annotations

import glob
import os
import shlex
import subprocess
import sys

# %run でも exec でも動くように __file__ 不在に備える
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
        print(os.path.relpath(path, ROOT))
        display(Image(path, width=WIDTH))


def show(n: int = 1) -> list[str]:
    """直近 n 枚を新しい順ではなく生成順で表示する。"""
    files = sorted(glob.glob(f"{ROOT}/output/*/*.png"), key=os.path.getmtime)
    picked = files[-n:] if n else files
    if not picked:
        print("output/ に画像がありません")
    _display(picked)
    return picked


def _run(args: list[str], expect: int) -> None:
    cmd = f"cd {shlex.quote(ROOT)} && {shlex.join([sys.executable, '-m', 'reina', *args])}"
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    # 進捗と保存先は stderr に出る
    tail = (proc.stderr or "").strip().splitlines()
    print("\n".join(tail[-12:]))
    if proc.returncode != 0:
        print(f"[失敗] 終了コード {proc.returncode}")
        if proc.stdout.strip():
            print(proc.stdout[-2000:])
        return
    show(expect)


def gen(prompt: str, ref: str | list[str] | None = None, n: int = 1, name: str = "shot", **opts) -> None:
    """1つのプロンプトから n 枚生成して表示する。

    opts は CLI のオプションにそのまま渡る（steps=30, cfg=3.0, width=1024 ...）。
    """
    args = ["generate", prompt, "--name", name, "--repeat", str(n)]
    for path in [ref] if isinstance(ref, str) else (ref or []):
        args += ["-r", path]
    for key, value in opts.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    _run(args, n)


def batch(ref: str | list[str] | None = None, only: list[str] | None = None, n: int = 1, **opts) -> None:
    """presets/scenes.yaml のシーンをまとめて生成して表示する。"""
    args = ["batch", "--repeat", str(n)]
    for path in [ref] if isinstance(ref, str) else (ref or []):
        args += ["-r", path]
    if only:
        args += ["--only", *only]
    for key, value in opts.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    expect = (len(only) if only else 8) * n
    _run(args, expect)


print("準備できました。  gen(\"プロンプト\")  /  gen(\"...\", ref=\"input/me.jpg\")  /  batch(ref=\"input/me.jpg\")  /  show(4)")
