#!/usr/bin/env python3
"""끝없이 오르기 긴 검사 (화면 없이, 몇 분 걸린다). 짧은 검사는 tests/test_climb.py.

    python3 tools/climb_check.py

일하는 펫 수로 오르는 검사 (홀드를 미리 깔아 둠, 쉬기, 다 씀·회복, 좌우 퍼짐, 제자리 구간, 날씨, 높이 기록,
로프 추락, 저절로 생기는 이벤트, 크럭스) 와 토큰으로 오르는 검사 (넣은 만큼 오름, 빠르기, 기다리기·쉬기, 실제 기록 읽기 비용).
"""
import collections
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import climbcore  # noqa: E402
from climbcore import M_PER_TOKEN, TokenMeter  # noqa: E402

FPS = 30
ok = True


def check(cond, text):
    global ok
    print(("  ✓ " if cond else "  ✗ ") + text)
    ok &= bool(cond)


def climber(seed, tokens=False):
    """tokens 가 아니면 일하는 펫 수로 오르는 아바타 (오를 높이를 쌓지 않아도 오른다)."""
    sim = climbcore.Climber(seed)
    sim.tokens_on = tokens
    return sim


def run(sim, seconds, events=(), feed=None):
    events = sorted(events, key=lambda e: e[0])
    t, i = 0.0, 0
    while t < seconds:
        while i < len(events) and events[i][0] <= t:
            events[i][1](sim)
            i += 1
        if feed:
            feed(sim, t)
        sim.update(1 / FPS)
        t += 1 / FPS


def repeat4(seq):
    return sum(seq[i] == seq[i - 4] for i in range(4, len(seq))) / max(1, len(seq) - 4)


