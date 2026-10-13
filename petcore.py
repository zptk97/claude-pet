"""claude-pet 의 Qt 없는 부분: 펫 이미지 규격, 세션 상태 계산, 말풍선 문구, 펫 배정.

상태 파일은 hooks/claude-pet-hook.sh 가 세션마다 하나씩 쓴다.
    {"seq": "<ns>", "ts": <ms>, "since": <ms>, "created": <ms>, "state": "running",
     "cwd": "...", "tool": "Bash", "claude_pid": "123", "claude_start": "456",
     "terminator_uuid": "urn:uuid:...", "bg_tasks": 1, "bg_desc": "...", "crons": 0}

background 는 턴이 끝났지만 백그라운드 작업(bg_tasks 개, 첫 작업 설명 bg_desc)이나
깨우기 예약(crons 개)이 남아 세션이 다시 깨어날 상태다.

사용량 파일은 hooks/claude-pet-statusline.sh 가 상태줄이 그려질 때마다 usage/ 아래에 쓴다.
    {"ts": <ms>, "context_pct": 62, "context_size": 200000, "context_tokens": 124010,
     "five_hour_pct": 23.5, "five_hour_reset": <epoch 초>, "seven_day_pct": 41, "seven_day_reset": <epoch 초>}
"""

import json
import os
import time
import zlib
from collections import Counter
from pathlib import Path

# Codex 펫 아틀라스: 8열 x 9행, 칸 192x208 (openai/skills hatch-pet animation-rows.md)
CELL_W, CELL_H = 192, 208
ATLAS_COLS, ATLAS_ROWS = 8, 9
ATLAS_W, ATLAS_H = CELL_W * ATLAS_COLS, CELL_H * ATLAS_ROWS

# 애니메이션 이름 -> (행, 프레임별 ms)
ANIMATIONS = {
    "idle": (0, [280, 110, 110, 140, 140, 320]),
    "running-right": (1, [120] * 7 + [220]),
    "running-left": (2, [120] * 7 + [220]),
    "waving": (3, [140] * 3 + [280]),
    "jumping": (4, [140] * 4 + [280]),
    "failed": (5, [140] * 7 + [240]),
    "waiting": (6, [150] * 5 + [260]),
    "running": (7, [120] * 5 + [220]),
    "review": (8, [150] * 5 + [280]),
}

# 펫 상태 -> 애니메이션
STATE_ANIMATION = {
    "idle": "idle",
    "running": "running",
    "waiting": "waiting",
    "done": "waving",
    "error": "failed",
    "background": "review",
}

# running/waiting 이 이만큼 조용하면 Esc 중단 등으로 Stop 이 안 온 것으로 보고 idle 처리
STALE_ACTIVE_MS = 30 * 60 * 1000
# PID 를 모르는 세션은 이보다 오래 조용하면 SessionEnd 없이 죽은 것으로 보고 무시
# (PID 가 있으면 살아 있는지 직접 확인하므로 밤새 도는 실험도 계속 보인다)
EXPIRE_MS = 12 * 60 * 60 * 1000
# 말풍선에 넣는 백그라운드 작업 설명의 최대 글자 수
BG_DESC_MAX = 24


def state_dir():
    """hook 스크립트와 같은 규칙: $XDG_RUNTIME_DIR/claude-pet, 없으면 /tmp/claude-pet-<uid>/claude-pet."""
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/claude-pet-{os.getuid()}"
    return Path(base) / "claude-pet"


def frame_rects(animation):
    """애니메이션 프레임들의 아틀라스 내 (x, y, w, h)."""
    row, durations = ANIMATIONS[animation]
    return [(col * CELL_W, row * CELL_H, CELL_W, CELL_H) for col in range(len(durations))]


