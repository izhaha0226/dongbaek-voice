"""한국어 날짜·시각 표현 파서 — 토큰 0.

"내일 오후 3시" 같은 정형화된 말을 datetime 으로 바꾼다.
확신이 없으면 None 을 돌려주고, 호출측은 Claude 로 넘긴다.
어설프게 추측해서 엉뚱한 날짜에 일정을 잡는 것보다 낫다.
"""

import re
from datetime import datetime, timedelta

_WEEK = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}

# 한자어 수사 — "세시", "다섯시" 처럼 쓰는 경우
_KO_NUM = {
    "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6,
    "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "열한": 11, "열두": 12,
}
_KO_MIN = {"반": 30}


def _base(now: datetime) -> datetime:
    return now.replace(second=0, microsecond=0)


def parse_date(text: str, now: datetime | None = None) -> datetime | None:
    """날짜만 뽑는다 (시각은 00:00). 못 찾으면 None."""
    now = _base(now or datetime.now())
    t = text.replace(" ", "")
    today = now.replace(hour=0, minute=0)

    # 8월 12일 / 8/12
    m = re.search(r"(\d{1,2})월\s*(\d{1,2})일", text) or re.search(r"\b(\d{1,2})/(\d{1,2})\b", text)
    if m:
        mo, d = int(m.group(1)), int(m.group(2))
        year = now.year + (1 if (mo, d) < (now.month, now.day) else 0)
        try:
            return today.replace(year=year, month=mo, day=d)
        except ValueError:
            return None

    if "모레" in t or "내일모레" in t:
        return today + timedelta(days=2)
    if "내일" in t:
        return today + timedelta(days=1)
    if "오늘" in t:
        return today
    if "어제" in t:
        return today - timedelta(days=1)

    # 이번주 목요일 / 다음주 화요일 / 그냥 목요일
    m = re.search(r"(이번주|금주|다음주|담주|차주)?.{0,3}?([월화수목금토일])요일", t)
    if m:
        want = _WEEK[m.group(2)]
        delta = (want - today.weekday()) % 7
        if m.group(1) in ("다음주", "담주", "차주"):
            delta += 7
            if today.weekday() > want:
                delta -= 7 * 0   # 이미 다음 주로 넘어감
        elif delta == 0:
            delta = 7            # "목요일"인데 오늘이 목요일이면 다음 목요일
        return today + timedelta(days=delta)

    return None


def parse_time(text: str) -> tuple[int, int] | None:
    """시각(시, 분)을 뽑는다. 못 찾으면 None."""
    t = text.replace(" ", "")
    pm = ("오후" in t) or ("저녁" in t) or ("밤" in t)
    am = ("오전" in t) or ("아침" in t) or ("새벽" in t)

    hour = minute = None

    m = re.search(r"(\d{1,2})시\s*(\d{1,2})?분?", text)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0
    else:
        for word, val in sorted(_KO_NUM.items(), key=lambda x: -len(x[0])):
            if f"{word}시" in t:
                hour = val
                minute = 0
                break
        if hour is None:
            m = re.search(r"(\d{1,2}):(\d{2})", text)
            if m:
                hour, minute = int(m.group(1)), int(m.group(2))

    if hour is None:
        return None
    if "반" in t and minute == 0:
        minute = _KO_MIN["반"]

    if pm and hour < 12:
        hour += 12
    elif am and hour == 12:
        hour = 0
    elif not pm and not am and hour <= 7:
        # "3시에 미팅" 은 업무 맥락상 오후로 본다 (새벽 3시일 리 없다)
        hour += 12

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def parse_datetime(text: str, now: datetime | None = None) -> datetime | None:
    """날짜+시각. 둘 중 하나라도 없으면 None (추측하지 않는다)."""
    now = _base(now or datetime.now())
    d = parse_date(text, now)
    hm = parse_time(text)
    if d is None or hm is None:
        return None
    return d.replace(hour=hm[0], minute=hm[1])


_DURATION = re.compile(r"(\d{1,2})\s*시간")
_STRIP = re.compile(
    r"(오늘|내일|모레|내일모레|어제|이번주|금주|다음주|담주|차주"
    r"|[월화수목금토일]요일"
    r"|\d{1,2}월|\d{1,2}일|\d{1,2}시|\d{1,2}분|\d{1,2}시간|\d{1,2}:\d{2}"
    # '미팅'은 빼지 않는다. "마바공방 미팅" 이 제목으로 자연스럽기 때문.
    # 대신 제목이 껍데기 말만 남는 경우는 router 에서 걸러낸다.
    r"|일정|스케줄|약속|캘린더에|캘린더"
    # 등록 계열 동사
    r"|등록|추가|생성|만들어|만들|잡아|잡자|잡죠|넣어|넣자"
    # 삭제 계열 동사 — 빠뜨렸다가 '동백테스트 삭제해' 가 통째로 제목이 됐다
    r"|삭제해|삭제|취소해|취소|지워|없애|빼줘"
    r"|줘|주세요|해줘|해주세요|해라|하자|좀|으로|이라는|라는|이름으로"
    # 수량 표현. 없으면 "일정 하나 만들어줘" 에서 '하나'가 제목이 된다.
    r"|하나|한개|1개|짜리)"
)

# 한 글자 조사·꼬리는 위 정규식에 넣으면 안 된다. 저건 낱말 안인지 밖인지
# 가리지 않고 지우기 때문이다.
#
#   "큐에이확인 등록해줘"  →  '에' 가 사라져 제목이 '큐 이확인' 이 됐다.
#   2026-08-12 QA 로 만든 일정이 그 이름으로 캘린더에 47건 남아 있었다.
#   같은 이유로 '로컬미팅' 은 '컬미팅', '반포미팅' 은 '포미팅' 이 된다.
#
# 그래서 앞뒤가 한글이 아닐 때만 지운다. 시각을 걷어낸 자리에 남은 조사는
# ("3시에 치과" → " 에 치과") 앞뒤가 공백이라 걸리고, 낱말 속에 든 같은
# 글자는 한글에 둘러싸여 살아남는다.
_PARTICLE = re.compile(r"(?<![가-힣])[에로반빼](?![가-힣])")

# 때를 가리키는 말도 같은 병을 앓는다. '저녁' 을 통째로 지우면 "5시에
# 저녁미팅 등록해줘" 의 제목이 '미팅' 만 남는다 — 사장님이 실제로 쓰시는
# 이름이다. 낱말로 서 있을 때만 때로 보고 걷어낸다.
_TIMEWORD = re.compile(r"(?<![가-힣])(오전|오후|저녁|아침|밤|새벽)(?![가-힣])")


def parse_duration_hours(text: str) -> float:
    m = _DURATION.search(text)
    if m:
        return max(0.5, min(int(m.group(1)), 12))
    return 1.0


def extract_title(text: str) -> str:
    """일정 제목만 남긴다. 날짜·시각·명령어를 걷어낸 나머지."""
    # 따옴표나 대괄호로 감싼 게 있으면 그게 제목이다
    m = re.search(r"[\[\"'「『](.+?)[\]\"'」』]", text)
    if m:
        return m.group(1).strip()
    s = _STRIP.sub(" ", text)
    s = _TIMEWORD.sub(" ", s)      # 낱말로 선 '저녁'·'오후' 만 때로 본다
    s = _PARTICLE.sub(" ", s)      # 시각을 걷어낸 자리에 남은 조사만 떨어진다
    s = re.sub(r"\s+", " ", s).strip(" .,·")
    return s or "일정"