def by_work():
    print("1. 일하는 펫 수로 오르기")
    for seed in (7, 11, 23):
        # 일하는 펫 1 → 3 (다이노 3번) → 2 (비·눈, 초크·팔 털기) → 1, 모두 120초
        sim = climber(seed)
        events = [(0, lambda s: setattr(s, "work", 1)), (30, lambda s: setattr(s, "work", 3)),
                  (35, lambda s: s.request("dyno")), (45, lambda s: s.request("dyno")), (55, lambda s: s.request("dyno")),
                  (60, lambda s: setattr(s, "work", 2)),
                  (62, lambda s: s.request("chalk")), (70, lambda s: s.request("shake")), (90, lambda s: setattr(s, "work", 1))]
        run(sim, 120, events)
        late = [(t, n, h) for t, n, h in sim.grabs if h.made_t > 0 and h.p.y < h.made_cam + climbcore.VIEW_TOP]
        check(sim.missing == 0 and not late and repeat4(sim.moves) < 0.7,
              f"[시드 {seed}] 120초: 잡은 횟수 {len(sim.grabs)}, 오른 높이 {sim.body.y:.1f}m, 그 자리에 만든 홀드 {sim.missing}, "
              f"화면 안에서 생긴 홀드를 잡음 {len(late)}, 손발 순서가 4박자 전과 같은 비율 {repeat4(sim.moves):.2f}")

        # 쉬기: 10초 오른 뒤 일하는 펫 0. 하던 동작이 끝나고 쉬는 자세로 내려앉은 뒤 30초 동안
        # 위쪽 홀드를 새로 잡지 않고, 30초 뒤 높이가 0.1m 넘게 바뀌지 않는다
        sim = climber(seed)
        run(sim, 10)
        sim.work = 0
        n0 = len(sim.actions)
        t = 0.0
        while t < 4 and not (sim.phase_target is None and all(l.mode == "grip" for l in sim.limbs.values())):
            sim.update(1 / FPS)
            t += 1 / FPS
        run(sim, 1.5)
        y0 = sim.body.y
        hands_top = max(sim.held_y(sim.limbs[n]) for n in ("LH", "RH"))
        g0 = len(sim.grabs)
        run(sim, 30)
        climbs = [h for t_, n, h in sim.grabs[g0:] if h.p.y > hands_top + 0.05]
        idle = [a for a in sim.actions[n0:] if a.startswith("idle_")]
        check(not climbs and abs(sim.body.y - y0) <= 0.1 and len(set(idle)) >= 2,
              f"  쉬기 30초: 위쪽 홀드를 새로 잡음 {len(climbs)}, 높이 {sim.body.y - y0:+.2f}m, 쉬는 동작 {idle}")

        # 다 씀 → 회복 → 일부 회복 (체력은 펫이 사용량으로 정한다)
        sim = climber(seed)
        run(sim, 1)
        sim.work, sim.hp = 0, 0
        sim.request("exhaust")
        for _ in range(100):   # 하던 손발 동작을 마치고 포타레지를 펴서 잠들 때까지 (최대 10초)
            if sim.sleeping:
                break
            run(sim, 0.1)
        slept = sim.sleeping
        run(sim, 14, [(0, lambda s: (setattr(s, "work", 1), setattr(s, "hp", 100), s.request("wake"))),
                      (10, lambda s: (setattr(s, "hp", 95), s.request("refill", 55)))])
        done = [a for a in sim.actions if a in ("exhaust", "wake", "refill")]
        check(slept and not sim.sleeping and sim.ledge is None and done == ["exhaust", "wake", "refill"],
              f"  다 씀 → 잠 {slept}, 회복 뒤 잠 {sim.sleeping}, 포타레지 {'남음' if sim.ledge else '접음'}, 동작 {done}")

    # 좌우 퍼짐: 일하는 펫 1 로 600초, 시드 5개 평균 (시제품 v1 은 0.94)
    spread, made = [], 0
    for seed in (7, 11, 23, 31, 47):
        sim = climber(seed)
        run(sim, 600)
        spread.append(statistics.pstdev([h.p.x for t, n, h in sim.grabs if n in ("LH", "RH")]))
        made += sim.missing
    check(statistics.mean(spread) >= 0.9 and made == 0,
          f"좌우 퍼짐 (600초, 손 홀드 x 표준편차): {[round(x, 2) for x in spread]} 평균 {statistics.mean(spread):.2f}, 만든 홀드 {made}")

    # 제자리 구간: 시드 10개 x 300초, 일하는 펫 1·3. 손발 높이의 합이 6번 넘게 연속으로 최고 기록을 못 넘으면 제자리.
    # 손발 고르기가 막히는지를 보는 검사라 추락 이벤트는 끈다 (홀드가 부서진 뒤 다시 오르기는 아래에서 따로 본다).
    # 손을 뻗을 때 뗀 발을 다시 딛는 동작은 세지 않는다 (트래버스에서 손 한 번마다 붙어 동작 수만 두 배가 된다)
    for work in (1, 3):
        stalls, longest = 0, 0
        for seed in range(1, 11):
            sim = climber(seed)
            sim.work = work
            sim.events = False
            best, run_len, seen = -1e9, 0, 0
            t = 0.0
            while t < 300:
                sim.update(1 / FPS)
                t += 1 / FPS
                if len(sim.moves) > seen:
                    seen = len(sim.moves)
                    if sim.limbs[sim.moves[-1]].mode != "grip":   # 뗀 발 다시 딛기
                        continue
                    top = sum(sim.held_y(l) for l in sim.limbs.values())
                    if top > best + 0.05:
                        best, run_len = top, 0
                    else:
                        run_len += 1
                        longest = max(longest, run_len)
                        stalls += run_len == 6
        check(stalls == 0, f"제자리 구간 (일하는 펫 {work}, 시드 10개 x 300초): {stalls}개, 가장 길게 이어진 동작 {longest}번")

    # 홀드가 부서진 뒤: 손이 아래 홀드로 떨어져도 12동작 안에 손발 높이의 합이 그 전 최고를 다시 넘는다 (이벤트 켬).
    # 부서짐은 드물어서 (BREAK_CHANCE) 이 검사에서만 확률을 올린다
    chance, climbcore.BREAK_CHANCE = climbcore.BREAK_CHANCE, 1 / 20
    for work in (1, 3):
        recover_moves = []
        for seed in range(1, 11):
            sim = climber(seed)
            sim.work = work
            best, seen, breaks, recover, t = -1e9, 0, 0, None, 0.0
            while t < 300:
                sim.update(1 / FPS)
                t += 1 / FPS
                if len(sim.moves) > seen:
                    seen = len(sim.moves)
                    top = sum(sim.held_y(l) for l in sim.limbs.values())
                    if sim.actions.count("break") > breaks:
                        breaks, recover = sim.actions.count("break"), [best, 0]
                    if recover is not None:
                        recover[1] += 1
                        if top > recover[0] + 0.05:
                            recover_moves.append(recover[1])
                            recover = None
                    best = max(best, top)
        check(recover_moves and max(recover_moves) <= 12,
              f"홀드가 부서진 뒤 (일하는 펫 {work}, 시드 10개 x 300초): {len(recover_moves)}번, 다시 오르는 데 든 동작 "
              f"가장 많이 {max(recover_moves) if recover_moves else '-'}번")
    climbcore.BREAK_CHANCE = chance

    # 같은 손발을 연달아 옮기는 비율: 시드 8개 x 300초, 일하는 펫 1·3. 5% 이하, 빠르기는 자세 다듬기 (삼지점, 빠른 템포) 뒤
    # 값 (분당 6.4·9.1m) 의 95% 넘게 (보통 빠르기는 몇 동작마다 둘러보며 쉬어서 85% 넘게),
    # 잡은 손이 닿는 거리를 2% 넘게 늘어난 프레임 12.5% 이하 (내다보기 전에는 약 13%, 내다본 뒤 9~10% 대,
    # 뗀 발을 다음 동작으로 바꾼 뒤 시드 24개 평균 약 11%, 시드 묶음마다 ±1%p)
    for work, before, keep in ((1, 6.4, 0.85), (3, 9.1, 0.95)):
        repeats = moves = far = made = frames = stretched = 0
        speeds = []

        def watch(s, t):   # 잡은 손이 닿는 거리를 2% 넘게 늘어난 프레임
            nonlocal frames, stretched
            frames += 1
            stretched += any(l.mode == "grip" and (l.pos - l.root(s.body, s.tilt)).len() > l.reach * 1.02
                             for l in s.limbs.values() if l.kind == "H")

        for seed in range(1, 9):
            sim = climber(seed)
            sim.work = work
            y0 = sim.top_y
            run(sim, 300, feed=watch)
            m = sim.moves
            repeats += sum(m[i] == m[i - 1] for i in range(1, len(m)))
            moves, far, made = moves + len(m), far + sim.far_reaches, made + sim.missing
            speeds.append((sim.top_y - y0) / 5)
        rate, speed = repeats / moves, statistics.mean(speeds)
        check(rate <= 0.05 and made == 0 and stretched / frames <= 0.125,
              f"같은 손발 연달아 (일하는 펫 {work}, 시드 8개 x 300초): {rate:.1%} ({repeats}/{moves}), 한 번에 멀리 {far}번, "
              f"만든 홀드 {made}, 팔이 닿는 거리를 넘은 프레임 {stretched / frames:.2%}")
        check(speed >= before * keep,
              f"오르는 빠르기 (일하는 펫 {work}): 분당 {speed:.1f}m (자세 다듬기 뒤 {before}m 의 {keep:.0%} 넘게)")

    # 날씨: 일하는 펫 3 (8초) ↔ 0 (12초) 를 20번. 비와 눈이 둘 다 나오고, 한 번 내리는 동안은 안 바뀐다
    sim = climber(5)
    kinds, mixed = [], 0
    for _ in range(20):
        seen_kinds = set()
        for work, secs in ((3, 8), (0, 12)):
            sim.work = work
            t = 0.0
            while t < secs:
                sim.update(1 / FPS)
                t += 1 / FPS
                if sim.precip() > 0:
                    seen_kinds.add(sim.weather_kind)
        kinds.append("/".join(sorted(seen_kinds)))
        mixed += len(seen_kinds) > 1
    check(mixed == 0 and {"rain", "snow"} <= set(kinds),
          f"날씨 20번: {dict(collections.Counter(kinds))}, 한 번 내리는 동안 바뀐 횟수 {mixed}")

    # 높이 기록: 100m 까지 오르면 30·56·93m 가 순서대로 한 번씩, 50m 내려왔다 다시 올라가도 또 뜨지 않는다
    sim = climber(3)
    sim.work = 3
    t = 0.0
    while sim.altitude() < 100 and t < 900:
        sim.update(1 / FPS)
        t += 1 / FPS
    first, peak = list(sim.banners), sim.peak
    sim.alt0 -= 50
    t = 0.0
    while sim.altitude() < peak + 10 and t < 900:
        sim.update(1 / FPS)
        t += 1 / FPS
    again = sim.banners[len(first):]
    check(first[:3] == [30, 56, 93] and len(set(sim.banners)) == len(sim.banners) and not any(h in first for h in again),
          f"높이 기록: 100m 까지 {first}, 50m 내려와 다시 {sim.altitude():.0f}m 까지 오른 뒤 새로 뜬 기록 {again}")

    # 로프 추락 10번: 모두 로프에 걸려 멈추고 (걸린 점 - 로프 길이 - 1m 보다 아래로 안 감), 12초 안에 다시 오른다
    sim = climber(7)
    sim.work = 1
    caught = resumed = 0
    for _ in range(10):
        run(sim, 20)
        sim.request("fall")
        lowest, limit, moves_before, t = 1e9, None, None, 0.0
        while t < 12:
            sim.update(1 / FPS)
            t += 1 / FPS
            if sim.rope is not None:
                anchor, length = sim.rope
                limit = anchor.y - length - 1.0
                lowest = min(lowest, sim.harness().y)
            elif limit is not None and moves_before is None:
                moves_before = len(sim.moves)
            if moves_before is not None and len(sim.moves) > moves_before:
                resumed += 1
                break
        caught += limit is not None and lowest >= limit
    check(caught == 10 and resumed == 10 and sim.missing == 0 and len(set(sim.banners)) == len(sim.banners),
          f"로프 추락 10번: 로프에 걸려 멈춤 {caught}번, 다시 오르기 {resumed}번, 만든 홀드 {sim.missing}")

    # 저절로 생기는 이벤트: 비·눈 (펫 2) + 체력 15% 300초, 폭풍우 (펫 3) 300초. 끄면 하나도 없다
    counts = collections.Counter()
    for events in (True, False):
        for work in (2, 3):
            sim = climber(13)
            sim.events = events
            sim.work, sim.hp = work, 15
            rocks = 0
            t = 0.0
            while t < 300:
                sim.update(1 / FPS)
                t += 1 / FPS
                rocks = max(rocks, len(sim.rocks))
            for a in sim.actions:
                if a in ("slip_foot", "slip_hand", "break", "clip"):
                    counts[(events, a)] += 1
            counts[(events, "rocks_seen")] += rocks > 0
            counts[(events, "missing")] += sim.missing
    on = {k[1]: v for k, v in counts.items() if k[0]}
    off = {k[1]: v for k, v in counts.items() if not k[0]}
    check(on.get("slip_foot", 0) >= 1 and on.get("slip_hand", 0) >= 1 and on.get("rocks_seen", 0) >= 1
          and on.get("missing", 0) == 0, f"저절로 생긴 이벤트 (켬, 600초): {on}")
    check(sum(v for k, v in off.items() if k != "missing") == 0, f"이벤트 끔 (600초): {off}")

    # 크럭스: 시드 8개 x 600초, 일하는 펫 1·3 과 토큰 전력 질주. 지나간 크럭스는 모두 끝 홀드를 잡아 넘고 (돌아가는 길 없이),
    # 크럭스 아래 끝에 닿은 뒤 뛰기까지 손발 동작 10번 이하 (실행마다 6~9번), 만든 홀드 0
    passed = skipped = made = longest = retried = 0
    for setup in (1, 3, "sprint"):
        for seed in range(1, 9):
            sim = climber(seed, tokens=setup == "sprint")
            if setup != "sprint":
                sim.work = setup
            run(sim, 600, feed=(lambda s, t: setattr(s, "bank", max(s.bank, 60.0))) if setup == "sprint" else None)
            grabbed = {id(g[2]) for g in sim.grabs}
            passed += len(sim.crux_log)
            retried += sum(1 for tries, _ in sim.crux_log if tries > 1)
            longest = max([longest] + [moves for _, moves in sim.crux_log])
            skipped += sum(1 for jug in sim.route.jugs if jug.p.y < sim.top_y - 1 and id(jug) not in grabbed)
            made += sim.missing
    check(passed >= 30 and skipped == 0 and longest <= 10 and made == 0,
          f"크럭스 (일하는 펫 1·3·전력 질주, 시드 8개 x 600초): {passed}번 넘음 (미끄러져 다시 {retried}), "
          f"끝 홀드 안 잡고 지나침 {skipped}, 아래 끝에서 뛰기까지 동작 가장 많이 {longest}번, 만든 홀드 {made}")


