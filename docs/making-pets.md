# 펫 만들기

claude-pet 은 Codex 펫 형식을 그대로 쓴다. Codex 나 Petdex 에서 쓰는 펫은 여기서도 쓸 수 있고,
여기서 만든 펫도 Codex·Petdex 에서 쓸 수 있다.

## 규격

펫 하나는 폴더 하나다.

```
pets/<펫 id>/
  pet.json
  spritesheet.webp      (PNG 도 된다)
```

```json
{"id": "mochi", "displayName": "Mochi", "description": "볼이 분홍색인 찹쌀떡 고양이", "spritesheetPath": "spritesheet.webp"}
```

`spritesheetPath` 는 폴더 안의 파일 이름만 쓴다 (하위 경로나 `../` 는 무시된다). 폴더 이름과 `id` 를 같게 두는 게 편하다.

스프라이트 시트는 **1536×1872 px, 투명 배경**, 192×208 칸이 8열 × 9행이다. 줄마다 쓰는 칸 수가 다르고, 남는 칸은 완전히 비워 둔다.

| 줄 | 동작 | 프레임 | claude-pet 에서 쓰는 때 |
|---|---|---|---|
| 0 | idle | 6 | 쉬는 중 (첫 프레임은 정지 그림으로도 쓰인다) |
| 1 | running-right | 8 | (쓰지 않음, Codex 호환용) |
| 2 | running-left | 8 | (쓰지 않음, Codex 호환용) |
| 3 | waving | 4 | 다 했어요 |
| 4 | jumping | 5 | 펫을 클릭했을 때 한 번 |
| 5 | failed | 8 | 오류로 멈춤 |
| 6 | waiting | 6 | 확인이 필요해요 |
| 7 | running | 6 | 작업 중 (달리기가 아니라 "일하는 중" 모습) |
| 8 | review | 6 | 백그라운드 작업 기다리는 중 |

프레임 시간(ms)은 `petcore.py` 의 `ANIMATIONS` 에 있다 (Codex 와 같다).

지켜야 할 것 (hatch-pet 규칙):
- 그림자, 빛 번짐, 속도선, 먼지, 글자, 몸에서 떨어진 효과는 넣지 않는다. 효과는 불투명하고 몸에 붙어 있을 때만 된다.
- 모든 프레임에서 크기·생김새·색·바닥선을 일정하게 유지한다 (jumping 만 아래 예외).
- 크기: 칸을 거의 꽉 채운다. 서 있는 모습(idle) 높이는 190~198px, 가로는 182px 이내로 하고, 발바닥은 칸 바닥에서 약 5px 위에 둔다.
  Codex·Petdex 펫이 모두 이 크기다. claude-pet 은 그림 크기가 아니라 칸 크기로 펫을 띄우므로, 칸 안에 작게 그리면 화면에서도 다른 펫보다 작게 보인다.
- 점프: 점프할 자리를 남기려고 전체 크기를 줄이지 않는다. jumping 줄은 칸 안에서 들 수 있는 만큼만(보통 10~35px) 들어 올리고,
  그래도 모자라면 그 줄만 몸을 조금 작게 그린다. 클릭했을 때 잠깐만 작아 보이고 평소 크기는 그대로다.
- 칸 가장자리에서 5px 이상 띄운다. hatch-pet 의 안전 여백(좌우 18px, 위아래 16px)은 방법 2에서 이미지 AI 에게 주는
  띠 그림 배치 가이드용이다. 자르는 스크립트가 프레임을 182×198 안에 맞춰 넣으므로 완성 시트에는 적용하지 않는다.
- 완전히 투명한 픽셀의 색 값은 0 이어야 한다 (`compose_atlas.py` 로 다시 저장하면 정리된다).

## 방법 1. Codex 로 만들기 (가장 쉬움)

Codex 에는 펫을 만들어 주는 `hatch-pet` 스킬이 있다. Codex 에서 "hatch-pet 으로 ○○ 펫 만들어 줘" 라고 하면
이미지를 생성하고 다듬어 `~/.codex/pets/<id>/` 에 저장한다.

