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


def usage_line(mid, out, ts=None, model=None, **usage):
    """기록 파일에서 응답 한 줄 (출력 토큰 out 개, 그 밖의 토큰은 usage 로)."""
    stamp = ts or time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    message = {"id": mid, "usage": {"output_tokens": out, **usage}}
    if model:
        message["model"] = model
    return json.dumps({"type": "assistant", "timestamp": stamp, "message": message}) + "\n"


def meters(m):
    """m 미터를 오를 만큼의 토큰 (출력 환산)."""
    return round(m / M_PER_TOKEN)


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

    def test_counts_all_tokens_at_api_prices(self):
        self.main.write_text("")
        meter = TokenMeter(self.root)
        # opus: 출력 1,000 + 입력 500 (x0.2) + 캐시 읽기 100,000 (x0.01) + 캐시 쓰기 5분 2,000 (x0.25) + 1시간 1,000 (x0.4)
        self.append(self.main, usage_line("A", 1000, model="claude-opus-5-5", input_tokens=500, cache_read_input_tokens=100000,
                                          cache_creation_input_tokens=3000,
                                          cache_creation={"ephemeral_5m_input_tokens": 2000, "ephemeral_1h_input_tokens": 1000}))
        self.assertAlmostEqual(meter.poll(), 1000 + 100 + 1000 + 500 + 400)
        self.append(self.main, usage_line("A", 1500, model="claude-opus-5-5", input_tokens=500, cache_read_input_tokens=100000,
                                          cache_creation_input_tokens=3000,
                                          cache_creation={"ephemeral_5m_input_tokens": 2000, "ephemeral_1h_input_tokens": 1000}))
        self.assertAlmostEqual(meter.poll(), 500)   # 같은 응답이 더 써지면 늘어난 출력만
        self.append(self.main, usage_line("B", 1000, model="claude-sonnet-5-5", cache_read_input_tokens=20000)
                    + usage_line("C", 1000, model="claude-haiku-5-5")
                    + usage_line("D", 0, cache_creation_input_tokens=4000))   # 나눈 값이 없으면 5분, 모르는 모델은 opus
        self.assertAlmostEqual(meter.poll(), (500 + 200) + 25 + 1000)
        self.assertAlmostEqual(climbcore.usage_cost("claude-opus-5-5", {"output_tokens": 1000}), 0.02)
        self.assertAlmostEqual(meter.total * climbcore.TOKEN_USD, (4000 + 1225) * 20 / 1e6)

    def test_prices_by_model_mode_and_attempts(self):
        tokens = climbcore.usage_tokens
        self.assertAlmostEqual(tokens("claude-opus-5-5", {"output_tokens": 1000}), 1000)
        self.assertAlmostEqual(tokens("claude-opus-5", {"output_tokens": 1000, "cache_read_input_tokens": 1000}), 1250 + 25)
        self.assertAlmostEqual(tokens("claude-opus-6", {"output_tokens": 1000}), 1000)   # 표에 없으면 같은 계열 최신
        self.assertAlmostEqual(tokens("claude-fable-5-1", {"output_tokens": 100, "cache_read_input_tokens": 1000}), 250 + 12.5)
        self.assertAlmostEqual(tokens("claude-haiku-4-5", {"output_tokens": 1000}), 250)
        self.assertAlmostEqual(tokens("claude-haiku-5-5", {"output_tokens": 1000, "input_tokens": 1000}), 25 + 5)
        self.assertAlmostEqual(tokens("claude-haiku-5-5", {"output_tokens": 1000, "cache_read_input_tokens": 200000}),
                               125 + 500)   # 프롬프트가 10만 토큰 넘으면 비싼 쪽
        self.assertAlmostEqual(tokens("claude-opus-5-5", {"output_tokens": 1000, "speed": "fast"}), 2000)   # 빠른 모드는 두 배
        tries = {"output_tokens": 300, "iterations": [{"output_tokens": 500, "input_tokens": 1000}, {"output_tokens": 300}]}
        self.assertAlmostEqual(tokens("claude-opus-5-5", tries), 800 + 200)   # 시도가 여럿이면 모두 더한다
        zeroed = {"output_tokens": 0, "cache_creation_input_tokens": 0, "cache_creation": {"ephemeral_1h_input_tokens": 1900}}
        self.assertAlmostEqual(tokens("claude-opus-5-5", zeroed), 0)   # 나눈 값은 합계에 맞춘다
        extra = {"output_tokens": 0, "cache_creation_input_tokens": 3000, "cache_creation": {"ephemeral_1h_input_tokens": 1000}}
        self.assertAlmostEqual(tokens("claude-opus-5-5", extra), 2000 * 0.25 + 1000 * 0.4)

    def test_old_saved_counts_and_rounding_do_not_add_again(self):
        self.main.write_text("")
        meter = TokenMeter(self.root)
        self.append(self.main, usage_line("H", 333, model="claude-haiku-5-5", cache_read_input_tokens=777))
        meter.poll()
        saved = json.loads(json.dumps(meter.state()))
        saved["seen"]["O"] = 500   # 옛 기록: 출력 토큰 (정수)
        again = TokenMeter(self.root, saved=saved)
        self.append(self.main, usage_line("H", 333, model="claude-haiku-5-5", cache_read_input_tokens=777)
                    + usage_line("O", 500))
        self.assertEqual(again.poll(), 0)

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
                s.deposit(meters(2))
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
        sim.deposit(meters(8))
        run(sim, 60)
        self.assertEqual(sim.banners, [30])
        saved = json.loads(json.dumps(sim.snapshot()))
        again = Climber(5)
        again.restore(saved)
        self.assertGreaterEqual(again.altitude(), 30)
        self.assertEqual(again.passed, 1)
        self.assertAlmostEqual(again.bank, sim.bank, places=2)
        self.assertEqual(again.tokens, meters(8))
        again.alt0 -= 5     # 내려왔다가 다시 넘어도
        again.deposit(meters(10))
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

    def test_far_hands_pull_the_body_over_several_frames(self):
        """손이 닿는 거리보다 몸이 아래에 있으면 (벌린 자세에서 발이 빠졌다) 잡은 손은 몸을 한 프레임에 끌어 올리지 않고
        PULL_SPEED 로 몇 프레임에 나눠 끈다."""
        sim = Climber(5)
        sim.tokens_on = False
        run(sim, 5.0)
        hands = [sim.limbs["LH"], sim.limbs["RH"]]
        for limb in hands:
            limb.mode, limb.pos = "grip", sim.grip_point(limb, limb.hold)
        for name in ("LF", "RF"):
            sim.limbs[name].mode = "free"
        sim.mode, sim.vel, sim.phase_target = "spring", climbcore.V(), None
        sim.body = sim.body - climbcore.V(0, 0.8)
        start = sim.body
        sim.move_body(1 / 60)
        self.assertLess((sim.body - start).len(), 0.12)   # 초당 5m 면 한 프레임 0.08m 남짓 (안 막으면 0.35m)
        for _ in range(30):
            sim.move_body(1 / 60)
        for limb in hands:   # 0.5초 안에는 다 끌어 올렸다
            self.assertLess((limb.pos - limb.root(sim.body, sim.tilt)).len(), limb.reach)

    def test_body_does_not_jump_when_a_foot_lets_go(self):
        """보통 오르기에서 몸이 한 프레임에 0.6m 넘게 튀지 않는다. 이 시드들은 PULL_SPEED 가 없으면 25초 안에 손발을
        닿는 거리 넘게 벌린 채 발을 떼어 (클립·미끄러짐) 남은 손이 몸을 한 번에 끌어 올렸다."""
        for seed in (2, 9, 16):
            sim = Climber(seed)
            sim.tokens_on, sim.work = False, 3
            worst = 0.0
            while sim.t < 25:
                body, mode = sim.body, sim.mode
                sim.update(DT)
                if sim.t > 3 and mode == sim.mode == "spring":
                    worst = max(worst, (sim.body - body).len())
            self.assertLess(worst, 0.6, seed)

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
        sim.deposit(meters(1.2))
        run(sim, 80)
        self.assertNotIn("nap", sim.actions)

    def test_nap_wakes_on_tokens_and_naps_again(self):
        sim = Climber(7)
        sim.work = 1
        run(sim, climbcore.NAP_AFTER + 10)
        self.assertTrue(sim.nap)
        moves = len(sim.moves)
        sim.deposit(meters(1.2))
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
        sim.deposit(meters(10))
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

    def test_no_lightning_above_clouds(self):
        for height, storm in ((500.0, True), (200000.0, False)):
            sim = Climber(7)
            sim.work, sim.events, sim.weather_kind = 3, False, "rain"
            sim.weather, sim.precipitating = 1.0, True   # 이미 내리는 비 (비·눈을 다시 뽑지 않는다)
            sim.jump(height)
            flashes = 0
            for _ in range(60 * 30):
                sim.update(DT)
                flashes += sim.flash == 1.0
            self.assertEqual(flashes > 0, storm)
            self.assertEqual(bool(sim.drops), storm)

    def test_holds_break_only_when_climbing_onto_new_holds(self):
        chance = climbcore.BREAK_CHANCE
        try:
            climbcore.BREAK_CHANCE = 1.0
            sim = Climber(7)
            sim.work = 1
            run(sim, 40)      # 쌓인 높이가 없어 기다리며 초크·털기로 같은 홀드를 다시 잡는다
            self.assertTrue(any(a.startswith("idle_") for a in sim.actions))
            self.assertNotIn("break", sim.actions)
            sim.deposit(meters(2))
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