def by_tokens():
    print("2. 토큰으로 오르기")
    for seed in (7, 11, 23):   # 300초 동안 15초마다 2m 어치 토큰 (분당 8m)
        sim = climber(seed, tokens=True)
        sim.work = 1
        top0, state = sim.top_y, {"next": 0.0}

        def feed(s, t):
            if t >= state["next"] and t < 300:
                s.deposit(round(2 / M_PER_TOKEN))
                state["next"] += 15

        run(sim, 300, feed=feed)
        extra = 0
        while sim.has_climb() and extra < 120:
            run(sim, 1.0)
            extra += 1
        run(sim, 3.0)
        put, climbed = sim.tokens * M_PER_TOKEN, sim.top_y - top0
        check(abs(climbed - put) <= 1.0 and sim.missing == 0,
              f"[시드 {seed}] 넣은 {put:.1f}m, 오른 {climbed:.1f}m (차이 {climbed - put:+.2f}m), 마저 오른 시간 {extra}초")

    result = {}
    for rate in (4, 20, 60):   # 분당 들어오는 높이 (m, 10초마다 토큰으로 나눠 넣는다), 240초
        speeds, sprint = [], []
        for seed in (7, 11, 23):
            sim = climber(seed, tokens=True)
            sim.work = 1
            top0, paces, state = sim.top_y, [], {"next": 0.0}

            def feed(s, t):
                if t >= state["next"]:
                    s.deposit(rate / M_PER_TOKEN / 6)
                    state["next"] += 10
                paces.append(s.pace if s.has_climb() and not s.resting else 0)

            run(sim, 240, feed=feed)
            speeds.append((sim.top_y - top0) / 4)
            climbing = [p for p in paces if p]
            sprint.append(sum(p == 3 for p in climbing) / max(1, len(climbing)))
        result[rate] = statistics.mean(speeds)
        print(f"  분당 {rate / M_PER_TOKEN:,.0f} 토큰 (분당 {rate}m 들어옴): 분당 {', '.join(f'{s:.1f}' for s in speeds)}m 오름, "
              f"전력 질주 {statistics.mean(sprint):.0%}")
        if rate == 60:
            check(statistics.mean(sprint) > 0.5, "분당 60m 어치에서 전력 질주가 절반 넘게")
    check(result[4] < result[20] < result[60], "빠르기 순서 분당 4m < 20m < 60m 어치")

    for work, name in ((1, "기다리기 (일하는 펫 1)"), (0, "쉬기 (일하는 펫 0)")):
        for seed in (7, 11):
            sim = climber(seed, tokens=True)
            sim.work = 1
            sim.deposit(round(6 / M_PER_TOKEN))
            run(sim, 30)
            sim.work = work
            for _ in range(240):
                if not sim.has_climb():
                    break
                run(sim, 0.5)
            run(sim, 3.0)
            top0, grabs0, actions0 = sim.top_y, len(sim.grabs), len(sim.actions)
            hi = max(sim.held_y(l) for l in sim.limbs.values())
            run(sim, 60)
            higher = [g for g in sim.grabs[grabs0:] if g[2].p.y > hi + 0.05]
            idles = [a for a in sim.actions[actions0:] if a.startswith("idle_")]
            check(sim.top_y - top0 <= 0.1 and not higher and len(idles) >= 3,
                  f"{name} [시드 {seed}] 60초 동안 높이 +{sim.top_y - top0:.2f}m, 위쪽 홀드 새로 잡음 {len(higher)}, "
                  f"쉬는 동작 {len(idles)}번")

    meter = TokenMeter()
    times = []
    for _ in range(20):
        t0 = time.process_time()
        meter.poll()
        times.append(time.process_time() - t0)
    per = statistics.median(times)
    check(per / 2.0 * 100 <= 0.5, f"실제 기록 파일 {len(meter.files())}개, 한 번 읽기 CPU {per * 1000:.2f}ms "
                                  f"→ 2초마다면 코어의 {per / 2.0 * 100:.3f}%")


by_work()
by_tokens()
print("통과" if ok else "실패")
sys.exit(0 if ok else 1)
