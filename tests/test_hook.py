"""hooks/claude-pet-hook.sh 에 예시 훅 입력을 넣고 상태 파일을 확인한다.

DISPLAY 를 비워서 펫을 띄우지 않는다.
"""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks/claude-pet-hook.sh"
SID = "abc-123"


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        self.env["XDG_RUNTIME_DIR"] = str(root / "run")
        self.env["XDG_CONFIG_HOME"] = str(root / "config")
        self.state_file = root / "run/claude-pet" / f"{SID}.json"

    def tearDown(self):
        self.tmp.cleanup()

    def fire(self, event, sid=SID, **fields):
        payload = {"hook_event_name": event, "session_id": sid, "cwd": "/home/u/rc_sw",
                   "transcript_path": "/dev/null", **fields}
        result = subprocess.run([str(HOOK)], input=json.dumps(payload), env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "", "훅은 아무것도 출력하면 안 된다")

    def state(self):
        return json.loads(self.state_file.read_text())

    def test_full_turn(self):
        self.fire("SessionStart", source="startup")
        self.assertEqual(self.state()["state"], "idle")

        self.fire("UserPromptSubmit", prompt="hi")
        prompt = self.state()
        self.assertEqual((prompt["state"], prompt["tool"]), ("running", ""))
        self.assertEqual(prompt["since"], prompt["ts"])

        self.fire("PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
        s = self.state()
        self.assertEqual((s["state"], s["tool"], s["since"]), ("running", "Bash", prompt["since"]))

        self.fire("Notification", notification_type="permission_prompt", message="needs permission")
        self.assertEqual(self.state()["state"], "waiting")

        self.fire("PostToolUse", tool_name="Bash", tool_input={}, tool_response={})
        s = self.state()
        self.assertEqual((s["state"], s["tool"]), ("running", ""))

        self.fire("PreToolUse", tool_name="AskUserQuestion", tool_input={})
        self.assertEqual(self.state()["state"], "waiting")

        self.fire("Notification", notification_type="idle_prompt", message="waiting")
        self.assertEqual(self.state()["state"], "waiting", "idle_prompt 는 무시한다")

        self.fire("Stop", stop_hook_active=False)
        s = self.state()
        self.assertEqual((s["state"], s["since"]), ("done", prompt["since"]))

        self.fire("StopFailure", error="rate_limit")
        self.assertEqual(self.state()["state"], "error")

        self.fire("SessionEnd", reason="exit")
        self.assertFalse(self.state_file.exists())

    def test_compact_session_start_keeps_running(self):
        self.fire("UserPromptSubmit", prompt="hi")
        self.fire("SessionStart", source="compact")
        self.assertEqual(self.state()["state"], "running")

    def test_new_prompt_resets_since(self):
        self.fire("UserPromptSubmit", prompt="1")
        first = self.state()["since"]
        self.fire("Stop")
        self.fire("UserPromptSubmit", prompt="2")
        self.assertGreater(self.state()["since"], first)

    def test_stop_with_background_work(self):
        self.fire("UserPromptSubmit", prompt="실험 돌려줘")
        self.fire("Stop", session_crons=[], background_tasks=[
            {"id": "b1", "type": "shell", "status": "running",
             "description": "Run hybrid\nattempt 3", "command": "python run.py"},
            {"id": "b2", "type": "monitor", "status": "running", "description": "watch log"}])
        s = self.state()
        self.assertEqual((s["state"], s["bg_tasks"], s["bg_desc"], s["crons"]),
                         ("background", 2, "Run hybrid attempt 3", 0))
        self.assertEqual(s["since"], s["ts"], "턴이 끝난 뒤부터 센다")

        # 작업이 끝나 세션이 깨어나면 작업 중 → 완료로 간다
        self.fire("PreToolUse", tool_name="Bash")
        woke = self.state()
        self.assertEqual(woke["state"], "running")
        self.assertGreater(woke["since"], s["since"])
        self.fire("Stop", background_tasks=[], session_crons=[])
        done = self.state()
        self.assertEqual((done["state"], done["since"], done["bg_tasks"]), ("done", woke["since"], 0))

    def test_stop_with_only_scheduled_wakeup(self):
        self.fire("Stop", background_tasks=[], session_crons=[
            {"id": "c1", "schedule": "*/5 * * * *", "recurring": False, "prompt": "check"}])
        s = self.state()
        self.assertEqual((s["state"], s["bg_tasks"], s["bg_desc"], s["crons"]), ("background", 0, "", 1))

    def test_background_label_fallbacks(self):
        self.fire("Stop", background_tasks=[{"id": "b", "type": "shell", "status": "running",
                                             "description": "", "command": "sleep 100"}])
        self.assertEqual(self.state()["bg_desc"], "sleep 100")
        self.fire("Stop", background_tasks=[{"id": "b", "type": "subagent", "status": "running",
                                             "description": "x" * 1000}])
        self.assertEqual(len(self.state()["bg_desc"]), 200)
        self.fire("Stop", background_tasks="garbage", session_crons={"x": 1})
        s = self.state()
        self.assertEqual((s["state"], s["bg_tasks"], s["crons"]), ("done", 0, 0))

    def test_records_terminal_and_keeps_created(self):
        self.env["TERMINATOR_UUID"] = "urn:uuid:1234"
        self.fire("SessionStart", source="startup")
        first = self.state()
        self.assertEqual(first["terminator_uuid"], "urn:uuid:1234")
        self.assertEqual(first["transcript"], "/dev/null")
        self.assertEqual(first["created"], first["ts"])
        self.fire("UserPromptSubmit", prompt="hi")
        self.fire("Stop")
        self.assertEqual(self.state()["created"], first["created"])

    def test_claude_pid_is_a_claude_ancestor(self):
        # 테스트를 Claude Code 안에서 돌리면 조상에 claude 가 있고, 아니면 빈 값이다
        self.fire("UserPromptSubmit", prompt="hi")
        s = self.state()
        if s["claude_pid"]:
            pid = s["claude_pid"]
            self.assertEqual(Path(f"/proc/{pid}/comm").read_text().strip(), "claude")
            self.assertEqual(Path(f"/proc/{pid}/stat").read_text().split()[21], s["claude_start"])
        else:
            self.assertEqual(s["claude_start"], "")

    def test_older_event_does_not_overwrite_newer(self):
        self.fire("Stop")
        data = self.state()
        data["seq"] = str(int(data["seq"]) + 10**15)  # 미래에 시작한 이벤트가 쓴 것처럼
        self.state_file.write_text(json.dumps(data))
        self.fire("PreToolUse", tool_name="Bash")
        self.assertEqual(self.state()["state"], "done")

    def test_unsafe_session_id_is_ignored(self):
        self.fire("UserPromptSubmit", sid="../escape")
        run = Path(self.env["XDG_RUNTIME_DIR"])
        self.assertFalse((run / "escape.json").exists())
        self.assertFalse(list(run.glob("**/*.json")))

    def test_unknown_event_and_bad_json_are_ignored(self):
        self.fire("SubagentStop")
        self.assertFalse(self.state_file.exists())
        result = subprocess.run([str(HOOK)], input="not json", env=self.env,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual((result.returncode, result.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
