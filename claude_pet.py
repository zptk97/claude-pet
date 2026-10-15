#!/usr/bin/env python3
"""Claude Code 세션마다 펫을 한 마리씩 바탕화면 위에 띄운다 (Codex 펫 형식 스프라이트).

hooks/claude-pet-hook.sh 가 쓰는 세션별 상태 파일을 감시해 펫의 애니메이션과 말풍선을 바꾼다.
작업 중 말풍선 첫 줄에는 Claude 가 마지막으로 남긴 말이나 생각을 Haiku(claude -p)로 한 줄 요약해 둔다 (메뉴에서 끌 수 있다).
펫들은 처음 생긴 세션부터 오른쪽 아래에서 왼쪽으로 한 줄로 선다.
GNOME Wayland 는 앱이 스스로 항상 위에 두는 걸 막으므로 XWayland(xcb)로 띄운다.

    python3 ~/.claude/pet/claude_pet.py

왼쪽 클릭: 그 세션의 터미널 창을 앞으로 (GNOME 확장 claude-pet-focus 필요) + 완료·오류 확인
마우스 올림: 그 세션의 컨텍스트와 계정 5시간·주간 사용량 카드 (상태줄 중계 hooks/claude-pet-statusline.sh 필요)
드래그: 줄 전체 이동 (펫 따로 움직이기를 켜면 그 펫만) / 휠 버튼 드래그: 펫을 들고 흔들기, 놓으면 자리로 돌아와 웃거나 운다
오른쪽 클릭: 나중에 이어서 볼게, 이 세션의 펫(펫 다시 읽기), 펫 켜고 끄기, 크기, 접기, 펫 따로 움직이기,
작업 요약 켜고 끄기, 자동 실행, 종료

나중에 이어서 볼게: 다른 세션을 보러 가기 전에 표시해 두면 보라색 말풍선과 책갈피로 기다린다.
그 펫을 클릭해 돌아가거나, 그 세션에 새 프롬프트를 보내거나, 표시 지우기를 누르면 풀린다.

접기: 손이 필요한 세션(확인 필요 > 오류 > 다 했어요) 하나만 세우고 나머지 수는 배지로 단다.
그런 세션이 없으면 가장 오래된 세션 펫에 상태 요약을 달고, 클릭하면 펼친다.
"""

import fcntl
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from PIL import Image  # noqa: E402
from PyQt5.QtCore import (QAbstractAnimation, QEasingCurve, QFileSystemWatcher, QObject, QPoint,  # noqa: E402
                          QProcess, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, pyqtSlot)
from PyQt5.QtDBus import QDBus, QDBusConnection, QDBusMessage  # noqa: E402
from PyQt5.QtGui import (QColor, QFont, QFontMetrics, QIcon, QImage, QPainter,  # noqa: E402
                         QPainterPath, QPen, QPixmap, QPolygonF, QRegion)
from PyQt5.QtWidgets import (QAction, QActionGroup, QApplication, QCheckBox, QMenu,  # noqa: E402
                             QProxyStyle, QStyle, QWidget, QWidgetAction)

import petcore  # noqa: E402

APP_DIR = Path(__file__).resolve().parent
# 직접 만든 펫, Petdex 등에서 받은 펫(git 제외), Codex 펫 순서로 찾는다. 같은 id 는 앞 폴더가 이긴다.
PET_SEARCH_DIRS = [APP_DIR / "pets", APP_DIR / "pets-downloaded",
                   Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser() / "pets"]
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser() / "claude-pet"
CONFIG_FILE = CONFIG_DIR / "config.json"
DISABLED_FLAG = CONFIG_DIR / "disabled"  # 있으면 훅이 새 세션에서 펫을 띄우지 않는다

SCALES = {"작게": 0.35, "보통": 0.5, "크게": 0.7}
ROW_GAP = 6
BUBBLE_MAX_W = 200
BUBBLE_PAD = 7
BUBBLE_TAIL = 8
BUBBLE_W_STEP = 16  # 경과 시간 글자 폭이 바뀔 때마다 옆 펫들이 흔들리지 않게 폭을 이 단위로 올린다
NAME_MAX_W = 130
SUMMARY_MAX_W = 240  # 접힌 요약 펫의 이름표 ("작업 중 2 · 백그라운드 1")
MAX_SAVED_ASSIGNMENTS = 100
MENU_ICON_SIZE = 24
DBUS_TIMEOUT_MS = 1000
HOVER_DELAY_MS = 350  # 펫 위를 스쳐 지나갈 때는 카드를 띄우지 않는다
CARD_PAD = 10
CARD_BAR_W = 90
CARD_BAR_H = 8
CARD_TITLE_MAX_W = 320
GIST_TIMEOUT_MS = 30_000
RETURN_MS = 450    # 휠로 들었다 놓은 펫이 자리로 돌아가는 시간
HELD_TICK_MS = 100  # 들고 가만히 있어도 이 간격으로 동작(버둥거림·어지러움)을 다시 고른다
GIST_MAX_FAILURES = 3  # 요약이 연달아 이만큼 실패하면 (로그인 문제 등) 다시 시도하지 않는다

# 상태별 말풍선 (배경, 테두리)
BUBBLE_COLORS = {
    "running": ("#FFFFFF", "#8A8A8A"),
    "waiting": ("#FFF4C2", "#C08A00"),
    "done": ("#E2F6E3", "#2F8F4E"),
    "error": ("#FDE2E1", "#C53030"),
    "background": ("#E3EFFD", "#2B6CB0"),
    "marked": ("#EFE4FB", "#7B3FC4"),  # 나중에 이어서 볼게
}
MARK_COLOR = "#7B3FC4"

def usage_color(percent):
    """사용량 막대 색: 50% 미만 초록, 80% 미만 노랑, 그 이상 빨강."""
    if percent < 50:
        return "#48BB78"
    if percent < 80:
        return "#ECC94B"
    return "#F56565"


# 펫 이미지가 없을 때 그리는 얼굴
FALLBACK_FACES = {"idle": "-.-", "running": "o.o", "waiting": "?.?", "done": "^.^", "error": "x.x",
                  "background": "o.-"}


def load_config():
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(config):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(CONFIG_FILE)
    except OSError as err:
        print(f"claude-pet: 설정 저장 실패: {err}", file=sys.stderr)