claude-pet 은 `~/.codex/pets` 도 읽으므로, 펫 오른쪽 클릭 → **이 세션의 펫 → 펫 다시 읽기** 를 누르면 바로 고를 수 있다.
이 저장소에 보관하려면 그 폴더를 `pets/<id>/` 로 복사한다.

## 방법 2. 다른 이미지 AI 로 그리고 스크립트로 조립하기

Claude Code 에는 이미지 생성 기능이 없다. ChatGPT, Gemini 같은 이미지 AI 로 그림을 만들고,
자르기·조립·검사는 `tools/hatch-pet/scripts/` 의 스크립트로 한다 (openai/skills 의 hatch-pet, Apache-2.0).
아래 명령은 저장소 폴더(`~/.claude/pet`)에서 실행한다. 작업 폴더 `runs/` 는 git 에서 제외된다.

```bash
H=tools/hatch-pet/scripts
RUN=runs/mochi
```

**1) 준비.** 프롬프트, 줄별 배치 가이드, 작업 목록을 만든다.

```bash
python3 $H/prepare_pet_run.py --pet-name "Mochi" \
  --pet-notes "a small round white rice-cake cat with pink cheeks" \
  --style-preset pixel --output-dir $RUN
```

- `--style-preset`: `auto`, `pixel`, `plush`, `clay`, `sticker`, `flat-vector`, `3d-toy`, `painterly`
- 참고 그림이 있으면 `--reference 그림.png` 를 붙인다.
- 생기는 것: `prompts/base-pet.md`, `prompts/rows/<동작>.md`, `references/layout-guides/<동작>.png`, `imagegen-jobs.json`
- 배경은 크로마키 단색(보통 마젠타 `#FF00FF`)으로 그리게 되어 있다. 나중에 이 색을 지워 투명하게 만든다.

**2) 그림 만들기.** 작업 순서와 각 작업에 줄 파일을 본다.

```bash
jq -c '.jobs[] | {id, depends_on, prompt_file, input_images: [.input_images[].path], output_path}' $RUN/imagegen-jobs.json
```

1. `base`: `prompts/base-pet.md` 내용을 이미지 AI 에 주고, 결과를 `decoded/base.png` 로 저장한다.
   마음에 들면 `references/canonical-base.png` 로도 복사한다 (이후 모든 줄의 기준 그림).
2. 나머지 줄(`idle`, `running-right`, ... `review`): 그 줄의 프롬프트와 `input_images` 의 그림들
   (배치 가이드 + 기준 그림)을 함께 주고, 결과 가로 띠 그림을 `output_path`(`decoded/<동작>.png`)에 저장한다.
   - 가이드는 칸 간격만 참고하라는 그림이다. 결과에 칸 선이 그려지면 다시 만든다.
   - 거부되거나 이상하면 `prompts/row-retries/<동작>.md` 로 다시 시도한다.
3. `running-left` 는 오른쪽 그림을 뒤집어도 이상하지 않으면 그려 달라는 대신 이렇게 만든다:

```bash
python3 $H/derive_running_left_from_running_right.py --run-dir $RUN \
  --confirm-appropriate-mirror --decision-note "좌우 대칭이라 뒤집어도 같음"
```

**3) 자르기 → 검사 → 조립 → 시트 검사 → 미리보기.**

```bash
mkdir -p $RUN/final $RUN/qa
python3 $H/extract_strip_frames.py --decoded-dir $RUN/decoded --output-dir $RUN/frames --states all --method auto
python3 $H/inspect_frames.py --frames-root $RUN/frames --json-out $RUN/qa/review.json --require-components
python3 $H/compose_atlas.py --frames-root $RUN/frames --output $RUN/final/spritesheet.png --webp-output $RUN/final/spritesheet.webp
python3 $H/validate_atlas.py $RUN/final/spritesheet.webp --json-out $RUN/final/validation.json
python3 $H/make_contact_sheet.py $RUN/final/spritesheet.webp --output $RUN/qa/contact-sheet.png
python3 $H/render_animation_previews.py --frames-root $RUN/frames --output-dir $RUN/qa/previews
```

