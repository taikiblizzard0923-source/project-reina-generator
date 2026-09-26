#!/usr/bin/env bash
# スマホのターミナルから短く打つための入口。
# JupyterLab のターミナルは貼り付けができないので、打鍵数を最小にしてある。
#
#   bash go.sh          セットアップをバックグラウンドで開始
#   bash go.sh models   モデル取得だけやり直す（ComfyUI は入れ直さない）
#   bash go.sh official 公式 safetensors 版の拡散モデルに切り替える（VRAM 24GB+）
#   bash go.sh info <repo>  HF リポジトリの中身とサイズを見る（DL前の確認用）
#   bash go.sh clean    中断した/誤って掴んだダウンロードを消す
#   bash go.sh fixdeps  壊れた Python パッケージを検出して入れ直す
#   bash go.sh webui    ブラウザだけで使える生成 UI を ComfyUI に組み込む
#   bash go.sh lora <URL> [--name N] [--strength 0.85]  LoRA を追加
#   bash go.sh backup   設定と参照画像を1ファイルに退避（Pod 移行前に）
#   bash go.sh restore <file>  退避したファイルから復元
#   bash go.sh log      進捗を表示（Ctrl-C で抜けても処理は続く）
#   bash go.sh ps       まだ動いているか確認
#   bash go.sh start    ComfyUI を起動
#   bash go.sh restart  ComfyUI を再起動（Web UI の更新を反映するとき）
#   bash go.sh check    doctor（疎通・モデル確認）
#   bash go.sh url      接続用 URL を表示
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG=/workspace/setup.log
COMFY=/workspace/ComfyUI