def acquire_single_instance(state_dir):
    """펫 프로그램은 하나만 띄운다. 훅도 같은 pet.lock 으로 떠 있는지 확인한다."""
    state_dir.mkdir(parents=True, exist_ok=True)
    lock = open(state_dir / "pet.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        return None
    return lock


def load_frames(sprite_path, scale):
    """아틀라스를 애니메이션별 QPixmap 목록으로 자른다. PyQt5 는 WebP 를 못 읽어서 Pillow 로 연다."""
    with Image.open(sprite_path) as im:
        if im.size != (petcore.ATLAS_W, petcore.ATLAS_H):
            raise ValueError(f"아틀라스 크기가 {im.size}, 기대값 {(petcore.ATLAS_W, petcore.ATLAS_H)}")
        rgba = im.convert("RGBA")
    width, height = rgba.size
    atlas = QImage(rgba.tobytes("raw", "RGBA"), width, height, width * 4, QImage.Format_RGBA8888).copy()
    target_w, target_h = round(petcore.CELL_W * scale), round(petcore.CELL_H * scale)
    frames = {}
    for name in petcore.ANIMATIONS:
        frames[name] = [
            QPixmap.fromImage(atlas.copy(*rect)).scaled(
                target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            for rect in petcore.frame_rects(name)
        ]
    return frames


def load_icon(sprite_path):
    """메뉴에 붙일 작은 그림: 쉬는 모습 첫 프레임에서 투명한 여백을 잘라낸다. 못 읽으면 빈 아이콘."""
    try:
        with Image.open(sprite_path) as im:
            cell = im.crop((0, 0, petcore.CELL_W, petcore.CELL_H)).convert("RGBA")
    except OSError:
        return QIcon()
    bbox = cell.getbbox()
    if bbox:
        cell = cell.crop(bbox)
    image = QImage(cell.tobytes("raw", "RGBA"), cell.width, cell.height, cell.width * 4,
                   QImage.Format_RGBA8888).copy()
    return QIcon(QPixmap.fromImage(image).scaled(MENU_ICON_SIZE, MENU_ICON_SIZE, Qt.KeepAspectRatio,
                                                 Qt.SmoothTransformation))


class MenuIconStyle(QProxyStyle):
    """메뉴 아이콘을 MENU_ICON_SIZE 로 키운다 (Qt5 QMenu 는 스타일시트의 icon-size 를 따르지 않는다)."""

    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PM_SmallIconSize:
            return MENU_ICON_SIZE
        return super().pixelMetric(metric, option, widget)


class Desktop:
    """D-Bus 로 Terminator 창 제목을 묻고, GNOME 확장(claude-pet-focus)에 그 창을 올려 달라고 한다."""

    EXTENSION = ("org.gnome.Shell", "/org/gnome/Shell/Extensions/ClaudePet",
                 "org.gnome.Shell.Extensions.ClaudePet")
    TERMINATOR_PREFIX = "net.tenshu.Terminator2"  # 뒤에 디스플레이 이름 해시가 붙는다

    def __init__(self):
        self.bus = QDBusConnection.sessionBus()
        self._terminator = None

    def _call(self, service, path, interface, method, *args):
        message = QDBusMessage.createMethodCall(service, path, interface, method)
        message.setArguments(list(args))
        reply = self.bus.call(message, QDBus.Block, DBUS_TIMEOUT_MS)
        if reply.type() != QDBusMessage.ReplyMessage:
            return None
        return reply.arguments()

    def terminator_title(self, terminal_uuid):
        """그 터미널이 들어 있는 Terminator 창의 지금 제목. 못 얻으면 빈 문자열."""
        if not terminal_uuid:
            return ""
        for _ in range(2):  # Terminator 가 다시 떴으면 서비스 이름을 새로 찾는다
            if not self._terminator:
                names = self.bus.interface().registeredServiceNames().value() or []
                self._terminator = next((n for n in names if n.startswith(self.TERMINATOR_PREFIX)), "")
                if not self._terminator:
                    return ""
            args = self._call(self._terminator, "/net/tenshu/Terminator2", self._terminator,
                              "get_window_title", terminal_uuid)
            if args:
                return str(args[0])
            self._terminator = None
        return ""

    def activate(self, window_title):
        """창을 올렸으면 True. 확장이 아직 안 켜졌거나 창을 못 찾으면 False."""
        title = petcore.clean_title(window_title)
        if not title:
            return False
        args = self._call(*self.EXTENSION, "ActivateWindow", title)
        return bool(args) and int(args[0]) > 0

    def watch_clicks(self, enabled):
        """켜 두면 화면 어디를 누르든 확장이 Clicked(x, y) 신호로 알려 준다. 예전 확장이거나 없으면 아무 일 없다."""
        self._call(*self.EXTENSION, "WatchClicks", bool(enabled))

    def connect_clicked(self, slot):
        service, path, interface = self.EXTENSION
        return self.bus.connect(service, path, interface, "Clicked", slot)


class PetWindow(QWidget):
    """세션 하나의 펫: 말풍선, 펫, 이름표. 배치와 클릭 처리는 PetManager 가 한다."""

    def __init__(self, manager, session_id):
        # 포커스를 받는 창이면 GNOME 이 포커스를 막고 "준비됨" 알림(DEMANDS_ATTENTION)을 띄운다
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.NoDropShadowWindowHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Claude Pet")
        self.setFont(QFont(self.font().family(), 10))
        self.name_font = QFont(self.font().family(), 9)
        self.badge_font = QFont(self.font().family(), 8, QFont.Bold)

        self.manager = manager
        self.session_id = session_id
        self.has_pet = False
        self.pet_id = None
        self.frames = None
        self.pet_size = (0, 0)
        self.state = "idle"
        self.bubble = ""  # 보이는 말풍선 (장난치는 동안은 play_bubble, 아니면 status_bubble)
        self.status_bubble = ""
        self.name = ""
        self.badge = ""  # 접혔을 때 뒤에 기다리는 세션 수 (+2)
        self.bubble_style = "idle"  # 말풍선 색 (보통 상태와 같고, 나중에 이어서 볼 세션이면 marked)
        self.marked = False  # 나중에 이어서 볼게 책갈피
        self.name_max_w = NAME_MAX_W

        self.animation = ""
        self.oneshot = None  # 한 번만 재생하고 상태 애니메이션으로 돌아갈 것 (클릭 시 점프)
        self.frame_index = 0
        self.anim_timer = QTimer(self, singleShot=True, timeout=self.next_frame)

        # 휠로 들고 장난치기
        self.play_animation = None  # 장난치는 동안의 동작 (상태 애니메이션보다 앞선다)
        self.play_bubble = None     # 장난치는 동안의 말풍선 (None 이면 상태 말풍선)
        self.held = None            # 들고 있으면 [커서와 펫 왼쪽 위의 차이, petcore.Shake, 마지막 커서 자리]
        self.reaction = None        # 놓은 뒤 자리로 돌아와서 할 (동작, 말풍선, ms)
        self.home = QPoint()        # 줄이나 따로 놓은 자리 (창 왼쪽 위)
        self.back = QPropertyAnimation(self, b"pos", self, duration=RETURN_MS,
                                       easingCurve=QEasingCurve(QEasingCurve.OutBack))
        self.back.finished.connect(self.react)
        self.held_timer = QTimer(self, interval=HELD_TICK_MS, timeout=self.follow)
        self.react_timer = QTimer(self, singleShot=True, timeout=self.end_play)

        self.pet_rect = self.bubble_rect = self.name_rect = self.badge_rect = QRect()
        self.set_animation(petcore.STATE_ANIMATION[self.state])

    # ---- 내용 / 크기 -------------------------------------------------------

    def set_pet(self, pet_id, frames, pet_size):
        self.has_pet = True
        self.pet_id = pet_id
        self.frames = frames
        self.pet_size = pet_size
        self.relayout()

    def set_view(self, state, bubble, name, badge="", name_max_w=NAME_MAX_W, bubble_style=None, marked=False):
        """상태를 반영한다. 창 크기가 바뀌었으면 True (줄을 다시 세워야 한다)."""
        if state != self.state:
            self.state = state
            self.oneshot = None
            self.set_animation(petcore.STATE_ANIMATION[state])
        look = (bubble_style or state, marked)
        if look != (self.bubble_style, self.marked):
            self.bubble_style, self.marked = look
            self.update()
        view = (bubble, name, badge, name_max_w)
        if view == (self.status_bubble, self.name, self.badge, self.name_max_w):
            return False
        self.status_bubble, self.name, self.badge, self.name_max_w = view
        return self.refit()

    def refit(self):
        """보일 말풍선을 정하고 창 크기를 다시 잰다. 크기가 바뀌었으면 True (줄을 다시 세워야 한다)."""
        self.bubble = self.play_bubble if self.play_bubble is not None else self.status_bubble
        old = self.size_hint_
        self.relayout()
        self.update()
        changed = self.size_hint_ != old
        if changed and self.playing():  # 들고 있거나 돌아가는 중에는 줄 배치가 크기를 맞춰 주지 않는다
            self.place(self.x(), self.y())
        return changed

    def relayout(self):
        """창 안에서 말풍선·펫·이름표 자리를 정하고 창 크기(size_hint_)를 계산한다."""
        pet_w, pet_h = self.pet_size
        if self.bubble:
            text = QFontMetrics(self.font()).boundingRect(
                QRect(0, 0, BUBBLE_MAX_W - 2 * BUBBLE_PAD, 1000), Qt.TextWordWrap, self.bubble)
            bubble_w = -(-(text.width() + 2 * BUBBLE_PAD) // BUBBLE_W_STEP) * BUBBLE_W_STEP
            bubble_w = min(bubble_w, BUBBLE_MAX_W)
            bubble_h = text.height() + 2 * BUBBLE_PAD
        else:
            bubble_w = bubble_h = 0
        name_metrics = QFontMetrics(self.name_font)
        self.name_text = name_metrics.elidedText(self.name, Qt.ElideRight, self.name_max_w)
        name_w = name_metrics.horizontalAdvance(self.name_text) + 10 if self.name_text else 0
        name_h = name_metrics.height() + 2 if self.name_text else 0

        width = max(pet_w, bubble_w, name_w)
        top = bubble_h + BUBBLE_TAIL if self.bubble else 0
        self.bubble_rect = QRect((width - bubble_w) // 2, 0, bubble_w, bubble_h)
        self.pet_rect = QRect((width - pet_w) // 2, top, pet_w, pet_h)
        self.name_rect = QRect((width - name_w) // 2, top + pet_h + 2, name_w, name_h)
        self.size_hint_ = (width, top + pet_h + 2 + name_h)
        if self.badge:  # 펫 오른쪽 위 모서리 (펫 영역 안이라 창 마스크에 이미 들어 있다)
            badge_metrics = QFontMetrics(self.badge_font)
            badge_h = badge_metrics.height() + 4
            badge_w = max(badge_h, badge_metrics.horizontalAdvance(self.badge) + 10)
            self.badge_rect = QRect(self.pet_rect.right() - badge_w + 1, self.pet_rect.top() + 2,
                                    badge_w, badge_h)
        # 투명한 빈 곳은 클릭이 아래 창으로 가게 말풍선·펫·이름표 영역만 남긴다.
        # 창 크기가 그대로여도 말풍선·이름표 폭은 바뀔 수 있어서 자리를 잴 때마다 다시 만든다.
        region = QRegion(self.pet_rect).united(QRegion(self.name_rect))
        if self.bubble:
            region = region.united(QRegion(self.bubble_rect.adjusted(0, 0, 0, BUBBLE_TAIL)))
        self.setMask(region)

    size_hint_ = (0, 0)

    def set_home(self, left, top):
        """줄이나 따로 놓은 자리 (창 왼쪽 위). 들고 있거나 돌아가는 중이 아니면 바로 옮긴다."""
        self.home = QPoint(left, top)
        if self.back.state() == QAbstractAnimation.Running:
            self.back.setEndValue(self.home)
        elif self.held is None:
            self.place(left, top)

    def place(self, left, top):
        """창 왼쪽 위를 (left, top) 에 두고 창 크기를 맞춘다."""
        width, height = self.size_hint_
        self.setGeometry(left, top, width, height)

    # ---- 애니메이션 --------------------------------------------------------

    def current_animation(self):
        return self.oneshot or self.play_animation or self.animation

    def set_animation(self, name):
        if name == self.animation:
            return
        self.animation = name
        if self.oneshot is None and self.play_animation is None:
            self.frame_index = 0
            self.anim_timer.stop()
            self.schedule_frame()
        self.update()

    def play_jump(self):
        self.oneshot = "jumping"
        self.frame_index = 0
        self.anim_timer.stop()
        self.schedule_frame()
        self.update()

    def schedule_frame(self):
        durations = petcore.ANIMATIONS[self.current_animation()][1]
        self.anim_timer.start(durations[self.frame_index % len(durations)])

    def next_frame(self):
        count = len(petcore.ANIMATIONS[self.current_animation()][1])
        self.frame_index += 1
        if self.frame_index >= count:
            self.frame_index = 0
            self.oneshot = None
        self.schedule_frame()
        self.update()

    # ---- 휠로 들고 장난치기 ---------------------------------------------------

    def playing(self):
        """들고 있거나 자리로 돌아가는 중인지 (그동안은 줄 배치가 이 창을 옮기지 않는다)."""
        return self.held is not None or self.back.state() == QAbstractAnimation.Running

    def set_play(self, animation, bubble):
        """장난치는 동안의 동작과 말풍선. None 이면 상태대로 돌아간다. 창 크기가 바뀌었으면 True."""
        if animation != self.play_animation:
            self.play_animation = animation
            if self.oneshot is None:
                self.frame_index = 0
                self.anim_timer.stop()
                self.schedule_frame()
        if bubble == self.play_bubble:
            self.update()
            return False
        self.play_bubble = bubble
        return self.refit()

    def grab_begin(self, pos):
        self.back.stop()
        self.react_timer.stop()
        self.oneshot = None
        shake = petcore.Shake()
        shake.add(pos.x(), int(time.time() * 1000))
        self.held = [pos - self.pos() - self.pet_rect.topLeft(), shake, pos]
        self.held_timer.start()
        self.follow()

    def grab_move(self, pos):
        self.held[1].add(pos.x(), int(time.time() * 1000))
        self.held[2] = pos
        self.follow()

    def follow(self):
        """커서를 따라가며 흔든 정도에 맞는 동작을 보인다. 펫이 커서 아래 그대로 있게 펫 자리를 기준으로 옮긴다."""
        if self.held is None:
            return
        offset, shake, pos = self.held
        self.set_play(*petcore.held_pose(shake, int(time.time() * 1000)))
        top_left = pos - offset - self.pet_rect.topLeft()
        self.place(top_left.x(), top_left.y())

    def grab_end(self):
        shake = self.held[1]
        self.held = None
        self.held_timer.stop()
        self.reaction = petcore.release_reaction(shake)
        self.back.setStartValue(self.pos())
        self.back.setEndValue(self.home)
        self.back.start()
        self.set_play("jumping", None)
        self.manager.layout()  # 말풍선이 상태로 돌아와 크기가 바뀌었으면 돌아갈 자리를 다시 잰다

    def react(self):
        """자리로 돌아오면 웃거나 운다."""
        animation, bubble, ms = self.reaction
        self.set_play(animation, bubble)
        self.manager.layout()
        self.react_timer.start(ms)

    def end_play(self):
        self.set_play(None, None)
        self.manager.layout()

    # ---- 그리기 ------------------------------------------------------------

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.bubble:
            self.paint_bubble(painter)
        if self.frames:
            frames = self.frames[self.current_animation()]
            painter.drawPixmap(self.pet_rect.topLeft(), frames[self.frame_index % len(frames)])
        else:
            self.paint_fallback(painter)
        if self.name_text:
            self.paint_name(painter)
        if self.badge:
            self.paint_badge(painter)
        if self.marked:
            self.paint_mark(painter)

    def paint_bubble(self, painter):
        style = "running" if self.play_bubble is not None else self.bubble_style
        fill, border = BUBBLE_COLORS.get(style, BUBBLE_COLORS["running"])
        rect = QRectF(self.bubble_rect).adjusted(1, 1, -1, -1)
        tail_x = int(min(max(self.pet_rect.center().x(), rect.left() + 14), rect.right() - 14))
        bottom = int(rect.bottom())
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        tail = QPainterPath()
        tail.addPolygon(QPolygonF([QPoint(tail_x - 7, bottom - 1), QPoint(tail_x + 7, bottom - 1),
                                   QPoint(tail_x, bottom + BUBBLE_TAIL - 1)]))
        painter.setPen(QPen(QColor(border), 1.5))
        painter.setBrush(QColor(fill))
        painter.drawPath(path.united(tail))
        painter.setPen(QColor("#222222"))
        painter.drawText(self.bubble_rect.adjusted(BUBBLE_PAD, BUBBLE_PAD, -BUBBLE_PAD, -BUBBLE_PAD),
                         Qt.TextWordWrap | Qt.AlignCenter, self.bubble)

    def paint_name(self, painter):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(30, 30, 30, 170))
        painter.drawRoundedRect(QRectF(self.name_rect), 6, 6)
        painter.setFont(self.name_font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(self.name_rect, Qt.AlignCenter, self.name_text)

    def paint_mark(self, painter):
        """펫 왼쪽 위 보라색 책갈피 (펫 영역 안이라 창 마스크에 이미 들어 있다)."""
        x, y = self.pet_rect.left() + 3, self.pet_rect.top() + 1
        w, h = 12, 18
        painter.setPen(QPen(QColor("#FFFFFF"), 1.5))
        painter.setBrush(QColor(MARK_COLOR))
        painter.drawPolygon(QPolygonF([QPoint(x, y), QPoint(x + w, y), QPoint(x + w, y + h),
                                       QPoint(x + w // 2, y + h - 5), QPoint(x, y + h)]))

    def paint_badge(self, painter):
        rect = QRectF(self.badge_rect).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor("#FFFFFF"), 1.5))
        painter.setBrush(QColor("#E8590C"))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        painter.setFont(self.badge_font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(self.badge_rect, Qt.AlignCenter, self.badge)

    def paint_fallback(self, painter):
        rect = QRectF(self.pet_rect).adjusted(6, 6, -6, -6)
        painter.setPen(QPen(QColor("#555555"), 2))
        painter.setBrush(QColor("#FFE9C7"))
        painter.drawEllipse(rect)
        painter.setPen(QColor("#333333"))
        painter.setFont(QFont(self.font().family(), max(8, int(rect.height() / 5)), QFont.Bold))
        painter.drawText(rect, Qt.AlignCenter, FALLBACK_FACES.get(self.state, "-.-"))

    # ---- 마우스: 관리자에게 넘긴다 -------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.held is None:
            self.manager.drag_begin(self.session_id, event.globalPos())
        elif event.button() == Qt.MiddleButton and self.manager.drag_origin is None and self.held is None:
            self.manager.hide_card()
            self.grab_begin(event.globalPos())

    def mouseMoveEvent(self, event):
        if self.held is not None:
            self.grab_move(event.globalPos())
        elif event.buttons() & Qt.LeftButton:
            self.manager.drag_move(event.globalPos())

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MiddleButton and self.held is not None:
            self.grab_end()
        elif event.button() == Qt.LeftButton and self.manager.drag_origin is not None:
            if not self.manager.drag_end():
                self.manager.on_click(self.session_id)

    def contextMenuEvent(self, event):
        self.manager.show_menu(self.session_id, event.globalPos())

    def enterEvent(self, _event):
        self.manager.hover_enter(self.session_id)

    def leaveEvent(self, _event):
        self.manager.hover_leave(self.session_id)


class UsageCard(QWidget):
    """펫에 마우스를 올리면 그 펫 위에 뜨는 카드: 세션 제목·상태, 컨텍스트, 계정 5시간·주간 사용량."""

    def __init__(self):
        # 카드가 마우스를 가로채면 펫에서 leave 가 와서 깜빡이므로 입력을 통과시킨다
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.NoDropShadowWindowHint | Qt.WindowDoesNotAcceptFocus
                         | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("Claude Pet Usage")
        family = self.font().family()
        self.title_font = QFont(family, 10, QFont.Bold)
        self.text_font = QFont(family, 9)
        self.note_font = QFont(family, 8)
        self.content = None
        self.title_text = ""
        self.label_w = self.pct_w = self.line_h = 0

    def set_content(self, title, subtitle, rows, note):
        content = (title, subtitle, tuple(rows), note)
        if content == self.content:
            return
        self.content = content
        title_metrics = QFontMetrics(self.title_font)
        text_metrics = QFontMetrics(self.text_font)
        self.title_text = title_metrics.elidedText(title, Qt.ElideRight, CARD_TITLE_MAX_W)
        self.label_w = max(text_metrics.horizontalAdvance(label) for label, _, _ in rows)
        self.pct_w = text_metrics.horizontalAdvance("100%")
        detail_w = max(text_metrics.horizontalAdvance(detail) for _, _, detail in rows)
        row_w = self.label_w + 8 + CARD_BAR_W + 6 + self.pct_w + 8 + detail_w
        width = max(title_metrics.horizontalAdvance(self.title_text),
                    text_metrics.horizontalAdvance(subtitle),
                    QFontMetrics(self.note_font).horizontalAdvance(note), row_w)
        self.line_h = text_metrics.height() + 3
        height = (title_metrics.height() + text_metrics.height() + 6 + len(rows) * self.line_h
                  + (QFontMetrics(self.note_font).height() + 2 if note else 0))
        self.resize(width + 2 * CARD_PAD, height + 2 * CARD_PAD)
        self.update()

    def show_above(self, window):
        """펫 창 위 가운데에 띄운다. 화면 밖으로 나가지 않게 맞춘다."""
        pet = window.geometry()
        screen = QApplication.screenAt(pet.center()) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(pet.center().x() - self.width() // 2, area.left() + 4), area.right() - self.width() - 4)
        y = max(pet.top() - self.height() - 6, area.top() + 4)
        self.move(x, y)
        if not self.isVisible():
            self.show()
        self.raise_()

    def paintEvent(self, _event):
        if not self.content:
            return
        _, subtitle, rows, note = self.content
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        painter.setBrush(QColor(28, 28, 32, 235))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)

        x, y = CARD_PAD, CARD_PAD
        title_metrics = QFontMetrics(self.title_font)
        painter.setFont(self.title_font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(x, y + title_metrics.ascent(), self.title_text)
        y += title_metrics.height()

        text_metrics = QFontMetrics(self.text_font)
        painter.setFont(self.text_font)
        painter.setPen(QColor("#B8B8C0"))
        painter.drawText(x, y + text_metrics.ascent(), subtitle)
        y += text_metrics.height() + 6

        for label, percent, detail in rows:
            baseline = y + text_metrics.ascent()
            painter.setPen(QColor("#E8E8EC"))
            painter.drawText(x, baseline, label)
            bar_x = x + self.label_w + 8
            bar = QRectF(bar_x, y + (text_metrics.height() - CARD_BAR_H) / 2, CARD_BAR_W, CARD_BAR_H)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 45))
            painter.drawRoundedRect(bar, CARD_BAR_H / 2, CARD_BAR_H / 2)
            if percent is not None and percent > 0:
                filled = QRectF(bar)
                filled.setWidth(max(CARD_BAR_H, CARD_BAR_W * min(percent, 100) / 100))
                painter.setBrush(QColor(usage_color(percent)))
                painter.drawRoundedRect(filled, CARD_BAR_H / 2, CARD_BAR_H / 2)
            pct_x = bar_x + CARD_BAR_W + 6
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(QRect(pct_x, y, self.pct_w, text_metrics.height()), Qt.AlignRight | Qt.AlignVCenter,
                             petcore.format_percent(percent) if percent is not None else "-")
            painter.setPen(QColor("#B8B8C0"))
            painter.drawText(pct_x + self.pct_w + 8, baseline, detail)
            y += self.line_h

        if note:
            painter.setFont(self.note_font)
            painter.setPen(QColor("#9A9AA2"))
            painter.drawText(x, y + 2 + QFontMetrics(self.note_font).ascent(), note)


class Gister(QObject):
    """Claude 가 남긴 말이나 생각을 Haiku(claude -p)로 한 줄 요약한다. 세션마다 요청은 하나만 돌리고,
    도는 동안 새 말이 오면 끝난 뒤 가장 새 말만 이어서 요약한다. 요약이 오면 on_done() 을 부른다."""

    def __init__(self, parent, on_done):
        super().__init__(parent)
        self.on_done = on_done
        self.claude = shutil.which("claude") or str(Path("~/.local/bin/claude").expanduser())
        self.gists = {}    # session_id -> (말, 요약). 요약이 실패했으면 요약은 ""
        self.running = {}  # session_id -> (QProcess, 말)
        self.wanted = {}   # session_id -> 다음에 요약할 가장 새 말
        self.failures = 0
        self.stopped = False
        self.enabled = True  # 메뉴에서 끄면 Haiku 를 부르지 않고 말의 끝 문장만 보여준다

    def gist(self, sid, said):
        """지금 보여줄 요약. 새 말이면 요약을 요청하고, 올 때까지는 지난 요약(없으면 말의 끝 문장)을 보여준다."""
        if not said:
            self.gists.pop(sid, None)
            self.wanted.pop(sid, None)
            return ""
        last = self.gists.get(sid)
        if last and last[0] == said:
            return last[1] or petcore.gist_fallback(said)
        if self.stopped or not self.enabled or not petcore.needs_gist(said):
            return petcore.gist_fallback(said)
        self.wanted[sid] = said
        self.start(sid)
        return last[1] if last and last[1] else petcore.gist_fallback(said)

    def forget(self, sids):
        """sids 에 없는(끝난) 세션의 요약을 버린다. 돌고 있는 요청은 끝나게 둔다."""
        for table in (self.gists, self.wanted):
            for sid in [s for s in table if s not in sids]:
                del table[sid]

    def start(self, sid):
        if self.stopped or sid in self.running or sid not in self.wanted:
            return
        said = self.wanted.pop(sid)
        args, stdin = petcore.gist_command(said, self.claude)
        proc = QProcess(self)
        proc.setProgram(args[0])
        proc.setArguments(args[1:])
        proc.setWorkingDirectory(str(Path.home()))
        proc.started.connect(lambda: (proc.write(stdin.encode("utf-8")), proc.closeWriteChannel()))
        proc.finished.connect(lambda code, status: self.finished(sid, code, status))
        proc.errorOccurred.connect(lambda error: self.failed_to_start(sid, error))
        QTimer(proc, singleShot=True, interval=GIST_TIMEOUT_MS, timeout=proc.kill).start()
        self.running[sid] = (proc, said)
        proc.start()

    def finished(self, sid, code, status):
        proc, said = self.running.pop(sid)
        gist = ""
        if status == QProcess.NormalExit and code == 0:
            gist = petcore.clean_gist(bytes(proc.readAllStandardOutput()).decode("utf-8", "replace"))
        if gist:
            self.failures = 0
        else:
            err = " ".join(bytes(proc.readAllStandardError()).decode("utf-8", "replace").split())
            print(f"claude-pet: 요약 실패 (exit {code}): {err[-300:]}", file=sys.stderr)
            self.failures += 1
            if self.failures >= GIST_MAX_FAILURES:
                self.stop(f"요약이 {self.failures}번 연달아 실패함")
        self.gists[sid] = (said, gist)
        proc.deleteLater()
        self.start(sid)
        self.on_done()

    def failed_to_start(self, sid, error):
        if error != QProcess.FailedToStart:  # 그 밖의 오류는 finished 로 온다
            return
        proc, said = self.running.pop(sid)
        self.gists[sid] = (said, "")
        proc.deleteLater()
        self.stop(f"요약용 claude 를 띄우지 못함: {self.claude}")

    def stop(self, reason):
        """요약을 끈다. 말풍선에는 Claude 가 남긴 말의 끝 문장을 보여준다."""
        self.stopped = True
        self.wanted.clear()
        print(f"claude-pet: {reason}. 요약을 끄고 말의 끝 문장을 보여줍니다.", file=sys.stderr)


class PetManager(QObject):
    """상태 파일을 읽어 세션마다 PetWindow 를 만들고 지우며, 한 줄로 세운다."""

    def __init__(self):
        super().__init__()
        self.config = load_config()
        self.state_dir = petcore.state_dir()
        self.desktop = Desktop()
        self.transcripts = petcore.TranscriptReader()
        self.gister = Gister(self, self.refresh)
        self.gister.enabled = self.gist_enabled()
        self.pets = petcore.find_pets(PET_SEARCH_DIRS)
        self.frames_cache = {}
        self.icon_cache = {}
        self.menu_icon_style = MenuIconStyle()  # 메뉴보다 오래 살아야 해서 들고 있는다
        self.windows = {}  # session_id -> PetWindow
        self.order = []
        self.sessions = {}
        self.acked = {}  # session_id -> 확인한 상태의 ts
        self.marks = petcore.load_marks(petcore.marks_file())  # 나중에 이어서 볼게 {session_id: {"set_at"}}
        self.view_states = {}  # session_id -> 지금 보이는 상태 (접혀서 안 보이는 세션도)
        self.titles_by_sid = {}
        self.folded_summary = False  # 접혔는데 손이 필요한 세션이 없어 요약 펫을 세웠는지
        self.card = UsageCard()
        self.hover_sid = None
        self.hover_timer = QTimer(self, singleShot=True, interval=HOVER_DELAY_MS, timeout=self.update_card)
        saved = self.config.get("assignments")
        self.assignments = ({k: v for k, v in saved.items() if isinstance(k, str) and isinstance(v, str)}
                            if isinstance(saved, dict) else {})
        self.anchor = self.initial_anchor()  # 줄의 오른쪽 아래 모서리
        self.positions = self.initial_positions()  # 따로 움직이기에서 옮긴 펫의 발밑 가운데 {session_id: [x, y]}
        self.drag_origin = None
        self.dragged = False
        self.warned_extension = False

        # XWayland 메뉴는 다른 창 클릭을 받지 못해서, 메뉴가 열린 동안 확장이 알려 주는 클릭으로 닫는다
        self.desktop.connect_clicked(self.on_screen_click)

        self.watcher = QFileSystemWatcher([str(self.state_dir)], self)
        self.watcher.directoryChanged.connect(self.refresh)
        self.tick = QTimer(self, interval=1000, timeout=self.refresh)
        self.tick.start()
        self.refresh()

    # ---- 설정 --------------------------------------------------------------

    def folded(self):
        return self.config.get("folded") is True

    def gist_enabled(self):
        return self.config.get("gist") is not False

    def free_layout(self):
        """펫 따로 움직이기. 접혀 있으면 한 마리만 보이므로 줄 자리에 세운다."""
        return self.config.get("free") is True and not self.folded()

    def scale(self):
        value = self.config.get("scale")
        return value if value in SCALES.values() else SCALES["보통"]

    def default_anchor(self):
        area = QApplication.primaryScreen().availableGeometry()
        return QPoint(area.right() - 20, area.bottom() - 10)

    def initial_anchor(self):
        saved = self.config.get("row_anchor")
        if (isinstance(saved, list) and len(saved) == 2 and all(isinstance(v, int) for v in saved)
                and QApplication.screenAt(QPoint(*saved)) is not None):
            return QPoint(*saved)
        return self.default_anchor()

    def initial_positions(self):
        saved = self.config.get("positions")
        if not isinstance(saved, dict):
            return {}
        return {sid: list(foot) for sid, foot in saved.items()
                if isinstance(foot, list) and len(foot) == 2 and all(isinstance(v, int) for v in foot)
                and QApplication.screenAt(QPoint(foot[0], foot[1] - 1)) is not None}

    def disabled_pets(self):
        saved = self.config.get("disabled_pets")
        return [p for p in saved if isinstance(p, str)] if isinstance(saved, list) else []

    def enabled_pets(self):
        """자동 배정과 '이 세션의 펫' 에 쓰는 켜진 펫들 (모두 꺼져 있으면 전체)."""
        ids = petcore.enabled_pet_ids([p["id"] for p in self.pets], self.disabled_pets())
        return [p for p in self.pets if p["id"] in ids]

    def save(self):
        if len(self.assignments) > MAX_SAVED_ASSIGNMENTS:
            keep = list(self.assignments.items())[-MAX_SAVED_ASSIGNMENTS:]
            self.assignments = dict(keep)
        self.config["assignments"] = self.assignments
        self.config["row_anchor"] = [self.anchor.x(), self.anchor.y()]
        self.positions = {sid: foot for sid, foot in self.positions.items() if sid in self.sessions}
        self.config["positions"] = self.positions
        save_config(self.config)

    # ---- 펫 이미지 ---------------------------------------------------------

    def pet_icon(self, pet):
        if pet["id"] not in self.icon_cache:
            self.icon_cache[pet["id"]] = load_icon(pet["sprite"])
        return self.icon_cache[pet["id"]]

    def pet_frames(self, pet_id):
        """(frames, pet_size). 이미지가 없거나 깨졌으면 frames 는 None (대체 얼굴을 그린다)."""
        if pet_id not in self.frames_cache:
            pet = next((p for p in self.pets if p["id"] == pet_id), None)
            frames = None
            if pet:
                try:
                    frames = load_frames(pet["sprite"], self.scale())
                except (OSError, ValueError) as err:
                    print(f"claude-pet: {pet['sprite']} 를 읽지 못함: {err}", file=sys.stderr)
            self.frames_cache[pet_id] = frames
        frames = self.frames_cache[pet_id]
        if frames:
            first = frames["idle"][0]
            return frames, (first.width(), first.height())
        side = round(150 * self.scale())
        return None, (side, side)

    # ---- 상태 --------------------------------------------------------------

    def refresh(self, *_):
        now = int(time.time() * 1000)
        sessions = {}
        for sid, session in petcore.load_sessions(self.state_dir).items():
            if petcore.is_alive(session):
                sessions[sid] = session
            else:  # 터미널을 그냥 닫아 SessionEnd 없이 끝난 세션
                (self.state_dir / f"{sid}.json").unlink(missing_ok=True)
                (petcore.usage_dir() / f"{sid}.json").unlink(missing_ok=True)
        self.sessions = sessions
        marks = petcore.active_marks(self.marks, sessions)  # 끝난 세션, 새 프롬프트가 온 세션은 풀린다
        if marks != self.marks:
            self.marks = marks
            self.save_marks()

        # 작업 중인 세션은 Claude 가 마지막으로 남긴 말이나 생각의 요약을 말풍선 첫 줄에 둔다
        gists = {}
        for sid, session in sessions.items():
            if petcore.effective_state(session, now, self.acked.get(sid)) == "running":
                _, said = self.transcripts.read(session.get("transcript"))
                gists[sid] = self.gister.gist(sid, said)
        self.gister.forget(sessions)
        views = petcore.session_views(sessions, now, self.acked, gists)
        self.view_states = {sid: state for sid, state, _ in views}
        # 펫 배정은 접혀서 안 보이는 세션까지 해 둬야 펼쳤을 때 펫이 바뀌지 않는다
        assigned = petcore.assign_pets([sid for sid, _, _ in views], [p["id"] for p in self.enabled_pets()],
                                       self.assignments)
        if any(self.assignments.get(sid) != pet for sid, pet in assigned.items()):
            self.assignments.update(assigned)
            self.save()

        badges, summaries = {}, {}
        self.folded_summary = False
        if self.folded():
            picked = petcore.folded_view(views, sessions, self.marks)
            views = []
            if picked:
                sid, state, bubble, more, summary = picked
                views = [(sid, state, bubble)]
                badges[sid] = f"+{more}" if more else ""
                if summary:
                    summaries[sid] = summary
                    self.folded_summary = True
        order = [sid for sid, _, _ in views]

        needs_layout = order != self.order
        for sid in [s for s in self.windows if s not in order]:
            window = self.windows.pop(sid)
            window.close()
            window.deleteLater()
            needs_layout = True

        for sid, state, bubble in views:
            session = sessions[sid]
            window = self.windows.get(sid)
            if window is None:
                window = self.windows[sid] = PetWindow(self, sid)
                needs_layout = True
            pet_id = assigned.get(sid)
            if not window.has_pet or window.pet_id != pet_id:
                window.set_pet(pet_id, *self.pet_frames(pet_id))
                needs_layout = True
            title = self.transcripts.read(session.get("transcript"))[0] or petcore.session_name(session)
            self.titles_by_sid[sid] = title
            name, name_max_w = (summaries[sid], SUMMARY_MAX_W) if sid in summaries else (title, NAME_MAX_W)
            marked = sid in self.marks
            bubble, style = petcore.marked_bubble(state, bubble) if marked else (bubble, state)
            if window.set_view(state, bubble, name, badges.get(sid, ""), name_max_w, style, marked):
                needs_layout = True

        self.order = order
        if needs_layout:
            self.layout()
        if self.card.isVisible():
            self.update_card()

    def layout(self):
        """처음 생긴 세션이 줄의 오른쪽 끝, 새 세션일수록 왼쪽. 모두 같은 바닥선에 선다.
        펫 따로 움직이기에서 옮긴 펫은 옮긴 자리에 서고, 안 옮긴 펫들이 줄에 남는다.
        휠로 들고 있거나 돌아가는 중인 펫은 자리만 알려 주고 옮기지 않는다."""
        screen = QApplication.screenAt(self.anchor) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        tallest = max((self.windows[sid].size_hint_[1] for sid in self.order), default=0)
        self.anchor = QPoint(min(max(self.anchor.x(), area.left() + 50), area.right() + 1),
                             min(max(self.anchor.y(), area.top() + tallest), area.bottom() + 1))
        sizes = {sid: self.windows[sid].size_hint_ for sid in self.order}
        positions = self.positions if self.free_layout() else {}
        spots = petcore.layout_spots(self.order, sizes, (self.anchor.x(), self.anchor.y()), ROW_GAP, positions)
        for sid in self.order:
            window = self.windows[sid]
            window.set_home(*spots[sid])
            if not window.isVisible():
                window.show()

    # ---- 마우스 ------------------------------------------------------------

    def hover_enter(self, sid):
        self.hover_sid = sid
        self.hover_timer.start()

    def hover_leave(self, sid):
        if self.hover_sid == sid:
            self.hide_card()

    def hide_card(self):
        self.hover_sid = None
        self.hover_timer.stop()
        self.card.hide()

    def update_card(self):
        """마우스를 올린 펫의 카드를 채워 그 펫 위에 띄운다 (떠 있는 동안 매초 다시 채운다)."""
        sid = self.hover_sid
        window = self.windows.get(sid)
        session = self.sessions.get(sid)
        if window is None or session is None or self.drag_origin is not None or window.playing():
            self.card.hide()
            return
        now = int(time.time() * 1000)
        usage = petcore.load_usage(petcore.usage_dir())
        rows, note = petcore.usage_rows(usage.get(sid), petcore.account_limits(usage), now)
        state = self.view_states.get(sid, session["state"])
        subtitle = f"{petcore.session_name(session)} · {petcore.STATE_LABELS.get(state, state)}"
        self.card.set_content(self.titles_by_sid.get(sid) or petcore.session_name(session), subtitle, rows, note)
        self.card.show_above(window)

    def drag_begin(self, sid, pos):
        """왼쪽 드래그 시작. 펫 따로 움직이기면 그 펫만, 아니면 줄 전체를 옮긴다."""
        self.hide_card()
        window = self.windows.get(sid)
        if self.free_layout() and window is not None:
            width, height = window.size_hint_
            self.drag_origin = (pos, sid, QPoint(window.home.x() + width // 2, window.home.y() + height))
        else:
            self.drag_origin = (pos, None, QPoint(self.anchor))
        self.dragged = False

    def drag_move(self, pos):
        if self.drag_origin is None:
            return
        start, sid, origin = self.drag_origin
        delta = pos - start
        if delta.manhattanLength() > 4:
            self.dragged = True
        if not self.dragged:
            return
        if sid is None:
            self.anchor = origin + delta
        elif sid in self.windows:
            # 펫 발밑 가운데를 커서가 있는 화면 안에 둔다
            area = (QApplication.screenAt(pos) or QApplication.primaryScreen()).availableGeometry()
            height = self.windows[sid].size_hint_[1]
            foot = origin + delta
            self.positions[sid] = [min(max(foot.x(), area.left()), area.right()),
                                   min(max(foot.y(), area.top() + height), area.bottom() + 1)]
        self.layout()

    def drag_end(self):
        """드래그였으면 True (클릭으로 치지 않는다)."""
        dragged = self.dragged
        if dragged:
            self.save()
        self.drag_origin = None
        self.dragged = False
        return dragged

    def on_click(self, sid):
        session = self.sessions.get(sid)
        window = self.windows.get(sid)
        if session is None or window is None:
            return
        if self.folded() and self.folded_summary:  # 기다리는 세션이 없을 때 요약 펫은 펼치기 단추
            self.set_folded(False)
            return
        if session["state"] in ("done", "error"):
            self.acked[sid] = session["ts"]
        if self.marks.pop(sid, None) is not None:  # 돌아왔으니 이어서 볼 표시를 푼다
            self.save_marks()
        window.play_jump()
        title = self.desktop.terminator_title(session.get("terminator_uuid"))
        if not self.desktop.activate(title) and not self.warned_extension:
            self.warned_extension = True
            print("claude-pet: 창을 올리지 못함 (GNOME 확장 claude-pet-focus 가 켜져 있는지, "
                  f"Terminator 창 제목 '{title}')", file=sys.stderr)
        self.refresh()

    # ---- 메뉴 --------------------------------------------------------------

    def show_menu(self, sid, pos):
        self.hide_card()
        menu = QMenu()

        if sid in self.marks:
            menu.addAction("표시 지우기", lambda: self.set_mark(sid, False))
        else:
            menu.addAction("나중에 이어서 볼게", lambda: self.set_mark(sid, True))
        menu.addSeparator()

        # 아이콘이 있는 체크 항목은 Qt 가 체크 표시 대신 아이콘 테두리로만 보여줘서,
        # 지금 펫은 굵은 글씨와 ✓ 로 표시한다
        pet_menu = menu.addMenu("이 세션의 펫")
        pet_menu.setStyle(self.menu_icon_style)
        if not self.pets:
            pet_menu.addAction("펫 없음 (pets/, pets-downloaded/, ~/.codex/pets)").setEnabled(False)
        window = self.windows.get(sid)
        for pet in self.enabled_pets():
            current = window is not None and pet["id"] == window.pet_id
            action = QAction(self.pet_icon(pet), pet["name"] + ("  ✓" if current else ""), pet_menu)
            if current:
                font = action.font()
                font.setBold(True)
                action.setFont(font)
            action.triggered.connect(lambda _=False, pid=pet["id"]: self.choose_pet(sid, pid))
            pet_menu.addAction(action)
        pet_menu.addSeparator()
        pet_menu.addAction("펫 다시 읽기", self.reload_pets)

        # 체크 상자 위젯은 눌러도 메뉴가 닫히지 않아 여러 펫을 연달아 켜고 끌 수 있다
        toggle_menu = menu.addMenu("펫 켜고 끄기")
        disabled = set(self.disabled_pets())
        toggles = []
        for pet in self.pets:
            box = QCheckBox(pet["name"])
            box.setIcon(self.pet_icon(pet))
            box.setIconSize(QSize(MENU_ICON_SIZE, MENU_ICON_SIZE))
            box.setChecked(pet["id"] not in disabled)
            box.setStyleSheet("QCheckBox { padding: 3px 12px 3px 8px; }")
            box.toggled.connect(lambda on, pid=pet["id"]: self.set_pet_enabled(pid, on, toggles))
            item = QWidgetAction(toggle_menu)
            item.setDefaultWidget(box)
            toggle_menu.addAction(item)
            toggles.append(box)
        self.lock_last_enabled(toggles)

        size_menu = menu.addMenu("크기")
        size_group = QActionGroup(size_menu)
        for label, value in SCALES.items():
            action = QAction(label, size_menu, checkable=True, checked=value == self.scale())
            action.triggered.connect(lambda _=False, v=value: self.choose_scale(v))
            size_group.addAction(action)
            size_menu.addAction(action)

        fold = QAction("접기 (기다리는 세션만 보기)", menu, checkable=True, checked=self.folded())
        fold.triggered.connect(self.set_folded)
        menu.addAction(fold)
        free = QAction("펫 따로 움직이기", menu, checkable=True, checked=self.config.get("free") is True)
        free.triggered.connect(self.set_free)
        menu.addAction(free)
        if self.free_layout():
            menu.addAction("다시 줄 세우기", self.line_up)
        else:
            menu.addAction("줄 위치 처음으로", self.reset_anchor)
        gist = QAction("작업 요약 (Haiku 로 말풍선 요약)", menu, checkable=True, checked=self.gist_enabled())
        gist.triggered.connect(self.set_gist)
        menu.addAction(gist)
        menu.addSeparator()
        autostart = QAction("새 Claude 세션에서 자동 실행", menu, checkable=True,
                            checked=not DISABLED_FLAG.exists())
        autostart.triggered.connect(self.set_autostart)
        menu.addAction(autostart)
        menu.addAction("종료", QApplication.quit)
        self.desktop.watch_clicks(True)
        try:
            menu.exec_(pos)
        finally:
            self.desktop.watch_clicks(False)

    @pyqtSlot(QDBusMessage)
    def on_screen_click(self, message):
        """메뉴가 열린 동안 메뉴·펫 밖을 누르면 메뉴를 닫는다 (메뉴·펫 위 클릭은 Qt 가 처리한다)."""
        args = message.arguments()
        if len(args) != 2 or QApplication.activePopupWidget() is None:
            return
        point = QPoint(int(args[0]), int(args[1]))
        for widget in QApplication.topLevelWidgets():
            if (widget.isVisible() and isinstance(widget, (QMenu, PetWindow))
                    and widget.geometry().contains(point)):
                return
        for _ in range(10):  # 하위 메뉴까지 차례로 닫는다
            popup = QApplication.activePopupWidget()
            if popup is None:
                break
            popup.close()

    def choose_pet(self, sid, pet_id):
        self.pets = petcore.find_pets(PET_SEARCH_DIRS)  # 새로 받은 펫도 보이게
        self.assignments.pop(sid, None)
        self.assignments[sid] = pet_id  # 맨 뒤로 보내 오래 남게 한다
        self.save()
        window = self.windows.get(sid)
        if window:
            window.set_pet(pet_id, *self.pet_frames(pet_id))
            self.layout()

    def set_mark(self, sid, marked):
        if marked:
            self.marks[sid] = {"set_at": int(time.time() * 1000)}
        else:
            self.marks.pop(sid, None)
        self.save_marks()
        self.refresh()
        window = self.windows.get(sid)
        if marked and window is not None:
            window.play_jump()

    def save_marks(self):
        try:
            petcore.save_marks(petcore.marks_file(), self.marks)
        except OSError as err:
            print(f"claude-pet: 이어서 볼 표시 저장 실패: {err}", file=sys.stderr)

    def set_pet_enabled(self, pet_id, enabled, toggles=()):
        """펫을 켜거나 끈다. 끈 펫을 쓰던 세션은 refresh 에서 켜진 펫으로 다시 배정된다."""
        disabled = set(self.disabled_pets())
        if enabled:
            disabled.discard(pet_id)
        else:
            disabled.add(pet_id)
        self.config["disabled_pets"] = sorted(disabled)
        self.save()
        self.lock_last_enabled(toggles)
        self.refresh()

    @staticmethod
    def lock_last_enabled(toggles):
        """켜진 펫이 하나만 남으면 그 체크 상자는 끌 수 없게 흐리게 한다."""
        last_one = sum(box.isChecked() for box in toggles) <= 1
        for box in toggles:
            box.setEnabled(not (last_one and box.isChecked()))

    def reload_pets(self):
        """펫 폴더를 다시 읽는다. 새로 넣거나 고친 펫을 앱을 다시 켜지 않고 볼 수 있다."""
        self.pets = petcore.find_pets(PET_SEARCH_DIRS)
        self.frames_cache.clear()
        self.icon_cache.clear()
        for window in self.windows.values():
            window.has_pet = False  # refresh 에서 이미지를 새로 읽는다
        self.refresh()

    def choose_scale(self, value):
        self.config["scale"] = value
        self.frames_cache.clear()
        for window in self.windows.values():
            window.set_pet(window.pet_id, *self.pet_frames(window.pet_id))
        self.layout()
        self.save()

    def set_folded(self, folded):
        self.config["folded"] = bool(folded)
        self.hide_card()
        self.save()
        self.refresh()

    def set_free(self, enabled):
        self.config["free"] = bool(enabled)
        self.hide_card()
        self.save()
        self.layout()

    def line_up(self):
        """따로 옮긴 펫들을 모두 줄로 되돌린다."""
        self.positions.clear()
        self.save()
        self.layout()

    def set_gist(self, enabled):
        self.config["gist"] = bool(enabled)
        self.gister.enabled = bool(enabled)
        self.save()
        self.refresh()

    def reset_anchor(self):
        self.anchor = self.default_anchor()
        self.layout()
        self.save()

    def set_autostart(self, enabled):
        try:
            if enabled:
                DISABLED_FLAG.unlink(missing_ok=True)
            else:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                DISABLED_FLAG.touch()
        except OSError as err:
            print(f"claude-pet: 자동 실행 설정 실패: {err}", file=sys.stderr)


def main():
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    lock = acquire_single_instance(petcore.state_dir())
    if lock is None:
        print("claude-pet: 이미 실행 중", file=sys.stderr)
        return 0
    app = QApplication(sys.argv)
    # 세션이 하나도 없으면 창이 없지만, 새 세션을 기다려야 하므로 꺼지지 않는다 (종료는 메뉴에서)
    app.setQuitOnLastWindowClosed(False)
    manager = PetManager()  # noqa: F841  (app 이 도는 동안 살아 있어야 한다)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
