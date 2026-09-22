"""JupyterLab のセルから生成して、その場で画像を表示するためのヘルパー。

使い方（セルに貼る）:
    %run /workspace/project-reina-generator/nb.py

    gen("walking on a Tokyo street at night, black leather jacket")
    gen("sitting in a cafe", ref="input/me.jpg")     # 参照画像つき（顔だけ引き継ぎ）
    gen("at home with my dog",                       # 人物3枚 + 写り込ませたいもの
        ref=["input/face.jpg", "input/side.jpg", "input/body.jpg"],
        scene_ref=["input/room.jpg", "input/pet.jpg=her golden retriever"])
    gen("sitting in a cafe", ref="input/me.jpg", keep_pose=True)  # ポーズ・構図も引き継ぐ
    gen("portrait", n=4)                             # 4枚
    batch(ref="input/me.jpg")                        # presets/scenes.yaml を一括
    batch(ref="input/me.jpg", scenes="beach")        # presets/beach.yaml を使う
    mix(ref="input/me.jpg", limit=12)                # presets/axes.yaml の組み合わせ
    mix(ref="input/me.jpg", axes="beach")            # 別の軸定義を使う
    show(4)                                          # 直近4枚を表示し直す
    bench()                                          # 速度を測る（設定比較用）
"""

from __future__ import annotations

import glob
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# %run でも exec でも動くように __file__ 不在に備える
# CLI が保存ごとに出す "  -> /path/to/image.png" の行
SAVED_LINE = re.compile(r"->\s*(\S+\.png)\s*$")

ROOT = (
    os.path.dirname(os.path.abspath(__file__))
    if "__file__" in dir()
    else "/workspace/project-reina-generator"
)
WIDTH = 420  # スマホで見やすい表示幅

# ノートブックの作業ディレクトリはリポジトリ外のことが多い。
# セルから `from reina.client import ...` できるようにしておく
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


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


def _run(args: list[str], expect: int) -> bool:
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
        return False
    if not shown:
        # 保存行を拾えなかったときの保険
        show(expect)
    return True


def ref_limit(node: str | None = None) -> int:
    """参照画像を何枚まで渡せるかを ComfyUI に聞く。

    ノートブックの作業ディレクトリ次第で `import reina` が通らないことがあるので、
    他の機能と同じく CLI をサブプロセスで呼ぶ。
    """
    args = ["refs"] + (["--node", node] if node else [])
    cmd = f"cd {shlex.quote(ROOT)} && {shlex.join([sys.executable, '-m', 'reina', *args])}"
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print((proc.stderr or "").strip())
    if proc.returncode != 0:
        return 0
    try:
        return int(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return 0


def _reference_args(
    ref: str | list[str] | None,
    scene_ref: str | list[str] | None = None,
) -> list[str]:
    """ref = 人物の参照画像、scene_ref = 写り込ませたいもの（部屋・ペットなど）。"""
    args: list[str] = []
    for path in [ref] if isinstance(ref, str) else (ref or []):
        args += ["-r", path]
    for item in [scene_ref] if isinstance(scene_ref, str) else (scene_ref or []):
        args += ["-s", item]
    return args


def gen(
    prompt: str,
    ref: str | list[str] | None = None,
    n: int = 1,
    name: str = "shot",
    scene_ref: str | list[str] | None = None,
    **opts,
) -> bool:
    """1つのプロンプトから n 枚生成して表示する。

    opts は CLI のオプションにそのまま渡る（steps=30, cfg=3.0, width=1024 ...）。
    """
    args = ["generate", prompt, "--name", name, "--repeat", str(n)]
    args += _reference_args(ref, scene_ref)
    if opts.pop("keep_pose", False):
        args.append("--keep-pose")
    for key, value in opts.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    return _run(args, n)


def batch(
    ref: str | list[str] | None = None,
    only: list[str] | None = None,
    n: int = 1,
    scenes: str | None = None,
    scene_ref: str | list[str] | None = None,
    **opts,
) -> bool:
    """シーン定義をまとめて生成して表示する。

    scenes を省略すると presets/scenes.yaml（無ければ scenes.example.yaml）。
    パスでも presets/ 内の名前でも指定できる:  batch(scenes="beach")
    """
    args = ["batch", "--repeat", str(n)]
    if scenes:
        args += ["--scenes", scenes]
    args += _reference_args(ref, scene_ref)
    if opts.pop("keep_pose", False):
        args.append("--keep-pose")
    if only:
        args += ["--only", *only]
    for key, value in opts.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    expect = (len(only) if only else 8) * n
    return _run(args, expect)


def mix(
    ref: str | list[str] | None = None,
    limit: int = 12,
    n: int = 1,
    mix_seed: int | None = None,
    axes: str | None = None,
    scene_ref: str | list[str] | None = None,
    **opts,
) -> bool:
    """軸（服装×場所×光×構図×髪型×顔の向き）を掛け合わせて生成する。

    シーンを1つずつ書くより、当たりの組み合わせを広く探すのに向く。
    axes を省略すると presets/axes.yaml（無ければ axes.example.yaml）。
    パスでも presets/ 内の名前でも指定できる:  mix(axes="beach")
    """
    args = ["mix", "--limit", str(limit), "--repeat", str(n)]
    if axes:
        args += ["--axes", axes]
    args += _reference_args(ref, scene_ref)
    if mix_seed is not None:
        args += ["--mix-seed", str(mix_seed)]
    if opts.pop("keep_pose", False):
        args.append("--keep-pose")
    for key, value in opts.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    return _run(args, limit * n)


def bench(steps: int = 8, width: int = 832, height: int = 1216, ref=None, **opts) -> None:
    """固定条件で1枚だけ生成して、1ステップあたりの秒数を出す。

    モデル（int8 / bf16）や解像度を変えたときの比較に使う。

    シードは毎回変える。固定すると ComfyUI が前回の結果をキャッシュから返し、
    1秒未満で「完了」してしまって計測にならない。
    シードの値はサンプリングの計算量には影響しないので、比較は成立する。
    """
    import random
    import time

    label = f"steps={steps} {width}x{height}" + (" 参照画像あり" if ref else " 参照画像なし")
    print(f"=== 計測: {label} ===")
    started = time.time()
    ok = gen(
        "a person standing in a photo studio, plain grey background, soft light",
        ref=ref,
        name="bench",
        seed=random.randint(0, 2**31),
        steps=steps,
        width=width,
        height=height,
        **opts,
    )
    elapsed = time.time() - started
    if not ok:
        print(f"\n=== 計測できませんでした（{elapsed:.1f}s で失敗）===")
        print("ComfyUI が落ちていないか:  !tail -n 40 /workspace/comfyui.log")
        return
    print(f"\n=== 結果: 合計 {elapsed:.1f}s / {elapsed / steps:.2f} 秒/ステップ ===")
    if elapsed < steps * 0.2:
        print("!! 速すぎます。ComfyUI のキャッシュが返った可能性があります")
    else:
        print("（1回目はモデルの VRAM 読み込みを含むので、2回目の数字を採用すること）")


print(
    "準備できました。"
    '  gen("プロンプト", ref="input/me.jpg")'
    '  /  batch(ref="input/me.jpg")'
    '  /  mix(ref="input/me.jpg", limit=12)'
    '  /  show(4)  /  bench()  /  ref_limit()'
)
