"""使うモデルだけを Pod 内蔵のディスクに写し、ComfyUI がそちらを先に読むようにする。

/workspace がネットワーク上のストレージ（RunPod の移行後など）だと、モデルの読み込みが
途中で止まったようになる（順に読めば 350MB/s 出るのに、ComfyUI の読み方だと数分〜止まる）。
内蔵ディスクは Pod を停止すると消えるので、runpod_start.sh が起動のたびに写し直す
（写し済みで大きさが同じものは飛ばす）。

    python local_models.py [--comfy /workspace/ComfyUI] [--dest /opt/models] [--dry-run]

写す対象は config.yaml の静止画用（本体・テキストエンコーダ・VAE・有効な LoRA）と
動画用（H3 の fl2va / ref2va・テキストエンコーダ・VAE・高速化 LoRA）。空きが足りなければ
静止画用を優先し、入らないものはネットワーク上のストレージから読む（今までどおり）。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FOLDERS = {
    # ComfyUI のフォルダ名: 探す場所（ComfyUI の models/ 配下）
    "diffusion_models": ("diffusion_models", "unet"),
    "unet": ("unet", "diffusion_models"),
    "text_encoders": ("text_encoders", "clip"),
    "vae": ("vae",),
    "loras": ("loras",),
}
KEEP_FREE = 2 * 1024**3  # 内蔵ディスクに最低限残す空き


def wanted() -> list[tuple[str, str]]:
    """(フォルダ, ファイル名) を、優先順（静止画 → 動画）で返す。"""
    from reina.config import load_config

    cfg = load_config()
    m, v = cfg.models, cfg.video
    unet = str(m["unet_gguf"])
    items = [
        ("unet" if unet.lower().endswith(".gguf") else "diffusion_models", unet),
        ("text_encoders", str(m["text_encoder"])),
        ("vae", str(m["vae"])),
    ]
    items += [("loras", str(l["name"])) for l in cfg.loras if float(l.get("strength", 0) or 0) != 0]
    vm = v["models"]
    items += [
        ("text_encoders", vm["text_encoder"]),
        ("vae", vm["video_vae"]),
        ("vae", vm["audio_vae"]),
        ("diffusion_models", vm["fl2va"]),
    ]
    items += [("loras", s["lora"]) for s in v.get("turbo_settings", {}).values()]
    items += [("diffusion_models", vm["ref2va"])]
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def find(comfy: Path, folder: str, name: str) -> Path | None:
    for sub in FOLDERS.get(folder, (folder,)):
        path = comfy / "models" / sub / name
        if path.is_file():
            return path
    return None


def write_extra_paths(comfy: Path, dest: Path) -> None:
    """ComfyUI が内蔵ディスクを先に探すようにする（is_default で先頭に入る）。"""
    lines = [
        "# scripts/local_models.py が作成。内蔵ディスクに写したモデルを先に読む",
        "reina_local:",
        f"  base_path: {dest}",
        "  is_default: true",
    ]
    lines += [f"  {folder}: {folder}" for folder in ("diffusion_models", "unet", "text_encoders", "vae", "loras")]
    (comfy / "extra_model_paths.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--comfy", default="/workspace/ComfyUI")
    parser.add_argument("--dest", default="/opt/models")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    comfy, dest = Path(args.comfy), Path(args.dest)

    dest.mkdir(parents=True, exist_ok=True)
    copied = skipped = missing = 0
    for folder, name in wanted():
        src = find(comfy, folder, name)
        if src is None:
            # どこにも無いものは読み込みもされないので、dynamic VRAM の判断には数えない
            print(f"  -- 見つからない: {folder}/{name}")
            continue
        target = dest / folder / name
        size = src.stat().st_size
        if target.is_file() and target.stat().st_size == size:
            skipped += 1
            continue
        free = shutil.disk_usage(dest).free
        if size + KEEP_FREE > free:
            print(f"  !! 空きが足りないので写しません: {name}（{size / 1e9:.1f}GB / 空き {free / 1e9:.1f}GB）")
            missing += 1
            continue
        print(f"  写す: {folder}/{name}（{size / 1e9:.1f}GB）", flush=True)
        if args.dry_run:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        with src.open("rb") as fin, partial.open("wb") as fout:
            shutil.copyfileobj(fin, fout, 64 * 1024 * 1024)
        partial.rename(target)
        copied += 1
    if not args.dry_run:
        write_extra_paths(comfy, dest)
    print(f"==> 内蔵ディスクのモデル: 新しく写した {copied} / 写し済み {skipped}（{dest}）")
    if missing:
        print(f"    写せなかったもの {missing} 件はネットワーク上のストレージから読みます")
    # 3 = 全部は写せていない（runpod_start.sh が dynamic VRAM を切る目印にする）
    return 3 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
