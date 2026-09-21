#!/usr/bin/env bash
# hf download --local-dir は repo のパス構造（split_files/... など）をそのまま作るので、
# ComfyUI が見る models/<種別>/ 直下に平す。
# mv -f で上書きするため、再実行しても二重コピーが残らない。
#   bash flatten_models.sh [ComfyUIのパス]
set -uo pipefail

COMFY="${1:-/workspace/ComfyUI}"

for dir in unet diffusion_models text_encoders vae loras; do
  target="$COMFY/models/$dir"
  [ -d "$target" ] || continue

  while IFS= read -r f; do
    [ -n "$f" ] || continue
    base="$(basename "$f")"
    if [ -e "$target/$base" ] && [ "$(stat -c%s "$f")" = "$(stat -c%s "$target/$base")" ]; then
      echo "  重複を削除: ${f#"$COMFY/models/"}"
      rm -f "$f"
    else
      mv -f "$f" "$target/$base"
    fi
  done < <(find "$target" -mindepth 2 -type f \( -name '*.safetensors' -o -name '*.gguf' \) 2>/dev/null)

  # 空になったサブディレクトリ（深い階層から順に）を消す
  find "$target" -mindepth 1 -depth -type d -empty -delete 2>/dev/null || true
done