def find_pets(dirs):
    """pet.json + 스프라이트가 있는 폴더를 찾는다. 받아온 데이터라 경로는 폴더 안으로 제한한다.

    반환: [{"id", "name", "description", "sprite": Path}] (id 순, 같은 id 는 앞 폴더 우선)
    """
    pets = {}
    for base in dirs:
        base = Path(base).expanduser()
        if not base.is_dir():
            continue
        for pet_dir in sorted(base.iterdir()):
            meta_path = pet_dir / "pet.json"
            if not meta_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(meta, dict):
                continue
            sprite_name = meta.get("spritesheetPath") or "spritesheet.webp"
            if not isinstance(sprite_name, str) or Path(sprite_name).name != sprite_name:
                continue  # 하위 경로나 ../ 는 받지 않는다
            sprite = pet_dir / sprite_name
            if not sprite.is_file():
                continue
            pet_id = str(meta.get("id") or pet_dir.name)
            pets.setdefault(pet_id, {
                "id": pet_id,
                "name": str(meta.get("displayName") or pet_id),
                "description": str(meta.get("description") or ""),
                "sprite": sprite,
            })
    return [pets[k] for k in sorted(pets)]


def load_sessions(directory):
    """상태 파일들을 읽는다. 깨진 파일은 건너뛴다. 반환: {session_id: dict}"""
    sessions = {}
    directory = Path(directory)
    if not directory.is_dir():
        return sessions
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("state") not in STATE_ANIMATION:
            continue
        try:
            data["ts"] = int(data.get("ts", 0))
            data["since"] = int(data.get("since", data["ts"]))
            data["created"] = int(data.get("created", data["ts"]))
            data["prompt_ts"] = int(data.get("prompt_ts") or 0)
        except (TypeError, ValueError):
            continue
        sessions[path.stem] = data
    return sessions


def is_alive(session, proc_root="/proc"):
    """훅이 남긴 claude 프로세스가 아직 살아 있는지. PID 정보가 없으면 살아 있다고 본다.

    PID 가 재사용됐을 수 있어서 프로세스 시작 시각(/proc/<pid>/stat 22번째 필드)도 맞춰 본다.
    """
    pid = str(session.get("claude_pid") or "")
    if not pid.isdigit():
        return True
    try:
        stat = (Path(proc_root) / pid / "stat").read_text()
    except OSError:
        return False
    start = str(session.get("claude_start") or "")
    if not start:
        return True
    fields = stat.rsplit(")", 1)[-1].split()  # comm 에 공백이 있어도 되게 ')' 뒤부터 센다
    return len(fields) > 19 and fields[19] == start


def effective_state(session, now_ms, acked_ts=None):
    """오래된 상태와 확인(클릭)한 완료/오류를 반영한 상태. 무시할 세션이면 None."""
    age = now_ms - session["ts"]
    if age > EXPIRE_MS and not str(session.get("claude_pid") or "").isdigit():
        return None
    state = session["state"]
    if state in ("running", "waiting") and age > STALE_ACTIVE_MS:
        return "idle"
    if state in ("done", "error") and acked_ts is not None and acked_ts >= session["ts"]:
        return "idle"
    return state


