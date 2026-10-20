#!/usr/bin/env python3
"""claude-pet 확인용 가짜 세션과 가짜 계정 사용량. hook 이 쓰는 것과 같은 상태 파일만 쓴다.

claude_pid 가 없어서 펫은 살아 있는 세션으로 본다 (12시간 동안 바뀌지 않으면 사라진다).
시험 모드(tools/pet_test_mode.sh on)가 켜져 있으면 시험용 상태 폴더에 쓴다.

    fake_session.py start NAME [--ago-min N]      새 세션 (쉬는 중). --ago-min 이면 N분 전부터 쉬던 세션
    fake_session.py set NAME STATE [--since-min N]
                                                 상태 바꾸기: idle running waiting done error background
                                                 --since-min: 그 일이 N분 전에 시작됨 (done 이면 N분 걸린 일).
                                                 running 은 새 프롬프트로 친다 (끝낸 일은 프롬프트마다 한 번 센다)
    fake_session.py end NAME                     세션 끝 (상태 파일 지움)
    fake_session.py usage 5H WEEK [--reset5-min N] [--reset7-min N]
                                                 계정 사용량 (5시간·주간 사용 %). 리셋은 기본으로 다음 5시간·7일
                                                 경계 (몇 번 불러도 같은 리셋 주기다), 주면 지금부터 N분 뒤.
                                                 리셋을 흉내 내려면 리셋 시각을 앞당기지 말고 (펫이 지난 주기의
                                                 옛 기록으로 보고 무시한다) 더 늦은 리셋의 새 값을 준다
    fake_session.py tokens NAME N                출력 토큰 N개를 쓴 응답 하나를 가짜 기록 파일에 남긴다 (끝없이 오르기에서
                                                 1,000개 = 0.6m). 시험 모드에서만 된다 (펫이 시험용 기록 폴더를 읽는다)
    fake_session.py clear                        가짜 세션·사용량·기록 모두 지움
"""
import json
import os
import sys
import time
from pathlib import Path

RUNTIME = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/claude-pet-{os.getuid()}")
TEST_RUN = RUNTIME / "claude-pet-test"  # pet_test_mode.sh on 이 만든다
DIR = (TEST_RUN if (TEST_RUN / "ON").exists() else RUNTIME) / "claude-pet"
USAGE = DIR / "usage" / "fake-usage.json"
PROJECTS = TEST_RUN / "projects"  # 시험 모드의 Claude Code 기록 폴더 (펫이 CLAUDE_PET_PROJECTS 로 읽는다)
TRANSCRIPTS = PROJECTS / "-fake"
# 진짜 hook 처럼 상태에 맞는 이벤트 이름을 남긴다 (펫은 다음 hook 이 새 프롬프트인지 보고 작업 시간을 확정한다)
EVENTS = {"idle": "SessionStart", "running": "PreToolUse", "waiting": "Notification", "done": "Stop",
          "error": "StopFailure", "background": "Stop"}


def path(name):
    return DIR / f"fake-{name}.json"


def write(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(target)  # hook 처럼 바꿔 끼워야 펫의 폴더 감시가 바로 알아챈다


def option(args, flag, default=0.0):
    if flag in args:
        return float(args[args.index(flag) + 1])
    return default


def main(argv):
    if not argv or argv[0] not in ("start", "set", "end", "usage", "tokens", "clear"):
        print(__doc__)
        return 1
    now = int(time.time() * 1000)
    cmd = argv[0]
    if cmd == "start":
        ago = int(option(argv, "--ago-min") * 60_000)
        write(path(argv[1]), {"seq": str(time.time_ns()), "ts": now - ago, "since": now - ago,
                              "created": now - ago, "prompt_ts": 0, "state": "idle", "cwd": f"/tmp/{argv[1]}",
                              "tool": "", "event": "SessionStart", "bg_tasks": 0, "bg_desc": "", "crons": 0})
    elif cmd == "set":
        name, state = argv[1], argv[2]
        if state not in EVENTS:
            print(f"모르는 상태: {state}")
            return 1
        data = json.loads(path(name).read_text())
        since = now - int(option(argv, "--since-min") * 60_000)
        data.update(seq=str(time.time_ns()), ts=now, since=since, state=state, event=EVENTS[state],
                    tool="Bash" if state == "running" else "")
        if state == "running":
            data["prompt_ts"] = since
        write(path(name), data)
    elif cmd == "end":
        path(argv[1]).unlink(missing_ok=True)
    elif cmd == "usage":
        now_s = now // 1000

        def reset(flag, period_s):  # 기본은 다음 경계라서 불러도 같은 주기로 남는다
            return now_s + int(option(argv, flag) * 60) if flag in argv else (now_s // period_s + 1) * period_s

        write(USAGE, {"ts": now, "five_hour_pct": float(argv[1]), "five_hour_reset": reset("--reset5-min", 5 * 3600),
                      "seven_day_pct": float(argv[2]), "seven_day_reset": reset("--reset7-min", 7 * 86400)})
    elif cmd == "tokens":
        if not (TEST_RUN / "ON").exists():
            print("tokens 는 시험 모드에서만 돼요 (tools/pet_test_mode.sh on)")
            return 1
        TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now / 1000)) + f".{now % 1000:03d}Z"
        line = {"type": "assistant", "timestamp": stamp, "requestId": f"req_fake_{time.time_ns()}",
                "message": {"id": f"msg_fake_{time.time_ns()}", "role": "assistant",
                            "usage": {"input_tokens": 1, "output_tokens": int(argv[2])}}}
        with open(TRANSCRIPTS / f"{argv[1]}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        print(f"tokens: {TRANSCRIPTS / argv[1]}.jsonl +{int(argv[2]):,}")
        return 0
    else:
        for p in DIR.glob("fake-*.json"):
            p.unlink()
        USAGE.unlink(missing_ok=True)
        for p in TRANSCRIPTS.glob("*.jsonl"):
            p.unlink()
    print(f"{cmd}: {DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