class BalanceTest(unittest.TestCase):
    """두 발이 한쪽 아래, 두 손이 반대쪽 위로 쏠리면 빈 쪽 (손 쪽 아래) 에 한 발을 둔다.
    손발 어디에도 갈 데가 없으면 홀드를 만들기 전에 발을 고쳐 딛는다."""

    def lopsided(self, spots=None):
        """두 손은 오른쪽 위, 두 발은 왼쪽 아래에 잡은 아바타 (홀드는 그 넷뿐, 몸은 (0, 10)). spots 로 자리를 바꾼다."""
        sim = Climber(7)
        sim.events = False
        sim.body, sim.vel, sim.tilt = climbcore.V(0.0, 10.0), climbcore.V(), 0.0
        spots = spots or {"LH": (0.7, 11.3), "RH": (1.1, 11.2), "LF": (-0.9, 8.5), "RF": (-0.5, 8.4)}
        sim.holds = []
        for name, (x, y) in spots.items():
            hold = climbcore.Hold(climbcore.V(x, y), 0.25, sim.rng)
            sim.holds.append(hold)
            sim.grab(sim.limbs[name], hold)
        return sim

    def add_hold(self, sim, x, y):
        hold = climbcore.Hold(climbcore.V(x, y), 0.25, sim.rng)
        sim.holds.append(hold)
        return hold

    def test_lopsided_moves_the_empty_side_foot_first(self):
        sim = self.lopsided()
        self.assertEqual(sim.lean_side(), 1)
        self.assertTrue(sim.leaning())
        self.assertIsNone(sim.fill_move())    # 디딜 홀드도, 손이 갈 홀드도 없으면 벽을 딛지 않는다 (곧 돌아온다)
        self.add_hold(sim, 0.9, 11.9)
        foot, hold = sim.fill_move()          # 손이 갈 데가 있고 오른쪽 아래에 디딜 홀드가 없으면 벽을 딛는다
        self.assertEqual((foot.name, hold), ("RF", None))
        under = self.add_hold(sim, 0.6, 8.1)
        foot, hold = sim.fill_move()          # 있으면 그 홀드로
        self.assertEqual((foot.name, hold), ("RF", under))
        self.assertEqual(sim.lean_log, ["wall", "step"])
        sim.moves.append("RF")                # 방금 그 발을 옮겼는데도 쏠려 있으면 맴돌지 않고 다른 동작으로 넘긴다
        self.assertIsNone(sim.fill_move())
        sim.tokens_on = False
        self.assertNotEqual(sim.choose_move(), (sim.limbs["RF"], None))
        self.assertEqual(sim.lean_log, ["wall", "step"])

    def test_reach_into_lopsided_puts_a_foot_under_the_hands(self):
        sim = self.lopsided()
        target = self.add_hold(sim, 1.3, 12.0)
        support, free, style = sim.reach_stance(sim.limbs["RH"], target)
        self.assertEqual((support.name, free.name, style), ("LF", "RF", "balance"))
        spot = sim.free_spot(free, style, sum(sim.hands_after(sim.limbs["RH"], target)) / 2)()
        hip = free.root(sim.body, sim.tilt)
        self.assertGreater(spot.x, sim.body.x + climbcore.LEAN_SIDE)   # 손 쪽 아래 벽
        self.assertLess(spot.y, hip.y)
        self.assertLessEqual((spot - hip).len(), free.reach * 0.95 + 1e-9)
        self.assertIsNone(free.spot_hold)
        under = self.add_hold(sim, 0.6, 8.1)
        spot = sim.free_spot(free, style, 1.0)()   # 닿는 홀드가 있으면 그 위에 올려 둔다
        self.assertIs(free.spot_hold, under)
        self.assertLess((spot - under.grip("F")).len(), 1e-9)

    def test_not_lopsided_keeps_the_usual_stance(self):
        sim = self.lopsided()
        sim.grab(sim.limbs["RF"], self.add_hold(sim, 0.5, 8.4))   # 오른발이 몸 오른쪽에 있다
        self.assertEqual(sim.lean_side(), 0)
        self.assertIsNone(sim.fill_move())
        target = self.add_hold(sim, 1.3, 12.0)
        self.assertNotEqual(sim.reach_stance(sim.limbs["RH"], target)[2], "balance")

    def test_wall_foot_moves_the_body_under_the_hands(self):
        sim = self.lopsided()
        before = sim.hang_target()[0].x
        rf = sim.limbs["RF"]
        spot = sim.free_spot(rf, "balance", sum(sim.hands_x()) / 2)
        rf.mode, rf.follow, rf.free_style = "follow", spot, "balance"
        rf.pos = spot()
        self.assertGreater(sim.hang_target()[0].x, before + 0.2)
        self.assertFalse(sim.leaning())                     # 빈 쪽에 발이 있다
        rf.free_style = "smear"                              # 다른 자세로 뗀 발은 몸 자리에 넣지 않는다
        self.assertAlmostEqual(sim.hang_target()[0].x, sim.limbs["LF"].pos.x * 0.5 + sum(sim.hands_x()) / 4, places=6)

    def test_stray_free_foot_goes_to_the_empty_side(self):
        sim = self.lopsided()
        rf = sim.limbs["RF"]
        rf.mode, rf.free_style = "follow", "smear"           # 원래 홀드 아래 (왼쪽) 벽을 딛고 있다
        rf.follow = sim.free_spot(rf, "smear")
        rf.pos = rf.follow()
        hands = sorted((sim.limbs["LH"], sim.limbs["RH"]), key=sim.held_y)
        self.assertNotEqual(sim.free_foot_move(rf, hands), (rf, None))   # 손이 갈 데가 없으면 벽으로 가지 않는다
        self.add_hold(sim, 0.9, 11.9)
        self.assertEqual(sim.free_foot_move(rf, hands), (rf, None))   # 홀드가 없으면 빈 쪽 벽으로
        self.assertEqual(sim.free_log[-1], "wall")
        under = self.add_hold(sim, 0.6, 8.1)
        self.assertEqual(sim.free_foot_move(rf, hands), (rf, under))
        self.assertEqual(sim.free_log[-1], "fill")


    def test_stuck_low_foot_lets_go_before_making_a_hold(self):
        # 왼발은 몸보다 높이, 오른발은 한참 아래: 낮은 발에 묶여 몸이 못 일어서고 손이 닿는 데가 없다
        spots = {"LH": (0.3, 12.1), "RH": (0.0, 12.7), "LF": (-1.0, 10.8), "RF": (0.25, 7.85)}
        sim = self.lopsided(spots)
        sim.tokens_on = False
        self.assertEqual(sim.choose_move(), (sim.limbs["RF"], None))   # 낮은 발을 떼어 벽을 딛는다
        self.assertEqual((sim.unstuck, sim.missing), (1, 0))
        sim = self.lopsided(spots)
        sim.tokens_on = False
        lf = sim.limbs["LF"]
        lf.mode, lf.free_style = "follow", "smear"            # 높은 발을 떼어 둔 채 막혔으면 그 홀드로 돌려 딛는다
        lf.follow = sim.free_spot(lf, "smear")
        lf.pos = lf.follow()
        self.assertEqual(sim.choose_move(), (lf, lf.hold))
        self.assertEqual((sim.unstuck, sim.missing), (1, 0))


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

    def test_space_is_sunlit(self):
        for height, dark in ((500.0, 1.0), (95000.0, 0.5), (200000.0, 0.0)):
            sim = Climber(3)
            sim.tokens_on, sim.work, sim.night = False, 1, 1.0
            sim.jump(height)
            run(sim, 3.0)
            self.assertAlmostEqual(sim.dark(), dark, delta=0.05)   # 90~100km 에서 서서히 낮처럼
            self.assertEqual(sim.lamp is not None, dark > 0.05)

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
        self.assertTrue(climbcore.CRUX_GAP[0] <= jug.p.y - start <= climbcore.CRUX_GAP[1] and jug.big)
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
        self.assertEqual(sum(1 for h in chips if jug.p.y - 2 < h.p.y < jug.p.y), 1)   # 구간 가운데 (구간은 2m 넘게 길다)
        gap_bolts = [b for b in sim.bolts if start - 1.0 < b.p.y < jug.p.y]
        self.assertEqual(gap_bolts, [])   # 구간 안에는 볼트가 없고
        base = [b for b in sim.bolts if start - 2.0 < b.p.y <= start - 1.0]
        self.assertTrue(base and all(b.p.x * jug.p.x < 0 for b in base))   # 뛰기 전에 걸 볼트는 시작 쪽 손 높이에
        worst = 0.0   # 그 아래 볼트와 멀지 않다 (크럭스 앞에서 기다리다 떨어져도 멀리 안 간다)
        for seed in range(1, 41):
            far = Climber(seed)
            far.route.next_crux = 15.0
            far.route.fill(far, 200.0)
            for crux in far.route.jugs:
                ys = sorted(b.p.y for b in far.bolts if b.p.y < crux.crux_from)
                worst = max(worst, ys[-1] - ys[-2])
        self.assertLess(worst, 4.8)

    def test_waits_for_tokens_before_the_crux(self):
        sim = Climber(3)
        sim.events = False   # 놓쳐 떨어지면 다시 오르는 데 오래 걸린다 (놓침은 따로 본다)
        sim.route.next_crux = 15.0
        for _ in range(600):   # 1m 씩 조금씩 넣으면 크럭스 아래 끝에서 끝 홀드까지 모자라 기다린다
            if sim.wait_for or sim.crux_log:
                break
            if sim.bank < 0.3:
                sim.deposit(meters(1))
            run(sim, 1.0)
        self.assertEqual(sim.crux_log, [])
        self.assertTrue(sim.parked and sim.wait_for > 0)
        self.assertFalse(sim.climb_ready())
        sim.deposit(meters(1))   # 1m 더 와도 모자라면 계속 기다린다
        run(sim, 25.0)
        self.assertEqual(sim.crux_log, [])
        sim.deposit(meters(6))
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

    def test_jumps_from_the_lower_edge_without_climbing_into_it(self):
        """위쪽 손이 크럭스 아래 끝 띠 (CRUX_EDGE) 에 닿으면 띠 안에 잡을 홀드가 남아 있어도 더 올라서지 않고 뛴다.
        이 시드들은 띠 규칙이 없으면 띠 안 작은 홀드로 3~7번 더 올라선 뒤 뛰었다."""
        for seed in (3, 5, 12):
            sim = self.climb_to_crux(seed)
            self.assertEqual(len(sim.crux_log), 1, seed)
            self.assertEqual(sim.crux_log[0][1], 0, seed)   # 띠에 닿은 뒤 뛰기까지 손발 동작 수

    def test_no_limb_jumps_during_crux(self):
        """뛰고, 놓쳐 로프에 걸려 떨어지고, 다시 붙고, 다시 뛰는 동안 몸과 그려지는 발끝이 한 프레임에 크게 튀지 않는다
        (60fps. 다시 붙은 뒤 크럭스 아래까지 다시 오르는 보통 오르기는 뺀다)."""
        saved = climbcore.CRUX_MISS
        climbcore.CRUX_MISS = 1.0
        try:
            for seed in (3, 4, 5, 7):
                sim = Climber(seed)
                sim.tokens_on, sim.work = False, 3
                sim.route.next_crux = 15.0
                last, worst, roped, back = None, {"body": 0.0, "feet": 0.0}, False, False
                for _ in range(60 * 240):
                    sim.update(1 / 60)
                    if sim.rope is not None:
                        roped = True
                    elif roped and sim.mode == "spring":   # 로프 추락이 끝나 다시 붙었다
                        roped, back = False, True
                    if back and sim.actions[-1] == "crux":   # 크럭스 아래까지 다시 올라와 다시 뛴다
                        back = False
                    inside = sim.actions and sim.actions[-1] in ("crux", "crux_miss", "crux_dyno") and not back
                    tips = [climbcore.two_bone(f.root(sim.body, sim.tilt), f.pos, *climbcore.LEG, f.side, "F", f.drop)[1]
                            for f in (sim.limbs["LF"], sim.limbs["RF"])]
                    now = (sim.body, tips)
                    if inside and last is not None:
                        worst["body"] = max(worst["body"], (now[0] - last[0]).len())
                        worst["feet"] = max([worst["feet"]] + [(a - b).len() for a, b in zip(now[1], last[1])])
                    last = now
                    if sim.crux_log:
                        break
                self.assertEqual(sim.crux_log[0][0], 2, seed)
                self.assertLess(worst["body"], 0.6, seed)   # 다이노는 빠르지만 이어진다 (전력 질주 다이노도 0.48m)
                self.assertLess(worst["feet"], 0.7, seed)
        finally:
            climbcore.CRUX_MISS = saved

    def test_miss_falls_on_the_rope_and_climbs_back(self):
        """놓치면 뛰기 전에 건 퀵드로에 로프가 걸려 크럭스 아래로 떨어졌다가, 다시 붙어 올라와 두 번째에 넘는다.
        건 퀵드로가 없으면 예전처럼 잡던 홀드로 미끄러져 바로 다시 뛴다."""
        saved = climbcore.CRUX_MISS
        climbcore.CRUX_MISS = 1.0
        try:
            for seed in (3, 5):
                sim = Climber(seed)
                sim.tokens_on, sim.work = False, 3
                sim.route.next_crux = 15.0
                jug, low, roped = None, 1e9, False
                for _ in range(60 * 300):
                    sim.update(1 / 60)
                    if sim.actions[-1:] == ["crux_miss"]:
                        jug = jug or next(h for h in sim.holds if h.crux)
                        low, roped = min(low, sim.body.y), roped or sim.rope is not None
                    if sim.crux_log:
                        break
                self.assertTrue(roped, seed)
                self.assertTrue(jug.crux_from - 8.0 < low < jug.crux_from - 1.0, (seed, low))   # 4~7m 떨어진다
                self.assertEqual(sim.crux_log[0][0], 2, seed)
                self.assertEqual((sim.tally["fall"], sim.tally["crux"]), (1, 1), seed)   # 일지에는 로프 추락으로도 센다
                self.assertEqual(sim.missing, 0, seed)
            bare = Climber(3)
            bare.tokens_on, bare.work = False, 3
            bare.route.next_crux = 15.0
            for _ in range(60 * 300):
                bare.bolts.clear()   # 볼트가 없어 퀵드로를 하나도 못 걸었다
                bare.update(1 / 60)
                self.assertIsNone(bare.rope)
                if bare.crux_log:
                    break
            self.assertEqual(bare.crux_log[0][0], 2)
            self.assertEqual(bare.tally["fall"], 0)
            back = Climber(3)   # 다시 오를 때는 빠르게 (이미 오른 높이보다 1m 넘게 아래)
            back.tokens_on, back.work = False, 1
            run(back, 5.0)
            self.assertEqual(back.pace, 1)
            back.top_y = back.body.y + 3.0
            self.assertEqual(back.pace, 2)
            back.tokens_on, back.bank = True, 0.5
            self.assertEqual(back.pace, 2)
        finally:
            climbcore.CRUX_MISS = saved

    def test_retry_without_new_tokens_does_not_hang(self):
        """토큰을 조금씩 받아 크럭스 앞에서 기다렸다 뛰고, 놓쳐 떨어진 뒤로는 토큰이 안 와도 다시 올라와 넘는다
        (예전에는 크럭스 아래로 돌아와 쌓인 높이를 다시 따지다 한 프레임도 안 쓰고 같은 판단만 되풀이해 앱이 멈췄다)."""
        import signal

        def stuck(*_):
            raise AssertionError("update 가 끝나지 않는다")
        saved, old = climbcore.CRUX_MISS, signal.signal(signal.SIGALRM, stuck)
        climbcore.CRUX_MISS = 1.0
        try:
            for seed in (13, 2):
                sim = Climber(seed)
                sim.route.next_crux = 15.0
                fell = False
                for i in range(30 * 700):
                    if not fell and i % 60 == 0 and (sim.bank < 0.3 or sim.parked):
                        sim.deposit(meters(0.3))
                    signal.alarm(5)
                    sim.update(1 / 30)
                    signal.alarm(0)
                    fell = fell or sim.rope is not None
                    if sim.crux_log:
                        break
                self.assertTrue(fell, seed)
                self.assertEqual(sim.crux_log[0][0], 2, seed)
                self.assertGreater(sim.bank, -1.0, seed)   # 다시 뛴 높이는 처음 뛸 때 썼다
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
            climbcore.CRUX_MISS = saved

    def test_climb_back_pace_only_on_the_wall(self):
        sim = Climber(3)
        sim.tokens_on, sim.work = False, 1
        run(sim, 5.0)
        sim.top_y = sim.body.y + 3.0
        self.assertEqual((sim.pace, sim.steady_pace()), (2, 1))   # 다이노 섞기는 steady_pace 로
        sim.grounded = True   # 땅·달에 앉아 있거나 벽 끝에서 떨어지는 동안은 빨라지지 않는다
        self.assertEqual(sim.pace, 1)
        sim.grounded, sim.falling = False, True
        self.assertEqual(sim.pace, 1)
        sim.falling, sim.work = False, 0   # 쉴 때는 쉰다
        self.assertEqual(sim.pace, 0)

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


