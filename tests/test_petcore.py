import json
import math
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


def entry_line(entry):
    """기록 파일 한 줄 (Claude Code 처럼 공백 없이 쓴다)."""
    return json.dumps(entry, ensure_ascii=False, separators=(",", ":"))


def said_line(text, sidechain=False):
    """기록 파일에서 Claude 가 남긴 말 한 줄."""
    return entry_line({"type": "assistant", "isSidechain": sidechain,
                       "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})


def prompt_line(text):
    """기록 파일에서 사용자가 보낸 프롬프트 한 줄."""
    return entry_line({"type": "user", "message": {"role": "user", "content": text}})


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

    def test_running_text_puts_gist_first(self):
        s = session("running", since_ago_ms=83_000, tool="Bash")
        self.assertEqual(petcore.status_text(s, "running", NOW, "NYU 로봇 실행 시작"),
                         "NYU 로봇 실행 시작\nBash 실행 중 · 1분 23초")
        self.assertEqual(petcore.status_text(session("waiting"), "waiting", NOW, "무시됨"), "확인이 필요해요!")
        sessions = {"a": s}
        self.assertEqual(petcore.session_views(sessions, NOW, gists={"a": "요약"})[0][2],
                         "요약\nBash 실행 중 · 1분 23초")

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

    def test_transcript_title_follows_appends(self):
        path = self.root / "t.jsonl"
        reader = petcore.TranscriptReader()
        self.assertEqual(reader.read(str(path)), ("", ""))  # 아직 없는 파일
        path.write_text('{"type":"user"}\n{"type":"ai-title","aiTitle":"첫 제목"}\n{"type":"assi')
        self.assertEqual(reader.read(str(path))[0], "첫 제목")
        with path.open("a") as f:  # 반쯤 쓰인 줄이 이어서 완성된다
            f.write('stant"}\n{"type":"ai-title","aiTitle":"새 제목"}\nnot json "aiTitle"\n')
        self.assertEqual(reader.read(str(path))[0], "새 제목")
        path.write_text('{"aiTitle":"다시"}\n')  # 더 짧게 새로 쓰였다
        self.assertEqual(reader.read(str(path))[0], "다시")
        self.assertEqual(reader.read(""), ("", ""))

    def test_transcript_reads_in_chunks(self):
        path = self.root / "big.jsonl"
        path.write_text("x" * 50 + "\n" + '{"aiTitle":"끝"}\n' + said_line("말도 끝") + "\n")
        reader = petcore.TranscriptReader()
        reader.CHUNK = 7  # 줄이 여러 조각으로 나뉘어도 맞게 읽는지
        self.assertEqual(reader.read(str(path)), ("끝", "말도 끝"))

    def test_transcript_said_is_last_text_of_this_turn(self):
        path = self.root / "s.jsonl"
        reader = petcore.TranscriptReader()

        def append(*lines):
            with path.open("a") as f:
                f.write("".join(line + "\n" for line in lines))
            return reader.read(str(path))[1]

        self.assertEqual(append(prompt_line("해 줘"), said_line("문서를 고칩니다.")), "문서를 고칩니다.")
        # 도구 호출, 도구 결과(안에 text 가 있어도), 빈 생각, 서브에이전트의 말은 바꾸지 않는다
        self.assertEqual(append(
            entry_line({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "echo '\"type\":\"text\"'"}}]}}),
            entry_line({"type": "user", "message": {"content": [
                {"type": "tool_result", "content": [{"type": "text", "text": "결과"}]}]}}),
            entry_line({"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": "", "signature": "x"}]}}),
            said_line("서브에이전트의 말", sidechain=True),
        ), "문서를 고칩니다.")
        # 말 없이 생각만 남겨도 따라간다
        self.assertEqual(append(entry_line({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "테스트 로그를 확인해야겠다", "signature": "x"}]}})),
            "테스트 로그를 확인해야겠다")
        self.assertEqual(append(said_line("로봇 실행을 시작합니다.")), "로봇 실행을 시작합니다.")
        # 턴이 끝나도 남긴다 (턴이 끝난 뒤 백그라운드 서브에이전트가 일하면 그 말이 지금 하는 일이다)
        self.assertEqual(append(said_line("서브에이전트 셋이 기록을 읽고 있어요."), entry_line(
            {"type": "system", "subtype": "turn_duration", "durationMs": 5})), "서브에이전트 셋이 기록을 읽고 있어요.")
        # 새 프롬프트가 오면 지운다
        self.assertEqual(append(prompt_line("다른 일")), "")

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


