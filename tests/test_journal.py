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


if __name__ == "__main__":
    unittest.main()
