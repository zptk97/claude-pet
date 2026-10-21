"""등반 일지 창: 한 주가 한 쪽이고 ◀ ▶ (← →) 로 넘긴다. 요약은 journalcore.week_summary 가 만든다.

주간 사용량과 API 금액, 요금제 값의 배수와 남은 몫, 한 줄 진단, 하루 금액 막대, 5시간 창 막대 (하루 칸과 같은 시간
축에 창이 시작한 자리로), 높이·등반 횟수·벽의 끝을 그린다. 열려 있는 동안 REFRESH_MS 마다 다시 읽는다.
크기 (px) 는 글자 배율 (DPI, GNOME 큰 글씨) 을 따라 키우고, 그래도 넘치는 줄은 말줄임으로 자른다.
"""
import math
import time
from datetime import datetime

from PyQt5.QtCore import QRect, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen
from PyQt5.QtWidgets import QWidget

import climbcore
import journalcore
import petcore
from journalcore import money, percent, span, when

REFRESH_MS = 10_000
WIDTH = 460         # 아래 크기는 96 DPI 기준 (px)
PAD = 16
ARROW = 28          # ◀ ▶ 버튼 한 변
DAY_H = 84          # 하루 금액 막대가 가장 높을 때
WINDOW_H = 44       # 5시간 창 막대가 100% 일 때
BG = QColor(28, 28, 32)
TEXT = QColor("#E8E8EC")
MUTED = QColor("#B8B8C0")
DIM = QColor("#7A7A84")
ACCENT = QColor(255, 196, 0)
DAY_COLOR = QColor(99, 179, 237)
TALLY_NAMES = {"crux": "크럭스", "dyno": "다이노", "fall": "로프 추락", "nap": "낮잠"}


def level_color(pct):
    """사용량 막대 색 (펫 카드와 같다): 50% 미만 초록, 80% 미만 노랑, 그 이상 빨강."""
    return QColor("#48BB78" if pct < 50 else "#ECC94B" if pct < 80 else "#F56565")


def day_label(ts):
    at = datetime.fromtimestamp(ts, climbcore.KST)
    return f"{at:%m/%d}({journalcore.WEEKDAYS[at.weekday()]}) {at:%H:%M}"


def passed_name(height):
    """height 까지 넘은 가장 높은 기록 이름 (없으면 None)."""
    names = [name for m, name, _ in climbcore.MILESTONES if m <= height]
    return names[-1] if names else None


