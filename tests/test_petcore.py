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


if __name__ == "__main__":
    unittest.main()
