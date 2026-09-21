#!/usr/bin/env bash
# Pod 上で ComfyUI を 0.0.0.0:8188 で起動する。
#   bash runpod_start.sh              # フォアグラウンド
#   bash runpod_start.sh --daemon     # バックグラウンド (ログ: /workspace/comfyui.log)
#
# VRAM が足りない場合:  COMFY_ARGS="--lowvram" bash runpod_start.sh
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
COMFY="$WORKSPACE/ComfyUI"
PORT="${PORT:-8188}"
COMFY_ARGS="${COMFY_ARGS:-}"

cd "$COMFY"
# shellcheck disable=SC1091
source venv/bin/activate

# RunPod のプロキシから届かせるため 0.0.0.0 で待ち受ける（127.0.0.1 では外から見えない）
CMD=(python main.py --listen 0.0.0.0 --port "$PORT")
# shellcheck disable=SC2206
[ -n "$COMFY_ARGS" ] && CMD+=($COMFY_ARGS)

if [ "${1:-}" = "--daemon" ]; then
  LOG="$WORKSPACE/comfyui.log"
  nohup "${CMD[@]}" >"$LOG" 2>&1 &
  echo "起動しました (pid $!) / ログ: $LOG"
  echo "確認: tail -f $LOG"
else
  exec "${CMD[@]}"
fi
