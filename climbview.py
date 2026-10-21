"""끝없이 오르기 창: 펫 줄 오른쪽 끝에 바위 기둥과 내 아바타를 그린다 (계산은 climbcore).

그림은 assets/climb/ (바위 rock.jpg, 높이 구간별 배경 bg/*.jpg) 과 내 아바타 avatar/body.png + avatar.json
(개인 캐릭터라 git 에 넣지 않는다, tools/avatar_prep.py 로 만든다. 없으면 회색 공을 그린다).
창 폭은 바위 기둥의 SCENE_W 배라 양옆으로 배경과 날씨가 보이고, 클릭은 바위 기둥과 높이 상자에서만 받는다
(X11 입력 모양, 양옆은 아래 창으로 지나간다). 늘 60fps 로 그리고 숨기면 멈춘다 (계산도 멈춘다). 밤 (서울 해 지는
시각, climbcore.night_level) 에는 하늘·바위·아바타를 어둡게 하고 별을 띄우고, 아바타가 헤드램프로 다음 홀드 쪽을 비춘다. 왼쪽 버튼으로 끌면
펫처럼 줄 전체를 옮긴다. 바위 층은 바뀔 때만 다시 그리고, 카메라가 멈춰 있고 비·눈이 없으면 아바타 둘레와
높이 상자만 다시 그린다 (창 관리자가 다시 합성할 넓이도 줄어든다).
"""
import json
import math
import os
import random
import time
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QLineF, QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PyQt5.QtGui import (QBrush, QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
                         QPixmap, QPolygonF, QRadialGradient, QRegion, QTransform)
from PyQt5.QtWidgets import QWidget

from climbcore import (ARM, BANDS, KST, LEG, M_PER_TOKEN, MOON, PACE_BANK, SCENE_W, V, VIEW_H, VIEW_TOP, WALL_HALF,
                       edge_x, fmt_alt, height_pct, night_level, smooth, two_bone)

APP_DIR = Path(__file__).resolve().parent
ASSET_DIR = APP_DIR / "assets" / "climb"
AVATAR_DIR = APP_DIR / "avatar"   # 내 아바타 (git 제외): body.png (투명 배경), avatar.json (몸통 공·눈 자리)
UNIT_PX = 50                      # 몸통 반지름 (px, 펫 크기 배율을 곱한다: 보통이면 25 → 창 높이 300)
FRAME_MS = 16                     # 늘 60fps (쉬거나 잘 때 낮추면 비·눈이 끊겨 보인다)
STEP_MAX = 0.05                   # 계산 한 번에 넘기는 시간 (초). 프레임이 길면 나눠서 넘긴다
NIGHT_EVERY = 5.0                 # 시계를 보고 밤 정도를 다시 정하는 간격 (초)
NIGHT_TINT = (8, 12, 30)          # 밤에 덮는 색 (하늘·바위·아바타)
LAMP = (-0.40, -0.70)             # 헤드램프 자리 (몸통 공 중심에서, 반지름 단위, 그림 좌표: 왼쪽 위가 -)
LAMP_POOL = 1.3                   # 헤드램프가 벽을 비추는 웅덩이 반지름 (m)
MOON_SLAB = 1.6                   # 기둥 위 끝 달 표면 띠의 두께 (m)
SPACE_MOON = (0.823, 0.147, 0.045)   # 우주 배경 그림 속 달: 가운데 (그림 폭·높이 비율), 반지름 (그림 폭 비율)
SPACE_SKY = (15, 18, 24)          # 그 달 둘레의 하늘색 (달 위에 있을 때 그림 속 달을 덮는다)


