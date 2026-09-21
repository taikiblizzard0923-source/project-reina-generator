"""config.yaml の読み込みとデフォルト値。"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "comfyui": {"host": "127.0.0.1", "port": 8188, "output_dir": None},
    "models": {
        "unet_gguf": "Qwen-Image-2.1-Q4_K_M.gguf",
        "text_encoder": "qwen3vl_8b_int8_convrot.safetensors",
        "text_encoder_is_gguf": False,
        "vae": "qwen_image_2.1_vae_bf16.safetensors",
        "clip_type": "qwen_image",
    },
    "defaults": {
        "width": 1024,
        "height": 1536,
        "steps": 20,
        "cfg": 2.5,
        "shift": 3.1,
        "sampler": "euler",
        "scheduler": "simple",
        "denoise": 1.0,
        "batch_size": 1,
        "reference_megapixels": 1.0,
        "reference_denoise": 1.0,
    },
    "loras": [],
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class Config:
    comfyui: dict[str, Any] = field(default_factory=dict)
    models: dict[str, Any] = field(default_factory=dict)
    defaults: dict[str, Any] = field(default_factory=dict)
    loras: list[dict[str, Any]] = field(default_factory=list)
    path: Path | None = None

    @property
    def server_address(self) -> str:
        return f"{self.comfyui['host']}:{self.comfyui['port']}"

    @property
    def base_url(self) -> str:
        return f"http://{self.server_address}"


def find_config(explicit: str | None = None) -> Path | None:
    """明示指定 > ./config.yaml > ./config.example.yaml の順に探す。"""
    if explicit:
        return Path(explicit)
    root = Path(__file__).resolve().parent.parent
    for name in ("config.yaml", "config.example.yaml"):
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def load_config(explicit: str | None = None) -> Config:
    path = find_config(explicit)
    raw: dict[str, Any] = {}
    if path and path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    merged = _merge(DEFAULTS, raw)
    return Config(
        comfyui=merged["comfyui"],
        models=merged["models"],
        defaults=merged["defaults"],
        loras=merged.get("loras") or [],
        path=path,
    )
