#!/usr/bin/env bash
# claude-pet 설치·점검. 여러 번 실행해도 된다.
#
# 코드는 ~/.claude/pet 경로만 안다. 이 저장소를 그 자리에 clone 했으면 그대로 쓰고,
# 다른 곳에 두었으면 ~/.claude/pet 을 이 저장소로 링크한다.
#
# 하는 일
#   1. ~/.claude/pet 링크 (다른 것이 있으면 건드리지 않고 알린다)
#   2. GNOME 확장 claude-pet-focus 링크 (펫 클릭 → 터미널 창 올리기)
#   3. 필요한 프로그램 확인
#   4. Claude Code settings.json 에 hook·상태줄이 등록돼 있는지 확인 (고치지 않는다, README.md 참고)

set -u

repo=$(cd "$(dirname "$(readlink -f "$0")")" && pwd -P)
target="$HOME/.claude/pet"
problems=0

ok()   { printf '  ok    %s\n' "$*"; }
done_() { printf '  만듦  %s\n' "$*"; }
note() { printf '  참고  %s\n' "$*"; }
warn() { printf '  !!    %s\n' "$*"; problems=$((problems + 1)); }

echo "1. ~/.claude/pet"
if [ -e "$target" ] || [ -L "$target" ]; then
  if [ "$(readlink -f "$target")" = "$repo" ]; then
    ok "~/.claude/pet -> $repo"
  else
    warn "~/.claude/pet 이 이미 다른 곳($(readlink -f "$target"))이다. 옮기거나 지운 뒤 다시 실행"
  fi
else
  mkdir -p "$HOME/.claude" && ln -s "$repo" "$target" && done_ "~/.claude/pet -> $repo"
fi

echo "2. GNOME 확장 (펫 클릭 → 터미널 창 올리기)"
uuid=claude-pet-focus@claude-pet
ext_dir="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions"
ext_src="$target/gnome-extension/$uuid"  # 저장소 위치가 아니라 ~/.claude/pet 경로로 건다
if ! command -v gnome-shell >/dev/null 2>&1; then
  note "GNOME Shell 이 없어 건너뜀 (펫 클릭으로 창 올리기는 안 된다)"
else
  link="$ext_dir/$uuid"
  if [ -L "$link" ] && [ "$(readlink "$link")" = "$ext_src" ]; then
    ok "$link"
  elif [ -e "$link" ] || [ -L "$link" ]; then
    warn "$link 에 다른 것이 있다"
  else
    mkdir -p "$ext_dir" && ln -s "$ext_src" "$link" && done_ "$link -> $ext_src"
  fi
  if gsettings get org.gnome.shell enabled-extensions 2>/dev/null | grep -q "'$uuid'"; then
    ok "켜짐 목록(enabled-extensions)에 있음 (새로 넣었으면 다시 로그인해야 켜진다)"
  else
    note "확장 켜기: gnome-extensions enable $uuid (Wayland 는 다시 로그인한 뒤에 된다)"
  fi
fi

echo "3. 필요한 프로그램"
for cmd in python3 jq flock setsid; do
  if command -v "$cmd" >/dev/null 2>&1; then ok "$cmd"; else warn "$cmd 없음"; fi
done
if python3 -c 'import PyQt5.QtWidgets, PyQt5.QtDBus' 2>/dev/null; then ok "PyQt5"
else warn "PyQt5 없음 (Ubuntu: sudo apt install python3-pyqt5)"; fi
if python3 -c 'from PIL import features; assert features.check("webp")' 2>/dev/null; then ok "Pillow (WebP)"
else warn "Pillow 또는 WebP 지원 없음 (Ubuntu: sudo apt install python3-pil)"; fi

echo "4. Claude Code 설정"
settings="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
hook_cmd='"$HOME/.claude/pet/hooks/claude-pet-hook.sh"'
relay_cmd='"$HOME/.claude/pet/hooks/claude-pet-statusline.sh"'
if [ ! -f "$settings" ]; then
  warn "$settings 이 없다 (README.md 의 '설정' 참고)"
else
  missing=()
  for event in SessionStart UserPromptSubmit PreToolUse PostToolUse Notification Stop StopFailure SessionEnd; do
    jq -e --arg ev "$event" --arg cmd "$hook_cmd" \
      '(.hooks[$ev] // []) | any(.[]; (.hooks // []) | any(.[]; .command == $cmd))' "$settings" >/dev/null 2>&1 \
      || missing+=("$event")
  done
  if [ ${#missing[@]} -eq 0 ]; then ok "hook 8개 등록됨"
  else warn "hook 이 빠진 이벤트: ${missing[*]} (README.md 의 '설정' 참고)"; fi
  if jq -e --arg cmd "$relay_cmd" '(.statusLine.command // "") | startswith($cmd)' "$settings" >/dev/null 2>&1; then
    ok "상태줄 중계 등록됨"
  else
    note "상태줄 중계가 없어 마우스 올림 카드의 사용량이 비어 보인다 (README.md 의 '설정' 참고)"
  fi
fi

echo
if [ "$problems" -eq 0 ]; then
  echo "모두 정상"
else
  echo "확인할 것 ${problems}개"
  exit 1
fi
