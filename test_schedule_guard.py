#!/usr/bin/env python3
"""받아쓰기가 일정 등록으로 새지 않는가 — 2026-08-14 사고의 회귀 시험.

그날 사장님이 전날 미팅 내용을 구술하시는 동안, 말을 보태실 때마다
앞말과 합쳐진 전사본 전체(끝내 3,499자)가 다시 명령으로 처리됐다.
그때마다 일정이 만들어져 캘린더에 열 건이 남았다. 게이트를 통과시킨 건
"목표 매출 한번 만들어보자" 의 '만들' 이었고, 시각은 본문의 "4시 반",
길이는 "4시간 동안 얘기 들었다" 에서 왔다. 제목은 전사본 통째였다.

여기서 지키는 건 두 가지다.
  - 긴 말(200자 초과)에는 일정을 쓰지 않는다. 지시는 짧다.
  - 제목이 40자를 넘으면 제목을 뽑은 게 아니라 문단을 실어 나른 것이다.

짧고 평범한 등록 지시는 그대로 통과해야 한다. 문턱을 세우다 정작
쓰던 기능을 막으면 그게 더 나쁘다 — 그래서 통과 쪽도 같이 본다.

    python tests/test_schedule_guard.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import sys

import calendar_local
import router

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


# 실제 등록 대신 호출만 받아 적는다. 시험이 사장님 캘린더를 건드리면 안 된다.
_calls = []


def _fake_create(title, start, hours=1.0):
    _calls.append({"title": title, "start": start, "hours": hours})
    return "등록했습니다."


def run(text):
    """_handle_schedule_write 를 태우고, 등록이 일어났는지 돌려준다."""
    _calls.clear()
    return router._handle_schedule_write(text, router.normalize(text))


# ── 사고 재현 ──────────────────────────────────────────────
# 그날 전사본의 뼈대만 남긴 것. 일정 낱말('미팅')·등록 동사('만들')·
# 시각('4시 반')·기간('4시간 동안') 이 한 문단에 우연히 모여 있다.
LONG_DICTATION = (
    "어제 3시 반부터 미팅했는데 키워드 광고 얘기를 4시간 동안 들었어. "
    "본인이 광고 파트가 답답해서 직접 뛰어들었고 키워드 15만 개를 "
    "세팅해놨다고 하시더라고. 제안서를 보시더니 코드가 안 맞았으면 "
    "5분 만에 일어났을 거라면서, 자기 목표가 연매출 목표 매출이다, "
    "목표 매출 한번 만들어보자 하시고 저녁까지 먹고 가라고 하셨어. "
    "4시 반쯤 홈페이지 문제도 물어보셨고. 그냥 저장만 하고 요약 정리해 둬."
)

print("받아쓰기 길이:", len(LONG_DICTATION), "자")
print("\n[사고 재현 — 긴 받아쓰기]")
check("긴 받아쓰기는 200자를 넘는다", len(LONG_DICTATION) > 200, True)
check("긴 받아쓰기에 일정을 쓰지 않는다", run(LONG_DICTATION), None)
check("create 를 부르지 않았다", len(_calls), 0)

# ── 문턱을 세우다 막지 말아야 할 것들 ──────────────────────
print("\n[짧고 평범한 등록 지시는 통과]")
calendar_local.create = _fake_create      # 여기서부터만 가로챈다
for cmd in ("내일 3시 치과 등록해줘",
            "내일 4시 큐에이확인 등록해줘",
            "오늘 일정에 대표자 변경의 건으로 서류 전달 11시 등록해줘"):
    run(cmd)
    ok = len(_calls) == 1
    if not ok:
        FAIL.append(cmd)
    got = _calls[0]["title"] if _calls else None
    print(f"  {'✓' if ok else '✗'} {cmd}"
          + (f"  → 제목 {got!r}" if ok else "  → 등록되지 않음"))
    if ok:
        check(f"    제목이 {router._TITLE_MAX_CHARS}자 이하",
              len(got) <= router._TITLE_MAX_CHARS, True)

# ── 제목 문턱 ──────────────────────────────────────────────
print("\n[제목이 길면 등록하지 않는다]")
check("_safe_title: 정상", router._safe_title("치과"), "치과")
check("_safe_title: 한 글자", router._safe_title("치"), None)
check("_safe_title: 빈 값", router._safe_title(""), None)
check("_safe_title: 41자", router._safe_title("가" * 41), None)
check("_safe_title: 40자", router._safe_title("가" * 40), "가" * 40)

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과")
