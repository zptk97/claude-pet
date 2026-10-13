# claude-pet

Claude Code 세션마다 펫 한 마리를 바탕화면 위에 띄워 작업 상태를 보여준다.
펫 그림은 [Codex 펫](https://petdex.dev) 형식(스프라이트 시트)을 그대로 쓴다.

| 세션 상태 | 펫 | 말풍선 |
|---|---|---|
| 쉬는 중 | idle | 없음 |
| 작업 중 | running | `Bash 실행 중 · 1분 23초` (흰색) |
| 확인 필요 (권한·질문) | waiting | `확인이 필요해요!` (노랑) |
| 다 했어요 | waving | `다 했어요! (2분 13초)` (초록) |
| 백그라운드 작업 기다림 | review | `백그라운드: 실험 이름 · 12분` (파랑) |
| 오류로 멈춤 | failed | `오류로 멈췄어요` (빨강) |

- **왼쪽 클릭**: 그 세션의 터미널 창을 앞으로 올리고, 완료·오류 표시를 확인 처리한다.
- **마우스 올림**: 세션 컨텍스트 사용량과 계정 5시간·주간 사용량, 리셋 시각을 보여준다.
- **드래그**: 펫 줄 전체를 옮긴다.
- **오른쪽 클릭**: 세션별 펫 고르기, 펫 다시 읽기, 펫 켜고 끄기(끈 펫은 자동 배정에서 빠진다), 크기, 접기(손이 필요한 세션 하나만 보기), 자동 실행, 종료.

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
tools/             펫 만들기 도구 (docs/making-pets.md)
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
