#!/usr/bin/env python3
"""겹쳐 들어온 일정은 한 건으로 읽는다.

2026-08-13 아침 브리핑 실측:
    "오늘 일정 45건. 16시 큐 이확인. 16시 큐 이확인.
     16시 큐 이확인. 16시 큐 이확인."
구글·아이클라우드·구독 캘린더에 같은 일정이 겹쳐 있어 EventKit 이 넷을
따로 돌려준 것이다. 읽어드릴 네 자리를 같은 일정 하나가 다 먹었다.

여기서 재는 것은 두 가지다 — 같은 것은 묶이는가, 그리고 **다른 것이
묻히지는 않는가**. 뒤엣것이 더 중요하다. 반복은 소음이지만 빠뜨림은
사장님이 약속을 놓치는 일이다.

    python test_calendar_dedup.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

from datetime import datetime

import calendar_local

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


def ev(hour, title, location="", minute=0, all_day=False):
    return {"title": title, "start": datetime(2026, 8, 13, hour, minute),
            "all_day": all_day, "location": location}


print("\n[1] 실측 그대로 — 16시 '큐 이확인' 넷")
evs = calendar_local._dedupe([ev(16, "큐 이확인")] * 4)
check("한 건으로 묶인다", len(evs), 1)
check("남은 것이 그 일정이다", evs[0]["title"], "큐 이확인")

print("\n[2] 다른 일정은 절대 묻히지 않는다")
same_title_other_time = calendar_local._dedupe([ev(10, "미팅"), ev(15, "미팅")])
check("시각이 다르면 두 건", len(same_title_other_time), 2)
same_time_other_place = calendar_local._dedupe(
    [ev(16, "미팅", "강남"), ev(16, "미팅", "본사")])
check("장소가 다르면 두 건", len(same_time_other_place), 2)
same_hour_other_minute = calendar_local._dedupe(
    [ev(16, "미팅"), ev(16, "미팅", minute=30)])
check("분이 다르면 두 건", len(same_hour_other_minute), 2)
check("빈 목록은 빈 목록", calendar_local._dedupe([]), [])

print("\n[3] 순서와 개수 — 브리핑이 세는 '오늘 N건' 이 여기서 나온다")
mixed = calendar_local._dedupe(
    [ev(9, "조회"), ev(16, "큐 이확인"), ev(16, "큐 이확인"), ev(18, "저녁")])
check("겹친 것만 빠진다", len(mixed), 3)
check("들어온 순서를 지킨다", [e["title"] for e in mixed],
      ["조회", "큐 이확인", "저녁"])

print("\n[4] 종일 일정도 같은 규칙")
check("종일 중복도 하나로",
      len(calendar_local._dedupe([ev(0, "휴가", all_day=True)] * 3)), 1)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과")
