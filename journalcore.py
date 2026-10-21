"""등반 일지 기록 (Qt 를 쓰지 않는다).

펫은 계정 5시간·주간 사용량의 지금 값만 보고 버려서, 지난주를 돌아보려면 따로 쌓아 둬야 한다. 여기서는
- 5시간 창과 주마다 (리셋 시각으로 가른다) 본 가장 큰 사용량, 처음 본 때, 사용량이 마지막으로 오른 때, 다 쓴 (100%) 때
- 한국 시각 1시간 칸마다 쓴 토큰 (climbcore.usage_tokens 의 출력 환산)
- 주마다 등반 횟수 (넘은 크럭스, 일 끝냄 다이노, 로프 추락, 낮잠. 클라이밍 보기가 보이는 동안만. 사용량 잠은 5시간 창이
  다 찬 횟수와 같아서 따로 세지 않는다)
을 남긴다. 사용량 % 는 기록을 시작한 때 (since) 부터 쌓이고, 토큰은 처음 한 번 Claude Code 기록 파일에서 지난 것까지
되살린다 (backfill).

기록 파일 ($XDG_DATA_HOME/claude-pet/journal.json):
    {"since": <기록을 시작한 epoch 초>, "backfilled": <되살린 epoch 초> (되살리기 전에는 "pending": {기록 파일: 바이트}),
     "windows": [{"reset": <epoch 초>, "peak": 72, "first": <초>, "last": <초>, "full": <초 또는 null>}, ...],
     "weeks": [같은 꼴, 등반 횟수 "climb": {"crux": 3, ...}], "hours": {"2026-10-20T14": <출력 환산 토큰>, ...}}

일지 창 (journalview) 은 week_resets 로 넘길 주를 고르고 week_summary 로 한 주씩 요약한다.
"""
import collections
import functools
import math
import re
from datetime import datetime

import climbcore
import petcore

KEEP_S = 12 * 7 * 86400   # 이보다 오래된 창·주·시간 칸은 지운다
# (계정 사용량 이름, 일지 칸, 같은 주기로 보는 리셋 시각 차이): 5시간은 체력과, 주간은 벽의 끝과 같은 기준
PERIODS = (("five_hour", "windows", petcore.RESET_SLACK_S), ("seven_day", "weeks", petcore.WEEK_SLACK_S))
FULL = petcore.WEEK_FULL  # 이만큼 (%) 넘으면 다 쓴 것
HOUR_KEY = re.compile(r"\d{4}-\d\d-\d\dT\d\d")
RATE_MIN_PCT = 3          # 주간을 이만큼 (%) 넘게 쓴 주로 1% 에 드는 토큰을 잰다 (적으면 들쭉날쭉하다)
WEEK_S = 7 * 86400
DAY_S = 86400
WINDOW_S = 5 * 3600
TALLY = ("crux", "dyno", "fall", "nap")   # 주마다 세는 등반: 넘은 크럭스, 일 끝냄 다이노, 로프 추락, 낮잠
PLAN_MONTH_USD = 100.0                    # 요금제 (Max 5x) 한 달 값
PLAN_WEEK_USD = PLAN_MONTH_USD * 12 / 52  # 그 한 주 몫 (약 $23.08): 주에 쓴 API 금액을 이것으로 나눠 '요금제 값의 N배'
WORK_USD = 0.5                            # 한 시간 칸에 이만큼 ($) 넘게 썼으면 일한 시간으로 센다
CROWDED = 3                               # 지난 주 5시간 창이 이만큼 다 찼으면 몰아 쓰는 편
LOW_PCT = 50                              # 지난 주 주간 사용량이 이보다 적으면 '...만 썼어요'
WEEKDAYS = "월화수목금토일"


def new(now_s):
    return {"since": now_s, "windows": [], "weeks": [], "hours": {}}


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def _period(entry):
    if not isinstance(entry, dict):
        return None
    reset, peak = _number(entry.get("reset")), _number(entry.get("peak"))
    if reset is None or peak is None:
        return None
    period = {"reset": reset, "peak": peak, **{key: _number(entry.get(key)) for key in ("first", "last", "full")}}
    climb = entry.get("climb")
    if isinstance(climb, dict):
        counts = {k: v for k, v in climb.items()
                  if k in TALLY and isinstance(v, int) and not isinstance(v, bool) and v > 0}
        if counts:
            period["climb"] = counts
    return period


