"""TTS — 단일 재생 워커. 토큰 0.

겹침의 역사를 요약하면: 재생 경로가 셋(캐시·Supertonic·say)이고 호출하는
스레드도 여럿(메인·Ack 타이머·제어 서버)이라, "지금 뭐가 나가고 있나"를
플래그로 쫓다가 stop() 과 재생 시작 사이의 틈에서 계속 겹쳤다.

그래서 구조를 바꾼다: 소리를 내는 곳은 워커 스레드 '하나' 뿐이다.
say() 는 큐에 넣기만 하고, 워커가 하나씩 꺼내 재생한다.
재생이 한 곳에서만 일어나므로 겹침이 구조적으로 불가능하다.

우선순위 규칙:
  REPLY  : 사장님이 기다리는 답변. **답변끼리는 줄을 선다 — 하나씩, 끝까지.**
           (예전에는 새 답변이 앞 답변을 끊었는데, 스트리밍이 들어오자
           말이 서로 밟혔다 — "다른 소리와 겹치고 있어. 큐에 넣어서
           하나씩 답변하게 해줘" 지시로 바꿨다.)
           필러(알림)는 자리를 내준다 — "확인하고 있습니다" 를 끝까지
           듣자고 답을 미룰 이유는 없다.
  NOTICE : 거드는 말("네", "확인하고 있습니다"). 답변이 있으면 침묵하고,
           다른 알림 뒤에는 줄을 선다 (끊지 않고 순서대로).
같은 말이 이미 대기·재생 중이면 다시 넣지 않는다 (중복 병합).

stop() 은 세대(_epoch)를 올린다. 진행 중이던 스트림(Stream)의 늦은 조각은
세대가 다르면 버려진다 — 입을 다문 뒤에 뒤늦게 떠들지 않기 위해서다.
"""

import queue as _queue_mod  # noqa: F401  (tts 가 쓰는 표준 모듈과 혼동 방지용 별칭)
import re
import subprocess
import threading
import time

import config


from dblog import log  # noqa: E402  (dongbaek 을 import 하면 순환이라 여기로 뺐다)


PRIORITY_NOTICE = 1
PRIORITY_REPLY = 2

_device_args: list[str] | None = None


