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
# venv を内蔵ディスクに置いている場合、Pod を停止すると中身が消えてリンクだけが残る
if [ ! -x venv/bin/python ]; then
  echo "==> Python 環境がないので作り直します（5分ほど）"
  bash "$(dirname "$(readlink -f "$0")")/make_venv.sh" "$COMFY"
fi
# shellcheck disable=SC1091
source venv/bin/activate

# /workspace がネットワーク上のストレージ（venv を内蔵ディスクに置いている）なら、
# 使うモデルも内蔵ディスクに写して、そちらから読ませる（local_models.py の説明を参照）
if [ -L venv ] && [ "${SKIP_LOCAL_MODELS:-0}" != "1" ]; then
  echo "==> モデルを内蔵ディスクに写します（写し済みは飛ばす。初回は数分）"
  # 全部写せたら 0、空き不足などで残りがあれば 3（そのときだけ dynamic VRAM を切る）
  # set -e で止まらないよう、終了コードはここで受け取る
  LOCAL_MODELS_STATUS=0
  python "$(dirname "$(readlink -f "$0")")/local_models.py" --comfy "$COMFY" || LOCAL_MODELS_STATUS=$?
fi

# RunPod のプロキシから届かせるため 0.0.0.0 で待ち受ける（127.0.0.1 では外から見えない）
CMD=(python main.py --listen 0.0.0.0 --port "$PORT")
# venv を内蔵ディスクに置いている＝/workspace がネットワーク上のストレージ（make_venv.sh）。
# そこに置いた SQLite はロックが残りやすく、前の ComfyUI が終わっていても
# "Another ComfyUI is already using this database" で起動が止まるので、DB も内蔵ディスクに置く
if [ -L venv ] && grep -q -- "--database-url" comfy/cli_args.py 2>/dev/null \
   && [[ "$COMFY_ARGS" != *--database-url* ]]; then
  mkdir -p /opt/comfy-db
  CMD+=(--database-url "sqlite:////opt/comfy-db/comfyui.db")
fi
# 新しい読み込み方式（dynamic VRAM）は重みをファイルから細切れに読むので、ネットワーク上の
# ストレージから読むモデルがあると止まったように遅くなる。全部を内蔵ディスクに写せたときは
# 移行前と同じく dynamic VRAM のまま（切ると H3 の生成中に VRAM が溢れることがある）
if [ -L venv ] && [ "${LOCAL_MODELS_STATUS:-3}" != "0" ] \
   && grep -q -- "--disable-dynamic-vram" comfy/cli_args.py 2>/dev/null \
   && [[ "$COMFY_ARGS" != *dynamic-vram* ]]; then
  CMD+=(--disable-dynamic-vram)
fi
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