def restore(data, now_s):
    """읽은 기록 → 일지 (모르는 값은 버린다). 없거나 깨졌으면 새 일지 (backfilled·pending 둘 다 없다)."""
    data = data if isinstance(data, dict) else {}
    journal = new(_number(data.get("since")) or now_s)
    for _, name, _ in PERIODS:
        items = data.get(name) if isinstance(data.get(name), list) else []
        journal[name] = sorted(filter(None, map(_period, items)), key=lambda e: e["reset"])
    hours = data.get("hours") if isinstance(data.get("hours"), dict) else {}
    journal["hours"] = {k: v for k, v in hours.items()
                        if isinstance(k, str) and HOUR_KEY.fullmatch(k) and _number(v) is not None and v > 0}
    pending = data.get("pending")
    if _number(data.get("backfilled")) is not None:
        journal["backfilled"] = data["backfilled"]
    elif isinstance(pending, dict):
        journal["pending"] = {p: o for p, o in pending.items()
                              if isinstance(p, str) and isinstance(o, int) and not isinstance(o, bool) and o >= 0}
    return journal


def state(journal):
    """저장할 것 (시간 칸 토큰은 소수 셋째 자리까지)."""
    return dict(journal, hours={k: round(v, 3) for k, v in sorted(journal["hours"].items())})


def record_limits(journal, limits, now_s):
    """계정 사용량 (PetManager.account_limits) 을 5시간 창·주에 남긴다. 바뀌었으면 True.
    리셋 시각이 slack 안이면 같은 주기 (리셋 시각은 늦은 쪽으로). 처음 본 때·오른 때·다 쓴 때는 사용량을 읽은 때
    (limits ts, 오래 쉰 세션의 옛 값이면 그때) 로 적는다."""
    if not limits:
        return False
    ts = _number(limits.get("ts"))
    seen = ts / 1000 if ts is not None else now_s
    changed = False
    for key, name, slack in PERIODS:
        pct, reset = _number(limits.get(f"{key}_pct")), _number(limits.get(f"{key}_reset"))
        if pct is None or reset is None:
            continue
        periods = journal[name]
        entry = next((e for e in reversed(periods) if abs(e["reset"] - reset) <= slack), None)
        if entry is None:
            entry = {"reset": reset, "peak": pct, "first": seen, "last": seen, "full": None}
            periods.append(entry)
            periods.sort(key=lambda e: e["reset"])
            changed = True
        if reset > entry["reset"]:
            entry["reset"] = reset
            changed = True
        if pct > entry["peak"]:
            entry["peak"], entry["last"] = pct, seen
            changed = True
        if entry["peak"] >= FULL and entry["full"] is None:
            entry["full"] = seen
            changed = True
    return changed


def hour_key(ts):
    """epoch 초 → 한국 시각 시간 칸 이름 ("2026-10-20T14")."""
    return datetime.fromtimestamp(ts, climbcore.KST).strftime("%Y-%m-%dT%H")


def add_hours(journal, spent):
    """{시간 칸 시작 epoch 초: 출력 환산 토큰} (TokenMeter.take_spent, backfill) 을 시간 칸에 더한다. 더했으면 True."""
    hours = journal["hours"]
    added = False
    for start, tokens in spent.items():
        if tokens > 0:
            key = hour_key(start)
            hours[key] = hours.get(key, 0) + tokens
            added = True
    return added


def trim(journal, now_s):
    """KEEP_S 보다 오래된 창·주 (리셋 시각으로)·시간 칸을 지운다."""
    cut = now_s - KEEP_S
    for _, name, _ in PERIODS:
        journal[name] = [e for e in journal[name] if e["reset"] >= cut]
    oldest = hour_key(cut)
    journal["hours"] = {k: v for k, v in journal["hours"].items() if k >= oldest}


