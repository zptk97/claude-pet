import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import petcore  # noqa: E402

NOW = 10_000_000_000  # ms


def session(state, age_ms=0, since_ago_ms=None, cwd="/home/u/rc_sw", tool="", created=None):
    ts = NOW - age_ms
    since = NOW - since_ago_ms if since_ago_ms is not None else ts
    return {"state": state, "ts": ts, "since": since, "cwd": cwd, "tool": tool,
            "created": created if created is not None else ts}


class AtlasTest(unittest.TestCase):
    def test_row_frame_counts_match_codex_spec(self):
        counts = [len(petcore.ANIMATIONS[name][1]) for name in sorted(
            petcore.ANIMATIONS, key=lambda n: petcore.ANIMATIONS[n][0])]
        self.assertEqual(counts, [6, 8, 8, 4, 5, 8, 6, 6, 6])
        self.assertEqual((petcore.ATLAS_W, petcore.ATLAS_H), (1536, 1872))

    def test_frame_rects(self):
        self.assertEqual(petcore.frame_rects("waving"), [
            (0, 624, 192, 208), (192, 624, 192, 208),
            (384, 624, 192, 208), (576, 624, 192, 208)])

    def test_every_state_has_animation(self):
        for animation in petcore.STATE_ANIMATION.values():
            self.assertIn(animation, petcore.ANIMATIONS)


class StateTest(unittest.TestCase):
    def test_stale_running_becomes_idle(self):
        s = session("running", age_ms=petcore.STALE_ACTIVE_MS + 1)
        self.assertEqual(petcore.effective_state(s, NOW), "idle")
        s = session("running", age_ms=petcore.STALE_ACTIVE_MS - 1)
        self.assertEqual(petcore.effective_state(s, NOW), "running")

    def test_expired_session_is_ignored(self):
        s = session("done", age_ms=petcore.EXPIRE_MS + 1)
        self.assertIsNone(petcore.effective_state(s, NOW))

    def test_live_session_does_not_expire(self):
        # PID 로 살아 있는지 확인하는 세션은 오래 조용해도 숨기지 않는다 (밤새 도는 실험)
        s = {**session("background", age_ms=petcore.EXPIRE_MS + 1), "claude_pid": "42"}
        self.assertEqual(petcore.effective_state(s, NOW), "background")
        s = {**session("idle", age_ms=petcore.EXPIRE_MS + 1), "claude_pid": "42"}
        self.assertEqual(petcore.effective_state(s, NOW), "idle")

    def test_background_is_not_stale_or_acked(self):
        s = session("background", age_ms=petcore.STALE_ACTIVE_MS + 1)
        self.assertEqual(petcore.effective_state(s, NOW), "background")
        self.assertEqual(petcore.effective_state(s, NOW, acked_ts=NOW), "background")

    def test_acked_done_and_error_become_idle(self):
        for state in ("done", "error"):
            s = session(state, age_ms=1000)
            self.assertEqual(petcore.effective_state(s, NOW, acked_ts=s["ts"]), "idle")
            # 확인한 뒤에 새로 끝난 작업은 다시 보여준다
            self.assertEqual(petcore.effective_state(s, NOW, acked_ts=s["ts"] - 1), state)

    def test_ack_does_not_hide_running(self):
        s = session("running")
        self.assertEqual(petcore.effective_state(s, NOW, acked_ts=NOW), "running")

    def test_views_keep_creation_order_and_include_idle(self):
        sessions = {
            "late": session("waiting", created=NOW - 10),
            "early": session("idle", created=NOW - 1000),
            "middle": session("running", created=NOW - 500, tool="Bash", since_ago_ms=5000),
            "gone": session("done", age_ms=petcore.EXPIRE_MS + 1),
        }
        self.assertEqual(petcore.session_views(sessions, NOW), [
            ("early", "idle", ""),
            ("middle", "running", "Bash 실행 중 · 5초"),
            ("late", "waiting", "확인이 필요해요!"),
        ])

    def test_views_apply_ack(self):
        sessions = {"a": session("done")}
        self.assertEqual(petcore.session_views(sessions, NOW, {"a": NOW})[0][1], "idle")


class AliveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proc = Path(self.tmp.name)
        # comm 에 공백과 ')' 가 있어도 시작 시각을 바르게 읽는지 본다
        fields = ["S"] + [str(i) for i in range(4, 22)] + ["777", "0"]
        (self.proc / "42").mkdir()
        (self.proc / "42/stat").write_text("42 (weird ) name) " + " ".join(fields) + "\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_alive_when_pid_and_start_match(self):
        s = {"claude_pid": "42", "claude_start": "777"}
        self.assertTrue(petcore.is_alive(s, self.proc))

    def test_dead_when_process_missing_or_reused(self):
        self.assertFalse(petcore.is_alive({"claude_pid": "43"}, self.proc))
        self.assertFalse(petcore.is_alive({"claude_pid": "42", "claude_start": "778"}, self.proc))

    def test_unknown_pid_counts_as_alive(self):
        self.assertTrue(petcore.is_alive({}, self.proc))
        self.assertTrue(petcore.is_alive({"claude_pid": ""}, self.proc))
        self.assertTrue(petcore.is_alive({"claude_pid": "42"}, self.proc))


class TextTest(unittest.TestCase):
    def test_format_elapsed(self):
        self.assertEqual(petcore.format_elapsed(12_400), "12초")
        self.assertEqual(petcore.format_elapsed(83_000), "1분 23초")
        self.assertEqual(petcore.format_elapsed(3_720_000), "1시간 2분")
        self.assertEqual(petcore.format_elapsed(-5), "0초")

    def test_status_texts(self):
        cases = [
            (session("running", since_ago_ms=83_000, tool="Bash"), "running", "Bash 실행 중 · 1분 23초"),
            (session("running", since_ago_ms=5_000), "running", "생각 중 · 5초"),
            (session("waiting"), "waiting", "확인이 필요해요!"),
            (session("done", since_ago_ms=133_000), "done", "다 했어요! (2분 13초)"),
            (session("error"), "error", "오류로 멈췄어요"),
            (session("idle"), "idle", ""),
        ]
        for s, state, expected in cases:
            self.assertEqual(petcore.status_text(s, state, NOW), expected)

    def test_background_texts(self):
        def bg(tasks, desc="", crons=0):
            return {**session("background", since_ago_ms=754_000),
                    "bg_tasks": tasks, "bg_desc": desc, "crons": crons}
        cases = [
            (bg(1, "Run hybrid attempt 3"), "백그라운드: Run hybrid attempt 3 · 12분 34초"),
            (bg(1, "Run hybrid attempt 3 conversion for 224 tile"),
             "백그라운드: Run hybrid attempt 3 co… · 12분 34초"),
            (bg(3, "watch  log"), "백그라운드 3개: watch log 외 · 12분 34초"),
            (bg(2), "백그라운드 작업 2개 · 12분 34초"),
            (bg(0, crons=1), "깨우기 예약됨 · 12분 34초"),
            (bg("?", crons="?"), "백그라운드 대기 · 12분 34초"),
            (session("background", since_ago_ms=754_000), "백그라운드 대기 · 12분 34초"),
        ]
        for s, expected in cases:
            self.assertEqual(petcore.status_text(s, "background", NOW), expected)

    def test_shorten(self):
        self.assertEqual(petcore.shorten("a\n b", 10), "a b")
        self.assertEqual(petcore.shorten("abcdef", 4), "abc…")
        self.assertEqual(petcore.shorten(None, 4), "")

    def test_session_name_fallback(self):
        self.assertEqual(petcore.session_name({"cwd": "/"}), "Claude")
        self.assertEqual(petcore.session_name({"cwd": "/a/b/"}), "b")

    def test_clean_title(self):
        self.assertEqual(petcore.clean_title("◐ GPT 또는 Codex 펫"), "GPT 또는 Codex 펫")
        self.assertEqual(petcore.clean_title("✳ 오늘 daily 정리"), "오늘 daily 정리")
        self.assertEqual(petcore.clean_title("⠂ 3D 맵"), "3D 맵")
        self.assertEqual(petcore.clean_title("Claude Code"), "Claude Code")
        self.assertEqual(petcore.clean_title(None), "")


class AssignTest(unittest.TestCase):
    PETS = ["cat", "dog", "fox"]

    def test_new_sessions_get_distinct_pets(self):
        result = petcore.assign_pets(["a", "b", "c"], self.PETS, {})
        self.assertEqual(sorted(result.values()), self.PETS)

    def test_existing_assignment_is_kept(self):
        result = petcore.assign_pets(["a", "b"], self.PETS, {"a": "fox", "b": "fox"})
        self.assertEqual(result, {"a": "fox", "b": "fox"})

    def test_missing_pet_is_reassigned(self):
        result = petcore.assign_pets(["a"], self.PETS, {"a": "deleted-pet"})
        self.assertIn(result["a"], self.PETS)

    def test_more_sessions_than_pets_reuses_evenly(self):
        sids = [f"s{i}" for i in range(7)]
        counts = {}
        for pet in petcore.assign_pets(sids, self.PETS, {}).values():
            counts[pet] = counts.get(pet, 0) + 1
        self.assertEqual(sorted(counts.values()), [2, 2, 3])

    def test_enabled_pet_ids(self):
        self.assertEqual(petcore.enabled_pet_ids(self.PETS, ["dog", "deleted-pet"]), ["cat", "fox"])
        self.assertEqual(petcore.enabled_pet_ids(self.PETS, None), self.PETS)
        self.assertEqual(petcore.enabled_pet_ids(self.PETS, self.PETS), self.PETS)  # 모두 꺼지면 전체
        self.assertEqual(petcore.enabled_pet_ids([], ["cat"]), [])

    def test_disabled_pet_is_reassigned(self):
        enabled = petcore.enabled_pet_ids(self.PETS, ["fox"])
        result = petcore.assign_pets(["a", "b"], enabled, {"a": "fox", "b": "cat"})
        self.assertEqual(result, {"a": "dog", "b": "cat"})

    def test_deterministic_and_empty(self):
        self.assertEqual(petcore.assign_pets(["x", "y"], self.PETS, {}),
                         petcore.assign_pets(["x", "y"], self.PETS, {}))
        self.assertEqual(petcore.assign_pets(["x"], [], {}), {})


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make_pet(self, folder, meta, sprite="spritesheet.webp"):
        d = self.root / folder
        d.mkdir(parents=True)
        (d / "pet.json").write_text(meta if isinstance(meta, str) else json.dumps(meta))
        if sprite:
            (d / sprite).write_bytes(b"x")
        return d

    def test_find_pets_validates_folders(self):
        self.make_pet("pets/cat", {"id": "cat", "displayName": "Cat"})
        self.make_pet("pets/broken", "{not json")
        self.make_pet("pets/nosprite", {"id": "nosprite"}, sprite=None)
        self.make_pet("pets/escape", {"id": "escape", "spritesheetPath": "../cat/spritesheet.webp"})
        pets = petcore.find_pets([self.root / "pets", self.root / "missing"])
        self.assertEqual([p["id"] for p in pets], ["cat"])
        self.assertEqual(pets[0]["name"], "Cat")

    def test_find_pets_first_dir_wins(self):
        self.make_pet("a/cat", {"id": "cat", "displayName": "First"})
        self.make_pet("b/cat", {"id": "cat", "displayName": "Second"})
        pets = petcore.find_pets([self.root / "a", self.root / "b"])
        self.assertEqual([p["name"] for p in pets], ["First"])

    def test_title_reader_follows_appends(self):
        path = self.root / "t.jsonl"
        reader = petcore.TitleReader()
        self.assertEqual(reader.title(str(path)), "")  # 아직 없는 파일
        path.write_text('{"type":"user"}\n{"type":"ai-title","aiTitle":"첫 제목"}\n{"type":"assi')
        self.assertEqual(reader.title(str(path)), "첫 제목")
        with path.open("a") as f:  # 반쯤 쓰인 줄이 이어서 완성된다
            f.write('stant"}\n{"type":"ai-title","aiTitle":"새 제목"}\nnot json "aiTitle"\n')
        self.assertEqual(reader.title(str(path)), "새 제목")
        path.write_text('{"aiTitle":"다시"}\n')  # 더 짧게 새로 쓰였다
        self.assertEqual(reader.title(str(path)), "다시")
        self.assertEqual(reader.title(""), "")

    def test_title_reader_reads_in_chunks(self):
        path = self.root / "big.jsonl"
        path.write_text("x" * 50 + "\n" + '{"aiTitle":"끝"}\n')
        reader = petcore.TitleReader()
        reader.CHUNK = 7  # 줄이 여러 조각으로 나뉘어도 맞게 읽는지
        self.assertEqual(reader.title(str(path)), "끝")

    def test_load_sessions_skips_bad_files(self):
        d = self.root / "state"
        d.mkdir()
        (d / "good.json").write_text(json.dumps({"state": "running", "ts": 5, "since": 1}))
        (d / "badjson.json").write_text("{")
        (d / "badstate.json").write_text(json.dumps({"state": "dancing", "ts": 5}))
        (d / "badts.json").write_text(json.dumps({"state": "done", "ts": "soon"}))
        (d / "note.txt").write_text("ignored")
        sessions = petcore.load_sessions(d)
        self.assertEqual(list(sessions), ["good"])
        self.assertEqual((sessions["good"]["since"], sessions["good"]["created"]), (1, 5))


class UsageTest(unittest.TestCase):
    # 2026-10-08(목) 12:00 KST
    NOW_S = 1791428400
    NOW_MS = NOW_S * 1000

    def setUp(self):
        self.old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Seoul"
        time.tzset()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        if self.old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old_tz
        time.tzset()
        self.tmp.cleanup()

    def usage(self, ts, **values):
        return {"ts": ts, **dict.fromkeys(petcore.USAGE_FIELDS), **values}

    def test_load_usage_skips_bad_files(self):
        (self.dir / "a.json").write_text(json.dumps(
            {"ts": 5, "context_pct": 62, "context_size": "big", "five_hour_pct": True}))
        (self.dir / "nots.json").write_text(json.dumps({"context_pct": 1}))
        (self.dir / "list.json").write_text("[1]")
        (self.dir / "broken.json").write_text("{")
        usage = petcore.load_usage(self.dir)
        self.assertEqual(list(usage), ["a"])
        self.assertEqual(usage["a"], self.usage(5, context_pct=62))
        self.assertEqual(petcore.load_usage(self.dir / "missing"), {})

    def test_account_limits_uses_latest_reading(self):
        usage = {
            "old": self.usage(1, five_hour_pct=10),
            "new": self.usage(3, seven_day_pct=40),
            "none": self.usage(9),  # 아직 API 응답이 없던 세션
        }
        self.assertEqual(petcore.account_limits(usage)["seven_day_pct"], 40)
        self.assertIsNone(petcore.account_limits({"none": self.usage(9)}))

    def test_formats(self):
        self.assertEqual(petcore.format_tokens(124_010), "124k")
        self.assertEqual(petcore.format_tokens(1_000_000), "1M")
        self.assertEqual(petcore.format_tokens(1_250_000), "1.2M")
        self.assertEqual(petcore.format_tokens(900), "900")
        self.assertEqual(petcore.format_percent(23.5), "24%")
        self.assertEqual(petcore.format_remaining(30_000), "1분")
        self.assertEqual(petcore.format_remaining(133 * 60_000), "2시간 13분")
        self.assertEqual(petcore.format_remaining(5580 * 60_000), "3일 21시간")

    def test_format_reset_today_and_later_day(self):
        self.assertEqual(petcore.format_reset(1791436380, self.NOW_MS), "14:13 리셋 (2시간 13분 뒤)")
        self.assertEqual(petcore.format_reset(1791763200, self.NOW_MS),
                         "10/12(월) 09:00 리셋 (3일 21시간 뒤)")

    def test_usage_rows(self):
        usage = self.usage(self.NOW_MS, context_pct=62, context_size=200_000, context_tokens=124_010)
        limits = self.usage(self.NOW_MS - 60_000, five_hour_pct=23.5, five_hour_reset=1791436380,
                            seven_day_pct=41, seven_day_reset=1791763200)
        rows, note = petcore.usage_rows(usage, limits, self.NOW_MS)
        self.assertEqual(rows, [
            ("컨텍스트", 62, "124k / 200k"),
            ("5시간", 23.5, "14:13 리셋 (2시간 13분 뒤)"),
            ("주간", 41, "10/12(월) 09:00 리셋 (3일 21시간 뒤)"),
        ])
        self.assertEqual(note, "")

    def test_usage_rows_reset_stale_and_missing(self):
        limits = self.usage(self.NOW_MS - 15 * 60_000, five_hour_pct=99, five_hour_reset=self.NOW_S - 1,
                            seven_day_pct=41)
        rows, note = petcore.usage_rows(None, limits, self.NOW_MS)
        self.assertEqual(rows, [("컨텍스트", None, "정보 없음"), ("5시간", None, "리셋됨"), ("주간", 41, "")])
        self.assertEqual(note, "계정 사용량은 15분 전 값")
        rows, note = petcore.usage_rows(self.usage(1, context_pct=5), None, self.NOW_MS)
        self.assertEqual(rows, [("컨텍스트", 5, ""), ("5시간", None, "정보 없음"), ("주간", None, "정보 없음")])
        self.assertEqual(note, "")


class FoldTest(unittest.TestCase):
    def fold(self, *rows):
        """rows: (sid, state, ts)"""
        sessions = {sid: session(state, age_ms=NOW - ts) for sid, state, ts in rows}
        views = [(sid, state, f"{sid}-bubble") for sid, state, _ in rows]
        return petcore.folded_view(views, sessions)

    def test_attention_priority_then_oldest(self):
        rows = [("a", "done", 1), ("b", "running", 2), ("c", "error", 5), ("d", "error", 3), ("e", "idle", 4)]
        self.assertEqual(self.fold(*rows), ("d", "error", "d-bubble", 2, ""))
        self.assertEqual(self.fold(*rows, ("f", "waiting", 9))[:2], ("f", "waiting"))
        self.assertEqual(self.fold(("a", "done", 1)), ("a", "done", "a-bubble", 0, ""))

    def test_summary_when_nothing_waits(self):
        rows = [("a", "idle", 1), ("b", "running", 2), ("c", "background", 3), ("d", "running", 4)]
        self.assertEqual(self.fold(*rows), ("a", "idle", "", 0, "작업 중 2 · 백그라운드 1"))
        self.assertEqual(self.fold(("a", "idle", 1)), ("a", "idle", "", 0, "모두 쉬는 중"))
        self.assertIsNone(petcore.folded_view([], {}))


if __name__ == "__main__":
    unittest.main()
