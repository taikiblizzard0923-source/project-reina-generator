"""「静止画プロンプト / 動画プロンプト」形式の md を presets/<名前>.yaml に変換する。

    python md_to_preset.py 街中_カフェ.md [--name cafe_smile] [--out presets/]

md の形（「### シーンNN：タイトル」の下に、静止画と動画のプロンプトを引用ブロックで書く）:

    ### シーン01：ショッピングエリアでのひととき
    * **静止画プロンプト**:
      > 明るいインドアショッピングエリアの椅子に…
    * **動画プロンプト**:
      > 明るいショッピングエリアで椅子に座り…8秒。

文面はそのまま入れ、変えるのは次の3つだけ:
  - 主体（「〇〇」「女性」「モデル」）を「参照画像の女性」（--subject で変更可）にそろえる
  - 動画の文から長さの記述（「全8秒。」「、8秒。」）を外す（長さは画面で選ぶ）
  - 静止画の文の最後に、背景・小物を細部まで配置する一文を足す（--no-detail で足さない）
prompt が静止画、video が動画の指示になり、/reina/clip でシーンを選ぶと両方の欄に入る。
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
# 動画の長さの書き方（「全8秒。」「、8秒。」「8秒、」）。長さは画面で選ぶので文から外す。
# 「最初の7秒で」「最後の1秒は」「5秒目あたりで」のような途中のタイミングは残す
DURATION = re.compile(r"(?:全\s*\d+(?:\.\d+)?\s*秒[。、]?\s*|[、,]\s*\d+(?:\.\d+)?\s*秒(?=[。、,]|$)|(?<=[。\s])\d+(?:\.\d+)?\s*秒[、,。]\s*)")
# 主体の書き方（〇〇 など）。--subject で置き換える
PLACEHOLDERS = ("〇〇", "○○", "◯◯")
DEFAULT_SUBJECT = "参照画像の女性"
DETAIL = "背景や小物は細部まで描き込み、場面に合ったものを自然でセンスよく配置する。"


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


# 主体を指す言葉。「モデルライクな表情」の「モデル」は主体ではないので外す
SUBJECT_WORDS = re.compile(r"(?<!参照画像の)(?:女性|モデル(?!ライク))")


def _subject(text: str, subject: str) -> str:
    for mark in PLACEHOLDERS:
        text = text.replace(mark, subject)
    return SUBJECT_WORDS.sub(subject, text)


def _drop_duration(text: str) -> str:
    text = DURATION.sub(lambda m: "。" if m.group(0).rstrip().endswith("。") and not m.group(0).startswith("全") else "", text)
    return re.sub(r"。。+", "。", text).strip()


def to_preset(
    title: str,
    scenes: list[dict],
    notes: list[str] | None = None,
    subject: str = DEFAULT_SUBJECT,
    detail: bool = True,
) -> dict:
    """文面はそのまま。変えるのは 主体（〇〇）の置き換え・動画の長さの記述の削除・背景の一文の追加だけ。"""
    out = []
    for s in scenes:
        if not s.get("prompt"):
            continue
        prompt = _subject(s["prompt"], subject)
        if detail:
            prompt = prompt.rstrip() + ("" if prompt.rstrip().endswith("。") else "。") + DETAIL
        item = {"id": f"s{s['no']:02d}", "title": _subject(s["title"], subject), "prompt": prompt}
        if s.get("video"):
            item["video"] = _drop_duration(_subject(s["video"], subject))
        out.append(item)
    preset = {"title": title}
    if notes:
        # 生成には使わない（読む人向けのメモ）。文面はそのまま
        preset["notes"] = [_subject(n, subject) for n in notes]
    preset["scenes"] = out
    return preset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("md", nargs="+")
    parser.add_argument("--name", help="出力名（1ファイルのときだけ。省略時は md のファイル名）")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "presets"))
    parser.add_argument("--subject", default=DEFAULT_SUBJECT, help=f"〇〇 を置き換える言葉（既定: {DEFAULT_SUBJECT}）")
    parser.add_argument("--no-detail", action="store_true", help="静止画の文に背景・小物の一文を足さない")
    args = parser.parse_args()
    for path in map(Path, args.md):
        title, scenes, notes = parse(path.read_text(encoding="utf-8"))
        preset = to_preset(title, scenes, notes, args.subject, not args.no_detail)
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
