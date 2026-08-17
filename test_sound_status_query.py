#!/usr/bin/env python3
""""이 정도 음향 괜찮아?" 는 실측으로 답한다 — 도구가 없다고 하지 않는다.

2026-08-17 09:07 실사례. 사장님이 마이크 입력을 올리시고 "이 정도 음향
괜찮아?" 하고 물으셨는데, 이 말이 _FAR_ASK 에 없어서 클로드로 올라갔고
"음향 상태 확인할 수 있는 도구가 없어서 직접 판단은 어렵고…" 라고 답했다.
열네 분 전(08:53)에 저 스스로 입력 볼륨을 30에서 65로 올려놓고 한 말이다.
데몬은 발화마다 증폭 배수를 재고 있으니 도구가 없는 게 아니라 안 본 것이고,
없는 이유를 지어내는 건 CLAUDE.md 가 제일 무겁게 치는 잘못이다.
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import time

import dongbaek
import router

FAIL = []


def check(name, got, want=True):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


print("[1] 음향·마이크를 묻는 말을 알아본다")
for q in ["이 정도 음향 괜찮아?", "음향 어때?", "마이크 괜찮아?",
          "지금 소리 어때?", "음질 괜찮아?", "마이크 잘 잡혀?"]:
    check(f"{q!r} 인식", router.is_far_status_query(router.normalize(q)), True)

print("[2] 상관없는 말은 안 건드린다")
for q in ["오늘 일정 알려줘", "음향 장비 어디서 사?", "소리 좀 키워줘",
          "메일 확인해줘"]:
    check(f"{q!r} 는 아님", router.is_far_status_query(router.normalize(q)), False)

print("[3] ⚠ 설명이 길게 붙으면 로컬이 가로채지 않는다")
# 뒤에 딴 부탁이 붙은 말을 한 줄짜리 상태 답으로 덮으면, 같은 날 아침
# 메일 즉답이 '확인' 두 글자만 보고 말을 가로챈 사고를 되풀이하는 것이다.
long_ask = ("이 정도 음향 괜찮아? 지금 내가 음향을 조금 더 올렸거든? "
            "네가 65라 했는데 내가 조금 더 올렸어.")
check("긴 설명은 클로드 몫", router.is_far_status_query(router.normalize(long_ask)), False)
check("딴 부탁이 붙어도 클로드 몫",
      router.is_far_status_query(router.normalize("음향 괜찮아? 그리고 메일 확인해줘")), False)

print("[4] 답은 실측 증폭 배수다 (0 토큰)")
router.set_far_status(dongbaek.far_status)
dongbaek._FAR.update(gain=2.5, at=time.monotonic())
got = router.handle_local("이 정도 음향 괜찮아?")
check("로컬이 답한다", got is not None, True)
check("실측 배수를 말한다", "2.5배" in (got or ""), True)
check("도구 핑계를 대지 않는다", "도구" in (got or ""), False)

print("[5] ⚠ 무르게 만든 게 아니다 — 분기를 없애면 잡힌다")
saved = router._SOUND_ASK
try:
    router._SOUND_ASK = ()
    check("분기를 없앤 변이는 09:07 을 놓친다",
          router.is_far_status_query(router.normalize("이 정도 음향 괜찮아?")), False)
finally:
    router._SOUND_ASK = saved

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 음향을 물으면 실측으로 답하고, 길게 물으시면 클로드가 받는다")
