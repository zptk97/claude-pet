"""등반 일지 기록 (Qt 를 쓰지 않는다).

펫은 계정 5시간·주간 사용량의 지금 값만 보고 버려서, 지난주를 돌아보려면 따로 쌓아 둬야 한다. 여기서는
- 5시간 창과 주마다 (리셋 시각으로 가른다) 본 가장 큰 사용량, 처음 본 때, 사용량이 마지막으로 오른 때, 다 쓴 (100%) 때
- 한국 시각 1시간 칸마다 쓴 토큰 (climbcore.usage_tokens 의 출력 환산)
을 남긴다. 사용량 % 는 기록을 시작한 때 (since) 부터 쌓이고, 토큰은 처음 한 번 Claude Code 기록 파일에서 지난 것까지
되살린다 (backfill).

기록 파일 ($XDG_DATA_HOME/claude-pet/journal.json):
    {"since": <기록을 시작한 epoch 초>, "backfilled": <되살린 epoch 초> (되살리기 전에는 "pending": {기록 파일: 바이트}),
     "windows": [{"reset": <epoch 초>, "peak": 72, "first": <초>, "last": <초>, "full": <초 또는 null>}, ...],
     "weeks": [같은 꼴], "hours": {"2026-10-20T14": <출력 환산 토큰>, ...}}
"""
import collections
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
    return {"reset": reset, "peak": peak, **{key: _number(entry.get(key)) for key in ("first", "last", "full")}}


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


def week_tokens(journal, reset):
    """리셋 시각이 reset 인 주 (그 7일 전부터) 에 쓴 토큰 (출력 환산)."""
    start, end = hour_key(reset - WEEK_S), hour_key(reset)
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
