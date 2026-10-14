#!/usr/bin/env python3
"""작업 요약(작업 중 말풍선 첫 줄의 Haiku 요약)이 쓰는 사용량을 내 Claude Code 기록으로 어림한다.

~/.claude/projects 의 최근 기록을 다시 읽어, 펫이 켜져 있었다면 요약을 몇 번 불렀을지 날짜별로 센다.
펫처럼 짧은 말은 요약하지 않고, 세션마다 요약을 하나씩만 돌리며 도는 동안 온 말은 가장 새 것만 이어서 요약한다.
턴의 마지막 답변(마지막 도구 호출 뒤의 말)은 턴이 끝나며 나와 작업 중 말풍선에 걸리지 않으므로 세지 않는다.

요약 한 번의 비용은 기본값(2026-10-14 측정)을 쓰고, --measure N 을 주면 기록에서 N 개를 골라 실제로 요약해
토큰·비용을 잰다 (이때만 사용량을 쓴다). 세션 비용은 Claude Code 가 기록에 남긴 cost-state(API 요금 기준)다.

    python3 tools/gist_usage.py               # 최근 14일
    python3 tools/gist_usage.py --days 7 --measure 10
"""

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import petcore  # noqa: E402

PROJECTS = Path("~/.claude/projects").expanduser()
GIST_SECONDS = 3.0      # 요약 한 번에 걸리는 시간 (측정 평균 2.8초)
GIST_CALL_USD = 0.00027  # 요약 한 번의 비용 (2026-10-14, Haiku 입력 약 1,270·출력 약 26토큰, 10회 평균)


def day_of(seconds):
    return datetime.fromtimestamp(seconds).date()


def seconds_of(entry):
    try:
        return datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00")).timestamp()
    except (KeyError, AttributeError, ValueError):
        return None


def read_transcript(path):
    """(작업 중 말풍선에 걸렸을 말들 [(초, 말)], 실행별 세션 비용 {(시작 ms, 날짜): $})."""
    said, turn, costs = [], [], {}

    def end_turn():
        last_tool = max((i for i, (_, kind, _) in enumerate(turn) if kind == "tool_use"), default=-1)
        said.extend((t, text) for i, (t, kind, text) in enumerate(turn)
                    if kind == "thinking" or (kind == "text" and i < last_tool))
        turn.clear()

    with open(path, "rb") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("isSidechain"):
                continue
            kind = entry.get("type")
            message = entry.get("message")
            content = message.get("content") if isinstance(message, dict) else None
            if kind == "cost-state":
                start = entry.get("startTime") or 0
                costs[(start, day_of(start / 1000))] = float(entry.get("totalCostUSD") or 0)
            elif kind == "system" and entry.get("subtype") in ("turn_duration", "stop_hook_summary"):
                end_turn()
            elif kind == "user" and isinstance(content, str):
                end_turn()
            elif kind == "assistant" and isinstance(content, list):
                t = seconds_of(entry)
                for block in content:
                    block_kind = block.get("type") if isinstance(block, dict) else None
                    text = block.get(block_kind) if block_kind in ("text", "thinking") else ""
                    if block_kind == "tool_use":
                        turn.append((t, "tool_use", ""))
                    elif t and isinstance(text, str) and text.strip():
                        turn.append((t, block_kind, text.strip()))
    end_turn()
    return said, costs


def gist_calls(said):
    """요약을 부르는 말들. 도는 동안(GIST_SECONDS) 온 말은 가장 새 것만 끝난 뒤 이어서 부른다."""
    calls, busy_until, pending = [], float("-inf"), None
    for t, text in sorted(said):
        if not petcore.needs_gist(text):
            continue
        while pending and busy_until <= t:
            calls.append(pending)
            busy_until += GIST_SECONDS
            pending = None
        if t >= busy_until:
            calls.append((t, text))
            busy_until = t + GIST_SECONDS
        else:
            pending = (t, text)
    if pending:
        calls.append(pending)
    return calls


