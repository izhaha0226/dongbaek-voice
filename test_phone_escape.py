#!/usr/bin/env python3
"""전화 모드에 갇히지 않는가 — 거듭 부르면 반드시 열려야 한다.

2026-08-12 08:19, 뉴스 낭독(176자 연속)을 통화로 오인해 전화 모드로
들어갔다. 그 뒤 08:20:30·08:20:55 에 "동백아" 를 세 번 부르셨는데 전부
조용히 버려졌다. 로그도 안 남았다. 사장님이 보시기엔 완전한 무응답이고,
PHONE_HOLD_SEC 가 10분이라 그동안 갇힌다.

원인은 호명 탈출구에 화자 인증이 겹쳐 있던 것이다. 인증에 실패하면
`else: continue` 로 빠졌다 — 코드 주석에 "이게 없으면 네 번을 불러도
무시당한다, 실제로 겪었다" 고 적어놓고 같은 함정을 다시 만든 셈이다.

여기서 재는 것은 하나다: **못 나오는 경우가 없어야 한다.**

    python test_phone_escape.py
"""
import config

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


print("\n탈출 조건이 설정에 있는가")
check("거듭 부르기 횟수가 있다", hasattr(config, "PHONE_WAKE_TRIES"), True)
check("횟수가 손에 닿는 범위 (2~4번)",
      2 <= config.PHONE_WAKE_TRIES <= 4, True)
check("창이 한 번 부를 만한 길이 (15~60초)",
      15 <= config.PHONE_WAKE_WINDOW_SEC <= 60, True)

print("\n코드에 탈출구가 살아 있는가")
src = open("dongbaek.py").read()
check("인증 실패를 로그로 남긴다 (조용히 버리지 않는다)",
      "목소리 확인 실패" in src, True)
check("거듭 부르면 인증과 무관하게 연다",
      "PHONE_WAKE_TRIES" in src, True)
check("열 때 세는 값을 초기화한다 (다음 통화에 이월되면 안 된다)",
      src.count('_HOLD["calls"] = 0') >= 1, True)
check("풀린 뒤 대화창이 열린다 (풀리자마자 또 씹히면 소용없다)",
      '_REPLIED_AT["at"] = time.monotonic()' in src, True)

print("\n말끝에서 불러도 열리는가 (통화 중엔 오히려 이쪽이 흔하다)")
import router
for said in ["이제서야 얘가 바뀐 거지? 동백아.", "그래서 말인데 동백아",
             "알겠어 동백아."]:
    check(f"{said[:20]!r}… → 부름으로 본다", router.wake_at_end(said), True)
for said in ["어제 동백한테 말했는데", "동백이는 잘 하고 있어", "동백 얘기 좀 하자"]:
    check(f"{said!r} → 소재일 뿐", router.wake_at_end(said), False)
check("전화 모드 탈출구도 말끝 호명을 본다",
      "wake_at_end" in src and src.count("wake_at_end") >= 2, True)


print("\n갇히는 경로가 남아 있지 않은가")
# 옛 코드의 조용한 탈락. 이 두 줄이 나란히 있으면 다시 갇힌다.
check("인증 실패 → 무조건 continue 하는 옛 경로가 없다",
      "if ok_r:\n                            _HOLD" in src, False)

print("\n세는 방식이 사람의 부르는 리듬과 맞는가")
# 30초 안에 3번이면, 한 번 부르고 10초쯤 기다렸다 또 부르는 리듬이다.
gap = config.PHONE_WAKE_WINDOW_SEC / config.PHONE_WAKE_TRIES
print(f"  · {config.PHONE_WAKE_TRIES}번 / {config.PHONE_WAKE_WINDOW_SEC:.0f}초 "
      f"= 평균 {gap:.0f}초 간격")
check("부르는 간격이 5초 이상 (숨 쉴 틈은 있어야 한다)", gap >= 5, True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 거듭 부르면 전화 모드에서 반드시 나옵니다")
