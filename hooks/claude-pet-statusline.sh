#!/usr/bin/env bash
# claude-pet 상태줄 중계
#
# settings.json 의 statusLine.command 맨 앞에 붙여 원래 상태줄 명령을 감싼다:
#   "$HOME/.claude/pet/hooks/claude-pet-statusline.sh" <원래 상태줄 명령...>
#
# 상태줄 입력에서 그 세션의 컨텍스트와 계정 사용량(5시간·주간)을
# $XDG_RUNTIME_DIR/claude-pet/usage/<session_id>.json 에 적고, 같은 입력으로 원래 명령을 실행한다.
# 펫(~/.claude/pet/claude_pet.py)이 펫에 마우스를 올렸을 때 이 값을 보여준다.
# 기록이 실패해도 상태줄 출력과 종료 코드는 원래 명령 그대로다.

input=$(cat)

record() {
  local dir="${XDG_RUNTIME_DIR:-/tmp/claude-pet-$(id -u)}/claude-pet/usage"
  local sid
  sid=$(jq -r '.session_id // empty' <<<"$input" 2>/dev/null)
  # session_id 는 파일 이름이 되므로 안전한 글자만 받는다
  [[ $sid =~ ^[A-Za-z0-9_-]+$ ]] || return
  mkdir -p "$dir" 2>/dev/null || return
  local tmp="$dir/$sid.json.tmp.$$"
  # resets_at 은 epoch 초. 값이 없거나 숫자가 아니면 null 로 둔다.
  jq --argjson ts "$(( $(date +%s%N) / 1000000 ))" '
    def num: if type == "number" then . else null end;
    def window(name): [.rate_limits | objects | .[name] | objects] | .[0] // {};
    ([.context_window | objects] | .[0] // {}) as $ctx
    | ([$ctx.current_usage | objects] | .[0]) as $cur
    | {ts: $ts,
       context_pct: ($ctx.used_percentage | num),
       context_size: ($ctx.context_window_size | num),
       context_tokens: (if $cur then [$cur.input_tokens, $cur.cache_creation_input_tokens,
                                      $cur.cache_read_input_tokens] | map(num // 0) | add
                        else null end),
       five_hour_pct: (window("five_hour").used_percentage | num),
       five_hour_reset: (window("five_hour").resets_at | num),
       seven_day_pct: (window("seven_day").used_percentage | num),
       seven_day_reset: (window("seven_day").resets_at | num)}' <<<"$input" >"$tmp" 2>/dev/null \
    && mv -f "$tmp" "$dir/$sid.json"
  rm -f "$tmp"
}
record

(( $# )) || exit 0
exec "$@" <<<"$input"
