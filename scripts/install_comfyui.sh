#!/usr/bin/env bash
# ComfyUI 本体 + GGUF カスタムノードをセットアップする（GPU マシンで実行）。
#   bash scripts/install_comfyui.sh [インストール先]   既定: ~/ComfyUI
set -euo pipefail

TARGET="${1:-$HOME/ComfyUI}"

if [ ! -d "$TARGET" ]; then
  echo "==> ComfyUI を clone: $TARGET"
  git clone https://github.com/comfyanonymous/ComfyUI.git "$TARGET"
else
  echo "==> 既存の ComfyUI を更新: $TARGET"
  git -C "$TARGET" pull --ff-only
fi

cd "$TARGET"

if [ ! -d venv ]; then
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

echo "==> PyTorch"
case "$(uname -s)" in
  Darwin) pip install -U torch torchvision torchaudio ;;                                   # Apple Silicon (MPS)
  *)      pip install -U torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 ;;
esac

echo "==> ComfyUI 依存"
pip install -r requirements.txt

echo "==> ComfyUI-GGUF カスタムノード"
mkdir -p custom_nodes
if [ ! -d custom_nodes/ComfyUI-GGUF ]; then
  git clone https://github.com/city96/ComfyUI-GGUF.git custom_nodes/ComfyUI-GGUF
else
  git -C custom_nodes/ComfyUI-GGUF pull --ff-only
fi
pip install -r custom_nodes/ComfyUI-GGUF/requirements.txt

mkdir -p models/unet models/text_encoders models/vae models/loras

cat <<MSG

==> 完了: $TARGET
    起動:   cd "$TARGET" && source venv/bin/activate && python main.py --listen 127.0.0.1 --port 8188
    次:     bash scripts/download_models.sh "$TARGET"
MSG
