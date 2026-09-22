# project-reina-generator

Qwen-Image 2.1 (GGUF) を **ComfyUI** 経由で叩き、**参照画像から同一人物のリアル写真風画像をバリエーション生成**するためのツールキット。

> **前提**: このリポジトリ自体は画像を生成しません。**GPU のあるマシン（ローカル or RunPod などのクラウド GPU）で ComfyUI を動かし、そこに API で投げる**クライアントです。
> 手元に GPU が無い場合は [§2-B RunPod で動かす](#2-b-runpod-で動かす) を参照してください。

> **利用範囲**: 自分自身の写真、または本人の同意がある写真・完全な架空人物のみを参照画像に使ってください。第三者の顔で性的・誤認を招く画像を作る用途は想定していません。

---

## 1. 必要なもの

| 項目 | 目安 |
|---|---|
| GPU | NVIDIA VRAM 8GB〜（Q4_K_M）/ 12GB以上推奨（Q6_K・Q8_0） |
| ディスク | 30GB程度（GGUF + テキストエンコーダ + VAE） |
| OS | Linux / Windows(WSL) / macOS (Apple Silicon は MPS、低速) |

量子化の目安：

| quant | サイズ感 | 向き |
|---|---|---|
| `Q4_K_M` | 最小 | VRAM 8〜10GB。まずはここから |
| `Q5_K_M` | 中 | VRAM 12GB |
| `Q6_K` | 大 | VRAM 16GB、品質重視 |
| `Q8_0` | 最大 | VRAM 24GB、ほぼ無劣化 |

---

## 2-A. セットアップ（ローカル GPU）

```bash
# ComfyUI 本体 + ComfyUI-GGUF（GGUF ローダ）
bash scripts/install_comfyui.sh ~/ComfyUI

# モデル取得（量子化を変える場合は QUANT=Q6_K を付ける）
QUANT=Q4_K_M bash scripts/download_models.sh ~/ComfyUI

# ComfyUI を起動（別ターミナルで常駐させる）
cd ~/ComfyUI && source venv/bin/activate && python main.py --listen 127.0.0.1 --port 8188
```

このツール側：

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp presets/character.example.yaml presets/character.yaml
```

`config.yaml` の `models:` を、実際に `~/ComfyUI/models/` に落ちたファイル名に合わせてから：

```bash
python -m reina doctor
```

疎通・必要ノード・モデルファイルの有無を全部チェックして、足りないものを指摘します。**ここが全部 [OK] になってから先に進んでください。**

---

## 2-B. RunPod で動かす

手元に GPU が無い場合。**ComfyUI を Pod 上で動かし、CLI は手元の PC から叩く**構成が扱いやすいです（参照画像のアップロードと生成画像の回収は CLI が自動でやります）。

### ステップ1: Pod を作る

| 項目 | 推奨 |
|---|---|
| GPU | RTX 4090 / A5000（24GB）。Q4_K_M なら RTX 3090・A4000(16GB) でも可 |
| テンプレート | `RunPod PyTorch 2.x`（CUDA 12.x 系） |
| Container Disk | 20 GB 以上 |
| **Volume (`/workspace`)** | **60 GB 以上** — モデルはここに入れるので必須 |
| Expose HTTP Ports | **`8188` を追加**（既定の 8888 等に加えて） |

> **Network Volume** を作って割り当てておくと、Pod を消してもモデルが残るので次回は起動だけで済みます（課金は保存分のみ）。

### ステップ2: Pod 上でセットアップ

Web Terminal か SSH で Pod に入り：

```bash
cd /workspace
git clone https://github.com/taikiblizzard0923-source/project-reina-generator.git
cd project-reina-generator

# ComfyUI + GGUF ノード + モデル取得（全部 /workspace 配下）
bash scripts/runpod_setup.sh

# 量子化を変える場合
# QUANT=Q6_K bash scripts/runpod_setup.sh
```

完了したら ComfyUI を起動（`--listen 0.0.0.0` が必須。127.0.0.1 だと外から見えません）：

```bash
bash scripts/runpod_start.sh --daemon
tail -f /workspace/comfyui.log        # "To see the GUI go to..." が出れば OK
```

### ステップ3: 手元の PC から接続

RunPod のダッシュボードで Pod の **Connect → HTTP Service [Port 8188]** の URL を控えます
（`https://<POD_ID>-8188.proxy.runpod.net` の形）。

```bash
export REINA_COMFY_URL="https://xxxxxxxxxxxx-8188.proxy.runpod.net"

python -m reina doctor
python -m reina batch -r input/me_front.jpg
```

`--server` で都度指定することもできます：

```bash
python -m reina doctor --server https://xxxxxxxxxxxx-8188.proxy.runpod.net
```

`config.yaml` に固定する場合：

```yaml
comfyui:
  url: "https://xxxxxxxxxxxx-8188.proxy.runpod.net"
```

### ステップ4（推奨）: SSH トンネルにする

`*.proxy.runpod.net` の URL は **Pod ID を知っていれば誰でも開けます**。個人の写真を扱うので、SSH ポートフォワードにして外に出さないほうが安全です。

```bash
# Pod の Connect → SSH の接続情報を使う
ssh root@<POD_IP> -p <SSH_PORT> -i ~/.ssh/id_ed25519 -L 8188:localhost:8188 -N
```

つないだまま別ターミナルで：

```bash
export REINA_COMFY_URL="http://127.0.0.1:8188"
python -m reina batch -r input/me_front.jpg
```

この場合 Pod 側の「Expose HTTP Ports 8188」は不要です。

### RunPod での注意点

- **Pod を止めるとコンテナ内は消えます。** `/workspace`（ボリューム）だけが残ります。`runpod_setup.sh` は全部 `/workspace` に入れるので、再開時は `runpod_start.sh` だけで立ち上がります。
- **課金は起動中ずっと発生します。** 生成が終わったら Pod を Stop してください。
- 初回は GGUF + テキストエンコーダで **20〜30GB のダウンロード**が走ります。数分〜十数分かかります。
- ゲート付きモデルを使う場合は `export HF_TOKEN=hf_xxx` してから `runpod_setup.sh` を実行。
- WebSocket がプロキシで切られる環境では、自動的に `/history` ポーリングに切り替わります（進捗表示は出ませんが生成は通ります）。

---

## 3. 使い方

### 3-1. 参照画像から生成する（メイン用途）

自分の写真を `input/` に置いて `-r` で渡します（最大3枚。正面・横顔・全身など角度違いを入れると同一性が安定します）。

```bash
# 1枚だけ試す
python -m reina generate "walking on a Tokyo street at night, black leather jacket" \
  -r input/me_front.jpg

# 角度違いを3枚渡す
python -m reina generate "sitting in a cafe by the window, beige knit cardigan" \
  -r input/me_front.jpg -r input/me_side.jpg -r input/me_full.jpg
```

### 3-2. シーンを一括生成

`presets/scenes.yaml` に書いたシーンをまとめて回します。

```bash
python -m reina batch -r input/me_front.jpg               # 全シーン
python -m reina batch -r input/me_front.jpg --only cafe_window city_night
python -m reina batch -r input/me_front.jpg --repeat 4    # 1シーン4枚ずつ
```

### 3-3. 大量バリエーション（組み合わせ生成）

`presets/axes.yaml` の「服装 × 場所 × 光 × 構図」をシャッフルして掛け合わせます。

```bash
python -m reina mix -r input/me_front.jpg --limit 30
```

### 3-4. 参照なし（テキストのみ）

`-r` を付けなければ純粋な text-to-image です。`presets/character.yaml` の外見記述だけで生成します。

```bash
python -m reina batch
```

出力は `output/<日時>/<scene_id>_<seed>.png` と、同名の `.json`（プロンプト・シード・モデル設定の記録）に保存されます。**気に入った画像の `.json` を見れば、そのシードで完全に再現できます。**

---

## 4. 同一性（顔が似ない）を上げる

似ないときは、上から順に効きます。

1. **`presets/character.yaml` を細かく書く** — 参照画像だけに頼らず、髪型・目の形・ほくろ・輪郭を言語化する。
2. **参照画像を増やす** — 正面 / 斜め45度 / 全身の3枚。背景がうるさい写真は避け、顔がはっきり写った高解像度のものを使う。
3. **`--denoise` を下げる** — `--denoise 0.6` 程度にすると参照画像の構図・顔に強く寄ります（ただしポーズの自由度は下がる）。
4. **シードを固定して振る** — `--seed 1000 --repeat 8` で当たりシードを探し、以降はそのシードを基準にする。
5. **LoRA を焼く**（本命） — 同じ人物の写真 15〜30枚で LoRA を学習し、`config.yaml` の `loras:` に登録する。参照画像方式より圧倒的に安定します。

```yaml
loras:
  - name: "reina_v1.safetensors"   # ~/ComfyUI/models/loras/ に置く
    strength: 0.85
```

---

## 5. パラメータの勘所

| パラメータ | 既定 | メモ |
|---|---|---|
| `steps` | 20 | 品質重視なら 28〜35 |
| `cfg` | 2.5 | Qwen-Image 系は低め。上げすぎると肌が硬くなる |
| `shift` | 3.1 | `ModelSamplingAuraFlow`。2.5〜4.0 で構図の印象が変わる |
| モデル形式 | bf16 | VRAM 24GB 以上なら bf16。下表参照 |
| `denoise` | 1.0 | 参照画像モードで下げると参照に忠実 |
| 解像度 | 1024x1536 | 縦ポートレート。横なら 1536x1024 |

すべて CLI から上書きできます：`--steps 30 --cfg 3.0 --width 1152 --height 1536`

### 拡散モデルの形式と速度

A40（Ampere / VRAM 48GB）での実測値：

| 形式 | サイズ | 秒/ステップ | 備考 |
|---|---|---|---|
| `qwen_image_2.1_bf16` | 約14GB | **1.81** | VRAM 24GB 以上ならこちら |
| `qwen_image_2.1_int8_convrot` | 約7GB | 4.4 | VRAM が足りない場合のみ |
| GGUF (第三者量子化) | 約4GB | — | ComfyUI-GGUF が 2.1 未対応（2026-09 時点） |

`int8_convrot` は Ada / Hopper 世代向けの最適化で、**Ampere では逆に遅くなります**。
切り替えは `DIT_VARIANT=int8 bash go.sh official` と `config.yaml` の `unet_gguf` の書き換えで行えます。

---

## 6. うまくいかないとき

**`doctor` で `UnetLoaderGGUF` が [NG]**
→ ComfyUI-GGUF が入っていません。`bash scripts/install_comfyui.sh` を再実行し、ComfyUI を再起動。

**`doctor` で 参照画像エンコーダが [NG]**
→ ComfyUI が古い可能性。`git -C ~/ComfyUI pull` で更新。それでも無い場合はノード名が違うだけなので、`--encoder <実際のノード名>` で指定できます。

**`doctor` でモデルが [NG]（候補が表示される）**
→ 表示された実ファイル名を `config.yaml` の `models:` にそのままコピーしてください。GGUF リポジトリ側のファイル名は配布者によって違います。

**ノード名が環境と合わず動かない**
→ ComfyUI の GUI でワークフローを組み、**Export (API)** で JSON を書き出し、テキスト欄に `__PROMPT__` / `__NEGATIVE__` / `__SEED__` / `__IMAGE__` と書いておけば `reina.workflows.patch_template()` で差し替えて実行できます。詳細は `workflows/README.md`。

**VRAM 不足で落ちる**
→ より小さい quant（`QUANT=Q4_K_M`）にする、解像度を下げる、ComfyUI を `--lowvram` で起動する。

**何が送られているか見たい**
→ 任意のコマンドに `--dry-run` を付けると、送信せずにワークフロー JSON を表示します。

### RunPod 固有

**`doctor` が「ComfyUI ではないようです」と言う**
→ その URL が ComfyUI ではなく JupyterLab などを指しています。Connect パネルで **Port 8188** の HTTP Service URL を使ってください。Pod 設定で 8188 を Expose し忘れていることも多いです。

**接続できない / 502 が返る**
→ ComfyUI が `--listen 0.0.0.0` で起動していないと RunPod のプロキシから届きません。`runpod_start.sh` を使うか、`tail -f /workspace/comfyui.log` で起動状況を確認してください。

**Pod を再起動したらモデルが消えた**
→ `/workspace` 以外に入れています。`runpod_setup.sh` は `/workspace/ComfyUI` に入れるので、そちらで再実行してください（既存ファイルは再ダウンロードしません）。

**生成が途中で止まる / タイムアウトする**
→ 大きい quant や高解像度で 15 分を超える場合があります。`--timeout 2400` で延ばせます。

---

## 7. 構成

```
reina/
  cli.py        コマンド（doctor / generate / batch / mix）
  client.py     ComfyUI の HTTP + WebSocket API クライアント
  workflows.py  API フォーマットのワークフロー組み立て / テンプレート差し替え
  prompts.py    キャラクター定義 × シーン定義 → プロンプト
  config.py     config.yaml 読み込み
presets/
  character.example.yaml  人物の固定特徴
  scenes.yaml             シーン一覧
  axes.yaml               組み合わせ生成用の軸
scripts/
  install_comfyui.sh      ComfyUI + ComfyUI-GGUF（ローカル用）
  download_models.sh      GGUF / テキストエンコーダ / VAE
  runpod_setup.sh         RunPod の Pod 上で一括セットアップ
  runpod_start.sh         Pod 上で ComfyUI を 0.0.0.0:8188 起動
input/   参照画像を置く
output/  生成結果（画像 + メタJSON）
```