class WallTest(unittest.TestCase):
    """벽의 끝 (주간 사용량): 빌레이 줄이 끊어져 화면 아래로 떨어진 뒤 새 벽 바닥 (0m) 에서 다시 시작한다."""

    def finish(self, sim, seconds=15.0):
        seen = {"cut": False, "alts": [], "track": 0.0, "times": []}
        t = 0.0
        while t < seconds:
            run(sim, 0.05)
            t += 0.05
            seen["cut"] |= sim.rope_cut is not None
            if sim.falling:
                seen["times"].append(sim.t)
                seen["alts"].append(sim.altitude())
                seen["track"] = max(seen["track"], abs(sim.cam - sim.body.y))
            if not sim.wall_pending and not sim.falling and sim.banner is not None:
                break
        return seen

    def check_fall(self, seen, height):
        alts, times = seen["alts"], seen["times"]
        dur = times[-1] - times[0]
        self.assertTrue(3.0 <= dur <= 7.5 if height > 15 else dur <= 3.0, dur)   # 높으면 4~7초, 낮으면 짧게
        self.assertTrue(all(b <= a + 0.05 for a, b in zip(alts, alts[1:])))   # 높이는 줄기만 한다
        tail = [a for a in alts if a < 11.0]   # 마지막 12m 는 떨어지는 빠르기대로 (땅이 아래에서 올라온다)
        floor = climbcore.GROUND + climbcore.BODY   # 땅에 앉은 높이: 낮은 데서 떨어지면 남은 시간 동안 여기 머문다 (그 샘플은 뺀다)
        while len(tail) > 1 and abs(tail[-1] - floor) < 1e-6 and abs(tail[-2] - floor) < 1e-6:
            tail.pop()
        if len(tail) > 3:
            steps = [(a - b) / 0.0667 for a, b in zip(tail, tail[1:-1])]
            self.assertTrue(all(5.0 < v < 11.0 for v in steps), steps)
        self.assertLess(alts[-1], 0.0)   # 땅까지
        self.assertLess(seen["track"], 2.5)   # 카메라가 몸을 따라간다

    def check_new_wall(self, sim, grounded=True):
        self.assertFalse(sim.wall_pending)
        self.assertLess(sim.shown_altitude(), 1.0)
        self.assertLess(sim.peak, 1.0)   # 기록을 새로 센다
        self.assertEqual((sim.passed, sim.bank), (0, 0.0))
        self.assertIn("새 벽", sim.banner[0])
        self.assertTrue(all(h.p.y < 20 for h in sim.holds))
        self.assertEqual(sim.grounded, grounded)
        if grounded:   # 땅에 주저앉아 있다
            self.assertAlmostEqual(sim.body.y, sim.ground_y() + climbcore.BODY, delta=0.05)
            self.assertTrue(all(l.mode == "follow" for l in sim.limbs.values()))
        else:
            self.assertTrue(all(l.mode == "grip" for l in sim.limbs.values()) or sim.sleeping or sim.ledge is not None)

    def check_climbs_again(self, sim):
        sim.rise()
        run(sim, 6.0)
        self.assertFalse(sim.grounded)
        self.assertIn("get_up", sim.actions)
        run(sim, 60.0)   # 새 벽을 다시 오른다
        self.assertGreater(sim.altitude(), 5.0)
        self.assertEqual(sim.missing, 0)

    def test_rope_snaps_falls_to_the_ground_and_climbs_again_later(self):
        sim = Climber(5)
        sim.tokens_on, sim.work = False, 1
        run(sim, 65.0)
        self.assertGreater(sim.altitude(), 5.0)
        sim.end_wall()
        self.assertEqual(sim.snapshot()["height"], 0.0)   # 떨어지는 장면 전에 꺼져도 다음에는 새 벽
        height = sim.altitude()
        seen = self.finish(sim)
        self.assertTrue(seen["cut"])
        self.check_fall(seen, height)
        self.assertIn("wall_end", sim.actions)
        self.check_new_wall(sim)
        run(sim, 30.0)   # 주간 사용량이 돌아오기 전에는 땅에서 기다린다 (일하는 펫이 있어도)
        self.assertTrue(sim.grounded)
        self.assertAlmostEqual(sim.body.y, sim.ground_y() + climbcore.BODY, delta=0.05)
        for name in ("chalk", "dyno", "fall", "wake"):   # 다른 동작은 받지 않는다
            sim.request(name)
        self.assertEqual(sim.requests, [])
        self.check_climbs_again(sim)

    def test_waits_on_the_ground_across_restore(self):
        sim = Climber(5)
        sim.tokens_on, sim.work = False, 1
        run(sim, 30.0)
        sim.end_wall(animate=False, locked=True)   # 창이 숨어 있으면 바로 땅에
        self.check_new_wall(sim)
        saved = sim.snapshot()
        self.assertTrue(saved["grounded"])
        again = Climber(6)
        again.tokens_on, again.work = False, 1
        again.restore(saved)
        self.assertTrue(again.grounded)
        run(again, 20.0)
        self.assertTrue(again.grounded)
        self.assertFalse(again.has_climb())
        self.check_climbs_again(again)

    def test_wall_ends_while_sleeping_and_dozes_on_the_ground(self):
        sim = Climber(7)
        sim.hp = 0
        sim.signal("exhaust")
        run(sim, 15.0)
        self.assertTrue(sim.sleeping)
        sim.end_wall()
        self.finish(sim)
        self.check_new_wall(sim)
        run(sim, 10.0)
        self.assertTrue(sim.grounded and sim.lids > 0.8)   # 5시간 사용량도 다 썼으면 땅에서 존다
        sim.rise()
        run(sim, 20.0)
        self.assertFalse(sim.grounded)
        self.assertTrue(sim.sleeping)   # 일어나 벽에 붙은 뒤 포타레지에서 잔다

    def test_wall_ends_hidden_while_hanging_on_the_rope(self):
        """로프에 매달린 동안 (창이 숨어) 벽이 끝나 땅에 앉아도, 버린 로프 추락 동작이 앉은 몸을 스프링으로 띄우지 않는다."""
        sim = Climber(3)
        sim.tokens_on, sim.work = False, 1
        run(sim, 20.0)
        sim.request("fall")
        for _ in range(600):
            if sim.rope is not None:
                break
            run(sim, DT)
        self.assertIsNotNone(sim.rope)
        sim.end_wall(animate=False, locked=True)
        run(sim, 2.0)
        self.check_new_wall(sim)
        self.assertEqual(sim.mode, "script")

    def test_hidden_or_without_events(self):
        sim = Climber(3)
        sim.tokens_on, sim.work = False, 1
        run(sim, 30.0)
        sim.end_wall(animate=False, locked=False)   # 숨어 있는 동안 주간 리셋: 바로 새 벽에 붙어서
        self.check_new_wall(sim, grounded=False)
        sim = Climber(3)
        sim.tokens_on, sim.work, sim.events = False, 1, False
        run(sim, 30.0)
        height = sim.altitude()
        sim.end_wall()
        seen = self.finish(sim)
        self.assertFalse(seen["cut"])   # 추락 이벤트를 끄면 줄은 안 보이고 떨어지기만
        self.check_fall(seen, height)
        self.check_new_wall(sim)

    def test_falls_from_a_high_wall_through_the_bands(self):
        sim = Climber(9)
        sim.tokens_on, sim.work = False, 1
        sim.jump(5000.0, 5000.0)
        run(sim, 20.0)
        height = sim.altitude()
        sim.end_wall()
        seen = self.finish(sim)
        self.check_fall(seen, height)
        self.assertGreater(seen["alts"][0], 4500)
        self.assertTrue(any(300 < a < 2000 for a in seen["alts"]))   # 언덕 구간도 지난다
        self.check_new_wall(sim)


