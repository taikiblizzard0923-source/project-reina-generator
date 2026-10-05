#!/usr/bin/env bash
# MiniMax H3（音声付き動画）のモデル一式を ComfyUI の models/ に取得する（約63GB）。
#   bash scripts/download_h3.sh [ComfyUIのパス]
#
# ライセンス（MiniMax H3 Community License）は米国・EU・英国・韓国での利用を禁じている。
set -euo pipefail

COMFY="${1:-/workspace/ComfyUI}"

if ! command -v hf >/dev/null 2>&1; then
  pip install -q -U "huggingface_hub[cli]"
fi

# fetch <repo> <リポジトリ内のパス> <models/ 配下の保存先フォルダ>
fetch() {
  local repo="$1" path="$2" dest="$COMFY/models/$3"
  if [ -s "$dest/$(basename "$path")" ]; then
    echo "==> あり: $3/$(basename "$path")"
    return
  fi
  echo "==> 取得: $3/$(basename "$path")"
  local tmp="$COMFY/models/.h3_tmp"
  hf download "$repo" "$path" --local-dir "$tmp"
  mkdir -p "$dest"
  mv "$tmp/$path" "$dest/"
}

# 本体・テキストエンコーダ・VAE（Comfy-Org のリパック）
CORE=Comfy-Org/MiniMax-H3
fetch $CORE diffusion_models/minimax_h3_fl2va_pruned_fp8_scaled.safetensors diffusion_models
fetch $CORE diffusion_models/minimax_h3_ref2va_pruned_fp8_scaled.safetensors diffusion_models
fetch $CORE text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors text_encoders
fetch $CORE vae/minimax_h3_video_vae_fp16.safetensors vae
fetch $CORE vae/minimax_h3_audio_vae_fp32.safetensors vae

# 高速化 LoRA（配布元 lightx2v。新しい版はこちらにしか無い）
TURBO=lightx2v/Minimax-h3-Turbo
fetch $TURBO minimax_h3_fl2v_turbo_4step_v1.1_768p_comfyui_bf16.safetensors loras
fetch $TURBO minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors loras
# 最高品質の静止画用（8ステップ。lora= で切り替えて使う）
fetch $TURBO minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors loras

rm -rf "$COMFY/models/.h3_tmp" "$COMFY/models/.cache"

echo
echo "完了。ComfyUI を再起動すると使えます:  bash go.sh restart"
