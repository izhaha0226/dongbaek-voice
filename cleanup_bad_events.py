#!/usr/bin/env python3
"""잘못 등록된 캘린더 일정을 골라 지운다 — 2026-08-14 받아쓰기 사고 정리.

그날 통화 내용을 구술하시는 동안 전사본이 통째로 일정으로 등록됐다.
제목이 수백 자짜리라 캘린더에서 손으로 지우기도 번거롭다.

  기본은 보여주기만 한다. 실제로 지우려면 --delete 를 붙인다.

    python tools/cleanup_bad_events.py               # 무엇이 지워질지 확인
    python tools/cleanup_bad_events.py --delete      # 실행

  ⚠ 반드시 `.venv/bin/python` 으로 돌려야 한다. 캘린더 접근 권한(TCC)은
    launchd 잡이 쓰는 그 파이썬에 붙어 있다 — 시스템 python3 나 홈브루
    Python.app 으로 돌리면 '권한 없음' 만 찍고 끝난다.

고르는 기준은 두 가지뿐이고, 지우기 전에 전부 눈으로 보게 한다.
  - 제목이 TITLE_MAX 자를 넘는 일정  (받아쓰기가 통째로 들어간 것)
  - --qa 를 주면 QA 시험용으로 남은 '큐 이확인' 반복 일정
"""
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import calendar_local as C

TITLE_MAX = 40          # 이보다 긴 제목은 제목이 아니라 문단이다
WINDOW_DAYS = 60        # 앞뒤로 훑을 범위

# QA 시험 잔여물. 받아쓰기가 'QA' 를 적는 방식이 여러 가지라 한 꼴만
# 잡으면 다음 번엔 또 놓친다. 실제로 그랬다 — 2026-08-14 에는 제목이
# '큐 이확인'(낱말 사이가 벌어진 꼴)이었고 그것만 넣어 뒀는데,
# 제목 추출을 고친 뒤로는 '큐에이확인'(붙은 꼴)로 쌓여서 이 도구가
# 32건을 그냥 지나쳤다 (2026-08-17 사장님: "QA 일정이 왜 이렇게 많아").
#
# 그래서 낱말 사이 공백을 무시하고 '큐에이/큐 이/QA' 를 함께 본다.
#
# ⚠ 'QA' 뒤에 \b 를 쓰면 안 된다. 파이썬에서 한글은 \w 라 'QA확인' 의
#   'A' 와 '확' 사이에는 경계가 없다 — 붙여 쓴 꼴이 통째로 빠진다.
#   앞쪽만 막아 'AQA' 같은 말에 안 걸리게 한다.
_QA_TITLE = re.compile(r"(큐\s*에?\s*이|(?<![A-Za-z])QA)\s*(확인|점검|테스트|체크)")


def main() -> int:
    do_delete = "--delete" in sys.argv
    with_qa = "--qa" in sys.argv

    store = C._get_store()
    if store is None:
        print("캘린더 권한이 없습니다. 사장님 터미널에서 실행해 주세요.")
        return 1

    from Foundation import NSDate

    now = datetime.now()
    lo = NSDate.dateWithTimeIntervalSince1970_(
        (now - timedelta(days=WINDOW_DAYS)).timestamp())
    hi = NSDate.dateWithTimeIntervalSince1970_(
        (now + timedelta(days=WINDOW_DAYS)).timestamp())
    pred = store.predicateForEventsWithStartDate_endDate_calendars_(lo, hi, None)

    targets = []
    for ev in store.eventsMatchingPredicate_(pred) or []:
        title = ev.title() or ""
        if len(title) > TITLE_MAX:
            targets.append((ev, title, "받아쓰기 통째"))
        elif with_qa and _QA_TITLE.search(title):
            targets.append((ev, title, "QA 시험 잔여"))

    if not targets:
        print("지울 일정이 없습니다.")
        return 0

    print(f"대상 {len(targets)}건"
          f" ({'삭제 실행' if do_delete else '확인만 — 지우려면 --delete'})\n")
    for ev, title, why in targets:
        st = C._to_dt(ev.startDate())
        rec = " · 반복" if ev.hasRecurrenceRules() else ""
        print(f"  {st:%m/%d %H:%M} | {len(title):>4}자 | {why}{rec}")
        print(f"      {title[:60]}{'…' if len(title) > 60 else ''}")

    if not do_delete:
        return 0

    import EventKit

    done = failed = 0
    for ev, title, _why in targets:
        # 반복 일정은 이후 회차까지 함께 지운다. 한 회차만 지우면 내일 또 뜬다.
        span = (EventKit.EKSpanFutureEvents if ev.hasRecurrenceRules()
                else EventKit.EKSpanThisEvent)
        ok, err = store.removeEvent_span_error_(ev, span, None)
        if ok:
            done += 1
        else:
            failed += 1
            print(f"  ✗ 실패: {title[:30]} — {err}")

    print(f"\n지움 {done}건" + (f", 실패 {failed}건" if failed else ""))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