class ScaleTest(unittest.TestCase):
    """높이 = 주간 사용량의 가속 눈금: % 사이는 토큰으로 채우고, 한 주 안에서는 내려가지 않고, 떨어질 때도 눈금을 따른다."""

    def climber(self, pct=None, per=1000.0, seed=4):
        sim = Climber(seed)
        sim.tokens_on, sim.work = False, 1
        if pct is not None:
            sim.set_week(pct, per)
        return sim

    def test_scale_values(self):
        H = climbcore.week_height
        self.assertEqual(H(0), 0.0)
        self.assertAlmostEqual(H(10), 110, delta=2)
        self.assertAlmostEqual(H(35.3), 8849, delta=8849 * 0.02)   # 에베레스트
        self.assertAlmostEqual(H(50), 100_000, delta=10)
        self.assertAlmostEqual(H(80), 14_155_000, delta=10_000)
        self.assertEqual(H(100), climbcore.MOON)
        self.assertEqual(H(120), climbcore.MOON)
        for pct in (0.5, 37.2, 99.9):
            self.assertAlmostEqual(climbcore.height_pct(H(pct)), pct)
        pcts = [climbcore.height_pct(m[0]) for m in climbcore.MILESTONES]
        self.assertEqual(pcts, sorted(pcts))
        self.assertAlmostEqual(pcts[-1], 100.0)

    def test_follows_the_week_and_tokens_fill_between(self):
        sim = self.climber(38)
        run(sim, 4.0)
        H = climbcore.week_height
        self.assertAlmostEqual(sim.altitude(), H(38), delta=H(38) * 0.01)
        self.assertEqual(sim.shown_altitude(), sim.altitude())
        sim.deposit(500)   # 1% 에 1,000 토큰: 반 % 더
        run(sim, 4.0)
        self.assertAlmostEqual(sim.altitude(), H(38.5), delta=H(38.5) * 0.01)
        sim.deposit(2000)   # 다음 % 는 넘지 않는다
        run(sim, 4.0)
        self.assertGreater(sim.altitude(), H(38.9))
        self.assertLess(sim.altitude(), H(39))
        sim.set_week(39)   # 계정 % 가 오르면 그 % 에 토큰이 들어 있으니 처음부터
        self.assertEqual(sim.week_tokens, 0.0)
        run(sim, 2.0)
        self.assertAlmostEqual(sim.altitude(), H(39), delta=H(39) * 0.01)
        sim.set_week(41)
        run(sim, 4.0)
        self.assertAlmostEqual(sim.altitude(), H(41), delta=H(41) * 0.01)
        sim.pct_shown = 45.0   # 보이는 % 가 더 높으면 (한 주 안에서) 내려가지 않는다
        sim.set_week(42)
        run(sim, 2.0)
        self.assertEqual(sim.pct_shown, 45.0)

    def test_scale_moves_milestones_and_scenery_not_the_ground(self):
        sim = self.climber(34)
        run(sim, 5.0)
        ground, passed = sim.ground_y(), sim.passed
        self.assertEqual(climbcore.MILESTONES[passed][1], "에베레스트")
        sim.set_week(36)
        run(sim, 5.0)
        self.assertIn(8849, sim.banners)
        self.assertGreater(sim.passed, passed)
        self.assertEqual(sim.ground_y(), ground)   # 땅은 벽 아래 그대로 (높이 숫자를 따라오지 않는다)
        before = sim.altitude()
        sim.request("fall")   # 로프 추락: 몸은 떨어져도 높이 숫자는 그대로
        run(sim, 3.0)
        self.assertIn("fall", sim.actions)
        self.assertGreaterEqual(sim.altitude(), before)
        self.assertEqual(sim.missing, 0)

    def test_first_reading_restore_and_new_week(self):
        H = climbcore.week_height
        old = self.climber()
        old.restore({"height": 5061.0, "peak": 5061.0})   # 예전 (바위 높이) 기록
        self.assertAlmostEqual(old.altitude(), 5061.0, delta=0.01)   # 주간 % 를 받기 전에는 그대로
        old.set_week(38, 1000.0)
        self.assertAlmostEqual(old.altitude(), 5061.0, delta=1.0)   # 거기서부터 따라붙는다
        run(old, 5.0)
        self.assertAlmostEqual(old.altitude(), H(38), delta=H(38) * 0.02)
        high = self.climber()
        high.restore({"height": 40_000.0, "peak": 40_000.0})
        high.set_week(38)   # 처음 받은 % 가 낮으면 거기에 맞춘다
        self.assertAlmostEqual(high.altitude(), H(38), delta=1.0)
        saved = high.snapshot()
        self.assertAlmostEqual(saved["height"], H(38), delta=1.0)
        again = self.climber()
        again.restore(saved)
        again.set_week(38)
        self.assertAlmostEqual(again.altitude(), H(38), delta=1.0)
        again.deposit(300)
        again.end_wall(animate=False, locked=False)   # 주간 리셋: 새 벽 0m 부터
        again.set_week(0)
        self.assertEqual(again.week_tokens, 0.0)
        run(again, 2.0)
        self.assertLess(again.altitude(), 1.0)
        unknown = self.climber()
        unknown.set_week(None)   # 주간 % 를 모르면 오른 바위 높이
        run(unknown, 20.0)
        self.assertFalse(unknown.scaled())
        self.assertAlmostEqual(unknown.altitude(), unknown.alt0 + unknown.body.y)

    def test_backlog_upgrade_and_catch_up(self):
        H = climbcore.week_height
        sim = self.climber()
        sim.deposit(5000)   # 꺼져 있던 동안 쓴 토큰 (계정 % 에 이미 들어 있다)
        sim.set_week(38, 1000.0)
        run(sim, 30.0)
        self.assertLess(sim.altitude(), H(38) * 1.001)
        self.assertLessEqual(len(sim.banners), 2)   # 0m 에서 멀리 따라붙는 동안은 기록을 조용히 넘는다
        old = self.climber()
        old.restore({"height": 8000.0, "peak": 8000.0})   # 예전 기록이 계정 % 보다 높다
        old.set_week(20, 1000.0)
        self.assertAlmostEqual(old.altitude(), H(20), delta=1.0)
        self.assertLessEqual(old.peak, H(20) + 1.0)   # 최고 높이와 넘은 기록도 맞춘다
        self.assertEqual(old.passed, sum(1 for m in climbcore.MILESTONES if m[0] <= old.peak))
        for pct in range(21, 37):   # 계정 % 는 1% 씩 오른다
            old.set_week(pct)
            run(old, 1.5)
        self.assertIn(1947, old.banners)   # 다시 넘는 기록은 말풍선이 뜬다
        self.assertIn(8849, old.banners)

    def test_new_week_without_wall_end_and_ground_after_restore(self):
        H = climbcore.week_height
        sim = self.climber(70)
        run(sim, 5.0)
        sim.set_week(1)   # 벽이 끝나지 않은 채 주가 바뀌었다 (지난주 높이에 머물지 않는다)
        run(sim, 1.0)
        self.assertLess(sim.altitude(), H(1.5))
        self.assertLess(sim.peak, H(1.5))
        pending = self.climber(70)
        run(pending, 5.0)
        pending.end_wall()   # 떨어지는 장면을 기다리는 동안은 지난주 높이에서 떨어진다
        pending.set_week(0)
        self.assertAlmostEqual(pending.altitude(), H(70), delta=H(70) * 0.01)
        low = self.climber(0.3, per=1e9)
        run(low, 120.0)   # 주간 0.3% 에서 바위를 한참 올랐다
        rock = low.body.y - low.ground_y()
        self.assertGreater(rock, 10.0)
        again = self.climber()
        again.restore(low.snapshot())
        again.set_week(0.3, 1e9)
        self.assertAlmostEqual(again.body.y - again.ground_y(), rock, delta=1.5)   # 땅은 다시 켜도 벽 아래에

    def test_fall_follows_the_scale(self):
        H = climbcore.week_height
        sim = self.climber(60, seed=9)
        sim.pct_shown = 60.0
        run(sim, 2.0)
        sim.end_wall()
        alts, t = [], 0.0
        while (sim.wall_pending or sim.falling) and t < 30:
            sim.update(DT)
            t += DT
            if sim.falling:
                alts.append(sim.altitude())
        self.assertAlmostEqual(alts[0], H(60), delta=H(60) * 0.02)   # 가속 눈금 높이에서 이어서
        self.assertTrue(all(b <= a + 0.05 for a, b in zip(alts, alts[1:])))
        bands = {"strato": (12_000, 100_000), "mountains": (2000, 9500), "hills": (300, 2000)}
        for name, (low, high) in bands.items():   # 오를 때 % 로 지나간 비율대로 지나간다
            spent = sum(DT for a in alts if low <= a < high)
            width = climbcore.height_pct(high) - climbcore.height_pct(low)
            self.assertAlmostEqual(spent, width / climbcore.FALL_PCT, delta=0.15, msg=name)
        tail = [(a - b) / DT for a, b in zip(alts, alts[1:]) if -1.3 < b and a < 11]
        self.assertTrue(tail and all(abs(v - climbcore.FALL_SPEED) < 0.5 for v in tail), tail)   # 땅이 올라오는 동안은 떨어지는 빠르기
        self.assertLess(len(alts) * DT, 9.0)
        self.assertTrue(sim.grounded)