case "${1:-setup}" in
  setup)
    if pgrep -f runpod_setup.sh >/dev/null; then
      echo "すでに実行中です。進捗は:  bash go.sh log"
      exit 0
    fi
    echo "セットアップをバックグラウンドで開始します → $LOG"
    nohup bash "$HERE/scripts/runpod_setup.sh" >"$LOG" 2>&1 &
    sleep 2
    echo "開始しました (pid $!)"
    echo "進捗:  bash go.sh log"
    ;;

  models)
    if pgrep -f "download_models.sh|runpod_setup.sh" >/dev/null; then
      echo "すでに実行中です。進捗は:  bash go.sh log"
      exit 0
    fi
    echo "モデル取得だけ再実行します → $LOG"
    nohup bash "$HERE/scripts/download_models.sh" "$COMFY" >"$LOG" 2>&1 &
    sleep 2
    echo "開始しました (pid $!)"
    echo "進捗:  bash go.sh log"
    ;;

  official)
    if pgrep -f "download_models.sh|runpod_setup.sh" >/dev/null; then
      echo "すでに実行中です。進捗は:  bash go.sh log"
      exit 0
    fi
    echo "公式 safetensors 版の拡散モデルを取得します → $LOG"
    MODEL_FORMAT=safetensors nohup bash "$HERE/scripts/download_models.sh" "$COMFY" >"$LOG" 2>&1 &
    sleep 2
    echo "開始しました (pid $!)"
    echo "進捗:  bash go.sh log"
    ;;

  fixdeps)
    PY="$COMFY/venv/bin/python"
    [ -x "$PY" ] || PY="$(command -v python3)"
    "$PY" "$HERE/scripts/fix_deps.py" --requirements "$COMFY/requirements.txt" "${@:2}"
    ;;

  lora)
    if [ -z "${2:-}" ]; then
      echo "使い方: bash go.sh lora <URL> [--name NAME] [--strength 0.85]"
      exit 1
    fi
    python3 "$HERE/scripts/add_lora.py" "${@:2}" --comfy "$COMFY"
    ;;

  backup)
    python3 "$HERE/scripts/backup.py" create "${@:2}"
    ;;

  restore)
    if [ -z "${2:-}" ]; then
      echo "使い方: bash go.sh restore <バックアップ.tgz>"
      echo
      echo "downloads/ にあるもの:"
      ls -1t "$HERE/downloads"/*.tgz 2>/dev/null | head -5 || echo "  (なし)"
      exit 1
    fi
    python3 "$HERE/scripts/backup.py" restore "${@:2}"
    ;;

  webui)
    TARGET="$COMFY/custom_nodes/reina_webui"
    rm -rf "$TARGET"
    ln -s "$HERE/comfy_extension" "$TARGET"
    echo "組み込みました: $TARGET -> $HERE/comfy_extension"

    # UI は CLI をサブプロセスで呼ぶので、ComfyUI の venv に依存を入れておく
    PY="$COMFY/venv/bin/python"
    [ -x "$PY" ] || PY="$(command -v python3)"
    "$PY" -m pip install -q -r "$HERE/requirements.txt" || true

    echo "ComfyUI を再起動します"
    pkill -f "main.py --listen" 2>/dev/null || true
    sleep 2
    bash "$HERE/scripts/runpod_start.sh" --daemon
    sleep 3
    id="${RUNPOD_POD_ID:-<POD_ID>}"
    cat <<MSG

======================================================================
 ブラウザでこれを開いてください（スマホでも可）:

   https://${id}-8188.proxy.runpod.net/reina

 起動まで1分ほどかかります。開けない場合:
   bash go.sh log-comfy
======================================================================
MSG
    ;;

  log-comfy)
    tail -n 40 /workspace/comfyui.log
    ;;

  start2)
    bash "$HERE/scripts/runpod_start.sh" --daemon
    echo "起動ログを表示します（Ctrl-C で抜けてもComfyUIは動き続けます）"
    sleep 2
    tail -f /workspace/comfyui.log
    ;;

  info)
    repo="${2:-vantagewithai/Qwen-Image-2.1-ComfyUI-GGUF}"
    python3 "$HERE/scripts/hf_info.py" "$repo"
    ;;

  clean)
    pkill -f "hf download" 2>/dev/null && echo "ダウンロードを中断しました" || true
    sleep 1
    echo "=== 掃除 ==="
    # 途中で止まった一時ファイルと、平す前のサブディレクトリ
    find "$COMFY/models" -name '*.incomplete' -delete 2>/dev/null || true
    rm -rf "$COMFY"/models/*/.cache 2>/dev/null || true
    # Qwen-Image 2.1 は Qwen3-VL 8B 系が必要。世代違いを掴んでいたら消す
    while IFS= read -r f; do
      case "$(basename "$f" | tr '[:upper:]' '[:lower:]')" in
        *qwen3vl*) ;;
        *) echo "  削除: $f"; rm -f "$f" ;;
      esac
    done < <(find "$COMFY/models/text_encoders" -type f -name '*.safetensors' 2>/dev/null)
    bash "$HERE/scripts/flatten_models.sh" "$COMFY"
    echo
    exec bash "${BASH_SOURCE[0]}" ps
    ;;

  log)
    [ -f "$LOG" ] || { echo "$LOG がまだありません。まず:  bash go.sh"; exit 1; }
    tail -n 30 -f "$LOG"
    ;;

  ps)
    if pgrep -af "runpod_setup.sh|hf |pip " >/dev/null; then
      echo "=== 実行中のプロセス ==="
      pgrep -af "runpod_setup.sh|hf |pip "
    else
      echo "セットアップのプロセスは動いていません。"
    fi
    echo
    echo "=== ダウンロード済みモデル ==="
    for d in unet diffusion_models text_encoders vae; do
      [ -d "$COMFY/models/$d" ] || continue
      printf '%-18s ' "$d"
      if [ -d "$COMFY/models/$d" ] && [ -n "$(ls -A "$COMFY/models/$d" 2>/dev/null)" ]; then
        du -sh "$COMFY/models/$d" 2>/dev/null | cut -f1
        ls -1 "$COMFY/models/$d" | sed 's/^/                 /'
      else
        echo "(空)"
      fi
    done
    ;;

  start)
    bash "$HERE/scripts/runpod_start.sh" --daemon
    ;;

  restart)
    # Web UI（custom_nodes/reina_webui）のサーバー側は起動時にしか読み込まれない
    if pkill -f "main.py --listen"; then
      echo "ComfyUI を停止しました"
      for _ in $(seq 1 20); do pgrep -f "main.py --listen" >/dev/null || break; sleep 1; done
    fi
    bash "$HERE/scripts/runpod_start.sh" --daemon
    ;;

  check)
    cd "$HERE" && python -m reina doctor
    ;;

  url)
    id="${RUNPOD_POD_ID:-<POD_ID>}"
    echo "ComfyUI:  https://${id}-8188.proxy.runpod.net"
    ;;

  *)
    sed -n '2,20p' "${BASH_SOURCE[0]}"
    ;;
esac
