import json
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import climbcore  # noqa: E402
from climbcore import M_PER_TOKEN, Climber, TokenMeter  # noqa: E402

DT = 1 / 30


def usage_line(mid, out, ts=None):
    """기록 파일에서 응답 한 줄 (출력 토큰 out 개)."""
    stamp = ts or time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    return json.dumps({"type": "assistant", "timestamp": stamp,
                       "message": {"id": mid, "usage": {"input_tokens": 2, "output_tokens": out,
                                                        "cache_read_input_tokens": 50000}}}) + "\n"


def run(sim, seconds, feed=None):
    t = 0.0
    while t < seconds:
        if feed:
            feed(sim, t)
        sim.update(DT)
        t += DT


class TokenMeterTest(unittest.TestCase):
    """기록 파일에서 출력 토큰 세기: 응답 id 마다 늘어난 만큼만, 다 쓰인 줄만, 띄운 뒤에 쓰인 것만."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "-home-u-proj"
        (self.project / "s1" / "subagents").mkdir(parents=True)
        self.main = self.project / "s1.jsonl"
        self.sub = self.project / "s1" / "subagents" / "agent-1.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def append(self, path, text):
        with path.open("a") as f:
            f.write(text)

    def test_first_run_skips_history(self):
        self.main.write_text(usage_line("old", 9999))
        meter = TokenMeter(self.root)
        self.assertEqual(meter.poll(), 0)
        self.append(self.main, usage_line("A", 300))
        self.assertEqual(meter.poll(), 300)

    def test_repeats_partials_new_files_and_subagents(self):
        self.main.write_text("")
        meter = TokenMeter(self.root)
        self.append(self.main, usage_line("A", 300) * 3 + '{"type":"user","message":{"content":"hi"}}\n')
        self.sub.write_text(usage_line("B", 50) + usage_line("B", 120) + usage_line("B", 400))
        (self.project / "s2.jsonl").write_text(usage_line("old", 7777, ts="2026-01-01T00:00:00.000Z")
                                               + usage_line("C", 1000))
        self.assertEqual(meter.poll(), 300 + 400 + 1000)
        half = usage_line("D", 250)
        self.append(self.main, half[:40])
        self.assertEqual(meter.poll(), 0)   # 반만 쓰인 줄은 아직
        self.append(self.main, half[40:] + usage_line("A", 300))
        self.append(self.sub, usage_line("B", 650))
        self.assertEqual(meter.poll(), 250 + 250)
        self.assertEqual(meter.total, 2200)

    def test_resume_counts_what_was_written_while_off_once(self):
        self.main.write_text("")
        meter = TokenMeter(self.root)
        self.append(self.main, usage_line("A", 100))
        meter.poll()
        self.append(self.main, usage_line("E", 30)[:25])   # 끄는 순간 반만 쓰인 줄
        meter.poll()
        saved = json.loads(json.dumps(meter.state()))     # 파일에 저장했다가 읽은 것처럼
        self.append(self.main, usage_line("E", 30)[25:] + usage_line("A", 100) + usage_line("F", 500))
        (self.project / "s3.jsonl").write_text(usage_line("old", 8888, ts="2026-01-01T00:00:00.000Z")
                                               + usage_line("G", 70))
        again = TokenMeter(self.root, saved=saved)
        self.assertEqual(again.poll(), 30 + 500 + 70)    # A 는 이미 셌고, 옛 줄은 옮겨 적힌 것
        self.assertEqual(again.poll(), 0)

    def test_resume_in_the_middle_of_a_backlog(self):
        self.main.write_text("")
        meter = TokenMeter(self.root)
        saved = meter.state()
        old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 600))   # 꺼져 있던 10분 전에 쓴 줄들
        self.append(self.main, "".join(usage_line(f"M{i}", 10, ts=old) for i in range(40)))
        saved["ts"] -= 900
        first = TokenMeter(self.root, saved=saved)
        first.POLL_BYTES = 1000
        got = first.poll()            # 밀린 것을 다 못 읽고
        again = TokenMeter(self.root, saved=json.loads(json.dumps(first.state())))   # 저장했다가 다시 켠다
        again.POLL_BYTES = 1000
        for _ in range(40):
            got += again.poll()
        self.assertEqual(got, 400)

    def test_odd_lines_are_skipped(self):
        self.main.write_text("")
        meter = TokenMeter(self.root)
        odd = [{"message": {"id": ["x"], "usage": {"output_tokens": 5}}},
               {"message": {"id": "y", "usage": {"output_tokens": True}}},
               {"message": {"id": "z", "usage": "x"}}, ["output_tokens"], "output_tokens"]
        self.append(self.main, "".join(json.dumps(o) + "\n" for o in odd) + "[" * 100000 + '"output_tokens"\n'
                    + usage_line("ok", 7))
        self.assertEqual(meter.poll(), 7)

    def test_broken_saved_state_starts_fresh(self):
        self.main.write_text(usage_line("old", 9999))
        meter = TokenMeter(self.root, saved={"offsets": "x", "ts": "y"})
        self.assertEqual(meter.poll(), 0)

    def test_deleted_file_is_forgotten(self):
        self.main.write_text(usage_line("old", 1))
        meter = TokenMeter(self.root)
        self.main.unlink()
        meter.poll()
        self.assertNotIn(str(self.main), meter.state()["offsets"])

    def test_poll_budget_spreads_a_big_backlog(self):
        self.main.write_text("")
        meter = TokenMeter(self.root)
        meter.POLL_BYTES = 1000
        self.append(self.main, "".join(usage_line(f"M{i}", 10) for i in range(40)))
        first = meter.poll()
        self.assertLess(first, 400)
        total = first
        for _ in range(40):
            total += meter.poll()
        self.assertEqual(total, 400)


class ClimberTest(unittest.TestCase):

    def test_climbs_what_was_deposited(self):
        sim = Climber(11)
        sim.work = 1
        top0, state = sim.top_y, {"next": 0.0}

        def feed(s, t):
            if t >= state["next"] and t < 120:
                s.deposit(1000)
                state["next"] += 15

        run(sim, 120, feed)
        for _ in range(120):   # 남은 건 마저 오른다
            if not sim.has_climb():
                break
            run(sim, 1.0)
        run(sim, 3.0)
        self.assertLessEqual(abs((sim.top_y - top0) - sim.tokens * M_PER_TOKEN), 1.0)
        self.assertEqual(sim.missing, 0)

    def test_waits_with_working_pets_and_rests_without(self):
        for work, allowed in ((1, {"idle_shake", "idle_chalk", "idle_look"}),
                              (0, {"idle_shake", "idle_chalk", "idle_look", "idle_blow"})):
            sim = Climber(7)
            sim.work = work
            top0 = sim.top_y
            run(sim, 40)
            idles = {a for a in sim.actions if a.startswith("idle_")}
            self.assertLessEqual(sim.top_y - top0, 0.1)
            self.assertTrue(idles and idles <= allowed, idles)

    def test_bigger_bank_is_faster(self):
        sim = Climber(7)
        for bank, pace in ((1.0, 1), (10.0, 2), (40.0, 3)):
            sim.bank = bank
            self.assertEqual(sim.pace, pace)
        climbed = {}
        for bank in (5.0, 50.0):   # 쌓인 높이를 그대로 두고 60초 동안 오른 높이
            sim = Climber(11)
            top0 = sim.top_y
            run(sim, 60, lambda s, t, b=bank: setattr(s, "bank", b))
            climbed[bank] = sim.top_y - top0
        self.assertGreater(climbed[50.0], climbed[5.0] * 1.8)

    def test_fall_recovery_does_not_spend_the_bank(self):
        sim = Climber(7)
        sim.top_y = sim.body.y + 1.0   # 쉬는 자세로 낮춘 몸은 추락이 아니다
        self.assertFalse(sim.has_climb())
        top = sim.top_y = sim.body.y + 3.0   # 3m 떨어진 뒤: 쌓인 높이가 없어도 원래 자리까지 다시 오른다
        run(sim, 40)
        self.assertGreater(sim.body.y, top - 1.6)
        self.assertGreater(sim.bank, -1.0)   # 원래 자리를 넘은 한 동작만큼만 쓴다

    def test_session_dyno_is_a_bonus(self):
        sim = Climber(7)
        top0 = sim.top_y
        sim.signal("dyno")
        run(sim, 6)
        self.assertGreater(sim.top_y - top0, 1.0)
        self.assertGreater(sim.bank, -0.3)

    def test_milestones_once_across_restore(self):
        sim = Climber(3)
        sim.jump(27.0)
        sim.deposit(4000)   # 8m
        run(sim, 60)
        self.assertEqual(sim.banners, [30])
        saved = json.loads(json.dumps(sim.snapshot()))
        again = Climber(5)
        again.restore(saved)
        self.assertGreaterEqual(again.altitude(), 30)
        self.assertEqual(again.passed, 1)
        self.assertAlmostEqual(again.bank, sim.bank, places=2)
        self.assertEqual(again.tokens, 4000)
        again.alt0 -= 5     # 내려왔다가 다시 넘어도
        again.deposit(5000)
        run(again, 60)
        self.assertNotIn(30, again.banners)

    def test_restore_ignores_broken_values(self):
        sim = Climber(3)
        sim.restore({"height": "x", "peak": float("nan"), "bank": None, "tokens": -5})
        self.assertEqual((round(sim.altitude(), 6), sim.peak, sim.bank, sim.tokens), (0.0, 0.0, 0.0, 0))
        sim.restore(None)
        self.assertEqual(sim.passed, 0)

    def test_signal_cooldown_and_sleep(self):
        sim = Climber(7)
        self.assertTrue(sim.signal("dyno"))
        self.assertFalse(sim.signal("dyno"))     # 8초 안
        sim.t += 9
        self.assertTrue(sim.signal("dyno"))
        sim.sleeping = True
        self.assertFalse(sim.signal("fall"))
        self.assertTrue(sim.signal("wake"))

    def test_requests_before_sleep_are_dropped(self):
        sim = Climber(7)
        sim.hp = 0
        sim.signal("exhaust")
        run(sim, 1)
        sim.signal("dyno")     # 펴는 동안 일을 끝낸 세션
        sim.signal("chalk")
        run(sim, 8)
        self.assertTrue(sim.sleeping)
        self.assertNotIn("dyno", sim.actions)
        self.assertIsNotNone(sim.ledge)
        self.assertEqual(sim.requests, [])

    def test_exhaust_is_called_off_when_hp_returns(self):
        sim = Climber(7)
        sim.hp = 0
        sim.signal("exhaust")
        run(sim, 1.5)
        sim.hp = 50            # 펴는 동안 리셋 (창이 숨어 깨우는 신호를 못 받았다)
        run(sim, 14)
        self.assertFalse(sim.sleeping)
        self.assertIsNone(sim.ledge)
        sim2 = Climber(7)
        sim2.signal("exhaust")  # 체력이 남아 있으면 펴지 않는다
        run(sim2, 3)
        self.assertNotIn("exhaust", sim2.actions)

    def test_tiny_steps_do_not_jolt(self):
        sim = Climber(7)
        sim.update(0.0)
        y = sim.body.y
        sim.update(1e-5)
        self.assertLess(abs(sim.body.y - y), 0.01)

    def test_exhaust_twice_makes_one_portaledge(self):
        sim = Climber(7)
        sim.hp = 0
        sim.signal("exhaust")
        sim.signal("exhaust")
        run(sim, 8)
        self.assertTrue(sim.sleeping)
        self.assertEqual(sim.actions.count("exhaust"), 1)
        sim.hp = 100
        sim.signal("wake")
        run(sim, 8)
        self.assertFalse(sim.sleeping)
        self.assertIsNone(sim.ledge)

    def test_same_limb_rarely_moves_twice(self):
        repeats = moves = far = 0
        for seed in (2, 5):
            sim = Climber(seed)
            sim.tokens_on, sim.work = False, 1
            run(sim, 150)
            m = sim.moves
            repeats += sum(m[i] == m[i - 1] for i in range(1, len(m)))
            moves, far = moves + len(m), far + sim.far_reaches
            self.assertEqual(sim.missing, 0)
        self.assertLessEqual(repeats / moves, 0.06)
        self.assertGreater(far, 0)   # 같은 손발을 또 옮기지 않으려고 한 번에 멀리 간 적이 있다

    def test_nap_after_three_minutes_of_rest(self):
        sim = Climber(7)
        sim.work = 1
        run(sim, climbcore.NAP_AFTER - 2)
        self.assertFalse(sim.sleeping)
        self.assertNotIn("nap", sim.actions)
        start = None
        t = climbcore.NAP_AFTER - 2
        while t < climbcore.NAP_AFTER + 12:
            sim.update(DT)
            t += DT
            if start is None and "nap" in sim.actions:
                start = t
        self.assertIsNotNone(start)
        self.assertLessEqual(start, climbcore.NAP_AFTER + 5)
        self.assertTrue(sim.sleeping and sim.nap)
        self.assertEqual(sim.state_label(), "낮잠")

    def test_tokens_before_three_minutes_keep_it_awake(self):
        sim = Climber(7)
        sim.work = 1
        run(sim, 150)
        sim.deposit(600)
        run(sim, 80)
        self.assertNotIn("nap", sim.actions)

    def test_nap_wakes_on_tokens_and_naps_again(self):
        sim = Climber(7)
        sim.work = 1
        run(sim, climbcore.NAP_AFTER + 10)
        self.assertTrue(sim.nap)
        moves = len(sim.moves)
        sim.deposit(600)
        run(sim, 10)
        self.assertFalse(sim.sleeping)
        self.assertGreater(len(sim.moves), moves)   # 깨어나 다시 오른다
        run(sim, climbcore.NAP_AFTER + 60)          # 다 오르고 다시 쉬기 시작한 뒤 3분
        self.assertTrue(sim.sleeping and sim.nap)
        self.assertEqual(sim.actions.count("nap"), 2)

    def test_nap_turns_into_exhaustion_at_zero(self):
        sim = Climber(7)
        sim.work = 1
        run(sim, climbcore.NAP_AFTER + 10)
        sim.hp = 0
        sim.signal("exhaust")
        self.assertFalse(sim.nap)
        sim.deposit(5000)
        run(sim, 20)
        self.assertTrue(sim.sleeping)   # 토큰으로는 깨지 않는다
        sim.hp = 100
        sim.signal("wake")
        run(sim, 10)
        self.assertFalse(sim.sleeping)

    def test_weather_stays_during_nap(self):
        sim = Climber(7)
        sim.work = 2
        run(sim, climbcore.NAP_AFTER + 30)
        self.assertTrue(sim.nap)
        self.assertGreater(sim.weather, 0.6)

    def test_holds_break_only_when_climbing_onto_new_holds(self):
        chance = climbcore.BREAK_CHANCE
        try:
            climbcore.BREAK_CHANCE = 1.0
            sim = Climber(7)
            sim.work = 1
            run(sim, 40)      # 쌓인 높이가 없어 기다리며 초크·털기로 같은 홀드를 다시 잡는다
            self.assertTrue(any(a.startswith("idle_") for a in sim.actions))
            self.assertNotIn("break", sim.actions)
            sim.deposit(1000)
            run(sim, 20)      # 오르며 새 홀드를 잡으면 부서진다
            self.assertIn("break", sim.actions)
        finally:
            climbcore.BREAK_CHANCE = chance

    def test_sprint_never_makes_holds(self):
        for seed in range(4):
            sim = Climber(seed)
            sim.work = 3
            run(sim, 60, lambda s, t: setattr(s, "bank", max(s.bank, 50.0)))
            self.assertEqual(sim.missing, 0)
            self.assertIn("dyno_sprint", sim.actions)


class SignalTest(unittest.TestCase):

    def test_hp_signals(self):
        self.assertEqual(climbcore.hp_signals(None, 0), [("exhaust",)])
        self.assertEqual(climbcore.hp_signals(None, 50), [])
        self.assertEqual(climbcore.hp_signals(50, 49), [("chalk",)])
        self.assertEqual(climbcore.hp_signals(3, 0), [("exhaust",)])
        self.assertEqual(climbcore.hp_signals(0, 100), [("wake",)])
        self.assertEqual(climbcore.hp_signals(40, 95), [("refill", 55)])
        self.assertEqual(climbcore.hp_signals(40, 45), [])
        self.assertEqual(climbcore.hp_signals(40, None), [])

    def test_session_signals(self):
        changes = {"a": ("running", "done"), "b": ("running", "error"), "c": ("idle", "running"),
                   "d": ("running", "waiting")}
        self.assertEqual(sorted(climbcore.session_signals(changes)), [("dyno",), ("fall",)])

    def test_working_count_includes_background(self):
        states = {"a": "running", "b": "background", "c": "waiting", "d": "idle", "e": "done"}
        self.assertEqual(climbcore.working_count(states), 2)


class NightTest(unittest.TestCase):
    """서울 해 뜨고 지는 시각, 밤 정도, 헤드램프."""

    def test_sun_times_seoul(self):
        for day, rise, sset in ((date(2026, 6, 21), 5 + 11 / 60, 19 + 57 / 60),
                                (date(2026, 12, 21), 7 + 43 / 60, 17 + 17 / 60)):
            r, s = climbcore.sun_times(day)
            self.assertAlmostEqual(r, rise, delta=5 / 60)
            self.assertAlmostEqual(s, sset, delta=5 / 60)

    def test_night_level(self):
        level = climbcore.night_level
        self.assertEqual(level(datetime(2026, 6, 21, 13, 0)), 0.0)
        self.assertEqual(level(datetime(2026, 6, 21, 23, 0)), 1.0)
        self.assertEqual(level(datetime(2026, 6, 21, 3, 0)), 1.0)
        self.assertEqual(level(datetime(2026, 6, 21, 18, 0)), 0.0)    # 여름 18시는 아직 낮
        self.assertEqual(level(datetime(2026, 12, 21, 18, 0)), 1.0)   # 겨울 18시는 밤
        evening = [level(datetime(2026, 10, 19, h, m)) for h in (17, 18) for m in range(0, 60, 5)]
        self.assertEqual(evening, sorted(evening))   # 해 질 무렵 천천히 어두워진다
        self.assertTrue(any(0.1 < v < 0.9 for v in evening))
        morning = [level(datetime(2026, 10, 19, h, m)) for h in (6, 7) for m in range(0, 60, 5)]
        self.assertEqual(morning, sorted(morning, reverse=True))
        for utc_hour, want in ((14, 1.0), (4, 0.0), (22, 0.0)):   # 시간대가 다른 시각은 서울 시각으로 바꿔 본다
            self.assertEqual(level(datetime(2026, 6, 21, utc_hour, tzinfo=timezone.utc)), want)

    def test_lamp_follows_next_hold_and_sleeps(self):
        sim = Climber(3)
        sim.tokens_on, sim.work = False, 1
        run(sim, 2.0)
        self.assertIsNone(sim.lamp)
        sim.night = 1.0
        near = 0
        for _ in range(60):   # 오르는 동안 다음 홀드 (보는 곳) 를 따라 비춘다
            run(sim, 0.1)
            self.assertIsNotNone(sim.lamp)
            if sim.look_at is not None and (sim.lamp - sim.look_at).len() < 0.3:
                near += 1
        self.assertGreater(near, 5)
        sim.hp = 0
        sim.signal("exhaust")
        run(sim, 12.0)
        self.assertTrue(sim.sleeping)
        self.assertIsNone(sim.lamp)
        sim.hp = 100
        sim.signal("wake")
        run(sim, 3.0)
        self.assertFalse(sim.sleeping)
        self.assertIsNotNone(sim.lamp)

    def test_night_does_not_change_climbing(self):
        ends = []
        for night in (0.0, 1.0):
            sim = Climber(11)
            sim.tokens_on, sim.work, sim.night = False, 1, night
            run(sim, 60.0)
            ends.append((round(sim.body.x, 6), round(sim.body.y, 6), list(sim.moves), list(sim.actions)))
        self.assertEqual(ends[0], ends[1])


class CruxTest(unittest.TestCase):
    """크럭스: 홀드를 비운 구간 끝의 몸 반대쪽 큰 홀드를 다이노로 잡아 넘는다."""

    def climb_to_crux(self, seed=3, events=True):
        sim = Climber(seed)
        sim.tokens_on, sim.work, sim.events = False, 3, events
        sim.route.next_crux = 15.0   # 첫 크럭스를 가까이 (아직 깔지 않은 높이)
        for _ in range(600):
            run(sim, 0.5)
            if sim.crux_log:
                break
        return sim

    def test_route_leaves_a_gap_and_puts_the_jug_on_the_far_side(self):
        sim = Climber(3)
        sim.route.next_crux = 15.0
        sim.route.fill(sim, 30.0)
        jug = sim.route.jugs[0]
        start = jug.crux_from
        self.assertTrue(1.5 <= jug.p.y - start <= 2.5 and jug.big)
        gap = [h for h in sim.holds if start <= h.p.y < jug.p.y - 0.01 and not h.foot]
        self.assertEqual(gap, [])   # 크럭스 구간에는 손으로 잡을 홀드가 없다
        below = [h for h in sim.holds if start - 1.5 < h.p.y < start and not h.foot]
        self.assertTrue(below)
        self.assertTrue(all(h.p.x * jug.p.x < 0 or abs(h.p.x) < 0.3 for h in below))   # 바로 아래 홀드는 반대쪽
        self.assertGreater(abs(jug.p.x), 1.5)
        chips = [h for h in sim.holds if h.foot]   # 발 턱: 끝 홀드 쪽, 둘은 끝 홀드 아래 (잡은 뒤 딛기), 하나는 구간 안
        self.assertEqual(len(chips), 3)
        self.assertTrue(all(h.p.x * jug.p.x > 0 for h in chips))
        self.assertEqual(sum(1 for h in chips if h.p.y < jug.p.y - 2), 2)
        self.assertEqual(sum(1 for h in chips if start < h.p.y < jug.p.y), 1)
        gap_bolts = [b for b in sim.bolts if start - 1.0 < b.p.y < jug.p.y]
        self.assertEqual(gap_bolts, [])   # 구간 안에는 볼트가 없고
        base = [b for b in sim.bolts if start - 4.5 < b.p.y <= start - 3.0]
        self.assertTrue(base and all(b.p.x * jug.p.x < 0 for b in base))   # 뛰기 전에 걸 볼트는 시작 쪽에

    def test_waits_for_tokens_before_the_crux(self):
        sim = Climber(3)
        sim.route.next_crux = 15.0
        for _ in range(600):   # 1m 씩 조금씩 넣으면 크럭스 아래 끝에서 끝 홀드까지 모자라 기다린다
            if sim.wait_for or sim.crux_log:
                break
            if sim.bank < 0.3:
                sim.deposit(500)
            run(sim, 1.0)
        self.assertEqual(sim.crux_log, [])
        self.assertTrue(sim.parked and sim.wait_for > 0)
        self.assertFalse(sim.climb_ready())
        sim.deposit(500)   # 1m 더 와도 모자라면 계속 기다린다
        run(sim, 25.0)
        self.assertEqual(sim.crux_log, [])
        sim.deposit(3000)
        run(sim, 30.0)
        self.assertEqual(len(sim.crux_log), 1)
        self.assertGreater(sim.bank, -1.0)   # 넣은 것보다 크게 더 오르지 않는다

    def test_climber_dynos_across_and_never_grabs_foot_chips(self):
        sim = self.climb_to_crux()
        self.assertEqual(len(sim.crux_log), 1)
        jug = sim.route.jugs[0]
        self.assertTrue(any(g[2] is jug for g in sim.grabs))
        self.assertIn("crux_dyno", sim.actions)
        self.assertFalse(any(g[2].foot and g[1] in ("LH", "RH") for g in sim.grabs))
        self.assertEqual(sim.missing, 0)
        run(sim, 45.0)   # 넘은 뒤에도 계속 오른다
        self.assertGreater(sim.top_y, jug.p.y + 3.0)

    def test_no_limb_jumps_during_crux(self):
        """놓치고 다시 뛰는 동안 몸과 그려지는 발끝이 한 프레임에 크게 튀지 않는다 (60fps)."""
        saved = climbcore.CRUX_MISS
        climbcore.CRUX_MISS = 1.0
        try:
            for seed in (3, 4, 5, 7):
                sim = Climber(seed)
                sim.tokens_on, sim.work = False, 3
                sim.route.next_crux = 15.0
                last, worst = None, {"body": 0.0, "feet": 0.0}
                for _ in range(60 * 240):
                    sim.update(1 / 60)
                    inside = sim.actions and sim.actions[-1] in ("crux", "crux_miss", "crux_dyno")
                    tips = [climbcore.two_bone(f.root(sim.body, sim.tilt), f.pos, *climbcore.LEG, f.side, "F", f.drop)[1]
                            for f in (sim.limbs["LF"], sim.limbs["RF"])]
                    now = (sim.body, tips)
                    if inside and last is not None:
                        worst["body"] = max(worst["body"], (now[0] - last[0]).len())
                        worst["feet"] = max([worst["feet"]] + [(a - b).len() for a, b in zip(now[1], last[1])])
                    last = now
                    if sim.crux_log and sim.actions[-1] not in ("crux", "crux_miss", "crux_dyno"):
                        break
                self.assertEqual(sim.crux_log[0][0], 2, seed)
                self.assertLess(worst["body"], 0.6, seed)   # 다이노는 빠르지만 이어진다 (전력 질주 다이노도 0.48m)
                self.assertLess(worst["feet"], 0.7, seed)
        finally:
            climbcore.CRUX_MISS = saved

    def test_miss_then_retry_only_with_events(self):
        saved = climbcore.CRUX_MISS
        climbcore.CRUX_MISS = 1.0
        try:
            sim = self.climb_to_crux()
            self.assertEqual(sim.crux_log[0][0], 2)
            self.assertIn("crux_miss", sim.actions)
            sim = self.climb_to_crux(events=False)
            self.assertEqual(sim.crux_log[0][0], 1)
            self.assertNotIn("crux_miss", sim.actions)
        finally:
            climbcore.CRUX_MISS = saved


class StateFileTest(unittest.TestCase):

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data" / "climb.json"
            self.assertEqual(climbcore.load_state(path), ({}, True))
            climbcore.save_state(path, {"climb": {"height": 12.5}})
            data, intact = climbcore.load_state(path)
            self.assertTrue(intact)
            self.assertEqual(data["climb"], {"height": 12.5})
            path.write_text("{broken")
            self.assertEqual(climbcore.load_state(path), ({}, False))


if __name__ == "__main__":
    unittest.main()
