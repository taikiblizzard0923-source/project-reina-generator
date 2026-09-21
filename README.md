# project-reina-generator

Qwen-Image 2.1 (GGUF) を **ComfyUI** 経由で叩き、**参照画像から同一人物のリアル写真風画像をバリエーション生成**するためのツールキット。

> **前提**: このリポジトリ自体は画像を生成しません。**GPU のあるあなたのマシンで ComfyUI を動かし、そこに API で投げる**クライアントです。
> （CI / クラウドコンテナ上では GPU も huggingface.co へのアクセスもないため、生成は必ずローカルで実行してください）

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

## 2. セットアップ

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
| `denoise` | 1.0 | 参照画像モードで下げると参照に忠実 |
| 解像度 | 1024x1536 | 縦ポートレート。横なら 1536x1024 |

すべて CLI から上書きできます：`--steps 30 --cfg 3.0 --width 1152 --height 1536`

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
  install_comfyui.sh      ComfyUI + ComfyUI-GGUF
  download_models.sh      GGUF / テキストエンコーダ / VAE
input/   参照画像を置く
output/  生成結果（画像 + メタJSON）
```
