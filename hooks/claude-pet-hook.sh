#!/usr/bin/env bash
# Claude Code 펫 상태 훅
#
# 여러 훅 이벤트에 같은 스크립트를 건다. 훅 JSON 을 stdin 으로 받아 세션별
# 상태 파일 $XDG_RUNTIME_DIR/claude-pet/<session_id>.json 을 쓰고,
# 펫(~/.claude/pet/claude_pet.py)이 그 폴더를 감시해 애니메이션을 바꾼다.
#
#   SessionStart                                     -> idle (펫이 안 떠 있으면 띄운다)
#   UserPromptSubmit, PreToolUse, PostToolUse        -> running
#   Notification(권한 요청·질문), PreToolUse(AskUserQuestion, ExitPlanMode) -> waiting
#   Stop                                             -> done
#     (백그라운드 작업이나 깨우기 예약이 남아 있으면)     -> background
#     (아티팩트를 발행하면 붙는 감시 작업은 세션을 깨우지 않으므로 세지 않는다)
#   StopFailure                                      -> error
#   SessionEnd                                       -> 상태 파일 삭제
#
# SessionStart/UserPromptSubmit 의 stdout 은 모델 컨텍스트로 들어가므로 아무것도 출력하지 않는다.

# async 훅은 끝나는 순서가 뒤바뀔 수 있어서, 시작 시각(ns)으로 옛 이벤트가 새 상태를 덮지 않게 한다
seq=$(date +%s%N)
ts=$((seq / 1000000))
input=$(cat)

dir="${XDG_RUNTIME_DIR:-/tmp/claude-pet-$(id -u)}/claude-pet"
pet_app="$HOME/.claude/pet/claude_pet.py"
disabled_flag="${XDG_CONFIG_HOME:-$HOME/.config}/claude-pet/disabled"

US=$'\x1f'
# background_tasks / session_crons 는 Stop 에만 온다. 백그라운드 작업 수, 첫 작업 설명, 깨우기 예약 수만 남긴다.
# 설명에 줄바꿈이 있으면 한 줄 read 가 깨지므로 제어 문자는 공백으로 바꾼다.
# 아티팩트 감시(live updates / presence)는 페이지 변경을 기다리기만 하는 ambient 작업인데, 훅 입력에는
# ambient 표시 없이 "monitor" 로만 와서 Claude Code 가 붙이는 설명 문구로 거른다.
IFS=$US read -r event sid cwd tool ntype source transcript bg_tasks bg_desc crons < <(
  jq -r 'def list(f): f | if type == "array" then . else [] end;
         def task_text: [.description, .name, .agent_type, .command]
                    | map(select(type == "string" and . != "")) | .[0] // "" | .[0:200];
         def artifact_watch: type == "object" and (.description | type == "string"
                    and test("^(live updates for|presence on) artifact "));
         (list(.background_tasks) | map(select(artifact_watch | not))) as $bg
         | [.hook_event_name, .session_id, .cwd, .tool_name, .notification_type, .source, .transcript_path,
          ($bg | length),
          ($bg[0] | if type == "object" then task_text else "" end),
          (list(.session_crons) | length)]
         | map(. // "" | tostring | gsub("[[:cntrl:]]+"; " ")) | join("\u001f")' <<<"$input" 2>/dev/null
)
[[ $bg_tasks =~ ^[0-9]+$ ]] || bg_tasks=0
[[ $crons =~ ^[0-9]+$ ]] || crons=0

# session_id 는 파일 이름이 되므로 안전한 글자만 받는다
[[ $sid =~ ^[A-Za-z0-9_-]+$ ]] || exit 0

case "$event" in
  SessionStart)     state=idle ;;
  UserPromptSubmit) state=running; tool="" ;;
  PreToolUse)
    case "$tool" in
      AskUserQuestion|ExitPlanMode) state=waiting ;;
      *)                            state=running ;;
    esac ;;
  PostToolUse)      state=running; tool="" ;;
  Notification)
    case "$ntype" in
      permission_prompt|elicitation_dialog) state=waiting ;;
      *) exit 0 ;;
    esac ;;
  Stop)
    # 턴은 끝났지만 백그라운드 작업이 끝나거나 예약 시각이 되면 세션이 다시 깨어난다
    if (( bg_tasks > 0 || crons > 0 )); then state=background; else state=done; fi ;;
  StopFailure)      state=error ;;
  SessionEnd)       state="" ;;
  *)                exit 0 ;;
esac

mkdir -p "$dir" 2>/dev/null || exit 0
file="$dir/$sid.json"