class RecordTest(unittest.TestCase):
    """지난 주들에 오른 높이: 랜드마크에 섞여 다음 목표가 되고, 닿으면 말풍선과 바위에 새긴 자국."""

    def climber(self, pct, seed=4):
        sim = Climber(seed)
        sim.tokens_on, sim.work = False, 1
        sim.set_week(pct, 1000.0)
        run(sim, 4.0)
        return sim

    def test_records_mix_into_landmarks(self):
        H = climbcore.week_height
        sim = Climber(4)
        sim.set_records([("지난주", H(40)), ("2주 전", H(30)), ("3주 전", H(40)), ("4주 전", climbcore.MOON),
                         ("5주 전", 0.0)])
        self.assertEqual([m[1] for m in sim.records], ["2주 전 기록", "지난주·3주 전 기록"])   # 같은 높이는 묶고 달·0 은 빼고
        self.assertEqual(sim.records[1][2:], ("지난주·3주 전 기록(19.2km)을 따라잡았어요!", ("지난주·3주 전", "19.2km")))
        heights = [m[0] for m in sim.landmarks]
        self.assertEqual(heights, sorted(heights))
        self.assertEqual(len(sim.landmarks), len(climbcore.MILESTONES) + 2)
        sim.set_records([])
        self.assertEqual(sim.landmarks, climbcore.MILESTONES)

    def test_reaching_a_record(self):
        H = climbcore.week_height
        sim = self.climber(39)
        sim.set_records([("지난주", H(40))])
        self.assertEqual(sim.next_milestone()[1], "지난주 기록")   # 높이 상자의 '다음:'
        self.assertEqual(sim.carved, [])
        sim.set_week(40)
        run(sim, 2.0)
        self.assertIn(H(40), sim.banners)
        self.assertEqual(len(sim.carved), 1)
        y, side, name, alt = sim.carved[0]
        self.assertEqual((name, alt), ("지난주", "19.2km"))
        self.assertLess(abs(y - sim.body.y), 1.0)   # 닿은 자리 바위에
        self.assertIn(side, (-1, 1))
        sim.end_wall(animate=False, locked=False)   # 새 벽이면 지운다
        self.assertEqual(sim.carved, [])

    def test_quiet_records(self):
        H = climbcore.week_height
        high = self.climber(45)
        high.set_records([("지난주", H(40))])   # 이미 넘은 높이: 말풍선 없이 넘은 걸로
        run(high, 2.0)
        self.assertNotIn(H(40), high.banners)
        self.assertEqual(high.passed, sum(1 for m in high.landmarks if m[0] <= high.peak))
        self.assertNotEqual(high.next_milestone()[1], "지난주 기록")
        far = self.climber(10)
        far.set_records([("지난주", H(20))])
        far.set_week(30)   # 멀리서 따라붙는 동안은 기존 랜드마크처럼 조용히
        run(far, 8.0)
        self.assertNotIn(H(20), far.banners)
        self.assertEqual(far.carved, [])
        moon = self.climber(99)
        moon.set_records([("지난주", H(60))])
        moon.reach_moon(animate=False)
        self.assertIsNone(moon.next_milestone())

    def test_restart_and_new_week(self):
        H = climbcore.week_height
        for pct in (6, 7, 11, 14, 51):   # 기록 파일의 0.01m 반올림으로 최고 높이가 기록보다 조금 낮아지는 %
            sim = self.climber(pct - 1)
            sim.set_records([("지난주", H(pct))])
            sim.set_week(pct)
            run(sim, 2.0)
            self.assertEqual(sim.banners.count(H(pct)), 1, pct)
            again = Climber(5)
            again.tokens_on, again.work = False, 1
            again.restore(json.loads(json.dumps(sim.snapshot())))
            again.set_week(pct, 1000.0)
            again.set_records([("지난주", H(pct))])
            run(again, 2.0)
            self.assertNotIn(H(pct), again.banners, pct)   # 다시 켜도 또 따라잡지 않는다
            self.assertEqual(again.carved, [])
        sim.rebase(10.0)   # 벽이 끝나지 않은 채 새 주: 다시 따라잡을 기록의 자국은 지운다
        self.assertEqual(sim.carved, [])

    def test_same_height_as_a_milestone_and_close_marks(self):
        H = climbcore.week_height
        sim = self.climber(49)
        sim.set_records([("지난주", H(50))])   # 카르만 선 (100km) 과 같은 높이
        self.assertEqual(sim.next_milestone()[1], "지난주 기록")
        sim.set_week(50)
        run(sim, 2.0)
        self.assertEqual(sim.banners[-2:], [H(50), 100000])
        self.assertTrue(sim.banner[0].startswith("지난주 기록"))   # 같은 때 넘으면 지난 주 기록 말풍선을 남긴다
        close = self.climber(30)
        close.set_records([("지난주", H(31)), ("2주 전", H(31.5))])
        close.set_week(32)   # 한꺼번에 넘어 같은 자리에 새긴다
        run(close, 3.0)
        self.assertEqual(len(close.carved), 2)
        self.assertLess(abs(close.carved[0][0] - close.carved[1][0]), climbcore.CARVE_GAP)
        self.assertNotEqual(close.carved[0][1], close.carved[1][1])   # 글자는 서로 다른 쪽


