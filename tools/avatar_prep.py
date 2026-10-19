#!/usr/bin/env python3
"""끝없이 오르기 아바타 준비: 투명 배경 몸통 그림 (팔다리 없이, 부리는 있어도 된다) 하나로
avatar/body.png 와 avatar/avatar.json (몸통 공의 중심·반지름, 눈 자리와 눈 둘레 색) 을 만든다.
팔다리는 펫이 코드로 그리고, 눈 자리는 눈 깜빡임과 잠든 눈꺼풀을 그리는 데 쓴다.

    python3 tools/avatar_prep.py 몸통.png

numpy 와 scipy 가 필요하다 (이 도구만 쓴다). avatar/ 는 개인 캐릭터라 git 에 넣지 않는다.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

OUT = Path(__file__).resolve().parent.parent / "avatar"
MAX_SIDE = 512


def is_beak(rgb):
    """노란 부리 (공 크기를 잴 때 뺀다)."""
    return (rgb[..., 0] > 170) & (rgb[..., 1] > 130) & (rgb[..., 2] < 110)


def main(argv):
    if len(argv) != 1:
        print(__doc__)
        return 1
    image = Image.open(argv[0]).convert("RGBA")
    a = np.array(image)
    if a[..., 3].min() > 16:
        print("배경이 투명한 그림이 필요해요")
        return 1
    a[..., 3] = np.where(a[..., 3] >= 250, 255, a[..., 3])   # 생성 그림은 몸 알파가 253~254 로 나오기도 한다
    ys, xs = np.where(a[..., 3] > 16)
    if not len(xs):
        print("그림이 비어 있어요")
        return 1
    a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    body = Image.fromarray(a)
    if max(body.size) > MAX_SIDE:
        k = MAX_SIDE / max(body.size)
        body = body.resize((round(body.width * k), round(body.height * k)), Image.LANCZOS)
    c = np.array(body).astype(int)
    rgb, alpha = c[..., :3], c[..., 3]
    solid = alpha > 200

    # 몸통 공: 부리를 뺀 불투명한 곳의 가로·세로 끝
    ball = solid & ~is_beak(rgb)
    ys, xs = np.where(ball)
    if not len(xs):
        print("불투명한 몸통이 없어요")
        return 1
    cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    r = ((xs.max() - xs.min()) + (ys.max() - ys.min())) / 4

    # 눈: 공 안의 어둡고 둥근 덩어리 (가로로 긴 눈썹과 작은 그늘은 뺀다). 눈 둘레 색으로 눈꺼풀을 칠한다
    dark = solid & (rgb.max(axis=2) < 50)
    labels, count = ndimage.label(dark)
    yy, xx = np.mgrid[:c.shape[0], :c.shape[1]]
    eyes = []
    for i in range(1, count + 1):
        ys, xs = np.where(labels == i)
        w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        if len(xs) < r * r * 0.0035 or w / h > 1.2:
            continue
        ex, ey, rx, ry = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2, w / 2, h / 2
        if max(rx, ry) > 0.3 * r or np.hypot(ex - cx, ey - cy) > 0.8 * r:
            continue   # 몸통 테두리 같은 큰 덩어리나 공 밖은 눈이 아니다
        d = np.hypot((xx - ex) / rx, (yy - ey) / ry)
        ring = (d > 1.35) & (d < 1.9) & solid & (rgb.max(axis=2) > 70) & ~is_beak(rgb)
        color = [int(np.median(rgb[..., k][ring])) for k in range(3)] if ring.any() else [150, 150, 150]
        eyes.append([float(ex), float(ey), float(rx), float(ry), color])

    OUT.mkdir(exist_ok=True)
    body.save(OUT / "body.png")
    (OUT / "avatar.json").write_text(json.dumps({"ball": [float(cx), float(cy), float(r)], "eyes": eyes}))
    print(f"{OUT / 'body.png'} {body.size}, 몸통 공 ({cx:.0f}, {cy:.0f}) 반지름 {r:.0f}, 눈 {len(eyes)}개")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
