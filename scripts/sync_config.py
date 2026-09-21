"""ComfyUI の models/ にある実ファイル名を config.yaml に書き込む。

配布者ごとにファイル名が違うので、手で転記させる代わりに実物から拾う。

usage: sync_config.py [COMFY_DIR] [CONFIG_PATH] [MODEL_FORMAT]
  MODEL_FORMAT: gguf | safetensors | auto（既定: auto = safetensors を優先）
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def pick(directory: Path, exts: tuple[str, ...], prefer: tuple[str, ...] = ()) -> str | None:
    if not directory.is_dir():
        return None
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in exts]
    if not files:
        return None

    def score(path: Path) -> tuple:
        low = path.name.lower()
        return (sum(p in low for p in prefer), path.stat().st_size)

    return max(files, key=score).name


def main() -> int:
    comfy = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/workspace/ComfyUI")
    config_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "config.yaml"
    fmt = (sys.argv[3] if len(sys.argv) > 3 else "auto").lower()

    models = comfy / "models"
    gguf = pick(models / "unet", (".gguf",)) or pick(models / "diffusion_models", (".gguf",))
    safet = pick(models / "diffusion_models", (".safetensors",), prefer=("2.1", "qwen"))
    if fmt == "gguf":
        unet = gguf or safet
    elif fmt == "safetensors":
        unet = safet or gguf
    else:
        unet = safet or gguf
    text_encoder = pick(
        models / "text_encoders", (".safetensors", ".gguf"), prefer=("qwen3vl", "qwen")
    ) or pick(models / "clip", (".safetensors", ".gguf"), prefer=("qwen3vl", "qwen"))
    vae = pick(models / "vae", (".safetensors",), prefer=("qwen", "2.1"))

    base = config_path if config_path.exists() else ROOT / "config.example.yaml"
    data = yaml.safe_load(base.read_text(encoding="utf-8")) or {}
    data.setdefault("models", {})

    updated: list[str] = []
    for key, value in (("unet_gguf", unet), ("text_encoder", text_encoder), ("vae", vae)):
        if value:
            data["models"][key] = value
            updated.append(f"  {key}: {value}")
        else:
            print(f"  ! {key}: models/ に見つかりません", file=sys.stderr)

    if text_encoder:
        data["models"]["text_encoder_is_gguf"] = text_encoder.lower().endswith(".gguf")
    if unet and not unet.lower().endswith(".gguf"):
        data["models"].setdefault("weight_dtype", "default")

    config_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    print(f"{config_path} を更新しました:")
    print("\n".join(updated) if updated else "  (更新なし)")
    return 0 if updated else 1


if __name__ == "__main__":
    raise SystemExit(main())