class MoonTest(unittest.TestCase):
    """주간 사용량 100%: 기둥 위 끝 달에 올라서 깃발을 꽂고 리셋까지 쉬다가, 리셋이면 뛰어내려 지구로 떨어진다."""

    def at_moon(self, seed=5, sleep=False):
        sim = Climber(seed)
        sim.tokens_on, sim.work = False, 1
        sim.set_week(99, 1000.0, 1_792_623_600)
        sim.pct_shown = 99.0
        run(sim, 20.0)
        if sleep:
            sim.hp = 0
            sim.signal("exhaust")
            run(sim, 15.0)
            self.assertTrue(sim.sleeping)
        sim.set_week(100)
        sim.reach_moon()
        self.assertTrue(sim.snapshot()["moon"])   # 올라서는 장면 전에 꺼져도 다음에는 달 위
        run(sim, 14.0)
        return sim

    def check_on_moon(self, sim):
        self.assertTrue(sim.grounded)
        self.assertIsNotNone(sim.moon)
        self.assertAlmostEqual(sim.body.y, sim.moon + climbcore.BODY, delta=0.05)   # 달 표면에 앉아 있다
        self.assertIsNotNone(sim.moon_flag)
        self.assertEqual(sim.altitude(), climbcore.MOON)
        self.assertEqual(sim.shown_altitude(), climbcore.MOON)
        self.assertFalse(any(h.p.y > sim.moon for h in sim.holds))   # 표면 위로는 홀드가 없다
        self.assertEqual(sim.state_label(), "달")

    def test_tops_out_plants_flag_and_waits(self):
        sim = self.at_moon()
        self.assertIn("moon_top", sim.actions)
        self.check_on_moon(sim)
        self.assertIn(climbcore.MOON, sim.banners)   # 달에 도착했어요!
        self.assertEqual(sim.missing, 0)
        for name in ("chalk", "dyno", "fall", "exhaust"):   # 다른 동작은 받지 않는다
            sim.request(name)
        self.assertEqual(sim.requests, [])
        holds = len(sim.holds)
        run(sim, 30.0)   # 리셋까지 달 위에서 (위로 홀드를 더 깔지 않는다)
        self.check_on_moon(sim)
        self.assertEqual(len(sim.holds), holds)
        sim.hp = 0
        run(sim, 8.0)
        self.assertGreater(sim.lids, 0.8)   # 5시간 사용량도 다 썼으면 존다
        saved = sim.snapshot()
        self.assertEqual((saved["moon"], saved["height"]), (True, climbcore.MOON))
        again = Climber(8)
        again.tokens_on, again.work = False, 1
        again.restore(saved)
        self.check_on_moon(again)
        again.set_week(100)   # 다시 켜도 달 위 (내려오지 않는다)
        run(again, 5.0)
        self.check_on_moon(again)

    def test_moon_surface_is_not_below_the_hands(self):
        """달 표면은 잡고 있던 손 홀드보다 낮지 않다 (손이 표면 위 허공의 안 보이는 홀드를 쥐고 있지 않게. 팔이 길어
        매달린 몸이 손에서 멀어도)."""
        for seed in (1, 3, 5, 10):
            sim = Climber(seed)
            sim.tokens_on, sim.work = False, 1
            sim.set_week(99, 1000.0, 1_792_623_600)
            run(sim, 20.0)
            sim.set_week(100)
            sim.reach_moon()
            while "moon_top" not in sim.actions:
                run(sim, DT)
            top = max(sim.held_y(sim.limbs[n]) for n in ("LH", "RH"))
            self.assertLessEqual(top, sim.moon + 1e-6, seed)
            self.assertLessEqual(sim.moon - top, 0.8 + 1e-6, seed)

    def test_from_the_portaledge_and_hidden(self):
        sim = self.at_moon(seed=7, sleep=True)   # 자다가 100%: 깨어 포타레지를 접고 오른다
        self.assertIn("wake", sim.actions)
        self.check_on_moon(sim)
        self.assertIsNone(sim.ledge)
        hidden = Climber(3)
        hidden.tokens_on, hidden.work = False, 1
        run(hidden, 20.0)
        hidden.reach_moon(animate=False)   # 창이 숨어 있으면 바로 달 위
        self.check_on_moon(hidden)
        hidden.leave_moon(animate=False)   # 숨어 있는 동안 리셋: 바로 새 벽에 붙는다
        self.assertFalse(hidden.grounded)
        self.assertIsNone(hidden.moon)
        self.assertIn("달까지", hidden.banner[0])

    def test_hidden_mid_action_restart_and_reach(self):
        for setup in ("dyno", "rest"):   # 하던 동작 (다이노, 오래 쉬다 낮잠) 은 버리고 달 위에서 처음부터
            sim = Climber(11)
            sim.tokens_on, sim.work = False, (0 if setup == "rest" else 1)
            run(sim, 15.0)
            if setup == "dyno":
                sim.request("dyno")
                run(sim, 0.3)
            sim.reach_moon(animate=False)
            run(sim, 200.0)
            self.check_on_moon(sim)
            self.assertFalse(sim.sleeping, setup)
            self.assertIsNone(sim.ledge, setup)
            self.assertTrue(all(l.mode == "follow" for l in sim.limbs.values()), setup)
        again = Climber(8)
        again.tokens_on, again.work = False, 1
        again.restore(sim.snapshot())
        again.set_week(99.7, 1000.0)   # 다시 켜면: 달 위면 처음 받은 % 에 맞추지 않는다 (도착 말풍선이 다시 뜨지 않게)
        run(again, 5.0)
        self.check_on_moon(again)
        self.assertEqual(again.banners, [])
        reach = Climber(13)   # 표면은 매달린 몸에서 손이 닿는 높이에
        reach.tokens_on, reach.work = False, 1
        run(reach, 20.0)
        reach.reach_moon()
        while reach.moon is None:
            reach.update(DT)
        self.assertLess(reach.moon - reach.body.y, climbcore.SHOULDER[1] + sum(climbcore.ARM))   # 팔이 닿는 거리 안

    def test_leaves_the_moon_falls_home_and_climbs_again(self):
        sim = self.at_moon(seed=9)
        sim.leave_moon()
        self.assertTrue(sim.snapshot()["grounded"])   # 내려오는 장면 전에 꺼지면 다음에는 땅에서
        self.assertNotIn("moon", sim.snapshot())
        sim.set_week(0)   # 새 주 (떠나는 장면을 기다리는 동안은 높이를 그대로)
        alts, t = [], 0.0
        while (sim.wall_pending or sim.falling) and t < 30:
            sim.update(DT)
            t += DT
            if sim.falling:
                alts.append(sim.altitude())
        self.assertIn("moon_leave", sim.actions)
        self.assertAlmostEqual(alts[0], climbcore.MOON, delta=climbcore.MOON * 0.05)
        self.assertTrue(all(b <= a + 0.05 for a, b in zip(alts, alts[1:])))
        self.assertLess(alts[-1], 0.0)
        self.assertLess(t, 16.0)
        self.assertTrue(sim.grounded)
        self.assertIsNone(sim.moon)
        self.assertIn("달까지", sim.banner[0])   # 새 벽! 지난주는 달까지 완등
        sim.rise()
        run(sim, 40.0)
        self.assertFalse(sim.grounded)
        self.assertEqual(sim.missing, 0)
        self.assertLess(sim.altitude(), climbcore.week_height(1.0))   # 새 주 0% 부터


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
