"""Hugging Face Hub の公開 API を標準ライブラリだけで叩く。

huggingface_hub パッケージに依存しない。
（壊れたインストールだと `import huggingface_hub` は通るのに
  `from huggingface_hub import list_repo_files` が失敗する、という事故があったため）
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

ENDPOINT = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")


class HfError(RuntimeError):
    pass


def _get(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "reina-generator"})
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        raise HfError(f"HTTP {exc.code} {url}") from exc
    except urllib.error.URLError as exc:
        raise HfError(f"接続できません: {exc.reason} ({url})") from exc


def repo_files(repo: str, with_size: bool = False) -> list[tuple[str, int | None]]:
    """(パス, サイズ) の一覧。サイズは with_size=True のときだけ入る。"""
    url = f"{ENDPOINT}/api/models/{repo}"
    if with_size:
        url += "?blobs=true"
    data = json.loads(_get(url))
    out: list[tuple[str, int | None]] = []
    for sibling in data.get("siblings") or []:
        name = sibling.get("rfilename")
        if name:
            out.append((name, sibling.get("size")))
    return out


def read_text_file(repo: str, path: str, revision: str = "main") -> str:
    return _get(f"{ENDPOINT}/{repo}/resolve/{revision}/{path}").decode("utf-8", "replace")
