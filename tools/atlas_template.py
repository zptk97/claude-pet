#!/usr/bin/env python3
"""직접 그릴 때 밑그림으로 쓰는 시트 가이드 이미지를 만든다 (1536x1872, 8열 x 9행).

쓰는 칸에는 테두리·안전 여백·바닥선을, 쓰지 않는 칸에는 빗금을 그리고 줄마다 동작 이름을 적는다.
그림 프로그램(Aseprite, LibreSprite, Krita 등)에서 이 이미지를 아래 레이어로 두고 위 레이어에 그린 뒤,
가이드 레이어는 숨기고 투명 배경 PNG/WebP 로 내보낸다.

    python3 tools/atlas_template.py --output runs/template.png
"""

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import petcore  # noqa: E402

# hatch-pet 레이아웃 가이드와 같은 안전 여백 (좌우 18px, 위아래 16px)
SAFE_X, SAFE_Y = 18, 16


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    image = Image.new("RGBA", (petcore.ATLAS_W, petcore.ATLAS_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # 쓰지 않는 칸: 빗금이 옆 칸으로 넘어가지 않게 칸 크기 타일에 그려 붙인다
    unused_tile = Image.new("RGBA", (petcore.CELL_W, petcore.CELL_H), (120, 120, 120, 60))
    tile_draw = ImageDraw.Draw(unused_tile)
    for k in range(-petcore.CELL_H, petcore.CELL_W, 16):
        tile_draw.line((k, petcore.CELL_H - 1, k + petcore.CELL_H, 0), fill=(120, 120, 120, 90))
    used = {(row, col) for name, (row, durations) in petcore.ANIMATIONS.items() for col in range(len(durations))}
    names = {row: name for name, (row, _) in petcore.ANIMATIONS.items()}

    for row in range(petcore.ATLAS_ROWS):
        for col in range(petcore.ATLAS_COLS):
            x, y = col * petcore.CELL_W, row * petcore.CELL_H
            cell = (x, y, x + petcore.CELL_W - 1, y + petcore.CELL_H - 1)
            if (row, col) not in used:
                image.paste(unused_tile, (x, y))
                continue
            draw.rectangle(cell, outline=(0, 160, 255, 200))
            draw.rectangle((x + SAFE_X, y + SAFE_Y, x + petcore.CELL_W - 1 - SAFE_X, y + petcore.CELL_H - 1 - SAFE_Y),
                           outline=(0, 200, 120, 150))
            base = y + petcore.CELL_H - 1 - SAFE_Y  # 발이 닿는 바닥선
            draw.line((x + SAFE_X, base, x + petcore.CELL_W - 1 - SAFE_X, base), fill=(255, 80, 80, 160), width=2)
            draw.text((x + 4, y + 2), f"{names[row]} {col}", fill=(0, 120, 220, 230))

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    print(output)


if __name__ == "__main__":
    main()