class GistTest(unittest.TestCase):
    def test_short_said_is_shown_as_is(self):
        self.assertFalse(petcore.needs_gist("**테스트**를 돌립니다."))
        self.assertEqual(petcore.gist_fallback("**테스트**를 돌립니다."), "테스트를 돌립니다.")

    def test_fallback_is_last_sentence(self):
        said = ("문서를 고쳤습니다. 이제 로봇이 비었으니 `NYU` 100장 로봇 실행을 시작합니다.\n"
                "- 남은 시간은 약 10분입니다.")
        self.assertTrue(petcore.needs_gist(said))
        self.assertEqual(petcore.gist_fallback(said), "남은 시간은 약 10분입니다.")
        long_sentence = "로봇 NYU 100장 실행은 약 10분 남았고 PC ADE 2,000장 평가는 좀 더 지켜보겠습니다."
        self.assertEqual(len(petcore.gist_fallback(long_sentence)), petcore.GIST_MAX)
        self.assertTrue(petcore.gist_fallback(long_sentence).endswith("…"))

    def test_clean_gist(self):
        self.assertEqual(petcore.clean_gist('\n "NYU 100장 로봇 실행 시작."\n'), "NYU 100장 로봇 실행 시작")
        self.assertEqual(petcore.clean_gist("`--effort low` 옵션으로 재실험 중"), "--effort low 옵션으로 재실험 중")
        self.assertEqual(petcore.clean_gist("파일 내용 확인 중\n\n<invoke name=\"bash\">"), "파일 내용 확인 중")
        self.assertEqual(petcore.clean_gist('<invoke name="bash">'), "")
        self.assertEqual(petcore.clean_gist("  \n"), "")
        self.assertEqual(len(petcore.clean_gist("가" * 50)), petcore.GIST_MAX)

    def test_gist_command_keeps_hooks_and_history_off(self):
        args, stdin = petcore.gist_command("말" * 2000, "/bin/claude")
        self.assertEqual(args[:2], ["/bin/claude", "-p"])
        self.assertEqual(args[args.index("--setting-sources") + 1], "")  # 사용자 설정(훅)을 읽지 않는다
        self.assertEqual(json.loads(args[args.index("--settings") + 1]), {"disableAllHooks": True})
        self.assertEqual(args[args.index("--tools") + 1], "")
        for flag in ("--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--model") + 1], "haiku")
        self.assertEqual(stdin, "<말>" + "말" * petcore.GIST_INPUT_MAX + "</말>")  # 긴 말은 끝부분만


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

    def test_marked_session_comes_after_waiting_and_error(self):
        rows = [("a", "done", 1), ("b", "running", 2), ("c", "idle", 3), ("d", "error", 4)]
        sessions = {sid: session(state, age_ms=NOW - ts) for sid, state, ts in rows}
        views = [(sid, state, f"{sid}-bubble") for sid, state, _ in rows]
        marks = {"b": {"set_at": 9}, "c": {"set_at": 5}}
        self.assertEqual(petcore.folded_view(views, sessions, marks)[:4], ("d", "error", "d-bubble", 3))
        views = views[:3]  # 오류 세션이 없으면 먼저 표시한 c, 그다음 b, 마지막 다 했어요 a
        self.assertEqual(petcore.folded_view(views, sessions, marks)[:4], ("c", "idle", "c-bubble", 2))
        self.assertEqual(petcore.folded_view(views, sessions, {})[:4], ("a", "done", "a-bubble", 0))

    def test_summary_when_nothing_waits(self):
        rows = [("a", "idle", 1), ("b", "running", 2), ("c", "background", 3), ("d", "running", 4)]
        self.assertEqual(self.fold(*rows), ("a", "idle", "", 0, "작업 중 2 · 백그라운드 1"))
        self.assertEqual(self.fold(("a", "idle", 1)), ("a", "idle", "", 0, "모두 쉬는 중"))
        self.assertIsNone(petcore.folded_view([], {}))


class MarkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "reminders.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_and_load(self):
        self.assertEqual(petcore.load_marks(self.path), {})  # 아직 없는 파일
        petcore.save_marks(self.path, {"a": {"set_at": 5}})
        self.assertEqual(petcore.load_marks(self.path), {"a": {"set_at": 5}})
        self.path.write_text(json.dumps({"a": {"set_at": 5}, "b": {"set_at": "x"}, "c": 3, "d": {"set_at": True}}))
        self.assertEqual(petcore.load_marks(self.path), {"a": {"set_at": 5}})
        self.path.write_text("[1]")
        self.assertEqual(petcore.load_marks(self.path), {})
        self.path.write_text("{")
        self.assertEqual(petcore.load_marks(self.path), {})

    def test_mark_clears_on_new_prompt_or_session_end(self):
        marks = {"kept": {"set_at": 100}, "prompted": {"set_at": 100}, "ended": {"set_at": 100}}
        sessions = {"kept": {"prompt_ts": 90}, "prompted": {"prompt_ts": 150}, "other": {"prompt_ts": 0}}
        self.assertEqual(petcore.active_marks(marks, sessions), {"kept": {"set_at": 100}})

    def test_marked_bubble(self):
        self.assertEqual(petcore.marked_bubble("idle", ""), ("이어서 봐 주세요!", "marked"))
        self.assertEqual(petcore.marked_bubble("done", "다 했어요! (2분 13초)"),
                         ("이어서 봐 주세요!\n다 했어요! (2분 13초)", "marked"))
        # 확인 필요·오류는 그 말풍선이 더 급하다
        self.assertEqual(petcore.marked_bubble("waiting", "확인이 필요해요!"), ("확인이 필요해요!", "waiting"))
        self.assertEqual(petcore.marked_bubble("error", "오류로 멈췄어요"), ("오류로 멈췄어요", "error"))

    def test_load_sessions_reads_prompt_time(self):
        d = Path(self.tmp.name) / "state"
        d.mkdir()
        (d / "new.json").write_text(json.dumps({"state": "idle", "ts": 5, "prompt_ts": 3}))
        (d / "old.json").write_text(json.dumps({"state": "idle", "ts": 5}))  # prompt_ts 를 남기기 전 hook
        self.path.write_text(json.dumps({"x": {"set_at": 1}}))  # 같은 폴더의 표시 파일은 세션이 아니다
        (d / "reminders.json").write_text(self.path.read_text())
        sessions = petcore.load_sessions(d)
        self.assertEqual({k: v["prompt_ts"] for k, v in sessions.items()}, {"new": 3, "old": 0})



def drag(shake, xs, start_ms=0, step_ms=16):
    """커서를 xs 를 따라 step_ms 간격으로 옮긴다. 마지막 시각(ms)을 돌려준다."""
    t = start_ms
    for x in xs:
        shake.add(x, t)
        t += step_ms
    return t - step_ms