def measure(samples):
    """실제로 요약해 평균 비용을 잰다. 요약 실행이 훅을 불렀는지 보려고 펫 상태 폴더를 임시 폴더로 돌린다."""
    claude = shutil.which("claude") or str(Path("~/.local/bin/claude").expanduser())
    rows = []
    with tempfile.TemporaryDirectory() as runtime:
        env = {**os.environ, "XDG_RUNTIME_DIR": runtime}
        for _, text in samples:
            args, stdin = petcore.gist_command(text, claude)
            start = time.time()
            done = subprocess.run(args + ["--output-format", "json"], input=stdin, capture_output=True, text=True,
                                  env=env, cwd=Path.home(), timeout=60)
            try:
                result = json.loads(done.stdout)
            except ValueError:
                print(f"  실패 (exit {done.returncode}): {done.stderr.strip()[-200:]}")
                continue
            usage = result.get("usage") or {}
            tokens_in = sum(usage.get(k) or 0 for k in
                            ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            tokens_out = usage.get("output_tokens") or 0
            cost = float(result.get("total_cost_usd") or 0)
            rows.append((time.time() - start, tokens_in, tokens_out, cost))
            print(f"  {rows[-1][0]:4.1f}초 입력 {tokens_in} 출력 {tokens_out} ${cost:.6f}  "
                  f"{len(text)}자 → {petcore.clean_gist(result.get('result'))}")
        traces = [str(p) for p in Path(runtime).rglob("*") if p.is_file()]
    if not rows:
        return None
    n = len(rows)
    print(f"  평균 {sum(r[0] for r in rows) / n:.1f}초, 입력 {sum(r[1] for r in rows) / n:.0f}토큰, "
          f"출력 {sum(r[2] for r in rows) / n:.0f}토큰, ${sum(r[3] for r in rows) / n:.6f}/회")
    print("  요약 실행이 남긴 훅 흔적:", traces or "없음")
    return sum(r[3] for r in rows) / n


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--measure", type=int, default=0, metavar="N", help="기록에서 N 개를 실제로 요약해 비용을 잰다")
    args = parser.parse_args()

    since = time.time() - args.days * 86400
    calls_by_day, cost_by_day, all_calls = defaultdict(int), defaultdict(float), []
    files = [p for p in PROJECTS.glob("*/*.jsonl") if p.stat().st_mtime >= since]
    for path in files:
        said, costs = read_transcript(path)
        for t, text in gist_calls(said):
            if t >= since:
                calls_by_day[day_of(t)] += 1
                all_calls.append((t, text))
        for (start, day), cost in costs.items():
            if start / 1000 >= since:
                cost_by_day[day] += cost

    call_usd = GIST_CALL_USD
    if args.measure and all_calls:
        print(f"요약 {args.measure}번 실제 측정:")
        call_usd = measure(random.Random(0).sample(all_calls, min(args.measure, len(all_calls)))) or call_usd
        print()

    print(f"기록 파일 {len(files)}개, 최근 {args.days}일, 요약 한 번 ${call_usd:.6f}")
    print("날짜        요약 호출  요약 비용($)  세션 비용($)  세션 대비")
    for day in sorted(set(calls_by_day) | set(cost_by_day)):
        calls, session = calls_by_day[day], cost_by_day[day]
        ratio = f"{calls * call_usd / session:.2%}" if session else "-"
        print(f"{day}  {calls:9d}  {calls * call_usd:12.4f}  {session:12.2f}  {ratio:>8}")
    busy_days = [d for d in calls_by_day if calls_by_day[d]]
    total_calls, total_session = sum(calls_by_day.values()), sum(cost_by_day.values())
    if busy_days:
        print(f"합계 요약 {total_calls}번 (요약한 날 {len(busy_days)}일, 하루 평균 {total_calls / len(busy_days):.0f}번, "
              f"가장 많은 날 {max(calls_by_day.values())}번), 요약 비용 ${total_calls * call_usd:.2f}")
    if total_session:
        print(f"세션 비용 대비 {total_calls * call_usd / total_session:.2%}")


if __name__ == "__main__":
    main()
