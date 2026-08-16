#!/usr/bin/env python3
"""speak.clean — 귀로 알아들을 수 있는 표기인가.

TTS 는 "32GB" 를 "삼십이지비" 로 읽는다. 화면이면 읽히지만 소리로는 아니다.
사장님 교정 두 건이 여기 걸려 있다 — "32GB 가 아니라 32기가바이트",
그리고 "'.' 은 '쩜' 으로".
    python test_speak_units.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

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

print("\n[2] 소수는 숫자째 한글로 — '삼쩜오시간'")
# ⚠ 점만 '쩜' 으로 바꾸면 "3쩜5" 가 되고 그 다음은 TTS 손에 달린다.
#   사장님 교정 2026-08-16: "3.5시간이 아니라 삼쩜오시간". 읽는 쪽에
#   맡기지 않고 여기서 확정한다.
check("여유 5.0기가", "오쩜영", "5.0 → 오쩜영")
check("스왑 2.3기가 사용", "이쩜삼", "2.3 → 이쩜삼")
check("학습 기억이 3.5시간짜리였습니다", "삼쩜오시간", "3.5시간 → 삼쩜오시간")
check("유사도 0.45입니다", "영쩜사오", "소수부는 한 자씩 읽는다")
check("12.5초 걸렸어요", "십이쩜오", "정수부는 자릿수를 살린다")
check("비용은 0.0123달러", "영쩜영일이삼", "0 도 한 자씩")
check("오늘 회의는 3시입니다.", "3시입니다", "문장 끝 마침표는 건드리지 않는다")
check("버전 1.2.3 입니다", "1.2.3", "점 두 개짜리는 소수가 아니다 — 그대로 둔다")

print("\n[3] 안 건드려야 할 것")
check("기가 막히는 일입니다", "기가 막히는", "'기가 막히다' 는 단위가 아니다")
check("깃허브에 올렸습니다", "깃허브", "숫자 없는 말은 그대로")

print(f"\n✅ 전부 통과 — {passed}건. 귀로 알아듣는 표기로 나간다")
