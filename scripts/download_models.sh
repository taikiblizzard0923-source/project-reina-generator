#!/usr/bin/env bash
# Qwen-Image 2.1 の GGUF / テキストエンコーダ / VAE を ComfyUI の models/ に取得する。
#   bash scripts/download_models.sh [ComfyUIのパス]
#
# 量子化を変えたいとき:  QUANT=Q6_K bash scripts/download_models.sh
# 別リポジトリを使うとき: GGUF_REPO=<user>/<repo> GGUF_FILE=<name>.gguf bash scripts/download_models.sh
set -euo pipefail

COMFY="${1:-$HOME/ComfyUI}"
QUANT="${QUANT:-Q4_K_M}"
GGUF_REPO="${GGUF_REPO:-abenzerps/Qwen-Image-2.1-GGUF}"
GGUF_FILE="${GGUF_FILE:-Qwen-Image-2.1-${QUANT}.gguf}"

# テキストエンコーダ / VAE は Comfy-Org の公式分割版
TE_REPO="${TE_REPO:-Comfy-Org/Qwen-Image_ComfyUI}"
TE_FILE="${TE_FILE:-split_files/text_encoders/qwen3vl_8b_int8_convrot.safetensors}"
VAE_REPO="${VAE_REPO:-Comfy-Org/Qwen-Image_ComfyUI}"
VAE_FILE="${VAE_FILE:-split_files/vae/qwen_image_2.1_vae_bf16.safetensors}"

command -v hf >/dev/null 2>&1 || pip install -U "huggingface_hub[cli]"

mkdir -p "$COMFY/models/unet" "$COMFY/models/text_encoders" "$COMFY/models/vae"

echo "==> 拡散モデル(GGUF): $GGUF_REPO / $GGUF_FILE"
echo "    ※ リポジトリの Files タブで実ファイル名を確認し、違えば GGUF_FILE= で指定してください"
hf download "$GGUF_REPO" "$GGUF_FILE" --local-dir "$COMFY/models/unet"

echo "==> テキストエンコーダ: $TE_REPO / $TE_FILE"
hf download "$TE_REPO" "$TE_FILE" --local-dir "$COMFY/models/text_encoders"

echo "==> VAE: $VAE_REPO / $VAE_FILE"
hf download "$VAE_REPO" "$VAE_FILE" --local-dir "$COMFY/models/vae"

# split_files/... のサブディレクトリを models 直下に平す
for dir in text_encoders vae; do
  find "$COMFY/models/$dir/split_files" -type f -name '*.safetensors' -exec mv -n {} "$COMFY/models/$dir/" \; 2>/dev/null || true
  rm -rf "$COMFY/models/$dir/split_files"
done

echo
echo "==> 取得結果"
ls -lh "$COMFY/models/unet" "$COMFY/models/text_encoders" "$COMFY/models/vae"
echo
echo "上のファイル名を config.yaml の models: に転記してから 'python -m reina doctor' を実行してください。"
