#!/usr/bin/env bash
# RunPod の Pod 上で実行する（Web Terminal か SSH から）。
#
#   bash runpod_setup.sh            # ComfyUI + GGUF ノード + モデル取得まで
#   SKIP_MODELS=1 bash runpod_setup.sh
#   QUANT=Q6_K bash runpod_setup.sh
#
# すべて /workspace（ネットワークボリューム）配下に入れるので、
# Pod を作り直してもボリュームを付け替えれば再ダウンロード不要。
set -euo pipefail

WORKSPACE="${WORKSPACE:-/workspace}"
COMFY="$WORKSPACE/ComfyUI"
QUANT="${QUANT:-Q4_K_M}"

if [ ! -d "$WORKSPACE" ]; then
  echo "!! $WORKSPACE がありません。RunPod のボリューム設定を確認してください。" >&2
  exit 1
fi

# $WORKSPACE が / と同じデバイスなら永続ボリュームが無い＝Pod 停止で全部消える
if [ "$(stat -c%d "$WORKSPACE" 2>/dev/null)" = "$(stat -c%d / 2>/dev/null)" ]; then
  cat >&2 <<'WARN'

  !! 警告: /workspace が永続ボリュームではありません（コンテナディスク上です）
     このまま進めると、Pod を停止した時点でモデル約30GBが消えます。
     RunPod の Pod 作成時に Volume Disk 60GB 以上 / マウントパス /workspace を
     設定してください。

WARN
  sleep 5
fi

echo "==> 依存パッケージ"
apt-get update -qq && apt-get install -y -qq git wget curl libgl1 libglib2.0-0 >/dev/null

echo "==> ComfyUI"
if [ ! -d "$COMFY" ]; then
  git clone --depth 1 https://github.com/comfyanonymous/ComfyUI.git "$COMFY"
else
  git -C "$COMFY" pull --ff-only || true
fi

cd "$COMFY"

# RunPod の PyTorch イメージには torch が入っているので、
# --system-site-packages でそれを再利用しつつ追加分だけボリュームに置く。
if [ ! -d venv ]; then
  echo "==> venv 作成 (system-site-packages を継承)"
  python3 -m venv --system-site-packages venv
fi
# shellcheck disable=SC1091
source venv/bin/activate
pip install -q -U pip

python -c "import torch" 2>/dev/null || {
  echo "==> torch が無いのでインストール"
  pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124
}

echo "==> ComfyUI 依存"
pip install -q -r requirements.txt

# ディスク不足などで pip が中断されると中身の無いパッケージが残り、
# import は通るのに中身が無い、という分かりにくい形で後から壊れる
echo "==> 依存パッケージの健全性チェック"
python "$(dirname "$(readlink -f "$0")")/fix_deps.py" --requirements requirements.txt || true

echo "==> ComfyUI-GGUF"
mkdir -p custom_nodes
if [ ! -d custom_nodes/ComfyUI-GGUF ]; then
  git clone --depth 1 https://github.com/city96/ComfyUI-GGUF.git custom_nodes/ComfyUI-GGUF
else
  git -C custom_nodes/ComfyUI-GGUF pull --ff-only || true
fi
pip install -q -r custom_nodes/ComfyUI-GGUF/requirements.txt

mkdir -p models/unet models/text_encoders models/vae models/loras input output

if [ "${SKIP_MODELS:-0}" != "1" ]; then
  echo "==> モデル取得 (format=${MODEL_FORMAT:-safetensors} quant=$QUANT)"
  QUANT="$QUANT" MODEL_FORMAT="${MODEL_FORMAT:-safetensors}" \
    bash "$(dirname "$(readlink -f "$0")")/download_models.sh" "$COMFY"
else
  echo "==> SKIP_MODELS=1 のためモデル取得をスキップ"
fi

# ブラウザだけで使えるようにしておく（Pod 作り直しのたびに手で入れ直さずに済む）
HERE_DIR="$(dirname "$(readlink -f "$0")")"
TARGET="$COMFY/custom_nodes/reina_webui"
rm -rf "$TARGET"
ln -s "$(dirname "$HERE_DIR")/comfy_extension" "$TARGET"
echo "==> Web UI を組み込みました（<ComfyUI の URL>/reina）"

cat <<MSG

======================================================================
 セットアップ完了: $COMFY

 起動:
   bash $(dirname "$(readlink -f "$0")")/runpod_start.sh

 ブラウザだけで使う（スマホ可）:
   https://${RUNPOD_POD_ID:-<POD_ID>}-8188.proxy.runpod.net/reina

 Pod を作り直した場合は、この画面でバックアップ(.tgz)を
 アップロードすれば設定と参照画像が戻ります。

 手元の PC からは:
   python -m reina doctor --server https://<POD_ID>-8188.proxy.runpod.net
======================================================================
MSG