def output_device(refresh: bool = False) -> list[str]:
    """`say -a` 인자를 만든다. 없으면 빈 리스트(시스템 기본).

    지정하지 않으면 기본 출력(스피커 없는 LG 모니터)으로 나가서 무음이 된다.
    """
    global _device_args
    if _device_args is not None and not refresh:
        return _device_args
    try:
        listing = subprocess.run(
            ["say", "-a", "?"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        _device_args = []
        return _device_args

    names = []
    for line in listing.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            names.append(parts[1].strip())

    for want in config.TTS_DEVICE_PREFERENCE:
        for n in names:
            if want.lower() in n.lower():
                _device_args = ["-a", n]
                return _device_args
    for n in names:
        if not any(h in n.lower() for h in config.VIRTUAL_DEVICE_HINTS):
            _device_args = ["-a", n]
            return _device_args
    _device_args = []
    return _device_args


_CODE_FENCE = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_MD = re.compile(r"[*_#>|\[\]]")
_URL = re.compile(r"https?://\S+")
_PATH = re.compile(r"(/[\w.\-]+){2,}")
_WS = re.compile(r"\s+")

# 소리로 읽으면 의미가 없거나 어색해지는 기호.
# TTS 가 "별표", "슬래시", "이퀄" 처럼 읽어버리거나 발음이 끊긴다.
# 한글·영문·숫자와 문장부호(. , ? !)만 남긴다.
_SYMBOLS = re.compile(r"[^\w\s가-힣.,?!%]", re.UNICODE)

# 소리로 나가는 말은 한국어다 (CLAUDE.md). 그런데 클로드가 영어로 답을
# 흘리면 그게 그대로 재생된다 — 2026-08-14 09:24 에 581자짜리 영어 작업
# 메모가, 2026-08-15 09:29 에 "This clearly isn't directed at me…" 가
# 그렇게 나갔다. 프롬프트로만 막아 왔는데 두 번 다 뚫렸다.
_HANGUL = re.compile(r"[가-힣]")
_LATIN = re.compile(r"[A-Za-z]")
# 한글이 한 글자도 없고 알파벳이 이만큼 넘으면 영어 '문장' 이다.
# 낱말 하나(RFP·AI·PDF·파일명)는 한국어 문장 안에 섞여 오므로 안 걸린다.
_FOREIGN_MIN_LETTERS = 20
_FOREIGN_SENT = re.compile(r"(?<=[.!?。])\s+|\n+")
_REPEAT_PUNCT = re.compile(r"([.,?!])\1+")

# 단위는 한글로 풀어 읽는다. "32GB" 를 TTS 가 "삼십이지비" 라고 읽으면
# 귀로는 알아들을 수 없다 — 사장님 교정: "32GB 가 아니라 32기가바이트".
# 숫자 뒤에 붙은 것만 바꾼다. 문장 속 영어 낱말까지 건드리지 않기 위해서다.
# ⚠ 끝 경계는 \b 가 아니라 '뒤에 알파벳이 없다' 로 본다. 위 시각과 같은
#   이유다 — "32GB를" 은 'B' 와 '를' 이 둘 다 낱말 문자라 \b 가 안 서서
#   통째로 안 바뀌었고, 그대로 "삼십이지비를" 로 나갔다.
_UNITS = [
    (re.compile(r"(?<=\d)\s*TB(?![A-Za-z])", re.I), "테라바이트"),
    (re.compile(r"(?<=\d)\s*GB(?![A-Za-z])", re.I), "기가바이트"),
    (re.compile(r"(?<=\d)\s*MB(?![A-Za-z])", re.I), "메가바이트"),
    (re.compile(r"(?<=\d)\s*KB(?![A-Za-z])", re.I), "킬로바이트"),
    (re.compile(r"(?<=\d)\s*ms(?![A-Za-z])"), "밀리초"),
    # 줄여 말한 "48기가" 도 끝까지 읽는다. '기가 막히다' 같은 말과 섞이지
    # 않도록 숫자 바로 뒤일 때만.
    (re.compile(r"(?<=\d)\s*기가(?!바이트)"), "기가바이트"),
    (re.compile(r"(?<=\d)\s*메가(?!바이트)"), "메가바이트"),
]

# 소수점은 "점" 이 아니라 "쩜" 으로 읽는다 (사장님 교정: 5.0기가 → 오쩜영기가).
#
# ⚠ 점만 바꾸면 "3쩜5시간" 이 되고, 그 다음은 TTS 손에 달린다. 사장님 교정
#   2026-08-16: "3.5시간이 아니라 삼쩜오시간". 그래서 소수는 **숫자째 한글로**
#   바꾼다 — 읽는 쪽에 맡기지 않고 여기서 확정한다.
#   정수부는 자릿수를 살려 읽고(12 → 십이), 소수부는 한 자씩 읽는다
#   (0.45 → 영쩜사오). 한국어가 소수를 그렇게 읽는다.
_DECIMAL_NUM = re.compile(r"(?<![\d.])(\d{1,7})\.(\d{1,4})(?![\d.])")
_DIGIT_KO = "영일이삼사오육칠팔구"
_SMALL_KO = ("", "십", "백", "천")
_BIG_KO = ("", "만", "억", "조")


def _int_ko(n: int) -> str:
    """1234 → '천이백삼십사'. 소수를 읽을 때 정수부에 쓴다."""
    if n == 0:
        return "영"
    out, unit = "", 0
    while n:
        n, chunk = divmod(n, 10000)
        if chunk:
            part = ""
            for pos in range(3, -1, -1):
                d = chunk // (10 ** pos) % 10
                if not d:
                    continue
                # 십·백·천 앞의 '일' 은 읽지 않는다 — '일십이' 가 아니라 '십이'
                part += ("" if d == 1 and pos else _DIGIT_KO[d]) + _SMALL_KO[pos]
            out = part + _BIG_KO[unit] + out
        unit += 1
    return out


def _say_decimal(m: "re.Match") -> str:
    whole, frac = m.group(1), m.group(2)
    return _int_ko(int(whole)) + "쩜" + "".join(_DIGIT_KO[int(d)] for d in frac)

# 시각은 시각으로 읽는다 (사장님 교정 2026-08-13: "시간은 시간으로 읽어,
# 공팔 삼사 이렇게 하지 말고"). 원인은 아래 _SYMBOLS 가 콜론을 지우는 것 —
# "08:34" 가 "08 34" 가 되면 TTS 는 숫자를 하나씩 읽는다.
# ⚠ 기호를 지우기 전에 풀어야 한다. 지운 뒤엔 시각이었다는 증거가 없다.
# ⚠ 경계를 \b 로 잡으면 한국어에서는 안 선다 — 조사가 시각에 딱 붙어 오기
#   때문이다("14:00부터", "9:30에", "12:58쯤"). 한글도 낱말 문자라 '00' 과
#   '부' 사이에는 경계가 없어 시각 풀이를 통째로 건너뛰었고, 그러면 아래
#   비율 규칙이 콜론을 먹어 "십사 대 영영부터" 가 나간다. 사장님 교정
#   2026-08-16 이 바로 이 소리다 — "':' 이게 들어가면 시간이잖아".
#   그래서 옆의 소수 규칙과 같이 숫자만 배제하는 전후탐색으로 잡는다.
_TIME_HMS = re.compile(r"(?<!\d)(\d{1,2}):([0-5]\d):([0-5]\d)(?!\d)")
_TIME_HM = re.compile(r"(?<!\d)(\d{1,2}):([0-5]\d)(?!\d)")

# 시각이 아닌 콜론은 점수·비율이다 — "3:1" 은 '삼 일' 이 아니라 '3대 1'
# (사장님 교정 2026-08-13). 시각 변환을 먼저 돌린 뒤 남은 콜론만 잡는다.
_RATIO = re.compile(r"(?<=\d):(?=\d)")

# '시' 앞의 숫자는 1~12 는 고유어(한 두 세…)로 읽어야 한다 — 한자어(십이)로
# 읽으면 "열두시"가 "십이시"가 된다. 사장님 교정(2026-08-16): "십이오팔이
# 아니고 열두시오십팔분". "08:34"를 "8시 34분"으로 콜론만 풀어 숫자는 그대로
# 둔 게 원인이다 — 소수(3.5시간→삼쩜오시간)와 같은 병이다. 숫자를 TTS 손에
# 맡기면 시각 낭독용 고유어를 안 쓰고 한자어로 읽어버린다. 그래서 숫자째
# 한글로 확정한다.
_HOUR_KO = ("", "한", "두", "세", "네", "다섯", "여섯", "일곱", "여덟",
            "아홉", "열", "열한", "열두")


def _hour_ko(h: int) -> str:
    """시각의 '시' 는 1~12 는 고유어, 그 밖(0, 13~23)은 한자어로 읽는다."""
    if 1 <= h <= 12:
        return _HOUR_KO[h]
    return _int_ko(h)


def clock_words(h: int, mi: int) -> str:
    """시·분을 한글로 확정해 돌려준다 ("지금 몇 시야" 같은 로컬 답변용).

    _say_time 과 같은 규칙을 쓴다 — 시각을 만드는 자리가 둘이면 한쪽만
    고쳤을 때 또 어긋난다(2026-08-16 재발이 그 예다).
    """
    return _hour_ko(h) + "시" + (f" {_int_ko(mi)}분" if mi else "")


def clock_ampm(h: int, mi: int = 0) -> str:
    """24시간 시각을 사람이 말하듯 — "오후 두시 삼십분".

    clock_words 만으로는 14시가 '십사시' 가 된다. 표기(14:00)를 그대로 읽는
    자리에서는 그게 맞지만, 일정을 **말로 알려 드리는** 자리에서는 아무도
    그렇게 말하지 않는다. 사장님 교정의 취지가 '자연스럽게 읽어라' 였으므로
    브리핑·알림처럼 사람에게 들려주는 자리는 이 함수를 쓴다.
    """
    ampm = "오전" if h < 12 else "오후"
    h12 = h if 1 <= h <= 12 else (h - 12 if h > 12 else 12)
    return f"{ampm} {clock_words(h12, mi)}"


def _say_time(m: "re.Match") -> str:
    """08:34 → '여덟시 삼십사분' · 14:00 → '십사시' · 12:58 → '열두시 오십팔분'.

    24시간 표기를 오전/오후로 바꾸지는 않는다 — 원문이 "14:00" 이면
    "십사시" 가 맞고, 오전·오후는 말한 쪽이 이미 붙여 온다(캘린더가 그렇다).
    """
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return m.group(0)                     # 시각이 아니다 (점수·비율 등)
    out = clock_words(h, mi)
    if m.lastindex == 3:
        s = int(m.group(3))
        if s:
            out += f" {_int_ko(s)}초"
    return out


def clean(text: str) -> str:
    """읽어서 알아들을 수 있는 형태로 정리. 코드블록·마크다운·URL은 소리로 무의미."""
    t = _CODE_FENCE.sub(" 코드 블록 생략. ", text)
    t = _INLINE_CODE.sub(r"\1", t)
    t = _URL.sub(" 링크 ", t)
    t = _PATH.sub(" 경로 ", t)
    t = _MD.sub("", t)
    # 기호를 지우기 전에 시각·단위·소수점을 푼다. 순서가 바뀌면 "5.0" 의
    # 점이 먼저 사라져 "오영" 이 되고, "08:34" 는 "공팔 삼사" 가 된다.
    t = _TIME_HMS.sub(_say_time, t)
    t = _TIME_HM.sub(_say_time, t)
    t = _RATIO.sub("대 ", t)          # 남은 콜론 = 점수·비율
    for rx, word in _UNITS:
        t = rx.sub(word, t)
    t = _DECIMAL_NUM.sub(_say_decimal, t)
    t = _SYMBOLS.sub(" ", t)
    t = _REPEAT_PUNCT.sub(r"\1", t)
    t = _WS.sub(" ", t).strip(" .,")
    if len(t) > config.TTS_MAX_CHARS:
        t = t[: config.TTS_MAX_CHARS].rsplit(" ", 1)[0] + " ... 이하 생략."
    return t


def korean_only(text: str) -> str:
    """영어로만 된 문장을 덜어낸다. 남는 게 없으면 빈 문자열.

    ⚠ 답 전체를 통째로 막으면 안 된다. "안 읽은 메일 6통입니다.
      yourname00@gmail.com 3통…" 처럼 주소·파일명이 섞인 멀쩡한 답이
      영문 비율만 보면 누출과 구분되지 않는다 (실측 정상 0.31 : 누출 0.24 —
      비율로는 못 가른다). 그래서 비율이 아니라 **문장 단위**로 본다.

    실측 근거 — state/transcript.jsonl 의 답변 3,914문장 중 걸리는 것은
    5문장뿐이고 그중 4개가 진짜 영어 누출이다. 나머지 하나는
    "2 files changed, 2 insertions(+)…" 라는 git 요약 줄인데, 그건
    소리로 들어도 못 알아듣는 줄이라 덜어내는 편이 낫다.
    """
    keep = []
    for s in _FOREIGN_SENT.split(text):
        s = s.strip()
        if not s:
            continue
        if not _HANGUL.search(s) and len(_LATIN.findall(s)) >= _FOREIGN_MIN_LETTERS:
            continue
        keep.append(s)
    return " ".join(keep)


# ─────────────────────────────────────────────────────────
# 재생 큐 — 소리는 워커 스레드 한 곳에서만 난다
# ─────────────────────────────────────────────────────────
class _Utt:
    __slots__ = ("body", "priority", "done", "follow")

    def __init__(self, body: str, priority: int, follow: bool = False):
        self.body = body
        self.priority = priority
        # 앞 답변에 '이어지는' 조각인가. 스트리밍 답변을 문장 단위로 넣을 때
        # 두 번째 조각이 첫 조각을 끊어버리면 마지막 문장만 들린다.
        self.follow = follow
        self.done = threading.Event()


_cv = threading.Condition()
_pending: list[_Utt] = []
_now: _Utt | None = None            # 워커가 재생 중인 발화
_interrupt = threading.Event()      # 현재 재생을 끊으라는 신호
_epoch = 0                          # stop() 마다 +1 — 죽은 스트림의 늦은 조각을 가려낸다
_worker_started = False
_finished_at = 0.0      # 마지막으로 말을 마친 시각 — 대화창을 여기서부터 잰다
_recent = ""                        # 마지막으로 재생을 시작한 말 (알림 포함)
_last_reply = ""                    # 마지막 '답변' (알림 제외) — "뭐라고?" 에 쓴다
# 스트리밍 답변은 조각이 여러 개라 _recent 가 계속 늘어난다. 자기 목소리를
# 가려내는 데만 쓰므로 최근 것만 있으면 된다 — 무한정 자라지 않게 자른다.
_RECENT_MAX = 600


def recent_text() -> str:
    """방금 동백이 한 말. 마이크로 되돌아온 자기 목소리를 가려내는 데 쓴다.

    끊고 들어오기를 켜면 동백이 말하는 중에도 마이크가 열려 있어서,
    에코를 사장님 말로 착각하고 자기 말을 명령으로 실행할 수 있다.
    받아쓴 문장을 이것과 대조해 걸러낸다 (dongbaek._is_own_voice).
    """
    return _recent


def last_reply() -> str:
    """마지막으로 말한 답변. 알림은 빼고 답변만.

    "뭐라고?" 처럼 되읽기를 청할 때 쓴다. 이걸 Claude 에 물으면 6초에
    수백 원이 드는데, 방금 한 말은 이미 여기 있다.
    """
    return _last_reply


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _cv:
        if _worker_started:
            return
        threading.Thread(target=_worker_loop, daemon=True).start()
        _worker_started = True


def _worker_loop() -> None:
    global _now, _recent, _last_reply
    while True:
        with _cv:
            while not _pending:
                _cv.wait()
            utt = _pending.pop(0)
            _now = utt
            # 이어지는 조각은 앞말에 '보탠다'. 덮어쓰면 자기 목소리 필터가
            # 마지막 문장만 기억하게 되고, 앞 문장의 에코가 마이크로 되돌아왔을 때
            # 사용자 말로 착각해 그대로 명령으로 실행한다.
            _recent = (f"{_recent} {utt.body}" if utt.follow else utt.body)[-_RECENT_MAX:]
            # "뭐라고?" 는 방금 한 '답변' 을 다시 읽는 것이다. 알림("네",
            # "확인하고 있습니다")까지 섞이면 엉뚱한 것을 되읽는다.
            if utt.priority >= PRIORITY_REPLY:
                _last_reply = (f"{_last_reply} {utt.body}"
                               if utt.follow else utt.body)[-_RECENT_MAX:]
            _interrupt.clear()
        try:
            _play(utt.body)
        except Exception:
            pass
        finally:
            with _cv:
                _now = None
                # 큐가 비었으면 '방금 말을 마쳤다'. 대화창은 말이 끝난 뒤부터
                # 재야 한다 — 시작할 때부터 재면 동백이 10초 말한 답변에서는
                # 사장님께 5초밖에 안 남는다. 사장님 지적: "내가 또 거기에
                # 대해서 대답을 하면 또 말이 없어."
                if not _pending:
                    global _finished_at
                    _finished_at = time.monotonic()
            utt.done.set()


def _queued_or_playing(body: str) -> bool:
    if _now is not None and _now.body == body:
        return True
    return any(u.body == body for u in _pending)


def _has_reply() -> bool:
    if _now is not None and _now.priority >= PRIORITY_REPLY:
        return True
    return any(u.priority >= PRIORITY_REPLY for u in _pending)


# 미팅 모드 — 입 전면 봉인. 발화·알림·비프 전부 여기서 삼켜진다.
# (2026-08-13, 동백이 줌미팅에 "메모리 48기가…" 를 낭독한 사고)
_MUTED = {"on": False}


def mute(on: bool) -> None:
    _MUTED["on"] = bool(on)


def is_muted() -> bool:
    return _MUTED["on"]


def say(text: str, *, block: bool = True, raw: bool = False,
        priority: int = PRIORITY_REPLY, follow: bool = False,
        epoch: int | None = None) -> None:
    """말할 것을 큐에 넣는다. 실제 재생은 워커가 한다.

    block=True 면 이 발화의 재생이 끝날 때까지(또는 stop 으로 끊길 때까지) 기다린다.

    답변(REPLY)은 앞 답변을 끊지 않는다 — 줄을 서서 하나씩, 끝까지 나간다.
    끊는 건 필러(알림)뿐이다. follow=True 는 '방금 한 답변에 이어지는 조각'
    이라는 뜻으로, 자기 목소리 필터와 되읽기가 답변을 한 덩어리로 기억하게
    한다 (스트리밍 조각들).

    epoch 를 주면 그 세대일 때만 말한다 — stop() 이후 뒤늦게 도착한
    스트림 조각이 조용해지는 장치다. 검사와 삽입이 같은 잠금 안이라
    stop 과의 경합에도 틈이 없다.
    """
    # 미팅 모드 — 무엇이든 소리로는 안 나간다. 삼킨 걸 로그로 남겨
    # "왜 조용했나" 를 나중에 알 수 있게 한다.
    if _MUTED["on"]:
        log(f"미팅 모드 침묵 — 삼킴: {text[:24]!r}")
        return
    body = text if raw else clean(text)
    # 영어로 새어 나온 문장은 소리로 내보내지 않는다. 말투 통일과 같은
    # 자리에서 거는 이유도 같다 — 답을 만드는 곳이 넷이라 한 곳씩 막으면
    # 새 경로가 생길 때마다 또 뚫린다. raw=True 는 안 건드린다.
    if body and not raw and getattr(config, "SPEAK_KOREAN_ONLY", True):
        kept = korean_only(body)
        if kept != body:
            log(f"영어 문장은 안 읽음: {body[:40]!r}")
            # 통째로 영어였으면 침묵하지 않는다 — 아무 소리도 안 나면
            # 사장님께는 고장으로 보인다 (2026-08-13 05:42 와 같은 교훈).
            body = kept or "답이 영어로 나와서 안 읽었어요. 다시 한번 말씀해 주시겠어요?"
    # 말끝을 한 문체로 맞춘다 — 답을 만드는 곳이 넷이어도(고정 문구·로컬
    # 규칙·큐웬·클로드) 사장님이 듣는 목소리는 하나여야 한다. 네 곳을 각각
    # 고치면 새 문구가 생길 때마다 또 어긋나므로 길목에서 한 번에 건다.
    # 뜻은 안 건드린다 — 바꾸는 건 문장 끝 어미뿐이다 (voice_style 참조).
    if body and not raw and getattr(config, "VOICE_STYLE_UNIFY", True):
        try:
            import voice_style

            body = voice_style.apply(body)
        except Exception:
            pass          # 말투 때문에 소리가 안 나가면 안 된다
    if not body:
        return
    _ensure_worker()

    with _cv:
        # 죽은 세대의 조각 — 이미 입을 다물기로 한 답의 잔향이다.
        if epoch is not None and epoch != _epoch:
            return
        # 같은 말이 이미 대기·재생 중이면 다시 넣지 않는다
        if _queued_or_playing(body):
            return
        if priority >= PRIORITY_REPLY and not follow:
            # 새 답변이 시작된다. 답변끼리는 줄을 서되, 필러는 자리를 내준다 —
            # "확인하고 있습니다" 를 끝까지 듣자고 답을 미룰 이유는 없다.
            keep = []
            for u in _pending:
                if u.priority < PRIORITY_REPLY:
                    u.done.set()
                else:
                    keep.append(u)
            _pending[:] = keep
            if _now is not None and _now.priority < PRIORITY_REPLY:
                _interrupt.set()
        elif priority < PRIORITY_REPLY:
            # 알림은 답변을 밀어내지 않는다. 답변이 있으면 아예 말하지 않는다.
            if _has_reply():
                return
        utt = _Utt(body, priority, follow=follow)
        _pending.append(utt)
        _cv.notify()

    if block:
        utt.done.wait()


# ─────────────────────────────────────────────────────────
# 스트리밍 답변 — 다 만들어지기 전에 말하기 시작한다
# ─────────────────────────────────────────────────────────
# 문장이 끝나는 자리. tts.py 의 것과 같은 기준이다 — 거기서 어차피 문장 단위로
# 쪼개 재생하므로, 여기서 다른 기준으로 자르면 두 번 쪼개져 리듬이 깨진다.
_SENT_END = re.compile(r"[.!?。](?=\s|$)|\n")
# 이보다 짧은 조각은 혼자 안 내보낸다. "네." 한 마디씩 끊어 재생하면
# 조각마다 재생 시작 지연이 붙어서 오히려 뚝뚝 끊겨 들린다.
_STREAM_MIN = 12
# 문장부호가 안 나와도 이만큼 쌓이면 끊어 내보낸다.
#
# 없으면 마침표 없는 답변이 영영 안 쪼개진다 — 나열형 답변("하나 둘 셋 …")이
# 통째로 close() 까지 밀려서 스트리밍이 아무 일도 안 한 것과 같아진다.
# 실제로 그렇게 재서 개선이 18% 로 나온 적이 있다.
_STREAM_MAX = 90


class Stream:
    """답변을 문장 단위로 말한다. 델타가 도착하는 대로 먹인다.

    쓰는 법:
        s = speak.Stream(lead="홍길동님 답변드리겠습니다. ")
        ...  s.feed(조각)  ...      # 브릿지가 글자를 줄 때마다
        s.add("두 파일을 고쳤습니다.")   # 답변 뒤에 덧붙는 안내
        s.close()

    첫 조각만 follow 가 아니다 — '새 답변 시작' 신호라 필러(알림)를 치운다.
    나머지는 줄을 선다 (say 의 follow). 말한 것은 spoken 에 모아두므로
    '무엇까지 말했나' 를 호출하는 쪽이 안다.

    만들어질 때의 세대(_epoch)를 기억한다. 중간에 stop() 이 불리면(끼어들기·
    말 보탬) 세대가 갈려서, 브릿지가 뒤늦게 보내는 조각은 조용히 버려진다 —
    입 다문 동백이 몇 초 뒤 혼자 이어 말하던 문제의 답이다.
    """

    def __init__(self, lead: str = ""):
        self._buf = ""
        self._started = False
        self.spoken = ""
        self._epoch = _epoch
        # 호칭은 첫 문장이 나갈 때 같이 붙인다. 여기서 바로 말하면, 명령이
        # 로컬로 처리돼 Claude 를 안 거쳤을 때 호칭만 말해놓고 끝난다 —
        # 그다음 say() 는 follow 가 아니라서 그 호칭을 끊어버린다.
        # 0~3초 공백은 Ack 가 이미 메운다.
        self._lead = lead

    def _emit(self, text: str) -> None:
        if not text.strip():
            return
        if not self._started and self._lead:
            text, self._lead = self._lead + text.lstrip(), ""
        say(text, block=False, follow=self._started, epoch=self._epoch)
        self._started = True
        self.spoken += text

    def spoke(self) -> bool:
        """실제로 소리를 낸 적이 있는가. 없으면 호출한 쪽이 평소대로 말하면 된다."""
        return self._started

    def _cut(self) -> int:
        """버퍼에서 잘라 내보낼 길이. 아직이면 0.

        문장이 끝나는 자리를 찾되, 너무 짧으면 다음 문장까지 기다린다.
        문장부호가 끝내 안 나오면 _STREAM_MAX 에서 띄어쓰기 자리로 끊는다.
        """
        for m in _SENT_END.finditer(self._buf):
            if m.end() >= _STREAM_MIN:
                return m.end()
        if len(self._buf) >= _STREAM_MAX:
            cut = self._buf.rfind(" ", _STREAM_MIN, _STREAM_MAX)
            return cut + 1 if cut > 0 else _STREAM_MAX
        return 0

    def feed(self, chunk: str) -> None:
        """브릿지가 준 조각을 받는다. 문장이 완성되면 그때 말한다."""
        self._buf += chunk
        while (n := self._cut()):
            head, self._buf = self._buf[:n], self._buf[n:]
            self._emit(head)

    def add(self, text: str) -> None:
        """답변 뒤에 덧붙는 안내를 이어 말한다 (수정 요약·재시작 알림 등)."""
        self.flush()
        self._emit(" " + text.strip())

    def flush(self) -> None:
        """버퍼에 남은 것을 지금 내보낸다."""
        rest, self._buf = self._buf, ""
        self._emit(rest)

    def close(self) -> str:
        """남은 것을 말하고 지금까지 말한 전부를 돌려준다."""
        self.flush()
        return self.spoken


def stop() -> None:
    """대기열을 비우고 지금 나가는 소리를 끊는다.

    세대도 올린다 — 진행 중이던 스트림이 뒤늦게 보내는 조각까지 조용해진다.
    """
    global _epoch
    with _cv:
        _epoch += 1
        for u in _pending:
            u.done.set()
        _pending.clear()
        if _now is not None:
            _interrupt.set()


def last_finished_at() -> float:
    """마지막으로 말을 마친 시각(monotonic). 아직 말하는 중이면 지금."""
    with _cv:
        return time.monotonic() if (_now is not None or _pending) else _finished_at


def is_speaking() -> bool:
    with _cv:
        return _now is not None or bool(_pending)


# ─────────────────────────────────────────────────────────
# 실제 재생 — 전부 워커 스레드 안에서만 호출된다
# ─────────────────────────────────────────────────────────
def _play_budget(body: str) -> float:
    """이 한 마디가 끝나기를 기다려 줄 최대 시간(초)."""
    return min(config.TTS_PLAY_MAX_SEC,
               config.TTS_PLAY_BASE_SEC + len(body) * config.TTS_PLAY_SEC_PER_CHAR)


def _await_playback(pb, body: str) -> bool:
    """재생이 끝나기를 기다린다. 내장 음성으로 다시 말할 필요가 없으면 True.

    ⚠ 상한이 반드시 있어야 한다. Supertonic 이 '끝났다' 를 안 알려주면
      워커가 여기 갇히고, _now 가 안 풀려 그 뒤 모든 답이 큐에만 쌓인다.
      2026-08-11 밤 실제로 그렇게 멎었다 — 마이크는 멀쩡히 듣고
      "호명 → 즉시 응답" 이 다섯 번 찍혔는데 소리는 한 번도 안 났다.
      예외가 안 나므로 로그만 봐서는 죽은 줄도 모른다.
    """
    deadline = time.monotonic() + _play_budget(body)
    while pb.is_active():
        if _interrupt.is_set():
            pb.stop()
            return True
        if time.monotonic() > deadline:
            pb.stop()
            spoken = pb.started()
            log(f"재생이 안 끝나 끊었습니다 ({len(body)}자, "
                + ("소리는 나갔음" if spoken else "소리 없음 — 내장 음성으로 다시") + ")")
            # 이미 소리가 나갔으면 다시 말하면 겹친다. 한 번도 안 났으면
            # 사장님은 못 들은 것이므로 내장 음성으로라도 말해야 한다.
            return spoken
        time.sleep(0.05)
    return True


def _play(body: str) -> None:
    # 무슨 소리가 나갔는지 한 줄 남긴다. '들림' 과 짝이다 — 2026-08-12 밤
    # "두 목소리가 겹쳤다/답이 무음이었다" 를 추적하는 데 세 시간이 걸렸다.
    # 말한 기록이 어디에도 없었기 때문이다. 들은 건 다 적으면서 말한 걸
    # 안 적을 이유가 없다.
    log(f"말함: {body[:24]!r}" + (f" 외 {len(body) - 24}자" if len(body) > 24 else ""))
    if body in _cache and _play_cached(body):
        return

    if config.TTS_ENGINE == "supertonic":
        pb = None
        try:
            import tts

            pb = tts.speak(body, block=False)
            if pb is not None and _await_playback(pb, body):
                # ⚠ '기다림이 끝났다' ≠ '소리가 나갔다'. 장치 오류로 재생
                #   스레드가 시작도 못 하고 죽으면 is_active 가 금세 꺼져
                #   여기 닿는다. 소리가 나갔을 때만 믿는다 — 아니면 내장
                #   음성이 말한다. 무음으로 삼키는 것보다 낫다.
                if pb.started():
                    return
                # ⚠ 끊으라고 해서 멈춘 것은 '못 낸' 게 아니다. 여기서
                #   폴백하면 사장님이 방금 끊은 말을 Yuna 가 다시 말한다 —
                #   2026-08-13 저녁 폴백 3건이 전부 '끊고 들어옴' 직후였다.
                if _interrupt.is_set():
                    return
                log(f"수퍼토닉이 소리를 못 냈습니다"
                    f"{' (장치 오류)' if pb.failed() else ''} — 내장 음성으로")
        except Exception as e:
            # 재생이 '이미 시작된 뒤' 터지면 소리는 나가는 중이다. 그대로
            # 폴백하면 같은 말이 두 목소리로 겹친다. 멈추고, 나간 소리는
            # 다시 말하지 않는다 — 겹침(둘 다 못 알아듣게 됨)이 무음보다
            # 나쁘다. 시작 전이었으면 내장 음성이 말한다.
            started = False
            if pb is not None:
                try:
                    started = pb.started()
                    pb.stop()
                except Exception:
                    started = True    # 상태를 모르면 겹침 쪽을 피한다
            log(f"수퍼토닉 오류 ({type(e).__name__}) — "
                + ("소리는 나갔으므로 다시 말하지 않음" if started else "내장 음성으로"))
            if started:
                return

    _say_builtin(body)


def _say_builtin(body: str) -> None:
    proc = subprocess.Popen(
        [
            "say",
            *output_device(),
            "-v", config.TTS_VOICE,
            "-r", str(config.TTS_RATE),
            body,
        ]
    )
    # 폴백에도 같은 상한을 둔다. 여기가 갇히면 결국 같은 무음이 된다 —
    # 오디오 장치가 물리면 say 도 안 끝난다.
    deadline = time.monotonic() + _play_budget(body)
    while proc.poll() is None:
        if _interrupt.is_set() or time.monotonic() > deadline:
            if not _interrupt.is_set():
                log(f"내장 음성이 안 끝나 끊었습니다 ({len(body)}자)")
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
            return
        time.sleep(0.05)


_cache: dict[str, "object"] = {}


def precache(*texts: str) -> None:
    """자주 쓰는 짧은 말을 미리 합성해둔다.

    "네" 를 매번 합성하면 0.4초가 걸린다. 부르자마자 대답해야 하는 말인데
    0.4초는 어색하다. 기동할 때 한 번 만들어두면 재생만 하면 된다.
    """
    if config.TTS_ENGINE != "supertonic":
        return
    try:
        import tts

        for t in texts:
            if t and t not in _cache:
                arr = tts._synth(t)
                if arr is not None:
                    _cache[t] = arr
    except Exception:
        pass


def _play_cached(body: str) -> bool:
    arr = _cache.get(body)
    if arr is None:
        return False
    # ⚠ False 를 돌려주면 호출측이 같은 말을 처음부터 다시 합성해 말한다.
    #   그래서 '재생을 시작한 뒤' 의 예외에서 False 를 주면 안 된다 —
    #   나가던 소리 위에 같은 말이 겹친다. 시작 전 실패만 False.
    #
    # 2026-08-13: sd.play(전역 스트림 여닫기) 대신 tts 의 상주 스트림을
    # 같이 쓴다 — 두 경로가 서로 장치를 뺏던 경합이 사라진다.
    started = False
    try:
        import tts

        started = tts.play_array(arr, interrupt=_interrupt)
        # 끊으라는 신호로 멈춘 것도 '재생 처리 완료' 다 — False 를 주면
        # 입 다물라는 순간에 같은 말을 다시 합성해 말하게 된다.
        return bool(started or _interrupt.is_set())
    except Exception:
        if started:
            log("캐시 재생 중 오류 — 겹치지 않게 다시 말하지 않음")
            return True
        return False


def beep(sound: str = "Tink") -> None:
    """푸시투토크 시작 신호. 말로 '네?' 하는 것보다 빠르고,
    블루투스에서 HFP 전환 대기를 타지 않는다."""
    if _MUTED["on"]:
        return
    subprocess.Popen(
        ["afplay", f"/System/Library/Sounds/{sound}.aiff"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