def clock_night():
    """지금 밤 정도 (night_level). CLAUDE_PET_HOUR (0~24, 소수 가능) 가 있으면 오늘 그 시각으로 본다 (밤 화면 미리 보기)."""
    now = datetime.now(KST)
    try:
        hour = float(os.environ.get("CLAUDE_PET_HOUR", ""))
    except ValueError:
        return night_level(now)
    if not 0 <= hour < 24:   # 숫자가 아니거나 (nan) 0~24 밖이면 지금 시각
        return night_level(now)
    minutes = int(hour * 60)
    return night_level(now.replace(hour=minutes // 60, minute=minutes % 60, second=0))


def load_avatar():
    """(몸통 QImage, {"ball": [cx, cy, r], "eyes": [[cx, cy, rx, ry, [r, g, b]], ...]}) 또는 None (없거나 깨짐)."""
    image = QImage(str(AVATAR_DIR / "body.png"))
    try:
        info = json.loads((AVATAR_DIR / "avatar.json").read_text(encoding="utf-8"))
        ball = [float(v) for v in info["ball"]]
        eyes = [[float(e[0]), float(e[1]), float(e[2]), float(e[3]), [int(c) for c in e[4]]] for e in info.get("eyes", [])]
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None
    if image.isNull() or len(ball) != 3 or ball[2] <= 0:
        return None
    return image, {"ball": ball, "eyes": eyes}


def plain_avatar():
    """아바타 그림이 없을 때 쓰는 회색 공 몸통 (왼쪽을 보는 눈 둘, 작은 노란 부리)."""
    image = QImage(240, 220, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(236, 186, 60))
    p.drawPolygon(QPolygonF([QPointF(48, 104), QPointF(4, 126), QPointF(52, 130)]))
    p.setBrush(QColor(150, 150, 156))
    p.drawEllipse(QPointF(140, 110), 100, 100)
    eyes = [[96.0, 92.0, 10.0, 13.0, [150, 150, 156]], [146.0, 88.0, 10.0, 13.0, [150, 150, 156]]]
    p.setBrush(QColor(30, 30, 34))
    for x, y, rx, ry, _ in eyes:
        p.drawEllipse(QPointF(x, y), rx, ry)
    p.end()
    return image, {"ball": [140.0, 110.0, 100.0], "eyes": eyes}


class Look:
    """배율에 맞춘 그림 자원과 그리기."""

    def __init__(self, scale, scene=SCENE_W):
        self.R = UNIT_PX * scale
        self.rock_w = round(2 * (WALL_HALF + 0.9) * self.R)   # 바위 기둥 폭
        self.W = round(self.rock_w * scene)
        self.wall_h = round(VIEW_H * self.R)
        self.H = self.wall_h
        body, info = load_avatar() or plain_avatar()
        cx, cy, r = info["ball"]
        k = self.R / r
        self.body = QPixmap.fromImage(body.scaled(round(body.width() * k), round(body.height() * k),
                                                  Qt.IgnoreAspectRatio, Qt.SmoothTransformation))
        self.anchor = (cx * k, cy * k)
        self.eyes = [((ex * k - self.anchor[0]), (ey * k - self.anchor[1]), rx * k, ry * k, QColor(*color))
                     for ex, ey, rx, ry, color in info["eyes"]]
        rock = QImage(str(ASSET_DIR / "rock.jpg"))
        tw = max(self.rock_w, round(7.5 * self.R))
        self.rock = QPixmap.fromImage(rock.scaledToWidth(tw, Qt.SmoothTransformation))
        self.font = QFont()
        self.font.setPixelSize(max(11, round(12 + 6 * scale)))
        self.small = QFont()
        self.small.setPixelSize(max(10, round(9 + 8 * scale)))
        self.small.setBold(True)
        self.layer = QImage(self.W, self.wall_h, QImage.Format_ARGB32_Premultiplied)
        self.lit = None             # 밤에 바위를 어둡게 하기 전 바위 층 (헤드램프가 비추는 곳에 붙인다)
        self.light = self.light_key = None   # 헤드램프 빛 웅덩이 그림과 그린 상태
        self.actors = QImage(self.W, self.wall_h, QImage.Format_ARGB32_Premultiplied)   # 밤에 아바타를 어둡게 할 때 먼저 그리는 곳
        self.clip = None
        # 높이 구간별 배경 (창 폭에 맞춰 키우고, 남는 위아래는 오를수록 천천히 가라앉는 데 쓴다)
        self.backs = {}
        for _, name in BANDS:
            path = ASSET_DIR / "bg" / f"{name}.jpg"
            if path.exists():
                image = QImage(str(path))
                if image.height() * self.W / image.width() < self.wall_h * 1.25:
                    image = image.scaledToHeight(round(self.wall_h * 1.25), Qt.SmoothTransformation)
                else:
                    image = image.scaledToWidth(self.W, Qt.SmoothTransformation)
                self.backs[name] = QPixmap.fromImage(image)
        # 창 가장자리를 투명하게 흐리는 가림 (위아래 14%, 양옆 10%)
        self.mask = QImage(self.W, self.wall_h, QImage.Format_ARGB32_Premultiplied)
        self.mask.fill(QColor(255, 255, 255))
        mp = QPainter(self.mask)
        mp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        for x1, y1, x2, y2, edge in ((0, 0, 0, self.wall_h, 0.14), (0, 0, self.W, 0, 0.10)):
            g = QLinearGradient(x1, y1, x2, y2)
            g.setColorAt(0.0, QColor(0, 0, 0, 0))
            g.setColorAt(edge, QColor(0, 0, 0, 255))
            g.setColorAt(1 - edge, QColor(0, 0, 0, 255))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            mp.fillRect(QRectF(0, 0, self.W, self.wall_h), QBrush(g))
        mp.end()
        rng = random.Random(3)
        self.stars = [(rng.random(), rng.random() * 0.8, rng.uniform(0.6, 1.4), rng.uniform(0, 6.3)) for _ in range(28)]
        self.pebbles = [(rng.random(), rng.uniform(0.15, 1.2), rng.uniform(0.06, 0.16), rng.choice((92, 108, 124)))
                        for _ in range(22)]   # 땅의 돌 (가로 자리 0~1, 땅 아래 깊이 m, 크기 m, 밝기)
        self.hold_cache = {}        # id(홀드) → (홀드, 그린 상태, 그림, pad)
        self.carved_cache = {}      # 지난 주 기록 자국 글자 → (그림, 붙일 자리)
        self.layer_drawn = None     # 지금 바위 층을 그린 상태 (layer_key)
        self.hud_key = self.hud = None   # 높이 상자 그림과 그린 내용
        self.hud_rect = self.banner_rect = QRect()   # 마지막으로 그린 높이 상자·기록 말풍선 (클릭 받는 곳)

    def actor_region(self, sim):
        """아바타·가루·떠오르는 글자가 있는 곳 (팔다리와 글자 폭만큼 넉넉히), 로프 마디마다의 둘레, 포타레지 둘레
        (창 안 QRegion). 바뀐 곳만 다시 그릴 때 쓴다."""
        cam, R = self.view_cam(sim), self.R
        window = QRect(0, 0, self.W, self.H)

        def box(points, pad, below=None):
            pts = [self.to_screen(q, cam) for q in points]
            left, right = min(pt.x() for pt in pts) - pad, max(pt.x() for pt in pts) + pad
            top, bottom = min(pt.y() for pt in pts) - pad, max(pt.y() for pt in pts) + (pad if below is None else below)
            return QRect(int(left), int(top), int(right - left) + 2, int(bottom - top) + 2).intersected(window)

        region = QRegion(box([sim.body] + [l.pos for l in sim.limbs.values()] + [p.p for p in sim.particles]
                             + [f.p for f in sim.texts], 1.6 * R + (3.0 * R if sim.texts else 0), 1.6 * R))
        if sim.events:   # 로프 (paint_rope 와 같은 마디)
            rope = [sim.harness()] + ([sim.limbs[sim.rope_hand].pos] if sim.rope_hand is not None else [])
            rope += [b.rope_point() for b in sorted((b for b in sim.bolts if b.clipped), key=lambda b: -b.p.y)]
            rope.append(V(rope[-1].x * 0.6, cam - VIEW_H))
            for a, b in zip(rope, rope[1:]):
                region = region.united(QRegion(box([a, b], max(2.0, R * 0.08) + 2)))
        if sim.ledge is not None:   # 포타레지 (펼친 침대, 끈, 앵커)
            ledge = sim.ledge
            region = region.united(QRegion(box([ledge.anchor + V(0, 0.4), ledge.bed + V(-1.5, -1.2),
                                                ledge.bed + V(1.5, 1.2)], 0.3 * R)))
        for rock in sim.rocks:   # 떨어지는 돌 (돌이 있으면 창 전체를 다시 그리지만, 밤에 아바타 둘레만 어둡게 그릴 때 쓴다)
            region = region.united(QRegion(box([rock[0]], rock[4] * 1.1 * R + 2)))
        if sim.lamp is not None and self.night_amount(sim) > 0.01:   # 헤드램프 빛 (몸에서 비추는 곳까지, 웅덩이만큼)
            region = region.united(QRegion(box([sim.body, sim.lamp], (LAMP_POOL + 0.1) * R)))
        return region

    def night_amount(self, sim):
        """밤에 어둡게 할 정도 (0~1). 우주는 원래 어두워서 덜 어둡게 한다."""
        if sim.night <= 0.01:
            return 0.0
        return sim.night * (1.0 if sim.altitude() < 100000 else 0.6)

    def view_cam(self, sim):
        """그릴 때 쓰는 카메라: 정수 픽셀에 맞춘다. Qt 는 그림 (바위 무늬·홀드·배경) 을 옮겨 붙일 때 자리를 정수로
        반올림해서, 카메라가 소수점 픽셀만큼 움직이면 그림마다 다른 때 1px 씩 튀어 홀드가 바위 위에서 떨려 보인다.
        카메라를 정수 픽셀로 옮기면 벽에 붙은 것이 모두 함께 1px 씩 움직이고, 아바타도 같은 카메라로 그려 손이 홀드에 붙어 있다."""
        return math.floor(sim.cam * self.R + 0.5) / self.R   # round() 는 .5 에서 짝수로 가서 쓰지 않는다

    def to_screen(self, p, cam):
        return QPointF(self.W / 2 + p.x * self.R, self.wall_h * 0.58 - (p.y - cam) * self.R)

    def layer_key(self, sim):
        """바위 층 (배경·바위·홀드·볼트·흐림) 이 지난번과 같은지 볼 값. 같으면 그려 둔 층을 그대로 쓴다.
        별이 반짝이거나 여객기·우주정거장이 지나가는 높이, 구간이 바뀌는 끝 무렵은 매번 그린다."""
        cam = self.view_cam(sim)
        top_y, bottom_y = cam + VIEW_TOP + 0.6, cam - (VIEW_H - VIEW_TOP) - 1
        i, k = self.band_at(sim.altitude())
        moving = BANDS[i][1] in ("clouds", "strato", "space") or k > 0.95
        holds = tuple((id(h), h.chalk > 0, round(h.chalk, 1), round(h.snow, 1) if h.snow > 0.02 else 0.0)
                      for h in sim.holds if bottom_y < h.p.y < top_y)
        bolts = tuple((id(b), b.clipped) for b in sim.bolts if bottom_y < b.p.y < top_y) if sim.events else ()
        ledge = (sim.ledge.anchor.x, sim.ledge.anchor.y) if sim.ledge is not None else None
        snow = round(sim.precip() * 60) if sim.weather_kind == "snow" else 0
        moon = (round(sim.moon * self.R) if sim.moon is not None else None, sim.moon_flag is not None)
        carved = tuple(mark for mark in sim.carved if bottom_y - 1.5 < mark[0] < top_y + 1)
        return (round(cam * self.R), round(k * 4000), round(sim.gloom() * 60), snow, holds, bolts, ledge,
                round(sim.t * 30) if moving else 0, round(self.night_amount(sim) * 60), moon, carved)

    def paint(self, painter, sim):
        cam = self.view_cam(sim)
        key = self.layer_key(sim)
        if key != self.layer_drawn:
            self.layer_drawn = key
            self.paint_layer(sim)
        painter.save()
        painter.drawImage(0, 0, self.layer)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        night = self.night_amount(sim)
        if night > 0.01 or sim.falling:   # 밤: 헤드램프가 비추는 벽, 어둡게 한 아바타, 램프 빛 / 벽의 끝: 가장자리에서 흐리게
            lamp = night > 0.01 and sim.lamp is not None
            if lamp:
                self.paint_light(painter, sim, cam, night)
            area = self.actor_region(sim).boundingRect()   # 아바타 둘레만 비우고 어둡게 해서 붙인다
            if area.isEmpty():   # 빈 사각형을 drawImage 에 주면 그림 전체를 붙인다
                area = QRect(0, 0, self.W, self.H)
            image = self.actors
            ap = QPainter(image)
            ap.setCompositionMode(QPainter.CompositionMode_Source)
            ap.fillRect(area, Qt.transparent)
            ap.setCompositionMode(QPainter.CompositionMode_SourceOver)
            ap.setClipRect(area)
            ap.setRenderHint(QPainter.Antialiasing)
            ap.setRenderHint(QPainter.SmoothPixmapTransform)
            self.paint_actors(ap, sim, cam, night)
            if night > 0.01:
                ap.setCompositionMode(QPainter.CompositionMode_SourceAtop)
                ap.fillRect(area, QColor(*NIGHT_TINT, round(130 * night)))
            if sim.falling:   # 떨어지는 몸과 줄도 창 가장자리에서는 흐려진다
                ap.setCompositionMode(QPainter.CompositionMode_DestinationIn)
                ap.drawImage(area.topLeft(), self.mask, area)
            ap.end()
            painter.drawImage(area.topLeft(), image, area)
            if lamp:
                self.paint_beam(painter, sim, cam, night)
        else:
            self.paint_actors(painter, sim, cam, 0.0)
        for f in sim.texts:
            self.paint_float(painter, f, cam)
        self.paint_weather(painter, sim)
        self.paint_altitude(painter, sim)
        painter.restore()

    def paint_actors(self, painter, sim, cam, night):
        """벽 위에서 움직이는 것: 포타레지, 아바타, 가루·땀·색종이, 로프, 떨어지는 돌."""
        R = self.R
        if sim.ledge is not None:
            self.paint_ledge(painter, sim, cam, front=False)
        if sim.events and sim.rope_cut is not None:
            self.paint_rope(painter, sim, cam)   # 끊어진 줄은 몸 뒤에
        self.paint_climber(painter, sim, cam, night)
        if sim.ledge is not None:
            self.paint_ledge(painter, sim, cam, front=True)
        for p in sim.particles:
            k = p.age / p.life
            pt = self.to_screen(p.p, cam)
            painter.setPen(Qt.NoPen)
            if p.kind == "sweat":
                painter.setBrush(QColor(120, 190, 255, round(230 * (1 - k))))
                s = p.size * R
                painter.drawEllipse(pt, s * 0.6, s * 0.8)
            elif p.kind == "rock":
                painter.setBrush(QColor(*p.color, round(255 * min(1.0, (1 - k) * 3))))
                s = p.size * R
                painter.drawRect(QRectF(pt.x() - s / 2, pt.y() - s / 2, s, s * 0.8))
            elif p.kind == "confetti":
                painter.setBrush(QColor(*p.color, round(255 * min(1.0, (1 - k) * 3))))
                s = p.size * R
                painter.drawRect(QRectF(pt.x() - s * 0.5, pt.y() - s * 0.8, s, s * 1.6))
            else:
                painter.setBrush(QColor(255, 255, 255, round(210 * (1 - k) ** 1.5)))
                s = p.size * R * (0.6 + k * 1.2)
                painter.drawEllipse(pt, s, s)
        roped = sim.moon is None or (sim.body.y < sim.moon + 0.5 and not sim.falling)   # 달 위에 올라서면 로프를 푼다
        if sim.events and sim.rope_cut is None and not sim.grounded and roped:   # 땅에 앉아 있는 동안은 로프를 풀어 둔다
            self.paint_rope(painter, sim, cam)
        for rock in sim.rocks:
            self.paint_rock(painter, rock, cam)

    def paint_light(self, p, sim, cam, night):
        """헤드램프가 비추는 벽: 어둡게 하기 전 바위 층 (self.lit) 을 가운데가 밝고 가장자리로 흐려지게 붙인다
        (빛 안의 홀드가 낮처럼 보인다). 바위 밖 하늘은 비추지 않는다."""
        if self.lit is None or self.clip is None:
            return
        c = self.to_screen(sim.lamp, cam)
        r = LAMP_POOL * self.R
        rect = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r).toAlignedRect().intersected(QRect(0, 0, self.W, self.H))
        if rect.isEmpty():
            return
        key = (rect.getRect(), round(c.x()), round(c.y()), round(night * 60), self.layer_drawn)
        if key != self.light_key:   # 바위 층과 비추는 곳이 그대로면 (쉬는 동안) 그려 둔 것을 쓴다
            self.light_key = key
            spot = self.light = QImage(rect.size(), QImage.Format_ARGB32_Premultiplied)
            spot.fill(Qt.transparent)
            sp = QPainter(spot)
            sp.drawImage(QPoint(0, 0), self.lit, rect)
            sp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            g = QRadialGradient(c - QPointF(rect.topLeft()), r)
            g.setColorAt(0.0, QColor(0, 0, 0, round(235 * night)))
            g.setColorAt(0.55, QColor(0, 0, 0, round(150 * night)))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            sp.fillRect(QRectF(0, 0, rect.width(), rect.height()), QBrush(g))
            sp.end()
        p.save()
        p.setClipPath(self.clip)
        p.drawImage(rect.topLeft(), self.light)
        warm = QRadialGradient(c, r)   # 램프 빛 색 (따뜻한 흰빛)
        warm.setColorAt(0.0, QColor(255, 226, 160, round(45 * night)))
        warm.setColorAt(1.0, QColor(255, 226, 160, 0))
        p.fillRect(QRectF(rect), QBrush(warm))
        p.restore()

    def paint_beam(self, p, sim, cam, night):
        """램프 렌즈의 빛과 비추는 곳까지 옅은 빛줄기 (어둡게 한 아바타 위에 그린다)."""
        R = self.R
        lamp = self.body_transform(sim, cam).map(QPointF(LAMP[0] * R, LAMP[1] * R))
        c = self.to_screen(sim.lamp, cam)
        d = c - lamp
        length = math.hypot(d.x(), d.y())
        p.save()
        p.setPen(Qt.NoPen)
        if length > 1:
            n = QPointF(-d.y() / length, d.x() / length)
            w = LAMP_POOL * R * 0.7
            g = QLinearGradient(lamp, c)
            g.setColorAt(0.0, QColor(255, 240, 200, round(70 * night)))
            g.setColorAt(1.0, QColor(255, 240, 200, 0))
            p.setBrush(QBrush(g))
            p.drawPolygon(QPolygonF([lamp + n * (R * 0.05), c + n * w, c - n * w, lamp - n * (R * 0.05)]))
        glow = QRadialGradient(lamp, R * 0.3)
        glow.setColorAt(0.0, QColor(255, 250, 228, round(255 * night)))
        glow.setColorAt(0.35, QColor(255, 236, 170, round(190 * night)))
        glow.setColorAt(1.0, QColor(255, 220, 140, 0))
        p.setBrush(QBrush(glow))
        p.drawEllipse(lamp, R * 0.3, R * 0.3)
        p.restore()

    def paint_layer(self, sim):
        """바위 층: 높이에 맞는 배경, 울퉁불퉁한 바위 기둥 (위아래는 흐려진다), 홀드·볼트·포타레지 앵커, 흐림."""
        R, W, H = self.R, self.W, self.wall_h
        cam = self.view_cam(sim)
        top_y, bottom_y = cam + VIEW_TOP, cam - (VIEW_H - VIEW_TOP)
        layer = self.layer
        layer.fill(Qt.transparent)
        lp = QPainter(layer)
        lp.setRenderHint(QPainter.Antialiasing)
        lp.setRenderHint(QPainter.SmoothPixmapTransform)
        self.paint_back(lp, sim)
        outline = QPolygonF()
        steps = 28
        for i in range(steps + 1):
            y = top_y - (top_y - bottom_y) * i / steps
            outline.append(self.to_screen(V(edge_x(y, -1), y), cam))
        for i in range(steps + 1):
            y = bottom_y + (top_y - bottom_y) * i / steps
            outline.append(self.to_screen(V(edge_x(y, 1), y), cam))
        clip = QPainterPath()
        clip.addPolygon(outline)
        surface = self.to_screen(V(0, sim.moon), cam).y() if sim.moon is not None else None
        if surface is not None:   # 기둥은 달 표면에서 끝난다
            below = QPainterPath()
            below.addRect(QRectF(0, surface, W, H - surface + 1))
            clip = clip.intersected(below)
        rock = self.clip = clip   # 바위만 (땅·달 표면을 더하기 전)
        lp.setClipPath(clip)
        th = self.rock.height()
        if th > 0:
            x0 = (W - self.rock.width()) / 2
            y = (H * 0.58 + cam * R) % th - th   # 타일 위 끝은 벽과 같이 흐른다
            while y < H:
                lp.drawPixmap(QPointF(x0, y), self.rock)
                y += th
        else:   # 바위 그림이 없으면 바위 색으로
            lp.fillRect(QRectF(0, 0, W, H), QColor(168, 118, 78))
        rw = self.rock_w
        for side in (-1, 1):  # 옆면 그늘
            g = QLinearGradient(W / 2 + side * rw / 2, 0, W / 2 + side * rw * 0.22, 0)
            g.setColorAt(0, QColor(60, 30, 10, 150))
            g.setColorAt(1, QColor(60, 30, 10, 0))
            lp.fillRect(QRectF(0, 0, W, H), QBrush(g))
        for hold in sim.holds:
            if bottom_y - 0.6 < hold.p.y < top_y + 0.6:
                self.paint_hold(lp, hold, cam)
        if sim.ledge is not None:
            self.paint_anchor(lp, sim.ledge, cam)
        if sim.events:
            for bolt in sim.bolts:
                if bottom_y - 1 < bolt.p.y < top_y + 1:
                    self.paint_bolt(lp, bolt, cam)
        ground = self.to_screen(V(0, sim.ground_y()), cam).y()
        if ground < H:   # 벽 맨 아래 땅 (새 벽을 시작할 때와 벽 끝에서 떨어져 닿을 때 보인다)
            lp.setClipping(False)
            self.paint_ground(lp, ground)
            land = QPainterPath()
            land.addRect(QRectF(0, ground - R * 0.15, W, H))
            clip = clip.united(land)   # addRect 로 더하면 기둥과 겹친 곳이 구멍이 된다
            self.clip = clip   # 밤에 헤드램프가 땅도 비춘다
        if surface is not None and -MOON_SLAB * R < surface < H + 2.0 * R:   # 기둥 위 끝 달 표면과 깃발 (위로 2m)
            lp.setClipping(False)
            top = self.paint_moon_top(lp, sim, surface, cam)
            clip = clip.united(top)   # 밤에 같이 어두워지고 헤드램프가 비춘다
            rock = rock.subtracted(top)
            self.clip = clip
        night = self.night_amount(sim)
        if night > 0.01:   # 밤: 헤드램프가 비출 밝은 바위를 남겨 두고, 바위도 어둡게 (하늘보다 덜, 홀드가 보이게)
            lp.end()
            self.lit = layer.copy()
            lp = QPainter(layer)
            lp.setRenderHint(QPainter.Antialiasing)
            lp.setClipPath(clip)
            lp.fillRect(QRectF(0, 0, W, H), QColor(*NIGHT_TINT, round(180 * night)))
        marks = [mark for mark in sim.carved if bottom_y - 1.5 < mark[0] < top_y + 1]
        if marks:   # 지난 주 기록 자국은 바위에만, 밤에도 덜 어둡게 (어둡게 한 뒤에), 헤드램프 빛 안에서는 밝게
            targets = [(lp, 1 - 0.4 * night)]
            if night > 0.01:
                targets.append((QPainter(self.lit), 1.0))
            for painter, fade in targets:
                painter.setRenderHint(QPainter.Antialiasing)
                painter.setClipPath(rock)
                for mark in marks:
                    self.paint_carved(painter, mark, cam, fade)
            if night > 0.01:
                targets[1][0].end()
        lp.setClipping(False)
        self.forget_holds(sim.holds)
        gloom = sim.gloom()
        if gloom > 0.01:   # 흐리면 배경까지 어둡게, 눈이 오면 희뿌옇게
            lp.fillRect(QRectF(0, 0, W, H), QColor(18, 24, 40, round(110 * gloom)))
            if sim.weather_kind == "snow":
                lp.fillRect(QRectF(0, 0, W, H), QColor(235, 240, 255, round(45 * sim.precip())))
        lp.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        lp.drawImage(0, 0, self.mask)   # 창 가장자리를 흐린다
        lp.end()

    def paint_ground(self, p, top):
        """땅: 위 끝 top (창 y) 에 풀 띠, 아래로 흙과 돌."""
        R, W, H = self.R, self.W, self.wall_h
        g = QLinearGradient(0, top, 0, H)
        g.setColorAt(0.0, QColor(112, 92, 62))
        g.setColorAt(1.0, QColor(72, 58, 40))
        p.setPen(Qt.NoPen)
        p.fillRect(QRectF(0, top, W, H - top + 1), QBrush(g))
        for x, depth, size, light in self.pebbles:
            y = top + depth * R
            if y - size * R < H:
                p.setBrush(QColor(light, light - 6, light - 14))
                p.drawEllipse(QPointF(x * W, y), size * R * 1.3, size * R * 0.8)
        grass = QPen(QColor(92, 132, 64), max(2.0, R * 0.16))
        grass.setCapStyle(Qt.RoundCap)
        p.setPen(grass)
        p.drawLine(QPointF(0, top), QPointF(W, top))
        p.setPen(QPen(QColor(120, 160, 80), max(1.0, R * 0.05)))
        for i in range(int(W / (R * 0.35)) + 1):   # 풀잎
            x = i * R * 0.35 + (i * 37 % 11) * 0.03 * R
            p.drawLine(QPointF(x, top), QPointF(x + R * 0.06, top - R * (0.12 + (i * 53 % 7) * 0.02)))

    def band_at(self, alt):
        """높이 alt 의 배경 구간 번호와 구간 안에서 간 정도 (0~1, 로그 눈금)."""
        i = max(k for k, (start, _) in enumerate(BANDS) if alt >= start) if alt >= 0 else 0
        if i == len(BANDS) - 1:   # 우주는 달까지 (주간 사용량 50~100%)
            start, end = BANDS[i][0], MOON
        else:
            start, end = BANDS[i][0], BANDS[i + 1][0]
        a = max(0.0, alt)
        k = (math.log(a + 50) - math.log(start + 50)) / (math.log(end + 50) - math.log(start + 50))
        return i, max(0.0, min(1.0, k))

    def paint_back(self, p, sim):
        """높이에 맞는 배경. 구간 안에서 오를수록 풍경이 가라앉고, 끝 무렵 다음 구간으로 서서히 바뀐다."""
        W, H = self.W, self.wall_h
        alt = sim.altitude()
        i, k = self.band_at(alt)

        def draw(name, k, opacity):
            pix = self.backs.get(name)
            if pix is None:
                p.fillRect(QRectF(0, 0, W, H), QColor(150, 190, 230))
                return
            p.setOpacity(opacity)
            p.drawPixmap(QPointF((W - pix.width()) / 2, -(pix.height() - H) * (1 - k)), pix)
            p.setOpacity(1.0)

        draw(BANDS[i][1], k, 1.0)
        if i + 1 < len(BANDS) and k > 0.95:   # 구간 끝 5% 에서 다음 구간으로
            draw(BANDS[i + 1][1], 0.0, (k - 0.95) / 0.05)
        disk = None   # 덧그린 하늘의 달 (그 앞에는 별을 그리지 않는다)
        if BANDS[i][1] == "space" and self.backs.get("space") is not None:   # 그림 속 달 (밤에는 같이 어두워진다)
            pix = self.backs["space"]
            cx = (W - pix.width()) / 2 + SPACE_MOON[0] * pix.width()
            cy = -(pix.height() - H) * (1 - k) + SPACE_MOON[1] * pix.height()
            art = SPACE_MOON[2] * pix.width()
            if sim.moon is not None:   # 달 위에서는 하늘의 달을 지운다
                g = QRadialGradient(QPointF(cx, cy), art * 1.7)
                g.setColorAt(0.0, QColor(*SPACE_SKY))
                g.setColorAt(0.65, QColor(*SPACE_SKY))
                g.setColorAt(1.0, QColor(*SPACE_SKY, 0))
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(g))
                p.drawEllipse(QPointF(cx, cy), art * 1.7, art * 1.7)
            sky = self.sky_moon(sim, alt, art)
            if sky is not None:
                self.paint_sky_moon(p, cx, cy, *sky)
                disk = (cx, cy, sky[0])
        night = self.night_amount(sim)
        if night > 0.01:   # 밤: 하늘과 풍경을 어둡게
            p.fillRect(QRectF(0, 0, W, H), QColor(*NIGHT_TINT, round(205 * night)))
        level = 0.0 if alt < 12000 else 0.5 if alt < 100000 else 1.0
        stars = max(level, night)
        if stars:   # 별 (늘 보이는 높이에서는 반짝이고 층을 매번 그린다. 밤에만 보이는 별은 가만히 있다)
            p.setPen(QColor(0, 0, 0, 0))
            for x, y, size, phase in self.stars:
                if not level and y > 0.4:
                    continue   # 밤에만 보이는 별은 하늘 위쪽에만 (건물·산에 겹치지 않게)
                if disk is not None and (x * W - disk[0]) ** 2 + (y * H - disk[1]) ** 2 < disk[2] ** 2:
                    continue
                a = stars * (0.5 + 0.5 * math.sin((sim.t * 2.2 if level else 0.0) + phase))
                p.setBrush(QColor(255, 255, 240, round(220 * a)))
                r = max(0.8, self.R * 0.035 * size)
                p.drawEllipse(QPointF(x * W, y * H), r, r)
        name = BANDS[i][1]
        if night > 0.01:
            p.setOpacity(1.0 - 0.7 * night)   # 밤에는 지나가는 것도 어둡게
        if name == "clouds":   # 지나가는 여객기
            self.paint_plane(p, ((sim.t / 22.0) % 1.4 - 0.2) * W, H * 0.28)
        if name == "space" and 150000 < alt < 2000000:   # 지나가는 국제우주정거장
            self.paint_iss(p, ((sim.t / 30.0) % 1.4 - 0.2) * W, H * 0.22)
        p.setOpacity(1.0)

    def sky_moon(self, sim, alt, art):
        """배경 그림 속 달 (반지름 art) 자리에 덧그릴 달 (반지름 px, 진하기) 또는 None: 주간 100% 로 갈수록 커지고
        (그림이 내려오며 위에서 나타난다), 기둥 위 끝 달 표면이 나타나면 1.5초 동안 사라진다. 벽의 끝으로 떨어질 때는
        높이를 따라 작아진다."""
        fade = 1.0 if sim.moon is None else 1.0 - (sim.t - sim.moon_t) / 1.5
        if fade <= 0:
            return None
        u = max(0.0, min(1.0, (height_pct(alt) - 50) / 50))
        return max(art * 1.05, self.W * 0.4 * u ** 4), fade

    def paint_sky_moon(self, p, cx, cy, r, fade):
        disk = QPainterPath()
        disk.addEllipse(QPointF(cx, cy), r, r)
        p.save()
        p.setOpacity(fade)
        p.setPen(Qt.NoPen)
        g = QRadialGradient(QPointF(cx - r * 0.3, cy - r * 0.3), r * 1.4)
        g.setColorAt(0.0, QColor(236, 234, 222))
        g.setColorAt(1.0, QColor(168, 166, 158))
        p.setBrush(QBrush(g))
        p.drawPath(disk)
        p.setClipPath(disk)
        p.setBrush(QColor(150, 148, 142, 150))   # 바다 (어두운 들판)
        for dx, dy, w, h in ((-0.3, -0.2, 0.42, 0.3), (0.15, -0.35, 0.3, 0.22), (0.25, 0.1, 0.38, 0.3), (-0.2, 0.35, 0.3, 0.2)):
            p.drawEllipse(QPointF(cx + dx * r, cy + dy * r), w * r, h * r)
        if r > self.R * 0.6:   # 커지면 크레이터
            for dx, dy, cr in ((-0.55, 0.15, 0.08), (0.45, -0.1, 0.06), (0.05, 0.6, 0.1), (-0.1, -0.6, 0.05), (0.6, 0.45, 0.07)):
                p.setBrush(QColor(130, 128, 122, 170))
                p.drawEllipse(QPointF(cx + dx * r, cy + dy * r), cr * r, cr * r)
        p.restore()

    def paint_moon_top(self, p, sim, top, cam):
        """기둥 위 끝 달 표면 (창 y top): 창 폭 가득 회색 띠, 아래 끝은 울퉁불퉁, 앞면에 크레이터. 깃발을 꽂았으면 깃발.
        그린 곳 (QPainterPath) 을 돌려준다."""
        R, W = self.R, self.W
        slab = MOON_SLAB * R
        path = QPainterPath(QPointF(0, top))
        path.lineTo(W, top)
        steps = 14
        for i in range(steps + 1):   # 아래 끝 (오른쪽에서 왼쪽으로)
            x = W * (1 - i / steps)
            path.lineTo(x, top + slab * (0.7 + 0.18 * math.sin(i * 1.9) + 0.1 * math.sin(i * 4.3)))
        path.closeSubpath()
        g = QLinearGradient(0, top, 0, top + slab)
        g.setColorAt(0.0, QColor(196, 196, 202))
        g.setColorAt(1.0, QColor(112, 112, 122))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(g))
        p.drawPath(path)
        p.save()
        p.setClipPath(path)
        for k in range(9):   # 크레이터
            cx, cy = (k * 0.137 + 0.05) % 1.0 * W, top + slab * (0.18 + (k * 0.29) % 0.42)
            cr = R * (0.1 + (k * 0.37) % 0.2)
            p.setBrush(QColor(140, 140, 150))
            p.drawEllipse(QPointF(cx, cy), cr * 1.3, cr * 0.7)
            p.setBrush(QColor(222, 222, 228, 160))
            p.drawEllipse(QPointF(cx - cr * 0.15, cy - cr * 0.3), cr * 1.0, cr * 0.25)
        p.restore()
        p.setPen(QPen(QColor(232, 232, 238), max(1.0, R * 0.06)))
        p.drawLine(QPointF(0, top), QPointF(W, top))
        flag = sim.moon_flag
        if flag is not None:   # 깃대 위 가로대에 걸린 깃발 (달에는 바람이 없다)
            base = self.to_screen(flag, cam)
            side = 1 if flag.x >= 0 else -1
            pole_top = base.y() - R * 2.0
            p.setPen(QPen(QColor(205, 205, 210), max(1.0, R * 0.06)))
            p.drawLine(base, QPointF(base.x(), pole_top))
            p.drawLine(QPointF(base.x(), pole_top), QPointF(base.x() + side * R * 0.9, pole_top))
            p.setPen(QPen(QColor(150, 70, 45), max(1.0, R * 0.03)))
            p.setBrush(QColor(217, 119, 87))
            cloth = QRectF(min(base.x(), base.x() + side * R * 0.9), pole_top, R * 0.9, R * 0.55)
            p.drawRect(cloth)
            for part in (cloth, QRectF(base.x() - R * 0.03, pole_top, R * 0.06, base.y() - pole_top),
                         QRectF(min(base.x(), base.x() + side * R * 0.9), pole_top - R * 0.03, R * 0.9, R * 0.06)):
                piece = QPainterPath()   # 천·깃대·가로대만 (그 뒤 하늘은 배경에서 이미 어두워졌다)
                piece.addRect(part.adjusted(-1, -1, 1, 1))
                path = path.united(piece)
        return path

    def paint_plane(self, p, x, y):
        s = self.R * 0.22
        p.setPen(QPen(QColor(90, 100, 120, 200), 1))
        p.setBrush(QColor(245, 248, 252))
        p.drawPolygon(QPolygonF([QPointF(x - 2.2 * s, y), QPointF(x + 2.0 * s, y - 0.25 * s), QPointF(x + 2.4 * s, y),
                                 QPointF(x + 2.0 * s, y + 0.25 * s)]))
        p.drawPolygon(QPolygonF([QPointF(x - 0.2 * s, y), QPointF(x + 0.5 * s, y - 1.3 * s), QPointF(x + 0.9 * s, y)]))
        p.drawPolygon(QPolygonF([QPointF(x - 1.9 * s, y), QPointF(x - 1.6 * s, y - 0.7 * s), QPointF(x - 1.3 * s, y)]))
        p.setPen(QPen(QColor(255, 255, 255, 120), max(1.0, s * 0.3)))
        p.drawLine(QPointF(x - 2.3 * s, y), QPointF(x - 9 * s, y))   # 비행운

    def paint_iss(self, p, x, y):
        s = self.R * 0.16
        p.setPen(QPen(QColor(30, 30, 40), 1))
        p.setBrush(QColor(60, 90, 170))
        for dx in (-3.2, -2.1, 1.1, 2.2):
            p.drawRect(QRectF(x + dx * s, y - 1.2 * s, s, 2.4 * s))
        p.setBrush(QColor(220, 220, 225))
        p.drawRect(QRectF(x - 2.2 * s, y - 0.12 * s, 4.4 * s, 0.24 * s))
        p.drawRect(QRectF(x - 0.6 * s, y - 0.4 * s, 1.2 * s, 0.8 * s))

    @staticmethod
    def moon_note(sim):
        if not sim.week_reset:
            return "주간 리셋까지 달에서 쉬어요"
        at = datetime.fromtimestamp(sim.week_reset, KST)
        return f"리셋 ({'월화수목금토일'[at.weekday()]} {at:%H:%M}) 까지 달에서 쉬어요"

    def prepare_hud(self, sim):
        """왼쪽 위 상자 그림 (self.hud, self.hud_rect): 높이와 다음 기록, 아직 오르지 않은 양과 빠르기 (막대),
        체력 (막대). 글자나 막대가 바뀔 때만 다시 그린다. 다시 그릴 곳 (ClimbWindow.dirty) 이 커진 상자를 알도록
        그리기 전에 부른다."""
        alt = max(0.0, sim.shown_altitude())
        nxt = sim.next_milestone()
        small = QFont(self.small)
        small.setBold(False)
        small.setPixelSize(max(9, round(self.small.pixelSize() * 0.8)))
        rows = [(fmt_alt(alt), self.small, QColor(255, 240, 210), None)]   # (글자, 글꼴, 색, (채움, 막대 색) 또는 None)
        if sim.moon is not None and not sim.falling:
            rows.append((self.moon_note(sim), small, QColor(230, 215, 190), None))
        elif sim.grounded:
            rows.append(("주간 사용량이 돌아오면 다시 올라요", small, QColor(230, 215, 190), None))
        elif nxt and not sim.falling:
            rows.append((f"다음: {nxt[1]}까지 {fmt_alt(nxt[0] - alt)}", small, QColor(230, 215, 190), None))
        if sim.tokens_on:
            state = sim.state_label()
            fill = max(0.0, min(1.0, sim.bank / (PACE_BANK[1] * 1.5)))   # 전력 질주 기준의 1.5배가 가득
            left = max(0.0, sim.bank)
            if sim.week_pct is not None:   # 높이 숫자는 주간 % 눈금이라 바위 m 대신 토큰으로 (100 단위 올림)
                left = f"남은 토큰 {math.ceil(left / M_PER_TOKEN / 100 - 1e-9) * 100:,}"
            else:
                left = f"오를 거리 {left:,.1f}m"
            rows.append((f"{left} · {state}", small, QColor(170, 235, 255),
                         (fill, {1: (110, 200, 120), 2: (240, 200, 70), 3: (255, 120, 60)}[sim.pace])))
        hp = max(0, min(100, round(sim.hp_shown)))
        rows.append((f"체력 {hp}%", small, QColor(230, 215, 190),
                     (hp / 100, (72, 187, 120) if hp > 50 else (236, 201, 75) if hp > 20 else (245, 101, 101))))
        key = tuple((text, round(meter[0] * 200) if meter else None, meter[1] if meter else None)
                    for text, _, _, meter in rows)
        if key != self.hud_key:   # 글자나 막대가 바뀔 때만 다시 그린다
            self.hud_key = key
            self.hud = self.draw_hud(rows)
            self.hud_rect = QRect(6, 6, self.hud.width(), self.hud.height())

    def paint_altitude(self, p, sim):
        """왼쪽 위 상자 (prepare_hud) 와 기록을 넘을 때 말풍선."""
        self.prepare_hud(sim)
        p.drawPixmap(6, 6, self.hud)
        self.banner_rect = QRect()
        if sim.banner is not None:
            text, age = sim.banner
            alpha = min(1.0, age / 0.25, (3.5 - age) / 0.5)
            font = QFont(self.font)
            font.setBold(True)
            p.setFont(font)
            fm = p.fontMetrics()
            bw = min(self.W - 16, fm.horizontalAdvance(text) + 20)
            box = QRectF((self.W - bw) / 2, self.wall_h * 0.13, bw, fm.height() * (1 if fm.horizontalAdvance(text) + 20 <= bw else 2) + 10)
            p.setOpacity(max(0.0, alpha))
            p.setPen(QPen(QColor(150, 100, 0), 1.5))
            p.setBrush(QColor(255, 236, 150))
            p.drawRoundedRect(box, 8, 8)
            self.banner_rect = box.toAlignedRect()
            p.setPen(QColor(60, 35, 0))
            p.drawText(box.adjusted(6, 0, -6, 0), Qt.AlignCenter | Qt.TextWordWrap, text)
            p.setOpacity(1.0)

    def draw_hud(self, rows):
        """높이 상자 그림: 줄마다 글자와 (있으면) 막대."""
        bar = max(3, round(self.R * 0.1))
        metrics = [QFontMetrics(font) for _, font, _, _ in rows]
        heights = [fm.height() + (bar + 2 if meter else 0) for fm, (_, _, _, meter) in zip(metrics, rows)]
        w = max(fm.horizontalAdvance(text) for fm, (text, _, _, _) in zip(metrics, rows))
        pixmap = QPixmap(w + 12, sum(heights) + 4)
        pixmap.fill(Qt.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(40, 30, 25, 200))
        p.drawRoundedRect(QRectF(0, 0, w + 12, sum(heights) + 4), 5, 5)
        y = 2
        for (text, font, color, meter), h in zip(rows, heights):
            p.setFont(font)
            p.setPen(color)
            line = h - (bar + 2 if meter else 0)
            p.drawText(QRectF(6, y, w, line), Qt.AlignLeft | Qt.AlignVCenter, text)
            if meter:
                fill, rgb = meter
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 255, 255, 50))
                p.drawRoundedRect(QRectF(6, y + line, w, bar), bar / 2, bar / 2)
                if fill > 0:
                    p.setBrush(QColor(*rgb))
                    p.drawRoundedRect(QRectF(6, y + line, max(bar, w * fill), bar), bar / 2, bar / 2)
            y += h
        p.end()
        return pixmap

    def paint_hold(self, p, hold, cam):
        """홀드는 초크·눈 정도마다 한 번 그려 둔 그림을 붙인다 (매 프레임 다각형을 다시 그리지 않는다)."""
        key = (hold.chalk > 0, round(hold.chalk, 1), round(hold.snow, 1) if hold.snow > 0.02 else 0.0)
        cached = self.hold_cache.get(id(hold))
        if cached is None or cached[0] is not hold or cached[1] != key:
            cached = (hold, key) + self.draw_hold(hold, key)
            self.hold_cache[id(hold)] = cached
        pixmap, pad = cached[2], cached[3]
        c = self.to_screen(hold.p, cam)
        p.drawPixmap(QPointF(c.x() - pad, c.y() - pad), pixmap)

    def forget_holds(self, holds):
        """지나간 홀드의 그림을 버린다."""
        if len(self.hold_cache) > 2 * len(holds) + 20:
            live = {id(h) for h in holds}
            self.hold_cache = {k: v for k, v in self.hold_cache.items() if k in live}

    def draw_hold(self, hold, key):
        """홀드 그림 한 장 (그림자, 바위 색 면, 위쪽 빛, 초크, 쌓인 눈) 과 그림 안 홀드 중심까지의 거리 pad."""
        used, chalk, snow = key
        r = hold.r * self.R
        pad = math.ceil(r * 1.6) + 2
        pixmap = QPixmap(2 * pad, 2 * pad)
        pixmap.fill(Qt.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(pad, pad)

        def poly(scale=1.0, dx=0.0, dy=0.0):
            return QPolygonF([QPointF(c.x() + x * r * scale + dx, c.y() - y * r * scale + dy) for x, y in hold.shape])

        k = 1.0 if used else 0.55   # 쓰지 않은 홀드는 바위에 섞이게 흐리게
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(55, 28, 12, round(190 * k)))
        p.drawPolygon(poly(1.08, 0, r * 0.3))
        p.setPen(QPen(QColor(90, 52, 28, round(200 * k)), max(1.0, r * 0.08)))
        p.setBrush(QColor(222, 178, 124, round(255 * (1.0 if used else 0.6))) if not hold.big
                   else QColor(212, 165, 110, round(255 * (1.0 if used else 0.6))))
        p.drawPolygon(poly())
        top = QPolygonF([pt for pt, (x, y) in zip(poly(), hold.shape) if y > -0.1])
        p.setPen(QPen(QColor(250, 222, 180, round(255 * k)), max(1.0, r * 0.12)))
        p.drawPolyline(top)
        if used:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, round(190 * max(0.05, chalk))))
            p.drawPolygon(poly(0.72, 0, -r * 0.12))
        if snow:   # 홀드 위에 쌓인 눈
            pen = QPen(QColor(250, 252, 255, round(255 * min(1.0, snow * 1.5))), max(1.5, r * (0.15 + 0.3 * snow)))
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawPolyline(QPolygonF([pt for pt, (x, y) in zip(poly(1.0, 0, -r * 0.1), hold.shape) if y > -0.1]))
        p.end()
        return pixmap, pad

    def paint_carved(self, p, mark, cam, fade):
        """바위에 새긴 지난 주 기록: 기둥을 가로지르는 초크 점선과 그 위 side 쪽 가장자리에 두 줄 ('지난주', '52.3km').
        fade 만큼 흐리게 (밤)."""
        y, side, name, alt = mark
        R = self.R
        left, right = self.to_screen(V(edge_x(y, -1), y), cam), self.to_screen(V(edge_x(y, 1), y), cam)
        pen = QPen(QColor(255, 255, 255, round(190 * fade)), max(1.5, R * 0.07), Qt.CustomDashLine, Qt.RoundCap)
        pen.setDashPattern([2.5, 2.0])
        p.setPen(pen)
        p.drawLine(left, right)
        image, dx, dy = self.carved_text(name, alt, side, int((right.x() - left.x()) * 0.62))
        x = right.x() - R * 0.4 if side > 0 else left.x() + R * 0.4
        p.setOpacity(fade)
        p.drawImage(QPoint(round(x + dx), round(left.y() + dy)), image)
        p.setOpacity(1.0)

    def carved_text(self, name, alt, side, width):
        """자국 글자 두 줄 (이름 위, 높이 아래) 을 테두리까지 그린 그림과 붙일 자리 (side 쪽 글자 끝, 점선에서 떨어진 만큼).
        좁으면 글자를 줄이고 그래도 넘치면 말줄임. 바위 층은 카메라가 움직이면 매번 다시 그려서 글자는 그려 둔다."""
        key = (name, alt, side, width)
        if key not in self.carved_cache:
            if len(self.carved_cache) > 16:
                self.carved_cache.clear()
            font = QFont(self.small)
            size = max(9, round(self.small.pixelSize() * 0.85))
            while True:
                font.setPixelSize(size)
                metrics = QFontMetrics(font)
                if size <= 8 or max(metrics.horizontalAdvance(t) for t in (name, alt)) <= width:
                    break
                size -= 1
            path = QPainterPath()
            for i, line in enumerate((alt, name)):   # 아래 줄부터, side 쪽 끝 (x 0) 에 맞춰
                text = metrics.elidedText(line, Qt.ElideRight, width)
                path.addText(-metrics.horizontalAdvance(text) if side > 0 else 0, -i * metrics.height(), font, text)
            box = path.boundingRect().adjusted(-2, -2, 2, 2).toAlignedRect()
            image = QImage(box.size(), QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            ip = QPainter(image)
            ip.setRenderHint(QPainter.Antialiasing)
            ip.translate(-box.x(), -box.y())
            ip.setPen(QPen(QColor(60, 30, 10, 150), 2.5))   # 바위 무늬 위에서도 읽히게 어두운 테두리
            ip.setBrush(Qt.NoBrush)
            ip.drawPath(path)
            ip.setPen(Qt.NoPen)
            ip.setBrush(QColor(255, 255, 255, 230))
            ip.drawPath(path)
            ip.end()
            self.carved_cache[key] = (image, box.x(), box.y() - self.R * 0.12 - metrics.descent())
        return self.carved_cache[key]

    def paint_bolt(self, p, bolt, cam):
        """볼트 고리와 퀵드로 (아래 카라비너까지)."""
        R = self.R
        a = self.to_screen(bolt.p, cam)
        b = self.to_screen(bolt.rope_point(), cam)
        p.setPen(QPen(QColor(40, 40, 45), max(1.0, R * 0.04)))
        p.setBrush(QColor(200, 200, 205))
        p.drawEllipse(a, R * 0.07, R * 0.07)
        p.setPen(QPen(QColor(90, 90, 100), max(1.0, R * 0.05)))
        p.drawLine(a, b)
        p.setPen(QPen(QColor(230, 120, 40) if bolt.clipped else QColor(170, 170, 180), max(1.0, R * 0.04)))
        p.setBrush(QColor(0, 0, 0, 0))
        p.drawEllipse(QRectF(b.x() - R * 0.06, b.y() - R * 0.09, R * 0.12, R * 0.18))

    def paint_rope(self, p, sim, cam):
        """로프: 하네스 (또는 로프를 쥔 손) → 건 퀵드로들 (위에서 아래로) → 화면 아래 빌레이어.
        줄이 끊어졌으면 (벽의 끝) 하네스에 끌려 나부끼는 줄과 퀵드로에 남은 짧은 끝만."""
        R = self.R
        if sim.rope_cut is not None:
            h = sim.harness()
            k = max(0.0, min(1.0, -sim.vel.y / 8.0))   # 하네스에 남은 줄: 처음엔 늘어지고 빨리 떨어질수록 위로 나부낀다
            tail = [h + V(0.25 * math.sin(sim.t * 9 + i * 0.9) * (i / 6), 0.45 * i * (2 * k - 1)) for i in range(7)]
            cut = sim.rope_cut   # 빌레이어 쪽 줄: 아래 퀵드로들을 지나 끊어진 끝이 맨 위 퀵드로에 늘어진다
            lower = sorted((b for b in sim.bolts if b.clipped and b.rope_point().y < cut.y - 0.01), key=lambda b: -b.p.y)
            belay = [cut + V(0.12 * math.sin(sim.t * 7), -0.4), cut] + [b.rope_point() for b in lower]
            belay.append(V(belay[-1].x * 0.6, max(cam - VIEW_H, sim.ground_y())))
            for line in (tail, belay):
                poly = QPolygonF([self.to_screen(q, cam) for q in line])
                p.setBrush(QColor(0, 0, 0, 0))
                p.setPen(QPen(QColor(20, 40, 60, 160), max(2.0, R * 0.08)))
                p.drawPolyline(poly)
                p.setPen(QPen(QColor(40, 170, 220), max(1.0, R * 0.05)))
                p.drawPolyline(poly)
            return
        pts = [sim.harness()]
        if sim.rope_hand is not None:
            pts.append(sim.limbs[sim.rope_hand].pos)
        clipped = sorted((b for b in sim.bolts if b.clipped), key=lambda b: -b.p.y)
        pts += [b.rope_point() for b in clipped]
        last = pts[-1]
        pts.append(V(last.x * 0.6, max(cam - VIEW_H, sim.ground_y())))   # 아래 빌레이어 (땅이 보이면 땅까지)
        poly = QPolygonF([self.to_screen(q, cam) for q in pts])
        p.setBrush(QColor(0, 0, 0, 0))
        p.setPen(QPen(QColor(20, 40, 60, 160), max(2.0, R * 0.08)))
        p.drawPolyline(poly)
        p.setPen(QPen(QColor(40, 170, 220), max(1.0, R * 0.05)))
        p.drawPolyline(poly)

    def paint_rock(self, p, rock, cam):
        pos, _, angle, _, size, _ = rock
        c = self.to_screen(pos, cam)
        r = size * self.R
        pts = [QPointF(c.x() + math.cos(angle + i * 1.05) * r * (0.8 + 0.25 * (i % 2)),
                       c.y() + math.sin(angle + i * 1.05) * r * (0.8 + 0.25 * (i % 2))) for i in range(6)]
        p.setPen(QPen(QColor(70, 45, 25), max(1.0, r * 0.15)))
        p.setBrush(QColor(165, 120, 80))
        p.drawPolygon(QPolygonF(pts))

    def paint_anchor(self, p, ledge, cam):
        a = self.to_screen(ledge.anchor, cam)
        s = max(2.0, self.R * 0.11)
        p.setPen(QPen(QColor(40, 40, 45), max(1.0, s * 0.4)))
        p.setBrush(QColor(190, 192, 200))
        p.drawEllipse(a, s, s)

    def paint_ledge(self, p, sim, cam, front):
        """포타레지: 뒤쪽 (끈·뒤 막대·천) 은 아바타 뒤에, 앞쪽 (앞 막대·앞 끈·침낭·꾸러미) 은 앞에 그린다."""
        ledge, R = sim.ledge, self.R
        anchor = self.to_screen(ledge.anchor, cam)
        o = smooth(ledge.open)
        hw, depth = 0.25 + 1.05 * o, 0.32 * o
        bed = ledge.bed
        back = [self.to_screen(bed + V(-hw, depth), cam), self.to_screen(bed + V(hw, depth), cam)]
        fore = [self.to_screen(bed + V(-hw, -0.02), cam), self.to_screen(bed + V(hw, -0.02), cam)]
        strap = QPen(QColor(45, 45, 50), max(1.0, R * 0.04))
        rail = QPen(QColor(80, 82, 90), max(1.5, R * 0.08))
        rail.setCapStyle(Qt.RoundCap)
        bundle = None
        if ledge.carry is not None:
            bundle = self.to_screen(sim.limbs[ledge.carry].pos + V(0, -0.15), cam)
        elif ledge.stow is not None:
            bundle = self.to_screen((ledge.anchor + V(0, -0.45)).lerp(sim.bag_mouth(), smooth(ledge.stow)), cam)
        elif ledge.hung and ledge.open < 0.05:
            bundle = self.to_screen(ledge.anchor + V(0, -0.45), cam)
        if not front:
            if ledge.hung and ledge.open >= 0.05:
                p.setPen(strap)
                for corner in back:
                    p.drawLine(anchor, corner)
                p.setPen(QPen(QColor(150, 70, 25), max(1.0, R * 0.04)))
                p.setBrush(QColor(226, 118, 44))
                p.drawPolygon(QPolygonF([back[0], back[1], fore[1], fore[0]]))
                p.setPen(rail)
                p.drawLine(back[0], back[1])
            return
        if ledge.hung and ledge.open >= 0.05:
            if ledge.bag > 0:   # 침낭
                top = bed.y + 0.1 + 0.62 * smooth(ledge.bag)
                rect = QRectF(self.to_screen(V(sim.body.x - 0.95, top), cam), self.to_screen(V(sim.body.x + 0.95, bed.y), cam))
                p.setPen(QPen(QColor(20, 70, 85), max(1.0, R * 0.05)))
                p.setBrush(QColor(44, 140, 160))
                p.drawRoundedRect(rect, R * 0.3, R * 0.3)
                p.setPen(QPen(QColor(110, 200, 215), max(1.0, R * 0.04)))
                for k in (0.33, 0.66):
                    x = rect.left() + rect.width() * k
                    p.drawLine(QPointF(x, rect.top() + R * 0.12), QPointF(x, rect.bottom() - R * 0.06))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(200, 235, 240))
                p.drawRoundedRect(QRectF(rect.left(), rect.top(), rect.width(), R * 0.14), R * 0.07, R * 0.07)
            p.setPen(strap)
            for corner in fore:
                p.drawLine(anchor, corner)
            p.setPen(rail)
            p.drawLine(fore[0], fore[1])
            p.drawLine(back[0], fore[0])
            p.drawLine(back[1], fore[1])
        if bundle is not None:   # 접힌 꾸러미
            if ledge.hung:
                p.setPen(strap)
                p.drawLine(anchor, bundle)
            p.setPen(QPen(QColor(150, 70, 25), max(1.0, R * 0.04)))
            p.setBrush(QColor(226, 118, 44))
            p.drawRoundedRect(QRectF(bundle.x() - R * 0.16, bundle.y() - R * 0.3, R * 0.32, R * 0.6), R * 0.12, R * 0.12)

    def paint_float(self, painter, f, cam):
        pt = self.to_screen(f.p, cam)
        fade = max(0.0, 1 - max(0.0, f.age - f.life * 0.5) / (f.life * 0.5))
        alpha = round(255 * fade)
        font = QFont(self.small)
        if f.style == "z":
            font.setPixelSize(round(self.small.pixelSize() * (0.8 + 0.5 * min(1.0, f.age / 1.5))))
            fill, line = QColor(225, 238, 255, alpha), QColor(40, 50, 90, alpha)
        elif f.style == "token":
            fill, line = QColor(170, 235, 255, alpha), QColor(10, 50, 80, alpha)
        else:
            fill, line = QColor(255, 236, 140, alpha), QColor(60, 30, 10, alpha)
        path = QPainterPath()
        painter.setFont(font)
        path.addText(pt.x() - painter.fontMetrics().horizontalAdvance(f.text) / 2, pt.y(), font, f.text)
        painter.setPen(QPen(line, 3))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawPath(path)

    def paint_weather(self, painter, sim):
        if not sim.drops and sim.flash <= 0:
            return
        W, H = self.W, self.wall_h
        painter.save()
        rain = sim.weather_kind == "rain"
        edge, side = H * 0.14, W * 0.10
        groups = {}   # 진하기 (6단계) 와 눈송이 크기로 묶어 한 번에 그린다 (방울마다 그리면 느리다)
        for x, y, speed, size, phase in sim.drops:
            px, py = x * W, y * H
            fade = max(0.0, min(1.0, py / edge, (H - py) / edge, px / side, (W - px) / side))   # 창 가장자리에서 흐리게
            if fade <= 0:
                continue
            level = min(5, int(fade * 6))
            if rain:
                length = H * 0.05 * size
                dx = -sim.wind / speed * length
                groups.setdefault(level, []).append(QLineF(px, py, px + dx, py - length))
            else:
                groups.setdefault((level, round(max(1.0, self.R * 0.06 * size) * 2)), []).append(QPointF(px, py))
        for key, items in groups.items():
            if rain:
                painter.setPen(QPen(QColor(215, 228, 245, round(150 * (key + 1) / 6)), max(1.0, self.R * 0.035)))
                painter.drawLines(items)
            else:
                pen = QPen(QColor(255, 255, 255, round(230 * (key[0] + 1) / 6)), key[1])
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)
                painter.drawPoints(QPolygonF(items))
        if sim.flash > 0:   # 번개: 창 전체를 (가장자리는 흐리게) 밝힌다
            painter.setOpacity(0.67 * sim.flash)
            painter.drawImage(0, 0, self.mask)
            painter.setOpacity(1.0)
        painter.restore()

    def body_pose(self, sim):
        """몸통 그림의 (세로 배율: 숨쉬기·찌그러짐, 가로 방향: 돌아보기)."""
        tired = sim.hp <= 30 and not sim.sleeping
        period = 4.0 if sim.sleeping else 1.2 if tired else 2.6
        depth = 0.035 if sim.sleeping else 0.04 if tired else 0.018
        sy = sim.squash * (1 + depth * math.sin(sim.t * 2 * math.pi / period))
        fx = sim.face_now if abs(sim.face_now) > 0.12 else math.copysign(0.12, sim.face_now or 1)
        return sy, fx

    def body_transform(self, sim, cam):
        """몸통 그림 좌표 (공 중심 원점, px) → 창 좌표. paint_climber 의 몸통과 같은 순서로 옮기고 돌리고 늘인다."""
        sy, fx = self.body_pose(sim)
        bpt = self.to_screen(sim.body, cam)
        t = QTransform()
        t.translate(bpt.x(), bpt.y())
        t.rotate(-math.degrees(sim.tilt + sim.look_tilt))
        t.scale(fx / math.sqrt(sy), sy)
        return t

    def paint_climber(self, p, sim, cam, night=0.0):
        R = self.R
        tilt = sim.tilt
        body = sim.body
        bpt = self.to_screen(body, cam)
        # 초크백 (몸 뒤, 허리). 흰 양이 체력
        if sim.ledge is None or sim.ledge.bag < 0.5:
            bag = self.to_screen(sim.bag_mouth(0.18), cam)
            p.save()
            p.translate(bag)
            p.rotate(-math.degrees(tilt))
            bw, bh = 0.36 * R, 0.44 * R
            p.setPen(QPen(QColor(40, 20, 20), max(1.0, R * 0.05)))
            p.setBrush(QColor(196, 60, 46))
            p.drawRoundedRect(QRectF(-bw / 2, -bh / 2, bw, bh), bw * 0.25, bw * 0.25)
            p.setPen(Qt.NoPen)
            level = max(0.0, sim.hp_shown) / 100
            p.setBrush(QColor(250, 250, 250))
            p.drawRect(QRectF(-bw / 2 + 2, -bh / 2 + 2 + (bh - 4) * (1 - level), bw - 4, (bh - 4) * level))
            p.restore()
        # 팔다리 (몸 뒤)
        pen = QPen(QColor(18, 18, 20), max(2.0, R * 0.13))
        pen.setJoinStyle(Qt.MiterJoin)
        pen.setCapStyle(Qt.FlatCap)
        tremble = sim.hp <= 30 and not sim.sleeping
        for i, limb in enumerate(sim.limbs.values()):
            if limb.tucked and sim.ledge is not None and sim.ledge.bag > 0.5:
                continue   # 침낭 안
            a, b = ARM if limb.kind == "H" else LEG
            root = limb.root(body, tilt)
            if sim.falling:   # 빙글 돌며 떨어지는 동안은 몸 기준으로 (팔꿈치가 한 프레임에 뒤집히지 않게)
                elbow, tip = two_bone(root, root + (limb.pos - root).rot(-tilt), a, b, limb.side, limb.kind, limb.drop)
                elbow, tip = root + (elbow - root).rot(tilt), root + (tip - root).rot(tilt)
            else:
                elbow, tip = two_bone(root, limb.pos, a, b, limb.side, limb.kind, limb.drop)
            if tremble and limb.mode == "grip":
                elbow = elbow + V(math.sin(sim.t * 47 + i * 1.7), math.cos(sim.t * 53 + i)) * 0.035
            pts = [self.to_screen(q, cam) for q in (root, elbow, tip)]
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(QPolygonF(pts))
            fore = math.atan2(tip.y - elbow.y, tip.x - elbow.x)
            thin = QPen(QColor(18, 18, 20), max(1.5, R * 0.09))
            thin.setCapStyle(Qt.RoundCap)
            p.setPen(thin)
            if limb.kind == "H":
                base = fore if limb.mode != "grip" else fore * 0.4 + (-math.pi / 2) * 0.6
                length = 0.08 if limb.fist else 0.2 if limb.mode != "grip" else 0.16
                for da in (-0.55, 0.0, 0.55):
                    f = tip + V(math.cos(base + da), math.sin(base + da)) * length
                    p.drawLine(pts[2], self.to_screen(f, cam))
            else:
                toe = tip + V(limb.side * 0.24, -0.04)
                p.drawLine(pts[2], self.to_screen(toe, cam))
        # 몸통 (숨쉬기·찌그러짐·돌아보기)
        sy, fx = self.body_pose(sim)
        p.save()
        p.translate(bpt)
        p.rotate(-math.degrees(tilt + sim.look_tilt))
        p.scale(fx / math.sqrt(sy), sy)
        p.drawPixmap(QPointF(-self.anchor[0], -self.anchor[1]), self.body)
        if night > 0.01:   # 헤드램프 띠와 몸통 (빛은 아바타를 어둡게 한 뒤 paint_beam 이 그린다)
            strap = QPen(QColor(40, 44, 52), max(1.5, R * 0.1))
            strap.setCapStyle(Qt.FlatCap)
            band = QPainterPath(QPointF(-0.9 * R, -0.42 * R))
            band.quadTo(QPointF(-0.45 * R, -0.82 * R), QPointF(0.35 * R, -0.93 * R))
            p.setPen(strap)
            p.setBrush(Qt.NoBrush)
            p.drawPath(band)
            lx, ly = LAMP[0] * R, LAMP[1] * R
            p.setPen(QPen(QColor(25, 25, 30), max(1.0, R * 0.04)))
            p.setBrush(QColor(70, 74, 84))
            p.drawRoundedRect(QRectF(lx - 0.15 * R, ly - 0.11 * R, 0.3 * R, 0.22 * R), 0.05 * R, 0.05 * R)
            p.setBrush(QColor(255, 244, 200) if sim.lamp is not None else QColor(110, 110, 104))
            p.drawEllipse(QPointF(lx, ly), 0.075 * R, 0.075 * R)
        closure = sim.closure()
        if closure > 0.03:   # 눈꺼풀
            for ex, ey, rx, ry, color in self.eyes:
                p.save()
                clip = QPainterPath()
                clip.addEllipse(QPointF(ex, ey), rx * 1.3, ry * 1.3)
                p.setClipPath(clip)
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawRect(QRectF(ex - rx * 1.4, ey - ry * 1.3, rx * 2.8, ry * 2.6 * closure))
                p.restore()
                if closure > 0.85:
                    line = QPen(QColor(20, 20, 20), max(1.0, ry * 0.35))
                    line.setCapStyle(Qt.RoundCap)
                    p.setPen(line)
                    p.setBrush(Qt.NoBrush)
                    p.drawArc(QRectF(ex - rx, ey - ry * 0.7, rx * 2, ry * 1.2), 200 * 16, 140 * 16)
        p.restore()


