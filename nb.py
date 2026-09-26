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
    edit("右手を自然な形に直して")                    # 直前の画像を修正指示で直す
    edit("服を白いシャツに", ref=["input/face.png"])  # 顔の参照を添えて直す
    compare("standing in a studio", ref=R, cfg=[1.5, 2.0, 3.0])   # cfgだけ変えて比較
    compare("standing in a studio", ref=R, steps=[12, 20, 30])    # stepsだけ変えて比較
    batch(ref="input/me.jpg")                        # presets/scenes.yaml を一括
    batch(ref="input/me.jpg", scenes="beach")        # presets/beach.yaml を使う
    mix(ref="input/me.jpg", limit=12)                # presets/axes.yaml の組み合わせ
    mix(ref="input/me.jpg", axes="beach")            # 別の軸定義を使う
    show(4)                                          # 直近4枚を表示し直す
    bench()                                          # 速度を測る（設定比較用）
    ref_limit()                                      # 参照画像の上限を確認
    add_lora("https://huggingface.co/xxx/yyy/resolve/main/reina_v1.safetensors")  # LoRA追加
    set_lora_strength(0.5)                            # 登録済みLoRAの強度を恒久的に変更
    gen("...", ref=R, lora_strength=0.5)              # 今回の生成だけ強度を変更
    compare("...", ref=R, lora_strength=[0.0, 0.5, 0.85])  # 強度違いを並べて比較

    # シーン定義を渡して一括生成 → ZIP にまとめてダウンロード
    pack(scenes="beach", ref=["input/face.png", "input/side.png", "input/body.png"])
    zip_run()                                        # 直近の結果だけ ZIP 化

    # 参照画像が3枚までのとき、顔・横顔・全身を1枚にまとめる
    c = collage(["input/face.png", "input/side.png", "input/body.png"])
    gen("at home with her dog", ref=c,
        scene_ref=["input/room.png", "input/pet.png=her golden retriever"])
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
# CLI が最後に出す "完了: /path/to/output/20260922-..." の行
DONE_LINE = re.compile(r"完了:\s*(\S+)")

# 直近の実行の出力ディレクトリ（pack() が ZIP にする対象）
LAST_RUN: str | None = None

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


BOOL_FLAGS = ("keep_pose", "no_reference_vae", "dry_run", "raw")


def _pop_flags(opts: dict, args: list[str], names: tuple[str, ...] = BOOL_FLAGS) -> None:
    """opts から store_true 系のCLIフラグを取り出し、値無しで args に足す。

    --dry-run のようなフラグは argparse 側が値を取らないので、他のオプションと
    同じく f"--{key} {value}" 形式で渡すと "unrecognized arguments" で弾かれる。
    """
    for name in names:
        if opts.pop(name, False):
            args.append(f"--{name.replace('_', '-')}")


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
            clean = line.replace("\r", "")
            match = SAVED_LINE.search(clean)
            if match and os.path.exists(match.group(1)):
                shown.append(match.group(1))
                _display([match.group(1)])
            done = DONE_LINE.search(clean)
            if done and os.path.isdir(done.group(1)):
                globals()["LAST_RUN"] = done.group(1)

    code = proc.wait()
    if code != 0:
        print(f"\n[失敗] 終了コード {code}")
        return False
    if not shown and "--dry-run" not in args:
        # 保存行を拾えなかったときの保険（dry-run は何も保存しないので、過去の画像を出さない）
        show(expect)
    return True


