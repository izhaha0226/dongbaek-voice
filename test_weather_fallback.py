#!/usr/bin/env python3
"""날씨 조회가 실패한 날 거짓말이 나가지 않는가.

08-16 09:11 실사례. "오늘 날씨 어때?" 에 open-meteo 가 1.1초 만에 끊겼고
(타임아웃 4초에 닿지도 못했다), 로컬이 None 을 돌려 클로드로 넘어갔다.
클로드에는 날씨 도구가 없으니 "날씨 조회 도구가 저한테는 없어서" 라고
답했다 — 방금 그 도구를 쓰다 실패한 동백의 입으로 나간 거짓말이다.
36초 뒤 같은 질문은 로컬이 멀쩡히 답했다.

    python tests/test_weather_fallback.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트

import router
import weather_local

FAIL = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAIL.append(f"{label}: 기대={want!r} 실제={got!r}")
    print(f"  {'✓' if ok else '✗'} {label}")


_GOOD = {
    "current": {"temperature_2m": 27.3, "weather_code": 1},
    "daily": {"weather_code": [1, 61],
              "temperature_2m_max": [31.2, 24.0],
              "temperature_2m_min": [24.1, 19.5],
              "precipitation_probability_max": [10, 70]},
}


def _boom():
    raise OSError("연결이 끊겼습니다")          # 실측과 같은 꼴 — 그물 실패


print("\n[1] 멀쩡할 때는 그대로 답한다")
weather_local._cache = None
weather_local._fetch = lambda: _GOOD
check("오늘 답에 현재 기온", "27도" in router.handle_local("오늘 날씨 어때"), True)
check("내일 답에 비 확률", "70%" in router.handle_local("내일 날씨 어때"), True)

print("\n[2] 조회가 끊기면 클로드로 넘기지 않는다")
weather_local._cache = None
weather_local._fetch = _boom
for q in ["오늘 날씨 어때", "내일 날씨 어때", "지금 몇 도야"]:
    got = router.handle_local(q)
    check(f"{q!r} → 로컬이 답한다 (클로드 아님)", got is not None, True)
    # 클로드가 하던 거짓말("도구가 없어서")을 동백이 대신 하면 안 된다.
    check(f"{q!r} → 없는 이유를 지어내지 않는다",
          any(w in (got or "") for w in ("도구", "권한", "세션")), False)

print("\n[3] 날씨 질문이 아니면 실패와 무관하게 클로드로 간다")
# ⚠ 무조건 문자열을 돌려주는 변이를 잡는 자리다. 폴백이 넓어지면
#   판단 질문·잡담까지 "날씨를 못 가져왔어요" 로 삼켜 버린다.
for q, why in [
    ("내일 미팅인데 우산 챙겨야 해?", "판단 질문"),
    ("이 문서 요약해줘", "날씨와 무관"),
]:
    check(f"{q!r} ({why}) → Claude", router.handle_local(q), None)

print()
if FAIL:
    print("실패:")
    for f in FAIL:
        print(" -", f)
    _sys.exit(1)
print("전부 통과")
