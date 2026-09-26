"""LoRA を URL からダウンロードして models/loras/ に置き、config.yaml に登録する。

usage:
    add_lora.py URL [--name NAME] [--strength 0.85] [--comfy /workspace/ComfyUI]
                     [--config config.yaml] [--replace]

URL は Hugging Face の resolve リンク、Civitai の直リンク、その他直接
.safetensors を返す URL に対応。HF の api/models 形式（ページURL）は
自動で resolve リンクに変換を試みる。
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from hf_api import HfError, repo_files  # noqa: E402


def _guess_filename(url: str) -> str:
    name = url.split("?")[0].rstrip("/").split("/")[-1]
    if not name.lower().endswith((".safetensors", ".pt", ".ckpt")):
        name += ".safetensors"
    return name


def _resolve_hf_page(url: str) -> str | None:
    """https://huggingface.co/<user>/<repo> のようなページURLなら、
    中の .safetensors を1つ選んで resolve リンクに変換する。
    """
    m = re.match(r"https?://huggingface\.co/([^/]+/[^/?#]+)/?$", url)
    if not m:
        return None
    repo = m.group(1)
    try:
        files = [p for p, _ in repo_files(repo) if p.endswith((".safetensors", ".pt"))]
    except HfError as exc:
        print(f"  ! {repo} を確認できません: {exc}", file=sys.stderr)
        return None
    if not files:
        return None
    path = files[0]
    if len(files) > 1:
        print(f"  複数ファイルがあります。先頭を使用: {path}", file=sys.stderr)
        print(f"  他の候補: {', '.join(files[1:5])}", file=sys.stderr)
    return f"https://huggingface.co/{repo}/resolve/main/{path}"


def download(url: str, dest: Path) -> Path:
    resolved = _resolve_hf_page(url) or url
    if resolved != url:
        print(f"  解決: {resolved}")

    name = _guess_filename(resolved)
    target = dest / name
    dest.mkdir(parents=True, exist_ok=True)

    request = urllib.request.Request(resolved, headers={"User-Agent": "reina-generator"})
    print(f"  ダウンロード中: {resolved}")
    with urllib.request.urlopen(request, timeout=60) as response, target.open("wb") as fh:
        total = int(response.headers.get("Content-Length") or 0)
        written = 0
        chunk = response.read(1 << 20)
        while chunk:
            fh.write(chunk)
            written += len(chunk)
            if total:
                print(f"\r  {written / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", file=sys.stderr)
            chunk = response.read(1 << 20)
    print(file=sys.stderr)

    if target.stat().st_size < 1024:
        raise RuntimeError(
            f"{target} が小さすぎます（{target.stat().st_size}B）。"
            "URL がログインや同意ページを返している可能性があります。"
        )
    return target


def update_config(config_path: Path, name: str, strength: float, replace: bool) -> None:
    import yaml

    base = config_path if config_path.exists() else ROOT / "config.example.yaml"
    data = yaml.safe_load(base.read_text(encoding="utf-8")) or {}
    loras = data.setdefault("loras", [])

    existing = next((l for l in loras if l.get("name") == name), None)
    if existing:
        if replace:
            existing["strength"] = strength
            print(f"  config.yaml: 既存の {name} の strength を更新")
        else:
            print(f"  config.yaml: {name} は既に登録済み（--replace で強度を上書き）")
    else:
        loras.append({"name": name, "strength": strength})
        print(f"  config.yaml: {name} (strength={strength}) を追加")

    config_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


# ComfyUI のネイティブ Qwen-Image 実装は gate/up 射影を分けて持つが、
# Diffusers 形式でエクスポートされた LoRA は融合した "gate_up" キーになっていることがある。
# この場合キーが1つも一致せず、strength を何にしても無効果になる
# （実際に確認済み: comfyui.log に "lora key not loaded" が全キー分出る）。
# リネームでは直せない（テンソルの分割が必要で、正しい分割方法が不明なため）。
SUSPICIOUS_KEY_PATTERNS = ("gate_up", "qkv_proj", "in_proj")


def _read_safetensors_keys(path: Path) -> list[str]:
    """safetensors のヘッダーだけを読んでテンソル名一覧を返す（依存ライブラリ不要）。"""
    import json
    import struct

    with path.open("rb") as fh:
        header_len = struct.unpack("<Q", fh.read(8))[0]
        header = json.loads(fh.read(header_len))
    return [k for k in header if k != "__metadata__"]


def _warn_if_incompatible(path: Path) -> None:
    try:
        keys = _read_safetensors_keys(path)
    except (OSError, ValueError, struct.error) as exc:  # noqa: F821
        print(f"  ! ヘッダーを読めませんでした（互換性チェックは省略）: {exc}", file=sys.stderr)
        return

    hits = sorted({p for k in keys for p in SUSPICIOUS_KEY_PATTERNS if p in k})
    if hits:
        print(
            f"  !! 警告: キー名に {hits} が含まれています。"
            "Diffusers形式でエクスポートされたLoRAの可能性が高く、"
            "ComfyUIのネイティブ実装では読み込めない（strengthを上げても無効果）ことがあります。",
            file=sys.stderr,
        )
        print(
            "     生成後に comfyui.log で確認してください:  grep 'lora key not loaded' /workspace/comfyui.log",
            file=sys.stderr,
        )


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__, file=sys.stderr)
        return 2

    url = args[0]
    opts = {"name": None, "strength": 0.85, "comfy": "/workspace/ComfyUI", "config": None, "replace": False}
    i = 1
    while i < len(args):
        token = args[i]
        if token == "--replace":
            opts["replace"] = True
            i += 1
        elif token.startswith("--") and i + 1 < len(args):
            opts[token[2:]] = args[i + 1]
            i += 2
        else:
            i += 1

    dest = Path(opts["comfy"]) / "models" / "loras"
    try:
        path = download(url, dest)
    except (RuntimeError, OSError) as exc:
        print(f"!! 失敗: {exc}", file=sys.stderr)
        return 1

    name = opts["name"] or path.name
    if name != path.name:
        renamed = dest / name
        path.rename(renamed)
        path = renamed

    size = path.stat().st_size / 1e6
    print(f"{path}  ({size:.1f}MB)")
    if path.suffix == ".safetensors":
        _warn_if_incompatible(path)

    config_path = Path(opts["config"]) if opts["config"] else ROOT / "config.yaml"
    update_config(config_path, path.name, float(opts["strength"]), opts["replace"])
    print("\n次の生成から自動的に使われます（ComfyUI の再起動は不要）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
