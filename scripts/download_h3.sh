#!/usr/bin/env bash
# MiniMax H3（音声付き動画）のモデル一式を ComfyUI の models/ に取得する（約63GB）。
#   bash scripts/download_h3.sh [ComfyUIのパス]
#
# MiniMax H3 Community License は米国・EU・英国・韓国での実行も出力の利用も禁じているので、
# RunPod のデータセンターがそれらの地域なら取得しない。
set -euo pipefail

COMFY="${1:-/workspace/ComfyUI}"
REPO="Comfy-Org/MiniMax-H3"

# RunPod のデータセンター ID（例: EU-SE-1, AP-JP-1）。環境変数が無ければネットワークボリュームの名前から読む
dc="${RUNPOD_DC_ID:-}"
if [ -z "$dc" ]; then
  dc="$(df /workspace 2>/dev/null | awk 'NR==2 {print $1}' | sed -n 's/.*#\([a-zA-Z0-9-]*\)\.runpod\.net.*/\1/p')"
fi
dc_upper="$(echo "$dc" | tr '[:lower:]' '[:upper:]')"
case "$dc_upper" in
  US-*|EU-*|*-GB-*|*-UK-*|*-KR-*|GB-*|UK-*|KR-*)
    echo "!! この Pod のデータセンターは $dc_upper です。" >&2
    echo "   MiniMax H3 のライセンスは米国・EU・英国・韓国での利用を禁じているため、取得しません。" >&2
    echo "   日本など対象外の地域の Pod で実行してください。" >&2
    exit 1
    ;;
  "")
    echo "※ データセンターを判定できませんでした。米国・EU・英国・韓国の Pod では使わないでください。"
    ;;
  *)
    echo "データセンター: $dc_upper"
    ;;
esac

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
