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

GGUF_REPO="${GGUF_REPO:-vantagewithai/Qwen-Image-2.1-ComfyUI-GGUF,AlperKTS/Qwen-Image-2.1-GGUF,Abiray/Qwen-Image-2.1-GGUF,abenzerps/Qwen-Image-2.1-GGUF}"
# テキストエンコーダ / VAE は Comfy-Org の公式リパック（2.1 は Qwen3-VL 8B 系）
TE_REPO="${TE_REPO:-Comfy-Org/Qwen-Image-2.1}"
VAE_REPO="${VAE_REPO:-Comfy-Org/Qwen-Image-2.1}"
# int8 は約9GB、bf16 は約17.5GB。TE_VARIANT=bf16 で切り替え
TE_VARIANT="${TE_VARIANT:-int8}"

# 拡散モデルの形式:
#   safetensors … Comfy-Org 公式（既定）。ComfyUI v0.37.0 が本体で 2.1 に対応済み
#   gguf        … 量子化GGUF（VRAM が足りない場合のみ）。2026-09 時点で
#                 city96/ComfyUI-GGUF は qwen_image アーキテクチャ未対応のため
#                 "Unknown model architecture" で失敗する
MODEL_FORMAT="${MODEL_FORMAT:-safetensors}"
DIT_REPO="${DIT_REPO:-Comfy-Org/Qwen-Image-2.1}"
DIT_VARIANT="${DIT_VARIANT:-int8}"

# ダウンロードには hf CLI を使う（ファイル名の解決は標準ライブラリのみで行う）
if ! command -v hf >/dev/null 2>&1; then
  pip install -q -U "huggingface_hub[cli]"
fi
if ! command -v hf >/dev/null 2>&1; then
  echo "!! hf コマンドが見つかりません。pip install -U 'huggingface_hub[cli]' を試してください。" >&2
  exit 1
fi

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

# 拡散モデル
if [ "$MODEL_FORMAT" = "safetensors" ]; then
  mkdir -p "$COMFY/models/diffusion_models"
  fetch "拡散モデル(safetensors)" "$COMFY/models/diffusion_models" "$DIT_REPO" ".safetensors" \
    "+diffusion_models" "+2.1" "$DIT_VARIANT"
elif [ -n "${GGUF_FILE:-}" ]; then
  echo "==> 拡散モデル(GGUF): ${GGUF_REPO%%,*} / $GGUF_FILE （明示指定）"
  hf download "${GGUF_REPO%%,*}" "$GGUF_FILE" --local-dir "$COMFY/models/unet"
else
  fetch "拡散モデル(GGUF)" "$COMFY/models/unet" "$GGUF_REPO" ".gguf" \
    "+$(echo "$QUANT" | tr '[:upper:]' '[:lower:]')" "2.1"
fi

# +qwen3vl は必須。旧世代の qwen_2.5_vl_7b を掴むと 2.1 では動かない
fetch "テキストエンコーダ" "$COMFY/models/text_encoders" "$TE_REPO" ".safetensors" \
  "+qwen3vl" "+text_encoders" "$TE_VARIANT"

fetch "VAE" "$COMFY/models/vae" "$VAE_REPO" ".safetensors" "+vae" "2.1"

# repo のパス構造を models/<種別>/ 直下に平す（重複は削除）
bash "$HERE/flatten_models.sh" "$COMFY"

echo
echo "==> 取得結果"
for dir in unet diffusion_models text_encoders vae; do
  [ -d "$COMFY/models/$dir" ] || continue
  printf '%-18s %s\n' "$dir" "$(du -sh "$COMFY/models/$dir" 2>/dev/null | cut -f1)"
  ls -1 "$COMFY/models/$dir" 2>/dev/null | sed 's/^/                 /'
done

echo
echo "==> config.yaml に実ファイル名を反映"
python3 "$HERE/sync_config.py" "$COMFY" "$(dirname "$HERE")/config.yaml" "$MODEL_FORMAT"