- `qa/contact-sheet.png` (전체 동작 모음)과 `qa/previews/*.gif` (줄별 움직임)를 눈으로 본다.
  줄마다 생김새가 바뀌거나 크기가 튀면 그 줄만 다시 그린다.
- 동작할 때 크기가 커졌다 작아졌다 하면 자르기를 `--method stable-slots` 로 다시 하고,
  검사에 `--allow-stable-slots` 를 붙인다.

**4) 펫 폴더로 옮기기.**

```bash
ID=$(jq -r .pet_id $RUN/pet_request.json)
mkdir -p pets/$ID && cp $RUN/final/spritesheet.webp pets/$ID/
jq -n --arg id "$ID" --arg name "$(jq -r .display_name $RUN/pet_request.json)" \
  '{id: $id, displayName: $name, description: "", spritesheetPath: "spritesheet.webp"}' > pets/$ID/pet.json
```

펫 메뉴에서 **펫 다시 읽기** → 그 펫을 고른다.

## 방법 3. 직접 그리기

1. 칸 가이드 이미지를 만든다. 쓰는 칸에는 테두리·그림 영역(초록, 가장자리 5px 안쪽)·바닥선(빨강), 안 쓰는 칸에는 빗금이 있다.
   서 있는 모습은 초록 상자 높이를 거의 다 채우고 발이 빨간 선에 닿게 그린다.

   ```bash
   python3 tools/atlas_template.py --output runs/template.png
   ```

2. 그림 프로그램에서 이 이미지를 맨 아래 레이어로 두고 위 레이어에 그린다.
   픽셀아트는 Aseprite(유료), LibreSprite(무료), Piskel(웹), 일반 그림은 Krita 등.
3. 가이드 레이어를 숨기고 투명 배경 1536×1872 PNG 로 내보낸다.
4. 정리해서 저장하고 검사한다.

   ```bash
   H=tools/hatch-pet/scripts
   python3 $H/compose_atlas.py --source-atlas 내그림.png --output runs/mine/spritesheet.png --webp-output pets/mine/spritesheet.webp
   python3 $H/validate_atlas.py pets/mine/spritesheet.webp --json-out runs/mine/validation.json
   python3 $H/make_contact_sheet.py pets/mine/spritesheet.webp --output runs/mine/contact-sheet.png
   python3 tools/atlas_to_frames.py pets/mine/spritesheet.webp --output-dir runs/mine/frames
   python3 $H/render_animation_previews.py --frames-root runs/mine/frames --output-dir runs/mine/previews
   ```

5. `pets/mine/pet.json` 을 쓰고 **펫 다시 읽기**.

`tools/atlas_to_frames.py` 는 완성된 시트를 프레임 PNG 로 나눈다. 받은 펫을 GIF 로 미리 보거나
`inspect_frames.py` 로 검사할 때도 쓴다.

## 알려진 한계

- hatch-pet 스크립트는 프레임을 칸에 맞출 때 부드럽게(LANCZOS) 줄인다. 픽셀아트는 경계가 흐려질 수 있으니
  처음부터 182×198 안에 들어가는 크기로 그리면 줄어들지 않는다.
- 자를 때 프레임을 바닥선이 아니라 가운데에 맞춘다. 위아래로 흔들리면 `stable-slots` 를 쓴다.
- Petdex 에서 받은 펫 중에는 투명 픽셀 색 값 때문에 `validate_atlas.py` 를 통과하지 못하는 것이 있다.
  보이는 모습에는 영향이 없고, `compose_atlas.py --source-atlas` 로 다시 저장하면 통과한다.

## Petdex 에 올리기 (선택)

[petdex.dev](https://petdex.dev) 는 펫 폴더(또는 zip)를 받는다. `npx petdex login` 후 `npx petdex submit pets/<id>`
(Node 20+), 또는 웹의 `/submit`. 관리자 검토 후 공개되고, 하루 10개까지 올릴 수 있다.
