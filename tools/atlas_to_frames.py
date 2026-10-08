#!/usr/bin/env python3
"""완성된 스프라이트 시트를 동작별 프레임 PNG 로 나눈다.

직접 그린 시트나 받은 펫을 hatch-pet 의 render_animation_previews.py(GIF 미리보기)와
inspect_frames.py(프레임 검사)에 넣을 수 있게 frames/<동작>/00.png ... 로 저장한다.

    python3 tools/atlas_to_frames.py pets/<펫>/spritesheet.webp --output-dir runs/<펫>/frames
"""

import argparse
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import petcore  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("atlas")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    with Image.open(args.atlas) as opened:
        atlas = opened.convert("RGBA")
    if atlas.size != (petcore.ATLAS_W, petcore.ATLAS_H):
        sys.exit(f"시트 크기가 {atlas.size}, 기대값 {(petcore.ATLAS_W, petcore.ATLAS_H)}")

    output = Path(args.output_dir)
    for name in petcore.ANIMATIONS:
        state_dir = output / name
        state_dir.mkdir(parents=True, exist_ok=True)
        for old in state_dir.glob("*.png"):
            old.unlink()
        for index, (x, y, w, h) in enumerate(petcore.frame_rects(name)):
            atlas.crop((x, y, x + w, y + h)).save(state_dir / f"{index:02d}.png")
    print(output)


if __name__ == "__main__":
    main()
