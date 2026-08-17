#!/usr/bin/env python3
"""'회의 모드' 는 회의가 아니다 — 2026-08-17 사고의 회귀 시험.

사장님이 개발을 부탁하셨다.

    "그럼 그거 개발해 줘. 지금 회의 모드, 전화 모드, 지피티 모드를 고도화 시켜줘"

동백의 답은 이랬다.

    "개발해 관련 일정은 찾지 못했어요"

'회의' 를 일정 낱말로 읽고, 남은 '개발해' 를 검색어로 삼아 캘린더를 뒤진
것이다. 개발 부탁이 일정 조회로 통째로 샜다.

⚠ 걸린 쪽만 고치면 안 된다. 실측에서 '전화 모드 고도화해줘' 는 안 걸리고
  '회의 모드 개발해 줘' 만 걸렸다 — 일관성이 없었다. 낱말 하나를 빼면
  다음엔 '미팅 모드' 로 또 난다. 그래서 꼴로 막는다: 일정 낱말 뒤에
  '모드' 가 붙으면 그건 동백의 기능 이름이라 일정 판정에서 빼고 본다.

여기서 지키는 것.
  1. 'X 모드' 를 손봐 달라는 말은 캘린더로 가지 않는다
  2. 진짜 일정 조회는 그대로 된다 (막다가 쓰던 기능을 죽이면 더 나쁘다)
  3. 한 문장에 둘 다 있으면 진짜 회의 쪽은 살아난다
  4. 개발 동사는 일정 이름이 될 수 없다

    python tests/test_mode_not_schedule.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import router

FAIL = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))


print("[1] 'X 모드' 개발 요청은 캘린더로 가지 않는다")
# 사고 문장 그대로 + 같은 꼴의 변형들. 하나만 막으면 다음에 또 난다.
for t in ("그럼 그거 개발해 줘. 지금 회의 모드, 전화 모드, 지피티 모드를 고도화 시켜줘",
          "회의 모드 개발해 줘",
          "미팅 모드 손봐줘",
          "화상 모드 개선해줘",
          "통화 모드 고도화 시켜줘",
          "전화 모드 고쳐줘",
          "지피티 모드 만들어줘"):
    check(f"{t[:30]!r}", router._is_schedule_query(t), False)

print("\n[2] 진짜 일정 조회는 그대로 된다")
# ⚠ 막다가 쓰던 기능을 죽이면 그게 더 나쁘다. 통과 쪽도 반드시 같이 본다.
for t in ("오늘 일정 알려줘",
          "내일 회의 언제야",
          "이번주 미팅 뭐 있어",
          "목요일 미팅 알려줘",
          "강남 미팅 언제야"):
    check(f"{t!r} 는 조회다", router._is_schedule_query(t), True)

print("\n[3] 한 문장에 둘 다 있으면 진짜 회의가 살아난다")
# '모드' 를 지우는 게 아니라 빼고 보는 이유가 이것이다.
t = "회의 모드 고치고, 내일 회의 언제야"
check("뒤쪽 진짜 회의로 조회된다", router._is_schedule_query(t), True)

print("\n[4] 개발 동사는 일정 이름이 될 수 없다")
# '고치고 관련 일정은 찾지 못했습니다' 같은 답이 나가면 안 된다.
for w in ("고치고", "손봐줘", "개선해줘", "개발해", "고도화", "구현해줘",
          "만들어줘", "수정해줘"):
    check(f"{w!r} 는 검색어가 아니다", bool(router._VERBISH.search(w)), True)

print("\n[5] 멀쩡한 일정 이름은 검색어로 남는다")
for t, want in (("강남 미팅 언제야", "강남"),
                ("농특위 일정 알려줘", "농특위")):
    check(f"{t!r} → {want!r}", router.schedule_keyword(t), want)

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — 모드는 기능 이름, 회의는 일정")
