#!/usr/bin/env bash
# MiniMax H3（音声付き動画）のモデル一式を ComfyUI の models/ に取得する（約63GB）。
#   bash scripts/download_h3.sh [ComfyUIのパス]
#
# ライセンス（MiniMax H3 Community License）は米国・EU・英国・韓国での利用を禁じている。
set -euo pipefail

COMFY="${1:-/workspace/ComfyUI}"
REPO="Comfy-Org/MiniMax-H3"

if ! command -v hf >/dev/null 2>&1; then
  pip install -q -U "huggingface_hub[cli]"
fi

# リポジトリ内のフォルダ名が ComfyUI の models/ 配下の名前と同じなので、そのまま models/ に落とす
FILES=(
  diffusion_models/minimax_h3_fl2va_pruned_fp8_scaled.safetensors
  diffusion_models/minimax_h3_ref2va_pruned_fp8_scaled.safetensors
  text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
  vae/minimax_h3_video_vae_fp16.safetensors
  vae/minimax_h3_audio_vae_fp32.safetensors
  loras/minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors
  loras/minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors
)

for f in "${FILES[@]}"; do
  if [ -s "$COMFY/models/$f" ]; then
    echo "==> あり: $f"
    continue
  fi
  echo "==> 取得: $f"
  hf download "$REPO" "$f" --local-dir "$COMFY/models"
done
rm -rf "$COMFY/models/.cache"

echo
echo "完了。ComfyUI を再起動すると使えます:  bash go.sh restart"