def collage(
    sources: list[str],
    out: str = "input/ref_collage.png",
    rows: int = 1,
    height: int = 768,
) -> str:
    """複数の参照画像を1枚にまとめ、表示して保存先を返す。

    参照画像の枚数に上限がある（3枚など）とき、顔・横顔・全身を1枚にすれば
    1枠で渡せる。戻り値をそのまま ref= に渡せる。
    """
    scripts = os.path.join(ROOT, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from collage import collage as _collage  # noqa: PLC0415

    paths = [p if os.path.isabs(p) else os.path.join(ROOT, p) for p in sources]
    target = out if os.path.isabs(out) else os.path.join(ROOT, out)
    made = _collage(target, paths, rows=rows, height=height)
    print(f"{os.path.relpath(made, ROOT)} を作りました（{len(sources)}枚）")
    _display([str(made)])
    return os.path.relpath(made, ROOT)


def zip_run(run_dir: str | None = None, out_dir: str | None = None) -> str:
    """生成結果のフォルダを ZIP にまとめ、ダウンロード用リンクを出す。

    run_dir を省略すると直近の実行の出力を使う。
    画像・メタデータ JSON に加えて、プロンプト一覧の summary.txt を入れる。
    """
    import json
    import zipfile

    run_dir = run_dir or LAST_RUN
    if not run_dir or not os.path.isdir(run_dir):
        print("まとめる出力フォルダがありません。先に生成してください")
        return ""

    # /workspace があればそこに置く（JupyterLab から辿りやすい）
    if out_dir is None:
        out_dir = "/workspace/downloads" if os.path.isdir("/workspace") else os.path.join(ROOT, "downloads")
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.basename(run_dir.rstrip("/"))
    archive = os.path.join(out_dir, f"reina-{name}.zip")

    files = sorted(glob.glob(os.path.join(run_dir, "*")))
    images = [f for f in files if f.endswith(".png")]

    # どの画像がどのプロンプトか、1ファイルで分かるようにしておく
    lines = [f"生成: {name}", f"画像: {len(images)}枚", ""]
    for meta_path in sorted(glob.glob(os.path.join(run_dir, "*.json"))):
        try:
            meta = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        params = meta.get("params", {})
        lines += [
            f"[{os.path.basename(meta_path)[:-5]}]",
            f"  scene : {meta.get('scene')}",
            f"  seed  : {params.get('seed')}",
            f"  size  : {params.get('width')}x{params.get('height')}"
            f"  steps={params.get('steps')} cfg={params.get('cfg')}",
            f"  prompt: {meta.get('prompt', '')}",
            "",
        ]

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, os.path.join(name, os.path.basename(path)))
        zf.writestr(os.path.join(name, "summary.txt"), "\n".join(lines))

    size = os.path.getsize(archive) / 1e6
    print(f"{archive}  ({len(images)}枚 / {size:.1f}MB)")
    _download_link(archive)
    return archive


def _download_link(path: str) -> None:
    """JupyterLab から辿れるならリンクを出す。無理ならパスだけ案内する。"""
    try:
        from IPython.display import FileLink, display
    except ImportError:
        return
    try:
        relative = os.path.relpath(path, os.getcwd())
    except ValueError:
        relative = ""
    if relative and not relative.startswith(".."):
        display(FileLink(relative))
    else:
        print("左のファイルブラウザで開いてダウンロードしてください:")
        print(f"  {os.path.dirname(path)}  ->  {os.path.basename(path)}")


def pack(
    scenes: str | None = None,
    ref: str | list[str] | None = None,
    scene_ref: str | list[str] | None = None,
    n: int = 1,
    only: list[str] | None = None,
    **opts,
) -> str:
    """シーン定義を渡して一括生成し、結果を ZIP にまとめる。

        pack(scenes="beach", ref=["input/face.png", "input/side.png"])
    """
    ok = batch(ref=ref, only=only, n=n, scenes=scenes, scene_ref=scene_ref, **opts)
    if not ok:
        print("生成に失敗したため ZIP は作りません")
        return ""
    return zip_run()


def add_lora(url: str, name: str | None = None, strength: float = 0.85, replace: bool = False) -> None:
    """LoRA を URL からダウンロードして models/loras/ に置き、config.yaml に登録する。

    次の gen()/batch()/mix() から自動的に反映される（ComfyUI 再起動不要）。
        add_lora("https://huggingface.co/xxx/yyy/resolve/main/reina_v1.safetensors")
        add_lora("https://huggingface.co/xxx/yyy", name="reina_v1.safetensors")  # ページURLでも可
    """
    args = [os.path.join(ROOT, "scripts", "add_lora.py"), url]
    if name:
        args += ["--name", name]
    args += ["--strength", str(strength)]
    if replace:
        args.append("--replace")
    comfy = os.environ.get("REINA_COMFY_DIR", "/workspace/ComfyUI")
    args += ["--comfy", comfy]
    cmd = f"cd {shlex.quote(ROOT)} && {shlex.join([sys.executable, *args])}"
    proc = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    print((proc.stdout + proc.stderr).strip())