class JournalWindow(QWidget):
    """source() 는 넘겨 볼 주의 요약 목록 (오래된 주부터 이번 주까지, journalcore.week_summary)."""

    def __init__(self, source):
        super().__init__(None, Qt.Window)
        self.setWindowTitle("등반 일지")
        self.source = source
        self.pages = []
        self.back = 0                     # 이번 주에서 몇 주 앞을 보는지
        self.shown = None                 # 보고 있는 지난 주의 리셋 시각 (이번 주면 None): 새 주가 와도 그 주에 머문다
        self.arrows = (QRect(), QRect())  # 앞 주, 다음 주 버튼
        family = self.font().family()
        self.title_font = QFont(family, 11, QFont.Bold)
        self.text_font = QFont(family, 9)
        self.bold_font = QFont(family, 9, QFont.Bold)
        self.small_font = QFont(family, 8)
        self.u = max(1.0, self.logicalDpiY() / 96)   # 글자 (pt) 가 커지는 만큼 창도 키운다
        self.W = self.px(WIDTH)
        self.timer = QTimer(self, timeout=self.refresh, interval=REFRESH_MS)

    def px(self, n):
        return round(n * self.u)

    def open(self):
        """이번 주 쪽으로 열고, 이미 열려 있으면 (내려 두었어도) 앞으로 올린다."""
        self.back, self.shown = 0, None
        self.refresh()
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def refresh(self):
        self.pages = self.source()
        back = 0
        if self.shown is not None:   # 보던 지난 주를 다시 찾는다 (없어졌으면 가장 오래된 주)
            for i, page in enumerate(reversed(self.pages)):
                back = i
                if page["reset"] <= self.shown + petcore.WEEK_SLACK_S:
                    break
        self.back = back
        self.fit()
        self.update()

    def fit(self):
        """그려 보고 그 높이로 창 크기를 맞춘다."""
        image = QImage(1, 1, QImage.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        height = self.draw(painter, time.time())
        painter.end()
        self.setFixedSize(self.W, height)

    def turn(self, step):
        """step > 0 이면 앞 주로, < 0 이면 다음 주로."""
        back = max(0, min(self.back + step, len(self.pages) - 1))
        if back != self.back:
            self.back = back
            self.shown = self.pages[-1 - back]["reset"] if back else None
            self.fit()
            self.update()

    def showEvent(self, event):
        self.timer.start()
        if event.spontaneous():   # 내려 두었다 다시 띄우면 바로 새로 (타이머를 기다리지 않고)
            self.refresh()

    def hideEvent(self, _event):
        self.timer.stop()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Left:
            self.turn(1)
        elif event.key() == Qt.Key_Right:
            self.turn(-1)
        elif event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        if self.arrows[0].contains(event.pos()):
            self.turn(1)
        elif self.arrows[1].contains(event.pos()):
            self.turn(-1)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), BG)
        self.draw(painter, time.time())

    # ---- 그리기 (그린 아래 끝 y 를 돌려준다) ------------------------------------

    def text(self, p, font, color, x, y, text, width=None, align=Qt.AlignLeft):
        """y 를 위로 한 줄을 쓰고 (width, 없으면 창 오른쪽 여백까지 넘치면 말줄임) 그 줄 높이를 돌려준다."""
        width = self.W - self.px(PAD) - x if width is None else width
        metrics = QFontMetrics(font)
        p.setFont(font)
        p.setPen(color)
        p.drawText(QRect(int(x), int(y), int(width), metrics.height()), align | Qt.AlignVCenter,
                   metrics.elidedText(text, Qt.ElideRight, int(width)))
        return metrics.height()

    def draw(self, p, now):
        pad = self.px(PAD)
        inner = self.W - 2 * pad
        y = pad
        if not self.pages:
            y += self.text(p, self.text_font, MUTED, pad, y, "주간 사용량을 받으면 주마다 기록이 보여요", inner, Qt.AlignHCenter)
            return y + pad
        s = self.pages[len(self.pages) - 1 - self.back]
        y = self.draw_header(p, s, y, pad, inner)
        y = self.draw_week(p, s, y + self.px(10), pad, inner)
        y = self.draw_days(p, s, y + self.px(14), pad, inner, now)
        y = self.draw_windows(p, s, y + self.px(14), pad, inner, now)
        y = self.draw_climb(p, s, y + self.px(14), pad)
        since = datetime.fromtimestamp(s["since"], climbcore.KST)
        y += self.px(10) + self.text(p, self.small_font, DIM, pad, y + self.px(10),
                                     f"사용량 %는 {since:%m/%d}부터 기록 · 금액은 이 컴퓨터의 Claude Code 기록을 API 가격으로 셈")
        return y + pad

    def draw_header(self, p, s, y, pad, inner):
        older, newer = self.back < len(self.pages) - 1, self.back > 0
        arrow = self.px(ARROW)
        self.arrows = (QRect(pad, y + self.px(4), arrow, arrow), QRect(self.W - pad - arrow, y + self.px(4), arrow, arrow))
        for rect, label, enabled in ((self.arrows[0], "◀", older), (self.arrows[1], "▶", newer)):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 28 if enabled else 10))
            p.drawRoundedRect(QRectF(rect), 6, 6)
            p.setFont(self.text_font)
            p.setPen(TEXT if enabled else QColor(255, 255, 255, 50))
            p.drawText(rect, Qt.AlignCenter, label)
        title = "이번 주" if s["current"] else journalcore.weeks_ago(self.back)
        x, width = pad + arrow + self.px(6), inner - 2 * (arrow + self.px(6))
        y += self.text(p, self.title_font, TEXT, x, y, title, width, Qt.AlignHCenter)
        y += self.text(p, self.small_font, MUTED, x, y + 2, f"{day_label(s['start'])} ~ {day_label(s['reset'])}",
                       width, Qt.AlignHCenter) + 2
        return max(y, pad + self.px(4) + arrow)

    def draw_week(self, p, s, y, pad, inner):
        pct = s["pct"]
        metrics = QFontMetrics(self.bold_font)
        label = f"주간 {percent(pct)}" if pct is not None else "주간 -"
        label_w = max(metrics.horizontalAdvance("주간 100%"), metrics.horizontalAdvance(label))
        self.text(p, self.bold_font, TEXT, pad, y, label, label_w)
        right = f"API {money(s['usd'])}"
        right_w = metrics.horizontalAdvance(right)
        self.text(p, self.bold_font, TEXT, pad, y, right, inner, Qt.AlignRight)
        bar_h = self.px(8)
        bar = QRectF(pad + label_w + self.px(10), y + (metrics.height() - bar_h) / 2,
                     max(0, inner - label_w - right_w - self.px(24)), bar_h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 45))
        p.drawRoundedRect(bar, bar_h / 2, bar_h / 2)
        if pct:
            filled = QRectF(bar)
            filled.setWidth(max(bar_h, bar.width() * min(pct, 100) / 100))
            p.setBrush(level_color(pct))
            p.drawRoundedRect(filled, bar_h / 2, bar_h / 2)
        y += metrics.height() + self.px(4)
        parts = [f"요금제 값(주 ${journalcore.PLAN_WEEK_USD:.2f})의 {s['multiple']:.1f}배", f"1% ≈ ${s['rate']:.1f}"]
        if s["left_pct"]:   # 다 썼으면 남은 몫은 빼고
            parts.append(f"{'남은' if s['current'] else '남긴'} {percent(s['left_pct'])} ≈ {money(s['left_usd'])}어치")
        y += self.text(p, self.text_font, MUTED, pad, y, " · ".join(parts)) + self.px(8)
        box_h = metrics.height() + self.px(12)
        p.setPen(QPen(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 110), 1))
        p.setBrush(QColor(ACCENT.red(), ACCENT.green(), ACCENT.blue(), 34))
        p.drawRoundedRect(QRectF(pad + 0.5, y + 0.5, inner - 1, box_h - 1), 6, 6)
        self.text(p, self.bold_font, TEXT, pad + self.px(10), y + self.px(6), s["diagnosis"], inner - self.px(20))
        return y + box_h

    def draw_days(self, p, s, y, pad, inner, now):
        small = QFontMetrics(self.small_font)
        start = datetime.fromtimestamp(s["start"], climbcore.KST)
        y += self.text(p, self.bold_font, TEXT, pad, y, f"하루 금액 ({start:%H:%M}부터 하루씩)") + 2
        col = inner / 7
        day_h = self.px(DAY_H)
        top = y + small.height()
        peak = max(max(s["days"]), 1.0)
        today = int((now - s["start"]) // journalcore.DAY_S) if s["current"] else 7
        for i, usd in enumerate(s["days"]):
            x = pad + i * col
            color = ACCENT if i == today else DAY_COLOR
            if usd >= 0.5:
                h = max(2.0, day_h * usd / peak)
                p.setPen(Qt.NoPen)
                p.setBrush(color)
                p.drawRoundedRect(QRectF(x + col * 0.22, top + day_h - h, col * 0.56, h), 3, 3)
                self.text(p, self.small_font, MUTED, x, top + day_h - h - small.height() - 1, money(usd), col, Qt.AlignHCenter)
            at = datetime.fromtimestamp(s["start"] + i * journalcore.DAY_S, climbcore.KST)
            shade = ACCENT if i == today else DIM if i > today else MUTED
            self.text(p, self.small_font, shade, x, top + day_h + 3, f"{journalcore.WEEKDAYS[at.weekday()]} {at.day}",
                      col, Qt.AlignHCenter)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawLine(pad, int(top + day_h) + 1, pad + inner, int(top + day_h) + 1)
        y = int(top + day_h + 3 + small.height() + self.px(4))
        days = min(7, math.ceil((now - s["start"]) / journalcore.DAY_S)) if s["current"] else 7
        line = f"일한 시간 {s['work_hours']}시간 · 하루 평균 {money(s['usd'] / max(1, days))}"
        return y + self.text(p, self.text_font, MUTED, pad, y, line)

    def draw_windows(self, p, s, y, pad, inner, now):
        windows = s["windows"]
        if windows:
            avg = sum(w["peak"] for w in windows) / len(windows)
            line = f"5시간 창 {len(windows)}개 · 평균 {percent(avg)} · 다 참 {s['full_windows']}번"
            if s["blocked"] > 0:
                line += f" (막힘 {span(s['blocked'])})"
        else:
            line = "5시간 창 기록 없음"
        y += self.text(p, self.bold_font, TEXT, pad, y, line) + self.px(4)
        window_h = self.px(WINDOW_H)
        area = QRectF(pad, y, inner, window_h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 14))
        p.drawRect(area)
        p.setPen(QPen(QColor(255, 255, 255, 30), 1))
        for i in range(1, 7):   # 하루 칸 경계 (하루 금액 막대와 같은 자리)
            x = pad + inner * i / 7
            p.drawLine(int(x), int(y), int(x), int(y + window_h))
        p.save()
        p.setClipRect(area)
        width = max(3.0, inner * journalcore.WINDOW_S / journalcore.WEEK_S - 1)
        for w in windows:
            x = pad + inner * (w["start"] - s["start"]) / journalcore.WEEK_S
            h = max(2.0, window_h * min(w["peak"], 100) / 100)
            p.setPen(Qt.NoPen)
            p.setBrush(level_color(w["peak"]))
            p.drawRect(QRectF(x, y + window_h - h, width, h))
        if s["current"]:   # 지금
            x = pad + inner * (now - s["start"]) / journalcore.WEEK_S
            p.setPen(QPen(ACCENT, 1, Qt.DashLine))
            p.drawLine(int(x), int(y), int(x), int(y + window_h))
        p.restore()
        return int(y + window_h)

    def draw_climb(self, p, s, y, pad):
        y += self.text(p, self.bold_font, TEXT, pad, y, "등반") + 2
        height = s["height"]
        if height is None:
            line = "높이 기록 없음"
        else:
            name = passed_name(height)
            line = f"높이 {climbcore.fmt_alt(height)}" + (" · 달" if height >= climbcore.MOON else f" · {name} 넘음" if name else "")
        y += self.text(p, self.text_font, MUTED, pad, y, line) + 2
        counts = [f"{TALLY_NAMES[k]} {s['climb'][k]}" for k in journalcore.TALLY if s["climb"].get(k)]
        y += self.text(p, self.text_font, MUTED, pad, y, " · ".join(counts) if counts else "등반 횟수 기록 없음") + 2
        kind, at = s["end"]
        end = {"going": f"진행 중 (리셋 {when(s['reset'])})", "moon": f"달 도착 ({when(at)})" if at else "달 도착",
               "reset": f"리셋에서 줄 끊김 ({when(at)})" if at else "리셋에서 줄 끊김"}.get(kind, "기록 없음")
        return y + self.text(p, self.text_font, MUTED, pad, y, f"벽의 끝: {end}")
