"""hooks/claude-pet-statusline.sh 에 예시 상태줄 입력을 넣고 사용량 파일과 중계 결과를 확인한다."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

RELAY = Path(__file__).resolve().parent.parent / "hooks/claude-pet-statusline.sh"

FULL = {
    "session_id": "abc-123",
    "model": {"id": "claude-opus-5-5", "display_name": "Opus"},
    "context_window": {
        "context_window_size": 200000, "used_percentage": 62, "remaining_percentage": 38,
        "current_usage": {"input_tokens": 10, "output_tokens": 5,
                          "cache_creation_input_tokens": 4000, "cache_read_input_tokens": 120000},
    },
    "rate_limits": {
        "five_hour": {"used_percentage": 23.5, "resets_at": 1791500000},
        "seven_day": {"used_percentage": 41, "resets_at": 1791800000},
    },
}


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = dict(os.environ, XDG_RUNTIME_DIR=self.tmp.name)
        self.usage_dir = Path(self.tmp.name) / "claude-pet/usage"

    def tearDown(self):
        self.tmp.cleanup()

    def run_relay(self, stdin, *command):
        return subprocess.run([str(RELAY), *command], input=stdin, env=self.env,
                              capture_output=True, text=True, timeout=10)

    def usage(self, sid="abc-123"):
        return json.loads((self.usage_dir / f"{sid}.json").read_text())

    def test_records_usage(self):
        self.run_relay(json.dumps(FULL))
        u = self.usage()
        self.assertIsInstance(u.pop("ts"), int)
        self.assertEqual(u, {"context_pct": 62, "context_size": 200000, "context_tokens": 124010,
                             "five_hour_pct": 23.5, "five_hour_reset": 1791500000,
                             "seven_day_pct": 41, "seven_day_reset": 1791800000})

    def test_passes_input_output_and_exit_code_through(self):
        script = "import sys; d = sys.stdin.read(); print('hud', len(d.strip())); sys.exit(3)"
        payload = json.dumps(FULL)
        result = self.run_relay(payload, "python3", "-c", script)
        self.assertEqual((result.returncode, result.stdout), (3, f"hud {len(payload)}\n"))
        echoed = self.run_relay(payload, "cat")
        self.assertEqual(json.loads(echoed.stdout), FULL)

    def test_missing_or_odd_fields_become_null(self):
        self.run_relay(json.dumps({"session_id": "abc-123", "context_window": {"used_percentage": None},
                                   "rate_limits": {"five_hour": "x", "seven_day": {"used_percentage": "9"}}}))
        u = self.usage()
        del u["ts"]
        self.assertEqual(set(u.values()), {None})

    def test_bad_input_still_reaches_statusline(self):
        result = self.run_relay("not json", "cat")
        self.assertEqual((result.returncode, result.stdout), (0, "not json\n"))
        self.assertFalse(self.usage_dir.exists())

    def test_unsafe_session_id_is_ignored(self):
        self.run_relay(json.dumps({**FULL, "session_id": "../escape"}))
        self.assertFalse(list(Path(self.tmp.name).glob("**/*.json")))

    def test_no_command_prints_nothing(self):
        result = self.run_relay(json.dumps(FULL))
        self.assertEqual((result.returncode, result.stdout), (0, ""))


if __name__ == "__main__":
    unittest.main()
