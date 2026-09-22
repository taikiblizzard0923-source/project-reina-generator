"""複数の参照画像を1枚に並べる。

参照画像の枚数に上限があるとき（Qwen-Image 2.1 の編集エンコーダは3枚まで）、
顔・横顔・全身を1枚にまとめれば1枠で3つの情報を渡せる。

usage: collage.py OUT.png IN1 IN2 [IN3 ...] [--rows N] [--height PX] [--gap PX]
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # noqa: BLE001
    print("Pillow が必要です:  pip install pillow", file=sys.stderr)
    raise SystemExit(1) from None

BACKGROUND = (255, 255, 255)


def _fit(image: Image.Image, height: int) -> Image.Image:
    ratio = height / image.height
    return image.convert("RGB").resize(
        (max(1, round(image.width * ratio)), height), Image.LANCZOS
    )


def collage(
    out: str | Path,
    sources: list[str | Path],
    rows: int = 1,
    height: int = 768,
    gap: int = 12,
) -> Path:
    if not sources:
        raise ValueError("画像が1枚以上必要です")

    tiles = [_fit(Image.open(p), height) for p in sources]
    per_row = math.ceil(len(tiles) / rows)
    grid = [tiles[i : i + per_row] for i in range(0, len(tiles), per_row)]

    width = max(sum(t.width for t in row) + gap * (len(row) - 1) for row in grid)
    total = len(grid) * height + gap * (len(grid) - 1)

    canvas = Image.new("RGB", (width, total), BACKGROUND)
    for r, row in enumerate(grid):
        x = (width - (sum(t.width for t in row) + gap * (len(row) - 1))) // 2
        y = r * (height + gap)
        for tile in row:
            canvas.paste(tile, (x, y))
            x += tile.width + gap

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


def main() -> int:
    args = sys.argv[1:]
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        return 2

    options = {"rows": 1, "height": 768, "gap": 12}
    positional: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        key = token.lstrip("-")
        if token.startswith("--") and key in options:
            options[key] = int(args[index + 1])
            index += 2
            continue
        positional.append(token)
        index += 1

    out, sources = positional[0], positional[1:]
    if not sources:
        print("入力画像がありません", file=sys.stderr)
        return 2

    path = collage(out, sources, **options)
    size = Image.open(path).size
    print(f"{path}  ({size[0]}x{size[1]}, {len(sources)}枚)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
