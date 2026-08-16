#!/usr/bin/env python3
"""시각 낭독 — "08:34" 를 '공팔 삼사' 로 읽던 것 (사장님 교정 2026-08-13).

원인은 기호 제거가 콜론을 지워 숫자 두 덩이만 남긴 것. 지우기 전에
한국어 시각으로 풀어야 한다.

콜론만 풀고 숫자는 아라비아 그대로 두면 또 틀린다 — "8시"를 TTS 가
고유어(여덟시) 대신 한자어(팔시)로, "12시"를 "열두시" 대신 "십이시"로
읽는다. 사장님 교정(2026-08-16): "십이오팔이 아니고 열두시오십팔분".
소수(3.5시간→삼쩜오시간)와 같은 이유로 숫자째 한글로 확정한다.
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import speak

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


print("[1] 시각은 시각으로, 숫자는 한글로")
for src, want in [
    ("08:34 에 부르셨습니다", "여덟시 삼십사분 에 부르셨습니다"),
    ("통화 시작 08:30 끝 08:52", "통화 시작 여덟시 삼십분 끝 여덟시 오십이분"),
    ("14:00 미팅", "십사시 미팅"),                    # 정각은 '분' 을 안 붙인다
    ("09:05:07 기록", "아홉시 오분 칠초 기록"),
    ("00:00 자정", "영시 자정"),
    ("12:58 이었어요", "열두시 오십팔분 이었어요"),   # 2026-08-16 재발 — 열두시 vs 십이
]:
    check(f"{src!r}", speak.clean(src), want)

print("\n[2] 로컬 '몇 시야' 답변도 같은 규칙을 쓴다")
check("clock_words", speak.clock_words(12, 58), "열두시 오십팔분")
check("clock_words 정각", speak.clock_words(9, 0), "아홉시")
check("clock_words 13시", speak.clock_words(13, 5), "십삼시 오분")

print("\n[3] 시각이 아닌 콜론은 점수·비율 — '3대 1' (사장님 교정)")
check("점수", speak.clean("점수는 3:1 이었다"), "점수는 3대 1 이었다")
check("비율", speak.clean("비율이 2:1 입니다"), "비율이 2대 1 입니다")
check("범위 밖(25:99)도 비율로", speak.clean("25:99"), "25대 99")
check("이미 한글이면 그대로", speak.clean("오전 10시 30분 미팅"), "오전 10시 30분 미팅")

print("\n[4] 다른 낭독 규칙과 충돌하지 않는다")
# 2026-08-16 사장님 교정: "3.5시간이 아니라 삼쩜오시간". 점만 바꾸면 숫자를
# 어떻게 읽을지가 TTS 손에 남는다 — 숫자째 한글로 확정한다.
check("소수는 숫자째 한글", speak.clean("5.0기가 남음"), "오쩜영기가바이트 남음")
check("삼쩜오시간", speak.clean("3.5시간 걸려요"), "삼쩜오시간 걸려요")
check("단위 풀어읽기", speak.clean("32GB 씁니다"), "32기가바이트 씁니다")

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 시각은 시각으로 읽는다")