def format_elapsed(ms):
    seconds = max(0, int(ms // 1000))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}시간 {minutes}분"
    if minutes:
        return f"{minutes}분 {seconds}초"
    return f"{seconds}초"


def shorten(text, limit):
    """공백을 한 칸으로 줄이고 limit 글자를 넘으면 … 로 자른다."""
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _count(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def session_name(session):
    """작업 폴더 이름 (창 제목을 못 얻을 때 이름표로 쓴다)."""
    cwd = str(session.get("cwd") or "").rstrip("/")
    return os.path.basename(cwd) or "Claude"


def clean_title(title):
    """Claude Code 가 붙이는 앞쪽 상태 글리프(✳, ◐ 등)와 공백을 뗀다. GNOME 확장도 같은 규칙으로 맞춘다."""
    title = str(title or "")
    i = 0
    while i < len(title) and not title[i].isalnum():
        i += 1
    return title[i:].strip()


class TitleReader:
    """기록 파일(transcript jsonl)에서 마지막 aiTitle 을 읽는다. 파일이 커진 만큼만 이어서 읽는다."""

    CHUNK = 1 << 20

    def __init__(self):
        self._files = {}  # path -> (읽은 위치, 제목, 아직 줄바꿈이 안 온 마지막 조각)

    def title(self, path):
        if not path:
            return ""
        offset, title, partial = self._files.get(path, (0, "", b""))
        try:
            size = os.path.getsize(path)
            if size < offset:  # 파일이 새로 쓰였다
                offset, title, partial = 0, "", b""
            with open(path, "rb") as f:
                f.seek(offset)
                while offset < size:
                    chunk = f.read(min(self.CHUNK, size - offset))
                    if not chunk:
                        break
                    offset += len(chunk)
                    lines = (partial + chunk).split(b"\n")
                    partial = lines.pop()
                    for line in lines:
                        title = self._title_in(line) or title
        except OSError:
            pass
        self._files[path] = (offset, title, partial)
        return title

    @staticmethod
    def _title_in(line):
        if b'"aiTitle"' not in line:
            return ""
        try:
            entry = json.loads(line)
        except ValueError:
            return ""
        value = entry.get("aiTitle") if isinstance(entry, dict) else None
        return value if isinstance(value, str) else ""


def status_text(session, state, now_ms):
    """말풍선 문구. 쉬는 중이면 빈 문자열 (이름표가 따로 있어서 세션 이름은 넣지 않는다)."""
    if state == "running":
        tool = session.get("tool") or ""
        doing = f"{tool} 실행 중" if tool else "생각 중"
        return f"{doing} · {format_elapsed(now_ms - session['since'])}"
    if state == "waiting":
        return "확인이 필요해요!"
    if state == "done":
        return f"다 했어요! ({format_elapsed(session['ts'] - session['since'])})"
    if state == "error":
        return "오류로 멈췄어요"
    if state == "background":
        return f"{background_text(session)} · {format_elapsed(now_ms - session['since'])}"
    return ""


def background_text(session):
    tasks = _count(session.get("bg_tasks"))
    desc = shorten(session.get("bg_desc"), BG_DESC_MAX)
    if tasks == 0:
        return "깨우기 예약됨" if _count(session.get("crons")) else "백그라운드 대기"
    if not desc:
        return f"백그라운드 작업 {tasks}개"
    if tasks == 1:
        return f"백그라운드: {desc}"
    return f"백그라운드 {tasks}개: {desc} 외"


def session_views(sessions, now_ms, acked=None):
    """펫 줄에 세울 세션들: [(session_id, state, 말풍선)] 를 처음 생긴 순서대로."""
    acked = acked or {}
    views = []
    for sid, session in sorted(sessions.items(), key=lambda kv: (kv[1]["created"], kv[0])):
        state = effective_state(session, now_ms, acked.get(sid))
        if state is not None:
            views.append((sid, state, status_text(session, state, now_ms)))
    return views


def enabled_pet_ids(pet_ids, disabled):
    """켜진 펫 id 들 (순서 유지). 모두 꺼져 있으면 펫이 사라지지 않게 전체를 돌려준다."""
    disabled = set(disabled or ())
    return [p for p in pet_ids if p not in disabled] or list(pet_ids)


def assign_pets(session_ids, pet_ids, assignments):
    """세션마다 펫을 정한다. 기존 배정은 유지하고, 새 세션은 지금 가장 적게 쓰인 펫을 받는다.

    같은 개수면 세션 id 로 정한 시작점부터 고른다 (실행마다 같은 결과가 나오게 crc32 를 쓴다).
    반환: {session_id: pet_id}
    """
    if not pet_ids:
        return {}
    counts = dict.fromkeys(pet_ids, 0)
    result = {}
    for sid in session_ids:
        pet = assignments.get(sid)
        if pet in counts:
            result[sid] = pet
            counts[pet] += 1
    for sid in session_ids:
        if sid in result:
            continue
        start = zlib.crc32(sid.encode()) % len(pet_ids)
        rotated = pet_ids[start:] + pet_ids[:start]
        pet = min(rotated, key=lambda p: counts[p])
        result[sid] = pet
        counts[pet] += 1
    return result


# ---- 마우스 올림 사용량 카드 -------------------------------------------------

STATE_LABELS = {"idle": "쉬는 중", "running": "작업 중", "waiting": "확인 필요", "done": "다 했어요",
                "error": "오류", "background": "백그라운드"}
USAGE_FIELDS = ("context_pct", "context_size", "context_tokens", "five_hour_pct", "five_hour_reset",
                "seven_day_pct", "seven_day_reset")
# 계정 사용량이 이보다 오래 갱신되지 않았으면 몇 분 전 값인지 적는다
USAGE_STALE_MS = 10 * 60 * 1000
WEEKDAYS = "월화수목금토일"


def usage_dir():
    return state_dir() / "usage"


def load_usage(directory):
    """상태줄 중계가 쓴 세션별 사용량. 숫자가 아닌 값은 None 으로 둔다. 반환: {session_id: dict}"""
    usage = {}
    directory = Path(directory)
    if not directory.is_dir():
        return usage
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entry = {"ts": int(data["ts"])}
        except (OSError, ValueError, TypeError, KeyError):
            continue
        for key in USAGE_FIELDS:
            value = data.get(key)
            entry[key] = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
        usage[path.stem] = entry
    return usage


def account_limits(usage):
    """5시간·주간 사용량은 계정 전체 값이라 어느 세션에서 읽혔든 가장 최근 것을 쓴다. 없으면 None."""
    readings = [u for u in usage.values()
                if u["five_hour_pct"] is not None or u["seven_day_pct"] is not None]
    return max(readings, key=lambda u: u["ts"], default=None)


def format_tokens(count):
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"
    if count >= 1000:
        return f"{int(count / 1000 + 0.5)}k"
    return str(int(count))


def format_percent(value):
    return f"{int(value + 0.5)}%"


def format_remaining(ms):
    """분 단위로 반올림하지 않고 올린다 (리셋까지 남은 시간이 0분으로 보이지 않게)."""
    minutes = max(1, -(-int(ms) // 60000))
    days, rest = divmod(minutes, 1440)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days}일 {hours}시간"
    if hours:
        return f"{hours}시간 {minutes}분"
    return f"{minutes}분"


def format_reset(reset_s, now_ms):
    """'14:30 리셋 (2시간 13분 뒤)', 다른 날이면 '10/12(월) 09:00 리셋 (3일 21시간 뒤)'."""
    at = time.localtime(reset_s)
    today = time.localtime(now_ms / 1000)
    clock = time.strftime("%H:%M", at)
    if (at.tm_year, at.tm_yday) != (today.tm_year, today.tm_yday):
        clock = f"{at.tm_mon}/{at.tm_mday}({WEEKDAYS[at.tm_wday]}) {clock}"
    return f"{clock} 리셋 ({format_remaining(reset_s * 1000 - now_ms)} 뒤)"


def usage_rows(usage, limits, now_ms):
    """카드에 그릴 줄들 [(이름, 사용 % 또는 None, 설명)] 과 아래에 붙일 메모."""
    rows = []
    if usage and usage["context_pct"] is not None:
        tokens, size = usage["context_tokens"], usage["context_size"]
        detail = f"{format_tokens(tokens)} / {format_tokens(size)}" if tokens is not None and size else ""
        rows.append(("컨텍스트", usage["context_pct"], detail))
    else:
        rows.append(("컨텍스트", None, "정보 없음"))
    for label, key in (("5시간", "five_hour"), ("주간", "seven_day")):
        pct = limits[f"{key}_pct"] if limits else None
        reset = limits[f"{key}_reset"] if limits else None
        if pct is None:
            rows.append((label, None, "정보 없음"))
        elif reset is not None and reset * 1000 <= now_ms:
            rows.append((label, None, "리셋됨"))
        else:
            rows.append((label, pct, format_reset(reset, now_ms) if reset is not None else ""))
    note = ""
    if limits and now_ms - limits["ts"] > USAGE_STALE_MS:
        note = f"계정 사용량은 {format_remaining(now_ms - limits['ts'])} 전 값"
    return rows, note


# ---- 펫 접기 ------------------------------------------------------------------

# 손이 필요한 정도, 앞일수록 먼저 보여준다 ("marked" 는 "나중에 이어서 볼게" 로 표시한 세션)
ATTENTION_ORDER = ("waiting", "error", "marked", "done")


def folded_view(views, sessions, marks=None):
    """접힌 줄에 세울 펫 하나: (session_id, state, 말풍선, 뒤에 기다리는 세션 수, 요약). 세션이 없으면 None.

    손이 필요한 세션이 있으면 확인 필요 > 오류 > 이어서 볼 세션 > 다 했어요,
    같은 종류는 오래 기다린 순(이어서 볼 세션은 먼저 표시한 순)으로 하나를 고른다.
    없으면 가장 오래된 세션을 쉬는 모습으로 세우고 다른 상태를 요약해 이름표에 쓴다.
    """
    if not views:
        return None
    marks = marks or {}

    def rank(view):
        sid, state, _ = view
        if state in URGENT_STATES:
            return ATTENTION_ORDER.index(state), sessions[sid]["ts"], sid
        if sid in marks:
            return ATTENTION_ORDER.index("marked"), marks[sid]["set_at"], sid
        if state == "done":
            return ATTENTION_ORDER.index("done"), sessions[sid]["ts"], sid
        return None

    attention = [(rank(v), v) for v in views if rank(v) is not None]
    if attention:
        sid, state, bubble = min(attention)[1]
        return sid, state, bubble, len(attention) - 1, ""
    counts = Counter(state for _, state, _ in views)
    parts = [f"{STATE_LABELS[s]} {counts[s]}" for s in ("running", "background") if counts[s]]
    return views[0][0], "idle", "", 0, " · ".join(parts) or "모두 쉬는 중"


# ---- 나중에 이어서 볼게 ------------------------------------------------------

MARK_TEXT = "이어서 봐 주세요!"
# 이 상태의 말풍선이 더 급해서 표시 말풍선으로 덮지 않는다 (책갈피만 단다)
URGENT_STATES = ("waiting", "error")


def marks_file():
    return state_dir() / "reminders.json"


def load_marks(path):
    """표시한 세션들 {session_id: {"set_at": ms}}. 파일이 없거나 깨졌으면 빈 dict."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    marks = {}
    for sid, mark in data.items():
        set_at = mark.get("set_at") if isinstance(mark, dict) else None
        if isinstance(set_at, int) and not isinstance(set_at, bool):
            marks[sid] = {"set_at": set_at}
    return marks


def save_marks(path, marks):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(marks), encoding="utf-8")
    tmp.replace(path)


def active_marks(marks, sessions):
    """세션이 끝났거나, 표시한 뒤 그 세션에 새 프롬프트가 왔으면(돌아와서 이어 했으면) 표시를 뺀다."""
    return {sid: mark for sid, mark in marks.items()
            if sid in sessions and sessions[sid].get("prompt_ts", 0) <= mark["set_at"]}


def marked_bubble(state, bubble):
    """표시한 세션의 말풍선 (문구, 말풍선 색 이름). 확인 필요·오류면 그 말풍선을 그대로 쓴다."""
    if state in URGENT_STATES:
        return bubble, state
    return (f"{MARK_TEXT}\n{bubble}" if bubble else MARK_TEXT), "marked"
