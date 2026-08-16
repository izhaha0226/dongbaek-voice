#!/usr/bin/env python3
"""speak.clean — 귀로 알아들을 수 있는 표기인가.

TTS 는 "32GB" 를 "삼십이지비" 로 읽는다. 화면이면 읽히지만 소리로는 아니다.
사장님 교정 두 건이 여기 걸려 있다 — "32GB 가 아니라 32기가바이트",
그리고 "'.' 은 '쩜' 으로".
    python test_speak_units.py
"""
import speak

passed = 0


def check(src: str, want: str, why: str) -> None:
    global passed
    got = speak.clean(src)
    assert want in got, f"{why}: {src!r} → {got!r} (기대: {want!r})"
    print(f"  ✓ {why}")
    passed += 1


def check_not(src: str, avoid: str, why: str) -> None:
    global passed
    got = speak.clean(src)
    assert avoid not in got, f"{why}: {src!r} → {got!r} ({avoid!r} 가 남았다)"
    print(f"  ✓ {why}")
    passed += 1


print("[1] 용량 단위는 한글로 끝까지")
check("메모리 32GB 남았습니다", "32기가바이트", "GB → 기가바이트")
check("모델이 512MB 입니다", "512메가바이트", "MB → 메가바이트")
check("디스크 2TB 짜리", "2테라바이트", "TB → 테라바이트")
check("여유 48기가 입니다", "48기가바이트", "줄여 말한 '기가' 도 끝까지")
check_not("메모리 32GB 남았습니다", "GB", "영문 약어는 소리로 안 나간다")

print("\n[2] 소수점은 '쩜'")
check("여유 5.0기가", "5쩜0", "5.0 → 5쩜0")
check("스왑 2.3기가 사용", "2쩜3", "2.3 → 2쩜3")
check("오늘 회의는 3시입니다.", "3시입니다", "문장 끝 마침표는 건드리지 않는다")

print("\n[3] 안 건드려야 할 것")
check("기가 막히는 일입니다", "기가 막히는", "'기가 막히다' 는 단위가 아니다")
check("깃허브에 올렸습니다", "깃허브", "숫자 없는 말은 그대로")

print(f"\n✅ 전부 통과 — {passed}건. 귀로 알아듣는 표기로 나간다")
