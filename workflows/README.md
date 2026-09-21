# 自前ワークフローを使う

`reina/workflows.py` が組み立てるグラフは ComfyUI / ComfyUI-GGUF の特定バージョンを前提にしています。
ノード名や入力名が環境と合わない場合は、**ComfyUI の GUI で動くワークフローを作って API 形式で書き出し**、
プロンプトやシードだけを差し替えて回すほうが確実です。

## 手順

1. ComfyUI の設定で **Settings → Enable Dev mode options** を ON にする。
2. GUI でワークフローを組み、**1枚正常に生成できることを確認**する。
3. テキスト入力欄などを placeholder に書き換える。

   | placeholder | 差し替わる値 |
   |---|---|
   | `__PROMPT__` | ポジティブプロンプト |
   | `__NEGATIVE__` | ネガティブプロンプト |
   | `__SEED__` | シード（数値として入る） |
   | `__IMAGE__` | 参照画像のファイル名 |
   | `__WIDTH__` / `__HEIGHT__` | 解像度 |
   | `__STEPS__` / `__CFG__` | サンプラ設定 |
   | `__PREFIX__` | SaveImage の filename_prefix |

   欄の中身が placeholder **だけ**なら型を保ったまま（数値は数値で）差し替わり、
   文章に混ぜた場合は文字列として置換されます。

4. **Save (API Format)** で書き出し、このディレクトリに置く。

## 使う

```python
from reina.client import ComfyClient
from reina.workflows import patch_template

client = ComfyClient("127.0.0.1:8188")
image = client.upload_image("input/me_front.jpg")

graph = patch_template("workflows/my_edit.json", {
    "prompt": "walking on a Tokyo street at night, black leather jacket",
    "negative": "lowres, blurry, watermark",
    "seed": 12345,
    "image": image,
})

for name, data in client.run(graph):
    open(f"output/{name}", "wb").write(data)
```

`Export (API)` ではなく通常の保存をした JSON（`"nodes"` キーを持つ UI 形式）を渡すと
エラーメッセージで知らせます。
