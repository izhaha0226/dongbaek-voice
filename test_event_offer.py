#!/usr/bin/env python3
"""통화·회의에서 나온 일정을 뽑아 여쭙는가 — 2026-08-16 지시의 시험.

사장님 지시: "일정이 생기면 바로 일정으로 등록해주는 기능".

⚠ 바로 넣지는 않는다. 2026-08-14 에 사장님이 메일에서 뽑은 일정 자동 등록을
  직접 끄셨고("메일만 보고 뽑은 일정은 정확하지 않다"), 8/15 에는 받아쓰기가
  일정이 되어 생긴 쓰레기 56건을 지웠다. 통화 받아쓰기는 메일보다 험하다 —
  같은 노트 안에서 이름이 '충무화(?)' 처럼 흔들린다. 그래서 뽑아서 여쭙고,
  승인하시면 그때 넣는다.

여기서 지키는 것.
  1. 때가 분명한 것만 후보다. 흐릿한 것·지난 것은 노트에만 남는다
  2. 후보는 30분만 살아 있다 — 한참 뒤의 "응" 이 엉뚱한 걸 넣으면 안 된다
  3. 여쭙는 말에 언제·무엇이 들어간다 (귀로 듣고 판단하실 수 있게)

    python tests/test_event_offer.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import call_notes

FAIL = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))


SUMMARY = """통화 한 줄 요약.

## 오간 내용
- 견적서 얘기

## 다음 일정·약속
- [내일 오후 3시] 세종 축산환경관리원 미팅
- [다음주 월요일 10시] 그로우아시아 견적 회신
- 조만간 백 회장님께 말씀 전달
- [어제 3시] 이미 지난 것
- [내일 4시] 가
"""

print("[1] 때가 분명한 것만 후보로 삼는다")
ev = call_notes.extract_events(SUMMARY)
titles = [e["title"] for e in ev]
check("두 건만 뽑힌다", len(ev), 2)
check("세종 미팅이 있다", "세종 축산환경관리원 미팅" in titles, True)
check("때가 흐릿한 것은 안 뽑는다",
      any("백 회장" in t for t in titles), False)
check("지난 일정은 안 뽑는다", any("지난" in t for t in titles), False)
check("제목이 한 글자면 안 뽑는다", any(t == "가" for t in titles), False)
check("때가 앞으로다", all(datetime.fromisoformat(e["when"]) > datetime.now()
                       for e in ev), True)

print("\n[2] 절이 없거나 비면 조용하다")
check("일정 절이 없으면 빈손", call_notes.extract_events("## 오간 내용\n- 잡담"), [])
check("(없음) 이면 빈손",
      call_notes.extract_events("## 다음 일정·약속\n(없음)"), [])
check("여쭙는 말도 없다", call_notes.ask_line([]), "")

print("\n[3] 여쭙는 말에 언제·무엇이 들어간다")
line = call_notes.ask_line(ev)
check("등록할지 묻는다", "등록할까요" in line, True)
check("무엇인지 말한다", "세종 축산환경관리원 미팅" in line, True)
check("몇 건인지 말한다", "외 1건" in line, True)

print("\n[4] 후보는 30분만 살아 있다")
_real = call_notes._PENDING
call_notes._PENDING = ROOT / "state" / "pending_events_test.json"
try:
    call_notes.stash_events(ev, "통화")
    check("적어 두면 남아 있다", call_notes.has_pending(), True)
    got, src = call_notes.take_events()
    check("꺼내면 그대로", len(got), 2)
    check("출처도 함께", src, "통화")
    check("꺼내면 사라진다", call_notes.has_pending(), False)

    # 30분 지난 것은 꺼내지지 않는다
    import json

    call_notes._PENDING.write_text(json.dumps({
        "at": (datetime.now() - timedelta(seconds=call_notes.PENDING_TTL_SEC + 60))
        .isoformat(timespec="seconds"),
        "source": "통화", "events": ev}, ensure_ascii=False))
    old, _ = call_notes.take_events()
    check("30분 지난 후보는 버린다", old, [])
finally:
    try:
        call_notes._PENDING.unlink()
    except OSError:
        pass
    call_notes._PENDING = _real

print("\n[5] 두 정리본 모두 일정 절을 청한다")
check("통화 템플릿에 있다", "다음 일정·약속" in call_notes._CALL_SHAPE, True)
check("회의록 템플릿에도 있다", "다음 일정·약속" in call_notes._MEETING_SHAPE, True)
check("지어내지 말라고 이른다", "지어내지 마라" in call_notes._WHEN_RULE, True)

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — 뽑아서 여쭙고, 넣는 건 사장님 한마디를 받는다")