CARD_ID = ":base"   # 마우스 올림 카드에서 이 창을 가리키는 이름 (세션 id 에는 ':' 가 없다)


class ClimbWindow(QWidget):
    """펫 줄 오른쪽 끝 칸의 끝없이 오르기 창. PetManager 가 칸 (slot_w) 을 비워 두고 place 로 세운다.
    보이는 동안만 계산과 그리기를 돌리고 (FRAME_MS), retire 로 숨으면 멈춘다."""

    def __init__(self, manager, climber):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.NoDropShadowWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Claude Pet Climb")
        self.manager = manager
        self.sim = climber
        self.look = None
        self.scale = None
        self.slot_w = 0
        self.size_hint_ = (0, 0)
        self.spot = None            # 마지막 place 인자 (칸 오른쪽 끝, 바닥, 작업 영역)
        self.shape = None           # 마지막으로 건 클릭 영역
        self.last = None            # 마지막으로 계산을 돌린 때 (monotonic)
        self.actor = None           # 지난번에 다시 그린 아바타 둘레 (None 이면 창 전체를 그렸다)
        self.timer = QTimer(self, timeout=self.tick)
        self.timer.setTimerType(Qt.PreciseTimer)   # 프레임 간격을 고르게 (기본 타이머는 5% 까지 어긋난다)
        self.night_t = 0.0          # 다음에 시계를 보고 밤 정도를 정할 때 (monotonic)

    def set_scale(self, scale):
        """펫 크기 배율에 맞춰 그림을 다시 만든다. 바뀌었으면 True (줄을 다시 세워야 한다)."""
        if scale == self.scale:
            return False
        self.scale = scale
        self.look = Look(scale)
        self.sim.drop_scale = self.look.W / self.look.rock_w   # 창이 넓으면 빗방울도 그만큼 많이
        self.slot_w = self.look.W
        self.size_hint_ = (self.look.W, self.look.H)
        self.shape = None
        if self.spot is not None and self.isVisible():
            self.place(*self.spot)
        return True

    def place(self, slot_right, floor, area):
        """칸 오른쪽 끝 slot_right, 바닥 floor 에 세우고 (화면 밖으로 나가지 않게) 계산과 그리기를 돌린다."""
        self.spot = (slot_right, floor, area)
        width, height = self.size_hint_
        left = max(area.left(), min(slot_right - width, area.right() + 1 - width))
        self.setGeometry(left, floor - height, width, height)
        self.actor = None           # 다음에는 창 전체를 그린다
        if not self.isVisible():
            self.show()
            self.shape = None       # 창을 다시 보이면 클릭 영역을 다시 건다
        if not self.timer.isActive():
            self.last = time.monotonic()
            self.night_t = self.last + NIGHT_EVERY
            self.sim.night = self.night_now()   # 첫 프레임부터 지금 시각의 밤 정도로 (숨어 있는 동안 해가 졌을 수 있다)
            self.timer.start(FRAME_MS)
        self.update_shape()
        self.update()

    def night_now(self):
        """지금 밤 정도: 밤낮을 껐으면 늘 낮 (0)."""
        enabled = getattr(self.manager, "climb_night_enabled", None)
        return clock_night() if enabled is None or enabled() else 0.0

    def refresh_night(self):
        """밤낮을 켜고 끈 뒤 바로 다시 그린다."""
        self.sim.night = self.night_now()
        self.night_t = time.monotonic() + NIGHT_EVERY
        self.update()

    def retire(self):
        """숨는다 (클라이밍 보기를 껐거나 펫·사용량이 없을 때). 계산도 멈추고, 받아 둔 동작은 버린다
        (몇 시간 뒤 다시 보일 때 그때 일을 하지 않게. 토큰은 PetManager 가 계속 쌓는다)."""
        self.timer.stop()
        if self.sim.wall_pending:   # 벽이 끝났는데 떨어지는 장면을 못 보였으면 바로 새 벽으로
            self.sim.new_wall()
        elif self.sim.moon_pending or (self.sim.moon is not None and not self.sim.grounded and not self.sim.falling):
            self.sim.place_on_moon()   # 달에 오르는 장면을 못 보였거나 오르는 도중이면 바로 달 위에
        self.sim.requests.clear()
        self.hide()

    def hit(self, point):
        """화면 좌표 point 가 클릭을 받는 곳 (바위 기둥·높이 상자·말풍선) 인지."""
        local = point - self.pos()
        return self.isVisible() and any(QRect(*r).contains(local) for r in (self.shape or ()))

    def dirty(self):
        """이번에 다시 그릴 곳. 바위 층이 바뀌었거나 창 전체에 걸치는 것 (비·눈, 번개, 낙석, 추락 중 로프, 말풍선)
        이 있으면 None (창 전체), 아니면 아바타·로프·포타레지 둘레 (지난번 자리까지) 와 높이 상자 (QRegion)."""
        sim, look = self.sim, self.look
        if (look.layer_key(sim) != look.layer_drawn or sim.drops or sim.flash > 0 or sim.rocks
                or sim.rope is not None or sim.banner is not None or sim.falling):
            self.actor = None
            return None
        actor = look.actor_region(sim)
        region = actor.united(self.actor) if self.actor is not None else QRegion(0, 0, look.W, look.H)
        self.actor = actor
        old = QRect(look.hud_rect)
        look.prepare_hud(sim)   # 상자가 커지면 커진 자리까지
        return region.united(old).united(look.hud_rect)

    def tick(self):
        now = time.monotonic()
        dt = min(0.5, now - (self.last or now))   # 멈춰 있다 돌아오면 (메뉴 등) 한꺼번에 넘기지 않는다
        self.last = now
        if now >= self.night_t:
            self.night_t = now + NIGHT_EVERY
            self.sim.night = self.night_now()
        steps = math.ceil(dt / STEP_MAX)   # 고르게 나눈다 (아주 짧은 조각이 남으면 몸이 튄다)
        for _ in range(steps):
            self.sim.update(dt / steps)
        self.update_shape()
        region = self.dirty()
        if region is None:
            self.update()
        else:
            self.update(region)

    def update_shape(self):
        """클릭은 바위 기둥과 높이 상자·기록 말풍선에서만 받는다 (양옆 배경은 아래 창으로)."""
        if self.look is None:
            return
        look = self.look
        rects = [QRect((look.W - look.rock_w) // 2, 0, look.rock_w, look.H), look.hud_rect]
        if not look.banner_rect.isNull():
            rects.append(look.banner_rect)
        shape = tuple((r.x(), r.y(), r.width(), r.height()) for r in rects if not r.isNull())
        if shape != self.shape:
            self.shape = shape
            self.manager.x11.set_input_shape(int(self.winId()), shape)

    def paintEvent(self, _event):
        if self.look is None:
            return
        painter = QPainter(self)
        self.look.paint(painter, self.sim)

    def mousePressEvent(self, event):
        """왼쪽 버튼으로 끌면 펫처럼 줄 전체를 옮긴다 (누르기만 하면 아무것도 하지 않는다)."""
        if event.button() == Qt.LeftButton:
            self.manager.drag_begin(CARD_ID, event.globalPos())

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton:
            self.manager.drag_move(event.globalPos())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.manager.drag_origin is not None:
            self.manager.drag_end()

    def enterEvent(self, _event):
        self.manager.hover_enter(CARD_ID)

    def leaveEvent(self, _event):
        self.manager.hover_leave(CARD_ID)

    def contextMenuEvent(self, event):
        self.manager.show_base_menu(event.globalPos())
