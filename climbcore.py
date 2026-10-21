"""끝없이 오르기 계산 (Qt 를 쓰지 않는다. 그리기는 climbview.py).

내 아바타가 흐르는 자연 암벽을 끝없이 오른다. Claude 가 쓴 토큰 (입력·출력·캐시) 을 API 가격으로 환산해 $1 어치마다
30m 를 오를 높이로 쌓고 (TokenMeter 가 Claude Code 기록 파일을 읽기만 해서 센다), 쌓인 높이가 많을수록 빠르게 오른다.
다 쓰면 일하는 펫이 있으면 기다리고 없으면 쉰다. 일하는 펫 수는 날씨, 5시간 사용량은 체력, 주간 사용량은 높이
(가속 눈금 week_height: 50% = 100km, 100% = 기둥 위 끝 달에 올라선다) 와 벽의 끝이고, 펫 상태에서 온 신호
(Climber.signal) 로 다이노·로프 추락·초크·잠·기상 같은 동작을 끼워 넣는다. 높이 기록 (아파트 10층 ~ 달) 을
넘으면 한 번씩 말풍선을 띄운다.

길이는 몸통 반지름을 1 로 둔 단위 (높이는 1 = 1m 로 센다), y 는 위가 +.
동작은 제너레이터로 쓰고 한 프레임마다 dt 를 보낸다 (yield from 으로 잇는다).
"""
import collections
import json
import math
import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---- 몸 치수 (몸통 반지름 = 1) ----------------------------------------------

SHOULDER = (0.60, 0.26)     # 어깨 (몸 중심에서, x 는 좌우 대칭)
HIP = (0.40, -0.60)
ARM = (0.84, 0.84)          # 위팔, 아래팔
LEG = (0.80, 0.86)          # 넓적다리, 정강이
HAND_UP = 1.30              # 편한 자세에서 손 높이 (몸 중심 위)
FOOT_DOWN = 1.62            # 편한 자세에서 발 높이 (몸 중심 아래)
WALL_HALF = 2.35            # 홀드를 둘 수 있는 벽 반폭
LIMBS = {"LH": (-1, "H"), "RH": (1, "H"), "LF": (-1, "F"), "RF": (1, "F")}
GRAVITY = 40.0

VIEW_H = 12.0               # 화면에 보이는 벽 높이
VIEW_TOP = VIEW_H * 0.58    # 카메라에서 화면 위 끝까지
FILL_AHEAD = VIEW_TOP + 2.5  # 홀드를 미리 깔아 두는 높이 (화면 위 끝보다 위)

SCENE_W = 1.8               # 창 폭 = 바위 기둥 폭의 몇 배 (양옆으로 배경과 날씨가 보인다)
HIGH = 9500                 # 이 높이 (m) 위로는 비·눈이 없고 흐리지도 않다 (구름바다 위)
SEOUL = (37.57, 126.98)     # 해 뜨고 지는 시각을 셀 곳 (위도, 경도)
KST = timezone(timedelta(hours=9))   # 서울 시각
DUSK = 0.5                  # 해가 지기 이만큼 (시간) 전부터 진 뒤 이만큼까지 어두워진다 (뜰 때는 거꾸로 밝아진다)
BANDS = [(0, "city"), (300, "hills"), (2000, "mountains"), (9500, "clouds"), (12000, "strato"), (100000, "space")]
MILESTONES = [  # (높이 m, 짧은 이름, 넘을 때 말풍선)
    (30, "아파트 10층", "아파트 10층(30m)보다 높아요!"),
    (56, "피사의 사탑", "피사의 사탑(56m)보다 높아요!"),
    (93, "자유의 여신상", "자유의 여신상(93m)보다 높아요!"),
    (249, "63빌딩", "63빌딩(249m)보다 높아요!"),
    (330, "에펠탑", "에펠탑(330m)보다 높아요!"),
    (555, "롯데월드타워", "롯데월드타워(555m)보다 높아요!"),
    (828, "부르즈 할리파", "부르즈 할리파(828m)보다 높아요!"),
    (836, "북한산", "북한산(836m)보다 높아요!"),
    (1708, "설악산", "설악산(1,708m)보다 높아요!"),
    (1947, "한라산", "한라산(1,947m)보다 높아요!"),
    (3776, "후지산", "후지산(3,776m)보다 높아요!"),
    (4806, "몽블랑", "몽블랑(4,806m)보다 높아요!"),
    (5895, "킬리만자로", "킬리만자로(5,895m)보다 높아요!"),
    (8849, "에베레스트", "에베레스트(8,849m)보다 높아요!"),
    (10000, "여객기 순항 고도", "여객기 순항 고도(약 10km)예요!"),
    (12000, "성층권", "성층권에 들어왔어요! (약 12km)"),
    (25000, "오존층", "오존층을 지나요! (약 25km)"),
    (100000, "카르만 선", "우주에 닿았어요! 카르만 선 100km"),
    (400000, "국제우주정거장", "국제우주정거장 높이예요! (약 400km)"),
    (2000000, "저궤도 끝", "저궤도를 벗어났어요! (약 2,000km)"),
    (12742000, "지구 지름", "지구 지름(12,742km)만큼 올라왔어요!"),
    (20200000, "GPS 위성", "GPS 위성 높이예요! (약 2만 km)"),
    (35786000, "정지궤도", "정지궤도에 닿았어요! (약 3만 6천 km)"),
    (192200000, "달까지 절반", "달까지 절반 왔어요! (약 19만 km)"),
    (384400000, "달", "달에 도착했어요! (384,400km)"),
]
CONFETTI = [(255, 196, 0), (255, 90, 90), (80, 200, 255), (120, 220, 120), (200, 120, 255)]

PACE_DUR = {1: 0.8, 2: 0.6, 3: 0.45}         # 오르는 빠르기별 동작 시간 배율 (3 = 전력 질주, 토큰이 많이 쌓였을 때)
PACE_PAUSE = {1: (0.05, 0.2), 2: (0.02, 0.08), 3: (0.0, 0.03)}   # 동작 사이 숨 고르기 (초). 보통은 몇 동작마다 둘러보며 쉰다
PACE_NAME = {1: "보통", 2: "빠름", 3: "전력 질주"}
PRICES = {   # API 가격 (달러 / 100만 토큰): 입력, 출력, 캐시 읽기, 캐시 쓰기 5분, 캐시 쓰기 1시간 (claude-api 모델 표, 2026-10)
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0, 8.0),
    "claude-opus-5": (5.0, 25.0, 0.50, 6.25, 10.0),
    "claude-opus-4-8": (5.0, 25.0, 0.50, 6.25, 10.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5, 4.0),
    "claude-sonnet-5": (2.0, 10.0, 0.20, 2.5, 4.0),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30, 3.75, 6.0),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125, 0.20),   # 프롬프트가 10만 토큰 넘으면 HAIKU_LONG
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25, 2.0),
    "claude-fable-5-1": (10.0, 50.0, 0.25, 12.5, 20.0),
    "claude-mythos-5-1": (10.0, 50.0, 0.25, 12.5, 20.0),
    "claude-fable-5": (10.0, 50.0, 1.0, 12.5, 20.0),
    "claude-mythos-5": (10.0, 50.0, 1.0, 12.5, 20.0),
}
PRICE_IDS = sorted(PRICES, key=len, reverse=True)   # 긴 이름부터 맞춰 본다 (claude-opus-5-5 를 claude-opus-5 로 보지 않게)
PRICE_FAMILY = {"opus": "claude-opus-5-5", "sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5",
                "fable": "claude-fable-5-1", "mythos": "claude-mythos-5-1"}   # 표에 없는 모델은 같은 계열 최신 가격 (아니면 opus)
HAIKU_LONG = (0.50, 2.50, 0.05, 0.625, 1.0)
FAST_PRICE = 2.0              # 빠른 모드 (speed: fast) 는 가격이 두 배
TOKEN_USD = PRICES["claude-opus-5-5"][1] / 1e6           # 출력 토큰 하나 값: 쓴 금액을 이 값으로 나눠 '토큰 (출력 환산)' 으로 센다
M_PER_USD = 30.0                              # API 가격 $1 어치 = 30m
M_PER_TOKEN = M_PER_USD * TOKEN_USD           # 출력 환산 토큰 1,000개 = 0.6m
PACE_BANK = (8.0, 30.0)                       # 쌓인 높이 (m) 가 이만큼 넘으면 빠름, 전력 질주
DYNO_MIX = {2: 0.06, 3: 0.18}                 # 빠름·전력 질주에서 손발 동작 대신 다이노를 할 비율
NAP_AFTER = 180.0                             # 쉬는 자세 (기다림·쉼) 로 이만큼 (초) 있으면 포타레지를 펴고 낮잠을 잔다
LEFTOVER_WAIT = 20.0                          # 1m 가 안 되게 남은 오를 높이는 토큰이 이만큼 (초) 안 들어오면 마저 오른다
GROUND = -2.4                 # 땅 높이 (m). 새 벽 첫 자세가 0m 이고 그 발 아래
GROUND_OK = ("get_up", "moon_leave")   # 바닥 (달) 에 앉아 있는 동안 받는 동작
FALL_SPEED = 8.0              # 벽의 끝: 떨어지는 빠르기 (m/s, 처음부터 끝까지 같다. 높이 숫자는 따로 가속 눈금을 따라)
FALL_PCT = 12.0               # 벽의 끝에서 높이 숫자가 가속 눈금을 따라 내려가는 빠르기 (%/s)
FALL_RAMP = 4.0               # 땅 가까이 이만큼 (%) 에서 느려져 마지막 12m 의 떨어지는 빠르기와 이어진다
MOON = 384_400_000            # 달까지 (m) = 주간 사용량 100%
SCALE_K = math.log(3843) / 50                 # 가속 눈금: 높이 = SCALE_A x (e^(SCALE_K x %) - 1). 50% = 100km, 100% = 달
SCALE_A = MOON / math.expm1(100 * SCALE_K)    # 약 26m
WEEK_RATE = 13.4 / TOKEN_USD                  # 주간 1% 에 드는 토큰 (출력 환산) 을 모를 때 (2026-10 기록에서 1% 에 약 $13.4)
PCT_STEP = 0.98                               # 주간 % 사이를 토큰으로 채울 때 다음 % 의 이만큼 앞까지
CARVE_GAP = 1.8                               # 지난 주 기록 자국이 이만큼 (m) 안에 있으면 새 자국 글자는 다른 쪽에 (겹치지 않게)
PCT_EASE = (1.5, 0.3)                         # 보이는 주간 % 가 따라붙는 빠르기: 남은 만큼의 비율 (1/s), 가장 느릴 때 (%/s)
LAST_ROOM = 0.3                               # 남은 오를 높이가 이보다 적은데 가까운 홀드가 없으면 멈추고 다음 토큰과 합쳐 오른다
COOLDOWN = {"dyno": 8.0, "chalk": 15.0}      # 잦은 신호는 이 간격 (초) 안이면 버린다 (일 끝냄 → 다이노, 사용량 → 초크)
SHAKE_EVERY = (40.0, 80.0)                    # 체력 60% 이하면 오르다가 이 간격으로 팔을 턴다
WORKING = ("running", "background")          # 일하는 펫으로 치는 상태 (날씨)
AWAKE = ("wake", "refill", "wall_end", "moon_top", "moon_leave")   # 자는 동안에도 받는 동작
FREE_FOOT = 0.75                              # 손을 뻗을 때 한 발을 떼어 (삼지점) 플래그·벽 딛기로 균형을 잡을 비율
FREE_HOLD = 0.3                               # 뗀 발은 손을 잡고 적어도 이만큼 (초) 더 균형을 잡은 뒤 홀드로 옮긴다
LEAN_SIDE = 0.15                              # 몸 가운데에서 이만큼 넘게 떨어져야 그쪽에 있다고 친다 (두 손이 한쪽, 두 발이 반대쪽이면 쏠림)
FOOT_SPAN = 3.6                               # 편한 자세에서 아래쪽 손과 발 사이 높이 (팔 펴고 다리로 민다, 무릎 약 100°)
FOOT_HIGH = 2.6                               # 가끔 발을 높이 올릴 때
FOOT_CEIL = 0.55                              # 발은 매달린 엉덩이보다 적어도 이만큼 아래에 둔다
FOOT_WIDTH = 0.35                             # 발을 몸 가운데에서 옆으로 두는 거리 (엉덩이 너비 가까이)
CRUX_EVERY = (40.0, 80.0)     # 크럭스 간격 (m)
CRUX_GAP = (1.5, 2.5)         # 크럭스: 이 높이만큼 홀드를 비우고 끝에 큰 홀드를 몸 반대쪽 끝에 둔다 (다이노로 넘는다)
CRUX_MISS = 0.2               # 크럭스에서 손이 미끄러져 다시 뛸 확률 (추락 이벤트를 켰을 때)
CRUX_BOLT = 1.4               # 크럭스 볼트: 구간 시작보다 이만큼 아래 (뛰기 전 손 높이. 놓치면 4~7m 떨어져 로프에 걸린다)
CRUX_CLIP = 1.06              # 크럭스 볼트는 팔을 쭉 뻗어 이만큼 (팔 길이의 배) 까지 닿으면 건다 (홀드를 잡은 손 높이라 조금 넘는다)
CRUX_RUNOUT = 5.0             # 놓쳤을 때 마지막 퀵드로가 하네스보다 이만큼 넘게 아래면 (크럭스 볼트를 지나쳐 멀리 떨어질 때) 떨어지지
                              # 않고 잡던 홀드로 미끄러진다 (떨어진 아래 홀드는 지워졌을 수 있다)
BREAK_CHANCE = 1 / 1000                       # 오르며 손으로 새 홀드를 잡을 때 그 홀드가 부서질 확률
DROP_KNEE = 0.75                               # 옆으로 멀리 뻗는데 같은 쪽 발이 높으면 드롭니를 할 비율
SWAY = 0.35                                   # 손을 떼면 몸이 남은 손 쪽으로 기우는 정도 (플래그 중이면 0.35 배)
FACE_HOLD = 2.5                               # 길이 한쪽으로 이만큼 (초) 넘게 이어져야 몸을 돌린다
WEATHER = {0: 0.0, 1: 0.3, 2: 0.65, 3: 1.0}  # 일하는 펫 수 → 날씨 세기 (0 맑음, 0.3 흐림, 비·눈, 폭풍우·눈보라)


class V:
    __slots__ = ("x", "y")

    def __init__(self, x=0.0, y=0.0):
        self.x, self.y = x, y

    def __add__(self, o):
        return V(self.x + o.x, self.y + o.y)

    def __sub__(self, o):
        return V(self.x - o.x, self.y - o.y)

    def __mul__(self, k):
        return V(self.x * k, self.y * k)

    def len(self):
        return math.hypot(self.x, self.y)

    def rot(self, a):
        c, s = math.cos(a), math.sin(a)
        return V(self.x * c - self.y * s, self.x * s + self.y * c)

    def lerp(self, o, t):
        return V(self.x + (o.x - self.x) * t, self.y + (o.y - self.y) * t)


def ease_out(k):
    """빠르게 출발해 천천히 닿는다 (손 뻗기: 데드포인트)."""
    return 1 - (1 - k) ** 3


def fmt_alt(m):
    """높이 글자: 10km 아래는 m, 위는 km."""
    return f"{m:,.0f}m" if m < 10000 else f"{m / 1000:,.1f}km".replace(".0km", "km")


def week_height(pct):
    """주간 사용량 (%) → 가속 눈금 높이 (m): 10% 110m, 35% 에베레스트, 50% 100km, 80% 1.4만 km, 100% 달.
    같은 1% 라도 위로 갈수록 더 많이 올라서 도시~우주 경치가 한 주에 고르게 퍼진다."""
    return SCALE_A * math.expm1(SCALE_K * max(0.0, min(100.0, pct)))


def height_pct(m):
    """week_height 의 거꾸로: 높이 (m) → 주간 사용량 (%)."""
    return math.log1p(max(0.0, m) / SCALE_A) / SCALE_K


def smooth(k):
    """천천히 출발해 천천히 멈춘다 (smootherstep)."""
    return k * k * k * (k * (6 * k - 15) + 10)