class PlayTest(unittest.TestCase):
    def test_turns_need_real_swings(self):
        shake = petcore.Shake()
        drag(shake, [0, 5, -5, 8, -3, 2])  # 손 떨림
        self.assertEqual(shake.turns, [])
        shake = petcore.Shake()
        swing = list(range(0, 61, 10)) + list(range(50, -1, -10))
        now = drag(shake, swing * 3)  # 0→60→0 을 세 번: 오른쪽·왼쪽 바꾸기가 다섯 번
        self.assertEqual(len(shake.turns), 5)
        self.assertEqual(shake.recent(now), 5)
        self.assertEqual(shake.recent(now + petcore.SHAKE_WINDOW_MS + 200), 0)  # 흔들기를 멈추면 가라앉는다
        self.assertEqual(shake.most, 5)  # 놓을 때 쓰는 가장 세게 흔든 정도는 남는다

    def test_held_pose(self):
        shake = petcore.Shake()
        self.assertEqual(petcore.held_pose(shake, 0), ("jumping", None))
        now = drag(shake, range(0, 100, 10))
        self.assertEqual(petcore.held_pose(shake, now), ("running-right", None))
        self.assertEqual(petcore.held_pose(shake, now + petcore.HEADING_MS + 1), ("jumping", None))  # 멈추면 매달린다
        now = drag(shake, range(90, 0, -10), now + 16)
        self.assertEqual(petcore.held_pose(shake, now), ("running-left", None))
        now = drag(shake, (list(range(0, 61, 10)) + list(range(50, -1, -10))) * 3, now + 16)
        self.assertEqual(petcore.held_pose(shake, now), ("failed", "어지러워요!"))

    def test_drag_pose(self):
        shake = petcore.Shake()
        self.assertIsNone(petcore.drag_pose(shake, 0))
        now = drag(shake, range(0, 100, 10))
        self.assertEqual(petcore.drag_pose(shake, now), "running-right")
        self.assertIsNone(petcore.drag_pose(shake, now + petcore.HEADING_MS + 1))  # 멈추면 상태 동작으로 선다
        now = drag(shake, range(90, 0, -10), now + 16)
        self.assertEqual(petcore.drag_pose(shake, now), "running-left")
        now = drag(shake, (list(range(0, 61, 10)) + list(range(50, -1, -10))) * 3, now + 16)
        now = drag(shake, range(0, -100, -10), now + 16)
        self.assertEqual(petcore.drag_pose(shake, now), "running-left")  # 줄을 옮길 때는 흔들어도 어지러워하지 않는다
        vertical = petcore.Shake()
        now = drag(vertical, [50] * 10)  # 위아래로만 끌면 x 가 그대로다
        self.assertIsNone(petcore.drag_pose(vertical, now))

    def test_release_reaction(self):
        gentle = petcore.Shake()
        drag(gentle, range(0, 100, 10))
        self.assertEqual(petcore.release_reaction(gentle)[:2], ("waving", "또 해 주세요!"))
        rough = petcore.Shake()
        now = drag(rough, (list(range(0, 61, 10)) + list(range(50, -1, -10))) * 3)
        rough.add(0, now + 5000)  # 한참 들고 있다가 놓아도 세게 흔든 건 기억한다
        self.assertEqual(petcore.release_reaction(rough)[:2], ("failed", "흑흑…"))

    def test_rapid_clicks(self):
        times, angry = [], False
        for i in range(petcore.RAPID_CLICKS - 1):
            times, angry = petcore.rapid_clicks(times, i * 300)
            self.assertFalse(angry)
        times, angry = petcore.rapid_clicks(times, 1300)
        self.assertTrue(angry)
        self.assertEqual(times, [])  # 화내고 나면 처음부터 다시 센다
        edge = []
        for t in (0, 500, 1000, 1500):
            edge, _ = petcore.rapid_clicks(edge, t)
        self.assertTrue(petcore.rapid_clicks(edge, petcore.RAPID_CLICK_MS)[1])  # 첫 클릭부터 딱 2초까지는 센다
        self.assertFalse(petcore.rapid_clicks(edge, petcore.RAPID_CLICK_MS + 1)[1])
        slow = []
        for i in range(10):  # 천천히 누르면 화내지 않는다
            slow, angry = petcore.rapid_clicks(slow, i * (petcore.RAPID_CLICK_MS // 2 + 100))
            self.assertFalse(angry)

    def test_pet_strokes(self):
        times, happy = petcore.pet_strokes([], 0)
        times, happy = petcore.pet_strokes(times, 400)
        self.assertFalse(happy)
        times, happy = petcore.pet_strokes(times, 800)
        self.assertTrue(happy)
        self.assertEqual(times, [])
        times, _ = petcore.pet_strokes([], 0)
        times, _ = petcore.pet_strokes(times, 1000)
        self.assertFalse(petcore.pet_strokes(times, petcore.PET_STROKE_MS + 1)[1])  # 첫 칸이 지나 버렸다


class FixedRandom:
    """wander_step 에 넣는 정해진 난수: uniform 은 value, choice 는 pick 번째."""

    def __init__(self, value, pick=0):
        self.value, self.pick = value, pick

    def uniform(self, low, high):
        return self.value

    def choice(self, items):
        return items[min(self.pick, len(items) - 1)]


class IdleTest(unittest.TestCase):
    def test_idle_mood(self):
        self.assertIsNone(petcore.idle_mood("idle", petcore.WANDER_AFTER_MS - 1))
        self.assertEqual(petcore.idle_mood("idle", petcore.WANDER_AFTER_MS), "wander")
        self.assertEqual(petcore.idle_mood("idle", petcore.SLEEP_AFTER_MS), "sleep")
        for state in ("running", "waiting", "done", "error", "background"):
            self.assertIsNone(petcore.idle_mood(state, petcore.SLEEP_AFTER_MS * 2))  # 쉬는 중일 때만

    def test_wander_step(self):
        self.assertEqual(petcore.wander_step(FixedRandom(0.5, 0), 100, 500, 500), -50)
        self.assertEqual(petcore.wander_step(FixedRandom(0.5, 1), 100, 500, 500), 50)
        self.assertEqual(petcore.wander_step(FixedRandom(1.0, 1), 100, 500, 30), -100)  # 오른쪽은 좁다
        self.assertEqual(petcore.wander_step(FixedRandom(1.0), 100, 20, 60), 60)  # 양쪽 다 좁으면 넓은 쪽 끝까지
        self.assertEqual(petcore.wander_step(FixedRandom(1.0), 100, 50, 6), -50)
        self.assertEqual(petcore.wander_step(FixedRandom(1.0), 100, 6, 20), 0)  # 화면 구석에 끼어 있다
        self.assertEqual(petcore.wander_step(FixedRandom(1.0), 100, 0, -5), 0)  # 걸을 자리가 없다

    def test_long_work(self):
        self.assertTrue(petcore.long_work(session("done", since_ago_ms=petcore.LONG_WORK_MS)))
        self.assertFalse(petcore.long_work(session("done", since_ago_ms=petcore.LONG_WORK_MS - 1000)))
        self.assertFalse(petcore.long_work(session("error", since_ago_ms=petcore.LONG_WORK_MS * 2)))

    def test_changed_states(self):
        old = {"a": "running", "b": "idle", "gone": "done"}
        new = {"a": "done", "b": "idle", "new": "running"}
        self.assertEqual(petcore.changed_states(old, new), {"a": ("running", "done")})


class NeighborTest(unittest.TestCase):
    SPOTS = {"a": (1000, 500), "b": (900, 500), "c": (1300, 500), "far": (1600, 500)}

    def test_nearest(self):
        self.assertEqual(petcore.nearest(self.SPOTS, "a", 400, self.SPOTS), "b")
        self.assertEqual(petcore.nearest(self.SPOTS, "a", 400, ["c", "far"]), "c")  # 고를 수 있는 펫 중에서
        self.assertIsNone(petcore.nearest(self.SPOTS, "a", 400, ["far"]))  # 너무 멀다
        self.assertIsNone(petcore.nearest(self.SPOTS, "a", 400, ["a"]))  # 자기 자신은 빼고
        self.assertIsNone(petcore.nearest(self.SPOTS, "gone", 400, self.SPOTS))

    def test_beside_dx(self):
        gap = round(petcore.BESIDE * 100)
        self.assertEqual(petcore.beside_dx(500, 100, 1000, 100), 1000 - gap - 500)  # 왼쪽에서 다가가 왼쪽 옆에
        self.assertEqual(petcore.beside_dx(1500, 100, 1000, 100), 1000 + gap - 1500)  # 오른쪽에서 오면 오른쪽 옆에
        self.assertEqual(petcore.beside_dx(1000 - 110, 100, 1000, 100), 110 - gap)  # 줄에서 바로 옆이면 한 걸음


class ThrowTest(unittest.TestCase):
    BOUNDS = (0, 0, 1000, 500)  # 왼쪽, 위, 오른쪽, 바닥

    def test_release_velocity(self):
        samples = [(0, 0, 0), (100, 50, 0), (150, 100, 20), (200, 200, 40)]
        vx, vy = petcore.release_velocity(samples, 200)
        self.assertAlmostEqual(vx, 1500)  # 80ms 이상 앞선 가장 가까운 자리(100ms)부터 놓은 때까지
        self.assertAlmostEqual(vy, 400)
        self.assertEqual(petcore.release_velocity([(0, 0, 0)], 0), (0.0, 0.0))
        fast = petcore.release_velocity([(0, 0, 0), (10, 1000, 0)], 10)
        self.assertAlmostEqual(math.hypot(*fast), petcore.THROW_MAX_SPEED)

    def test_jitter_or_stop_before_release_is_not_a_throw(self):
        jitter = [(0, 500, 500), (998, 500, 500), (1000, 504, 500)]  # 가만히 들고 있다가 놓을 때 4px 떨림
        self.assertFalse(petcore.is_throw(petcore.release_velocity(jitter, 1000)))
        stopped = [(t, 30 * t // 20, 0) for t in range(0, 401, 20)] + [(460, 600, 0)]  # 1500px/s 로 끌다 멈춰 60ms 뒤 놓기
        self.assertFalse(petcore.is_throw(petcore.release_velocity(stopped, 460)))
        flick = [(t, 30 * t // 20, 0) for t in range(0, 401, 20)]  # 끌던 그대로 놓기
        self.assertTrue(petcore.is_throw(petcore.release_velocity(flick, 400)))

    def test_fling_inward_from_past_the_wall_keeps_speed(self):
        x, _, vx, _, _ = petcore.fling_step(-48, 400, 1300, 0, 0.016, self.BOUNDS)  # 반쯤 벽 밖에서 안쪽으로
        self.assertEqual((x, vx), (0, 1300))  # 자리만 맞추고 속도는 그대로
        self.assertTrue(petcore.is_throw((petcore.THROW_MIN_SPEED, 0)))
        self.assertFalse(petcore.is_throw((300, 400)))

    def test_fling_bounds_are_screen_edges(self):
        # 화면 (1080, 379)~(3000, 1459), 펫 그림 96 폭, 창 안 42px 아래에서 시작, 창 높이 166 (말풍선 42 + 펫 104 + 이름표 20)
        # 바닥에 서면 창 아래 끝(이름표)이 화면 아래 끝에 닿는다: 1335 - 42 + 166 = 1459
        self.assertEqual(petcore.fling_bounds((1080, 379, 3000, 1459), 96, 42, 166), (1080, 379, 2904, 1335))

    def run_fling(self, x, y, vx, vy, dt=0.016):
        path = []
        for _ in range(int(petcore.FLING_MAX_MS / 1000 / dt)):
            x, y, vx, vy, rested = petcore.fling_step(x, y, vx, vy, dt, self.BOUNDS)
            path.append((x, y))
            if rested:
                return path, True
        return path, False

    def test_fling_falls_bounces_and_rests_on_floor(self):
        path, rested = self.run_fling(500, 300, 0, -1500)
        self.assertTrue(rested)
        self.assertEqual(path[-1][1], 500)  # 바닥에 선다
        self.assertEqual(min(y for _, y in path), 0)  # 위로 세게 던지면 천장에 닿는다
        self.assertTrue(all(0 <= y <= 500 for _, y in path))

    def test_fling_bounces_off_walls(self):
        path, rested = self.run_fling(900, 500, 3000, 0)
        self.assertTrue(rested)
        self.assertTrue(all(0 <= x <= 1000 for x, _ in path))
        self.assertIn(1000, [x for x, _ in path])  # 오른쪽 벽에 닿았다가
        self.assertLess(path[-1][0], 1000)  # 튕겨 나와 선다


class MotionTest(unittest.TestCase):
    AREA = (1000, 3000)  # 화면 왼쪽 x, 오른쪽 끝 x + 1

    def test_new_sessions(self):
        sessions = {"old": session("idle", created=NOW - 60_000), "new": session("idle", created=NOW - 1000),
                    "seen": session("idle", created=NOW - 1000)}
        self.assertEqual(petcore.new_sessions(sessions, {"seen"}, NOW - 10_000), {"new"})

    def test_run_off_toward_wider_side(self):
        self.assertEqual(petcore.run_off_left(2600, 100, self.AREA, 300), 2300)  # 오른쪽 아래 줄: 왼쪽이 넓다
        self.assertEqual(petcore.run_off_left(1300, 100, self.AREA, 200), 1500)
        self.assertEqual(petcore.run_off_left(1950, 100, self.AREA, 300), 2250)  # 양쪽이 같으면 오른쪽

    def test_run_off_stays_on_screen(self):
        self.assertEqual(petcore.run_off_left(1000, 100, (1000, 1250), 300), 1150)  # 넓은 쪽도 좁으면 화면 끝까지만
        self.assertEqual(petcore.run_off_left(1000, 250, (1000, 1250), 300), 1000)  # 자리가 없으면 제자리


class WorkAreaTest(unittest.TestCase):
    # 가운데 주 모니터는 위 상단바 32px, 아래 고정한 독 66px 을 뺀다. 양옆 세로 모니터는 전체
    VALUES = [1080, 411, 1920, 982, 3000, 142, 1080, 1920, 0, 0, 1080, 1920]

    def test_work_areas(self):
        self.assertEqual(petcore.work_areas(self.VALUES),
                         [(1080, 411, 1920, 982), (3000, 142, 1080, 1920), (0, 0, 1080, 1920)])
        self.assertEqual(petcore.work_areas([1, 2, 3, 4, 5]), [(1, 2, 3, 4)])  # 모자란 끝은 버린다
        self.assertEqual(petcore.work_areas([]), [])

    def test_work_area_for_each_monitor(self):
        areas = petcore.work_areas(self.VALUES)
        self.assertEqual(petcore.work_area_for((1080, 379, 1920, 1080), areas), (1080, 411, 1920, 982))
        self.assertEqual(petcore.work_area_for((3000, 142, 1080, 1920), areas), (3000, 142, 1080, 1920))
        self.assertEqual(petcore.work_area_for((0, 0, 1080, 1920), areas), (0, 0, 1080, 1920))
        self.assertEqual(petcore.work_area_for((5000, 0, 800, 600), areas), (5000, 0, 800, 600))  # 모르면 모니터 전체
        self.assertEqual(petcore.work_area_for((0, 0, 1000, 1000), [(-10, 20, 2000, 900)]), (0, 20, 1000, 900))


class GrowthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "claude-pet" / "growth.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_level_needs_more_each_time(self):
        self.assertEqual(petcore.level_of(0), (1, 0, 100))
        self.assertEqual(petcore.level_of(99), (1, 99, 100))
        self.assertEqual(petcore.level_of(100), (2, 0, 200))
        self.assertEqual(petcore.level_of(299), (2, 199, 200))
        self.assertEqual(petcore.level_of(1000), (5, 0, 500))
        self.assertEqual(petcore.level_of(4500), (10, 0, 1000))
        for xp in range(0, 30_000, 7):  # 근의 공식 어림이 한 칸씩 쌓아 올린 것과 같다
            level, have, need = petcore.level_of(xp)
            self.assertEqual(petcore.level_start(level) + have, xp)
            self.assertTrue(0 <= have < need == 100 * level)

    def test_huge_xp_is_quick(self):
        level, have, need = petcore.level_of(10 ** 30)  # 손으로 고친 터무니없는 기록에도 멈추지 않는다
        self.assertEqual(petcore.level_start(level) + have, 10 ** 30)
        self.assertLess(have, need)

    def test_xp_from_work_minutes_and_done(self):
        self.assertEqual(petcore.pet_xp({"work_ms": 59_999, "done": 0}), 0)  # 1분이 안 되면 아직 0
        self.assertEqual(petcore.pet_xp({"work_ms": 120_000, "done": 3}), 2 + 15)

    def test_add_growth_reports_level_up(self):
        growth = {}
        self.assertIsNone(petcore.add_growth(growth, "a", work_ms=60_000))  # 처음 보는 펫은 새로 만든다
        self.assertEqual(growth, {"a": {"work_ms": 60_000, "done": 0}})
        growth["a"]["done"] = 19  # 1 + 95 XP
        self.assertIsNone(petcore.add_growth(growth, "a", work_ms=3 * 60_000))  # 99 XP
        self.assertEqual(petcore.add_growth(growth, "a", done=1), 2)  # 104 XP
        self.assertEqual(petcore.add_growth(growth, "a", done=60), 3)  # 여러 레벨을 한꺼번에 넘으면 마지막 레벨
        self.assertIsNone(petcore.add_growth(growth, "a"))

    def test_work_step(self):
        self.assertEqual(petcore.work_step(1000, 2000), 1000)
        self.assertEqual(petcore.work_step(1000, 1000), 0)
        self.assertEqual(petcore.work_step(2000, 1000), 0)  # 시계가 뒤로 가면
        self.assertEqual(petcore.work_step(0, petcore.WORK_STEP_MAX_MS), petcore.WORK_STEP_MAX_MS)
        self.assertEqual(petcore.work_step(0, 3_600_000), petcore.WORK_STEP_MAX_MS)  # 절전에서 돌아오면

    @staticmethod
    def hooked(state, ts, event):
        s = session(state)
        s.update(ts=ts, event=event)
        return s

    def test_work_waits_for_next_hook(self):
        sessions = {"a": self.hooked("running", 100, "PreToolUse")}
        credit, pending = petcore.work_tick({}, sessions, {"a": "running"}, 1000)
        self.assertEqual((credit, pending), ({}, {"a": [100, 1000]}))  # 아직 보류
        credit, pending = petcore.work_tick(pending, sessions, {"a": "running"}, 1000)
        self.assertEqual((credit, pending), ({}, {"a": [100, 2000]}))
        sessions = {"a": self.hooked("running", 200, "PostToolUse")}  # 일이 이어졌다
        credit, pending = petcore.work_tick(pending, sessions, {"a": "running"}, 1000)
        self.assertEqual((credit, pending), ({"a": 2000}, {"a": [200, 1000]}))
        sessions = {"a": self.hooked("done", 300, "Stop")}  # 끝낸 일도 확정
        credit, pending = petcore.work_tick(pending, sessions, {"a": "done"}, 1000)
        self.assertEqual((credit, pending), ({"a": 1000}, {}))

    def test_work_before_new_prompt_is_dropped(self):
        # Esc 로 끊으면 Stop 이 안 와서 30분 동안 작업 중으로 보이다가 (그 뒤는 쉬는 중), 다음 프롬프트가 온다
        pending = {"a": [100, 1_800_000]}
        sessions = {"a": self.hooked("running", 100, "PreToolUse")}
        credit, pending = petcore.work_tick(pending, sessions, {"a": "idle"}, 1000)
        self.assertEqual((credit, pending), ({}, {"a": [100, 1_800_000]}))  # 더 쌓지 않고 기다린다
        sessions = {"a": self.hooked("running", 900, "UserPromptSubmit")}
        credit, pending = petcore.work_tick(pending, sessions, {"a": "running"}, 1000)
        self.assertEqual((credit, pending), ({}, {"a": [900, 1000]}))  # 버리고 새로 쌓는다

    def test_work_of_ended_session_is_dropped(self):
        credit, pending = petcore.work_tick({"gone": [100, 5000]}, {}, {}, 1000)
        self.assertEqual((credit, pending), ({}, {}))

    @staticmethod
    def prompted(state, prompt_ts, age_ms=0):
        s = session(state, age_ms)
        s["prompt_ts"] = prompt_ts
        return s

    def test_new_dones_counts_once_per_prompt(self):
        sessions = {"a": self.prompted("done", 100), "b": self.prompted("running", 100),
                    "c": self.prompted("done", 50, age_ms=500), "d": self.prompted("error", 100),
                    "e": self.prompted("background", 100), "f": self.prompted("done", 0)}  # f: 프롬프트를 보낸 적 없음
        self.assertEqual(petcore.new_dones(sessions, {}), {"a": 100, "c": 50})
        self.assertEqual(petcore.new_dones(sessions, {"a": 100, "c": 50}), {})
        # 백그라운드 작업 알림으로 혼자 깨어나 다시 끝내면 ts 는 바뀌어도 프롬프트는 같다
        self.assertEqual(petcore.new_dones({"a": self.prompted("done", 100, age_ms=-9000)}, {"a": 100}), {})
        self.assertEqual(petcore.new_dones({"a": self.prompted("done", 700)}, {"a": 100}), {"a": 700})  # 다음 프롬프트

    def test_done_tick(self):
        sessions = {"a": self.prompted("done", 100), "b": self.prompted("running", 200)}
        self.assertEqual(petcore.done_tick(None, sessions), ({}, {"a": 100}))  # 켜기 전에 끝나 있던 일은 기억만
        self.assertEqual(petcore.done_tick({"a": 100}, sessions), ({}, {"a": 100}))
        sessions["b"] = self.prompted("done", 200)
        self.assertEqual(petcore.done_tick({"a": 100}, sessions), ({"b": 200}, {"a": 100, "b": 200}))
        self.assertEqual(petcore.done_tick({"a": 100, "gone": 5}, {"a": self.prompted("done", 100)}),
                         ({}, {"a": 100}))

    def test_save_and_load(self):
        self.assertEqual(petcore.load_growth(self.path), ({}, True))  # 아직 없는 파일
        petcore.save_growth(self.path, {"동원리": {"work_ms": 5, "done": 2}})
        petcore.save_growth(self.path, {"동원리": {"work_ms": 6, "done": 2}})  # 덮어쓴다
        self.assertEqual(petcore.load_growth(self.path), ({"동원리": {"work_ms": 6, "done": 2}}, True))
        self.assertEqual(os.listdir(self.path.parent), ["growth.json"])  # tmp 가 남지 않는다

    def test_bad_entries_are_skipped_but_reported(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"pets": {"a": {"work_ms": 5, "done": 2}, "b": {"work_ms": "x"},
                                                  "c": 3, "d": {"done": True}, "e": {"work_ms": -1},
                                                  "f": {"done": 4}, "g": {"work_ms": 120000.0}}}))
        self.assertEqual(petcore.load_growth(self.path),
                         ({"a": {"work_ms": 5, "done": 2}, "f": {"work_ms": 0, "done": 4}}, False))

    def test_broken_file_is_not_treated_as_intact(self):
        self.path.parent.mkdir(parents=True)
        for text in ("{", "[1]", json.dumps({"pets": [1]})):
            self.path.write_text(text)
            self.assertEqual(petcore.load_growth(self.path), ({}, False))
        self.path.unlink()
        self.path.mkdir()  # 읽을 수 없는 경로
        self.assertEqual(petcore.load_growth(self.path), ({}, False))

    def test_growth_row(self):
        record = {"work_ms": (32 * 60 + 5) * 60_000 + 59_000, "done": 214}  # 1925 + 1070 = 2995 XP (Lv.8 은 2800)
        self.assertEqual(petcore.growth_row("복슬이", record),
                         ("복슬이 Lv.8", 24, "195/800 XP", "같이 일한 시간 32시간 5분 · 끝낸 일 214번"))
        self.assertEqual(petcore.growth_row("아주아주긴펫이름입니다요", None),
                         ("아주아주긴펫이름입… Lv.1", 0, "0/100 XP", "같이 일한 시간 0분 · 끝낸 일 0번"))
        self.assertEqual(petcore.growth_row("a", {"work_ms": 60 * 60_000, "done": 0})[3],
                         "같이 일한 시간 1시간 0분 · 끝낸 일 0번")
        self.assertEqual(petcore.growth_row("a", {"work_ms": 0, "done": 19})[1], 95)
        self.assertEqual(petcore.growth_row("a", {"work_ms": 99 * 60_000 + 59_999, "done": 0})[1:3],
                         (99, "99/100 XP"))  # 레벨이 오르기 전에는 100% 로 보이지 않는다


class TeamTest(unittest.TestCase):
    NOW_S = 1791428400  # 2026-10-08(목) 12:00 KST
    NOW_MS = NOW_S * 1000

    def setUp(self):
        self.old_tz = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Seoul"
        time.tzset()

    def tearDown(self):
        if self.old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old_tz
        time.tzset()

    def usage(self, ts, **values):
        return {"ts": ts, **dict.fromkeys(petcore.USAGE_FIELDS), **values}

    def limits(self, five=None, five_reset=None, week=None, week_reset=None):
        return {"ts": self.NOW_MS, "five_hour_pct": five, "five_hour_reset": five_reset,
                "seven_day_pct": week, "seven_day_reset": week_reset}

    def test_account_limits_takes_largest_in_current_window(self):
        reset = self.NOW_S + 3600
        usage = {
            "busy": self.usage(1, five_hour_pct=40, five_hour_reset=reset, seven_day_pct=20, seven_day_reset=reset + 9),
            # 오래 쉰 세션이 같은 주기의 옛 값을 늦게 다시 썼다
            "stale": self.usage(9, five_hour_pct=25, five_hour_reset=reset + 30, seven_day_pct=18,
                                seven_day_reset=reset),
            "last_window": self.usage(5, five_hour_pct=95, five_hour_reset=reset - 5 * 3600),  # 지난 주기
        }
        limits = petcore.account_limits(usage)
        self.assertEqual(limits, {"ts": 9, "five_hour_pct": 40, "five_hour_reset": reset + 30,
                                  "seven_day_pct": 20, "seven_day_reset": reset + 9})
        only_old = petcore.account_limits({"a": self.usage(3, five_hour_pct=95, five_hour_reset=self.NOW_S - 10)})
        self.assertEqual((only_old["five_hour_pct"], only_old["seven_day_pct"]), (95, None))
        self.assertEqual(petcore.account_limits({"a": self.usage(3, five_hour_pct=7)})["five_hour_pct"], 7)

    def test_account_limits_edges(self):
        reset = self.NOW_S + 3600
        same = {"a": self.usage(1, five_hour_pct=30, five_hour_reset=reset),
                "b": self.usage(2, five_hour_pct=50, five_hour_reset=reset - 60)}  # 60초 안은 같은 주기
        self.assertEqual(petcore.account_limits(same)["five_hour_pct"], 50)
        apart = {"a": self.usage(1, five_hour_pct=30, five_hour_reset=reset),
                 "b": self.usage(2, five_hour_pct=50, five_hour_reset=reset - 61)}
        self.assertEqual(petcore.account_limits(apart)["five_hour_pct"], 30)
        # 리셋 시각을 아는 값이 없으면 가장 최근에 읽힌 값 (며칠 묵은 파일의 100% 를 집지 않는다)
        unknown = {"old": self.usage(1, five_hour_pct=100), "new": self.usage(5, five_hour_pct=12)}
        self.assertEqual(petcore.account_limits(unknown)["five_hour_pct"], 12)
        mixed = {"a": self.usage(1, five_hour_pct=20, five_hour_reset=reset), "b": self.usage(5, five_hour_pct=90)}
        self.assertEqual(petcore.account_limits(mixed)["five_hour_pct"], 20)  # 주기를 아는 값을 쓴다

    def test_hold_peaks_keeps_the_largest_in_a_cycle(self):
        reset, week = self.NOW_S + 3600, self.NOW_S + 86400
        limits, peaks = petcore.hold_peaks(self.limits(60, reset, 20, week), {})
        self.assertEqual((limits["five_hour_pct"], peaks), (60, {"five_hour": (reset, 60), "seven_day": (week, 20)}))
        # 60% 를 가진 세션이 끝나 파일이 지워지고 옛 30% 만 남았다: 거꾸로 오르지 않는다
        limits, peaks = petcore.hold_peaks(self.limits(30, reset, 20, week), peaks)
        self.assertEqual(petcore.team_hp(limits, self.NOW_MS), (40, reset))
        limits, peaks = petcore.hold_peaks(self.limits(61, reset, 20, week), peaks)  # 다음 값은 그대로 1% 만
        self.assertEqual(petcore.team_hp(limits, self.NOW_MS), (39, reset))
        # 지난 주기의 파일만 남아도 기억한 새 주기 값을 쓴다
        limits, peaks = petcore.hold_peaks(self.limits(95, reset - 5 * 3600, 20, week), peaks)
        self.assertEqual((limits["five_hour_pct"], limits["five_hour_reset"]), (61, reset))
        # 그 창 값을 가진 파일이 다 지워져도
        limits, peaks = petcore.hold_peaks(self.limits(None, None, 20, week), peaks)
        self.assertEqual((limits["five_hour_pct"], limits["five_hour_reset"]), (61, reset))
        # 새 주기가 오면 새로 센다
        limits, peaks = petcore.hold_peaks(self.limits(3, reset + 5 * 3600, 20, week), peaks)
        self.assertEqual((limits["five_hour_pct"], peaks["five_hour"]), (3, (reset + 5 * 3600, 3)))
        self.assertEqual(petcore.hold_peaks(None, peaks), (None, peaks))
        limits, _ = petcore.hold_peaks(self.limits(7, None), {"five_hour": (reset, 50)})  # 주기를 모르면 그대로
        self.assertEqual(limits["five_hour_pct"], 7)

    def test_team_hp_is_the_five_hour_window(self):
        week_reset, five_reset = self.NOW_S + 86400, self.NOW_S + 3600
        self.assertEqual(petcore.team_hp(self.limits(28.4, five_reset, 41, week_reset), self.NOW_MS), (72, five_reset))
        self.assertEqual(petcore.team_hp(self.limits(71.5, five_reset, 41, week_reset), self.NOW_MS), (28, five_reset))
        self.assertEqual(petcore.team_hp(self.limits(10, five_reset, 100, week_reset), self.NOW_MS), (90, five_reset))  # 주간은 안 본다
        self.assertEqual(petcore.team_hp(self.limits(None, None, 41, None), self.NOW_MS), (100, None))  # 5시간을 모르면 가득
        self.assertIsNone(petcore.team_hp(self.limits(), self.NOW_MS))
        self.assertIsNone(petcore.team_hp(None, self.NOW_MS))

    def test_team_hp_after_reset(self):
        week_reset = self.NOW_S + 86400
        # 5시간이 리셋되면 새 값이 오기 전까지 0% 로 본다
        self.assertEqual(petcore.team_hp(self.limits(100, self.NOW_S - 1, 41, week_reset), self.NOW_MS),
                         (100, self.NOW_S - 1))
        self.assertEqual(petcore.team_hp(self.limits(130, self.NOW_S + 60), self.NOW_MS), (0, self.NOW_S + 60))
        self.assertEqual(petcore.team_hp(self.limits(50, self.NOW_S), self.NOW_MS), (100, self.NOW_S))  # 리셋 시각 = 지금
        self.assertEqual(petcore.team_hp(self.limits(99.5, self.NOW_S + 60), self.NOW_MS)[0], 0)  # 반올림해 100%

    def test_week_end_once_per_cycle(self):
        now, week = self.NOW_S, self.NOW_S + 86400
        state, reason = petcore.week_end({}, self.limits(10, None, 40, week), now)   # 처음은 기준만
        self.assertEqual((state, reason), ({"seen": week, "ended": None}, None))
        state, reason = petcore.week_end(state, self.limits(10, None, 99.4, week + 600), now)   # 리셋 시각이 흔들려도 같은 주기
        self.assertEqual((state, reason), ({"seen": week + 600, "ended": None}, None))
        state, reason = petcore.week_end(state, self.limits(10, None, 100, week), now)   # 다 썼다
        self.assertEqual((state, reason), ({"seen": week, "ended": week}, "full"))
        for shift in (60, 3000, 9000, 15000):   # 다 쓴 뒤 리셋 시각이 흔들려도 다시 끝나지 않는다
            state, reason = petcore.week_end(state, self.limits(10, None, 100, week + shift), now + 60)
            self.assertIsNone(reason)
        state, reason = petcore.week_end(state, self.limits(10, None, 100, week + 15000), week + 15005)   # 리셋 시각이 지나도
        self.assertIsNone(reason)
        state, reason = petcore.week_end(state, self.limits(10, None, 2, week + 7 * 86400), week + 15010)
        self.assertEqual((state, reason), ({"seen": week + 7 * 86400, "ended": week + 15000}, None))   # 새 주기를 본다

    def test_week_end_on_reset(self):
        now, week = self.NOW_S, self.NOW_S + 86400
        state = {"seen": week, "ended": None}
        state, reason = petcore.week_end(state, self.limits(10, None, 60, week), week + 1)   # 리셋 시각이 지났다 (새 값은 아직)
        self.assertEqual((state, reason), ({"seen": week, "ended": week}, "reset"))
        state, reason = petcore.week_end(state, self.limits(10, None, 3, week + 7 * 86400), week + 30)
        self.assertEqual((state, reason), ({"seen": week + 7 * 86400, "ended": week}, None))   # 새 값이 와도 다시 안 끝난다
        # 꺼져 있던 동안 두 주기가 지났다: 한 번만 끝나고 지금 주기를 본다
        state, reason = petcore.week_end({"seen": week, "ended": None}, self.limits(10, None, 30, week + 14 * 86400), week + 8 * 86400)
        self.assertEqual((state, reason), ({"seen": week + 14 * 86400, "ended": week}, "reset"))
        state, reason = petcore.week_end(state, self.limits(10, None, 30, week + 14 * 86400), week + 8 * 86400)
        self.assertIsNone(reason)
        # 꺼져 있던 동안 리셋이 지나고 새 주기도 이미 다 썼다: 한 번만
        state, reason = petcore.week_end({"seen": week, "ended": None}, self.limits(10, None, 100, week + 7 * 86400), week + 86400)
        self.assertEqual(reason, "reset")
        state, reason = petcore.week_end(state, self.limits(10, None, 100, week + 7 * 86400), week + 86400 + 1)
        self.assertIsNone(reason)
        # 사용량을 모르게 돼도 리셋 시각이 지나면 끝난다
        state, reason = petcore.week_end({"seen": week, "ended": None}, None, week + 1)
        self.assertEqual(reason, "reset")
        self.assertEqual(petcore.week_end({}, None, now), ({"seen": None, "ended": None}, None))

    def test_week_end_first_run_never_ends(self):
        """옛 기록 파일로 처음 켰을 때: 지난 주기의 값만 남았거나 이미 다 쓴 주기여도 켜자마자 끝내지 않는다."""
        now = self.NOW_S
        state, reason = petcore.week_end({}, self.limits(10, None, 70, now - 3600), now)   # 지난 주기의 묵은 값
        self.assertEqual((state, reason), ({"seen": now - 3600, "ended": now - 3600}, None))
        state, reason = petcore.week_end(state, self.limits(10, None, 70, now - 3600), now + 5)
        self.assertIsNone(reason)
        state, reason = petcore.week_end({}, self.limits(10, None, 100, now + 3600), now)   # 이미 다 쓴 주기
        self.assertEqual((state, reason), ({"seen": now + 3600, "ended": now + 3600}, None))
        state, reason = petcore.week_end(state, self.limits(10, None, 100, now + 3600), now + 5)
        self.assertIsNone(reason)

    def test_hp_events(self):
        self.assertEqual(petcore.hp_events(None, 72), (0, False, None))  # 처음 값은 기준만
        self.assertEqual(petcore.hp_events(72, 72), (0, False, None))
        self.assertEqual(petcore.hp_events(72, 71), (1, False, None))
        self.assertEqual(petcore.hp_events(72, 69), (3, False, None))
        self.assertEqual(petcore.hp_events(25, 15), (10, True, None))   # 20 아래로
        self.assertEqual(petcore.hp_events(15, 12), (3, False, None))
        self.assertEqual(petcore.hp_events(21, 20), (1, True, None))
        self.assertEqual(petcore.hp_events(11, 9), (2, True, None))     # 10 아래로
        self.assertEqual(petcore.hp_events(9, 0), (9, False, "over"))
        self.assertEqual(petcore.hp_events(0, 3), (0, False, "potion"))  # GAME OVER 에서 벗어나면
        self.assertEqual(petcore.hp_events(30, 41), (0, False, "potion"))
        self.assertEqual(petcore.hp_events(30, 39), (0, False, None))   # 조금 오른 것은 그냥 지나간다
        self.assertEqual(petcore.hp_events(25, 5), (20, True, None))    # 문턱 둘을 한꺼번에 넘어도 경고 한 번
        self.assertEqual(petcore.hp_events(0, 0), (0, False, None))

    def test_texts(self):
        self.assertEqual(petcore.hp_label(72), "팀 HP 72%")
        self.assertEqual(petcore.hp_label(0), "GAME OVER")
        self.assertEqual(petcore.game_over_text(self.NOW_S + 42 * 60, self.NOW_MS), "12:42 리셋\n(42분 뒤)")
        self.assertEqual(petcore.game_over_text(None, self.NOW_MS), "리셋을 기다려요")
        self.assertEqual(petcore.game_over_text(1791763200, self.NOW_MS), "10/12(월) 09:00 리셋\n(3일 21시간 뒤)")
        self.assertEqual(petcore.game_over_text(self.NOW_S - 1, self.NOW_MS), "리셋을 기다려요")
        rows, note = petcore.limit_rows(self.limits(28, self.NOW_S + 3600, None, None), self.NOW_MS)
        self.assertEqual(rows, [("5시간", 28, "13:00 리셋 (1시간 0분 뒤)"), ("주간", None, "정보 없음")])
        self.assertEqual(note, "")


class LayoutTest(unittest.TestCase):
    SIZES = {"a": (100, 150), "b": (120, 200), "c": (80, 150)}

    def test_row_from_anchor_to_the_left(self):
        spots = petcore.layout_spots(["a", "b", "c"], self.SIZES, (1000, 700), 6)
        self.assertEqual(spots, {"a": (900, 550), "b": (774, 500), "c": (688, 550)})

    def test_moved_pet_stands_on_its_foot_and_leaves_the_row(self):
        spots = petcore.layout_spots(["a", "b", "c"], self.SIZES, (1000, 700), 6, {"a": [300, 400]})
        self.assertEqual(spots["a"], (250, 250))  # 발밑 가운데가 (300, 400)
        self.assertEqual(spots["b"], (880, 500))  # 안 옮긴 펫들이 줄 오른쪽 끝부터 빈칸을 채운다
        self.assertEqual(spots["c"], (794, 550))


if __name__ == "__main__":
    unittest.main()
