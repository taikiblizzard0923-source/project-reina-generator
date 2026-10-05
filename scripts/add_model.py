"""画像生成の本体モデル（Qwen-Image 2.1 系の拡散モデル）を URL からダウンロードする。

usage:
    add_model.py URL [--name NAME] [--comfy /workspace/ComfyUI] [--token TOKEN]

.gguf は models/unet/、それ以外（.safetensors）は models/diffusion_models/ に置く。
ファイル名はサーバーが返す名前（Content-Disposition）を使い、--name で上書きできる。
Civitai はログインが必要なモデルが多いので、API キーを環境変数 CIVITAI_TOKEN か
--token で渡す（https://civitai.com/user/account の API Keys で発行）。
切り替えは nb の use_model("ファイル名")、1回だけなら gen(..., model="ファイル名")。
"""

from __future__ import annotations

import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def _with_token(url: str, token: str | None) -> str:
    host = urllib.parse.urlparse(url).netloc
    if not token or "civitai" not in host or "token=" in url:
        return url
    return url + ("&" if "?" in url else "?") + "token=" + urllib.parse.quote(token)


def _filename(response, url: str) -> str:
    disposition = response.headers.get("Content-Disposition") or ""
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition) or re.search(
        r'filename="?([^";]+)"?', disposition
    )
    if match:
        return Path(urllib.parse.unquote(match.group(1))).name
    return Path(urllib.parse.urlparse(response.geturl() or url).path).name


def download(url: str, comfy: Path, name: str | None, token: str | None) -> Path:
    request = urllib.request.Request(_with_token(url, token), headers={"User-Agent": "reina-generator"})
    print(f"  ダウンロード中: {url}")
    with urllib.request.urlopen(request, timeout=60) as response:
        name = name or _filename(response, url)
        if not name.lower().endswith((".gguf", ".safetensors")):
            raise RuntimeError(
                f"モデルのファイルではありません（{name or '名前不明'}）。"
                "URL がログインや同意のページを返している可能性があります。CIVITAI_TOKEN を確認してください"
            )
        folder = "unet" if name.lower().endswith(".gguf") else "diffusion_models"
        target = comfy / "models" / folder / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            print(f"  既にあります: {target}")
            return target
        partial = target.with_name(target.name + ".part")
        total = int(response.headers.get("Content-Length") or 0)
        written = 0
        with partial.open("wb") as fh:
            chunk = response.read(1 << 22)
            while chunk:
                fh.write(chunk)
                written += len(chunk)
                if total:
                    print(f"\r  {written / 1e9:5.1f} / {total / 1e9:.1f} GB", end="", file=sys.stderr)
                chunk = response.read(1 << 22)
        print(file=sys.stderr)
    if total and written != total:
        raise RuntimeError(f"途中で切れました（{written}/{total} バイト）。もう一度実行してください")
    partial.rename(target)
    return target


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        print(__doc__)
        return 1
    url, opts = args[0], {}
    for key, value in zip(args[1::2], args[2::2]):
        opts[key.lstrip("-")] = value
    comfy = Path(opts.get("comfy", "/workspace/ComfyUI"))
    token = opts.get("token") or os.environ.get("CIVITAI_TOKEN")
    try:
        target = download(url, comfy, opts.get("name"), token)
    except Exception as exc:  # noqa: BLE001 - スマホのセルで読める1行にする
        print(f"  !! 失敗: {exc}")
        return 1
    size = target.stat().st_size / 1e9
    print(f"  完了: {target}（{size:.1f} GB）")
    print(f'  試す:     gen("...", ref=R, model="{target.name}")')
    print(f'  切り替え: use_model("{target.name}")')
    return 0


if __name__ == "__main__":
    sys.exit(main())