def sun_times(day, tz=9.0, lat=SEOUL[0], lon=SEOUL[1]):
    """그날 해 뜨는·지는 시각 (그날 0시부터 시간, UTC+tz 시각). NOAA 간이식이라 몇 분 안쪽으로 맞는다."""
    g = 2 * math.pi / 365 * (day.timetuple().tm_yday - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    phi = math.radians(lat)
    c = math.cos(math.radians(90.833)) / (math.cos(phi) * math.cos(decl)) - math.tan(phi) * math.tan(decl)
    half = math.degrees(math.acos(max(-1.0, min(1.0, c)))) * 4   # 해가 떠 있는 시간의 절반 (분)
    noon = 720 - 4 * lon - eqtime   # 남중 (UTC, 분)
    return (noon - half) / 60 + tz, (noon + half) / 60 + tz


def night_level(when=None):
    """밤 정도 (0 낮 ~ 1 밤). 서울 기준으로 해가 지기 DUSK 시간 전부터 진 뒤 DUSK 시간까지 어두워지고, 뜰 때는
    거꾸로 밝아진다. when 은 datetime (한국 시각으로 바꿔 본다. 시간대가 없으면 한국 시각), 없으면 지금."""
    if when is None:
        when = datetime.now(KST)
    elif when.tzinfo is not None:
        when = when.astimezone(KST)
    rise, sset = sun_times(when.date())
    h = when.hour + when.minute / 60 + when.second / 3600

    def ramp(start):
        return smooth(max(0.0, min(1.0, (h - start) / (2 * DUSK))))
    return 1.0 - ramp(rise - DUSK) * (1.0 - ramp(sset - DUSK))


def edge_x(y, side):
    """바위 기둥 옆 가장자리 (울퉁불퉁)."""
    return side * (WALL_HALF + 0.5 + 0.22 * math.sin(y * 0.9 + side * 2.1) + 0.12 * math.sin(y * 2.3 + side * 0.7))


def two_bone(root, end, a, b, side, kind, flip=False):
    """팔꿈치·무릎 자리와 실제 손끝 (닿지 않으면 그쪽으로 쭉 뻗는다). 팔꿈치는 바깥·아래, 무릎은 바깥·위로.
    flip 이면 반대로 굽힌다 (드롭니: 무릎이 안·아래로)."""
    d = end - root
    dist = d.len() or 1e-6
    reach = max(abs(a - b) + 1e-3, min(a + b - 1e-3, dist))
    tip = root + d * (reach / dist)
    base = math.atan2(d.y, d.x)
    alpha = math.acos(max(-1.0, min(1.0, (a * a + reach * reach - b * b) / (2 * a * reach))))
    up = -0.35 if kind == "H" else 0.35
    best = None
    for ang in (base + alpha, base - alpha):
        e = root + V(math.cos(ang), math.sin(ang)) * a
        score = side * (e.x - root.x) + up * (e.y - root.y)
        if best is None or (score < best[0] if flip else score > best[0]):
            best = (score, e)
    return best[1], tip


class Hold:
    def __init__(self, p, r, rng, big=False):
        self.p, self.r, self.big = p, r, big
        self.crux = False           # 크럭스 끝 홀드 (crux_from: 크럭스 아래 끝 높이, crux_tries: 뛴 횟수 (놓쳤으면 2))
        self.crux_from = 0.0
        self.crux_tries = 1
        self.foot = False           # 발만 딛는 작은 턱 (손으로 잡지 않는다)
        self.chalk = 0.0
        self.snow = 0.0
        self.made_t = self.made_cam = 0.0
        n = 7
        self.shape = []
        for i in range(n):
            a = 2 * math.pi * (i + rng.uniform(-0.25, 0.25)) / n
            k = rng.uniform(0.78, 1.12)
            y = math.sin(a) * 0.62 * (0.75 if math.sin(a) > 0 else 1.0)  # 위는 납작한 턱
            self.shape.append((math.cos(a) * k, y * k))

    def grip(self, kind, side=0):
        if kind == "H":
            return self.p + V(side * self.r * 0.45, self.r * 0.25)
        return self.p + V(side * self.r * 0.45, self.r * 0.32)


class Route:
    """벽에 홀드를 미리 깔아 둔다. 길(홀드가 몰린 줄기)은 높이 1.5~4m 마다 벽 폭 안의 새 지점으로 굽이치고,
    가끔 짧은 높이 안에서 반대편으로 길게 옆으로 간다 (트래버스)."""
    BAND = 0.5

    def __init__(self, rng, xrng=None):
        self.rng = rng
        self.xrng = xrng or random.Random(0)   # 크럭스 (홀드 깔기와 따로)
        self.top = -5.0
        self.a = (self.top, 0.0)            # 지금 구간의 시작 (높이, x)
        self.b = (self.top + 3.0, 0.0)      # 지금 구간의 끝
        self.centers = [(self.top, 0.0)]
        self.next_crux = 0.0 + self.xrng.uniform(*CRUX_EVERY)   # 다음 크럭스 시작 높이
        self.crux = None            # 깔고 있는 크럭스 (시작 높이, 끝 높이, 끝 홀드 쪽 -1/1)
        self.crux_bolted = False    # 그 크럭스 아래 볼트를 두었다
        self.jugs = []              # 깐 크럭스 끝 홀드 (검사용)
        self.next_bolt = -1.5       # 다음 볼트 높이 (약 3m 마다)

    def center(self, y):
        cs = self.centers
        if y <= cs[0][0]:
            return cs[0][1]
        if y >= cs[-1][0]:
            return cs[-1][1]
        i = max(0, min(len(cs) - 2, int((y - cs[0][0]) / self.BAND)))
        (y0, x0), (y1, x1) = cs[i], cs[i + 1]
        return x0 + (x1 - x0) * (y - y0) / (y1 - y0)

    def next_point(self):
        rng = self.rng
        limit = WALL_HALF - 0.55
        y, x = self.b
        # 아바타가 옆으로 따라갈 수 있게 옆으로 1m 가는 데 높이를 1m 넘게 쓴다
        if rng.random() < 0.25:   # 트래버스: 반대편 끝 쪽으로, 따라갈 수 있는 가장 가파른 기울기로
            nx = -math.copysign(rng.uniform(limit * 0.6, limit), x or rng.choice((-1, 1)))
            return y + abs(nx - x) * 1.05, nx
        while True:   # 벽 양쪽으로 자주 간다 (가운데에만 머물지 않는다)
            nx = rng.choice((-1, 1)) * limit * (rng.uniform(0.5, 1.0) if rng.random() < 0.8 else rng.uniform(0, 0.5))
            if abs(nx - x) > 0.8:
                return y + max(rng.uniform(1.5, 4.0), abs(nx - x) * 1.2), nx

    def fill(self, sim, upto):
        rng = self.rng
        while self.top < upto:
            y0 = self.top
            self.top += self.BAND
            if self.crux is None and self.top + 4.0 >= self.next_crux:   # 크럭스 4m 앞: 길을 끝 홀드 반대쪽으로 모은다
                cx = self.centers[-1][1]
                side = (1 if cx < 0 else -1) if abs(cx) > 0.3 else self.xrng.choice((-1, 1))
                start = y0 + self.BAND * math.ceil((max(self.next_crux, y0 + 3.0) - y0) / self.BAND)   # 홀드 띠 경계에서 비운다
                end = start + self.xrng.uniform(*CRUX_GAP)
                self.crux, self.crux_bolted = (start, end, side), False
                self.a, self.b = (y0, cx), (start, -side * 1.3)
                for x, y in ((1.55, end - 2.5), (1.0, end - 3.1), (0.5, (start + end) / 2)):   # 발만 딛는 작은 턱:
                    chip = sim.place_hold(V(side * x, y))   # 끝 홀드를 잡은 뒤 디딜 곳 둘, 구간을 건너 오를 때 딛을 곳 하나
                    if chip is not None:
                        chip.foot, chip.r = True, 0.16
                self.bolt_crux(sim)
            if self.crux is not None and y0 >= self.crux[0]:
                start, end, side = self.crux
                if y0 < end:   # 크럭스 구간은 비운다 (길 가운데는 끝 홀드 쪽으로 옮겨 간다)
                    self.centers.append((self.top, -side * 1.3 + side * 2.9 * smooth(min(1.0, (self.top - start) / (end - start)))))
                    continue
                jug = sim.place_hold(V(side * (WALL_HALF - 0.55), end), big=True)
                if jug is None:
                    jug = Hold(V(side * (WALL_HALF - 0.55), end), 0.34, rng, True)
                    sim.holds.append(jug)
                jug.crux, jug.crux_from = True, start
                jug.r, jug.chalk = 0.38, 0.5   # 크고, 초크 자국이 묻은 홀드 (크럭스 끝이라는 걸 알아보게)
                self.jugs.append(jug)
                self.crux = None
                self.next_crux = end + self.xrng.uniform(*CRUX_EVERY)
                self.b = (end, side * 0.9)   # 끝 홀드 쪽에서 길을 잇는다 (벽 끝 구석에 몰리지 않게 조금 안쪽)
                self.a, self.b = self.b, self.next_point()
            while self.top > self.b[0]:
                self.a, self.b = self.b, self.next_point()
            (ya, xa), (yb, xb) = self.a, self.b
            cx = xa + (xb - xa) * smooth((self.top - ya) / (yb - ya))
            self.centers.append((self.top, cx))
            for _ in range(rng.choice((2, 3, 3))):
                sim.place_hold(V(cx + rng.uniform(-1.15, 1.15), y0 + rng.uniform(0, self.BAND)), big=rng.random() < 0.08)
            if rng.random() < 0.2:   # 길 밖의 바위 턱
                x = rng.uniform(-WALL_HALF + 0.3, WALL_HALF - 0.3)
                if self.crux is not None:   # 크럭스로 다가가는 동안은 시작 쪽에만 (끝 홀드 쪽으로 건너가는 길이 없게)
                    x = -self.crux[2] * abs(x)
                sim.place_hold(V(x, y0 + rng.uniform(0, self.BAND)))
            if self.top >= self.next_bolt:   # 볼트는 길 가운데 근처에 약 3m 마다
                sim.bolts.append(Bolt(V(cx + sim.wrng.uniform(-0.4, 0.4), self.next_bolt)))
                self.next_bolt += sim.wrng.uniform(2.6, 3.4)
                self.bolt_crux(sim)

    def bolt_crux(self, sim):
        """다음 볼트가 크럭스 근처에 걸리면 크럭스 아래 (시작 쪽, 뛰기 전에 로프를 걸 손 높이) 에 두고, 크럭스 구간은
        건너뛰어 끝 홀드 위에 다음 볼트를 둔다 (구간 안이나 먼 끝 홀드 쪽 볼트는 손이 안 닿아 건너뛰게 된다)."""
        if self.crux is None or self.crux_bolted or self.next_bolt < self.crux[0] - 5.2:
            return
        start, end, side = self.crux
        if self.next_bolt < start - CRUX_BOLT - 1.2:   # 그 아래 볼트도 둔다 (크럭스 앞에서 기다리다 떨어져도 멀리 안 가게)
            sim.bolts.append(Bolt(V(-side * 1.1 + sim.wrng.uniform(-0.4, 0.4), self.next_bolt)))
        sim.bolts.append(Bolt(V(-side * 1.1 + sim.wrng.uniform(-0.2, 0.2), start - CRUX_BOLT)))
        self.next_bolt = end + sim.wrng.uniform(0.8, 1.4)
        self.crux_bolted = True


class Limb:
    def __init__(self, name):
        self.name = name
        self.side, self.kind = LIMBS[name]
        self.pos = V()
        self.vel = V()
        self.mode = "grip"          # grip (홀드), move (옮기는 중), follow (몸에 붙은 자리), free (매달려 흔들림)
        self.hold = None
        self.recent = collections.deque(maxlen=3)   # 최근에 잡았던 홀드 (제자리에서 왔다 갔다 하지 않게)
        self.goal = None            # 옮기는 중인 홀드 (몸이 미리 그쪽으로 옮겨 간다)
        self.target = None          # 옮기는 중인 홀드를 잡을 자리 (동작 후반부터 몸을 당긴다)
        self.progress = 0.0
        self.follow = None
        self.drop = False           # 드롭니 (무릎을 안·아래로 굽혀 몸을 튼다), 손을 뻗어 잡을 때까지
        self.kept = 0               # 뗀 발을 그대로 둔 채 손을 뻗은 횟수
        self.free_style = None      # 뗀 발 자세 (flag, backflag, smear, 빈 쪽 벽 딛기 balance)
        self.spot_hold = None       # 빈 쪽에 발을 올려 둔 홀드 (다음 차례에 딛는다)
        self.tucked = False         # 침낭 안에 넣었다 (안 그린다)
        self.fist = False
        self.wiggle = 0.0           # 잡은 뒤 손가락을 고쳐 쥐는 남은 시간 (그림만)

    @property
    def reach(self):
        return sum(ARM if self.kind == "H" else LEG)

    def root(self, body, tilt):
        base = SHOULDER if self.kind == "H" else HIP
        return body + V(base[0] * self.side, base[1]).rot(tilt)


class Particle:
    __slots__ = ("p", "v", "age", "life", "size", "kind", "color")

    def __init__(self, p, v, life, size, kind="chalk", color=None):
        self.p, self.v, self.age, self.life, self.size, self.kind, self.color = p, v, 0.0, life, size, kind, color


class Float:
    """떠오르는 글자 (+2m, z)."""

    def __init__(self, p, text, style="gold"):
        self.p, self.text, self.style, self.age = p, text, style, 0.0
        self.life = 2.4 if style == "z" else 1.6


class Bolt:
    """벽에 박힌 볼트와 퀵드로. 지나가며 로프를 건다 (clipped)."""

    def __init__(self, p):
        self.p = p
        self.clipped = False
        self.skipped = False        # 손이 안 닿거나 너무 지나쳐서 걸지 않고 넘어갔다

    def rope_point(self):
        return self.p + V(0, -0.32)   # 퀵드로 아래 카라비너


class Ledge:
    """포타레지 (벽에 매다는 침대). anchor 에 걸고, bed 는 침대 바닥 가운데."""

    def __init__(self, anchor):
        self.anchor = anchor
        self.bed = anchor + V(0, -2.5)
        self.carry = None           # 꾸러미를 든 손 이름
        self.hung = False           # 앵커에 걸렸다
        self.open = 0.0             # 펼친 정도
        self.bag = 0.0              # 침낭을 끌어올린 정도
        self.stow = None            # 접어 넣는 중 (0~1)
        self.home = None            # 자기 전 자리 (몸, {손발: 홀드})


def usage_parts(usage):
    """응답 사용량의 (입력, 출력, 캐시 읽기, 캐시 쓰기 5분, 캐시 쓰기 1시간) 토큰. 시도가 여럿 (iterations) 이면 모두 더하고
    (위 값은 마지막 시도만이다), 정수가 아닌 값은 0. 캐시 쓰기는 5분/1시간으로 나눈 값을 합계에 맞춘다 (나눈 값이 없으면 5분)."""
    def count(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0

    def one(u):
        total = count(u.get("cache_creation_input_tokens"))
        split = u.get("cache_creation")
        if isinstance(split, dict):
            w1h, w5m = count(split.get("ephemeral_1h_input_tokens")), count(split.get("ephemeral_5m_input_tokens"))
            if "cache_creation_input_tokens" in u:
                w1h = min(w1h, total)
                w5m = total - w1h
        else:
            w5m, w1h = total, 0
        return (count(u.get("input_tokens")), count(u.get("output_tokens")), count(u.get("cache_read_input_tokens")), w5m, w1h)

    tries = usage.get("iterations")
    tries = [u for u in tries if isinstance(u, dict)] if isinstance(tries, list) else []
    if len(tries) > 1:
        return tuple(map(sum, zip(*(one(u) for u in tries))))
    return one(usage)


def model_prices(model, prompt):
    """모델 이름과 프롬프트 길이 (입력 + 캐시) 의 가격 (PRICES 한 줄)."""
    name = model if isinstance(model, str) else ""
    key = next((k for k in PRICE_IDS if name.startswith(k)), None)
    if key is None:
        key = next((v for family, v in PRICE_FAMILY.items() if family in name), "claude-opus-5-5")
    if key == "claude-haiku-5-5" and prompt > 100_000:
        return HAIKU_LONG
    return PRICES[key]


def usage_tokens(model, usage):
    """응답 하나를 출력 환산 토큰으로: 입력·출력 (생각 포함)·캐시 읽기·캐시 쓰기 (5분/1시간) 토큰에 모델별 가격 (PRICES) 을
    opus 5.5 출력 가격으로 나눈 비율을 곱해 더한다 (opus 5.5 출력 토큰은 그대로). 빠른 모드는 두 배.
    TOKEN_USD 를 곱하면 API 가격 (달러)."""
    tokens = usage_parts(usage)
    price = model_prices(model, tokens[0] + sum(tokens[2:]))
    base = PRICES["claude-opus-5-5"][1] / (FAST_PRICE if usage.get("speed") == "fast" else 1.0)
    return sum(t * p / base for t, p in zip(tokens, price))


def usage_cost(model, usage):
    """응답 하나의 API 가격 (달러)."""
    return usage_tokens(model, usage) * TOKEN_USD


def line_usage(line):
    """Claude Code 기록 파일의 한 줄 (bytes) → (응답 id, 출력 환산 토큰, 적힌 때 epoch 초 또는 None). 응답 줄이 아니면 None."""
    if b'"output_tokens"' not in line:
        return None
    try:
        entry = json.loads(line)
    except (ValueError, RecursionError):
        return None
    message = entry.get("message") if isinstance(entry, dict) else None
    if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
        return None
    usage = message["usage"]
    out = usage.get("output_tokens")
    key = message.get("id") or entry.get("requestId")
    if not isinstance(out, int) or isinstance(out, bool) or not isinstance(key, str) or not key:
        return None
    stamp, when = entry.get("timestamp"), None
    if isinstance(stamp, str):
        try:
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return key, usage_tokens(message.get("model"), usage), when


class TokenMeter:
    """Claude Code 기록 파일에서 쓴 토큰을 API 가격으로 환산해 센다 (출력 토큰 값으로 나눈 '출력 환산 토큰').
    읽기만 한다.

    ~/.claude/projects/<프로젝트>/<세션>.jsonl 과 서브에이전트 기록 <세션>/subagents/*.jsonl 을 본다.
    응답 하나가 여러 줄에 같은 값으로 남고, 서브에이전트 기록에는 응답 도중의 덜 센 값도 남아서
    응답 id 마다 가장 큰 값을 기억하고 늘어난 만큼만 더한다 (입력·캐시는 응답마다 같고 출력이 늘어난다).

    처음 쓰면 (saved 가 없으면) 지금 있는 기록은 세지 않고 그 뒤에 쓰인 줄만 센다. state() 를 저장해 두었다가
    saved 로 주면 파일마다 읽던 자리부터 이어 읽어서, 펫이 꺼져 있던 동안 쓰인 토큰도 빠짐없이 한 번만 센다
    (그사이 새로 생긴 파일은 처음부터 읽되, 저장한 때보다 옛 줄은 다른 파일에서 옮겨 적힌 것으로 보고 뺀다).
    기록 폴더는 root, 없으면 CLAUDE_PET_PROJECTS 환경 변수 (시험 모드), 없으면 ~/.claude/projects."""

    SEEN_KEEP = 5000            # 기억하는 응답 id 수
    SEEN_SAVE = 300             # 저장하는 응답 id 수 (끄기 직전 응답이 이어서 더 늘어날 수 있다)
    POLL_BYTES = 4 << 20        # 한 번에 읽는 양 (오래 꺼져 있다 켜지면 밀린 기록을 여러 번에 나눠 읽는다)

    def __init__(self, root=None, saved=None):
        root = root or os.environ.get("CLAUDE_PET_PROJECTS")
        self.root = Path(root) if root else Path.home() / ".claude" / "projects"
        self.offsets = {}           # 파일 → 읽은 바이트 수
        self.rest = {}              # 파일 → 아직 줄바꿈이 오지 않은 끝부분
        self.seen = collections.OrderedDict()   # 응답 id → 지금까지 본 출력 환산 토큰
        self.total = 0
        self.spent = collections.Counter()   # 시간 칸 시작 epoch 초 → 그 시간에 적힌 응답의 출력 환산 토큰 (take_spent)
        saved = saved if isinstance(saved, dict) else {}
        offsets, seen = saved.get("offsets"), saved.get("seen")
        since = saved.get("ts")
        if isinstance(offsets, dict) and isinstance(since, (int, float)):
            self.start = since
            self.offsets = {Path(p): o for p, o in offsets.items() if isinstance(p, str) and isinstance(o, int) and o >= 0}
            if isinstance(seen, dict):
                self.seen.update((k, v) for k, v in seen.items()   # 옛 기록의 출력 토큰도 출력만 환산한 값과 같다
                                 if isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)
                                 and math.isfinite(v))
        else:
            self.start = time.time()
            for path in self.files():
                try:
                    self.offsets[path] = path.stat().st_size   # 지난 기록은 세지 않는다
                except OSError:
                    pass
        self.polled = self.start    # 마지막으로 읽은 때

    def files(self):
        return list(self.root.glob("*/*.jsonl")) + list(self.root.glob("*/*/subagents/*.jsonl"))

    def take_spent(self):
        """지난번 뒤로 센 토큰을 응답이 적힌 시간 칸마다 {시간 칸 시작 epoch 초: 출력 환산 토큰} 으로 넘기고 비운다 (등반 일지)."""
        spent, self.spent = self.spent, collections.Counter()
        return spent

    def state(self):
        """저장할 것: 파일마다 읽은 자리 (다 쓰이지 않은 끝줄은 다시 읽게 뺀다), 마지막으로 읽은 때, 최근 응답 id."""
        offsets = {str(p): o - len(self.rest.get(p, b"")) for p, o in self.offsets.items()}
        seen = {k: round(v, 3) for k, v in list(self.seen.items())[-self.SEEN_SAVE:]}
        return {"ts": self.polled, "offsets": offsets, "seen": seen}

    def poll(self):
        """새로 쓰인 줄을 읽고 늘어난 출력 환산 토큰 수를 돌려준다."""
        added = 0
        now = time.time()
        present = set()
        budget = self.POLL_BYTES
        for path in self.files():
            try:
                size = path.stat().st_size
            except OSError:
                continue
            present.add(path)
            done = self.offsets.get(path, 0)   # 처음 보는 파일은 그 뒤에 생긴 것이라 처음부터
            if size < done:                    # 파일이 새로 바뀌었다
                done = 0
                self.rest.pop(path, None)
            if size == done or budget <= 0:
                continue
            try:
                with open(path, "rb") as f:
                    f.seek(done)
                    data = f.read(min(size - done, budget))
            except OSError:
                continue
            budget -= len(data)
            self.offsets[path] = done + len(data)
            lines = (self.rest.pop(path, b"") + data).split(b"\n")
            if lines[-1]:
                self.rest[path] = lines[-1]    # 아직 다 쓰이지 않은 줄
            for line in lines[:-1]:
                added += self.read_line(line)
        for path in [p for p in self.offsets if p not in present]:   # 지워진 기록
            del self.offsets[path]
            self.rest.pop(path, None)
        self.total += added
        if budget > 0:   # 밀린 기록을 다 읽었을 때만 (다 못 읽고 저장했다가 다시 켜면 그 줄들이 옛 줄로 빠지지 않게)
            self.polled = now
        return added

    def read_line(self, line):
        got = line_usage(line)
        if got is None:
            return 0
        key, tokens, when = got
        if when is not None and when < self.start - 60:   # 다른 파일에서 옮겨 적힌 옛 줄은 세지 않는다
            return 0
        old = self.seen.get(key, 0)
        if tokens <= old + 1e-3:   # 저장할 때 반올림한 값 (소수 셋째 자리) 때문에 다시 더하지 않게
            return 0
        self.seen[key] = tokens
        self.seen.move_to_end(key)
        while len(self.seen) > self.SEEN_KEEP:
            self.seen.popitem(last=False)
        when = time.time() if when is None else when
        self.spent[int(when // 3600) * 3600] += tokens - old
        return tokens - old


class Climber:
    """오르는 아바타 한 마리. update(dt) 로 움직이고, request() 로 동작을 끼워 넣는다."""

    def __init__(self, seed=7):
        self.rng = random.Random(seed)
        self.wrng = random.Random(seed + 1)   # 날씨·눈 깜빡임 (등반 결정과 따로)
        self.t = 0.0
        self.cam = 0.45
        self.mode = "spring"        # spring (손발로 버팀), hang (손만, 중력), script (동작이 직접 옮김)
        self.vel = V()
        self.tilt = self.tilt_vel = 0.0
        self.squash, self.squash_vel = 1.0, 0.0
        self.face, self.face_now = 1, 1.0   # 1 = 왼쪽을 봄 (그림 그대로), -1 = 오른쪽
        self.crouch = 0.0
        self.work = 1               # 일하는 펫 수 (0 이면 쉰다)
        self.hp = self.hp_shown = 100
        self.sleeping = False
        self.nap = False            # 자는 게 낮잠 (오래 쉬어서) 이다. 토큰이 모이면 깬다 (사용량 잠은 리셋이 와야 깬다)
        self.idle_since = None      # 쉬는 자세로 있기 시작한 때 (오르면 지운다)
        self.ledge = None
        self.lids, self.blink_t, self.blink_age = 0.0, 2.0, None
        self.z_t = 0.0
        self.weather_kind = "rain"
        self.weather = WEATHER[1]
        self.drops = []
        self.drops_kind = self.weather_kind
        self.precipitating = False
        self.wind = 0.0
        self.flash, self.flash_t, self.flash2 = 0.0, 4.0, None
        self.holds, self.particles, self.texts = [], [], []
        self.requests = []
        self.sweat_t = 0.0
        self.alt0 = 0.0             # 오른 바위 높이 (m) = alt0 + 몸 높이 (땅 자리, 주간 % 를 모를 때의 높이)
        self.week_pct = None        # 계정 주간 사용량 (%, set_week). 알면 높이가 가속 눈금 (scaled)
        self.week_per = WEEK_RATE   # 주간 1% 에 드는 토큰 (출력 환산)
        self.week_tokens = 0.0      # 계정 주간 % 를 마지막으로 받은 뒤 (오르거나 처음) 받은 토큰 (% 사이를 채운다)
        self.pct_shown = 0.0        # 보이는 주간 % (가속 눈금 높이는 이것으로)
        self.peak = 0.0             # 가장 높이 올라간 높이 (기록은 이걸로 센다)
        self.passed = 0             # 넘은 기록 수 (landmarks 앞에서부터)
        self.records = []           # 지난 주들에 오른 높이 (set_records): (높이, 짧은 이름, 말풍선, (새길 이름, 높이 글자))
        self.landmarks = MILESTONES  # 넘을 기록: MILESTONES 에 records 를 높이 순으로 섞은 것
        self.carved = []            # 넘으며 바위에 새긴 지난 주 기록 (벽 y, 글자 쪽 -1/1, 이름, 높이 글자). 새 벽이면 지운다
        self.banner = None          # [말풍선, 나이]
        self.banners = []           # 넘은 기록 높이 (검사용)
        self.drop_scale = 1.0       # 창이 넓으면 빗방울도 그만큼 많이 (Look 이 정한다)
        self.events = True          # 추락 이벤트 (발·손 미끄러짐, 홀드 부서짐, 낙석, 로프 추락) 켜기
        self.bolts = []
        self.rope = None            # 추락 중 로프 (걸린 점, 길이)
        self.rope_hand = None       # 로프를 걸려고 쥔 손 이름
        self.rocks = []             # 떨어지는 돌 [자리, 속도, 각도, 도는 빠르기, 크기, 튕김]
        self.rock_t = 8.0
        self.slip_t = 20.0
        self.duck, self.duck_t = 0.0, 0.0
        self.tokens_on = True       # 토큰으로 오르기 (끄면 일하는 펫 수로 오른다: 검사용)
        self.signal_t = {}          # 신호별 마지막으로 받은 때 (COOLDOWN)
        self.shake_t = 0.0          # 다음 팔 털기 (체력 60% 이하)
        self.bank = 0.0             # 쌓인 오를 높이 (m). 몸이 가장 높이 올라간 자리를 넘을 때마다 줄어든다
        self.tokens = 0             # 받은 토큰 합 (출력 환산, TOKEN_USD 를 곱하면 API 가격 달러)
        self.token_buf, self.token_float_t = 0, -1.0   # 아직 글자로 띄우지 않은 토큰
        self.deposit_t = 0.0        # 마지막으로 토큰을 받은 때
        self.resting = False
        self.charging = True        # 몸이 가장 높은 자리를 넘으면 쌓인 높이를 쓴다 (일 끝냄 다이노는 덤이라 안 쓴다)
        self.k_now = 1.0            # 지금 동작의 시간 배율 (빠를수록 몸도 또렷하게 따라간다)
        self.missing = 0            # 잡을 데가 없어 그 자리에 만든 홀드 수 (0 이어야 한다)
        self.unstuck = 0            # 갈 데가 없어 뗀 발을 원래 홀드로 돌리거나 낮은 발을 뗀 횟수 (검사용)
        self.phase_target = None    # 손발을 옮기는 동작 단계가 정한 몸 자리 (함수)
        self.move_start = 0.0
        self.flagging = False
        self.free_since = 0.0       # 뗀 발을 둔 채로 손을 잡은 때 (그 발은 FREE_HOLD 초 뒤에 옮긴다)
        self.face_want, self.face_since = None, 0.0   # 돌아보려는 쪽과 그렇게 된 때
        self.look_at = None         # 보고 있는 곳 (다음 홀드)
        self.look_tilt = 0.0        # 보는 쪽으로 몸통 그림을 기울인 정도 (그림만)
        self.night = 0.0            # 밤 정도 (0 낮 ~ 1 밤, 창이 시계를 보고 정한다. night_level)
        self.lamp = None            # 헤드램프가 비추는 곳 (밤에 깨어 있을 때만, 보는 곳을 부드럽게 따라간다)
        self.relaxed = 0            # 조건을 풀어서 고른 횟수
        self.far_reaches = 0        # 같은 손발을 또 옮기지 않으려고 한 번에 멀리 간 횟수
        self.burst_left = 4         # 둘러보며 쉬기 전에 이어서 할 손발 동작 수 (보통 빠르기)
        self.reach_log = []         # 손 뻗기마다 (종류, 가라앉은 깊이, 잡은 뒤 처진 깊이, 힘 모을 때·잡을 때 몸통 눌림) (검사용)
        self.squash_min = 1.0       # 지난 기록 뒤 몸통이 가장 눌린 정도 (검사용)
        self.drop_log = []          # 드롭니를 할 만한 손 뻗기마다 했는지 (검사용)
        self.pause_log = []         # 둘러보며 쉰 (시간, 본 곳 수) (검사용)
        self.free_log = []          # 뗀 발로 한 다음 동작: up (올라가 딛기), match (합발), keep (그대로 두고 손), return,
                                    # spot (빈 쪽에 올려 둔 홀드), fill (빈 쪽 홀드), wall (빈 쪽 벽) (검사용)
        self.lean_log = []          # 쏠렸을 때 빈 쪽에 발을 둔 동작: reach (손을 뻗으며), step (홀드로), wall (벽을 딛어) (검사용)
        self.switch_foot = None     # 합발 뒤 위로 옮길 발
        self.parked = False         # 남은 높이로 닿는 홀드가 없어 다음 토큰을 기다리는 중
        self.wait_for = 0.0         # 크럭스 앞에서 기다릴 때 모여야 할 높이
        self.crux_log = []          # 넘은 크럭스마다 (시도 횟수, 크럭스 아래 끝에 닿은 뒤 뛰기까지 손발 동작 수) (검사용)
        self.moves_since_crux = 0   # 크럭스 아래 끝에 닿은 뒤 한 손발 동작 수
        self.stall = 0              # 제자리 (손발 높이 합이 안 오름) 로 이어진 손발 동작 수
        self.best_top = -1e9        # 손발 높이 합의 최고 기록
        self.moves, self.grabs, self.actions = [], [], []
        self.tally = collections.Counter()   # 등반 일지 (journalcore.TALLY) 에 셀 일: crux, dyno (일 끝냄), fall, nap
        self.falling = False        # 벽의 끝으로 떨어지는 중 (카메라가 몸에 붙어 따라 내려간다)
        self.rope_cut = None        # 끊어진 줄이 남은 퀵드로 자리 (벽의 끝)
        self.wall_pending = False   # 벽이 끝나 새 벽을 기다린다 (떨어지는 장면을 아직 안 보였다)
        self.carry = 0.0            # 벽이 끝난 뒤 떨어지는 동안 받은 오를 높이 (새 벽으로 넘긴다)
        self.grounded = False       # 벽이 끝나 땅에 앉아 있다 (주간 사용량이 돌아오면 일어나 새 벽을 오른다)
        self.moon = None            # 달 표면 높이 (몸 좌표 y): 주간 사용량 100% 로 기둥 위 끝 달에 올라섰다 (앉으면 grounded 도)
        self.moon_t = -1e9          # 달 표면이 나타난 때 (하늘의 달이 사라진다)
        self.moon_flag = None       # 달에 꽂은 깃발 자리
        self.moon_pending = False   # 달에 오르는 장면을 기다린다 (그 전에 꺼져도 다음에는 달 위에서)
        self.week_reset = None      # 계정 주간 리셋 epoch 초 (달에서 기다릴 때 보인다)
        self.start_pose = None      # 새 벽의 첫 자세 (몸 자리, {손발: 홀드}) — 일어나 붙을 곳
        self.lay_wall(random.Random(seed + 3))
        self.brain_task = self.brain()
        next(self.brain_task)

    def lay_wall(self, xrng):
        """길과 홀드를 깔고 (크럭스는 xrng) 바닥에서 손발을 가까운 홀드에 붙여 세운다."""
        self.route = Route(self.rng, xrng)
        self.route.fill(self, self.cam + FILL_AHEAD)
        self.body = V(self.route.center(0.0), 0.0)
        self.limbs = {name: Limb(name) for name in LIMBS}
        taken = set()
        for limb in self.limbs.values():
            ideal = self.body + V(limb.side * 0.7, HAND_UP if limb.kind == "H" else -FOOT_DOWN)
            root = limb.root(self.body, 0.0)
            near = [h for h in self.holds if id(h) not in taken and (h.p - root).len() < limb.reach * 0.9
                    and limb.side * (h.p.x - self.body.x) > -0.2] or [h for h in self.holds if id(h) not in taken]
            hold = min(near, key=lambda h: (h.p - ideal).len())
            taken.add(id(hold))
            limb.hold, limb.pos = hold, hold.grip(limb.kind)
        self.body = self.stance_target()[0]
        self.top_y = self.body.y    # 몸이 가장 높이 올라간 자리
        for bolt in self.bolts:   # 시작할 때 이미 지나온 볼트에는 로프가 걸려 있다
            bolt.clipped = bolt.p.y < self.body.y

    def new_wall(self, restart=True, grounded=False, banner=True, keep=None):
        """새 벽: 길·홀드·볼트를 새로 깔고 바닥 (0m) 에서 다시 시작한다. 높이 기록도 새로 세고, 못 오른 높이는 버린다.
        지난 벽 높이를 말풍선으로 띄운다. restart 면 하던 동작을 버리고 처음부터 (brain 안에서 부를 때는 False).
        grounded 면 벽에 붙지 않고 땅에 앉는다 (keep = (카메라 - 몸 높이, 몸 x) 면 화면 속 자리를 그대로)."""
        last = self.peak
        self.holds, self.bolts, self.rocks, self.particles, self.texts, self.requests = [], [], [], [], [], []
        self.cam, self.falling, self.rope_cut, self.wall_pending = 0.45, False, None, False
        self.moon, self.moon_flag, self.moon_pending = None, None, False
        self.mode, self.vel, self.tilt, self.tilt_vel, self.crouch = "spring", V(), 0.0, 0.0, 0.0
        self.phase_target = self.look_at = self.lamp = self.rope = self.rope_hand = self.ledge = None
        self.sleeping = self.nap = self.resting = self.parked = False
        self.lids, self.idle_since, self.wait_for = 0.0, None, 0.0
        self.bank, self.carry, self.token_buf = self.carry, 0.0, 0   # 지난 벽에서 못 오른 높이는 버리고, 끝난 뒤 받은 것만
        self.best_top, self.stall, self.moves_since_crux, self.switch_foot = -1e9, 0, 0, None
        self.lay_wall(random.Random(self.rng.random()))
        self.jump(0.0, 0.0)
        self.start_pose = (self.body, {n: l.hold for n, l in self.limbs.items()})
        self.grounded = grounded
        if grounded:
            self.sit(keep[1] if keep else self.body.x)
            self.cam = self.body.y + (keep[0] if keep else 1.0)
        if banner:
            self.banner = ["새 벽! 지난주는 달까지 완등" if last >= MOON else f"새 벽! 지난 벽 {fmt_alt(last)}", 0.0]
        if self.hp <= 0 and not grounded:
            self.requests.append(("exhaust", ()))   # 5시간 사용량도 다 썼으면 새 벽에서 다시 잔다
        if restart:
            self.brain_task = self.brain()
            next(self.brain_task)

    def end_wall(self, animate=True, locked=True):
        """주간 사용량을 다 썼거나 주간 리셋: 지금 벽을 끝낸다. animate 면 다음 동작으로 빌레이 줄이 끊어져 땅까지
        떨어진다 (wall_end). 아니면 (창이 숨어 있다) 바로 새 벽: locked (주간을 다 써서 못 쓴다) 면 땅에 앉아서, 아니면
        벽에 붙어서. 이미 땅에 앉아 있으면 조용히 벽만 새로. 달 위에 있으면 달에서 내려온다 (leave_moon)."""
        if self.moon is not None:
            self.leave_moon(animate)
        elif self.grounded:
            self.new_wall(grounded=True, banner=False)
        elif animate:
            self.wall_pending = True
            self.requests.insert(0, ("wall_end", ()))
        else:
            self.new_wall(grounded=locked)

    def ground_y(self):
        """땅의 y (높이 GROUND)."""
        return GROUND - self.alt0

    def seat_y(self):
        """앉는 바닥의 y: 달 위면 달 표면, 아니면 땅."""
        return self.moon if self.moon is not None else self.ground_y()

    def sit(self, x):
        """땅 (달) 에 주저앉는다: 몸통이 바닥에 닿고 다리는 양옆으로 뻗고 손은 옆 바닥을 짚는다."""
        self.body = V(max(-WALL_HALF + 0.6, min(WALL_HALF - 0.6, x)), self.seat_y() + 1.0)
        self.mode, self.vel, self.tilt, self.tilt_vel = "script", V(), 0.0, 0.0
        for name, goal in self.sit_goals().items():
            limb = self.limbs[name]
            limb.mode, limb.tucked, limb.follow = "follow", False, goal
            limb.pos = limb.follow()

    def sit_goals(self):
        return {name: (lambda dx=dx, dy=dy: V(self.body.x + dx, self.seat_y() + dy))
                for name, (dx, dy) in {"LF": (-1.0, 0.1), "RF": (1.0, 0.1), "LH": (-1.25, 0.25), "RH": (1.25, 0.25)}.items()}

    def rise(self, animate=True):
        """주간 사용량이 돌아왔다: 땅에서 일어나 새 벽에 붙는다 (animate 면 걸어가 첫 홀드를 잡는 get_up, 아니면 바로)."""
        if not self.grounded or self.moon is not None or any(r[0] == "get_up" for r in self.requests):
            return
        if animate:
            self.requests.append(("get_up", ()))
        else:
            body, holds = self.start_pose
            self.body, self.mode, self.vel, self.grounded = body, "spring", V(), False
            for name, hold in holds.items():
                self.grab(self.limbs[name], hold)
            self.cam = self.body.y + 0.45

    def reach_moon(self, animate=True):
        """주간 사용량을 다 썼다 (100%): 기둥 위 끝 달에 오른다. animate 면 다음 동작으로 올라서는 장면 (moon_top),
        아니면 (창이 숨어 있다) 바로 달 위에 앉는다."""
        if self.moon is not None or self.moon_pending or self.falling or self.wall_pending:
            return   # 벽의 끝으로 떨어지는 중에는 오르지 않는다 (같은 주에 둘이 겹칠 일은 없다)
        if self.grounded:   # 땅에서 기다리던 중이면 벽에 붙인 뒤에
            self.rise(animate=False)
        if animate:
            self.moon_pending = True
            self.requests.insert(0, ("moon_top", ()))
        else:
            self.place_on_moon()

    def place_on_moon(self):
        """장면 없이 달 위에 앉힌다 (창이 숨어 있었거나 다시 켰을 때, 올라서는 도중에 숨었을 때). 깃발은 꽂혀 있다.
        brain 밖에서만 부른다 (하던 동작을 버리고 brain 을 새로 띄운다)."""
        hands = [self.held_y(self.limbs[n]) for n in ("LH", "RH")]
        self.moon, self.moon_t, self.moon_pending = min(max(hands) + 0.8, self.body.y + 1.75), self.t - 10.0, False
        self.clear_above_moon()
        self.ledge, self.sleeping, self.nap, self.rope, self.rope_hand = None, False, False, None, None
        self.phase_target = self.look_at = None
        self.requests, self.resting, self.parked, self.wait_for, self.crouch = [], False, False, 0.0, 0.0
        self.idle_since, self.lids, self.vel, self.falling = None, 0.0, V(), False
        self.brain_task = self.brain()   # 하던 동작 (다이노·낮잠 펴기 등) 을 버리고 달 위에서 처음부터
        next(self.brain_task)
        x = max(-WALL_HALF + 0.8, min(WALL_HALF - 0.8, self.body.x))
        self.moon_flag = self.flag_spot(x)
        self.grounded = True
        self.sit(x)
        self.clear_above_moon(gripped=False)
        self.cam = self.body.y + 0.45
        self.pct_shown, self.peak, self.passed = 100.0, float(MOON), len(self.landmarks)

    def clear_above_moon(self, gripped=True):
        """달 표면 위의 홀드·볼트를 치운다 (gripped 면 잡고 있는 홀드는 둔다)."""
        keep = {id(l.hold) for l in self.limbs.values() if l.mode == "grip"} if gripped else set()
        self.holds = [h for h in self.holds if h.p.y < self.moon - 0.35 or id(h) in keep]
        self.bolts = [b for b in self.bolts if b.p.y < self.moon - 0.5]

    def flag_spot(self, x):
        side = 1 if x < 0 else -1   # 넓은 쪽에 꽂는다
        return V(x + side * 1.0, self.moon)

    def leave_moon(self, animate=True):
        """달에서 주간 리셋: animate 면 다음 동작으로 뛰어내려 지구까지 떨어지는 장면 (moon_leave), 아니면 바로 새 벽에
        붙는다."""
        if self.moon is None:
            return
        if animate:
            self.wall_pending = True
            self.requests.insert(0, ("moon_leave", ()))
        else:
            self.new_wall(grounded=False)

    def moon_top(self):
        """주간 사용량 100%: 바위 기둥 위 끝이 달 표면이다. 손 위로 달 표면이 나타나고, 끝에 두 손을 걸어 몸을 끌어
        올려 올라선 뒤 깃발을 꽂고 '이번 주 완등!' 하며 만세, 리셋까지 달 위에 앉아 쉰다 (ground_wait).
        포타레지에서 자고 있었으면 깨어 포타레지를 접고 오른다."""
        if self.moon is not None:
            return
        if self.sleeping and self.ledge is not None:
            yield from self.wake()
        self.actions.append("moon_top")
        top = max(self.held_y(self.limbs[n]) for n in ("LH", "RH"))
        lip = self.moon = min(top + 0.8, self.body.y + 1.75)   # 매달린 몸에서 손이 닿는 높이
        self.moon_t, self.moon_pending = self.t, False
        self.clear_above_moon()
        self.phase_target, self.rope, self.rope_hand = None, None, None
        self.lids, self.look_at = 0.0, V(self.body.x, lip + 0.6)
        yield from self.wait(0.7, stoppable=False)
        x0 = max(-WALL_HALF + 0.8, min(WALL_HALF - 0.8, self.body.x))

        def at(dx, dy, base=None):
            return lambda: V(self.body.x + dx, (lip if base is None else self.body.y) + dy)
        yield from self.move_limb("LH", at(-0.55, 0.05), 0.35)   # 두 손을 달 표면 끝에 건다
        yield from self.move_limb("RH", at(0.55, 0.05), 0.35)
        yield from self.move_all({"LF": at(-0.45, -1.35, "body"), "RF": at(0.45, -1.35, "body")}, 0.8,
                                 body_to=lambda: V(x0, lip - 0.15), tilt_to=0.0)   # 몸을 끌어 올린다
        yield from self.move_all({"RF": at(0.45, 0.05)}, 0.6, body_to=lambda: V(x0, lip + 0.9))   # 눌러 올라서며 한 발을 올린다
        yield from self.move_all({"LF": at(-0.35, 0.05), "RF": at(0.35, 0.05), "LH": at(-0.75, -0.55, "body"),
                                  "RH": at(0.75, -0.55, "body")}, 0.7, body_to=lambda: V(x0, lip + 2.15))   # 일어선다
        spot = self.flag_spot(x0)
        hand = "RH" if spot.x > x0 else "LH"
        self.face = -1 if spot.x > x0 else 1
        self.look_at = spot + V(0, 0.5)
        yield from self.move_limb(hand, lambda: V(spot.x, lip + 1.5), 0.4, arc=0.1)   # 깃대를 들어
        yield from self.move_limb(hand, lambda: V(spot.x, lip + 0.8), 0.14, arc=0.0)   # 푹 꽂는다
        self.moon_flag = spot
        self.puff(spot, n=10, speed=0.9, size=0.15, life=0.9)
        self.texts.append(Float(spot + V(0, 2.3), "푹!"))
        yield from self.wait(0.4, stoppable=False)
        self.look_at = None
        self.banner = [f"이번 주 완등! 달 {fmt_alt(MOON)}", 0.0]
        self.confetti(40)
        yield from self.move_all({"LH": lambda: self.body + V(-0.6, 1.75), "RH": lambda: self.body + V(0.6, 1.75)}, 0.4)   # 만세
        self.squash_vel -= 1.5
        yield from self.wait(1.6, stoppable=False)
        yield from self.move_all(self.sit_goals(), 0.6, body_to=lambda: V(x0, lip + 1.0), tilt_to=0.0)   # 앉는다
        self.grounded = True
        self.sit(x0)
        self.clear_above_moon(gripped=False)

    def moon_leave(self):
        """달에서 주간 리셋: 일어나 '다시 지구로!' 하고 뛰어내린다. 높이 숫자와 배경이 가속 눈금을 따라 내려가고
        (fall_home), 땅에 닿으면 새 벽 아래에 앉는다 (주간 사용량이 돌아왔으니 곧 일어나 오른다)."""
        if self.moon is None:
            return
        self.actions.append("moon_leave")
        lip, x0 = self.moon, self.body.x
        self.lids, self.look_at = 0.0, None
        self.sleeping, self.nap, self.ledge = False, False, None

        def at(dx, dy):
            return lambda: V(self.body.x + dx, self.body.y + dy)
        yield from self.move_all({"LF": lambda: V(self.body.x - 0.35, lip + 0.05), "RF": lambda: V(self.body.x + 0.35, lip + 0.05),
                                  "LH": at(-0.75, -0.55), "RH": at(0.75, -0.55)}, 0.7, body_to=lambda: V(x0, lip + 2.15),
                                 tilt_to=0.0)   # 일어선다
        self.texts.append(Float(self.body + V(0, 1.6), "다시 지구로!"))
        yield from self.move_all({"RH": at(0.7, 1.6)}, 0.3)   # 손을 흔들고
        yield from self.move_all({"RH": at(0.9, 1.3)}, 0.2)
        yield from self.move_all({"RH": at(0.7, 1.6)}, 0.2)
        yield from self.move_all({"LH": at(-0.75, -0.55), "RH": at(0.75, -0.55)}, 0.3, body_to=lambda: V(x0, lip + 1.7))   # 웅크렸다
        yield from self.move_all({"LH": at(-0.6, 1.6), "RH": at(0.6, 1.6)}, 0.3, body_to=lambda: V(x0, lip + 2.8))   # 뛰어오른다
        self.grounded = False
        for limb in self.limbs.values():   # 허우적
            limb.mode, limb.tucked = "free", False
            limb.vel = V(self.wrng.uniform(-2.0, 2.0), self.wrng.uniform(1.5, 3.5))
        self.mode, self.falling = "script", True
        self.alt0 = MOON - self.body.y
        yield from self.fall_home(float(MOON), "다녀왔어요!")

    def get_up(self):
        """땅에서 영차 일어나 벽 앞으로 걸어가 첫 홀드를 잡고 올라선다."""
        if not self.grounded:
            return
        self.actions.append("get_up")
        self.lids, self.look_at = 0.0, None
        self.texts.append(Float(self.body + V(0, 1.6), "영차!"))
        body, holds = self.start_pose

        def stand(x):
            return lambda: V(x, self.ground_y() + 2.15)
        feet = {"LF": lambda: V(self.body.x - 0.35, self.ground_y() + 0.05),
                "RF": lambda: V(self.body.x + 0.35, self.ground_y() + 0.05),
                "LH": lambda: V(self.body.x - 0.75, self.body.y - 0.55), "RH": lambda: V(self.body.x + 0.75, self.body.y - 0.55)}
        yield from self.move_all(feet, 0.7, body_to=stand(self.body.x), tilt_to=0.0)   # 일어선다
        steps = max(1, round(abs(body.x - self.body.x) / 0.6))
        x0 = self.body.x
        for i in range(steps):   # 벽 앞으로 걸어간다 (콩콩)
            yield from self.move_all({}, 0.28, body_to=stand(x0 + (body.x - x0) * (i + 1) / steps), hop=0.12)
        self.face = 1 if body.x < self.body.x else self.face
        yield from self.move_all(dict(holds), 0.9, body_to=lambda: body, tilt_to=0.0)   # 첫 홀드를 잡고 올라선다
        self.mode, self.grounded = "spring", False
        self.top_y = max(self.top_y, self.body.y)
        if self.hp <= 0:
            self.requests.append(("exhaust", ()))

    def ground_wait(self):
        """땅 (달) 에서 기다린다: 가끔 벽을 올려다보며 (달에서는 지구를 내려다보며) 한숨 (5시간 사용량도 다 썼으면 꾸벅꾸벅 존다)."""
        if self.hp <= 0:
            if self.lids < 0.85:
                yield from self.tween(self, "lids", self.lids, 0.9, 0.8)
            self.texts.append(Float(self.body + V(-0.5 * self.face, 1.0), self.wrng.choice(("z", "Z")), "z"))
            yield from self.wait(2.4, until=lambda: bool(self.requests) or self.hp > 0)
            return
        if self.lids > 0.1:
            yield from self.tween(self, "lids", self.lids, 0.0, 0.4)
        yield from self.wait(self.wrng.uniform(4.0, 8.0), until=lambda: bool(self.requests) or self.hp <= 0)
        if self.requests or self.hp <= 0:
            return
        moon = self.moon is not None
        self.look_at = self.body + (V(2.0, -3.0) if moon else V(-0.6 * self.face, 4.0))   # 달에서는 아래 지구를 내려다보고, 땅에서는 벽을 올려다본다
        yield from self.wait(1.4, until=lambda: bool(self.requests))
        self.look_at = None
        if self.wrng.random() < 0.5:
            self.texts.append(Float(self.body + V(0, 1.4), "지구다…" if moon else "휴…"))

    # ---- 바깥에서 부르는 것 ---------------------------------------------------

    def signal(self, name, *args):
        """펫 상태에서 온 신호로 동작을 끼워 넣는다 (dyno, fall, chalk, exhaust, wake, refill).
        잦은 신호 (COOLDOWN) 는 그 간격 안이면 버린다. 끼워 넣었으면 True."""
        wait = COOLDOWN.get(name)
        if wait is not None:
            if self.t - self.signal_t.get(name, -1e9) < wait:
                return False
            self.signal_t[name] = self.t
        if self.sleeping and self.nap and name == "exhaust":   # 낮잠 중에 사용량을 다 썼다: 이제 리셋이 와야 깬다
            self.nap = False
            return True
        if self.sleeping and name not in AWAKE:
            return False
        self.request(name, *args)
        return True

    def request(self, name, *args):
        if self.sleeping and name not in AWAKE:
            return   # 자는 동안은 깨우기만 받는다
        if self.grounded and name not in GROUND_OK:
            return   # 땅에 앉아 있는 동안은 일어나기만 받는다
        self.requests.append((name, args))

    @property
    def pace(self):
        """빠르기 (0 쉼, 1 보통, 2 빠름, 3 전력 질주). 떨어져서 이미 오른 높이보다 1m 넘게 아래면 빠르게 다시 오른다."""
        steady = self.steady_pace()
        back = (steady > 0 and self.rope is None and not self.grounded and not self.falling
                and self.body.y < self.top_y - 1.0)
        return max(steady, 2) if back else steady

    def steady_pace(self):
        """쌓인 토큰 (또는 일하는 펫 수) 으로만 정한 빠르기 (다시 오를 때 빨라지는 것은 빼고)."""
        if self.tokens_on:   # 쌓인 높이가 많을수록 빠르게
            return 1 if self.bank < PACE_BANK[0] else 2 if self.bank < PACE_BANK[1] else 3
        return 0 if self.work <= 0 else 1 if self.work <= 2 else 2

    def deposit(self, tokens):
        """쓴 토큰 (출력 환산) 을 받아 오를 높이로 쌓는다."""
        self.tokens += tokens
        self.week_tokens += tokens
        self.bank += tokens * M_PER_TOKEN
        if self.wall_pending:
            self.carry += tokens * M_PER_TOKEN
        if self.room() >= self.wait_for:   # 크럭스 앞에서 기다리면 끝 홀드까지 오를 만큼 모여야 다시 오른다
            self.parked, self.wait_for = False, 0.0
        self.token_buf += tokens
        self.deposit_t = self.t

    def has_climb(self):
        """더 오를지. 토큰으로 오를 때는 쌓인 높이가 남았거나 추락해서 다시 올라야 할 때, 아니면 일하는 펫이 있을 때."""
        if self.grounded:
            return False
        if self.tokens_on:
            if self.parked and self.wait_for:   # 크럭스 앞에서 기다린다 (떨어져 생긴 높이도 쳐서 끝 홀드까지 모여야)
                return self.room() >= self.wait_for
            return (self.bank > 0.05 and not self.parked) or self.body.y < self.top_y - 1.5   # 쉬는 자세로 낮춘 몸 (약 0.9m) 은 추락으로 치지 않는다
        return self.work > 0

    def room(self):
        """이번에 더 오를 수 있는 높이: 토큰으로 오를 때는 쌓인 높이 + 쉬며 낮춘 몸, 아니면 끝없이."""
        if self.tokens_on:
            return self.bank + max(0.0, self.top_y - self.body.y)
        return 1e9

    def climb_ready(self):
        """쉬다가 다시 오를지. 토큰은 1m 넘게 모였거나 (조금씩 들어올 때 한 동작만 하고 또 쉬지 않게 모아서 오른다),
        1m 가 안 되게 남았는데 토큰이 LEFTOVER_WAIT 초 동안 더 안 들어왔으면 남은 것을 마저 오른다."""
        if self.grounded:
            return False
        if self.tokens_on:
            if self.parked and self.wait_for:
                return self.room() >= self.wait_for
            return (self.bank >= 0.99 or (self.bank > 0.05 and not self.parked and self.t - self.deposit_t >= LEFTOVER_WAIT)
                    or self.body.y < self.top_y - 1.5)
        return self.work > 0

    def state_label(self):
        """높이 상자에 보이는 지금 상태: 잠, 쉬는 자세면 기다림 (일하는 펫 있음)·쉼, 오르는 중이면 빠르기."""
        if self.grounded:
            return "달" if self.moon is not None else "바닥"
        if self.sleeping:
            return "낮잠" if self.nap else "잠"
        if self.resting:
            return "기다림" if self.work > 0 else "쉼"
        return PACE_NAME[max(1, self.pace)]

    def shown_altitude(self):
        """높이 상자에 보이는 높이: 가속 눈금이면 그 높이, 아니면 이번 길에서 가장 높이 올라간 자리 (일어섰다 앉을 때마다
        숫자가 오르내리지 않게). 1.5m 넘게 떨어졌으면 (추락) 지금 높이."""
        if self.moon is not None and not self.falling:
            return float(MOON)
        if self.scaled():
            return week_height(self.pct_shown)
        return self.alt0 + self.top_y if self.body.y >= self.top_y - 1.5 else self.altitude()

    def scaled(self):
        """높이가 주간 사용량의 가속 눈금인지: 주간 % 를 알고 벽에 붙어 있을 때 (떨어질 때·땅에서는 실제 높이)."""
        return self.week_pct is not None and not self.falling and not self.grounded

    def set_week(self, pct, per=None, reset=None):
        """계정 주간 사용량 (%, None 이면 그대로) 과 1% 에 드는 토큰 (None 이면 그대로). 처음 받거나 % 가 오르면 그
        % 에 그때까지의 토큰이 들어 있으니 토큰은 처음부터 센다 (꺼져 있던 동안 쌓인 토큰이 % 를 밀어 올리지 않게).
        처음 받으면 저장해 둔 높이가 더 높아도 이 % 에 맞추고, % 가 줄면 (새 주) 벽의 끝 장면을 기다리는 게 아니면
        새 주 높이로 맞춘다 (벽이 끝나지 않은 채 주가 바뀌었어도 지난주 높이에 머물지 않게. 달 위면 내려올 때 새로)."""
        if per:
            self.week_per = per
        if reset:
            self.week_reset = reset
        if pct is None:
            return
        old, self.week_pct = self.week_pct, max(0.0, min(100.0, pct))
        if old is None or self.week_pct != old:
            self.week_tokens = 0.0
        if self.moon is None and (old is None or (self.week_pct < old and not self.wall_pending and not self.falling)):
            self.rebase(min(self.pct_shown, self.week_target()))

    def rebase(self, pct):
        """보이는 주간 % 를 pct 로 낮추고 최고 높이와 넘은 기록도 거기에 맞춘다 (말풍선 없이). 새 주에 다시 따라잡을
        지난 주 기록 자국은 지운다."""
        self.pct_shown = pct
        self.peak = min(self.peak, week_height(pct))
        self.passed = self.count_passed()
        self.carved = []

    def week_target(self):
        """보일 주간 %: 계정 값에 그 뒤 받은 토큰만큼 (다음 % 는 넘지 않게)."""
        return min(100.0, self.week_pct + min(PCT_STEP, self.week_tokens / self.week_per))

    def reroll_weather(self, other=False):
        """비·눈을 다시 뽑는다 (other 면 지금과 다른 쪽으로)."""
        if other:
            self.weather_kind = "snow" if self.weather_kind == "rain" else "rain"
        else:
            self.weather_kind = self.wrng.choice(("rain", "snow"))

    def altitude(self):
        """지금 높이 (m): 가속 눈금이면 보이는 주간 % 의 높이, 아니면 오른 바위 높이."""
        if self.moon is not None and not self.falling:
            return float(MOON)
        return week_height(self.pct_shown) if self.scaled() else self.alt0 + self.body.y

    def jump(self, target, peak=None):
        """높이를 target (m) 으로 옮긴다 (다시 켤 때, 검사). peak 까지의 기록은 말풍선 없이 넘은 걸로 친다."""
        self.alt0 = target - self.body.y
        self.top_y = self.body.y
        self.pct_shown = height_pct(target)
        self.peak = max(target, peak or 0.0)
        self.passed = self.count_passed()
        self.banner = None
        self.carved = []
        if target >= HIGH:
            self.drops = []   # 구름 위로 건너뛰면 아래에서 내리던 비·눈은 없다

    def take_tally(self):
        """지난번 뒤로 센 일 (넘은 크럭스, 일 끝냄 다이노, 로프 추락, 낮잠) 을 넘기고 비운다 (등반 일지)."""
        tally, self.tally = self.tally, collections.Counter()
        return tally

    def snapshot(self):
        """저장할 기록: 높이 (이번 길에서 가장 높이 올라간 자리), 최고 높이, 쌓인 높이, 받은 토큰 합."""
        if (self.moon is not None or self.moon_pending) and not self.wall_pending:   # 달 위 (오르는 장면 전에 꺼져도)
            return {"height": float(MOON), "peak": float(MOON), "bank": 0.0, "tokens": round(self.tokens, 1),
                    "grounded": True, "moon": True}
        if self.wall_pending or self.grounded:   # 벽이 끝났다 (떨어지는 장면 전에 꺼져도 다음에는 새 벽 땅에서)
            return {"height": 0.0, "peak": 0.0, "bank": 0.0, "tokens": round(self.tokens, 1), "grounded": True}
        height = week_height(self.pct_shown) if self.scaled() else self.alt0 + self.top_y
        return {"height": round(height, 2), "rock": round(self.alt0 + self.top_y, 2), "peak": round(self.peak, 2),
                "bank": round(self.bank, 3), "tokens": round(self.tokens, 1)}

    def restore(self, saved):
        """snapshot() 으로 저장한 기록으로 되돌린다. 깨진 값은 처음 값으로 둔다."""
        def number(key):
            value = saved.get(key) if isinstance(saved, dict) else None
            return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else 0.0
        height = max(0.0, number("height"))
        self.jump(height, max(height, number("peak")))
        if isinstance(saved, dict) and "rock" in saved:   # 땅 자리는 오른 바위 높이로 (가속 눈금 높이와 다르다)
            self.alt0 = max(0.0, number("rock")) - self.body.y
        self.bank = number("bank")
        tokens = saved.get("tokens") if isinstance(saved, dict) else None
        ok = isinstance(tokens, (int, float)) and not isinstance(tokens, bool) and math.isfinite(tokens) and tokens >= 0
        self.tokens = tokens if ok else 0
        if isinstance(saved, dict) and saved.get("moon") is True:   # 달 위에서 리셋을 기다리던 중
            self.place_on_moon()
        elif isinstance(saved, dict) and saved.get("grounded") is True:   # 벽이 끝나 땅에서 기다리던 중
            self.new_wall(grounded=True, banner=False)

    def next_milestone(self):
        return self.landmarks[self.passed] if self.passed < len(self.landmarks) else None

    def count_passed(self):
        """최고 높이까지의 기록 수. 기록 파일은 높이를 0.01m 로 반올림해서 그만큼 너그럽게 (다시 켠 뒤 같은 % 에서 끝난
        지난 주 기록을 또 넘지 않게)."""
        return sum(1 for m in self.landmarks if m[0] <= self.peak + 0.01)

    def set_records(self, records):
        """지난 주들에 오른 높이 [(이름 '지난주'·'2주 전'…, 높이 m)] (journalcore.past_reach) 를 랜드마크에 섞는다. 높이
        글자가 같으면 하나로 묶고, 0 이하나 달 (달 도착 장면이 따로 있다) 은 뺀다. 지금 최고 높이 아래 기록은 말풍선 없이
        넘은 걸로 친다. 지난주가 51% 로 끝났으면 이번 주도 51% 에서 닿아서 '넘었어요' 대신 '따라잡았어요'."""
        merged = {}
        for name, height in records:
            if 0 < height < MOON:
                mark = merged.setdefault(fmt_alt(height), [height, []])
                mark[0] = min(mark[0], height)
                mark[1].append(name)
        marks = sorted((height, f"{'·'.join(names)} 기록", f"{'·'.join(names)} 기록({label})을 따라잡았어요!",
                        ("·".join(names), label)) for label, (height, names) in merged.items())
        if marks == self.records:
            return
        self.records = marks
        self.landmarks = sorted(MILESTONES + marks, key=lambda m: (m[0], len(m) == 3))   # 같은 높이면 지난 주 기록 먼저
        self.passed = self.count_passed()

    def precip(self):
        """비·눈의 양 (흐림까지는 0, 구름 위로는 없다)."""
        if self.altitude() >= HIGH:
            return 0.0
        return max(0.0, (self.weather - 0.35) / 0.65)

    def gloom(self):
        """흐려서 어두운 정도 (구름 위로 올라갈수록 옅다)."""
        alt = self.altitude()
        return self.weather if alt < HIGH else 0.0

    def closure(self):
        """눈꺼풀이 감긴 정도."""
        blink = math.sin(math.pi * self.blink_age / 0.16) if self.blink_age is not None else 0.0
        return max(self.lids, blink, 0.8 * self.duck)   # 돌을 피할 때 질끈

    def update(self, dt):
        dt = min(dt, 0.05)
        if dt <= 0:
            return
        self.t += dt
        self.flagging = any(f.mode == "follow" and getattr(f, "free_style", None) in ("flag", "backflag") and not f.tucked
                            for f in (self.limbs["LF"], self.limbs["RF"]))
        if self.moon is None:   # 달 표면 위로는 홀드가 없다
            self.route.fill(self, self.cam + FILL_AHEAD)
        self.brain_task.send(dt)
        steps = max(1, math.ceil(dt * (30 if self.resting else 60)))   # 쉬는 동안은 몸 계산을 덜 잘게
        for _ in range(steps):
            self.move_body(dt / steps)
            self.move_free_limbs(dt / steps)
        if self.body.y > self.top_y:   # 가장 높이 올라간 자리를 넘은 만큼 쌓인 높이를 쓴다 (추락 뒤 다시 오르는 높이는 안 쓴다)
            if self.tokens_on and self.charging and self.ledge is None:   # 포타레지에 오르내리기·덤 다이노는 안 쓴다
                self.bank -= self.body.y - self.top_y
            self.top_y = self.body.y
        if self.scaled():   # 보이는 주간 % 가 따라붙는다 (한 주 안에서는 내려가지 않는다)
            target = self.week_target()
            if target > self.pct_shown:
                step = max((target - self.pct_shown) * (1 - math.exp(-PCT_EASE[0] * dt)), PCT_EASE[1] * dt)
                self.pct_shown = min(target, self.pct_shown + step)
        if self.token_buf >= 0.5 and self.t - self.token_float_t > 0.7 and not self.falling:   # 받은 토큰을 글자로 띄운다 (잦으면 모아서)
            side = 1.3 if len(self.texts) % 2 else -1.3
            self.texts.append(Float(self.body + V(side, 0.9), f"+{int(self.token_buf + 0.5):,} 토큰", "token"))
            self.token_buf, self.token_float_t = 0, self.t
        for limb in self.limbs.values():
            if limb.mode == "grip":
                limb.pos = self.grip_point(limb, limb.hold)
                if limb.wiggle > 0:   # 고쳐 쥐기: 손이 살짝 들렸다 다시 감긴다
                    limb.wiggle = max(0.0, limb.wiggle - dt)
                    limb.pos = limb.pos + V(0, 0.07 * math.sin(math.pi * limb.wiggle / 0.18))
            elif limb.mode == "follow":
                limb.pos = limb.follow()
        self.move_effects(dt)
        self.squash_min = min(self.squash_min, self.squash)
        self.move_weather(dt)
        self.move_milestones(dt)
        self.move_events(dt)
        if self.falling:   # 떨어지는 동안은 wall_end 가 카메라를 옮긴다
            pass
        else:
            self.cam += (self.body.y + 0.45 - self.cam) * (1 - math.exp(-2.6 * dt))
        keep = {id(l.hold) for l in self.limbs.values()}
        if self.ledge and self.ledge.home:
            keep |= {id(h) for h in self.ledge.home[1].values()}
        low = self.cam - 14   # 떨어져도 아래 홀드를 다시 잡을 수 있게 넉넉히 남긴다
        self.holds = [h for h in self.holds if h.p.y > low or id(h) in keep]
        self.bolts = [b for b in self.bolts if b.p.y > low]

    # ---- 홀드 -------------------------------------------------------------

    def place_hold(self, p, big=False):
        p.x = max(-WALL_HALF + 0.3, min(WALL_HALF - 0.3, p.x))
        for h in self.holds:
            if abs(h.p.y - p.y) < 0.5 and (h.p - p).len() < 0.48:
                return None
        hold = Hold(p, self.rng.uniform(0.3, 0.36) if big else self.rng.uniform(0.19, 0.27), self.rng, big)
        hold.made_t, hold.made_cam = self.t, self.cam
        self.holds.append(hold)
        return hold

    def other(self, limb):
        return next(l for l in self.limbs.values() if l.kind == limb.kind and l is not limb)

    def held_y(self, limb):
        """손발 높이: 잡고 있으면 그 홀드, 잠깐 뗀 손발 (플래그·벽 딛기, 초크·털기) 은 돌아갈 홀드, 옮기는 중이면 지금 자리."""
        if limb.mode == "grip" or (limb.mode == "follow" and limb.hold is not None and not limb.tucked):
            return limb.hold.p.y
        return limb.pos.y

    def grip_point(self, limb, hold):
        other = self.other(limb)
        side = limb.side if other.hold is hold and other.mode in ("grip", "move") else 0   # 두 손·두 발이 한 홀드를 모아 잡는다·딛는다
        return hold.grip(limb.kind, side)

    def hand_options(self, limb, relax=0, up_only=False):
        """손이 갈 만한 홀드들 (점수, id, 홀드). relax 면 조건을 풀어 옆으로 고쳐 잡기까지 넣는다.
        지금 발로 몸을 세울 수 있는 데까지 세워도 팔 97% 안에 닿지 않는 홀드는 뺀다 (손과 발 사이가 너무 벌어져
        팔이 늘어난 채 매달리지 않게. 조건을 다 푼 relax 2 는 막히지 않게 뺀지 않는다)."""
        other = self.other(limb)
        shoulder = limb.root(self.body, self.tilt) + V(0, 0.25)   # 손을 뻗는 동안 몸도 따라 올라온다
        top = self.feet_top() if relax < 2 else None
        stand = limb.root(V(self.body.x, top), self.tilt) if top is not None else None
        my_y, other_y = self.held_y(limb), self.held_y(other)
        want = min(0.75, max(0.3, self.room()))   # 남은 높이가 적으면 마지막 한 수도 그만큼만
        out = []
        for h in self.holds:
            if h is limb.hold or (h is other.hold and h.r < 0.3) or h.foot:
                continue
            gain = h.p.y - my_y
            if gain < (0.12 if up_only else 0.12 - 0.17 * relax) or h.p.y > other_y + 1.25 + 0.4 * relax:
                continue
            if h in limb.recent:   # 최근에 잡았던 홀드로 돌아가지 않는다
                continue
            if limb.side * (h.p.x - other.pos.x) < -0.25 - 0.25 * relax:   # 팔을 크게 엇갈리지 않는다
                continue
            if (h.grip("H") - shoulder).len() > limb.reach * 0.97 + 0.15 + 0.2 * relax:
                continue
            if stand is not None and (h.grip("H") - stand).len() > limb.reach * 0.97:
                continue
            c = self.route.center(h.p.y)
            score = -abs(gain - want) * 1.4 - abs(h.p.x - (c + limb.side * 0.6)) * 0.7 + self.rng.uniform(0, 0.6)
            out.append((score, id(h), h))
        return out

    def foot_options(self, limb, relax=0):
        """발이 갈 만한 홀드들. relax 면 높이 띠·몸 쪽·거리 조건을 푼다. 다리가 닿는지는 몸이 팔을 펴고 내려앉을
        높이에서 본다 (손을 뻗느라 일어선 채로 보면 낮은 홀드가 빠져 발이 높이 놓이고 개구리 자세가 된다)."""
        other = self.other(limb)
        hip = limb.root(V(self.body.x, min(self.body.y, self.arm_hang_y())), self.tilt)
        hands_low = min(self.held_y(l) for l in self.limbs.values() if l.kind == "H")
        free = limb.mode == "follow" and limb.hold is not None   # 뗀 발은 원래 홀드 높이를 기준으로 (플래그한 발 높이가 아니라)
        my_y = limb.hold.p.y if free else self.held_y(limb)
        now = (limb.pos - hip).len()
        want_gap = FOOT_HIGH if self.rng.random() < 0.15 else FOOT_SPAN   # 가끔 발을 높이 올린다
        want = hands_low - want_gap
        out = []
        ceiling = self.foot_ceiling() if relax < 2 and self.pace <= 2 else 1e9   # 조건을 다 풀거나 전력 질주면 높은 곳도
        for h in self.holds:
            if (h is limb.hold and limb.mode == "grip") or h is other.hold or h in limb.recent or h.p.y > ceiling:
                continue
            d = (h.grip("F") - hip).len()
            bends = now > limb.reach * 0.9 and d < now - 0.25 and h.p.y > my_y - 0.3   # 쭉 펴진 다리를 굽힌다 (많이 내려가지 않고)
            if h.p.y < my_y + (-0.1 if free else 0.15) and not bends:   # 발은 나아질 때만 옮긴다 (뗀 발은 같은 높이로 돌아가도 된다)
                continue
            if not hands_low - FOOT_SPAN - 0.55 - 0.3 * relax < h.p.y < hands_low - min(want_gap, FOOT_SPAN - 0.55) + 0.3 * relax:
                continue
            if limb.side * (h.p.x - self.body.x) < -0.15 * relax:
                continue
            if d > limb.reach * (0.88 + 0.06 * relax):   # 다리를 쭉 펴지 않는다
                continue
            score = (-abs(h.p.y - want) * 3.0 + (0.2 if h.chalk > 0 else 0.0)
                     - abs(limb.side * (h.p.x - self.body.x) - FOOT_WIDTH) * 1.0 + self.rng.uniform(0, 0.5))
            out.append((score, id(h), h))
        return out

    def hands_x(self):
        """두 손 x: 잡은 홀드 (잠깐 뗀 손은 돌아갈 홀드), 홀드가 없으면 지금 자리."""
        return [h.hold.grip("H").x if h.hold is not None else h.pos.x for h in (self.limbs["LH"], self.limbs["RH"])]

    def hands_after(self, limb, hold):
        """limb 이 hold 를 잡고 난 뒤의 두 손 x."""
        other = self.other(limb)
        return [hold.grip("H").x, other.hold.grip("H").x if other.hold is not None else other.pos.x]

    def lean_side(self, hands_x=None, bx=None):
        """두 손이 몸 (x 가 bx) 한쪽 위, 두 발 (디딘 홀드, 뗀 발은 돌아갈 홀드) 이 반대쪽 아래로 쏠렸으면 손 쪽 (빈 쪽, ±1),
        아니면 0. 대각선으로 늘어나 손 쪽 아래가 비어 있어 균형이 무너져 보이는 자세."""
        hands_x = self.hands_x() if hands_x is None else hands_x
        bx = self.body.x if bx is None else bx
        feet_x = [f.hold.p.x if f.hold is not None else f.pos.x for f in (self.limbs["LF"], self.limbs["RF"])]
        for s in (1, -1):
            if all(s * (x - bx) > LEAN_SIDE for x in hands_x) and all(s * (x - bx) < -LEAN_SIDE for x in feet_x):
                return s
        return 0

    def leaning(self):
        """쏠렸는데 빈 쪽에 발이 하나도 없다 (뗀 발·옮기는 발도 지금 자리로)."""
        s = self.lean_side()
        return bool(s) and not any(s * (f.pos.x - self.body.x) > LEAN_SIDE for f in (self.limbs["LF"], self.limbs["RF"]))

    def fill_options(self, foot, s, relax=0):
        """빈 쪽 (s) 아래에서 foot 이 디딜 홀드들 (점수, id, 홀드). 손 가운데 아래 가까이, 지금 발 높이에서 크게 내려가지
        않고 다리를 쭉 펴지 않는 데만. relax 면 높이·거리 조건을 조금 푼다 (뗀 발이 반대쪽으로 돌아가기 전에)."""
        hip = foot.root(V(self.body.x, min(self.body.y, self.arm_hang_y())), self.tilt)
        hands_low = min(self.held_y(h) for h in (self.limbs["LH"], self.limbs["RH"]))
        hx = sum(self.hands_x()) / 2
        want_x = self.body.x + s * min(0.9, max(0.45, s * (hx - self.body.x)))
        base = foot.hold.p.y if foot.hold is not None else foot.pos.y
        top = hands_low - (FOOT_SPAN - 0.55) + 0.3 * relax
        if self.pace <= 2 and not relax:
            top = min(top, self.foot_ceiling())
        other = self.other(foot)
        out = []
        for h in self.holds:
            if h is foot.hold or h is other.hold or h.p.y > top or h.p.y < base - 0.45 - 0.35 * relax:
                continue
            if s * (h.p.x - self.body.x) < LEAN_SIDE or (h.grip("F") - hip).len() > foot.reach * (0.9 + 0.05 * relax):
                continue
            score = -abs(h.p.x - want_x) - 0.8 * abs(h.p.y - (hands_low - FOOT_SPAN)) + self.rng.uniform(0, 0.3)
            out.append((score, id(h), h))
        return out

    def hands_can_move(self):
        """어느 손이든 갈 홀드가 있다 (조건을 조금 풀어도)."""
        return any(self.hand_options(h, relax) for relax in (0, 1) for h in (self.limbs["LH"], self.limbs["RH"]))

    def fill_move(self):
        """네 손발이 다 붙은 채 쏠려 있으면 (빈 쪽 발, 빈 쪽 홀드). 디딜 홀드가 없으면 홀드 자리에 None (벽을 딛는다).
        쏠리지 않았거나, 방금 그 발을 옮겼는데도 쏠려 있거나 (같은 발을 또 옮기지 않게 평소 순서로 넘긴다), 디딜 홀드가
        없는데 손도 갈 데가 없으면 (벽을 딛어도 다음 차례에 발이 그대로 돌아온다) None."""
        if any(l.mode != "grip" for l in self.limbs.values()):
            return None
        s = self.lean_side()
        foot = self.limbs["RF" if s > 0 else "LF"]
        if not s or (self.moves and self.moves[-1] == foot.name):
            return None
        options = self.fill_options(foot, s)
        if not options and not self.hands_can_move():
            return None
        self.lean_log.append("step" if options else "wall")
        return foot, (max(options)[2] if options else None)

    def choose_move(self):
        """다음에 옮길 손발과 홀드. 정해진 순서 없이 그때 자세를 보고 고른다."""
        hands = sorted((self.limbs["LH"], self.limbs["RH"]), key=self.held_y)
        feet = sorted((self.limbs["LF"], self.limbs["RF"]), key=self.held_y)
        if self.switch_foot is not None:   # 합발 뒤: 원래 디뎠던 발을 위로 (발 바꾸기)
            foot, self.switch_foot = self.switch_foot, None
            options = foot.mode == "grip" and (self.foot_options(foot, 0) or self.foot_options(foot, 1))
            if options:
                return foot, max(options)[2]
        for foot in self.free_feet():   # 뗀 발은 다음 동작으로 (삼지점을 다시 만든다)
            move = self.free_foot_move(foot, hands)
            if move is not None:
                return move
        if self.stall < 3 and not self.free_feet():   # 두 발이 한쪽 아래, 두 손이 반대쪽 위로 쏠렸으면 빈 쪽 발부터
            move = self.fill_move()
            if move is not None:
                return move
        if self.stall >= 3:   # 세 번 연속 제자리면: 아래에 펴진 채 남은 발을 끌어올리고, 손은 위로만 (작은 다이노까지)
            for f in feet:
                hip = f.root(self.body, self.tilt)
                if f.pos.y < hip.y and (f.pos - hip).len() > f.reach * 0.9:
                    options = self.catch_up_options(f, side_tol=0.8)
                    if options:
                        self.relaxed += 1
                        return f, max(options)[2]
            for relax in (0, 1, 2):
                for limb in hands:
                    options = self.hand_options(limb, relax, up_only=True)
                    if options:
                        self.relaxed += 1
                        return limb, max(options)[2]
            reach = self.reach_options(hands) or self.reach_options(feet)
            if reach:
                self.relaxed += 1
                return reach[1], reach[0]
        stretch = self.held_y(hands[0]) - self.held_y(feet[1])
        r = self.rng.random()
        pinned = [f for f in feet if (f.pos - f.root(self.body, self.tilt)).len() > f.reach * 0.9]
        if pinned:
            order = pinned + [l for l in (hands[0], hands[1], feet[0], feet[1]) if l not in pinned]   # 다리가 펴진 발부터 따라 올린다
        elif stretch > FOOT_SPAN + 0.3 or (stretch > FOOT_SPAN - 0.1 and r < 0.4):
            order = [feet[0], feet[1], hands[0], hands[1]]          # 몸이 길게 늘어났으면 발부터
        elif r < 0.12:
            order = [hands[1], hands[0], feet[0], feet[1]]          # 위쪽 손을 한 번 더 (멀리 뻗기·고쳐 잡기)
        elif r < 0.22:
            order = [self.rng.choice(feet), hands[0], hands[1]]     # 발 고쳐 딛기
        else:
            order = [hands[0], hands[1], feet[0], feet[1]]
        last = self.limbs.get(self.moves[-1]) if self.moves else None
        if last in order:   # 방금 옮긴 손발은 맨 뒤로 (다른 손발에 갈 데가 있으면 그쪽이 먼저)
            order = [l for l in order if l is not last] + [last]
        for relax in (0, 1, 2):   # 맞는 데가 없으면 조건을 풀어 고쳐 잡기·고쳐 딛기, 그래도 없으면 멀리 뻗어 몸을 당겨 올리기
            if relax == 2 and self.room() < LAST_ROOM:
                return None   # 남은 높이가 적은데 가까운 데가 없으면 크게 뻗지 않고 여기서 멈춘다 (다음 토큰과 합쳐 오른다)
            for limb in (order if relax == 0 else order + [l for l in hands + feet if l not in order]):
                options = self.hand_options(limb, relax) if limb.kind == "H" else self.foot_options(limb, relax)
                if options:
                    self.relaxed += relax > 0
                    return limb, self.pick_ahead(limb, options)
        for limb in order + [l for l in hands + feet if l not in order]:   # 따라잡기: 닿는 홀드라면 지금보다 위 아무 데나
            options = self.catch_up_options(limb)
            if options:
                self.relaxed += 1
                return limb, max(options)[2]
        reach = self.reach_options(hands + feet)
        if reach:
            self.relaxed += 1
            return reach[1], reach[0]
        move = self.unstick() if self.stall < 4 else None   # 발을 고쳐 디뎌 몸을 세운다 (맴돌면 그만두고 홀드를 만든다)
        if move is not None:
            self.unstuck += 1
            return move
        self.missing += 1   # 그래도 없으면 가장 처진 손발 쪽 길 위에 하나 만든다
        limb = min(hands + feet, key=lambda l: self.held_y(l) - (self.body.y + (HAND_UP if l.kind == "H" else -FOOT_DOWN)))
        y = self.held_y(limb) + 0.6
        x = (self.body.x + self.route.center(y)) / 2 + limb.side * 0.6
        hold = self.place_hold(V(x, y))
        if hold is None:
            hold = Hold(V(x, y), 0.22, self.rng)
            self.holds.append(hold)
        return limb, hold

    def unstick(self):
        """손발 어디에도 갈 데가 없을 때 (홀드를 만들기 전에): 뗀 발은 원래 홀드에 다리가 닿으면 그리로 돌려 딛고, 두 발을 다
        디뎠는데 한 발이 1.5 넘게 낮으면 그 발을 떼어 벽을 딛는다 (홀드 자리에 None). 낮은 발에 다리 길이만큼 묶여 몸이 못
        일어서던 것을 높은 발로 일어서 손이 닿게 한다. 둘 다 아니면 None."""
        for f in self.free_feet():
            if (f.hold is not None and f.hold in self.holds and f.hold is not self.other(f).hold
                    and (f.hold.grip("F") - f.root(self.body, self.tilt)).len() < f.reach * 0.98):
                return f, f.hold
        feet = (self.limbs["LF"], self.limbs["RF"])
        if all(f.mode == "grip" for f in feet):
            low, high = sorted(feet, key=lambda f: f.hold.p.y)
            if high.hold.p.y - low.hold.p.y > 1.5:
                return low, None
        return None

    def pick_ahead(self, limb, options):
        """options 중 limb 이 갈 홀드. 좋은 순서대로 거기로 옮겼다고 치고, 다른 손발에 갈 데가 생기는 첫 홀드를 고른다
        (안 생기면 다음에도 같은 손발을 옮겨야 한다). 닿는 데 중 그런 홀드가 없으면 조금 더 먼 홀드 중 다른 손발을
        풀어 주는 가장 가까운 곳, 그것도 없으면 가장 먼 곳으로 한 번에 간다 (같은 손발을 두 번 옮기지 않게)."""
        ranked = [o[2] for o in sorted(options, key=lambda o: (o[0], o[1]), reverse=True)]
        for hold in ranked:
            if self.frees_others(limb, hold):
                return hold
        far = self.hand_options(limb, 1, up_only=True) if limb.kind == "H" else self.foot_options(limb, 1)
        far = sorted((o[2] for o in far if o[2].p.y > ranked[0].p.y), key=lambda h: h.p.y)
        if not far or self.room() < 0.75:   # 남은 높이가 적으면 멀리 가지 않는다
            return ranked[0]
        self.far_reaches += 1
        return next((hold for hold in far if self.frees_others(limb, hold)), far[-1])

    def arm_hang_y(self):
        """잡은 손들에 팔을 쭉 펴고 (95%) 매달릴 때의 몸 높이 (잡은 손이 없으면 지금 높이)."""
        hands = [l for l in self.limbs.values() if l.kind == "H" and l.hold is not None and l.mode in ("grip", "move", "follow")]
        if not hands:
            return self.body.y
        x = self.body.x
        return max(l.hold.grip("H").y - l.root(V(), self.tilt).y
                   - math.sqrt(max(0.01, (l.reach * 0.95) ** 2 - (l.hold.grip("H").x - x - l.root(V(), self.tilt).x) ** 2))
                   for l in hands)

    def foot_ceiling(self):
        """발을 둘 수 있는 가장 높은 곳: 팔을 펴고 매달릴 때의 엉덩이에서 FOOT_CEIL 아래 (무릎이 깊게 접히지 않게)."""
        return min(self.body.y, self.arm_hang_y()) + HIP[1] - FOOT_CEIL

    def feet_top(self):
        """지금 딛은 발로 몸을 세울 수 있는 가장 높은 몸 높이 (다리 95%). 딛은 발이 없으면 None."""
        feet = [f for f in self.limbs.values() if f.kind == "F" and f.mode == "grip"]
        if not feet:
            return None
        return min(f.pos.y - HIP[1] + math.sqrt(max(0.01, (f.reach * 0.95) ** 2 - (f.pos.x - self.body.x - f.side * HIP[0]) ** 2))
                   for f in feet)

    def frees_others(self, limb, hold):
        """limb 을 hold 로 옮겼다고 치면 다른 손발에 갈 데가 있는지 (몸은 그대로 둔 채로 본다)."""
        saved = limb.hold, limb.mode, limb.pos
        limb.hold, limb.mode, limb.pos = hold, "grip", self.grip_point(limb, hold)
        try:
            return any(self.hand_options(o, 0) if o.kind == "H" else self.foot_options(o, 0)
                       for o in self.limbs.values() if o is not limb)
        finally:
            limb.hold, limb.mode, limb.pos = saved

    def reach_options(self, limbs):
        """작은 다이노: 위쪽의 가장 가까운 홀드까지 뻗어 몸을 끌어올린다. (홀드, 손발) 또는 None."""
        best = None
        for limb in limbs:
            root = limb.root(self.body, self.tilt)
            for h in self.holds:
                if h is limb.hold or h is self.other(limb).hold or h.p.y <= self.held_y(limb) + 0.2 or (h.foot and limb.kind == "H"):
                    continue
                d = (h.p - root).len()
                if d < (2.6 if limb.kind == "H" else 2.2) and (best is None or d < best[0]):
                    best = (d, h, limb)
        return best[1:] if best else None

    def catch_up_options(self, limb, side_tol=0.4):
        other = self.other(limb)
        root = limb.root(self.body, self.tilt) + (V(0, 0.25) if limb.kind == "H" else V())
        ideal = self.body + V(limb.side * 0.7, HAND_UP if limb.kind == "H" else -FOOT_DOWN)
        limit = limb.reach * (0.97 if limb.kind == "H" else 0.92)
        return [(-(h.p - ideal).len(), id(h), h) for h in self.holds
                if h is not limb.hold and h not in limb.recent and (h is not other.hold or (limb.kind == "H" and h.r >= 0.3))
                and not (h.foot and limb.kind == "H")
                and h.p.y > self.held_y(limb) + 0.2 and (h.grip(limb.kind) - root).len() < limit
                and limb.side * (h.p.x - self.body.x) > -side_tol]

    # ---- 몸 움직임 ---------------------------------------------------------

    def anchors(self, kind):
        pts = []
        for limb in self.limbs.values():
            if limb.kind != kind:
                continue
            if limb.mode == "grip":
                pts.append(limb.pos)
            elif limb.mode == "move" and limb.goal is not None:
                pts.append(limb.goal)
            elif limb.mode in ("move", "follow") and limb.hold is not None and self.mode == "spring":
                pts.append(limb.hold.p)   # 초크·팔 털기로 잠깐 뗀 손은 돌아갈 홀드 기준 (몸이 남은 손 쪽으로 끌려가지 않는다)
        return pts

    def stance_target(self):
        """몸이 갈 자리. 손발을 옮기는 동작 단계 (무게 옮기기·일어서기) 가 정했으면 그 자리, 아니면 평소 자세."""
        hx = self.hang_target()
        if self.phase_target is not None:
            return self.phase_target(), hx[1]
        return hx

    def hang_target(self):
        """평소 자세: 팔을 쭉 펴고 뼈로 매달린다 (가장 높이 매달린 팔이 95% 펴지는 높이).
        무릎을 살짝 굽힌 채 (다리 90%) 다리가 닿는 데까지만 내려가고, 엉덩이는 발보다 0.3 위에 둔다. 잠깐 뗀 손은 돌아갈 홀드 기준.
        몸 x 는 손 가운데와 발 가운데의 중간 (빈 쪽 벽을 딛은 발도 발로 쳐서 몸이 손 아래로 옮겨 간다)."""
        hands = [l for l in self.limbs.values() if l.kind == "H" and l.hold is not None and l.mode in ("grip", "move", "follow")]
        feet = [l for l in self.limbs.values() if l.kind == "F" and l.mode == "grip"]
        if not hands:
            return V(self.body.x, self.body.y), self.body.x
        grips = [(l, l.hold.grip("H")) for l in hands]
        hx = sum(g.x for _, g in grips) / len(grips)
        stand = feet + [f for f in self.limbs.values()   # 벽을 딛은 발은 홀드로 옮기는 동안에도 (몸이 반대쪽으로 흔들리지 않게)
                        if f.kind == "F" and f.mode in ("follow", "move") and not f.tucked and f.free_style == "balance"]
        fx = sum(f.pos.x for f in stand) / len(stand) if stand else hx
        wind = math.sin(self.t * 1.3) * 0.16 * self.precip() ** 2   # 거센 바람에 몸이 흔들린다
        x = hx * 0.5 + fx * 0.5 + wind
        # 어깨·엉덩이는 몸이 기운 만큼 돌린 자리로 잰다 (안 그러면 기울어 올라간 쪽 팔이 덜 펴진다)
        y = max(g.y - l.root(V(), self.tilt).y
                - math.sqrt(max(0.01, (l.reach * 0.95) ** 2 - (g.x - x - l.root(V(), self.tilt).x) ** 2))
                for l, g in grips)
        for f in feet:
            off = f.root(V(), self.tilt)
            dx = f.pos.x - x - off.x
            y = min(y, f.pos.y - off.y + math.sqrt(max(0.01, (f.reach * 0.9) ** 2 - dx * dx)))   # 무릎은 살짝 굽혀 둔다
        if feet:
            y = max(y, max(f.pos.y for f in feet) - HIP[1] + 0.3)
        return V(x, y - self.crouch - 0.3 * self.duck), hx

    def reach_height(self, limb, hold, x):
        """손이 hold 에 편하게 (팔 88%) 닿는 몸 높이."""
        g = hold.grip("H")
        dx = g.x - x - limb.side * SHOULDER[0]
        return g.y - SHOULDER[1] - math.sqrt(max(0.01, (limb.reach * 0.88) ** 2 - dx * dx))

    def project(self, pos):
        """잡고 있는 손발이 닿는 거리 안으로 몸을 끌어당긴다. 홀드로 가는 손발은 동작 후반부터 조금씩 당긴다
        (잡는 순간 몸이 한 번에 끌려가지 않게)."""
        for _ in range(4):
            for limb in self.limbs.values():
                if limb.mode == "grip":
                    target, limit = limb.pos, limb.reach * 0.985
                elif limb.mode == "move" and limb.target is not None:
                    target = limb.target
                    limit = limb.reach * 0.985 + 2.5 * (1 - smooth(max(0.0, limb.progress - 0.4) / 0.6))
                else:
                    continue
                d = target - limb.root(pos, self.tilt)
                dist = d.len()
                if dist > limit:
                    pos = pos + d * ((dist - limit) / dist)
        return pos

    def move_body(self, dt):
        if self.mode == "script":
            return
        if self.mode == "spring":
            target, hx = self.stance_target()
            w, zeta = (9.0, 0.85) if self.phase_target is not None else (6.5, 0.72)   # 동작 단계는 또렷하게
            if self.phase_target is not None and self.k_now < 0.72:   # 전력 질주: 짧아진 동작을 몸이 따라가게
                w *= math.sqrt(0.72 / self.k_now)
            acc = (target - self.body) * (w * w) - self.vel * (2 * zeta * w)
            lh, rh = self.limbs["LH"], self.limbs["RH"]
            lean = 0.0
            if lh.hold is not None and rh.hold is not None:   # 한 손이 높으면 그쪽 어깨를 올려 두 팔로 고르게 매달린다
                lean = 0.45 * (rh.hold.p.y - lh.hold.p.y)    # (초크 등으로 잠깐 뗀 손도 돌아갈 홀드로 쳐서 몸이 들썩이지 않게)
            for hand, other in ((lh, rh), (rh, lh)):   # 손을 뻗느라 떼면 몸이 남은 손 아래로 쏠린다 (보통 빠르기, 플래그 중이면 덜, 쉬면서 털 때는 안)
                if hand.mode != "grip" and other.mode == "grip" and not self.resting and self.pace <= 1:
                    sway = SWAY * (other.pos.x - self.body.x) * (0.35 if self.flagging else 1.0)
                    lean += sway
            lean += sum(0.15 * f.side for f in (self.limbs["LF"], self.limbs["RF"]) if f.drop)   # 드롭니: 그쪽 어깨를 올린다
            tilt_goal = max(-0.5, min(0.5, -0.22 * (hx - self.body.x) + lean))
        else:
            acc = V(0, -GRAVITY)
            grips = [l.pos for l in self.limbs.values() if l.mode == "grip" and l.kind == "H"]
            gx = sum(p.x for p in grips) / len(grips) if grips else self.body.x
            tilt_goal = max(-0.6, min(0.6, -0.7 * (gx - self.body.x)))
        self.vel = self.vel + acc * dt
        if self.mode == "hang":
            self.vel = self.vel * math.exp(-0.9 * dt)
        if self.rope is not None:   # 로프가 팽팽해지면 늘어나며 잡아 준다 (출렁)
            anchor, length = self.rope
            d = self.harness() - anchor
            dist = d.len() or 1e-6
            if dist > length:
                n = d * (1 / dist)
                along = self.vel.x * n.x + self.vel.y * n.y
                self.vel = self.vel - n * ((110.0 * (dist - length) + 7.0 * along) * dt)
                if dist > length + 0.7:   # 로프는 0.7 넘게 늘어나지 않는다
                    self.body = self.body - n * (dist - length - 0.7)
                    if along > 0:
                        self.vel = self.vel - n * along
        old = self.body
        free = old + self.vel * dt
        moved = self.project(free)
        self.vel = self.vel + (moved - free) * (0.5 / dt)   # 끌려간 만큼의 속도는 반만 남긴다 (튀어 오르지 않게)
        self.body = moved
        self.tilt_vel += ((tilt_goal - self.tilt) * 60 - self.tilt_vel * 11) * dt
        self.tilt += self.tilt_vel * dt

    def move_free_limbs(self, dt):
        for limb in self.limbs.values():
            if limb.mode != "free":
                continue
            limb.vel = (limb.vel + V(0, -GRAVITY) * dt) * math.exp(-3.0 * dt)
            old = limb.pos
            pos = old + limb.vel * dt
            root = limb.root(self.body, self.tilt)
            d = pos - root
            dist = d.len() or 1e-6
            lo, hi = limb.reach * 0.6, limb.reach * 0.96
            if dist > hi or dist < lo:
                pos = root + d * (max(lo, min(hi, dist)) / dist)
            limb.vel = (pos - old) * (1 / dt)
            limb.pos = pos

    def move_effects(self, dt):
        self.squash_vel += ((1.0 - self.squash) * 160 - self.squash_vel * 13) * dt
        self.squash += self.squash_vel * dt
        if self.look_at is not None:   # 다음 홀드 쪽으로 고개를 든다·숙인다 (몸을 돌리는 건 look_ahead 가 정한다)
            dy = self.look_at.y - self.body.y
            look = -self.face * max(-0.3, min(0.3, 0.12 * dy))
        else:
            look = 0.0
        self.look_tilt += (look - self.look_tilt) * (1 - math.exp(-8 * dt))
        if self.night > 0.05 and not self.sleeping and not self.falling:   # 헤드램프: 보는 곳 (없으면 앞 위쪽) 을 부드럽게 따라 비춘다
            aim = self.look_at if self.look_at is not None else self.body + V(-self.face * 0.9, 1.4)
            self.lamp = aim if self.lamp is None else self.lamp + (aim - self.lamp) * (1 - math.exp(-6 * dt))
        else:
            self.lamp = None
        step = 7.0 * dt
        self.face_now = max(self.face_now - step, self.face) if self.face < self.face_now else min(self.face_now + step, self.face)
        self.hp_shown += max(-60 * dt, min(45 * dt, self.hp - self.hp_shown))
        for p in self.particles:
            p.age += dt
            p.p = p.p + p.v * dt
            if p.kind == "sweat":
                p.v = p.v + V(0, -GRAVITY * 0.35) * dt
            elif p.kind == "confetti":
                p.v = p.v * math.exp(-1.5 * dt) + V(0, -6.0) * dt
            elif p.kind == "rock":
                p.v = p.v + V(0, -GRAVITY * 0.6) * dt
            else:
                p.v = p.v * math.exp(-3.0 * dt) + V(0, -0.15) * dt
        self.particles = [p for p in self.particles if p.age < p.life]
        for f in self.texts:
            f.age += dt
            if f.style == "z":
                f.p = f.p + V(0.25 * math.cos(f.age * 2.6) * dt - 0.12 * self.face * dt, 0.5 * dt)
            else:
                f.p = f.p + V(0, 0.9 * dt)
        self.texts = [f for f in self.texts if f.age < f.life]
        if self.hp <= 60 and not self.sleeping:
            self.sweat_t -= dt
            if self.sweat_t <= 0:
                self.sweat_t = 1.6 if self.hp > 30 else 0.8
                side = self.wrng.choice((-1, 1))
                self.particles.append(Particle(self.body + V(side * 0.7, 0.75).rot(self.tilt),
                                               V(side * 0.6, 0.6), 1.1, 0.12, "sweat"))
        # 눈 깜빡임과 잠꼬대
        if self.blink_age is not None:
            self.blink_age += dt
            if self.blink_age > 0.16:
                self.blink_age = None
        elif self.lids < 0.5:
            self.blink_t -= dt
            if self.blink_t <= 0:
                self.blink_t, self.blink_age = self.wrng.uniform(2.2, 5.0), 0.0
        if self.sleeping:
            self.z_t -= dt
            if self.z_t <= 0:
                self.z_t = 1.3
                self.texts.append(Float(self.body + V(-0.5 * self.face, 1.0), self.wrng.choice(("z", "Z")), "z"))

    def move_milestones(self, dt):
        """기록을 넘으면 말풍선과 색종이 (지난 주 기록이면 그 자리 바위에 자국도). 같은 기록은 한 번만."""
        if self.banner is not None:
            self.banner[1] += dt
            if self.banner[1] > 3.5:
                self.banner = None
        alt = self.altitude()
        if alt <= self.peak:
            return
        self.peak = alt
        quiet = self.scaled() and self.week_target() - self.pct_shown > 1.0   # 멀리서 따라붙는 중이면 조용히 넘는다
        record = False   # 같은 때 넘은 기록들 중에서는 지난 주 기록 말풍선을 남긴다
        while self.passed < len(self.landmarks) and alt >= self.landmarks[self.passed][0]:
            height, name, text, *carve = self.landmarks[self.passed]
            self.passed += 1
            if quiet:
                continue
            if carve or not record:
                self.banner = [text, 0.0]
            self.banners.append(height)
            self.confetti(26)
            if carve:   # 지난 주 기록은 닿은 자리 바위에 새긴다 (글자는 몸 반대쪽, 가까운 자국이 그쪽에 있으면 다른 쪽)
                record = True
                side = 1 if self.body.x < 0 else -1
                if any(abs(y - self.body.y) < CARVE_GAP and other == side for y, other, *_ in self.carved):
                    side = -side
                self.carved.append((self.body.y, side, *carve[0]))

    def confetti(self, n):
        for _ in range(n):
            a = self.wrng.uniform(0.35, math.pi - 0.35)
            speed = self.wrng.uniform(2.5, 4.5)
            self.particles.append(Particle(self.body + V(0, 1.2), V(math.cos(a) * speed, math.sin(a) * speed),
                                           1.6, 0.1, "confetti", self.wrng.choice(CONFETTI)))

    def move_events(self, dt):
        """추락 이벤트: 비·눈에 발 미끄러짐, 체력이 낮으면 손 미끄러짐, 폭풍우에 낙석."""
        rng = self.wrng
        for rock in self.rocks:
            rock[1] = rock[1] + V(0, -GRAVITY * 0.3) * dt
            rock[0] = rock[0] + rock[1] * dt
            rock[2] += rock[3] * dt
            if not rock[5] and rock[0].y < self.body.y + 1.0:   # 바위 턱에 튕겨 옆으로
                rock[5] = True
                side = 1 if rock[0].x > self.body.x else -1
                rock[1] = V(side * rng.uniform(1.5, 2.5), 1.5)
                self.puff(rock[0], n=5, speed=1.0, size=0.12, life=0.6)
        self.rocks = [r for r in self.rocks if r[0].y > self.cam - 10]
        near = any(0 < r[0].y - self.body.y < 3.5 and not r[5] for r in self.rocks)
        if near:
            self.duck_t = 0.5
        elif self.duck_t > 0:
            self.duck_t -= dt
            if self.duck_t <= 0:
                self.texts.append(Float(self.body + V(0, 1.4), "휴!"))
        self.duck += ((1.0 if self.duck_t > 0 else 0.0) - self.duck) * (1 - math.exp(-10 * dt))
        if not self.events or self.sleeping or self.work <= 0 or self.resting or self.rope is not None:
            return
        if self.weather > 0.85 and self.precip() > 0:   # 폭풍우: 낙석
            self.rock_t -= dt
            if self.rock_t <= 0:
                self.rock_t = rng.uniform(15, 30)
                side = rng.choice((-1, 1))
                x = max(-WALL_HALF, min(WALL_HALF, self.body.x + side * rng.uniform(0.9, 1.4)))
                self.rocks.append([V(x, self.cam + VIEW_TOP + 1), V(0, -4.0), 0.0, rng.uniform(-6, 6), rng.uniform(0.25, 0.4), False])
        wet = self.precip() > 0.3
        if (wet or self.hp <= 20) and not self.requests:
            self.slip_t -= dt
            if self.slip_t <= 0:
                self.slip_t = rng.uniform(25, 45)
                self.request("slip_hand" if self.hp <= 20 and (not wet or rng.random() < 0.5) else "slip_foot")

    def move_weather(self, dt):
        target = WEATHER[max(0, min(3, self.work))] if not self.sleeping or self.nap else 0.0   # 낮잠 중에는 날씨 그대로
        self.weather += (target - self.weather) * (1 - math.exp(-0.6 * dt))
        amount = self.precip()
        if amount > 0 and not self.precipitating:   # 비·눈이 새로 시작될 때마다 무작위 (내리는 동안은 그대로)
            self.weather_kind = self.wrng.choice(("rain", "snow"))
        self.precipitating = amount > 0
        rain = self.weather_kind == "rain"
        want = round((90 if rain else 70) * amount * self.drop_scale)
        wind = (0.35 if rain else 0.55) * amount ** 1.5
        rng = self.wrng
        if self.drops_kind != self.weather_kind:   # 비 ↔ 눈: 화면에 고루 다시 뿌린다
            self.drops_kind = self.weather_kind
            self.drops = [self.new_drop(rain, rng.random()) for _ in range(want)]
        kept = []
        for d in self.drops:
            d[1] += d[2] * dt
            d[0] = (d[0] + (wind + (0 if rain else 0.04 * math.sin(self.t * 2 + d[4]))) * dt) % 1.0
            if d[1] > 1.05:
                if len(kept) >= want:
                    continue
                d[0], d[1] = rng.random(), rng.uniform(-0.1, 0.0)
            kept.append(d)
        while len(kept) < want:
            kept.append(self.new_drop(rain, rng.uniform(-0.15, 0.0)))
        self.drops = kept
        self.wind = wind
        # 폭풍우에는 가끔 번개
        self.flash = max(0.0, self.flash - dt * 5)
        if self.flash2 is not None:
            self.flash2 -= dt
            if self.flash2 <= 0:
                self.flash, self.flash2 = 0.8, None
        if rain and self.weather > 0.88:
            self.flash_t -= dt
            if self.flash_t <= 0:
                self.flash_t, self.flash, self.flash2 = rng.uniform(5.0, 11.0), 1.0, 0.14
        snowing = not rain and amount > 0
        for h in self.holds:
            h.snow = min(1.0, h.snow + dt * 0.12 * amount) if snowing else max(0.0, h.snow - dt * 0.04)

    def new_drop(self, rain, y):
        """빗방울·눈송이 [x, y, 떨어지는 빠르기, 크기, 흔들림 위상] (창 높이에 대한 비율)."""
        rng = self.wrng
        speed = rng.uniform(1.6, 2.2) if rain else rng.uniform(0.1, 0.22)
        return [rng.random(), y, speed, rng.uniform(0.6, 1.0), rng.uniform(0, 6.3)]

    def puff(self, at, n=6, speed=1.2, size=0.16, life=0.8):
        for _ in range(n):
            a = self.wrng.uniform(0, 2 * math.pi)
            s = self.wrng.uniform(0.3, 1.0) * speed
            self.particles.append(Particle(at, V(math.cos(a) * s, math.sin(a) * s * 0.7 + 0.2),
                                           life * self.wrng.uniform(0.7, 1.2), size * self.wrng.uniform(0.7, 1.3)))

    def blow_puff(self, name):
        hand = self.limbs[name].pos
        for _ in range(7):
            self.particles.append(Particle(hand, V(-self.face * self.wrng.uniform(0.8, 1.8), self.wrng.uniform(-0.3, 0.6)),
                                           self.wrng.uniform(0.6, 1.0), self.wrng.uniform(0.12, 0.2)))
        self.squash_vel -= 1.2

    # ---- 몸에 붙은 자리 -----------------------------------------------------

    def harness(self):
        """하네스 (로프를 묶은 곳)."""
        return self.body + V(0, -0.62).rot(self.tilt)

    def bag_mouth(self, depth=0.0):
        """허리 뒤 초크백 입구 (보는 쪽의 반대편)."""
        return self.body + V(0.46 * self.face, -0.78 - depth).rot(self.tilt)

    def blow_point(self):
        return self.body + V(-1.05 * self.face, -0.05).rot(self.tilt)

    def tucked(self, name):
        """침낭 안에 넣은 손발 자리."""
        side, kind = LIMBS[name]
        offset = V(side * 0.55, -0.35) if kind == "H" else V(side * 0.35, -0.85)

        def point():
            return self.body + offset.rot(self.tilt)

        point.tucked = True
        return point

    # ---- 동작 (제너레이터: 한 프레임마다 dt 를 받는다) ---------------------------

    def brain(self):
        yield
        last, spins = None, 0
        while True:
            spins = spins + 1 if self.t == last else 0   # 한 프레임도 쓰지 않고 판단만 되풀이하면 (앱이 멈춘다) 잠깐 쉰다
            last = self.t
            if spins > 20:
                yield from self.wait(0.2, stoppable=False)
                continue
            self.charging = True
            if self.requests:
                name, args = self.requests.pop(0)
                if (self.sleeping and name not in AWAKE) or (self.grounded and name not in GROUND_OK):
                    continue   # 잠들기 전·땅에 떨어지기 전에 받아 둔 동작은 버린다
                yield from getattr(self, name)(*args)
                continue
            if self.grounded:   # 벽이 끝나 땅에서 기다린다
                yield from self.ground_wait()
                continue
            if self.sleeping:
                if self.nap and self.climb_ready():   # 낮잠: 토큰이 모였으면 깨어나 다시 오른다
                    yield from self.wake()
                    continue
                yield from self.wait(0.3)
                continue
            if not self.has_climb():
                yield from self.rest()
                continue
            bolt = self.bolt_to_clip()
            if bolt is not None:
                yield from self.clip(bolt)
                continue
            self.idle_since = None
            jug = self.crux_ready()
            if jug is not None:
                need = jug.p.y - max(self.held_y(self.limbs[n]) for n in ("LH", "RH"))
                if jug.crux_tries == 1 and self.room() < need:   # 쌓인 높이가 끝 홀드까지 안 되면 크럭스 앞에서 토큰을
                    # 기다린다 (놓쳐 떨어졌다 다시 뛸 때는 처음 뛴 높이에 이미 썼다)
                    self.parked, self.wait_for = True, need
                    continue
                yield from self.crux(jug)
                continue
            sprint = DYNO_MIX.get(self.steady_pace(), 0.0) if self.tokens_on and self.hp > 30 else 0.0   # 다시 오를 때는
            # 섞지 않는다 (크럭스 끝 홀드로 바로 뛰어 크럭스를 건너뛰게 된다)
            if sprint and self.rng.random() < sprint and self.dyno_jug(near=True) is not None:
                yield from self.dyno(show=False)   # 많이 쌓였으면 다이노를 섞어 크게 오른다
            else:
                yield from self.climb_move()
            if self.hp <= 60 and self.t >= self.shake_t:   # 체력이 줄었으면 오르다가 가끔 팔을 턴다
                if self.shake_t:
                    yield from self.shake()
                self.shake_t = self.t + self.wrng.uniform(*SHAKE_EVERY)
            self.look_ahead()
            if self.pace == 1 and not self.free_feet():   # 보통 빠르기: 몇 동작 이어 하고 둘러보며 한숨 돌린다
                self.burst_left -= 1
                if self.burst_left <= 0:
                    self.burst_left = self.rng.randint(3, 5)
                    yield from self.scan_ahead()
                    continue
            yield from self.wait(self.pause_time(), until=lambda: not self.has_climb())

    def look_ahead(self):
        """가는 쪽을 본다: 길이 한쪽으로 FACE_HOLD 초 넘게 이어질 때만 몸을 돌린다 (손발을 옮길 때마다 뒤집지 않게)."""
        ahead = self.route.center(self.body.y + 2.5) - self.body.x
        want = (-1 if ahead > 0 else 1) if abs(ahead) > 1.0 else self.face
        if want == self.face:
            self.face_want = None
        elif self.face_want != want:
            self.face_want, self.face_since = want, self.t
        elif self.t - self.face_since > FACE_HOLD:
            self.face, self.face_want = want, None

    def scan_ahead(self):
        """한숨 돌리며 위쪽 홀드 2~3곳을 차례로 본다 (모두 0.6~1.5초, 쉴 일이 생기면 그만둔다).
        쏠린 채로는 쉬지 않고 균형을 잡은 다음 동작 뒤로 미룬다."""
        if self.leaning():
            self.burst_left = 1
            return
        above = sorted((h for h in self.holds if 1.0 < h.p.y - self.body.y < 3.5), key=lambda h: h.p.y)
        if len(above) < 2:
            return
        picks = sorted(self.rng.sample(above, min(len(above), self.rng.randint(2, 3))), key=lambda h: h.p.y)
        total = self.rng.uniform(0.6, 1.5)
        start = self.t
        for hold in picks:
            self.look_at = hold.p
            yield from self.wait(total / len(picks), until=lambda: not self.has_climb())
        self.look_at = None
        self.pause_log.append((round(self.t - start, 2), len(picks)))

    def pause_time(self):
        lo, hi = PACE_PAUSE[max(1, self.pace)]
        pause = self.rng.uniform(lo, hi) + (0.35 if self.hp <= 30 else 0.0)
        return pause * 0.3 if self.leaning() else pause   # 쏠렸으면 숨 고르지 않고 이어 간다

    def wait(self, seconds, stoppable=True, until=None):
        t = 0.0
        while t < seconds:
            if (stoppable and self.requests) or (until is not None and until()):
                return
            t += yield

    def tween(self, obj, attr, a, b, dur):
        t = 0.0
        while t < dur:
            t += yield
            setattr(obj, attr, a + (b - a) * smooth(min(1.0, t / dur)))

    def move_limb(self, name, goal, dur, arc=0.14, support=True, ease=smooth):
        """손발 하나를 goal (Hold, 점, 또는 점을 돌려주는 함수) 로 옮긴다. 홀드면 잡고, 함수면 그 자리에 붙어 있는다."""
        limb = self.limbs[name]
        start = limb.pos
        hold = goal if isinstance(goal, Hold) else None

        def end():
            if hold is not None:
                return self.grip_point(limb, hold)
            return goal() if callable(goal) else goal

        limb.mode, limb.tucked, limb.drop = "move", False, False
        limb.goal = end() if hold is not None and support else None
        limb.target, limb.progress = (end() if hold is not None else None), 0.0
        t = 0.0
        while t < dur:
            t += yield
            k = min(1.0, t / dur)
            bump = math.sin(math.pi * k)
            limb.pos = start.lerp(end(), ease(k)) + V(limb.side * arc, arc * 0.6) * bump
            limb.progress = k
        limb.goal = limb.target = None
        if hold is not None:
            self.grab(limb, hold)
        elif callable(goal):
            limb.mode, limb.follow = "follow", goal
        else:
            limb.mode, limb.follow = "follow", (lambda p=end(): p)

    def grab(self, limb, hold):
        new = hold is not limb.hold   # 새 홀드 (초크·털기 뒤 같은 홀드를 다시 잡는 게 아니라)
        if new and limb.hold is not None:
            limb.recent.append(limb.hold)
        limb.mode, limb.hold = "grip", hold
        limb.free_style = limb.spot_hold = None
        limb.pos = self.grip_point(limb, hold)
        self.grabs.append((self.t, limb.name, hold))
        if hold.snow > 0.3:   # 눈을 털어 낸다
            self.puff(limb.pos, n=3, speed=0.6, size=0.1, life=0.5)
            hold.snow = 0.0
        if limb.kind == "H":
            hold.chalk = min(1.0, hold.chalk + 0.3)
            if self.wrng.random() < 0.35:
                self.puff(limb.pos, n=2, speed=0.5, size=0.1, life=0.5)
            shared = self.other(limb).hold is hold
            if (self.events and new and not self.resting and not hold.big and not shared and self.rope is None
                    and self.wrng.random() < BREAK_CHANCE):
                self.requests.insert(0, ("break_hold", (limb, hold)))   # 오르며 새로 잡은 사암 홀드가 드물게 부서진다

    def move_all(self, goals, dur, body_to=None, tilt_to=None, hop=0.0):
        """여러 손발 (과 몸) 을 한꺼번에 옮긴다. goals: {이름: Hold 또는 점을 돌려주는 함수}."""
        starts = {n: self.limbs[n].pos for n in goals}
        for n in goals:
            self.limbs[n].mode, self.limbs[n].goal, self.limbs[n].tucked = "move", None, False
        b0, a0 = self.body, self.tilt
        if body_to is not None:
            self.mode = "script"
        t = 0.0
        while t < dur:
            dt = yield
            t += dt
            k = smooth(min(1.0, t / dur))
            if body_to is not None:
                old = self.body
                self.body = b0.lerp(body_to(), k) + V(0, hop * math.sin(math.pi * k))
                self.vel = (self.body - old) * (1 / dt)
            if tilt_to is not None:
                self.tilt, self.tilt_vel = a0 + (tilt_to - a0) * k, 0.0
            for n, goal in goals.items():
                limb = self.limbs[n]
                end = self.grip_point(limb, goal) if isinstance(goal, Hold) else goal()
                limb.pos = starts[n].lerp(end, k)
        for n, goal in goals.items():
            limb = self.limbs[n]
            if isinstance(goal, Hold):
                self.grab(limb, goal)
            else:
                limb.mode, limb.follow = "follow", goal
                limb.tucked = getattr(goal, "tucked", False)

    def climb_move(self):
        move = self.choose_move()
        if move is None:
            self.parked = True
            return
        limb, hold = move
        self.moves.append(limb.name)
        top = max(self.held_y(self.limbs[n]) for n in ("LH", "RH"))
        if any(h.crux and h.crux_from - 0.8 < top < h.p.y for h in self.holds):
            self.moves_since_crux += 1   # 크럭스 아래 끝에서 맴돈다
        else:
            self.moves_since_crux = 0
        self.move_start = self.body.x   # 이 동작을 시작할 때 몸 x (검사용)
        self.k_now = PACE_DUR[max(1, self.pace)]
        k = self.k_now * (1.4 if self.hp <= 30 else 1.0)
        try:
            if limb.kind == "H":
                yield from self.reach_move(limb, hold, k)
            else:
                yield from self.foot_move(limb, hold, k)
        finally:
            self.phase_target = self.look_at = None
        total = sum(self.held_y(l) for l in self.limbs.values())   # 손발 높이의 합이 최고 기록을 넘어야 나아간 것
        if total > self.best_top + 0.05:
            self.best_top, self.stall = total, 0
        else:
            self.stall += 1   # 옆으로만 옮기거나 오르내리며 맴돌면 쌓인다

    def settle(self, target, least, most, left=0.0):
        """몸이 target 에 거의 닿을 때까지 (least~most 초) 기다린다. left 면 처음 거리의 그만큼 남았을 때 끝낸다
        (일어서는 도중에 손을 뻗기 시작해 동작이 이어진다)."""
        t, first = 0.0, (target - self.body).len()
        while t < most and (t < least or (target - self.body).len() > max(0.06, first * left)):
            t += yield

    def reach_move(self, limb, hold, k):
        """손 뻗기: 보기 → 디딜 발 위로 무게 옮기기 → 다리로 일어서기 → 뻗어 잡기 → 고쳐 쥐기.
        뻗는 동안 대개 한 발은 떼어 (삼지점) 플래그나 벽 딛기로 균형을 잡고, 잡은 뒤에도 그대로 두었다가
        다음 차례에 홀드로 옮긴다 (choose_move 가 뗀 발부터 고른다). 가까운 홀드는 천천히 다가가 멈추듯 잡고 (정적),
        먼 홀드는 살짝 가라앉았다 던지듯 잡은 뒤 잠깐 처진다 (데드포인트). 옆으로 멀리 뻗는데 같은 쪽 발이 높으면
        그 무릎을 안·아래로 떨어뜨려 몸을 틀고 조금 더 멀리 닿는다 (드롭니). 잡고 나면 두 손이 한쪽 위, 두 발이 반대쪽 아래로
        쏠리는 자세면 디딜 발에 무게를 조금만 싣고 빈 쪽 발을 먼저 손 아래쪽에 내려 둔 뒤 두 발 사이로 일어선다."""
        self.look_at = hold.p
        support, free, style = self.reach_stance(limb, hold)
        shift = None
        if style == "balance":
            base = self.hang_target()[0]
            x0 = self.body.x
            lean = V(x0 + (support.pos.x - x0) * 0.25, base.y)
            self.phase_target = lambda: lean
            yield from self.settle(lean, 0.1 * k, 0.3 * k)
            spot = self.free_spot(free, style, sum(self.hands_after(limb, hold)) / 2)
            yield from self.move_limb(free.name, spot, 0.26 * k, arc=0.08)
            free.free_style, free.kept = style, 0
            shift = V(x0 + ((support.pos.x + free.pos.x) / 2 - x0) * 0.6, base.y)
            free = None
        dist = (hold.p - limb.pos).len()
        kind = ("static" if dist < 1.0 else "dynamic" if dist >= 1.4 else "normal") if self.pace <= 1 else "normal"   # 빨리 오를 때는 다 날렵하게
        same = self.limbs["RF" if limb.side > 0 else "LF"]
        wide = limb.side * (hold.p.x - self.body.x) > 0.8
        if wide and support is same and same.pos.y > self.body.y + HIP[1] - 0.6 and self.pace <= 1:   # 보통 빠르기에서만
            same.drop = self.rng.random() < DROP_KNEE
            self.drop_log.append(same.drop)
        base = self.hang_target()[0]
        if shift is None:
            x0 = self.body.x
            dx = ((support.pos.x if support.mode == "grip" else x0) - x0) * 0.55
            if style is None and -limb.side * dx < 0.15:
                dx = -limb.side * 0.15   # 적어도 조금은 반대쪽으로
            shift = V(x0 + dx, base.y)
        self.phase_target = lambda: shift
        yield from self.settle(shift, 0.16 * k, 0.4 * k)
        if free is not None:
            yield from self.move_limb(free.name, self.free_spot(free, style), 0.26 * k, arc=0.08)
            free.free_style, free.kept = style, 0
        sink, preload = 0.0, 1.0
        if kind == "dynamic":   # 팔을 편 채 살짝 가라앉으며 몸을 웅크려 힘을 모았다가
            self.vel = V(self.vel.x, min(0.0, self.vel.y))   # 올라가던 힘은 멈추고
            low = V(shift.x, min(base.y, self.body.y) - 0.3)
            self.phase_target = lambda: low
            top_before = self.body.y
            self.squash_vel -= 2.2
            self.squash_min = self.squash
            yield from self.settle(low, 0.15 * k, 0.25 * k)
            sink = top_before - self.body.y
            preload = self.squash_min
        stand = V(shift.x + (0.12 * limb.side if same.drop else 0.0),
                  max(base.y, min(self.reach_height(limb, hold, shift.x), base.y + 1.2)))
        self.phase_target = lambda: stand
        yield from self.settle(stand, 0.1 * k, (0.25 + 0.4 * max(0.0, stand.y - self.body.y)) * k, left=0.3)
        if kind == "static":   # 천천히 다가가 멈추듯
            yield from self.move_limb(limb.name, hold, (0.3 + 0.12 * dist) * k, arc=0.04, support=False, ease=smooth)
        elif kind == "dynamic":   # 던지듯 (올라가는 힘이 줄어들 때 손이 떠나 정점에서 잡는다)
            yield from self.move_limb(limb.name, hold, (0.16 + 0.08 * dist) * k, arc=0.08, support=False, ease=ease_out)
        else:
            yield from self.move_limb(limb.name, hold, (0.2 + 0.1 * dist) * k, arc=0.06, support=False, ease=ease_out)
        limb.wiggle = 0.18
        if any(f.mode == "follow" for f in (self.limbs["LF"], self.limbs["RF"])):
            self.free_since = self.t
        sag = 0.0
        self.look_at = None
        catch = 1.0
        if kind == "dynamic":   # 정점에서 잡아 (올라가던 힘이 멈춘다) 무게가 실리며 몸통이 눌리고 뻗은 쪽으로 흔들렸다가
            self.vel = V(self.vel.x, min(0.0, self.vel.y))
            self.squash_vel -= 2.0
            self.tilt_vel += 1.6 * limb.side
            self.squash_min = self.squash
            hang = self.hang_target()[0]
            dip = V(hang.x, self.body.y - 0.12)
            self.phase_target = lambda: dip
            top_after = self.body.y
            yield from self.settle(dip, 0.12 * k, 0.2 * k)
            sag = top_after - self.body.y
            catch = self.squash_min
        self.reach_log.append((kind, round(sink, 3), round(sag, 3), round(preload, 3), round(catch, 3)))
        same.drop = False          # 드롭니는 손을 잡을 때까지만
        self.phase_target = None   # 잡고 나면 팔을 펴며 내려앉아 한숨 돌린다
        yield from self.settle(self.hang_target()[0], 0.1 * k, 0.35 * k)

    def reach_stance(self, limb, hold):
        """손을 뻗는 동안 (디딜 발, 뗄 발 또는 None, 뗀 발 자세). 뻗는 쪽 발이 몸 밑 가까이 잘 딛고 있으면 그 발로 딛고
        반대 발을 떼고 (대각선으로 버틴다. 옆으로 멀리 뻗으면 반대쪽으로 플래그), 아니면 반대 발로 딛고 뻗는 쪽 발을
        뗀다 (같은 쪽 손발만 남아 몸이 열리려 하므로 멀리 뻗으면 뒤로 엇갈려 백플래그). 가깝게 뻗으면 뗀 발은 벽을 딛는다.
        잡고 나면 쏠리는 자세면 (두 손이 한쪽 위, 두 발이 반대쪽 아래) 빈 쪽 발을 떼어 손 아래쪽에 둔다 (balance)."""
        feet = {f.side: f for f in (self.limbs["LF"], self.limbs["RF"])}
        same, opp = feet[limb.side], feet[-limb.side]
        if same.mode != "grip" or opp.mode != "grip":   # 이미 한 발이 떨어져 있다
            return (same if same.mode == "grip" else opp), None, None
        hands_x = self.hands_after(limb, hold)
        after = 0.5 * sum(hands_x) / 2 + 0.5 * (same.pos.x + opp.pos.x) / 2   # 잡고 나서 몸이 갈 x (hang_target)
        s = self.lean_side(hands_x, after) or self.lean_side(hands_x)
        if s:
            self.lean_log.append("reach")
            return feet[-s], feet[s], "balance"
        if self.rng.random() >= FREE_FOOT:
            return opp, None, None
        wide = limb.side * (hold.p.x - self.body.x) > 0.6
        placed = (abs(same.pos.x - (self.body.x + limb.side * 0.3)) < 1.0
                  and (same.pos - same.root(self.body, self.tilt)).len() < same.reach * 0.9)
        if placed:
            return same, opp, ("flag" if wide else "smear")
        return opp, same, ("backflag" if wide else "smear")

    def free_spot(self, foot, style, hx=None):
        """뗀 발이 갈 자리 (점을 돌려주는 함수). 플래그는 다리를 바깥으로 뻗고, 백플래그는 디딘 다리 뒤로 엇갈리고,
        벽 딛기는 원래 홀드 조금 아래 벽에 발을 댄다 (몸이 멀어지면 다리가 닿는 데까지만). 빈 쪽 (balance, 발 쪽) 은
        다리가 닿는 홀드가 있으면 그 위에 올려 두고 (다음 차례에 딛는다), 없으면 손 가운데 (hx) 아래쪽 벽을 딛는다
        (몸이 그쪽으로 옮겨 가도 몸 바깥에 남게)."""
        if style == "flag":
            return lambda: foot.root(self.body, self.tilt) + V(foot.side * 1.2, -0.8)
        if style == "backflag":
            return lambda: foot.root(self.body, self.tilt) + V(-foot.side * 0.95, -1.3)
        if style == "balance":
            s = foot.side
            hx = sum(self.hands_x()) / 2 if hx is None else hx
            options = self.fill_options(foot, s)
            foot.spot_hold = max(options)[2] if options else None
            if foot.spot_hold is not None:
                spot = foot.spot_hold.grip("F")
            else:
                x = self.body.x + s * min(1.1, max(0.55, s * (hx - self.body.x) - 0.1))
                spot = V(max(-WALL_HALF + 0.2, min(WALL_HALF - 0.2, x)), self.body.y - 1.55)
        else:
            spot = foot.pos + V(foot.side * 0.12, -0.3)

        def smear():
            root = foot.root(self.body, self.tilt)
            d = spot - root
            dist, limit = d.len() or 1e-6, foot.reach * 0.95
            return spot if dist <= limit else root + d * (limit / dist)
        return smear

    def free_feet(self):
        """홀드에서 뗀 발 (플래그·벽 딛기)."""
        return [f for f in (self.limbs["LF"], self.limbs["RF"]) if f.mode == "follow" and not f.tucked]

    def free_foot_move(self, foot, hands):
        """뗀 발로 할 다음 동작 (손발, 홀드). 더 높은 홀드가 있으면 올라가 딛고, 없으면 디딘 발 옆에 모아 (합발) 다음에
        그 발을 올리고, 그것도 안 되면 뗀 발은 그대로 둔 채 다른 손을 뻗는다 (두 번까지). 다 안 되면 원래 높이 근처로.
        빈 쪽 벽을 딛은 발은 올려 둔 홀드·빈 쪽 홀드부터 찾고, 손이 아직 그쪽 위면 합발보다 벽을 딛은 채 손 뻗기와 조건을
        푼 빈 쪽 홀드가 먼저다. 손이 이 발 쪽으로 쏠렸는데 다른 자세로 뗀 발이 반대쪽에 떠 있으면 빈 쪽 홀드, 위쪽 홀드,
        그것도 없으면 빈 쪽 벽으로 옮긴다 (홀드 자리에 None)."""
        base, s = foot.hold.p.y, foot.side
        wall = foot.free_style == "balance"
        stray = not wall and self.lean_side() == s and s * (foot.pos.x - self.body.x) <= LEAN_SIDE
        hold = foot.spot_hold if wall else None
        if (hold is not None and hold in self.holds and hold is not self.other(foot).hold
                and (hold.grip("F") - foot.root(self.body, self.tilt)).len() < foot.reach * 0.95):
            self.free_log.append("spot")
            return foot, hold
        if wall or stray:
            options = self.fill_options(foot, s) or (stray and self.fill_options(foot, s, relax=1))
            if options:
                self.free_log.append("fill")
                return foot, max(options)[2]
        for relax in (0, 1):
            up = [o for o in self.foot_options(foot, relax) if o[2].p.y >= base + 0.15]
            if up:
                self.free_log.append("up")
                return foot, max(up)[2]
        if stray and self.hands_can_move():   # 손이 갈 데가 없으면 벽을 딛어도 다음 차례에 돌아온다
            self.free_log.append("wall")
            return foot, None
        support = self.other(foot)
        over = wall and all(s * (x - self.body.x) > LEAN_SIDE for x in self.hands_x())   # 손이 아직 빈 쪽 위
        if over:
            move = self.keep_free(foot, support, hands)
            if move is not None:
                return move
            options = self.fill_options(foot, s, relax=1)
            if options:
                self.free_log.append("fill")
                return foot, max(options)[2]
        if support.mode == "grip" and support.hold is not None:
            spot = support.hold.grip("F", foot.side)
            if ((spot - foot.root(self.body, self.tilt)).len() < foot.reach * 0.92
                    and (self.foot_options(support, 0) or self.foot_options(support, 1))):
                self.free_log.append("match")
                self.switch_foot = support
                return foot, support.hold
        move = None if over else self.keep_free(foot, support, hands)
        if move is not None:
            return move
        hold = self.free_foot_hold(foot)
        if hold is not None:
            self.free_log.append("return")
            return foot, hold
        return None

    def keep_free(self, foot, support, hands):
        """뗀 발은 그대로 둔 채 다른 손을 뻗는 동작 (두 번까지, 방금 뻗은 손은 빼고). 없으면 None."""
        if foot.kept < 2 and support.mode == "grip" and all(h.mode == "grip" for h in hands):
            last = self.limbs.get(self.moves[-1]) if self.moves else None
            for relax in (0, 1):
                for hand in hands:
                    options = hand is not last and self.hand_options(hand, relax)   # 방금 뻗은 손을 또 뻗지는 않는다
                    if options:
                        foot.kept += 1
                        self.free_log.append("keep")
                        return hand, self.pick_ahead(hand, options)
        return None

    def free_foot_hold(self, foot):
        """뗀 발이 디딜 홀드: 발 옮기기 조건을 차례로 풀고, 그래도 없으면 닿는 홀드 중 원래 높이에 가장 가까운 곳 (없으면 None)."""
        for relax in (0, 1, 2):
            options = self.foot_options(foot, relax)
            if options:
                return max(options)[2]
        hip = foot.root(self.body, self.tilt)
        base = foot.hold.p.y if foot.hold is not None else foot.pos.y   # 원래 디뎠던 높이에 가까운 곳 (크게 내려가지 않게)
        near = [h for h in self.holds if h is not self.other(foot).hold and h.p.y < self.body.y
                and (h.grip("F") - hip).len() < foot.reach * 0.95]
        ceiling = self.foot_ceiling() if self.pace <= 2 else 1e9
        near = [h for h in near if h.p.y <= ceiling] or near      # 너무 높은 곳은 다른 데가 없을 때만 (전력 질주는 높이 딛어도 된다)
        near = [h for h in near if h.p.y >= base - 0.3] or near   # 0.3 넘게 낮은 곳도 다른 데가 없을 때만
        return min(near, key=lambda h: abs(h.p.y - base) + 0.3 * abs(h.p.x - foot.pos.x)) if near else None

    def foot_move(self, limb, hold, k):
        """발 옮기기: 아래를 보고 무게를 다른 발로 넘긴 뒤, 무릎을 들어 홀드 위에서 잠깐 겨눴다가 콕 놓는다.
        뗀 발 (플래그·벽 딛기) 이면 손을 잡고 FREE_HOLD 초는 그대로 균형을 잡은 뒤에 옮긴다. hold 가 None 이면 빈 쪽 벽을 딛는다."""
        if hold is None:
            yield from self.wall_step(limb, k)
            return
        free = limb.mode == "follow"
        if free:   # 빠르게 오를 때는 균형 잡는 시간도 짧게
            yield from self.wait(self.free_since + FREE_HOLD * k - self.t, stoppable=False)
        self.look_at = hold.p
        if not free:   # 뗀 발이면 무게는 이미 다른 발에 실려 있다
            other = self.other(limb)
            base = self.hang_target()[0]
            shift = V(base.x + ((other.pos.x if other.mode == "grip" else base.x) - base.x) * 0.45, base.y)
            self.phase_target = lambda: shift
            yield from self.settle(shift, 0.1 * k, 0.3 * k)
        above = lambda: self.grip_point(limb, hold) + V(limb.side * 0.05, 0.28)
        dist = (hold.p - limb.pos).len()
        yield from self.move_limb(limb.name, above, (0.18 + 0.1 * dist + (0.1 if free else 0.0)) * k, arc=0.12)
        yield from self.wait(0.1 * k, stoppable=False)
        yield from self.move_limb(limb.name, hold, 0.09 * k, arc=0.0)

    def wall_step(self, foot, k):
        """빈 쪽 벽 딛기 (쏠렸는데 빈 쪽에 디딜 홀드가 없을 때): 디딘 발로 무게를 조금 옮기고, foot 을 떼어 손 가운데
        아래쪽 벽을 딛은 뒤 두 발 사이로 몸을 옮긴다 (hang_target 이 벽을 딛은 발도 발로 친다)."""
        other = self.other(foot)
        base = self.hang_target()[0]
        lean = V(base.x + (other.pos.x - base.x) * 0.25, base.y)
        self.phase_target = lambda: lean
        yield from self.settle(lean, 0.08 * k, 0.25 * k)
        spot = self.free_spot(foot, "balance", sum(self.hands_x()) / 2)
        self.look_at = spot()
        yield from self.move_limb(foot.name, spot, 0.3 * k, arc=0.1)
        foot.free_style, foot.kept = "balance", 0
        self.free_since = self.t
        self.phase_target = None
        yield from self.settle(self.hang_target()[0], 0.1 * k, 0.3 * k)

    def bolt_to_clip(self):
        """하네스가 막 지나간, 아직 로프를 안 건 볼트. 너무 지나쳤으면 걸지 않고 넘어간다 (런아웃)."""
        if not self.events:
            return None
        h = self.harness()
        for bolt in self.bolts:
            if bolt.clipped or bolt.skipped or bolt.p.y > h.y + 0.4:
                continue
            if bolt.p.y < h.y - 1.5:
                bolt.skipped = True   # 너무 지나쳤다 (런아웃)
                continue
            return bolt
        return None

    def clip(self, bolt, reach=0.98):
        """클립: 볼트 쪽 손으로 허리에서 로프를 집어 올려 퀵드로에 걸고 홀드로 돌아간다. 팔 길이의 reach 배 안에 닿아야 건다."""
        self.actions.append("clip")
        side = 1 if bolt.p.x > self.body.x else -1
        limb = self.limbs["RH" if side > 0 else "LH"]
        if limb.mode != "grip" or (bolt.p - limb.root(self.body, self.tilt)).len() > limb.reach * reach:
            limb = self.limbs["LH" if side > 0 else "RH"]
            if limb.mode != "grip" or (bolt.p - limb.root(self.body, self.tilt)).len() > limb.reach * reach:
                bolt.skipped = True   # 손이 안 닿으면 건너뛴다
                return
        home = limb.hold
        self.look_at = bolt.p
        try:
            yield from self.move_limb(limb.name, self.harness, 0.25, arc=0.05)
            self.rope_hand = limb.name
            yield from self.move_limb(limb.name, bolt.rope_point, 0.3, arc=0.08)
            bolt.clipped = True
            self.rope_hand = None
            self.puff(bolt.rope_point(), n=2, speed=0.4, size=0.08, life=0.4)
            yield from self.move_limb(limb.name, home, 0.28, arc=0.1)
        finally:
            self.look_at, self.rope_hand = None, None

    def break_now(self):
        """시제품용: 지금 잡은 손 홀드를 바로 부순다."""
        for limb in (self.limbs["LH"], self.limbs["RH"]):
            if limb.mode == "grip" and self.other(limb).hold is not limb.hold:
                yield from self.break_hold(limb, limb.hold)
                return

    def rockfall(self):
        """시제품용: 낙석을 하나 떨어뜨린다."""
        side = self.wrng.choice((-1, 1))
        x = max(-WALL_HALF, min(WALL_HALF, self.body.x + side * 1.1))
        self.rocks.append([V(x, self.cam + VIEW_TOP + 1), V(0, -4.0), 0.0, self.wrng.uniform(-6, 6), 0.35, False])
        yield

    def slip_foot(self):
        """A. 발 미끄러짐 (젖은 바위): 발이 쭉 빠져 몸이 덜컥 처지고, 다시 디딘다."""
        feet = [f for f in (self.limbs["LF"], self.limbs["RF"]) if f.mode == "grip"]
        if not feet or self.mode != "spring":
            return
        f = self.wrng.choice(feet)
        home = f.hold
        self.actions.append("slip_foot")
        f.mode, f.vel = "free", V(f.side * 1.2, -2.0)
        self.vel = self.vel + V(0, -1.6)
        self.squash_vel -= 2.0
        self.puff(home.grip("F"), n=4, speed=0.8, size=0.1, life=0.5)
        self.texts.append(Float(self.body + V(0, 1.4), "앗!"))
        yield from self.wait(0.6, stoppable=False)
        yield from self.move_limb(f.name, home, 0.3, arc=0.1)

    def slip_hand(self):
        """B. 손 미끄러짐 (펌핑): 한 손과 같은 쪽 발이 빠져 문짝처럼 휙 돌다가 다시 잡는다."""
        hands = [h for h in (self.limbs["LH"], self.limbs["RH"]) if h.mode == "grip"]
        if len(hands) < 2 or self.mode != "spring":
            return
        h = self.wrng.choice(hands)
        f = self.limbs["RF" if h.side > 0 else "LF"]
        homes = {h.name: h.hold, f.name: f.hold}
        self.actions.append("slip_hand")
        h.mode, h.vel = "free", V(h.side * 2.0, -1.0)
        if f.mode == "grip":
            f.mode, f.vel = "free", V(f.side * 1.5, -1.0)
        self.mode = "hang"
        self.vel = V(-h.side * 1.8, -0.6)
        self.squash_vel -= 2.5
        self.texts.append(Float(self.body + V(0, 1.4), "으앗!"))
        try:
            yield from self.wait(0.9, stoppable=False)
            yield from self.move_limb(h.name, homes[h.name], 0.35, arc=0.1)
            if homes[f.name] is not None and f.mode != "grip":
                yield from self.move_limb(f.name, homes[f.name], 0.3, arc=0.1)
        finally:
            self.mode = "spring"

    def break_hold(self, limb, hold):
        """C. 홀드 부서짐: 잡은 사암 홀드가 부서져 조각이 떨어지고, 손이 빠졌다가 다른 홀드를 잡는다."""
        if limb.hold is not hold or limb.mode != "grip" or self.mode != "spring":
            return
        self.actions.append("break")
        for _ in range(7):
            self.particles.append(Particle(hold.p, V(self.wrng.uniform(-1.2, 1.2), self.wrng.uniform(-0.5, 1.5)),
                                           1.2, self.wrng.uniform(0.07, 0.13), "rock", (180, 130, 85)))
        if hold in self.holds:
            self.holds.remove(hold)
        limb.mode, limb.vel = "free", V(limb.side * 0.8, -2.0)
        self.vel = self.vel + V(0, -1.2)
        self.texts.append(Float(self.body + V(0, 1.4), "으악!"))
        yield from self.wait(0.5, stoppable=False)
        options = self.hand_options(limb, 1) or self.hand_options(limb, 2)
        if options:
            new = max(options)[2]
        else:
            root = limb.root(self.body, self.tilt)
            new = min((h for h in self.holds if h is not self.other(limb).hold and not h.foot), key=lambda h: (h.p - root).len())
        yield from self.move_limb(limb.name, new, 0.35, arc=0.1)

    def fall(self):
        """E. 진짜 추락 (세션 오류): 손발이 다 빠져 떨어지다가 마지막에 건 퀵드로에서 로프가 잡아 준다.
        출렁 튕기고 흔들린 뒤 `괜찮아요!` 하고 근처 홀드에 다시 붙는다. 높이는 잠깐 줄지만 기록은 그대로."""
        if not self.events or self.sleeping or self.rope is not None:
            return
        if not any(b.clipped for b in self.bolts):
            return
        self.actions.append("fall")
        yield from self.rope_fall()

    def rope_fall(self, vel=None):
        """손발이 다 빠져 떨어지다가 마지막에 건 퀵드로에서 로프가 잡아 준다 (진짜 추락, 크럭스에서 놓침). 출렁 튕기고
        흔들린 뒤 `괜찮아요!` 하고 근처 홀드에 다시 붙는다. vel 은 떨어지기 시작할 때 몸 빠르기 (없으면 살짝 튀어 오른다)."""
        self.tally["fall"] += 1
        top = max((b for b in self.bolts if b.clipped), key=lambda b: b.p.y)
        anchor = top.rope_point()
        self.rope = (anchor, (self.harness() - anchor).len() + 0.8)
        self.phase_target = self.look_at = None
        for l in self.limbs.values():   # 허우적
            l.mode, l.tucked = "free", False
            l.vel = V(self.wrng.uniform(-1.5, 1.5), self.wrng.uniform(1.0, 3.0))
        self.mode = "hang"
        self.vel = V(self.wrng.uniform(-0.5, 0.5), 0.8) if vel is None else vel
        self.lids = 0.0
        self.texts.append(Float(self.body + V(0, 1.4), "앗!"))
        try:
            yield from self.wait(1.8, stoppable=False)
            self.texts.append(Float(self.body + V(0, 1.4), "괜찮아요!"))
            self.squash_vel += 1.5
            yield from self.wait(0.9, stoppable=False)
            goals, taken = {}, set()
            for l in self.limbs.values():   # 몸 근처 홀드에 다시 붙는다
                ideal = self.body + V(l.side * 0.7, HAND_UP - 0.2 if l.kind == "H" else -FOOT_DOWN + 0.3)
                root = l.root(self.body, self.tilt)
                free = [h for h in self.holds if id(h) not in taken and not (h.foot and l.kind == "H")]   # 손은 발 턱을 잡지 않는다
                near = [h for h in free if (h.p - root).len() < l.reach * 0.95] or free
                hold = min(near, key=lambda h: (h.p - ideal).len())
                taken.add(id(hold))
                goals[l.name] = hold
            body = self.body + V(0, 0.2)
            yield from self.move_all(goals, 0.8, body_to=lambda: body, tilt_to=0.0)
        finally:
            self.rope = None
            self.mode = "spring"
            self.vel = V()
            self.best_top = sum(self.held_y(l) for l in self.limbs.values())
            self.stall = 0

    def lower_hand(self):
        return min((self.limbs["LH"], self.limbs["RH"]), key=self.held_y).name

    def rest(self):
        """오를 게 없으면 (일하는 펫이 없거나, 토큰으로 오를 때 쌓인 높이를 다 썼으면) 더 오르지 않고,
        팔을 쭉 펴고 엉덩이를 낮춰 매달려 쉰다. 일하는 펫은 있는데 토큰을 기다리는 중이면 쉬는 동작을 짧게 자주 한다."""
        self.actions.append("rest")
        self.resting, start = True, self.t
        if self.idle_since is None:
            self.idle_since = self.t
        for foot in (self.limbs["LF"], self.limbs["RF"]):
            foot.drop = False
        ready = self.climb_ready
        try:
            for foot in self.free_feet():   # 뗀 발은 먼저 홀드로
                hold = self.free_foot_hold(foot)
                if hold is not None:
                    yield from self.move_limb(foot.name, hold, 0.4, arc=0.1)
            hands_low = min(self.held_y(l) for l in self.limbs.values() if l.kind == "H")
            for foot in (self.limbs["LF"], self.limbs["RF"]):   # 높이 올렸거나 멀리 뻗은 발은 편한 자리로
                if self.held_y(foot) > hands_low - FOOT_SPAN + 0.45 or abs(foot.pos.x - self.body.x) > 1.1:
                    options = [(-abs(h.p.y - (hands_low - FOOT_SPAN)) - abs(foot.side * (h.p.x - self.body.x) - FOOT_WIDTH), id(h), h)
                               for h in self.holds if h is not self.other(foot).hold
                               and hands_low - FOOT_SPAN - 0.45 < h.p.y < hands_low - FOOT_SPAN + 0.45
                               and h.p.y <= self.held_y(foot) + 0.05
                               and foot.side * (h.p.x - self.body.x) > 0.2
                               and (h.grip("F") - foot.root(self.body, self.tilt)).len() < foot.reach * 0.92]
                    if options:
                        yield from self.move_limb(foot.name, max(options)[2], 0.4, arc=0.1)
            yield from self.tween(self, "crouch", self.crouch, 0.5, 0.8)
            last, first = None, True
            while not ready() and not self.requests and not self.sleeping:
                waiting = self.work > 0   # 토큰을 기다리는 중: 초크·털기·두리번을 짧게 자주
                left = NAP_AFTER - (self.t - self.idle_since)
                if left <= 0:   # 오래 쉬었다: 포타레지를 펴고 낮잠
                    yield from self.exhaust(nap=True)
                    break
                pause = self.rng.uniform(1.5, 2.5) if first else self.rng.uniform(*((1.0, 2.0) if waiting else (2.5, 5.0)))
                yield from self.wait(min(pause, left), until=ready)
                first = False
                if ready() or self.requests or self.t - self.idle_since >= NAP_AFTER:
                    continue
                pick = self.rng.choice([a for a in (("shake", "chalk", "look") if waiting else ("shake", "chalk", "look", "blow"))
                                        if a != last])
                last = pick
                self.actions.append("idle_" + pick)
                if pick == "shake":
                    yield from self.shake(both=True)
                elif pick == "chalk":
                    yield from self.chalk()
                elif pick == "look":
                    yield from self.look_around()
                else:
                    yield from self.blow()
            if self.sleeping:   # 낮잠에 들었다
                return
            yield from self.tween(self, "crouch", self.crouch, 0.0, 0.5)
            if ready() and not self.requests and (not self.tokens_on or self.t - start > 8):
                yield from self.shake()   # 오래 쉬었으면 한 번 털고 다시 오른다
        finally:
            self.resting = False

    def look_around(self):
        self.face = -self.face
        yield from self.wait(self.rng.uniform(0.9, 1.6), stoppable=False)
        if self.rng.random() < 0.6:
            self.face = -self.face
            yield from self.wait(0.6, stoppable=False)

    def blow(self):
        """손에 입김 불기."""
        name = self.lower_hand()
        home = self.limbs[name].hold
        yield from self.move_limb(name, self.blow_point, 0.34, arc=0.08)
        self.blow_puff(name)
        yield from self.wait(0.5, stoppable=False)
        yield from self.move_limb(name, home, 0.34, arc=0.14)

    def chalk(self):
        """초크 바르기: 아래쪽 손을 초크백에 넣어 세 번 휘젓고, 입김을 불고, 홀드로 돌아간다."""
        self.actions.append("chalk")
        name = self.lower_hand()
        home = self.limbs[name].hold
        yield from self.move_limb(name, self.bag_mouth, 0.36, arc=0.12)
        for _ in range(3):
            yield from self.move_limb(name, lambda: self.bag_mouth(0.17), 0.11, arc=0.0)
            self.puff(self.bag_mouth(), n=3, speed=0.8, size=0.13, life=0.7)
            yield from self.move_limb(name, self.bag_mouth, 0.11, arc=0.0)
        yield from self.move_limb(name, self.blow_point, 0.32, arc=0.08)
        self.blow_puff(name)
        yield from self.wait(0.4, stoppable=False)
        yield from self.move_limb(name, home, 0.34, arc=0.16)

    def shake(self, both=False):
        """팔 털기: 아래쪽 손을 놓고 몸 밖으로 늘어뜨려 털다가 다시 잡는다. both 면 반대 손도."""
        self.actions.append("shake")
        first = self.lower_hand()
        for name in ((first, "RH" if first == "LH" else "LH") if both else (first,)):
            limb = self.limbs[name]
            home = limb.hold

            def hang(wiggle=0.0, limb=limb):
                root = limb.root(self.body, self.tilt)
                shake = math.sin(self.t * 2 * math.pi * 4.5) * wiggle
                return root + V(limb.side * (0.95 + shake), -1.15 + shake * 0.5)

            yield from self.move_limb(name, hang, 0.3, arc=0.1)
            yield from self.move_limb(name, lambda hang=hang: hang(0.17), 1.0, arc=0.0)
            yield from self.move_limb(name, home, 0.34, arc=0.16)

    def dyno_jug(self, near=False):
        """다이노로 잡을 홀드 (손보다 1.5~2.5m 위, 몸 가까이). near 가 아니면 조금 더 넓게 찾는다."""
        top = max(self.held_y(self.limbs[n]) for n in ("LH", "RH"))
        limit = 1e9
        crux = next((h for h in self.holds if h.crux and h.p.y > top), None)
        if crux is not None:   # 크럭스 너머로는 건너뛰지 않는다 (끝 홀드는 crux 가 뛴다. 세션 다이노는 닿으면 끝 홀드로)
            if not near and 1.0 < crux.p.y - top < 3.8:
                return crux
            limit = crux.crux_from
        options = [h for h in self.holds if top + 1.5 < h.p.y < min(top + 2.5, limit) and abs(h.p.x - self.body.x) < 1.4
                   and not h.foot]
        if not options and not near:
            options = [h for h in self.holds if top + 1.2 < h.p.y < min(top + 2.8, limit) and not h.foot]
        return max(options, key=lambda h: h.r + self.rng.uniform(0, 0.1)) if options else None

    def dyno(self, show=True, jug=None, label=None):
        """다이노: 웅크렸다가 튀어 올라 멀리 있는 홀드 (jug, 없으면 dyno_jug) 를 두 손으로 잡고, 매달려 흔들리다
        발을 올린다. show 면 오른 높이를 글자로 띄운다 (세션이 일을 끝낼 때)."""
        self.actions.append(label or ("dyno" if show else "dyno_sprint"))
        if show and label is None:
            self.tally["dyno"] += 1
        self.charging = not show    # 일 끝냄 다이노는 덤 (brain 이 다음 동작 전에 되돌린다)
        hands = [self.limbs["LH"], self.limbs["RH"]]
        feet = [self.limbs["LF"], self.limbs["RF"]]
        top = max(self.held_y(l) for l in hands)
        jug = jug or self.dyno_jug()
        if jug is None:
            self.missing += 1
            jug = self.place_hold(V(self.route.center(top + 2), top + 2), big=True)
            if jug is None:
                jug = Hold(V(self.body.x, top + 2), 0.33, self.rng, True)
                self.holds.append(jug)
        yield from self.tween(self, "crouch", self.crouch, 0.6, 0.42)
        # 튀어 오르기: 몸은 점점 느려지며 꼭대기로, 손이 먼저 홀드로 간다
        self.mode = "script"
        start = self.body
        swing = 1 if start.x < jug.p.x else -1
        apex = jug.p + V(-swing * 0.35, -1.45)
        hand_from = [l.pos for l in hands]
        for l in hands:
            l.mode = "move"
        self.squash_vel += 2.6
        dur, t = 0.36, 0.0
        while t < dur:
            dt = yield
            t += dt
            k = min(1.0, t / dur)
            old = self.body
            self.body = start.lerp(apex, 1 - (1 - k) ** 2)
            self.vel = (self.body - old) * (1 / dt)
            self.tilt += (0 - self.tilt) * min(1.0, 10 * dt)
            for l, p in zip(hands, hand_from):
                l.pos = p.lerp(jug.grip("H", l.side), smooth(min(1.0, k * 1.25)))
            if k > 0.25:
                for f in feet:
                    if f.mode == "grip":
                        self.let_go(f)
        # 잡기
        for l in hands:
            self.grab(l, jug)
        jug.chalk = 1.0
        self.crouch = 0.0
        self.mode = "hang"
        self.vel = V(swing * 1.6, 0.8)
        self.squash_vel -= 3.2
        self.puff(jug.p + V(0, 0.15), n=12, speed=2.0, size=0.2, life=0.9)
        if show:
            self.texts.append(Float(jug.p + V(0, 0.6), f"+{max(1, round(jug.p.y - top))}m"))
        yield from self.wait(1.0, stoppable=False)
        for f in sorted(feet, key=lambda f: f.side * swing):
            below = [h for h in self.holds if h.p.y < jug.p.y - 1.9 and h is not self.other(f).hold]
            chips = [h for h in below if h.foot and h.p.x * jug.p.x > 0] if jug.crux else []   # 크럭스: 끝 홀드 쪽 턱부터
            options = self.foot_options(f)
            if chips:
                hold = min(chips, key=lambda h: (h.grip("F") - f.root(self.body, self.tilt)).len())
            elif options:
                hold = max(options)[2]
            elif below:
                hold = min(below, key=lambda h: (h.grip("F") - f.root(self.body, self.tilt)).len())
            else:
                continue   # 디딜 데가 없으면 (긴 추락 뒤 아래 홀드가 없어졌다) 발은 매달린 채로, 다음 동작이 고른다
            yield from self.move_limb(f.name, hold, 0.32, arc=0.12)
        self.mode = "spring"
        yield from self.wait(0.3, stoppable=False)

    def let_go(self, foot):
        """뛰어오르며 발을 홀드에서 뗀다: 다리가 닿는 데까지 당겨 놓고 몸 따라 흔들리게 한다 (몸이 이미 멀어져
        늘어난 발을 그대로 놓으면 한 프레임에 크게 끌려가 그 빠르기로 엉덩이 위까지 튄다)."""
        root = foot.root(self.body, self.tilt)
        d = foot.pos - root
        dist, hi = d.len() or 1e-6, foot.reach * 0.96
        if dist > hi:
            foot.pos = root + d * (hi / dist)
        foot.mode, foot.vel = "free", self.vel * 0.5

    def wall_end(self):
        """벽의 끝: 빌레이 줄이 툭 끊어지고 (추락 이벤트를 켰으면 끊어진 줄이 보인다) 허우적대며 같은 빠르기로 떨어진다.
        카메라가 따라 내려가며 벽이 흘러가고, 높이 숫자와 배경은 가속 눈금을 따라 고르게 (FALL_PCT, 오를 때 % 로
        지나간 비율대로 경치가 지나간다) 내려가다 땅 가까이 느려지고, 마지막 12m 는 떨어지는 빠르기대로라 땅이
        아래에서 올라와 닿는다. 쿵 하고 새 벽의 땅에 주저앉는다. 포타레지에서 자고 있었으면 깨어 떨어진다."""
        self.actions.append("wall_end")
        height = max(0.0, self.altitude())   # 떨어지기 전 (가속 눈금) 높이
        if self.ledge is not None:   # 침낭에서 빠져 떨어진다 (빈 포타레지는 남는다)
            self.ledge.bag, self.ledge.carry = 0.0, None
        self.sleeping, self.nap, self.lids = False, False, 0.0
        self.texts = [f for f in self.texts if f.style != "z"]
        clipped = [b for b in self.bolts if b.clipped]
        if self.events and clipped:
            top = max(clipped, key=lambda b: b.p.y)
            self.rope_cut = top.rope_point()
            self.texts.append(Float(top.p + V(0.5, 0.2), "툭!"))
            self.puff(self.rope_cut, n=6, speed=1.0, size=0.12, life=0.6)
        self.phase_target = self.look_at = None
        for limb in self.limbs.values():   # 허우적
            limb.mode, limb.tucked = "free", False
            limb.vel = V(self.wrng.uniform(-2.0, 2.0), self.wrng.uniform(1.5, 3.5))
        self.mode, self.falling = "script", True
        self.alt0 = height - self.body.y   # 떨어지는 동안 높이는 실제 높이 (가속 눈금 높이에서 이어서)
        self.texts.append(Float(self.body + V(0, 1.4), "으아아!"))
        yield from self.fall_home(height, "아야…")

    def fall_home(self, height, after):
        """height (m) 에서 땅까지 떨어진다 (wall_end·moon_leave): 몸은 같은 빠르기, 높이 숫자와 배경은 가속 눈금을 따라.
        쿵 하고 새 벽의 땅에 주저앉아 after 를 말한다."""
        land = GROUND + 1.0   # 몸통이 땅에 닿는 높이
        final = min(height, 12.0)   # 마지막 이만큼은 떨어지는 빠르기대로 (땅이 아래에서 올라와 닿는다)
        pct, pct_end = height_pct(height), height_pct(final)   # 그 위는 높이 숫자만 가속 눈금을 따라
        slow = FALL_SPEED / (SCALE_K * (SCALE_A + final))   # final 에서 FALL_SPEED 가 되는 % 빠르기
        head = None if pct > pct_end + 1e-6 else 0.0   # 높이 숫자가 final 에 닿은 때
        dur = None if head is None else max(0.6, (final - land) / FALL_SPEED)
        decor = random.Random(int(self.t * 1000))   # 지나가는 벽의 홀드 (그림만)
        fill, t = self.body.y - 6.0, 0.0
        while dur is None or t < dur:
            dt = yield
            t = t + dt if dur is None else min(dur, t + dt)
            if head is None:
                pct -= (slow + (FALL_PCT - slow) * min(1.0, (pct - pct_end) / FALL_RAMP)) * dt
                if pct <= pct_end:
                    head = t
                    dur = max(0.6, head + (final - land) / FALL_SPEED)
            old = self.body
            self.body = self.body + V(math.sin(t * 2.0) * 0.4 * dt, -FALL_SPEED * dt)
            self.vel = (self.body - old) * (1 / dt)
            self.tilt += 1.2 * dt   # 천천히 돈다
            for i, limb in enumerate(self.limbs.values()):   # 팔다리를 허우적댄다
                limb.vel = limb.vel + V(math.sin(t * 11 + i * 1.7), math.cos(t * 9 + i * 2.3)) * (40.0 * dt)
            alt = week_height(pct) if head is None else final - (t - head) * FALL_SPEED
            self.alt0 = max(land, alt) - self.body.y   # 높이 숫자와 배경이 내려가고 마지막에 땅이 올라온다
            near = 0.0 if head is None else smooth(max(0.0, min(1.0, (t - head) / max(0.01, dur - head))))   # 마지막: 카메라가 느려지며 땅 쪽에 선다
            aim = self.body.y + 1.0 + (self.ground_y() + 1.45 - self.body.y - 1.0) * near
            self.cam += (aim - self.cam) * (1 - math.exp(-8.0 * dt))
            while fill > self.body.y - 8.0:   # 아래로 지나갈 벽에 홀드를 깔고, 지나간 위쪽은 버린다
                cx = 1.2 * math.sin(fill * 0.35)
                for _ in range(decor.choice((2, 3, 3))):
                    x = max(-WALL_HALF + 0.3, min(WALL_HALF - 0.3, cx + decor.uniform(-1.15, 1.15)))
                    self.holds.append(Hold(V(x, fill - decor.uniform(0, Route.BAND)), decor.uniform(0.19, 0.27), decor))
                fill -= Route.BAND
            self.holds = [h for h in self.holds if h.p.y < self.cam + 15]
            self.bolts = [b for b in self.bolts if b.p.y < self.cam + 15]
        self.new_wall(restart=False, grounded=True, keep=(self.cam - self.body.y, self.body.x))   # 땅에 주저앉는다
        self.puff(self.body + V(0, -0.9), n=16, speed=2.2, size=0.22, life=0.9)   # 쿵
        self.texts.append(Float(self.body + V(0, 1.6), "쿵!"))
        self.squash_vel -= 4.0
        yield from self.wait(1.0, stoppable=False)
        self.texts.append(Float(self.body + V(0.6, 1.3), after))

    def crux_ready(self):
        """크럭스 끝 홀드로 뛸 때면 그 홀드: 손보다 1~3.6m 위에 있고, 손이 더 오를 데가 없고 (제자리로 맴돌기 시작했으면
        고쳐 잡기도 없다고 친다), 발도 더 올릴 데가 없다."""
        top = max(self.held_y(self.limbs[n]) for n in ("LH", "RH"))
        jug = next((h for h in self.holds if h.crux and 1.0 < h.p.y - top < 3.6), None)
        if jug is None or any(l.hold is jug for l in self.limbs.values()):
            return None
        hands = (self.limbs["LH"], self.limbs["RH"])
        if any(self.hand_options(l, 0) for l in hands) or (self.stall < 2 and any(self.hand_options(l, 1) for l in hands)):
            return None
        if self.stall < 3 and any(self.foot_options(f, 0) for f in (self.limbs["LF"], self.limbs["RF"]) if f.mode == "grip"):
            return None
        return jug

    def crux(self, jug):
        """크럭스: 멈칫하며 끝 홀드를 보고, 보통 빠르기면 초크도 바른 뒤 다이노로 몸 반대쪽 끝의 홀드를 잡는다.
        추락 이벤트를 켰으면 첫 번째는 CRUX_MISS 확률로 손끝이 미끄러진다: 로프에 걸려 떨어지면 크럭스 아래까지 다시
        올라와 (brain 이 다시 부른다) 뛰고, 건 퀵드로가 없어 제자리로 미끄러졌으면 바로 다시 뛴다. 넘으면 주먹을 쥔다."""
        self.actions.append("crux")
        pace = max(1, self.pace)
        approach = self.moves_since_crux
        self.look_at = jug.p
        yield from self.wait(self.rng.uniform(0.8, 1.4) if pace == 1 else 0.4 if pace == 2 else 0.1, stoppable=False)
        h = self.harness()   # 뛰기 전에 크럭스 볼트에 로프를 건다 (빨리 오르다 지나쳤어도. 뛰고 나면 너무 지나쳐 못 건다)
        bolt = max((b for b in self.bolts if h.y - 1.5 < b.p.y < jug.crux_from), key=lambda b: b.p.y, default=None)
        if self.events and bolt is not None and not bolt.clipped:
            yield from self.clip(bolt, CRUX_CLIP)
            self.look_at = jug.p
        if pace == 1:
            yield from self.chalk()
            self.look_at = jug.p
            yield from self.wait(0.4, stoppable=False)
        tries = jug.crux_tries
        if self.events and tries == 1 and self.wrng.random() < CRUX_MISS:
            fell = yield from self.crux_miss(jug)
            jug.crux_tries = tries = 2
            if fell:
                return
            self.look_at = jug.p
            yield from self.wait(0.9 if pace == 1 else 0.4, stoppable=False)
        yield from self.dyno(show=False, jug=jug, label="crux_dyno")
        self.crux_log.append((tries, approach))
        self.tally["crux"] += 1
        self.texts.append(Float(jug.p + V(0, 0.8), "크럭스!"))
        if pace <= 2:
            yield from self.fist_pump("RH" if jug.p.x < 0 else "LH")   # 벽 안쪽 손으로
        self.look_at = None

    def crux_miss(self, jug):
        """크럭스에서 놓침: 끝 홀드로 뛰어 손끝이 닿았다가 미끄러진다. 건 퀵드로가 있으면 손발이 다 빠져 떨어지다가
        로프에 걸리고 (rope_fall, True 를 돌려준다), 없으면 원래 잡던 홀드로 미끄러져 돌아간다 (False)."""
        self.actions.append("crux_miss")
        hands = [self.limbs["LH"], self.limbs["RH"]]
        feet = [self.limbs["LF"], self.limbs["RF"]]
        home = {n: (l.mode, l.hold, l.follow) for n, l in self.limbs.items()}
        yield from self.tween(self, "crouch", self.crouch, 0.6, 0.42)
        self.mode = "script"
        start = self.body
        swing = 1 if start.x < jug.p.x else -1
        apex = jug.p + V(-swing * 0.45, -1.75)   # 조금 못 미친다
        hand_from = [l.pos for l in hands]
        touch = [jug.grip("H", l.side) + V(-swing * 0.12, -0.2) for l in hands]
        for l in hands:
            l.mode = "move"
        self.squash_vel += 2.6
        dur, t = 0.36, 0.0
        while t < dur:
            dt = yield
            t += dt
            k = min(1.0, t / dur)
            old = self.body
            self.body = start.lerp(apex, 1 - (1 - k) ** 2)
            self.vel = (self.body - old) * (1 / dt)
            for l, a, b in zip(hands, hand_from, touch):
                l.pos = a.lerp(b, smooth(min(1.0, k * 1.25)))
            if k > 0.25:
                for f in feet:
                    if f.mode == "grip":
                        self.let_go(f)
        self.puff(jug.p + V(0, 0.1), n=6, speed=1.2, size=0.14, life=0.6)
        anchor = max((b.p.y for b in self.bolts if b.clipped), default=None)
        if self.events and anchor is not None and anchor > self.harness().y - CRUX_RUNOUT:   # 건 퀵드로가 잡아 준다
            self.crouch = 0.0
            yield from self.rope_fall(V(-swing * 0.6, 0.2))
            return True
        self.texts.append(Float(start + V(0, 1.4), "앗!"))   # 떨어져 돌아갈 자리 위 (높이 상자에 가리지 않게)
        loose = [(f, f.pos, home[f.name][1]) for f in feet if f.mode == "free" and home[f.name][1] in self.holds]
        for f, _, _ in loose:
            f.mode = "move"   # 매달려 흔들리던 발도 손처럼 천천히 원래 홀드로
        top, dur, t = self.body, 0.34, 0.0
        while t < dur:   # 미끄러져 떨어진다
            dt = yield
            t += dt
            k = min(1.0, t / dur)
            old = self.body
            self.body = top.lerp(start, k * k)
            self.vel = (self.body - old) * (1 / dt)
            for l, a, b in zip(hands, hand_from, touch):
                l.pos = b.lerp(a, smooth(k))
            for f, a, hold in loose:
                f.pos = a.lerp(self.grip_point(f, hold), smooth(k))
        self.crouch = 0.0
        for name, limb in self.limbs.items():
            mode, hold, follow = home[name]
            if mode == "follow" and follow is not None:   # 플래그·벽 딛기로 뺀 발은 뺀 채로
                limb.mode, limb.follow = "follow", follow
            elif hold is not None and hold in self.holds:
                self.grab(limb, hold)
            else:
                limb.mode = "free"
        self.mode, self.vel = "spring", V()
        self.squash_vel -= 3.0
        yield from self.wait(0.3, stoppable=False)
        return False

    def exhaust(self, nap=False):
        """사용량을 다 쓰면 포타레지를 꺼내 걸고 펴서, 그 위 침낭에 들어가 리셋까지 잔다.
        nap 이면 오래 쉬어서 자는 낮잠이다 (토큰이 모이면 깬다)."""
        if self.sleeping or self.ledge is not None or (self.hp > 0 and not nap):
            return   # 이미 자고 있거나 펴는 중이거나 (같은 신호가 두 번 왔다), 그사이 체력이 돌아왔다
        self.nap = nap
        self.actions.append("nap" if nap else "exhaust")
        if nap:   # 사용량 잠은 일지가 5시간 창이 다 찬 횟수로 센다
            self.tally["nap"] += 1
        side = 1 if self.body.x < 0 else -1   # 넓은 쪽에 편다
        ax = max(-WALL_HALF + 1.2, min(WALL_HALF - 1.2, self.body.x + side * 0.9))
        ledge = self.ledge = Ledge(V(ax, self.body.y + 1.9))
        yield from self.tween(self, "crouch", self.crouch, 0.2, 0.3)
        # 1. 아래쪽 손으로 초크백 옆의 꾸러미를 꺼내 앵커에 건다
        name = self.lower_hand()
        home = self.limbs[name].hold
        yield from self.move_limb(name, self.bag_mouth, 0.3, arc=0.1)
        ledge.carry = name
        yield from self.move_limb(name, lambda: ledge.anchor + V(0, -0.25), 0.5, arc=0.15)
        ledge.carry, ledge.hung = None, True
        yield from self.move_limb(name, home, 0.3, arc=0.1)
        ledge.home = (self.body, {n: l.hold for n, l in self.limbs.items()})
        # 2. 펼친다
        yield from self.tween(ledge, "open", 0.0, 1.0, 0.7)
        # 3. 손발을 놓고 침대에 올라앉는다
        seat = ledge.bed + V(0, 0.92)
        yield from self.move_all({n: self.tucked(n) for n in self.limbs}, 0.9, body_to=lambda: seat, tilt_to=0.12, hop=0.5)
        self.crouch = 0.0
        # 4. 침낭을 끌어올리고 잔다
        yield from self.tween(ledge, "bag", 0.0, 1.0, 0.5)
        self.sleeping = True
        self.requests = [r for r in self.requests if r[0] in AWAKE]   # 자기 전에 받아 둔 동작은 버린다
        self.z_t = 0.6
        yield from self.tween(self, "lids", self.lids, 1.0, 0.8)
        if (self.hp > 0 and not self.nap) or (self.nap and self.climb_ready()):
            yield from self.wake()   # 펴는 동안 체력이 돌아왔거나 (그사이 창이 숨어 깨우는 신호를 못 받았을 수도 있다), 토큰이 모였다

    def wake(self):
        """사용량이 돌아오면 깨어나 기지개를 켜고, 벽으로 돌아가 포타레지를 접어 넣고, 초크를 바르고 다시 오른다."""
        ledge = self.ledge
        if not self.sleeping or ledge is None:
            yield from self.chalk()
            return
        self.actions.append("wake")
        self.sleeping = False
        yield from self.tween(self, "lids", 1.0, 0.0, 0.35)
        yield from self.wait(0.3, stoppable=False)
        self.squash_vel += 1.8
        yield from self.move_all({"LH": lambda: self.body + V(-0.6, 1.75), "RH": lambda: self.body + V(0.6, 1.75)}, 0.45)
        yield from self.wait(0.5, stoppable=False)
        yield from self.move_all({"LH": self.tucked("LH"), "RH": self.tucked("RH")}, 0.35)
        yield from self.tween(ledge, "bag", 1.0, 0.0, 0.4)
        body, holds = ledge.home
        yield from self.move_all(holds, 0.8, body_to=lambda: body, tilt_to=0.0, hop=0.35)
        self.vel, self.mode = V(), "spring"
        yield from self.tween(ledge, "open", 1.0, 0.0, 0.5)
        ledge.hung, ledge.stow = False, 0.0
        yield from self.tween(ledge, "stow", 0.0, 1.0, 0.45)
        self.ledge = None
        self.nap, self.idle_since = False, None   # 깨면 쉬기를 처음부터 센다
        yield from self.chalk()

    def refill(self, gain=0):
        """사용량이 일부 돌아오면 (5시간 창 리셋) 초크백이 다시 차오르고 주먹을 불끈 쥔다 (gain: 늘어난 체력 %)."""
        if self.sleeping:
            yield from self.wake()
            return
        self.actions.append("refill")
        self.puff(self.bag_mouth(), n=8, speed=1.0, size=0.14, life=0.9)
        if gain:
            self.texts.append(Float(self.body + V(0, 1.5), f"+{gain}%"))
        yield from self.fist_pump()

    def fist_pump(self, name=None):
        """손 (없으면 아래쪽 손) 으로 주먹을 불끈 쥐고 두 번 흔든 뒤 다시 잡는다."""
        name = name or self.lower_hand()
        limb = self.limbs[name]
        home = limb.hold
        limb.fist = True

        def up():
            return limb.root(self.body, self.tilt) + V(limb.side * 0.55, 0.8)

        def down():
            return limb.root(self.body, self.tilt) + V(limb.side * 0.6, 0.35)

        yield from self.move_limb(name, up, 0.3, arc=0.1)
        for _ in range(2):
            yield from self.move_limb(name, down, 0.13, arc=0.0)
            self.squash_vel -= 1.0
            yield from self.move_limb(name, up, 0.13, arc=0.0)
        yield from self.wait(0.25, stoppable=False)
        limb.fist = False
        yield from self.move_limb(name, home, 0.34, arc=0.14)



# ---- 저장과 신호 -------------------------------------------------------------

def load_state(path):
    """climb.json → (기록, 멀쩡한지). 없으면 ({}, True), 깨졌으면 ({}, False)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, True
    except (OSError, ValueError):
        return {}, False
    return (data, True) if isinstance(data, dict) else ({}, False)


def save_state(path, data):
    """tmp 에 다 쓰고 디스크에 내린 뒤 바꿔 끼운다 (전원이 꺼져도 빈 파일이 남지 않게)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dict(data, version=1), f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def hp_signals(prev, hp):
    """체력 (계정 사용량) 이 prev → hp 로 바뀔 때 보낼 신호 [(이름, 인자...)].
    0 이 되면 잠, 0 에서 오르면 기상, 10 넘게 오르면 초크백이 차오름, 줄면 초크. 처음 읽은 값이 0 이면 바로 잠."""
    if hp is None:
        return []
    if prev is None:
        return [("exhaust",)] if hp == 0 else []
    if hp == 0 and prev > 0:
        return [("exhaust",)]
    if prev == 0 and hp > 0:
        return [("wake",)]
    if hp >= prev + 10:
        return [("refill", hp - prev)]
    if hp < prev:
        return [("chalk",)]
    return []


def session_signals(changes):
    """세션 상태가 바뀐 것 {sid: (전, 후)} → 보낼 신호. 일을 끝내면 다이노, 오류면 로프 추락."""
    out = []
    for _, new in changes.values():
        if new == "done":
            out.append(("dyno",))
        elif new == "error":
            out.append(("fall",))
    return out


def working_count(states):
    """일하는 펫 수 (작업 중, 백그라운드 작업 중): 날씨를 정한다."""
    return sum(1 for state in states.values() if state in WORKING)
