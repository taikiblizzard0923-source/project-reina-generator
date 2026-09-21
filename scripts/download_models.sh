#!/usr/bin/env bash
# Qwen-Image 2.1 の GGUF / テキストエンコーダ / VAE を ComfyUI の models/ に取得する。
#   bash scripts/download_models.sh [ComfyUIのパス]
#
# 配布リポジトリごとにファイル名が違うため、決め打ちせず repo の中身から解決する。
#
#   QUANT=Q6_K bash scripts/download_models.sh          量子化を変える
#   GGUF_REPO=<user>/<repo> bash ...                    別リポジトリを使う
#   GGUF_FILE=<name>.gguf  bash ...                     ファイル名を明示指定
set -euo pipefail

COMFY="${1:-/workspace/ComfyUI}"
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
QUANT="${QUANT:-Q4_K_M}"

GGUF_REPO="${GGUF_REPO:-abenzerps/Qwen-Image-2.1-GGUF,QuantStack/Qwen-Image-GGUF}"
# テキストエンコーダ / VAE は Comfy-Org の公式分割版（2.1 を先に探す）
TE_REPO="${TE_REPO:-Comfy-Org/Qwen-Image-2.1_ComfyUI,Comfy-Org/Qwen-Image_ComfyUI}"
VAE_REPO="${VAE_REPO:-Comfy-Org/Qwen-Image-2.1_ComfyUI,Comfy-Org/Qwen-Image_ComfyUI}"

command -v hf >/dev/null 2>&1 || pip install -q -U "huggingface_hub[cli]"
python3 -c "import huggingface_hub" 2>/dev/null || pip install -q -U huggingface_hub

mkdir -p "$COMFY/models/unet" "$COMFY/models/text_encoders" "$COMFY/models/vae"

# resolve <repos> <ext> <prefer...>  ->  "repo<TAB>path"
resolve() {
  python3 "$HERE/resolve_hf_file.py" "$@"
}

# fetch <label> <dest_dir> <repos> <ext> <prefer...>
fetch() {
  local label="$1" dest="$2" repos="$3" ext="$4"
  shift 4
  echo "==> $label"
  local found repo path
  if ! found="$(resolve "$repos" "$ext" "$@")"; then
    echo "!! $label のファイルを解決できませんでした。環境変数で明示指定してください。" >&2
    return 1
  fi
  repo="${found%%$'\t'*}"
  path="${found#*$'\t'}"
  echo "    -> $repo / $path"
  hf download "$repo" "$path" --local-dir "$dest"
}

# 拡散モデル(GGUF)
if [ -n "${GGUF_FILE:-}" ]; then
  echo "==> 拡散モデル(GGUF): ${GGUF_REPO%%,*} / $GGUF_FILE （明示指定）"
  hf download "${GGUF_REPO%%,*}" "$GGUF_FILE" --local-dir "$COMFY/models/unet"
else
  fetch "拡散モデル(GGUF)" "$COMFY/models/unet" "$GGUF_REPO" ".gguf" \
    "$(echo "$QUANT" | tr '[:upper:]' '[:lower:]')" "2.1"
fi

fetch "テキストエンコーダ" "$COMFY/models/text_encoders" "$TE_REPO" ".safetensors" \
  "text_encoders" "qwen3vl" "${TE_PREFER:-int8}"

fetch "VAE" "$COMFY/models/vae" "$VAE_REPO" ".safetensors" "vae" "2.1"

# split_files/... やサブディレクトリを models 直下に平す
for dir in unet text_encoders vae; do
  find "$COMFY/models/$dir" -mindepth 2 -type f \( -name '*.safetensors' -o -name '*.gguf' \) \
    -exec mv -n {} "$COMFY/models/$dir/" \; 2>/dev/null || true
  find "$COMFY/models/$dir" -mindepth 1 -type d -empty -delete 2>/dev/null || true
done

echo
echo "==> 取得結果"
for dir in unet text_encoders vae; do
  printf '%-16s %s\n' "$dir" "$(du -sh "$COMFY/models/$dir" 2>/dev/null | cut -f1)"
  ls -1 "$COMFY/models/$dir" 2>/dev/null | sed 's/^/                 /'
done

echo
echo "==> config.yaml に実ファイル名を反映"
python3 "$HERE/sync_config.py" "$COMFY" "$(dirname "$HERE")/config.yaml"
