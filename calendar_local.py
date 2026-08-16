"""맥 캘린더 직접 조회 — EventKit. 토큰 0.

왜 AppleScript 가 아닌가:
  Calendar.app 의 `every event whose start date ≥ …` 는 3분이 지나도 안 끝난다.
  이벤트를 전부 훑으며 AppleEvent 로 왕복하기 때문이다.
  EventKit 은 같은 데이터를 캘린더 DB 에서 바로 읽어 밀리초 단위로 끝난다.

권한: 최초 실행 시 macOS 가 캘린더 접근을 묻는다. 터미널에서 한 번 허용해야 한다.
"""

from datetime import datetime, timedelta

import config

_store = None
_denied = False


def _get_store():
    """EventKit 스토어를 준비한다. 권한이 없으면 None."""
    global _store, _denied
    if _store is not None or _denied:
        return _store
    try:
        import EventKit
        from Foundation import NSRunLoop, NSDate

        store = EventKit.EKEventStore.alloc().init()
        done = {"ok": None}

        def handler(granted, err):
            done["ok"] = bool(granted)

        # macOS 14+ 는 전용 API 를 쓴다. 없으면 구형 API 로 내려간다.
        if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
            store.requestFullAccessToEventsWithCompletion_(handler)
        else:
            store.requestAccessToEntityType_completion_(0, handler)

        # 콜백이 올 때까지 런루프를 돌린다 (최대 10초)
        loop = NSRunLoop.currentRunLoop()
        deadline = NSDate.dateWithTimeIntervalSinceNow_(10)
        while done["ok"] is None and NSDate.date().compare_(deadline) < 0:
            loop.runMode_beforeDate_(
                "NSDefaultRunLoopMode", NSDate.dateWithTimeIntervalSinceNow_(0.1)
            )

        if not done["ok"]:
            _denied = True
            return None
        _store = store
        return _store
    except Exception:
        _denied = True
        return None


def _to_dt(nsdate):
    from Foundation import NSTimeZone

    try:
        ts = nsdate.timeIntervalSince1970()
        return datetime.fromtimestamp(ts)
    except Exception:
        return None


def events(days: int = 7, calendar_name: str | None = None,
           include_past_today: bool = False) -> list[dict] | None:
    """앞으로 `days` 일간의 일정. 권한이 없으면 None (호출측이 Claude 로 폴백).

    include_past_today=True 면 오늘 자정부터 본다 — 업무일지에는
    '이미 지난 오늘 미팅' 이 필요하다.
    """
    store = _get_store()
    if store is None:
        return None
    try:
        import EventKit
        from Foundation import NSDate

        want = calendar_name or config.CALENDAR_MAIN
        cals = [c for c in store.calendarsForEntityType_(0)
                if want.lower() in (c.title() or "").lower()] if want else None
        if not cals:
            cals = list(store.calendarsForEntityType_(0))

        if include_past_today:
            from datetime import datetime as _dt

            _now = _dt.now()
            _midnight = _now.replace(hour=0, minute=0, second=0, microsecond=0)
            start = NSDate.dateWithTimeIntervalSinceNow_((_midnight - _now).total_seconds())
        else:
            start = NSDate.date()
        end = NSDate.dateWithTimeIntervalSinceNow_(days * 86400)
        pred = store.predicateForEventsWithStartDate_endDate_calendars_(start, end, cals)

        out = []
        for e in store.eventsMatchingPredicate_(pred) or []:
            sd = _to_dt(e.startDate())
            if sd is None:
                continue
            out.append({
                "title": (e.title() or "제목 없음"),
                "start": sd,
                "all_day": bool(e.isAllDay()),
                "location": e.location() or "",
            })
        out.sort(key=lambda x: x["start"])
        return _dedupe(out)
    except Exception:
        return None


