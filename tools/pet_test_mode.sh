#!/usr/bin/env bash
# claude-pet 시험 모드: 펫을 시험용 상태 폴더로 다시 띄워 가짜 세션·가짜 사용량(tools/fake_session.py)만 보이게 한다.
# 실제 세션 펫은 끌 때까지 안 보이고, 경험치·클라이밍 기록과 설정(줄 위치, 메뉴 토글)도 시험용 사본에 쌓여서
# 실제 기록과 설정은 그대로다.
# 시험 모드 동안 새 Claude 세션을 열면 실제 펫이 하나 더 뜰 수 있다.
#
#   pet_test_mode.sh on        시험용 폴더를 새로 만들고 시험 모드로 다시 띄운다
#   pet_test_mode.sh restart   시험용 폴더 그대로 다시 띄운다 (다시 켜도 기록이 남는지 보기)
#   pet_test_mode.sh off       실제 상태로 다시 띄우고 시험용 폴더를 지운다
#
# 시험용 폴더: $XDG_RUNTIME_DIR/claude-pet-test (상태: claude-pet/, 기록: data/, 설정 사본: config/,
# 가짜 Claude Code 기록: projects/ — 끝없이 오르기가 실제 토큰 대신 fake_session.py tokens 로 쓴 토큰을 센다),
# 로그는 실제 pet.log 에 이어 쓴다.
set -u
APP="$(cd "$(dirname "$0")/.." && pwd)/claude_pet.py"
RUNTIME="${XDG_RUNTIME_DIR:-/tmp/claude-pet-$(id -u)}"
TEST="$RUNTIME/claude-pet-test"
LOG="$RUNTIME/claude-pet/pet.log"

# 내 계정의 펫 실행만 맞춘다 (hook 이 띄운 ~/.claude/pet/claude_pet.py 와 저장소의 claude_pet.py 둘 다)
PET='^python3 (\S*/)?claude_pet\.py$'

stop() {
  # SIGTERM 을 받으면 다음 refresh(1초 안)에 기록을 저장하고 끝난다. 끝나야 새 펫이 단일 실행 잠금을 얻는다
  for pid in $(pgrep -u "$(id -u)" -f "$PET"); do kill "$pid"; done
  for _ in $(seq 1 50); do pgrep -u "$(id -u)" -f "$PET" >/dev/null || return 0; sleep 0.1; done
  echo "펫이 5초 안에 끝나지 않았어요" >&2
  return 1
}

start() {
  mkdir -p "$(dirname "$LOG")"
  setsid -f env QT_QPA_PLATFORM=xcb "$@" python3 "$APP" </dev/null >>"$LOG" 2>&1
  sleep 2
  pgrep -u "$(id -u)" -f "$PET" >/dev/null || { echo "펫이 뜨지 않았어요: $LOG 확인" >&2; return 1; }
}

case "${1:-}" in
  on)
    stop || exit 1
    rm -rf "$TEST"
    mkdir -p "$TEST/claude-pet" "$TEST/data" "$TEST/config/claude-pet" "$TEST/projects"
    chmod 700 "$TEST"
    CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/claude-pet/config.json"
    [ -f "$CONFIG" ] && cp "$CONFIG" "$TEST/config/claude-pet/config.json"  # 크기·줄 위치는 지금 그대로
    touch "$TEST/ON"
    start XDG_RUNTIME_DIR="$TEST" XDG_DATA_HOME="$TEST/data" XDG_CONFIG_HOME="$TEST/config" CLAUDE_PET_PROJECTS="$TEST/projects" \
      && echo "시험 모드로 실행 중 ($TEST)" ;;
  restart)
    [ -e "$TEST/ON" ] || { echo "시험 모드가 아니에요 (먼저 on)" >&2; exit 1; }
    stop || exit 1
    start XDG_RUNTIME_DIR="$TEST" XDG_DATA_HOME="$TEST/data" XDG_CONFIG_HOME="$TEST/config" CLAUDE_PET_PROJECTS="$TEST/projects" \
      && echo "시험 모드로 다시 실행 중" ;;
  off)
    stop || exit 1
    rm -rf "$TEST"
    start && echo "실제 상태로 실행 중" ;;
  *)
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 1 ;;
esac
