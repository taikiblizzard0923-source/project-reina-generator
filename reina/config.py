"""config.yaml の読み込みとデフォルト値。"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "comfyui": {
        "url": None,  # RunPod 等のリモート: "https://<POD_ID>-8188.proxy.runpod.net"
        "host": "127.0.0.1",
        "port": 8188,
        "output_dir": None,
        "verify_tls": True,
        "auth": {},  # {"bearer": "..."} または {"username": "...", "password": "..."}
    },
    "models": {
        "unet_gguf": "Qwen-Image-2.1-Q4_K_M.gguf",
        "text_encoder": "qwen3vl_8b_int8_convrot.safetensors",
        "text_encoder_is_gguf": False,
        "vae": "qwen_image_2.1_vae_bf16.safetensors",
        "clip_type": "qwen_image",
    },
    "defaults": {
        "width": 832,
        "height": 1216,
        "steps": 25,
        "cfg": 2.0,
        "shift": 3.1,
        "sampler": "euler",
        "scheduler": "simple",
        "denoise": 1.0,
        "batch_size": 1,
        "reference_megapixels": 1.0,
        "reference_denoise": 1.0,
        "edit_denoise": 1.0,
    },
    "loras": [],
    # MiniMax H3（音声付き動画）。ファイルは Comfy-Org/MiniMax-H3 の配布名。
    # 拡散モデルは PyTorch cu130 未満なら fp8_scaled（配布元の推奨）
    "video": {
        "models": {
            "fl2va": "minimax_h3_fl2va_pruned_fp8_scaled.safetensors",
            "ref2va": "minimax_h3_ref2va_pruned_fp8_scaled.safetensors",
            "text_encoder": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
            "video_vae": "minimax_h3_video_vae_fp16.safetensors",
            "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
            "fl2va_turbo_lora": "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors",
            "ref2va_turbo_lora": "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
        },
        "seconds": 5,
        "megapixels": 0.4,
        # 高速化 LoRA（4ステップで生成）。false にすると LoRA 無しの steps で回す
        "turbo": True,
        "turbo_steps": 4,
        "steps": 20,
        "sampler": "res_multistep",
        # 公式テンプレートの注記: 参照画像を使う ref2va は beta / normal の方が良い
        "scheduler_i2v": "simple",
        "scheduler_r2v": "beta",
        # match: 参照画像を生成サイズに縮める（速い） / max: 大きいまま（同一性は強いが遅い）
        "ref_image_size": "match",
    },
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
    video: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @property
    def server_url(self) -> str:
        """環境変数 REINA_COMFY_URL > config の url > host:port の順で決まる。"""
        env = os.environ.get("REINA_COMFY_URL")
        if env:
            return env
        if self.comfyui.get("url"):
            return str(self.comfyui["url"])
        return f"http://{self.comfyui['host']}:{self.comfyui['port']}"

    @property
    def auth(self) -> dict:
        auth = dict(self.comfyui.get("auth") or {})
        for key, env in (("bearer", "REINA_COMFY_TOKEN"), ("username", "REINA_COMFY_USER"), ("password", "REINA_COMFY_PASSWORD")):
            if os.environ.get(env):
                auth[key] = os.environ[env]
        return auth

    @property
    def verify_tls(self) -> bool:
        return bool(self.comfyui.get("verify_tls", True))


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
        video=merged["video"],
        path=path,
    )
