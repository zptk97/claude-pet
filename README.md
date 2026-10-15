# claude-pet

Claude Code 세션마다 펫 한 마리를 바탕화면 위에 띄워 작업 상태를 보여준다.
펫 그림은 [Codex 펫](https://petdex.dev) 형식(스프라이트 시트)을 그대로 쓴다.

| 세션 상태 | 펫 | 말풍선 |
|---|---|---|
| 쉬는 중 | idle | 없음 |
| 작업 중 | running | `NYU 100장 로봇 실행 시작` / `Bash 실행 중 · 1분 23초` (흰색, 두 줄) |
| 확인 필요 (권한·질문) | waiting | `확인이 필요해요!` (노랑) |
| 다 했어요 | waving | `다 했어요! (2분 13초)` (초록) |
| 백그라운드 작업 기다림 | review | `백그라운드: 실험 이름 · 12분` (파랑) |
| 오류로 멈춤 | failed | `오류로 멈췄어요` (빨강) |

**작업 중 말풍선 첫 줄**: Claude 가 마지막 프롬프트 뒤에 마지막으로 남긴 말이나 생각(접혀 보이는 Thinking)을
기록 파일에서 읽어 Haiku 로 한 줄 요약한다 (`claude -p --model haiku`, 2~3초). 요약이 올 때까지는 지난 요약이나
그 말의 끝 문장을 보여주고, 짧은 말은 요약 없이 그대로 쓴다. 요약 실행은 설정 파일·훅·도구·MCP 를 끄고 세션 기록을
남기지 않아서 펫이나 알림 훅이 다시 불리지 않는다. 연달아 실패하면(로그인 문제 등) 요약을 끄고 끝 문장만 보여준다.

**사용량**: 요약 한 번에 약 $0.00027 (Haiku 입력 약 1,270·출력 약 26토큰). 개발자 기록(2026-10-01~14)으로는
하루 평균 227번, 세션 비용의 약 0.07%(가장 많은 날 0.38%)였다. 자세한 측정과 내 기록으로 재는 법은
[docs/gist-usage.md](docs/gist-usage.md). 신경 쓰이면 오른쪽 클릭 메뉴에서 **작업 요약** 을 끄면
Haiku 를 부르지 않고 말의 끝 문장만 보여준다.

**나중에 이어서 볼게**: 다른 세션을 보러 가기 전에 펫 메뉴에서 눌러 두면 그 펫이 보라색 말풍선
`이어서 봐 주세요!`(둘째 줄은 지금 상태)와 책갈피로 기다린다. 그 펫을 클릭해 돌아가거나, 그 세션에 새 프롬프트를
보내거나, `표시 지우기`를 누르면 풀린다. 접힌 상태에서는 확인 필요·오류 다음으로 먼저 보여준다.

- **왼쪽 클릭**: 그 세션의 터미널 창을 앞으로 올리고, 완료·오류 표시를 확인 처리한다. 빠르게 다섯 번 누르면 `그만 눌러요!`.
- **마우스 올림**: 세션 컨텍스트 사용량과 계정 5시간·주간 사용량, 리셋 시각을 보여준다.
- **드래그**: 펫 줄 전체를 옮긴다. 옮기는 펫은 끄는 쪽으로 달리고, 멈추면 선다. **펫 따로 움직이기**를 켜면 그 펫만 옮기고, 옮긴 자리는 세션별로 기억한다
  (안 옮긴 펫들은 줄에 남아 빈칸을 채운다). 접기 중에는 줄로 선다.
- **휠 버튼 드래그**: 펫을 들고 다닌다. 끄는 쪽으로 달리고, 가만히 들면 버둥거리고, 좌우로 세게 흔들면
  `어지러워요!`. 놓으면 자리로 통통 돌아와서 살살 다뤘으면 웃고(`또 해 주세요!`) 세게 흔들었으면 운다(`흑흑…`).
  빠르게 휙 놓으면 `으아아!` 하고 날아가 모니터 가장자리(위·아래·양옆)에 튕기다가 화면 바닥에 서서 `아야!`,
  그다음 자리로 달려 돌아와 웃거나 운다. 날고 있는 펫은 휠 버튼으로 다시 잡을 수 있다.
- **휠 굴리기**: 펫을 쓰다듬는다. 한 칸마다 하트가 떠오르고, 빠르게 세 칸 굴리면 `헤헤`.
- **오른쪽 클릭**: 나중에 이어서 볼게, 세션별 펫 고르기, 펫 다시 읽기, 펫 켜고 끄기(끈 펫은 자동 배정에서 빠진다), 크기, 접기(손이 필요한 세션 하나만 보기), 펫 따로 움직이기(켜면 `다시 줄 세우기`), 펫이 혼자 움직이기, 작업 요약 켜고 끄기, 자동 실행, 종료.

**펫이 혼자 움직이기** (메뉴에서 끌 수 있다): 새 세션의 펫은 화면의 넓은 쪽(줄이 오른쪽 아래면 왼쪽)에서 서서히 나타나며
자리로 달려와 `안녕하세요!`, 세션이 끝나면 `수고했어요!` 하고 넓은 쪽으로 달리며 사라진다. 모니터가 여럿이면 화면 밖이 옆
모니터라서 같은 화면 안에서 펫 폭의 세 배쯤 달린다. 접기로 숨거나 다시 보이는 펫, 접혀 있을 때 끝난 세션은 그냥 사라지고 나타난다.
쉬는 펫은 2분쯤 지나면 가끔(40초~2분마다) 옆으로 조금 걸어갔다 돌아온다. 옆 펫과 겹치면 그 펫 뒤로 지나가서
앞에 선 펫이 클릭을 그대로 받는다. 접혀 있을 때는 걷지 않는다. 일이 생기면 서둘러 자리로 돌아온다.

**졸기**: 15분 넘게 쉬면 느리게 숨 쉬며 `z Z Z` 를 단다. 클릭하거나 쓰다듬거나 일이 생기면 깨서 한 번 뛴다.
쉬는 시간은 마지막 작업이나 손길(클릭, 쓰다듬기, 들기)부터 센다. 다 했어요·확인 필요처럼 손이 필요한 펫과
나중에 이어서 볼 펫은 졸거나 산책하지 않는다.

**긴 작업 축하**: 10분 넘게 걸린 일이 끝나면 세 번 뛰며 색종이를 뿌린다.

**펫끼리**: 어떤 펫이 다 했어요가 되면 400px 안에서 가장 가까운, 깬 채 쉬는 펫이 `잘했어!` 하고 뛴다. 오류가 나면 그 펫이
옆으로 찾아가 `괜찮아요?` 하고 돌아온다. 산책할 때가 되면 가끔 가까이 있는 깬 펫에게 다가가 서로 `안녕!`.
찾아갈 때도 가는 길의 펫들 뒤로 지나간다. 높이가 다른 펫(따로 움직이기)에게는 걸어가지 않고 제자리에서 말하고,
접혀 있으면 하지 않는다. **펫이 혼자 움직이기**를 끄면 위로는 제자리에서 하고 인사하러 가지는 않는다 (축하는 그대로).

## 구성

```
claude_pet.py      펫 창 (PyQt5)
petcore.py         Qt 없는 로직: 상태 계산, 말풍선 문구, 펫 배정, 사용량 카드, 접기
hooks/
  claude-pet-hook.sh        Claude Code hook → 세션별 상태 파일, 새 세션에서 펫 자동 실행
  claude-pet-statusline.sh  상태줄 중계 → 컨텍스트·사용량 파일 (원래 상태줄 출력은 그대로)
gnome-extension/claude-pet-focus@claude-pet/   펫 클릭 시 창 올리기, 메뉴 밖 클릭 알리기 (GNOME Shell 확장)
pets/              직접 만든 펫
pets-downloaded/   Petdex 등에서 받은 펫 (git 제외)
tools/             펫 만들기 도구 (docs/making-pets.md), 작업 요약 사용량 재기 gist_usage.py (docs/gist-usage.md)
tests/             python3 -m unittest discover -s tests
install.sh         설치·점검
```

실행 중에 쓰는 파일: `$XDG_RUNTIME_DIR/claude-pet/` (세션 상태, 사용량, 로그), 설정은 `~/.config/claude-pet/config.json`.

## 설치

확인한 환경: Ubuntu 24.04, GNOME 46 (Wayland), Terminator. 펫 창은 항상 위에 두려고 XWayland 로 뜬다.

1. 필요한 것: `python3`, PyQt5, Pillow(WebP), `jq`, `flock`

   ```bash
   sudo apt install python3-pyqt5 python3-pil jq
   ```

2. 저장소를 `~/.claude/pet` 에 clone 하거나, 다른 곳에 두고 `install.sh` 로 링크한다.
   코드는 `~/.claude/pet` 경로만 안다.

   ```bash
   git clone <저장소> ~/somewhere/claude-pet
   ~/somewhere/claude-pet/install.sh
   ```

3. Claude Code `~/.claude/settings.json` 에 아래 [설정](#설정)을 넣는다. `install.sh` 를 다시 돌리면 빠진 것을 알려준다.
4. GNOME 확장을 켠다. Wayland 에서는 새 확장이 다시 로그인한 뒤에 보인다.

   ```bash
   gnome-extensions enable claude-pet-focus@claude-pet   # 다시 로그인한 뒤
   ```

   확장이 없으면 펫 클릭으로 창을 올리지 못하고, 펫 메뉴도 다른 창을 눌러서는 닫히지 않는다
   (GNOME 은 XWayland 창이 화면 전체 클릭을 잡지 못하게 막아서, 확장이 클릭 위치를 알려 준다).
   확장 코드를 바꾼 뒤에도 다시 로그인해야 새 코드가 적용된다.

5. 새 Claude Code 세션을 열면 펫이 뜬다. 직접 띄우려면 `python3 ~/.claude/pet/claude_pet.py`.

## 설정

hook 은 아래 8개 이벤트에 같은 스크립트를 건다. 이미 다른 hook 이 있으면 그 배열에 항목을 더한다.
SessionStart 와 UserPromptSubmit hook 의 출력은 모델 컨텍스트로 들어가므로 스크립트는 아무것도 출력하지 않는다.

```json
{
  "hooks": {
    "SessionStart":     [{"hooks": [{"type": "command", "command": "\"$HOME/.claude/pet/hooks/claude-pet-hook.sh\"", "async": true}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "\"$HOME/.claude/pet/hooks/claude-pet-hook.sh\"", "async": true}]}],
    "PreToolUse":       [{"hooks": [{"type": "command", "command": "\"$HOME/.claude/pet/hooks/claude-pet-hook.sh\"", "async": true}]}],
    "PostToolUse":      [{"hooks": [{"type": "command", "command": "\"$HOME/.claude/pet/hooks/claude-pet-hook.sh\"", "async": true}]}],
    "Notification":     [{"hooks": [{"type": "command", "command": "\"$HOME/.claude/pet/hooks/claude-pet-hook.sh\"", "async": true}]}],
    "Stop":             [{"hooks": [{"type": "command", "command": "\"$HOME/.claude/pet/hooks/claude-pet-hook.sh\"", "async": true}]}],
    "StopFailure":      [{"hooks": [{"type": "command", "command": "\"$HOME/.claude/pet/hooks/claude-pet-hook.sh\"", "async": true}]}],
    "SessionEnd":       [{"hooks": [{"type": "command", "command": "\"$HOME/.claude/pet/hooks/claude-pet-hook.sh\""}]}]
  }
}
```

마우스 올림 카드의 사용량은 Claude Code 가 상태줄 프로그램에 넘겨주는 값을 쓴다.
쓰던 상태줄 명령 **앞에** 중계 스크립트를 붙인다 (중계는 같은 입력으로 원래 명령을 실행하므로 상태줄은 그대로다).

```json
{
  "statusLine": {
    "type": "command",
    "command": "\"$HOME/.claude/pet/hooks/claude-pet-statusline.sh\" <원래 상태줄 명령>"
  }
}
```

상태줄을 쓰지 않았다면 원래 명령 없이 중계 스크립트만 둬도 된다 (상태줄은 빈 채로 사용량만 기록한다).

## 펫

`pets/` → `pets-downloaded/` → `~/.codex/pets` 순서로 찾는다 (같은 id 는 앞 폴더가 이긴다).
세션마다 다른 펫이 자동으로 배정되고, 오른쪽 클릭 메뉴에서 바꿀 수 있다.
**펫 켜고 끄기** 에서 끈 펫은 배정에서 빠지고, 그 펫을 쓰던 세션은 바로 켜진 펫으로 바뀐다 (마지막 한 마리는 끌 수 없다).

- 받기: [petdex.dev](https://petdex.dev) 에서 `pet.json` 과 `spritesheet.webp` 를 받아 `pets-downloaded/<id>/` 에 넣는다.
- 만들기: [docs/making-pets.md](docs/making-pets.md)

## 끄기

- 새 세션에서 자동으로 뜨지 않게: 펫 오른쪽 클릭 → "새 Claude 세션에서 자동 실행" 해제
- 완전히 빼기: settings.json 에서 위 hook 과 상태줄 중계를 지우고 `~/.claude/pet` 링크와 GNOME 확장 링크를 지운다.

## 출처

- `tools/hatch-pet/`: [openai/skills](https://github.com/openai/skills) 의 hatch-pet (Apache-2.0, `tools/hatch-pet/SOURCE.md`)
- 펫 형식: Codex 펫 (`pet.json` + 1536×1872 스프라이트 시트)
