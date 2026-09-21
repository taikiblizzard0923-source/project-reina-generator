# 再開手順（スマホ・RunPod）

## 現在の状態

| 項目 | 状態 |
|---|---|
| ComfyUI | `/workspace/ComfyUI` に導入済み |
| テキストエンコーダ | ✅ `qwen3vl_8b_int8_convrot.safetensors` |
| VAE | ✅ `qwen_image_2.1_vae_bf16.safetensors` |
| 拡散モデル（GGUF） | ❌ `abenzerps` 版は `Unknown model architecture` で失敗。別配布元（`vantagewithai`）は未検証 |
| 拡散モデル（公式 safetensors） | ⏳ **ここから再開** |
| `config.yaml` | ダウンロード完了時に自動更新される |

**残タスクは「公式の拡散モデル（約20GB）を落として起動する」だけ**です。

---

## 1. Pod を起動する

RunPod の Pods → 対象の Pod → **Start**

`Running` になったら **Connect → `:8888` Jupyter Notebook** を開き、
Notebook（Python 3）のセルで以下を順に実行します。

> ⚠️ JupyterLab の Terminal は iOS Safari から貼り付けできません。**必ず Notebook のセル**を使ってください。

---

## 2. 状態を確認

```python
!cd /workspace/project-reina-generator && git pull && bash go.sh clean
```

`clean` は重複ファイルを削除して現状を表示します。

- `diffusion_models` に `qwen_image_2.1_*.safetensors` が **ある** → 手順4へ
- **無い** → 手順3へ

---

## 3. 拡散モデルを取得

### まずサイズを確認（任意）

```python
!cd /workspace/project-reina-generator && bash go.sh info Comfy-Org/Qwen-Image-2.1
```

```python
!cd /workspace/project-reina-generator && bash go.sh info vantagewithai/Qwen-Image-2.1-ComfyUI-GGUF
```

ファイル一覧と実サイズ、README が表示される。

### 公式 safetensors（確実に動く）

```python
!cd /workspace/project-reina-generator && bash go.sh official
```

進捗:

```python
!cd /workspace/project-reina-generator && bash go.sh ps
```

エラー時はログを見る:

```python
!tail -n 30 /workspace/setup.log
```

### GGUF を試す場合（任意 / 小容量）

`vantagewithai/Qwen-Image-2.1-ComfyUI-GGUF` は ComfyUI-GGUF 向けに
用意されたもの。ただし ComfyUI-GGUF 側が `qwen_image` アーキテクチャに
対応していないと `Unknown model architecture` で失敗する。

```python
!cd /workspace/project-reina-generator && GGUF_REPO=vantagewithai/Qwen-Image-2.1-ComfyUI-GGUF bash scripts/download_models.sh /workspace/ComfyUI
```

失敗しても公式 safetensors に戻せる（`bash go.sh official`）。

---

## 4. ComfyUI を起動して確認

```python
!pkill -f "main.py --listen" ; cd /workspace/project-reina-generator && bash go.sh start && sleep 90 && bash go.sh check
```

3つとも `[OK]` になれば準備完了です。

---

## 5. 生成する

```python
%run /workspace/project-reina-generator/nb.py
```

```python
gen("standing in a bright photo studio, white blouse, soft natural light")
```

画像はセルの下に表示されます。

### 参照画像を使う（本命）

1. JupyterLab 左の **↑（Upload）** ボタンで、自分の写真を
   `/workspace/project-reina-generator/input/` にアップロード
2. 生成:

```python
gen("walking on a Tokyo street at night, black leather jacket", ref="input/me.jpg")
```

```python
batch(ref="input/me.jpg")     # presets/scenes.yaml の8シーンを一括
```

```python
show(8)                        # 直近8枚を再表示
```

---

## コマンド一覧

| コマンド | 内容 |
|---|---|
| `bash go.sh official` | 公式 safetensors の拡散モデルを取得 |
| `bash go.sh ps` | 進捗・モデル一覧 |
| `bash go.sh log` | ログを流す |
| `bash go.sh clean` | 中断ファイル・重複を掃除 |
| `bash go.sh start` | ComfyUI 起動 |
| `bash go.sh check` | 疎通・モデル確認 |

---

## 終わるとき

RunPod の Pod を **Stop**（Terminate ではない）。

- **Stop** → `/workspace` のモデルは残る。停止中も約 **$0.017/時（1日 約$0.4）** のディスク代
- **Terminate** → 全部消える。次回は 30GB の再ダウンロードからやり直し

しばらく使わないなら Terminate、明日も使うなら Stop。
