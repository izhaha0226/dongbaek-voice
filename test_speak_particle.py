#!/usr/bin/env python3
"""조사가 붙은 시각·단위 낭독 — "14:00부터" 가 "십사 대 영영부터" 로 나가던 것.

시각 풀이(2026-08-13)와 단위 풀이(32GB→기가바이트)는 낱말 끝을 `\\b` 로
잡고 있었다. 한국어에서는 그 경계가 서지 않는다 — 조사가 딱 붙어 오고
한글도 낱말 문자라 '00' 과 '부' 사이에는 경계가 없다. 그래서 조사가 붙은
순간 규칙이 통째로 건너뛰어졌고, 시각은 남은 콜론이 비율 규칙에 걸려
"십사 대 영영" 이, 단위는 "삼십이지비" 가 소리로 나갔다.

⚠ 비율(3:1)과 범위 밖(25:99)은 그대로 비율이어야 한다. 조사를 받아들이려고
  숫자 배제까지 풀면 점수가 시각으로 둔갑한다.
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


print("[1] 시각에 조사가 붙어도 시각으로 읽는다")
for src, want in [
    ("14:00부터 회의", "십사시부터 회의"),
    ("9:30에 출발하세요", "아홉시 삼십분에 출발하세요"),
    ("12:58쯤 오셨어요", "열두시 오십팔분쯤 오셨어요"),
    ("회의는 15:00입니다", "회의는 십오시입니다"),
    ("09:05:07까지 기록", "아홉시 오분 칠초까지 기록"),
]:
    check(f"{src!r}", speak.clean(src), want)

print("\n[2] 비율·점수는 여전히 비율이다")
check("점수", speak.clean("점수는 3:1로 이겼다"), "점수는 3대 1로 이겼다")
check("범위 밖(25:99)", speak.clean("25:99까지"), "25대 99까지")
check("숫자가 더 붙으면 시각이 아니다", speak.clean("125:30 어쩌고"), "125대 30 어쩌고")

print("\n[3] 단위도 조사가 붙은 채로 풀어 읽는다")
check("32GB를", speak.clean("32GB를 씁니다"), "32기가바이트를 씁니다")
check("48GB가", speak.clean("메모리 48GB가 남았어요"), "메모리 48기가바이트가 남았어요")
check("300ms로", speak.clean("응답 300ms로 줄었어요"), "응답 300밀리초로 줄었어요")
# 단위 뒤에 알파벳이 이어지면 다른 낱말이다 — 거기까진 건드리지 않는다.
check("GBps 는 그대로", speak.clean("10GBps 링크"), "10GBps 링크")

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 조사가 붙어도 시각은 시각, 단위는 단위")
