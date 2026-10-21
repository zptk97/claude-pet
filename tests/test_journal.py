import json
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import climbcore  # noqa: E402
import journalcore  # noqa: E402
from climbcore import KST, TokenMeter  # noqa: E402

H = 3600
R5 = 1_792_479_600     # 2026-10-20 16:00 KST, 5시간 창 리셋
RW = 1_792_623_600     # 2026-10-22 08:00 KST, 주간 리셋


def limits(ts, five=None, five_reset=R5, week=None, week_reset=RW):
    return {"ts": ts * 1000, "five_hour_pct": five, "five_hour_reset": five_reset,
            "seven_day_pct": week, "seven_day_reset": week_reset}


def kst(*args):
    return datetime(*args, tzinfo=KST).timestamp()


def line(mid, out, when, **usage):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(when))
    return json.dumps({"type": "assistant", "timestamp": stamp,
                       "message": {"id": mid, "usage": {"output_tokens": out, **usage}}}) + "\n"


class RecordLimitsTest(unittest.TestCase):
    """계정 사용량을 리셋 주기마다: 같은 주기면 가장 큰 값, 처음 본 때·오른 때·다 쓴 때는 한 번씩."""

    def test_windows_and_weeks_by_reset(self):
        j = journalcore.new(0)
        t = R5 - 4 * H
        self.assertTrue(journalcore.record_limits(j, limits(t, 10, week=30), t))
        self.assertTrue(journalcore.record_limits(j, limits(t + 60, 25, R5 + 30, 31), t + 60))   # 리셋 30초 흔들림
        self.assertFalse(journalcore.record_limits(j, limits(t + 120, 20, week=31), t + 120))    # 줄어든 값은 그대로
        self.assertEqual(j["windows"], [{"reset": R5 + 30, "peak": 25, "first": t, "last": t + 60, "full": None}])
        self.assertEqual(len(j["weeks"]), 1)
        self.assertEqual(j["weeks"][0]["peak"], 31)
        later = R5 + H
        journalcore.record_limits(j, limits(later, 5, R5 + 5 * H, 32, RW + 3 * H), later)   # 새 5시간 창, 주간은 흔들림
        self.assertEqual([w["reset"] for w in j["windows"]], [R5 + 30, R5 + 5 * H])
        self.assertEqual([(w["reset"], w["peak"]) for w in j["weeks"]], [(RW + 3 * H, 32)])
        journalcore.record_limits(j, limits(RW + H, 2, week_reset=RW + 7 * 86400, week=1), RW + H)   # 새 주
        self.assertEqual([w["peak"] for w in j["weeks"]], [32, 1])

    def test_full_time_once(self):
        j = journalcore.new(0)
        t = R5 - 2 * H
        journalcore.record_limits(j, limits(t, 99), t)
        self.assertIsNone(j["windows"][0]["full"])
        journalcore.record_limits(j, limits(t + 600, 100), t + 600)
        journalcore.record_limits(j, limits(t + 900, 100), t + 900)
        self.assertEqual(j["windows"][0]["full"], t + 600)
        self.assertEqual(j["windows"][0]["last"], t + 600)

    def test_unknown_values_and_old_readings(self):
        j = journalcore.new(0)
        self.assertFalse(journalcore.record_limits(j, None, 0))
        self.assertFalse(journalcore.record_limits(j, limits(100, None, week=None), 100))
        self.assertFalse(journalcore.record_limits(j, limits(100, 40, five_reset=None), 100))
        old = R5 - 3 * H   # 오래 쉰 세션의 옛 값: 읽은 때로 적는다
        journalcore.record_limits(j, limits(old, 40), old + 9 * H)
        self.assertEqual(j["windows"][0]["first"], old)
        journalcore.record_limits(j, {"five_hour_pct": 50, "five_hour_reset": R5}, R5 - H)   # 읽은 때를 모르면 지금
        self.assertEqual(j["windows"][0]["last"], R5 - H)