def backfill(offsets):
    """Claude Code 기록 파일마다 offsets ({경로: 바이트}, TokenMeter 가 이미 읽은 자리) 앞까지 쓴 토큰 →
    {시간 칸 시작 epoch 초: 출력 환산 토큰}. 같은 응답이 여러 줄·여러 파일에 남아도 응답 id 마다 가장 큰 값을 한 번
    (TokenMeter 와 같은 규칙), 그 값이 적힌 때의 시간 칸에 센다. 그 자리 뒤는 계수기가 이어 세므로 겹치지 않는다
    (자리에 걸친 덜 쓰인 줄도 빼서 계수기 몫으로 둔다). 기록 800MB 에 2초쯤 걸려서 스레드에서 부른다."""
    best = {}
    for path, size in offsets.items():
        try:
            with open(path, "rb") as f:
                left = size
                for line in f:
                    left -= len(line)
                    if left < 0:
                        break
                    got = climbcore.line_usage(line)
                    if got is not None and got[2] is not None and got[1] > best.get(got[0], (0, 0))[0]:
                        best[got[0]] = (got[1], got[2])
        except OSError:
            continue
    spent = collections.Counter()
    for tokens, ts in best.values():
        spent[int(ts // 3600) * 3600] += tokens
    return spent


def week_keys(reset):
    """리셋 시각이 reset 인 주의 (첫 시간 칸, 다음 주 첫 시간 칸) 이름. 경계는 가장 가까운 정시로 (리셋이 몇 초·몇 분
    흔들려도 앞뒤 주가 같은 경계를 써서 칸이 빠지거나 겹치지 않게)."""
    return hour_key(reset - WEEK_S + 1800), hour_key(reset + 1800)


def week_tokens(journal, reset):
    """리셋 시각이 reset 인 주 (그 7일 전부터) 에 쓴 토큰 (출력 환산)."""
    start, end = week_keys(reset)
    return sum(v for k, v in journal["hours"].items() if start <= k < end)


def tokens_per_pct(journal):
    """주간 1% 에 드는 토큰: 가장 최근 주 (이번 주, 아니면 지난주...) 중 RATE_MIN_PCT 넘게 쓴 주의 토큰 ÷ %. 없으면 None."""
    for entry in reversed(journal["weeks"]):
        if entry["peak"] >= RATE_MIN_PCT:
            tokens = week_tokens(journal, entry["reset"])
            if tokens > 0:
                return tokens / entry["peak"]
    return None


def week_scale(journal, limits, now_s):
    """끝없이 오르기 높이 (가속 눈금) 에 줄 (주간 사용량 %, 1% 에 드는 토큰, 주간 리셋 epoch 초). % 는 리셋 시각이
    지났으면 0 (새 주), 모르면 None. 1% 에 드는 토큰은 tokens_per_pct (모르면 None), 리셋은 지났거나 모르면 None."""
    pct = _number(limits.get("seven_day_pct")) if limits else None
    reset = _number(limits.get("seven_day_reset")) if limits else None
    if reset is not None and reset <= now_s:
        pct, reset = (0.0 if pct is not None else None), None
    return pct, tokens_per_pct(journal), reset


def add_tally(journal, tally, now_s):
    """등반 횟수 ({이름: 번}, Climber.take_tally) 를 now_s 가 든 주에 더한다 (그 주를 아직 모르면 버린다). 더했으면 True."""
    counts = {k: n for k, n in tally.items() if k in TALLY and n > 0}
    entry = next((e for e in reversed(journal["weeks"]) if e["reset"] - WEEK_S <= now_s < e["reset"]), None)
    if not counts or entry is None:
        return False
    climb = entry.setdefault("climb", {})
    for k, n in counts.items():
        climb[k] = climb.get(k, 0) + n
    return True


@functools.lru_cache(maxsize=4096)
def _hour_ts(key):
    """시간 칸 이름 → 그 칸 시작 epoch 초 (일지 창이 10초마다 모든 주를 다시 요약해서 기억해 둔다)."""
    return datetime.strptime(key, "%Y-%m-%dT%H").replace(tzinfo=climbcore.KST).timestamp()


def _week_near(journal, reset, slack=petcore.WEEK_SLACK_S):
    """리셋 시각이 reset 에서 slack 안인 주 기록 중 가장 가까운 것, 없으면 None."""
    near = [e for e in journal["weeks"] if abs(e["reset"] - reset) <= slack]
    return min(near, key=lambda e: abs(e["reset"] - reset)) if near else None


def week_resets(journal, now_s, reset=None):
    """일지 창에서 넘겨 볼 주 (리셋 epoch 초), 오래된 것부터 이번 주까지. 이번 주 리셋은 reset (계정 값, 지났거나 모르면
    일지의 가장 최근 주에서 7일씩 옮겨 now_s 뒤로. 한 주 넘게 뒤인 값은 버린다), 둘 다 없으면 빈 목록. 앞으로는 기록
    (시간 칸·주) 이 있는 주까지, 시작이 KEEP_S 안인 주만 (그보다 오래된 시간 칸은 지워서 덜 찬 주가 된다). 7일 앞 근처
    (반 주 안) 에 기록된 주가 있으면 그 리셋 시각을 쓴다 (리셋 시각이 흔들리거나 리셋 요일이 바뀌어도 기록된 주로)."""
    limit = now_s + WEEK_S + petcore.WEEK_SLACK_S
    reset = _number(reset)
    if reset is None or not now_s < reset <= limit:
        if not journal["weeks"]:
            return []
        reset = journal["weeks"][-1]["reset"]
        if reset <= now_s:
            reset += (int((now_s - reset) // WEEK_S) + 1) * WEEK_S
        if reset > limit:
            return []
    entry = _week_near(journal, reset)
    reset = entry["reset"] if entry else reset
    oldest = min(([_hour_ts(min(journal["hours"]))] if journal["hours"] else []) +
                 [e["reset"] - WEEK_S for e in journal["weeks"]], default=now_s)
    resets = []
    while reset > oldest and reset - WEEK_S >= now_s - KEEP_S:
        resets.append(reset)
        guess = reset - WEEK_S
        entry = _week_near(journal, guess, WEEK_S / 2)
        reset = entry["reset"] if entry and entry["reset"] < reset - DAY_S else guess
    return resets[::-1]


def _wall(walls, start, reset):
    """그 주 (start ~ reset) 에 끝난 벽 (PetManager.walls): 다 써서 (full) 그 주 안에, 리셋으로 (reset) 그 리셋 뒤 한 주 안에."""
    slack = petcore.WEEK_SLACK_S
    found = None
    for wall in walls:
        end = _number(wall.get("end"))
        if end is None:
            continue
        if (wall.get("reason") == "full" and start <= end < reset + slack) or \
                (wall.get("reason") == "reset" and reset - slack <= end < reset + WEEK_S - slack):
            found = wall
    return found


def week_summary(journal, reset, now_s, walls=(), height=None):
    """일지 창 한 쪽: 리셋 시각이 reset 인 주 (그 7일 전부터) 의 요약.
    pct (주간 최고 %, 기록이 없으면 None), full_at (다 쓴 때), usd (이 컴퓨터 Claude Code 의 API 금액), rate (1% 에 드는 $,
    이 주가 RATE_MIN_PCT 넘게 썼으면 이 주로, 아니면 최근 주나 기본값), left_pct·left_usd (남긴 몫), multiple (요금제 값의
    배수), days (리셋 시각부터 하루씩 7칸 $), work_hours (WORK_USD 넘게 쓴 시간 칸 수), windows (그 주에 시작한 5시간 창:
    start, peak, full, blocked 초), full_windows, blocked (다 차서 막힌 초 합), height (이번 주면 height, 아니면 주간 % 의
    가속 눈금, 그것도 없으면 끝난 벽 높이), end (("going"|"moon"|"reset"|None, 때)), climb (등반 횟수), eta (이번 주 이
    빠르기면 다 쓸 때), projected (이 빠르기면 리셋 때 %), since (% 를 기록하기 시작한 때), diagnosis (한 줄 진단)."""
    start = reset - WEEK_S
    entry = _week_near(journal, reset)
    pct = entry["peak"] if entry else None
    current = start <= now_s < reset
    days, work = [0.0] * 7, 0
    first, last = week_keys(reset)   # 시간 칸 이름으로, 경계는 가장 가까운 정시 (week_tokens 와 같다)
    base = _hour_ts(first)
    for key, tokens in journal["hours"].items():
        if first <= key < last:
            usd = tokens * climbcore.TOKEN_USD
            days[min(6, int((_hour_ts(key) - base) // DAY_S))] += usd
            work += usd >= WORK_USD
    usd = sum(days)
    if pct is not None and pct >= RATE_MIN_PCT and usd > 0:
        rate = usd / pct
    else:
        per = tokens_per_pct(journal)
        rate = (per or climbcore.WEEK_RATE) * climbcore.TOKEN_USD
    windows = []
    for w in journal["windows"]:
        begin = w["reset"] - WINDOW_S
        if start <= begin < reset:
            blocked = max(0.0, min(w["reset"], now_s) - w["full"]) if w["full"] is not None else 0.0
            windows.append({"start": begin, "peak": w["peak"], "full": w["full"], "blocked": blocked})
    full_at = entry["full"] if entry else None
    wall = _wall(walls, start, reset)
    if full_at is None and wall and wall.get("reason") == "full":
        full_at = _number(wall.get("end"))
    if not current or height is None:
        if pct is not None:
            height = climbcore.MOON if pct >= FULL else climbcore.week_height(pct)
        else:
            height = _number(wall.get("height")) if wall else None
    if full_at is not None:
        end = ("moon", full_at)
    elif current:
        end = ("going", reset)
    elif wall:
        end = ("reset", _number(wall.get("end")))
    else:
        end = (None, None)
    eta = projected = None
    elapsed = now_s - start
    if current and pct is not None and full_at is None and pct > 0 and elapsed >= DAY_S:
        speed = pct / elapsed   # % / 초
        finish = now_s + (100 - pct) / speed
        if finish < reset:
            eta = finish
        else:
            projected = pct + speed * (reset - now_s)
    left_pct = max(0.0, 100 - pct) if pct is not None else None
    summary = {
        "start": start, "reset": reset, "current": current, "pct": pct, "full_at": full_at, "usd": usd, "rate": rate,
        "left_pct": left_pct, "left_usd": left_pct * rate if left_pct is not None else None,
        "multiple": usd / PLAN_WEEK_USD, "days": days, "work_hours": work, "windows": windows,
        "full_windows": sum(1 for w in windows if w["full"] is not None), "blocked": sum(w["blocked"] for w in windows),
        "height": height, "end": end, "climb": dict(entry.get("climb", {})) if entry else {},
        "eta": eta, "projected": projected, "since": journal["since"],
    }
    summary["diagnosis"] = diagnose(summary, now_s)
    return summary


def when(ts):
    """epoch 초 → 한국 시각 '수 14:20'."""
    at = datetime.fromtimestamp(ts, climbcore.KST)
    return f"{WEEKDAYS[at.weekday()]} {at:%H:%M}"


def span(seconds):
    """초 → '1일 3시간', '18시간', '1시간 20분', '40분' (분은 올린다)."""
    minutes = max(1, math.ceil(seconds / 60 - 1e-9))
    days, rest = divmod(minutes, 1440)
    hours, minutes = divmod(rest, 60)
    if days:
        return f"{days}일 {hours}시간" if hours else f"{days}일"
    if hours:
        return f"{hours}시간 {minutes}분" if minutes else f"{hours}시간"
    return f"{minutes}분"


def money(usd):
    return f"${int(usd + 0.5):,}"


def percent(value):
    """% 는 펫 카드처럼 정수로 (반올림)."""
    return f"{int(value + 0.5)}%"


def diagnose(s, now_s):
    """한 주 요약 (week_summary) 의 한 줄 진단. 위에서부터 맞는 것 하나."""
    pct = s["pct"]
    if pct is None:
        return "주간 사용량 % 를 기록하기 전이라 금액만 있어요"
    shown = percent(pct)
    if s["current"]:
        if s["full_at"] is not None:
            return f"{when(s['full_at'])}에 다 썼어요, 리셋까지 {span(s['reset'] - now_s)} 달에서 쉬어요"
        if s["eta"] is not None:
            return f"이 빠르기면 {when(s['eta'])}쯤 다 써요"
        if s["projected"] is not None:
            return f"이 빠르기면 주간 약 {s['projected']:.0f}%로 끝나요"
        return f"주간 {shown} 썼어요, 남은 {percent(s['left_pct'])} ≈ {money(s['left_usd'])}어치"
    if s["full_at"] is not None:
        return f"{when(s['full_at'])}에 다 써서 {span(s['reset'] - s['full_at'])} 막혔어요"
    if s["full_windows"] >= CROWDED:
        return f"5시간 창이 {s['full_windows']}번 다 찼어요, 몰아 쓰는 편이에요"
    if pct < LOW_PCT:
        return f"주간 한도의 {shown}만 썼어요, {money(s['left_usd'])}어치 남겼어요"
    return f"주간 {shown} 썼어요, {money(s['left_usd'])}어치 남겼어요"
