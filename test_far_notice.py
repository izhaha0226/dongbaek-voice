#!/usr/bin/env python3
"""원거리 인지 — 동백이 '멀리 계신 걸' 알아채고 말로 알리는지.

사장님 지시(2026-08-13): "원거리에 계셔서 출력을 높여 듣습니다 — 라고
니가 인지하고 있는지 확인할 수 있게 해줘."
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import config
import dongbaek
import router

FAIL = []
SPOKEN = []


def check(name, got, want=True):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


# 소리는 내지 않고 무엇을 말하려 했는지만 잡는다
import speak
speak.say = lambda text, **kw: SPOKEN.append(text)

print("[1] 증폭이 크면 한 번 알린다")
dongbaek._FAR.update(gain=0.0, at=0.0, told=0.0)
dongbaek._on_far_gain(0.005, 4.0)
check("멀다고 말했다", any("멀리" in s for s in SPOKEN), True)

print("[2] 매번 말하지 않는다 (쿨다운)")
n = len(SPOKEN)
dongbaek._on_far_gain(0.005, 4.0)
dongbaek._on_far_gain(0.006, 3.5)
check("반복 안 함", len(SPOKEN), n)

print("[3] 살짝 작은 소리는 알리지 않는다")
SPOKEN.clear()
dongbaek._FAR.update(told=0.0)
dongbaek._on_far_gain(0.02, 1.4)          # FAR_NOTICE_MIN_GAIN(1.8) 아래
check("조용히 넘어감", SPOKEN, [])

print("[4] '내 목소리 잘 들려?' 에 실측으로 답한다")
for q in ["내 목소리 잘 들려?", "지금 소리 잘 들려", "내가 멀리 있는 거 알아?"]:
    check(f"{q!r} 인식", router.is_far_status_query(router.normalize(q)), True)
check("무관한 말은 아님",
      router.is_far_status_query(router.normalize("오늘 일정 알려줘")), False)

import time
dongbaek._FAR.update(gain=4.0, at=time.monotonic())
# 사장님 교정: 등록 개수를 읊지 말고 "네/아니오 + 조치" 로 짧게.
check("멀 때 '아니요' 로 시작", dongbaek.far_status().startswith("아니요"), True)
check("배수를 말한다", "4.0배" in dongbaek.far_status(), True)
check("짧다 (30자 이내)", len(dongbaek.far_status()) <= 30, True)
dongbaek._FAR.update(gain=0.0, at=0.0)
check("가까울 때 '네'", dongbaek.far_status().startswith("네"), True)
check("현황 낭독이 아니다", "등록" not in dongbaek.far_status(), True)

print("[5] ⚠ 목소리 현황 낭독에 가로채이지 않는다 (사장님 지적 사례)")
router.set_far_status(dongbaek.far_status)
got = router.handle_local("내 목소리 잘 들려?")
check("현황이 아니라 상태로 답", got is not None and "등록" not in got, True)
check("'화자 인식 현황' 은 여전히 현황",
      router.is_voice_status(router.normalize("화자 인식 현황 알려줘")), True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 멀리 계시면 알아채고, 물으면 실측으로 답한다")