class HoursTest(unittest.TestCase):
    """쓴 토큰을 한국 시각 시간 칸마다: 계수기가 응답이 적힌 시간으로 나누고, 오래된 칸은 지운다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "p").mkdir()
        self.main = self.root / "p" / "s.jsonl"
        self.main.write_text("")

    def tearDown(self):
        self.tmp.cleanup()

    def test_meter_spends_by_hour_kst(self):
        meter = TokenMeter(self.root)
        meter.start = kst(2026, 10, 19, 0)
        with self.main.open("a") as f:
            f.write(line("A", 100, kst(2026, 10, 19, 23, 50)) + line("A", 300, kst(2026, 10, 20, 0, 5))
                    + line("B", 50, kst(2026, 10, 20, 0, 30), input_tokens=200))
        total = meter.poll()
        spent = meter.take_spent()
        self.assertAlmostEqual(sum(spent.values()), total)
        j = journalcore.new(0)
        self.assertTrue(journalcore.add_hours(j, spent))
        self.assertEqual(set(j["hours"]), {"2026-10-19T23", "2026-10-20T00"})
        self.assertEqual(j["hours"]["2026-10-19T23"], 100)
        self.assertAlmostEqual(j["hours"]["2026-10-20T00"], 200 + 50 + 200 * 4 / 20)   # 늘어난 200 + B (입력은 1/5)
        self.assertEqual(meter.take_spent(), {})
        self.assertFalse(journalcore.add_hours(j, {}))

    def test_trim_old(self):
        now = kst(2026, 10, 20, 12)
        j = journalcore.new(0)
        old = now - journalcore.KEEP_S - H
        j["hours"] = {journalcore.hour_key(old): 5, journalcore.hour_key(now): 7}
        j["windows"] = [{"reset": old, "peak": 1, "first": None, "last": None, "full": None},
                        {"reset": now, "peak": 2, "first": None, "last": None, "full": None}]
        journalcore.trim(j, now)
        self.assertEqual(j["hours"], {"2026-10-20T12": 7})
        self.assertEqual([w["peak"] for w in j["windows"]], [2])


class SaveTest(unittest.TestCase):
    def test_round_trip_and_broken_values(self):
        j = journalcore.new(123)
        journalcore.record_limits(j, limits(R5 - H, 40, week=12), R5 - H)
        j["hours"]["2026-10-20T14"] = 1234.56789
        j["pending"] = {"/a.jsonl": 10}
        back = journalcore.restore(json.loads(json.dumps(journalcore.state(j))), 999)
        self.assertEqual(back["since"], 123)
        self.assertEqual(back["windows"], j["windows"])
        self.assertEqual(back["weeks"], j["weeks"])
        self.assertEqual(back["hours"], {"2026-10-20T14": 1234.568})
        self.assertEqual(back["pending"], {"/a.jsonl": 10})
        broken = journalcore.restore({"since": "x", "windows": [{"reset": 1}, 5, {"reset": 2, "peak": float("nan")},
                                                               {"reset": 3, "peak": 9, "full": "y"}],
                                      "weeks": "z", "hours": {"bad": 1, "2026-10-20T01": -2, "2026-10-20T02": 3},
                                      "pending": {"/a": -1, "/b": True, "/c": 4}}, 999)
        self.assertEqual(broken["since"], 999)
        self.assertEqual(broken["windows"], [{"reset": 3, "peak": 9, "first": None, "last": None, "full": None}])
        self.assertEqual(broken["weeks"], [])
        self.assertEqual(broken["hours"], {"2026-10-20T02": 3})
        self.assertEqual(broken["pending"], {"/c": 4})
        self.assertNotIn("pending", journalcore.restore({"backfilled": 5, "pending": {"/c": 4}}, 0))
        fresh = journalcore.restore(None, 7)
        self.assertEqual(fresh, journalcore.new(7))


class WeekScaleTest(unittest.TestCase):
    """끝없이 오르기 높이에 줄 주간 % 와 1% 에 드는 토큰."""

    def test_tokens_per_pct_from_the_latest_used_week(self):
        j = journalcore.new(0)
        self.assertIsNone(journalcore.tokens_per_pct(j))
        last, this = RW - 7 * 86400, RW
        j["weeks"] = [{"reset": last, "peak": 40, "first": None, "last": None, "full": None},
                      {"reset": this, "peak": 2, "first": None, "last": None, "full": None}]
        j["hours"] = {journalcore.hour_key(last - 3 * 86400): 30_000_000, journalcore.hour_key(last - 8 * 86400): 999,
                      journalcore.hour_key(this - H): 1_000_000}
        self.assertEqual(journalcore.tokens_per_pct(j), 30_000_000 / 40)   # 이번 주는 2% 라 지난주로
        j["weeks"][1]["peak"] = 4
        self.assertEqual(journalcore.tokens_per_pct(j), 1_000_000 / 4)
        self.assertEqual(journalcore.week_tokens(j, last), 30_000_000)   # 그 전 주 토큰은 안 센다

    def test_week_pct(self):
        j = journalcore.new(0)
        self.assertEqual(journalcore.week_scale(j, None, RW - H), (None, None, None))
        self.assertEqual(journalcore.week_scale(j, limits(RW - H, 5, week=38), RW - H), (38, None, RW))
        self.assertEqual(journalcore.week_scale(j, limits(RW + 60, 5, week=38), RW + 60), (0.0, None, None))   # 리셋이 지났다
        self.assertEqual(journalcore.week_scale(j, limits(RW - H, 5, week=38, week_reset=None), RW - H), (38, None, None))


class BackfillTest(unittest.TestCase):
    """지난 토큰 되살리기: 계수기가 읽은 자리 앞까지만, 응답마다 한 번. 뒤는 계수기가 세서 합이 맞는다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "p" / "s1" / "subagents").mkdir(parents=True)
        self.main = self.root / "p" / "s1.jsonl"
        self.sub = self.root / "p" / "s1" / "subagents" / "a.jsonl"
        self.copy = self.root / "p" / "s2.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_up_to_meter_offsets_and_once_per_response(self):
        t = kst(2026, 10, 15, 9)
        self.main.write_text(line("A", 100, t) + line("A", 400, t + 1) + line("B", 50, t + 2 * H)
                             + '{"type":"user"}\n' + line("C", 70, t + 3 * H)[:-10])   # C 는 덜 쓰였다
        self.sub.write_text(line("S", 30, t + 5) + line("S", 90, t + 6))
        self.copy.write_text(line("A", 400, t + 1))   # 이어 하기로 옮겨 적힌 줄
        meter = TokenMeter(self.root)
        meter.start = t + 3 * H
        offsets = meter.state()["offsets"]
        spent = journalcore.backfill(offsets)
        self.assertEqual(spent, {int(t // H) * H: 400 + 90, int((t + 2 * H) // H) * H: 50})
        with self.main.open("a") as f:   # 계수기 몫은 새 응답 D 만 (처음 켤 때 쓰이던 C 는 둘 다 세지 않는다)
            f.write(line("C", 70, t + 3 * H)[-10:] + line("A", 400, t + 1) + line("D", 20, t + 4 * H))
        self.assertEqual(meter.poll(), 20)
        self.assertEqual(meter.take_spent(), {int((t + 4 * H) // H) * H: 20})

    def test_partial_offset_and_missing_files(self):
        t = kst(2026, 10, 16, 10)
        text = line("A", 100, t) + line("B", 200, t)
        self.main.write_text(text)
        cut = len(line("A", 100, t)) + 5   # B 줄 가운데
        spent = journalcore.backfill({str(self.main): cut, str(self.root / "gone.jsonl"): 99})
        self.assertEqual(sum(spent.values()), 100)
        self.assertEqual(sum(journalcore.backfill({str(self.main): len(text)}).values()), 300)

    def test_shared_line_parser(self):
        got = climbcore.line_usage(line("A", 10, kst(2026, 10, 20, 1)).encode())
        self.assertEqual(got, ("A", 10, kst(2026, 10, 20, 1)))
        self.assertIsNone(climbcore.line_usage(b'{"type":"user"}'))
        self.assertIsNone(climbcore.line_usage(b'{"message":{"id":"x","usage":{"output_tokens":true}}}'))
        no_time = climbcore.line_usage(b'{"message":{"id":"x","usage":{"output_tokens":5}}}')
        self.assertEqual(no_time, ("x", 5, None))




W = journalcore.WEEK_S
START = RW - W         # 2026-10-15 08:00 KST, 이번 주 시작


def usd(dollars):
    return dollars / climbcore.TOKEN_USD


def week(reset=RW, peak=40, full=None, **extra):
    return {"reset": reset, "peak": peak, "first": reset - W + H, "last": reset - W + 2 * H, "full": full, **extra}


def window(begin, peak, full=None):
    return {"reset": begin + 5 * H, "peak": peak, "first": begin, "last": begin + H, "full": full}


class SummaryTest(unittest.TestCase):
    """일지 창 한 쪽: 하루 칸은 리셋 시각부터, 그 주에 시작한 5시간 창, 남긴 몫·배수."""

    def test_days_split_at_the_reset_hour(self):
        j = journalcore.new(0)
        for at, dollars in ((START - H, 9), (START, 1), (START + 23 * H, 2), (START + 24 * H, 4),
                            (RW - H, 8), (RW, 16), (START + 30 * H, 0.2)):
            j["hours"][journalcore.hour_key(at)] = usd(dollars)
        s = journalcore.week_summary(j, RW, RW + 10 * W)
        self.assertEqual([round(d, 6) for d in s["days"]], [3, 4.2, 0, 0, 0, 0, 8])
        self.assertAlmostEqual(s["usd"], 15.2)
        self.assertEqual(s["work_hours"], 4)   # $0.5 아래 칸은 빼고
        self.assertEqual(datetime.fromtimestamp(s["start"], KST).strftime("%m/%d %H"), "10/15 08")

    def test_windows_in_the_week_and_blocked_time(self):
        j = journalcore.new(0)
        j["weeks"] = [week()]
        j["windows"] = [window(START - 2 * H, 100, full=START + H),     # 지난주에 시작: 빼고
                        window(START + 10 * H, 100, full=START + 13 * H),
                        window(START + 30 * H, 45),
                        window(RW - 3 * H, 100, full=RW - 2 * H)]       # 지금 열린 창
        now = RW - H
        s = journalcore.week_summary(j, RW, now)
        self.assertEqual([w["peak"] for w in s["windows"]], [100, 45, 100])
        self.assertEqual(s["full_windows"], 2)
        self.assertEqual(s["blocked"], 2 * H + H)   # 다 찬 뒤 리셋까지, 열린 창은 지금까지

    def test_left_share_rate_and_plan_multiple(self):
        j = journalcore.new(0)
        j["weeks"] = [week(peak=40)]
        j["hours"][journalcore.hour_key(START + 5 * H)] = usd(520)
        s = journalcore.week_summary(j, RW, RW + H)
        self.assertAlmostEqual(s["rate"], 13)
        self.assertEqual(s["left_pct"], 60)
        self.assertAlmostEqual(s["left_usd"], 780)
        self.assertAlmostEqual(journalcore.PLAN_WEEK_USD, 100 * 12 / 52)
        self.assertAlmostEqual(s["multiple"], 520 / (100 * 12 / 52))
        self.assertAlmostEqual(s["height"], climbcore.week_height(40))
        self.assertEqual(s["end"], (None, None))

    def test_rate_from_other_weeks_when_this_one_is_small(self):
        j = journalcore.new(0)
        j["weeks"] = [week(reset=RW - W, peak=50), week(peak=2)]
        j["hours"][journalcore.hour_key(START - W + H)] = usd(600)
        j["hours"][journalcore.hour_key(START + H)] = usd(100)
        self.assertAlmostEqual(journalcore.week_summary(j, RW, RW + H)["rate"], 12)   # 지난주 $600 / 50%
        empty = journalcore.new(0)
        self.assertAlmostEqual(journalcore.week_summary(empty, RW, RW + H)["rate"], climbcore.WEEK_RATE * climbcore.TOKEN_USD)

    def test_money_only_week(self):
        j = journalcore.new(0)
        j["hours"][journalcore.hour_key(START + H)] = usd(80)
        s = journalcore.week_summary(j, RW, RW + H)
        self.assertIsNone(s["pct"])
        self.assertIsNone(s["left_usd"])
        self.assertIsNone(s["height"])
        self.assertEqual(s["diagnosis"], "주간 사용량 % 를 기록하기 전이라 금액만 있어요")

    def test_walls_and_how_the_week_ended(self):
        j = journalcore.new(0)
        j["weeks"] = [week(reset=RW - W, peak=70), week(peak=100, full=RW - 18 * H)]
        walls = [{"end": RW - W + 60, "height": 9000.0, "reason": "reset"},
                 {"end": RW - 18 * H, "height": climbcore.MOON, "reason": "full"}]
        last = journalcore.week_summary(j, RW - W, RW + H, walls)
        self.assertEqual(last["end"], ("reset", RW - W + 60))
        self.assertAlmostEqual(last["height"], climbcore.week_height(70))
        this = journalcore.week_summary(j, RW, RW - H, walls, height=1234.0)
        self.assertEqual(this["end"], ("moon", RW - 18 * H))
        self.assertEqual(this["height"], 1234.0)   # 이번 주는 아바타 높이
        going = journalcore.week_summary(journalcore.new(0), RW, RW - H)
        self.assertEqual(going["end"], ("going", RW))


class DiagnoseTest(unittest.TestCase):
    """한 줄 진단: 위에서부터 맞는 것 하나."""

    def summary(self, now, peak, full=None, windows=(), spent=100):
        j = journalcore.new(0)
        j["weeks"] = [week(peak=peak, full=full)]
        j["windows"] = list(windows)
        j["hours"][journalcore.hour_key(START + H)] = usd(spent)
        return journalcore.week_summary(j, RW, now)

    def test_this_week(self):
        full = self.summary(RW - 18 * H, 100, full=RW - 30 * H)
        self.assertEqual(full["diagnosis"], f"{journalcore.when(RW - 30 * H)}에 다 썼어요, 리셋까지 18시간 달에서 쉬어요")
        fast = self.summary(START + 3 * 86400, 60)   # 하루 20% → 이틀 뒤 다 씀
        self.assertAlmostEqual(fast["eta"], START + 5 * 86400)
        self.assertEqual(fast["diagnosis"], f"이 빠르기면 {journalcore.when(START + 5 * 86400)}쯤 다 써요")
        slow = self.summary(START + 4 * 86400, 20)   # 하루 5% → 리셋 때 35%
        self.assertAlmostEqual(slow["projected"], 35)
        self.assertEqual(slow["diagnosis"], "이 빠르기면 주간 약 35%로 끝나요")
        early = self.summary(START + 12 * H, 5, spent=65)   # 하루가 안 지나면 가늠하지 않는다
        self.assertEqual(early["diagnosis"], "주간 5% 썼어요, 남은 95% ≈ $1,235어치")

    def test_past_weeks(self):
        later = RW + H
        full = self.summary(later, 100, full=RW - 18 * H)
        self.assertEqual(full["diagnosis"], f"{journalcore.when(RW - 18 * H)}에 다 써서 18시간 막혔어요")
        crowded = self.summary(later, 80, windows=[window(START + i * 20 * H, 100, full=START + i * 20 * H + 4 * H)
                                                   for i in range(3)])
        self.assertEqual(crowded["diagnosis"], "5시간 창이 3번 다 찼어요, 몰아 쓰는 편이에요")
        low = self.summary(later, 38, spent=380)
        self.assertEqual(low["diagnosis"], "주간 한도의 38%만 썼어요, $620어치 남겼어요")
        plenty = self.summary(later, 72, spent=720)
        self.assertEqual(plenty["diagnosis"], "주간 72% 썼어요, $280어치 남겼어요")

    def test_time_words(self):
        self.assertEqual(journalcore.when(kst(2026, 10, 21, 14, 20)), "수 14:20")
        self.assertEqual(journalcore.span(18 * H), "18시간")
        self.assertEqual(journalcore.span(H + 20 * 60), "1시간 20분")
        self.assertEqual(journalcore.span(27 * H), "1일 3시간")
        self.assertEqual(journalcore.span(40 * 60 - 5), "40분")


class PagesTest(unittest.TestCase):
    """넘겨 볼 주: 이번 주부터 기록이 있는 주까지, 기록된 주의 리셋 시각으로."""

    def test_weeks_back_to_the_oldest_record(self):
        j = journalcore.new(0)
        j["weeks"] = [week(reset=RW + 60)]   # 리셋 시각이 1분 흔들려도 같은 주
        j["hours"][journalcore.hour_key(START - 2 * W + H)] = usd(5)
        now = RW - H
        self.assertEqual(journalcore.week_resets(j, now, RW), [RW + 60 - 2 * W, RW + 60 - W, RW + 60])   # 시간 칸은 2주 전 주
        self.assertEqual(journalcore.week_resets(j, RW + 2 * W + H)[-1], RW + 60 + 3 * W)   # 지난 리셋에서 7일씩
        self.assertEqual(journalcore.week_resets(journalcore.new(0), now), [])
        self.assertEqual(journalcore.week_resets(journalcore.new(now), now, RW), [RW])

    def test_keep_window(self):
        j = journalcore.new(0)
        j["hours"][journalcore.hour_key(RW - 30 * W)] = usd(5)
        resets = journalcore.week_resets(j, RW - H, RW)
        self.assertEqual(len(resets), journalcore.KEEP_S // W)   # 시작이 지울 때보다 앞인 (덜 찬) 주는 빼고
        self.assertGreaterEqual(resets[0] - W, RW - H - journalcore.KEEP_S)

    def test_reset_day_moved(self):
        """리셋 요일이 바뀌어도 (목 08시 → 월 10시) 지난 주는 기록된 리셋으로."""
        j = journalcore.new(0)
        monday = RW + 4 * 86400 + 2 * H
        j["weeks"] = [week(reset=RW - W, peak=60), week(reset=RW, peak=70), week(reset=monday, peak=10)]
        resets = journalcore.week_resets(j, monday - H, monday)
        self.assertEqual(resets[-3:], [RW - W, RW, monday])
        self.assertEqual([journalcore.week_summary(j, r, monday - H)["pct"] for r in resets[-3:]], [60, 70, 10])

    def test_bad_reset_values(self):
        j = journalcore.new(0)
        now = RW - H
        for bad in (float("inf"), float("nan"), RW * 1000, RW + 1000 * W, True, "x"):
            self.assertEqual(journalcore.week_resets(j, now, bad), [], bad)
        j["weeks"] = [week()]
        self.assertEqual(journalcore.week_resets(j, now, RW * 1000), [RW])   # 일지의 주로

    def test_hour_buckets_with_jittery_resets(self):
        """리셋이 몇 초 흔들려도 시간 칸은 한 주에만, 빠짐없이."""
        j = journalcore.new(0)
        j["weeks"] = [week(reset=RW - W - 10), week(reset=RW + 10)]
        for at in (RW - W - H, RW - W, RW - W + H, RW - H):
            j["hours"][journalcore.hour_key(at)] = usd(10)
        old = journalcore.week_summary(j, RW - W - 10, RW)
        new = journalcore.week_summary(j, RW + 10, RW)
        self.assertAlmostEqual(old["usd"] + new["usd"], 40)
        self.assertAlmostEqual(new["usd"], 30)   # 08:00 칸은 새 주
        self.assertAlmostEqual(new["days"][0], 20)


class ReachTest(unittest.TestCase):
    """지난 주들에 오른 높이 (기록 랜드마크): 일지 창의 높이와 같은 규칙, 넘겨 보는 주와 같은 이름."""

    def test_reach_of_a_week(self):
        j = journalcore.new(0)
        j["weeks"] = [week(reset=RW - 2 * W, peak=100, full=RW - 2 * W - H), week(reset=RW - W, peak=40)]
        walls = [{"end": RW - 2 * H, "height": 1234.5, "reason": "full"}]
        self.assertEqual(journalcore.reach(j, RW - 2 * W), climbcore.MOON)
        self.assertEqual(journalcore.reach(j, RW - W + 60), climbcore.week_height(40))   # 리셋이 흔들려도 같은 주
        self.assertEqual(journalcore.reach(j, RW, walls), 1234.5)   # % 가 없으면 그 주에 끝난 벽
        self.assertIsNone(journalcore.reach(j, RW))
        self.assertEqual(journalcore.week_summary(j, RW - W, RW - H)["height"], climbcore.week_height(40))

    def test_past_weeks(self):
        j = journalcore.new(0)
        j["weeks"] = [week(reset=RW - n * W, peak=10 * n) for n in (1, 3, 4, 5)] + [week(reset=RW, peak=5)]
        now = RW - H
        self.assertEqual(journalcore.past_reach(j, now, RW),   # 2주 전은 기록이 없어 빠지고, 5주 전은 4주 밖
                         [("지난주", climbcore.week_height(10)), ("3주 전", climbcore.week_height(30)),
                          ("4주 전", climbcore.week_height(40))])
        self.assertEqual(journalcore.past_reach(j, now, RW, weeks=1), [("지난주", climbcore.week_height(10))])
        self.assertEqual(journalcore.past_reach(j, RW + H, RW + W)[0], ("지난주", climbcore.week_height(5)))   # 리셋 뒤
        self.assertEqual(journalcore.past_reach(journalcore.new(0), now, RW), [])
        self.assertEqual([journalcore.weeks_ago(n) for n in (1, 2, 4)], ["지난주", "2주 전", "4주 전"])

    def test_week_just_ended_with_a_late_reset(self):
        """일지의 주가 리셋을 1분 늦게 들고 있어도, 리셋 바로 뒤 그 주는 지난주."""
        j = journalcore.new(0)
        j["weeks"] = [week(reset=RW - W, peak=20), week(reset=RW + 59, peak=30)]
        self.assertEqual(journalcore.past_reach(j, RW + 10, RW + W),
                         [("지난주", climbcore.week_height(30)), ("2주 전", climbcore.week_height(20))])

    def test_wall_of_the_cycle_it_ended(self):
        """펫이 한 주 넘게 꺼져 있다 늦게 끝낸 벽은 끝낸 주기 (cycle) 의 주에."""
        j = journalcore.new(0)
        walls = [{"end": RW + 3 * 86400, "height": 777.0, "reason": "reset", "cycle": RW - W}]
        self.assertEqual(journalcore.reach(j, RW - W, walls), 777.0)
        self.assertIsNone(journalcore.reach(j, RW, walls))
        self.assertEqual(journalcore.reach(j, RW, [dict(walls[0], cycle=None)]), 777.0)   # cycle 없는 옛 기록은 끝난 때로

    def test_broken_hour_keys(self):
        j = journalcore.restore({"hours": {"2026-10-00T10": 5.0, "2026-10-20T25": 5.0, "2026-10-20T10": 5.0}}, RW)
        self.assertEqual(list(j["hours"]), ["2026-10-20T10"])
        self.assertTrue(journalcore.week_resets(j, RW - H, RW))


class TallyTest(unittest.TestCase):
    """등반 횟수: 아바타가 세고, 일지는 그때 든 주에 더한다."""

    def test_add_to_the_current_week_and_restore(self):
        j = journalcore.new(0)
        self.assertFalse(journalcore.add_tally(j, {"crux": 1}, RW - H))   # 주를 모르면 버린다
        j["weeks"] = [week(reset=RW - W), week()]
        self.assertTrue(journalcore.add_tally(j, {"crux": 2, "fall": 1, "jump": 5}, RW - H))
        self.assertTrue(journalcore.add_tally(j, {"crux": 1, "dyno": 0}, RW - 2 * H))
        self.assertFalse(journalcore.add_tally(j, {}, RW - H))
        self.assertEqual(j["weeks"][1]["climb"], {"crux": 3, "fall": 1})
        self.assertNotIn("climb", j["weeks"][0])
        data = json.loads(json.dumps(journalcore.state(j)))
        data["weeks"][1]["climb"].update({"nap": True, "sleep": -1, "bogus": 3})
        self.assertEqual(journalcore.restore(data, RW)["weeks"][1]["climb"], {"crux": 3, "fall": 1})
        self.assertEqual(journalcore.week_summary(j, RW, RW - H)["climb"], {"crux": 3, "fall": 1})

    def test_climber_counts(self):
        sim = climbcore.Climber(seed=3)
        sim.work = 1
        for _ in range(60 * 8):
            sim.update(1 / 60)
        sim.signal("dyno")
        for _ in range(60 * 8):
            sim.update(1 / 60)
        sim.hp = 0
        sim.signal("exhaust")   # 사용량 잠은 세지 않는다 (5시간 창이 다 찬 횟수)
        for _ in range(60 * 8):
            sim.update(1 / 60)
        self.assertTrue(sim.sleeping)
        self.assertEqual(sim.take_tally(), {"dyno": 1})
        self.assertEqual(sim.take_tally(), {})
        nap = climbcore.Climber(seed=4)
        next(nap.exhaust(nap=True))
        self.assertEqual(nap.take_tally(), {"nap": 1})


if __name__ == "__main__":
    unittest.main()
