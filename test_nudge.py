#!/usr/bin/env python3
"""상황 능동 검증 — 일정 임박 판정만 (스레드·소리 없이).

핵심: 창 안의 일정만, 한 번만, 종일 일정은 제외.
    python test_nudge.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import sys
from datetime import datetime, timedelta

import dongbaek

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


now = datetime(2026, 8, 11, 13, 50)
evs = [
    {"title": "강남 미팅", "start": now + timedelta(minutes=5), "all_day": False},
    {"title": "저녁 회식", "start": now + timedelta(minutes=40), "all_day": False},
    {"title": "창립기념일", "start": now + timedelta(minutes=3), "all_day": True},
    {"title": "지나간 회의", "start": now - timedelta(minutes=5), "all_day": False},
]

print("\n[1] 창(10분) 안의 시각 일정만")
announced = set()
due = dongbaek._due_meetings(evs, now, announced)
check("한 건만", len(due), 1)
check("문장", due[0][1], "5분 뒤 강남 미팅 일정입니다.")

print("\n[2] 같은 일정을 두 번 알리지 않는다")
announced.add(due[0][0])
check("재알림 없음", dongbaek._due_meetings(evs, now, announced), [])

print("\n[3] 시간이 흘러 창에 들어오면 그때 알린다")
later = now + timedelta(minutes=31)
due2 = dongbaek._due_meetings(evs, later, announced)
check("회식이 창에 들어옴", [d[1] for d in due2], ["9분 뒤 저녁 회식 일정입니다."])

print("\n[4] 달력에 똑같은 일정이 여러 개여도 한 번만 알린다")
# [2] 는 '호출과 호출 사이' 의 중복만 막는다. announced 는 말한 뒤에야
# 갱신되므로, 한 호출 안에서 따로 거르지 않으면 N개짜리 중복 일정을 N번
# 말한다. 2026-08-13 15:50 실측: 4개가 4초간 4번, 다음날치엔 36개가 쌓여
# 있었다 (음성으로 잘못 등록된 것).
dup_evs = [{"title": "큐 이확인", "start": now + timedelta(minutes=5),
            "all_day": False} for _ in range(36)]
dup_due = dongbaek._due_meetings(dup_evs, now, set())
check("36개여도 한 번만", len(dup_due), 1)
check("문장은 그대로", dup_due[0][1], "5분 뒤 큐 이확인 일정입니다.")

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과 — 먼저 말을 걸되, 한 번만")
