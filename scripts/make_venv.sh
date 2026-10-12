#!/usr/bin/env bash
# ComfyUI 用の Python 環境（venv）を作り、依存パッケージを入れる。
#
#   bash make_venv.sh [/workspace/ComfyUI]
#
# /workspace がネットワーク上のストレージ（RunPod の移行後など。df に mfs#... と出る）だと、
# 細かいファイルを大量に書く pip install が止まったように遅くなる。その場合は venv を
# Pod 内蔵のディスク（/opt/comfy-venv）に作り、$COMFY/venv をそこへのリンクにする。
# 内蔵ディスクは Pod を停止すると消えるので、runpod_start.sh が起動時に作り直す（5分ほど）。
# 場所を明示するなら VENV_DIR=/path/to/venv。
set -euo pipefail

COMFY="${1:-${WORKSPACE:-/workspace}/ComfyUI}"
HERE="$(dirname "$(readlink -f "$0")")"

is_network_fs() {
  local type
  type="$(findmnt -no FSTYPE -T "$1" 2>/dev/null || stat -f -c %T "$1" 2>/dev/null || true)"
  case "$type" in
    fuse*|nfs*|cifs|smb*|ceph*) return 0 ;;
  esac
  df -P "$1" 2>/dev/null | tail -1 | grep -q '^mfs#'
}

VENV="${VENV_DIR:-}"
if [ -z "$VENV" ]; then
  if is_network_fs "$COMFY"; then
    VENV=/opt/comfy-venv
  else
    VENV="$COMFY/venv"
  fi
fi

if [ "$VENV" != "$COMFY/venv" ]; then
  echo "==> venv は内蔵ディスクに置きます: $VENV（$COMFY/venv はそこへのリンク）"
  if [ -e "$COMFY/venv" ] && [ ! -L "$COMFY/venv" ]; then
    # 同じボリューム内の名前変更なので一瞬で終わる（中身は消さない）
    old="$COMFY/venv.old.$(date +%Y%m%d%H%M%S)"
    mv "$COMFY/venv" "$old"
    echo "    既存の venv は $old に退避しました（不要なら削除してください）"
  fi
  ln -sfn "$VENV" "$COMFY/venv"
fi

if [ ! -x "$VENV/bin/python" ]; then
  echo "==> venv 作成 (system-site-packages を継承)"
  python3 -m venv --system-site-packages "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install -q -U pip

python -c "import torch" 2>/dev/null || {
  echo "==> torch が無いのでインストール"
  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
}

echo "==> ComfyUI 依存"
pip install --progress-bar off -r "$COMFY/requirements.txt"

# ディスク不足などで pip が中断されると中身の無いパッケージが残り、
# import は通るのに中身が無い、という分かりにくい形で後から壊れる
echo "==> 依存パッケージの健全性チェック"
python "$HERE/fix_deps.py" --requirements "$COMFY/requirements.txt" || true

if [ -f "$COMFY/custom_nodes/ComfyUI-GGUF/requirements.txt" ]; then
  echo "==> ComfyUI-GGUF 依存"
  pip install --progress-bar off -r "$COMFY/custom_nodes/ComfyUI-GGUF/requirements.txt"
fi

# Web UI はこのリポジトリの CLI をサブプロセスで呼ぶ
pip install -q -r "$(dirname "$HERE")/requirements.txt" || true
echo "==> venv 準備完了: $VENV"
