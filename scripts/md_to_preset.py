"""「静止画プロンプト / 動画プロンプト」形式の md を presets/<名前>.yaml に変換する。

    python md_to_preset.py 街中_カフェ.md [--name cafe_smile] [--out presets/]

md の形（「### シーンNN：タイトル」の下に、静止画と動画のプロンプトを引用ブロックで書く）:

    ### シーン01：ショッピングエリアでのひととき
    * **静止画プロンプト**:
      > 明るいインドアショッピングエリアの椅子に…
    * **動画プロンプト**:
      > 明るいショッピングエリアで椅子に座り…8秒。

文面は書き換えずにそのまま入れる。prompt が静止画、video が動画の指示になり、
/reina/clip でシーンを選ぶと両方の欄に入る。動画の文に「N秒」があれば seconds にも入れる
（文面はそのまま）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

# 「### シーン01：タイトル」と「### 01. タイトル」の両方に対応
SCENE = re.compile(r"^#{2,4}\s*(?:シーン\s*)?(\d+)\s*[.．：:]\s*(.+?)\s*$")
# 「**静止画プロンプト**:」「**静止画プロンプト (画像生成用)**:」「**A 静止画**」など
FIELD = re.compile(r"^\s*[*\-]\s*\*\*(?:[A-Z]\s*)?(静止画|動画)[^*]*\*\*\s*[：:]?\s*(.*)$")
# Google Drive のテキスト表示などで付く、Markdown 記号の前のバックスラッシュ
ESCAPED = re.compile(r"\\([#*>\-_.\[\]()!])")
QUOTE = re.compile(r"^\s*>\s?(.*)$")
# 「8秒」は長さ、「5秒目」は途中のタイミングなので数えない
SECONDS = re.compile(r"(\d+(?:\.\d+)?)\s*秒(?!目)")


def parse(text: str) -> tuple[str, list[dict]]:
    title = ""
    notes: list[str] = []
    scenes: list[dict] = []
    current: dict | None = None
    field: str | None = None
    for line in ESCAPED.sub(r"\1", text).splitlines():
        line = line.rstrip()
        if not title and line.startswith("# "):
            title = line[2:].strip()
            continue
        if m := SCENE.match(line):
            current = {"no": int(m.group(1)), "title": m.group(2)}
            scenes.append(current)
            field = None
            continue
        if current is None:
            # 見出しより前の「共通モデル表記」「衣装指定」などは notes として残す
            if line.strip() and not line.startswith("#") and line.strip() != "---":
                notes.append(line.strip())
            continue
        if m := FIELD.match(line):
            field = "prompt" if m.group(1) == "静止画" else "video"
            if m.group(2).strip():
                current[field] = m.group(2).strip()
            continue
        if field and (m := QUOTE.match(line)):
            part = m.group(1).strip()
            if part:
                current[field] = (current.get(field, "") + " " + part).strip()
            continue
        if line.strip() in ("", "---"):
            field = None if line.strip() == "---" else field
    return title, scenes, notes


def to_preset(title: str, scenes: list[dict], notes: list[str] | None = None) -> dict:
    out = []
    for s in scenes:
        if not s.get("prompt"):
            continue
        item = {"id": f"s{s['no']:02d}", "title": s["title"], "prompt": s["prompt"]}
        if s.get("video"):
            item["video"] = s["video"]
            if found := SECONDS.findall(s["video"]):
                # 「全8秒。最初の7秒で…最後の1秒は…」のように複数あれば一番長いものが全体の長さ
                value = max(found, key=float)
                item["seconds"] = float(value) if "." in value else int(value)
        out.append(item)
    preset = {"title": title}
    if notes:
        # 生成には使わない（読む人向けのメモ）。文面はそのまま
        preset["notes"] = notes
    preset["scenes"] = out
    return preset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("md", nargs="+")
    parser.add_argument("--name", help="出力名（1ファイルのときだけ。省略時は md のファイル名）")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "presets"))
    args = parser.parse_args()
    for path in map(Path, args.md):
        title, scenes, notes = parse(path.read_text(encoding="utf-8"))
        preset = to_preset(title, scenes, notes)
        if not preset["scenes"]:
            print(f"!! {path.name}: シーンが見つかりません（形式が違う可能性）", file=sys.stderr)
            continue
        name = args.name if args.name and len(args.md) == 1 else path.stem
        dest = Path(args.out) / f"{name}.yaml"
        dest.write_text(
            yaml.safe_dump(preset, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8"
        )
        print(f"{path.name} -> {dest}（{len(preset['scenes'])} シーン）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