# 터미널을 그냥 닫으면 SessionEnd 가 안 올 수 있어서, 펫이 살아 있는지 볼 수 있게
# 이 훅을 띄운 claude 프로세스(조상 중 이름이 claude)와 그 시작 시각을 남긴다
claude_pid="" claude_start=""
p=$PPID
for _ in 1 2 3 4 5; do
  [ -r "/proc/$p/comm" ] || break
  if [ "$(<"/proc/$p/comm")" = claude ]; then
    claude_pid=$p
    claude_start=$(awk '{print $22}' "/proc/$p/stat" 2>/dev/null)
    break
  fi
  p=$(awk '/^PPid:/{print $2}' "/proc/$p/status" 2>/dev/null)
  [[ $p =~ ^[0-9]+$ ]] && (( p > 1 )) || break
done

exec 9>"$dir/.lock"
flock -w 2 9 || exit 0

write_state() {
  if [ -z "$state" ]; then
    rm -f "$file"
    return
  fi

  local since=$ts created=$ts prompt_ts=0 tmp="$file.tmp.$$"
  local old_seq="" old_state="" old_since="" old_created="" old_prompt=""
  if [ -f "$file" ]; then
    IFS=$US read -r old_seq old_state old_since old_created old_prompt < <(
      jq -r '[.seq, .state, .since, .created, .prompt_ts] | map(. // "" | tostring) | join("\u001f")' \
        "$file" 2>/dev/null
    )
    # 펫 줄의 순서는 세션이 처음 생긴 시각으로 고정한다
    [[ $old_created =~ ^[0-9]+$ ]] && created=$old_created
    # 마지막으로 프롬프트를 보낸 시각 (펫의 "나중에 이어서 볼게" 표시는 새 프롬프트가 오면 풀린다)
    [[ $old_prompt =~ ^[0-9]+$ ]] && prompt_ts=$old_prompt
    # 더 나중에 시작한 이벤트가 이미 썼으면 그대로 둔다. 늦게 끝난 프롬프트 이벤트라도 프롬프트 시각은 남긴다.
    if [[ $old_seq =~ ^[0-9]+$ ]] && (( old_seq > seq )); then
      if [ "$event" = UserPromptSubmit ] && (( ts > prompt_ts )); then
        jq --argjson p "$ts" '.prompt_ts = $p' "$file" >"$tmp" 2>/dev/null && mv -f "$tmp" "$file"
        rm -f "$tmp"
      fi
      return
    fi
    # 압축(compact) 뒤 SessionStart 는 작업 중에도 오므로 기존 상태를 지운다고 보지 않는다
    if [ "$event" = SessionStart ] && [ "$source" = compact ]; then
      return
    fi
    # 같은 작업이 이어지는 동안은 시작 시각을 유지한다 (경과 시간, 완료까지 걸린 시간).
    # background 는 턴이 끝난 뒤부터 기다린 시간을 보여주므로 새로 센다.
    if [ "$event" != UserPromptSubmit ] && [ "$state" != idle ] && [ "$state" != background ] \
       && [[ $old_state == running || $old_state == waiting ]] && [[ $old_since =~ ^[0-9]+$ ]]; then
      since=$old_since
    fi
  fi
  [ "$event" = UserPromptSubmit ] && prompt_ts=$ts

  jq -n --arg seq "$seq" --argjson ts "$ts" --argjson since "$since" --argjson created "$created" \
        --arg state "$state" --arg cwd "$cwd" --arg tool "$tool" --arg event "$event" \
        --arg claude_pid "$claude_pid" --arg claude_start "$claude_start" \
        --arg terminator_uuid "${TERMINATOR_UUID:-}" --arg transcript "$transcript" \
        --argjson bg_tasks "$bg_tasks" --arg bg_desc "$bg_desc" --argjson crons "$crons" \
        --argjson prompt_ts "$prompt_ts" \
        '{seq: $seq, ts: $ts, since: $since, created: $created, prompt_ts: $prompt_ts, state: $state, cwd: $cwd,
          tool: $tool, event: $event, claude_pid: $claude_pid, claude_start: $claude_start,
          terminator_uuid: $terminator_uuid, transcript: $transcript,
          bg_tasks: $bg_tasks, bg_desc: $bg_desc, crons: $crons}' \
        >"$tmp" 2>/dev/null && mv -f "$tmp" "$file"
  rm -f "$tmp"
}

write_state
flock -u 9
exec 9>&-

# 펫은 세션보다 오래 살아야 하므로 setsid 로 떼어 띄운다.
# GNOME Wayland 는 앱이 스스로 항상 위에 두는 걸 막아서 XWayland(xcb)로 띄운다.
if [ "$event" = SessionStart ] && [ -n "$DISPLAY" ] && [ ! -e "$disabled_flag" ] && [ -f "$pet_app" ]; then
  if flock -n "$dir/pet.lock" true 2>/dev/null; then
    setsid -f env QT_QPA_PLATFORM=xcb python3 "$pet_app" </dev/null >>"$dir/pet.log" 2>&1
  fi
fi

exit 0