def set_lora_strength(strength: float, name: str | None = None) -> None:
    """config.yaml に登録済みの LoRA の強度を、ダウンロードし直さずに変更する。

    name を省略すると登録済み全 LoRA に適用。以降ずっと（Pod を作り直すまで）
    この強度が使われる。1回の生成だけ変えたいなら gen(..., lora_strength=x) を使う。
    """
    config_path = os.path.join(ROOT, "config.yaml")
    if not os.path.exists(config_path):
        print("config.yaml がありません。まず add_lora() で LoRA を追加してください")
        return
    import yaml

    with open(config_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    loras = data.get("loras") or []
    if not loras:
        print("登録済みの LoRA がありません（add_lora() で追加してください）")
        return

    changed = []
    for lora in loras:
        if name is None or lora.get("name") == name:
            lora["strength"] = strength
            changed.append(lora["name"])
    if not changed:
        print(f"'{name}' という LoRA は登録されていません。登録済み: {[l.get('name') for l in loras]}")
        return

    with open(config_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
    print(f"strength={strength} に更新: {', '.join(changed)}")


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
    _pop_flags(opts, args)
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
    _pop_flags(opts, args)
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
    _pop_flags(opts, args)
    for key, value in opts.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    return _run(args, limit * n)


def edit(
    instruction: str,
    image: str | None = None,
    ref: str | list[str] | None = None,
    n: int = 1,
    **opts,
) -> bool:
    """生成済みの画像に修正指示を与えて直し、表示する。

    image を省略すると、最後に生成した画像を直す。ref に顔の参照写真（2枚まで）を
    渡すと、修正を重ねても顔が別人に寄りにくい。ref は顔のアップだけにすること
    （全身写真を渡すと、そのポーズや構図に引っ張られて別の絵になる）。
        edit("右手を自然な形に直して")
        edit("服を白いシャツに変えて", ref=["input/face.png", "input/side.png"])
        edit("背景を夜の街に", image="output/20260926-071346/shot_777.png")
    """
    args = ["edit", instruction, "--repeat", str(n)]
    if image:
        args += ["--image", image]
    args += _reference_args(ref)
    _pop_flags(opts, args)
    for key, value in opts.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    return _run(args, n)


def compare(prompt: str, ref=None, seed: int | None = None, label_fmt: str | None = None, **grid) -> None:
    """同じシード・プロンプトのまま、パラメータだけ変えて並べて比較する。

    grid の各キーワードにリストを渡すと、その全組み合わせを生成する。
    複数キーを渡すと直積になる（数が増えすぎないよう注意）。

        compare("standing in a studio", ref=R, cfg=[1.5, 2.0, 3.0])
        compare("standing in a studio", ref=R, steps=[12, 20, 30], cfg=[1.5, 3.0])
    """
    import itertools
    import random

    if not grid:
        print("比較するパラメータを指定してください（例: cfg=[1.5, 2.0, 3.0]）")
        return
    seed = seed if seed is not None else random.randint(0, 2**31)

    keys = list(grid)
    combos = list(itertools.product(*(grid[k] if isinstance(grid[k], (list, tuple)) else [grid[k]] for k in keys)))

    print(f"seed={seed} を固定して {len(combos)} 通りを比較します")
    for combo in combos:
        overrides = dict(zip(keys, combo))
        label = label_fmt.format(**overrides) if label_fmt else " ".join(f"{k}={v}" for k, v in overrides.items())
        print(f"\n--- {label} ---")
        gen(prompt, ref=ref, seed=seed, name="cmp", **overrides)


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
    '  /  edit("右手を直して")'
    '  /  pack(scenes="beach", ref=...)  /  show(4)  /  bench()  /  ref_limit()'
)