def _dedupe(evs: list[dict]) -> list[dict]:
    """같은 시각·같은 제목·같은 장소는 한 건으로 친다.

    구글·아이클라우드·구독 캘린더에 같은 일정이 겹쳐 들어와 있으면
    EventKit 은 그걸 전부 따로 돌려준다. 2026-08-13 아침 브리핑이
    "오늘 일정 45건. 16시 큐 이확인. 16시 큐 이확인. 16시 큐 이확인.
    16시 큐 이확인." 으로 나갔다 — 읽어드릴 네 자리를 같은 일정 하나가
    다 먹어서 나머지는 한 마디도 못 나갔다. 개수(45건)도 같은 이유로 부풀었다.

    ⚠ 장소까지 같아야 묶는다. 시각·제목만 보면 이름이 같고 장소가 다른
      '진짜 두 건' 이 하나로 묻힌다 — 반복은 소음일 뿐이지만 빠뜨림은
      사장님이 약속을 놓치는 일이다.
    """
    seen = set()
    out = []
    for e in evs:
        key = (e["start"], e["title"], e.get("location") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _main_calendar(store):
    """메인 캘린더 객체. 못 찾으면 기본 캘린더."""
    want = (config.CALENDAR_MAIN or "").lower()
    for c in store.calendarsForEntityType_(0) or []:
        if want and want in (c.title() or "").lower():
            return c
    return store.defaultCalendarForNewEvents()


def _nsdate(dt):
    from Foundation import NSDate

    return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


def create(title: str, start: datetime, hours: float = 1.0) -> str | None:
    """일정 등록. 성공하면 안내 문장, 실패하면 None (→ Claude 폴백).

    ⚠ 이 함수는 되돌릴 수 없는 작업이다. 반드시 위험 게이트를 통과한 뒤에만
      호출되어야 한다 (dongbaek.handle 이 danger 판정을 먼저 한다).
    """
    store = _get_store()
    if store is None:
        return None
    try:
        import EventKit

        cal = _main_calendar(store)
        if cal is None:
            return None
        ev = EventKit.EKEvent.eventWithEventStore_(store)
        ev.setTitle_(title)
        ev.setStartDate_(_nsdate(start))
        ev.setEndDate_(_nsdate(start + timedelta(hours=hours)))
        ev.setCalendar_(cal)
        ok, err = store.saveEvent_span_error_(ev, 0, None)
        if not ok:
            return None
        d = start
        yo = _WEEK[d.weekday()]
        ampm = "오전" if d.hour < 12 else "오후"
        h12 = d.hour if 1 <= d.hour <= 12 else (d.hour - 12 if d.hour > 12 else 12)
        when = f"{d.month}월 {d.day}일 {yo}요일 {ampm} {h12}시"
        if d.minute:
            when += f" {d.minute}분"
        return f"{when}에 {title} 일정을 등록했습니다."
    except Exception:
        return None


def delete_matching(keyword: str, days: int = 60) -> str | None:
    """제목에 keyword 가 든 다가오는 일정 하나를 지운다.

    ⚠ 되돌릴 수 없다. 위험 게이트 통과 후에만 호출할 것.
    여러 건이 걸리면 지우지 않고 되묻는다 — 엉뚱한 걸 지우는 것보다 낫다.
    """
    store = _get_store()
    if store is None:
        return None
    try:
        from Foundation import NSDate

        cal = _main_calendar(store)
        start = NSDate.date()
        end = NSDate.dateWithTimeIntervalSinceNow_(days * 86400)
        pred = store.predicateForEventsWithStartDate_endDate_calendars_(
            start, end, [cal] if cal else None)
        hits = [e for e in (store.eventsMatchingPredicate_(pred) or [])
                if keyword.lower() in (e.title() or "").lower()]
        if not hits:
            return f"'{keyword}' 와 맞는 일정을 찾지 못했습니다."
        if len(hits) > 1:
            names = ", ".join((e.title() or "") for e in hits[:3])
            return (f"'{keyword}' 로 {len(hits)}개가 걸립니다: {names}. "
                    f"어느 것인지 더 정확히 말씀해 주세요.")
        ev = hits[0]
        title = ev.title() or "일정"
        ok, err = store.removeEvent_span_error_(ev, 0, None)
        return f"{title} 일정을 삭제했습니다." if ok else None
    except Exception:
        return None


def delete_day(target, label: str = "오늘") -> str | None:
    """그날 일정을 전부 지운다 — "오늘 일정 전부 삭제해" (2026-08-13 지시).

    ⚠ 되돌릴 수 없다. 단건 삭제와 달리 걸리는 게 여러 개인 게 정상이라,
    반드시 위험 게이트(복창+진행)를 통과한 뒤에만 부를 것 — router 가
    danger 목록으로 보장한다.
    """
    store = _get_store()
    if store is None:
        return None
    try:
        from datetime import datetime, timedelta

        from Foundation import NSDate

        day0 = datetime(target.year, target.month, target.day)
        start = NSDate.dateWithTimeIntervalSince1970_(day0.timestamp())
        end = NSDate.dateWithTimeIntervalSince1970_(
            (day0 + timedelta(days=1)).timestamp())
        cal = _main_calendar(store)
        pred = store.predicateForEventsWithStartDate_endDate_calendars_(
            start, end, [cal] if cal else None)
        hits = list(store.eventsMatchingPredicate_(pred) or [])
        if not hits:
            return f"{label} 일정이 없습니다."
        removed = 0
        for ev in hits:
            ok, _err = store.removeEvent_span_error_(ev, 0, None)
            removed += 1 if ok else 0
        return f"{label} 일정 {removed}개를 전부 삭제했습니다."
    except Exception:
        return None


def _match(events, keyword: str) -> list:
    """제목이 keyword 와 맞는 일정들.

    통째로 넣어보고, 안 되면 낱말 단위로 본다. 말한 순서와 제목의 순서가
    다르기 때문이다 — "본사 미팅" 이라고 하시는데 제목은
    "[미팅] 본사 김철수" 이다. 통째로만 찾으면 영영 못 만난다.

    낱말이 '전부' 들어 있어야 한다. 하나라도 맞으면 잡는 식으로 하면
    '미팅' 한 낱말에 온 일정이 다 걸린다.
    """
    key = keyword.lower().strip()
    whole = [e for e in events if key in (e.title() or "").lower()]
    if whole:
        return whole
    words = [w for w in key.split() if len(w) >= 2]
    if not words:
        return []
    return [e for e in events
            if all(w in (e.title() or "").lower() for w in words)]


def reschedule(keyword: str, start: datetime, hours: float | None = None,
               days: int = 60) -> str | None:
    """제목에 keyword 가 든 일정 하나의 시각을 옮긴다.

    2026-08-12 에 없어서 만들었다. "10시 미팅을 10시 반으로 옮겨줘" 를
    동백이 처리하지 못하자 "이 세션에서는 캘린더 쓰는 기능이 꺼져 있어서
    안 됩니다" 라고 답했다. 앞부분(못 한다)은 사실이고 뒷부분(세션)은
    지어낸 말이었다. 없는 기능이면 만들면 될 일이었다.

    삭제와 달리 내용이 사라지지 않는다 — 잘못 옮기면 다시 옮기면 된다.
    그래서 등록과 같은 무게로 다룬다. 다만 대상이 여럿이면 손대지 않고
    되묻는다. 엉뚱한 일정을 옮기는 건 안 옮기느니만 못하다.
    """
    store = _get_store()
    if store is None:
        return None
    try:
        from Foundation import NSDate

        cal = _main_calendar(store)
        # ⚠ 오늘 0시부터 본다. 지금부터 앞만 보면 이미 시작한 일정을 못
        #   옮긴다 — "10시 미팅인데 늦어서 11시로 옮겨줘" 가 바로 그 경우다.
        #   실측 2026-08-12 10:47, 10시 본사 미팅을 못 찾았다.
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        pred = store.predicateForEventsWithStartDate_endDate_calendars_(
            _nsdate(midnight), NSDate.dateWithTimeIntervalSinceNow_(days * 86400),
            [cal] if cal else None)
        hits = _match(store.eventsMatchingPredicate_(pred) or [], keyword)
        if not hits:
            return f"'{keyword}' 와 맞는 일정을 찾지 못했습니다."
        if len(hits) > 1:
            names = ", ".join((e.title() or "") for e in hits[:3])
            return (f"'{keyword}' 로 {len(hits)}개가 걸립니다: {names}. "
                    f"어느 것인지 더 정확히 말씀해 주세요.")

        ev = hits[0]
        title = ev.title() or "일정"
        # 길이를 안 주면 원래 길이를 지킨다 — "옮겨줘" 는 시각만 바꾸라는 뜻이다.
        if hours is None:
            old_s, old_e = _to_dt(ev.startDate()), _to_dt(ev.endDate())
            span = (old_e - old_s) if (old_s and old_e) else timedelta(hours=1)
        else:
            span = timedelta(hours=hours)
        ev.setStartDate_(_nsdate(start))
        ev.setEndDate_(_nsdate(start + span))
        ok, err = store.saveEvent_span_error_(ev, 0, None)
        if not ok:
            return None
        return f"{title} 일정을 {_when_words(start)}로 옮겼습니다."
    except Exception:
        return None


def _when_words(d: datetime) -> str:
    """소리내어 읽을 시각. create 와 같은 말투를 쓴다."""
    ampm = "오전" if d.hour < 12 else "오후"
    h12 = d.hour if 1 <= d.hour <= 12 else (d.hour - 12 if d.hour > 12 else 12)
    when = f"{d.month}월 {d.day}일 {_WEEK[d.weekday()]}요일 {ampm} {h12}시"
    if d.minute:
        when += f" {d.minute}분"
    return when


_WEEK = "월화수목금토일"


def speak_events(days: int = 7, limit: int = 3, only_date=None,
                 keyword: str | None = None) -> str | None:
    """음성으로 읽을 문장. 조회 실패 시 None.

    only_date 를 주면 그 하루만, keyword 를 주면 이름이 맞는 것만 읽는다.
    "목요일 미팅" 에 일주일치를, "강남 미팅" 에 전체 일정을 통째로 읽어주던
    문제 때문에 생겼다 — 콕 집어 물었는데 브리핑이 돌아오면 사장님이
    원하는 한 줄을 직접 골라내야 한다.
    """
    # 이름으로 콕 집어 물으시면 오늘 지난 것도 본다.
    #
    # "본사 미팅 몇시야" 를 오후에 물으시면, 그 미팅은 오전에 끝났어도
    # 몇 시였는지는 알려드려야 한다. 앞만 보면 "찾지 못했습니다" 가 나간다
    # (실측 2026-08-12 22:3x, 10시 본사 미팅을 22시에 물었을 때).
    #
    # 이름 없이 물으실 때는 그대로 앞만 본다 — "오늘 일정" 에 이미 끝난
    # 것까지 읽으면 지금 뭘 해야 하는지가 묻힌다.
    # only_date 로 '그 하루' 를 물으실 때도 지난 것을 본다.
    # "오늘 일정 알려줘" 를 저녁에 물으셨는데 "잡힌 일정이 없습니다" 가
    # 나가면 거짓말처럼 들린다 — 오늘 세 건이 있었는데 다 지났을 뿐이다.
    # 반면 기간(주간) 브리핑은 앞만 본다. 지난 것까지 읽으면 지금 뭘
    # 해야 하는지가 묻힌다.
    evs = events(days, include_past_today=bool(keyword) or only_date is not None)
    if evs is None:
        return None

    if keyword:
        want = keyword.lower()
        evs = [e for e in evs if want in (e.get("title") or "").lower()]
        if not evs:
            return f"{keyword} 관련 일정은 찾지 못했습니다."

    if only_date is not None:
        evs = [e for e in evs if e["start"].date() == only_date]
        label = f"{only_date.month}월 {only_date.day}일 {_WEEK[only_date.weekday()]}요일"
        if not evs:
            return f"{label}에 잡힌 일정이 없습니다."
    else:
        label = None

    if not evs:
        return f"앞으로 {days}일 안에 잡힌 일정이 없습니다."

    parts = []
    for e in evs[:limit]:
        d = e["start"]
        yo = _WEEK[d.weekday()]
        when = f"{d.month}월 {d.day}일 {yo}요일"
        if not e["all_day"]:
            hour = d.hour
            ampm = "오전" if hour < 12 else "오후"
            h12 = hour if 1 <= hour <= 12 else (hour - 12 if hour > 12 else 12)
            when += f" {ampm} {h12}시" + (f" {d.minute}분" if d.minute else "")
        else:
            when += " 하루 종일"
        parts.append(f"{when} {e['title']}")

    if keyword:
        head = (f"{keyword} 일정입니다. " if len(evs) == 1
                else f"{keyword} 관련 일정 {len(evs)}개입니다. ")
    elif label:
        head = f"{label}에 일정 {len(evs)}개 있습니다. "
    else:
        head = f"앞으로 {days}일 안에 일정 {len(evs)}개 있습니다. "
    body = ", ".join(parts) + "입니다."
    tail = f" 그 외 {len(evs) - limit}개 더 있습니다." if len(evs) > limit else ""
    return head + body + tail
