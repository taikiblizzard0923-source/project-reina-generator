"""resolve_hf_file.py の選択ロジックを、HF に接続せずに検証する。

    python tests/test_resolve_hf_file.py

スクリプトは自分のディレクトリを sys.path の先頭に入れるので、
スタブの hf_api.py と同じ一時ディレクトリにコピーして実行する。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

STUB = '''
class HfError(RuntimeError):
    pass

REPOS = {
    "Comfy-Org/Qwen-Image-2.1": [
        ("text_encoders/qwen3vl_8b_bf16.safetensors", 17_500_000_000),
        ("text_encoders/qwen3vl_8b_int8_convrot.safetensors", 9_000_000_000),
        ("diffusion_models/qwen_image_2.1_bf16.safetensors", 14_000_000_000),
        ("diffusion_models/qwen_image_2.1_int8_convrot.safetensors", 7_000_000_000),
        ("vae/qwen_image_2.1_vae_bf16.safetensors", 250_000_000),
    ],
    # 旧世代。TE の必須条件 +qwen3vl を満たさないので選ばれてはいけない
    "Comfy-Org/Qwen-Image_ComfyUI": [
        ("split_files/text_encoders/qwen_2.5_vl_7b.safetensors", 16_000_000_000),
    ],
    "missing/repo": None,
}

def repo_files(repo, with_size=False):
    files = REPOS.get(repo)
    if files is None:
        raise HfError("HTTP 404")
    return files
'''

CASES = [
    (
        "テキストエンコーダは 2.1 世代の int8 を選ぶ",
        ("Comfy-Org/Qwen-Image-2.1,Comfy-Org/Qwen-Image_ComfyUI", ".safetensors",
         "+qwen3vl", "+text_encoders", "int8"),
        0,
        "text_encoders/qwen3vl_8b_int8_convrot.safetensors",
    ),
    (
        "拡散モデルは diffusion_models/ の int8 を選ぶ",
        ("Comfy-Org/Qwen-Image-2.1", ".safetensors", "+diffusion_models", "+2.1", "int8"),
        0,
        "diffusion_models/qwen_image_2.1_int8_convrot.safetensors",
    ),
    (
        "VAE は vae/ を選ぶ",
        ("Comfy-Org/Qwen-Image-2.1", ".safetensors", "+vae", "2.1"),
        0,
        "vae/qwen_image_2.1_vae_bf16.safetensors",
    ),
    (
        "必須条件を満たす候補が無ければ、近いものを選ばず失敗する",
        ("Comfy-Org/Qwen-Image_ComfyUI", ".safetensors", "+qwen3vl"),
        1,
        None,
    ),
    (
        "存在しない repo は失敗する",
        ("missing/repo", ".safetensors", "+vae"),
        1,
        None,
    ),
]


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp)
        (sandbox / "hf_api.py").write_text(textwrap.dedent(STUB), encoding="utf-8")
        script = sandbox / "resolve_hf_file.py"
        shutil.copy(SCRIPTS / "resolve_hf_file.py", script)

        failures = 0
        for label, args, want_code, want_path in CASES:
            proc = subprocess.run(
                [sys.executable, str(script), *args], capture_output=True, text=True
            )
            got = (
                proc.stdout.strip().split("\t")[1]
                if proc.returncode == 0 and "\t" in proc.stdout
                else None
            )
            ok = proc.returncode == want_code and got == want_path
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'}  {label}")
            if not ok:
                print(f"      終了コード={proc.returncode} 選択={got!r} 期待={want_path!r}")
                print(textwrap.indent(proc.stderr.strip(), "      "))

    print(f"\n失敗: {failures}/{len(CASES)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
