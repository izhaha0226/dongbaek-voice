"""라우터 — 호출어 판정, 로컬 처리(0 토큰), 위험 명령 탐지."""

import difflib
import re
import subprocess
from datetime import datetime

import config

_PUNCT = re.compile(r"[^\w가-힣]+")


def normalize(text: str) -> str:
    return _PUNCT.sub("", text).lower()


# ─────────────────────────────────────────────────────────
# 호출어
# ─────────────────────────────────────────────────────────
def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """정규화 문자열과 '정규화 위치 → 원문 위치' 대응표를 함께 만든다.

    호출어를 떼어낸 나머지를 원문 그대로 살리려면 이 대응표가 필요하다.
    (구두점·공백이 빠지면서 위치가 어긋나기 때문)
    """
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(text):
        for c in _PUNCT.sub("", ch).lower():
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def _rest_after(text: str, idx_map: list[int], norm_end: int) -> str:
    if norm_end >= len(idx_map):
        return ""
    return text[idx_map[norm_end] :].lstrip(" ,.·~!?　\t").strip()


# whisper 가 잡음을 문자로 적을 때의 꼴. 사람 말이 아니다.
#
# 실측(2026-08-11 로그): "문문문문문문…", "롱, 롱, 롱.", "다윗, 다윗,
# 다윗, 다윗.", "동." 같은 것들이 하루에도 여러 번 명령 처리까지 갔다.
_REPEAT_CHAR = re.compile(r"(.)\1{3,}")          # 같은 글자 4번 이상
_REPEAT_WORD = re.compile(r"(\b\S{1,3}\b)([ ,.]+\1){2,}")  # 짧은 말 3회 이상


def is_noise(text: str) -> bool:
    """받아쓰기 결과가 사람 말이 아닌가 — 그렇다면 아예 보지 않는다."""
    t = normalize(text)
    if not t:
        return True
    if _REPEAT_CHAR.search(t):
        return True
    if _REPEAT_WORD.search(text.strip()):
        return True
    # 한 글자짜리는 호출어("동")로도 잡히는데, 그건 명령이 될 수 없다.
    return len(t) <= 1


# 영상·방송 소리의 상투구 — 사람이 동백에게 할 리 없는 말들.
# 실사례 2026-08-13 09:02: 유튜브 끝맺음("다음 영상에서 만나요")이
# "오늘 일정 전부 삭제해" 와 한 덩어리로 합쳐져 일정 검색 키워드가
# 통째로 오염됐다 ("…와 맞는 일정을 찾지 못했습니다").
_MEDIA_NOISE = ("다음영상에서", "다음영상으로", "구독과좋아요", "구독좋아요",
                "좋아요와알림", "알림설정", "시청해주셔서", "시청해줘서",
                "채널에서만나", "영상에서만나")


def is_media_noise(text: str) -> bool:
    """영상·방송 상투구인가 — 이어 듣기 조각에서 걸러낸다."""
    s = normalize(text).replace(" ", "")
    return any(k in s for k in _MEDIA_NOISE)


# 말끝에 부르는 호명 — "...그렇지? 동백아"
#
# match_wake 는 호출어가 발화 '맨 앞' 이어야 인정한다. 그 규칙에는 이유가
# 있다 — "어제 동백한테 말했는데" 같은 잡담이 명령으로 오인되면 안 된다.
# 그런데 말하다가 부르실 때는 끝에 온다.
#
# 실측 2026-08-12 22:24, 통화 중에 "이제서야 얘가 바뀐 거지? 동백아." 하고
# 부르셨는데 "호출어 없음 — 무시" 로 버려졌다. 사장님 반응: "아직도 정신을
# 못차리네??? 통화 중에 동백아 불렀는데".
#
# ⚠ 부름꼴(동백아·동백이·동백야)만 받는다. '동백한테'·'동백이는' 같은
#   조사가 붙은 말은 부르는 게 아니라 얘기 소재다. 그리고 맨 끝에 있을
#   때만 본다 — 문장 한가운데의 '동백' 은 여전히 소재다.
#
# 앞말은 버린다. 말하다가 부르셨다는 건 앞엣것이 동백에게 한 말이 아니라는
# 뜻이다. 부름만 받고 "네" 한 뒤 다음 말을 기다린다.
_WAKE_END = re.compile(
    r"(동백아|동백이|동백야|똥백아|돈백아|동백|공배가|공개가|동대가)"
    r"[.!?~\s]*$")


def wake_at_end(text: str) -> bool:
    """말끝에서 부르셨는가. 앞말은 동백에게 한 말이 아니다."""
    t = (text or "").strip()
    if not t:
        return False
    # 통째로 호출어면 match_wake 가 이미 잡는다. 여기는 '뒤에 붙은' 경우만.
    if match_wake(t) is not None:
        return False
    return bool(_WAKE_END.search(normalize(t)))


# 초성만 흔들린 오기 — whisper 는 '동백아'의 모음·받침은 제대로 적고
# 초성을 놓친다: 공개가·공대가·동대가·동재가·홍백아·공백아 전부
# 뼈대(ㅗㅇ/ㅐ/ㅏ)는 그대로고 첫소리만 다르다. 그래서 유사도 대신
# 모음+받침이 통째로 같은지를 본다.
#
# 목록에 변형을 하나씩 채워 넣는 술래잡기를 끝내려고 만들었다. 2026-08-13
# 로그 실측(들림 4,448건): 지금껏 목록에 없어 조용히 버려지던 오기를 전부
# 잡고, 새로 깨어나는 것은 못 잡던 4,126건 중 3건이다 — 그중 '홍배가
# 이번주 스케줄을 확인해줘' 는 진짜 부름이고, 나머지 둘은 방송 소리 조각
# ('동대라?', '동대자야.') 이라 화자 인증에서 다시 막힌다.
#
# ⚠ 뼈대는 통째로 같아야 한다. 받침 하나만 흘려도 다른 말이다 —
#   그 한 글자가 '동생아'(ㅐㅇ)·'담배가'(ㅏㅁ)·'동기가'(ㅣ)·'동거가'(ㅓ)
#   를 걸러낸다. 자모 유사도(0.70)로 재던 시안은 저것들까지 깨웠다.
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def _vowel_skeleton(word: str) -> tuple | None:
    """음절마다 (중성, 종성). 한글 아닌 글자가 섞이면 None (= 비교 안 함)."""
    out = []
    for ch in word:
        code = ord(ch) - 0xAC00
        if not 0 <= code < 11172:
            return None
        out.append((_JUNG[(code % 588) // 28], _JONG[code % 28]))
    return tuple(out)


# '동백' 은 지명이기도 하다 — 아파트 방송이 "동백1동 행정복지센터…" 라고
# 말하자 호출어가 매칭돼 동백이 방송에 말대꾸했다 (2026-08-13 19:37 실사례).
# 호출어 바로 뒤가 지명 꼬리(N동·지구·역·마을…)면 부름이 아니다.
# ⚠ normalize 는 공백을 지우므로 경계(\s|$)를 쓸 수 없다. 민꼬리 '동백'
#   바로 뒤에 지명자가 오면 지명 우선 — 실제 호명은 거의 "동백아" 꼴이라
#   (호격이 붙으면 이 검사를 안 탄다) 잃는 명령이 사실상 없다.
_PLACE_TAIL = re.compile(r"^(\d+동|동(?!백)|지구|역|마을|아파트|택지|신도시)")


def _looks_place(norm: str, start: int) -> bool:
    """매칭 지점이 '동백1동·동백지구' 같은 지명인가.

    판정을 매칭된 변형이 아니라 **원문의 '동백' 어간 바로 뒤**에 앵커링한다 —
    뼈대 매칭("동백이"↔"동백지")이 지명 일부를 먹어도 여기서 잡힌다.
    "동백아 역까지…" 는 어간 뒤가 호격 '아' 라 지명 꼬리가 아니므로 산다.
    """
    tail = norm[start:]
    m = re.match(r"동백\s*", tail)
    if not m:
        return False                     # '공대가' 류 오기는 지명 걱정이 없다
    return bool(_PLACE_TAIL.match(tail[m.end():]))


def match_wake(text: str) -> str | None:
    """호출어가 있으면 그 뒤의 명령을 반환. 없으면 None.

    whisper가 '동백'를 매번 다르게 적어서 정확일치로는 못 잡습니다.
    변형 목록 + 유사도로 판정하되, 반드시 발화 맨 앞이어야 합니다.
    """
    norm, idx_map = _normalize_with_map(text)
    if not norm:
        return None
    limit = config.WAKE_MAX_OFFSET

    for w in config.WAKE_WORDS:
        wn = normalize(w)
        if not wn:
            continue
        pos = norm.find(wn)
        if 0 <= pos <= limit:
            if _looks_place(norm, pos):
                continue                 # "동백1동 행정복지센터…" — 지명이다
            return _rest_after(text, idx_map, pos + len(wn))

    # 변형 목록에 없는 발음 — 앞머리를 유사도로 비교
    for w in config.WAKE_WORDS[:6]:
        wn = normalize(w)
        for start in range(0, limit + 1):
            cand = norm[start : start + len(wn)]
            if len(cand) < len(wn):
                continue
            if difflib.SequenceMatcher(None, cand, wn).ratio() >= config.wake_fuzzy_ratio():
                if _looks_place(norm, start):
                    continue
                return _rest_after(text, idx_map, start + len(wn))

    # 초성만 흔들린 오기 — 모음·받침 뼈대가 같으면 부른 것으로 본다.
    # 글자 유사도로는 0.33 밖에 안 나와('공대가' vs '동배가') 위 관문을
    # 통과할 길이 없다. 그래서 별도 관문이다.
    if getattr(config, "WAKE_SKELETON", False):
        for w in config.WAKE_WORDS[:6]:
            wn = normalize(w)
            skeleton = _vowel_skeleton(wn)
            if skeleton is None:
                continue
            for start in range(0, limit + 1):
                cand = norm[start : start + len(wn)]
                if len(cand) < len(wn):
                    continue
                if _vowel_skeleton(cand) == skeleton:
                    if _looks_place(norm, start):
                        continue
                    return _rest_after(text, idx_map, start + len(wn))
    return None


_FILLERS = {normalize(w) for w in config.CALL_ANSWER_FILLERS}


def is_bare_call(tail: str) -> bool:
    """호출어 뒤에 남은 말이 '이름만 불렀다' 인가.

    길이로 재면 안 된다. '뭐라고'·'몇시야'·'왜' 는 셋 다 짧지만 분명한
    질문이고, 그렇게 재던 시절엔 "동백이 뭐라고?" 가 "네" 로 되돌아왔다.

    붙어도 호명으로 볼 말은 군말뿐이다. 그 목록으로 판정한다.
    """
    if not normalize(tail):
        return True
    if len(normalize(tail)) > config.CALL_ANSWER_MAX_TAIL:
        return False
    words = [normalize(w) for w in tail.split()]
    return all(w in _FILLERS for w in words if w)


# 되읽기 요청. normalize 가 공백·구두점을 지우므로 붙여 쓴 형태로 적는다.
#
# ⚠ fullmatch 다. "그게 뭐라고 생각해?" 처럼 문장에 섞여 들어온 것까지 잡으면
#   진짜 질문이 되읽기로 새어 나간다 — 시각·날짜 처리와 같은 이유다.
_REPEAT_ONLY = re.compile(
    r"(뭐라고|뭐라구|뭐래|뭐랬어|뭐라했어|뭐라그랬어|"
    r"다시말해|다시말해줘|다시말씀|다시말씀해주세요|다시한번|다시한번말해줘|"
    r"못들었어|못들었는데|못알아들었어|안들려|안들렸어|다시들려줘)"
)


# ─────────────────────────────────────────────────────────
# 위험 명령
# ─────────────────────────────────────────────────────────
_DANGER = [re.compile(p, re.I) for p in config.DANGER_PATTERNS]


# 문장 끝 부호. whisper 가 "수정해줘." 처럼 마침표를 붙이는데,
# 문장 끝($)에 고정된 패턴이 이것 때문에 통째로 빗나갔다.
# 실제로 '수정해줘.' 가 게이트를 그냥 통과한 적이 있다.
_TRAILING = re.compile(r"[\s.!?~…,]+$")


_SAFE = [re.compile(p, re.I) for p in config.SAFE_PATTERNS]
_SAFE_OVERRIDE = [re.compile(p, re.I) for p in config.SAFE_OVERRIDE]


def is_repeat_request(text: str) -> bool:
    """'방금 뭐라고?' — 다시 읽어달라는 말인가.

    발화 '전체' 가 되읽기 요청일 때만 참이다. 부분일치로 잡으면
    "메일 뭐라고 보낼까" 까지 걸려서, 발송 명령이 조회로 새어 나간다.

    config.SAFE_PATTERNS 에 넣지 않고 여기 두는 이유가 그것이다 — 그 목록은
    부분일치라 '발화 전체' 라는 조건을 표현할 수 없다. 게이트와 라우터가
    같은 판정을 쓰도록 정의도 한 곳에만 둔다.
    """
    return bool(_REPEAT_ONLY.fullmatch(normalize(text)))


# 일정 '등록' — 사장님 지시로 승인 없이 바로 잡는다.
# ⚠ 취소·삭제·변경은 제외한다. 되돌리기 어려운 쪽은 그대로 승인을 받는다.
_CAL_CREATE = re.compile(
    r"(일정|미팅|약속|회의|스케줄|예약).{0,10}?(잡아|잡자|잡아줘|잡아둬|등록|추가|넣어)"
    r"|캘린더에.{0,10}?(추가|등록|넣어|만들)"
    # ⚠ 제목에 '미팅' 같은 말이 없는 일정도 많다 — "내일 3시 치과 등록해줘",
    #   "내일 4시 큐에이확인 등록해줘". 위 두 줄만 두면 이런 게 전부 막혀
    #   Claude 로 새고, normal 등급에는 Bash 가 없어 결국 등록이 안 된다.
    #   2026-08-12 QA 에서 걸렸다 — 그전 시험에 쓴 "테스트회의" 에 '회의' 가
    #   들어 있어서 통과하는 바람에 못 봤다.
    #
    #   때(날짜·시각)가 앞에 있을 때만 받는다. 그래야 "목소리 등록해줘"
    #   같은 '일정 아닌 등록' 과 갈린다 — 그런 말에는 때가 없다.
    r"|(오늘|내일|모레|이번주|다음주|\d+월|\d+일|\d+시).{0,20}?"
    r"(등록|잡아|잡아줘|추가|넣어)")
# ⚠ '변경'·'바꿔' 는 동사꼴로만 적는다. 통짜로 두면 제목에 든 두 글자에
#   걸려 등록 의도가 부정된다 — "대표 변경의 건", "주소 변경의 건" 처럼
#   서류 이름에 흔히 들어간다. 2026-08-12 10:34, 사장님이 텔레그램으로
#   "법무사 대표 변경의 건으로 서류 전달 11시 등록해줘" 하셨는데 등록이
#   아니라 '일정 찾기' 로 새어 "맞는 일정을 찾지 못했습니다" 로 끝났다.
_CAL_DESTRUCTIVE = ("취소", "삭제", "지워", "없애", "빼줘",
                    "옮겨", "미뤄", "변경해", "바꿔줘")


def is_calendar_create(text: str) -> bool:
    t = normalize(text)
    if any(k in t for k in _CAL_DESTRUCTIVE):
        return False
    return bool(_CAL_CREATE.search(t))


# 캘린더 삭제만 콕 집어 승인에서 뺀다.
#
# ⚠ DANGER_PATTERNS 의 '삭제' 자체는 절대 건드리지 않는다. 그건 파일·DB·
#   저장소 삭제까지 막는 문턱이다. 여기서 푸는 건 '일정' 이라는 말이
#   분명히 붙은 삭제 하나뿐이다.
_CAL_DELETE = re.compile(
    r"(일정|미팅|약속|회의|스케줄|예약|캘린더).{0,14}?(삭제|취소|지워|빼줘|없애)"
    r"|(삭제|취소|지워|빼줘|없애).{0,10}?(일정|미팅|약속|회의|스케줄)")


def is_calendar_delete(text: str) -> bool:
    """'일정을 지워달라' 인가. 다른 삭제와 섞이지 않게 좁게 본다."""
    return bool(_CAL_DELETE.search(normalize(text)))


def is_safe_query(text: str) -> bool:
    """승인 없이 그냥 처리해도 되는 '조회·질문' 인가."""
    # 되읽기는 이미 한 말을 다시 읽는 것뿐이다. 승인을 물으면
    # "뭐라고. 진행할까요?" 가 되어 대화가 막힌다.
    # (전체일치라서 행위어와 공존할 수 없다 — 무조건 통과가 안전한 유일한 경우)
    if is_repeat_request(text):
        return True
    # 일정 등록도 여기서 통과시킨다 — 아래 수정 동사 검사보다 '앞' 이어야
    # 한다. '등록'·'추가' 가 그 목록에 있어서 뒤에 두면 영영 안 통한다.
    if is_calendar_create(text):
        return True
    # 일정 삭제도 사장님 지시로 승인에서 뺐다 (2026-08-12). 파일·DB 삭제는
    # 그대로 게이트를 탄다 — is_calendar_delete 가 '일정' 이라는 말이 붙은
    # 것만 좁게 본다.
    # ⚠ 단, '전부 삭제' 는 면제 밖이다 — 단건과 달리 하루치가 한 번에
    #   사라진다. 복창+진행 승인을 거쳐 elevated 로 들어와야 실행된다.
    if is_calendar_delete(text) and not is_calendar_delete_all(text):
        return True
    # "너한테 한 말이 아닌데" 에 승인을 묻는 건 우스운 일이다. 실제로
    # 텔레그램에서 "'진행해' 라고 답장해 주세요" 가 갔다 (사장님 지적).
    if is_not_for_you(normalize(text)):
        return True
    probe = _TRAILING.sub("", text)
    # 수정 동사가 하나라도 있으면 조회가 아니다.
    # "고쳐줘 그리고 알려줘" 가 조회로 새는 걸 막는다.
    if any(rx.search(probe) for rx in _SAFE_OVERRIDE):
        return False
    # 날씨·브리핑은 읽기 전용 로컬 조회다. 승인을 물으면 "비 와?. 진행할까요?"
    # 가 된다. ⚠ 반드시 행위어 검사 '뒤' 여야 한다 — 앞에 두면
    # "추워도 배포 진행해" 가 '추워' 하나로 게이트를 통과해 버린다.
    t = normalize(text)
    # 시각·날짜도 같은 이유로 면제 — "몇 시냐고." 가 승인 게이트에 막혀
    # blocked 로 끝난 실사례가 있다.
    if (_is_weather_query(t) or _is_briefing(t) or _is_ads_detail(t)
            or _is_time_query(t) or _DATE_ONLY.fullmatch(t)
            or _is_mem_status(t) or _is_mem_clean(t) or _is_score_query(t)
            or _is_perf_query(t)
            or _is_end_call(t)
            or is_voice_status(t) or is_voice_correction(t)
            or any(k in t for k in ("오픈클로", "오픈클라우", "오픈크로", "openclaw"))):
        return True
    return any(rx.search(probe) for rx in _SAFE)


def is_bare_response(text: str) -> bool:
    """승인·거부 말만 덩그러니 있는가.

    "진행" 같은 말은 되물음에 대한 '대답' 이지 명령이 아니다.
    화이트리스트 방식으로 바꾸면서, 대기 중인 질문이 없을 때 이런 말이
    새 명령으로 취급돼 "진행. 진행할까요?" 하고 되묻는 일이 생겼다.
    """
    norm = normalize(text)
    if not norm or len(norm) > 8:
        return False
    words = (config.CONFIRM_WORDS + config.CONFIRM_NEGATIVES
             + config.CONFIRM_MISHEARD + config.CONFIRM_HOLD_WORDS)
    return any(normalize(w) == norm or norm == normalize(w) + "해" for w in words)


# 안전 목록에 안 걸렸을 때의 포괄 사유. 명시적 위험과 구분해야 한다 —
# DEV_MODE 는 이 사유로만 걸린 요청을 승인 없이 (셸 없는 권한으로) 통과시킨다.
SAFE_ONLY_REASON = "확인이 필요한 요청"


def danger_hit(text: str) -> str | None:
    """승인이 필요하면 그 이유를 반환. 필요 없으면 None.

    SAFE_ONLY_MODE 에서는 '안전 목록에 없으면 전부 승인' 이다.
    위험 표현을 하나씩 쫓는 방식으로는 whisper 오인식을 못 따라잡는다는 걸
    실사용에서 배웠다 ('주석문'→'주성문', 'config'→'폼픽').
    """
    probe = _TRAILING.sub("", text)

    # 일정 삭제·변경은 명시적 위험 검사보다 '앞' 에서 뺀다.
    #
    # 사장님 지시(2026-08-12): "일정 등록, 수정하는데 무슨 승인이 필요해?
    # 내가 등록하라 해서 등록하는 거고 수정하라 해서 수정하는 걸텐데."
    # 삭제도 같이 빼기로 정하셨다.
    #
    # ⚠ 여기서 빠지는 건 '일정' 이라는 말이 분명히 붙은 것뿐이다
    #   (is_calendar_delete). _DANGER 의 '삭제'·'지워' 자체는 그대로 살아
    #   있어서 파일·저장소·DB 삭제는 여전히 음성 승인을 받는다. 아래
    #   순서를 뒤집으면 그 구분이 사라지므로 반드시 앞에 둔다.
    # ⚠ '전부 삭제' 는 면제 밖 — 하루치가 한 번에 사라진다. 아래 _DANGER
    #   의 '삭제' 에 걸려 복창+진행을 받고, elevated 로 통째 삭제가 돈다.
    if ((is_calendar_delete(text) and not is_calendar_delete_all(text))
            or is_calendar_create(text)):
        return None

    # 명시적 위험 표현이 있으면 그것부터 (이유를 정확히 말해주기 위해)
    for rx in _DANGER:
        m = rx.search(probe)
        if m:
            return m.group(0)

    if not config.SAFE_ONLY_MODE:
        return None

    # 안전 목록에 없으면 승인을 받는다.
    if is_safe_query(text):
        return None
    return SAFE_ONLY_REASON


def is_hold(text: str) -> bool:
    """'기다려 달라'는 말인가.

    승인도 거부도 아닌 세 번째 대답이다. "조금만 기다려주세요" 를 취소로
    처리하면 사장님은 명령을 처음부터 다시 말해야 한다. 실제로 겪은 일이다.

    우선순위는 취소어 > 보류어 > 승인어.
    "잠깐, 아니 취소" 는 취소고, "오케이 잠깐만" 은 보류지 승인이 아니다.
    """
    norm = normalize(text)
    if not norm:
        return False
    if not any(normalize(k) in norm for k in config.CONFIRM_HOLD_WORDS):
        return False
    # 취소어가 함께 오면 취소가 이긴다
    return not any(normalize(k) in norm for k in config.CONFIRM_NEGATIVES)


def is_rejection(text: str) -> bool:
    """명확히 거부한 말인가.

    '승인을 못 알아들음' 과 '거부함' 을 구분하기 위해 필요하다.
    전자는 되물어야 하고, 후자는 바로 끝내야 한다.
    """
    norm = normalize(text)
    if not norm:
        return False
    return any(normalize(k) in norm for k in config.CONFIRM_NEGATIVES)


def is_confirmation(text: str) -> bool:
    """재확인 통과 판정. 전부 로컬 정규식 — 토큰을 쓰지 않는다.

    애매하면 무조건 False. 승인을 놓쳐 한 번 더 말하는 건 3초 손해지만,
    거부를 승인으로 잘못 읽으면 사고다. 비대칭이 크니 안전측으로 판정한다.
    """
    norm = normalize(text)
    if not norm:
        return False

    # 부정어가 하나라도 있으면 즉시 거부.
    # "진행하지마" 처럼 승인어와 부정어가 함께 오는 경우가 실제로 있다.
    # ("노" 같은 한 글자는 '노트북' 에도 걸리므로 목록에 넣지 않는다)
    if any(normalize(k) in norm for k in config.CONFIRM_NEGATIVES):
        return False

    # 보류어가 섞여도 승인이 아니다. "오케이 잠깐만" 은 기다려 달라는 뜻이다.
    if is_hold(text):
        return False

    if any(normalize(w) in norm for w in config.CONFIRM_WORDS):
        return True

    # whisper 가 잘못 적은 것으로 '관측된' 표기만 인정한다.
    # 자모 유사도 자동 보정은 '실패'·'성인'까지 승인으로 만들어 접었다.
    return any(normalize(w) in norm for w in config.CONFIRM_MISHEARD)


# ─────────────────────────────────────────────────────────
# 로컬 처리 — Claude 안 거침 (0 토큰)
# ─────────────────────────────────────────────────────────
def _osa(script: str) -> None:
    subprocess.run(["osascript", "-e", script], capture_output=True)


# normalize() 를 거친 문장(공백·구두점 없음) 전체와 맞아야 한다.
_TIME_ONLY = re.compile(
    r"(지금|현재)?(몇시|시간)(야|지|이야|예요|이에요|인가요|인가|입니까|니|냐|요)?"
    r"(좀)?(알려줘?|알려주세요|말해줘?)?"
)
_DATE_ONLY = re.compile(
    r"(오늘)?(며칠|몇일|날짜|무슨요일|요일)(이야|이지|이에요|인가요|인가|입니까|이니|이냐|요)?"
    r"(좀)?(알려줘?|알려주세요|말해줘?)?"
)


# 메모리 지킴이 — 상태는 조회, 정리는 허용목록 실행. "메모리에 남겨줘" 같은
# '기억' 요청과 헷갈리면 안 되므로 동사를 좁게 잡는다.
_MEM_WORDS = ("메모리", "램")
_MEM_STATUS_VERBS = ("상태", "현황", "얼마", "어때", "확인", "남았")
_MEM_CLEAN_VERBS = ("정리", "확보", "비워", "비우", "청소", "부족")


def _is_mem_status(t: str) -> bool:
    return any(w in t for w in _MEM_WORDS) and any(k in t for k in _MEM_STATUS_VERBS)


def _is_mem_clean(t: str) -> bool:
    return any(w in t for w in _MEM_WORDS) and any(k in t for k in _MEM_CLEAN_VERBS)


# "잘 알아듣고 있어?" — 동백이 자기 성적을 말한다.
# 채점 자체는 score.py 가 로그를 읽어서 한다 (0 토큰).
# ⚠ 한글은 음절 단위라 활용형을 다 적어야 한다. "알아듣고" 에는 "알아들" 이
#   없다 (듣 ≠ 들). 이 함정으로 "잘 알아듣고 있어?" 를 한 번 놓쳤다.
_SCORE_WORDS = ("점수", "성적", "채점", "몇점", "성공률",
                "잘알아들", "잘알아듣", "알아듣고있", "알아들고있",
                "잘듣고", "얼마나맞", "잘하고있")


def _is_score_query(t: str) -> bool:
    return any(k in t for k in _SCORE_WORDS)


# "빠르기 어때" — 층별 시간과 규칙 구멍을 스스로 말한다 (0 토큰).
# ⚠ 한글은 활용형을 다 적어야 한다. "얼마나 걸려" 에는 "걸리" 가 없다
#   (걸+려). 이 함정으로 처음에 한 번 놓쳤다.
_PERF_WORDS = ("빠르기", "속도", "느려", "느린가", "응답시간", "몇초", "빠른가",
               "얼마나걸리", "얼마나걸려", "얼마나걸렸", "얼마나빨라", "반응속도")
# '얼마나 걸려' 는 세상 모든 소요시간 질문에 들어간다 — "미팅 장소까지
# 얼마나 걸려?" 가 "오늘 165건 처리했습니다" 로 답한 실사례 (2026-08-13
# 12:24, 사장님: "자꾸 엉뚱한 대답하네"). 이동·장소·작업 얘기가 섞이면
# 동백 성능 질문이 아니다 → 클로드로 흘려 문맥으로 답하게 한다.
_PERF_BLOCK = ("장소", "까지", "가는", "가면", "이동", "거리", "차로", "지하철",
               "버스", "걸어", "도착", "출발", "미팅", "회의", "약속", "공항")
# 성능 질문은 짧다. 이보다 길면 '속도' 가 다른 얘기 끝에 끼어든 것이다.
_PERF_MAX_LEN = 24
# 문장이 끝나고 또 이어지면 여러 얘기를 한 것이다 — 성능이 주제가 아니다.
_MULTI_CLAUSE = re.compile(r"[.!?][^.!?]*[가-힣]")


def _is_end_call(t: str) -> bool:
    """'전화 끝났어' — 통화 정리는 승인 없이 한다 (읽고 쓰는 건 내 위키뿐)."""
    try:
        import call_notes

        return call_notes.is_end_call(t)
    except Exception:
        return False


def _is_perf_query(t: str) -> bool:
    s = t.replace(" ", "")
    if any(k in s for k in _PERF_BLOCK):
        return False
    if not any(k in s for k in _PERF_WORDS):
        return False
    # ⚠ 막을 낱말을 늘리는 것으로는 못 끝난다. 이 저장소는 안전 경계에서
    #   이미 배웠다 — "경계를 단어로 긋지 마라". 의도 판정도 같다.
    #   "너도 지금 속도가 느린 건 아닌데" 는 막을 낱말이 하나도 없는데
    #   성능 보고가 튀어나왔다 (2026-08-14 실사례, 사장님: "자꾸 동문서답").
    #   성능 질문은 짧게 튀어나온다("지금 속도 어때"). 여러 절을 거쳐 끝에
    #   '속도' 가 나오면 그건 주제가 아니라 지나가는 말이다.
    if len(s) > _PERF_MAX_LEN:
        return False
    # 절이 여럿이면(마침표·물음표가 중간에 있으면) 마찬가지로 지나가는 말이다.
    return not _MULTI_CLAUSE.search(t.strip())


# 회상 낌새 — 이 말이 보이면 장기 기억(memory_local)을 찾아 프롬프트에 붙인다.
_RECALL_HINT = ("지난", "저번", "그때", "전에말", "전에얘기", "아까말", "어제말",
                "기억나", "기억해", "말했던", "얘기했던", "했었잖", "뭐였지", "누구였지")


# 목소리 오인 정정과 현황. 전체일치가 아니라 짧은 발화만 받는다 —
# "방금 나야 라고 했는데" 같은 문장까지 정정으로 잡으면 안 된다.
_VOICE_FIX = ("방금나야", "방금나였어", "나야", "내목소리야", "내목소리인데",
              "나였어", "내가말한거야", "나라고")
# ⚠ '목소리잘'·'내목소리어때' 를 여기 두면 "내 목소리 잘 들려?" 까지
#   등록 현황 낭독(등록 6개, 학습 10개…)으로 샌다 — 사장님이 물으신 건
#   '지금 잘 들리냐' 였다 (2026-08-13). 그건 is_far_status_query 몫이라
#   여기서 뺐다. 현황은 '인식/학습' 처럼 명시할 때만.
_VOICE_STAT = ("목소리인식", "화자인식", "목소리학습", "목소리현황", "지문현황")

# ⚠ 전체일치만 받았더니 "지금 얘기하는 것도 나야" 를 놓쳤다 (2026-08-12).
#   정정이 안 잡히면 그 말이 Claude 로 넘어가고, 되살릴 발화는 90초 창이
#   닫히면서 사라진다 — 가장 비싸게 실패하는 길이다.
#   '너한테 한 말 아니야' 와 같은 처방을 쓴다: 짧은 발화에 한해 맺음말로 본다.
_ME_END = ("나야", "나였어", "나예요", "나입니다", "저예요", "저야",
           "나라고", "내목소리야", "나거든", "나임")
# 정정이 아니라 정정을 '인용' 하는 말. 이게 섞이면 명령이지 정정이 아니다.
_QUOTED = ("라고", "라는", "했는데", "그랬", "말했잖", "뭐라")
# 정정은 '지금 이 발화' 를 가리킨다. 이 말이 있어야 '어제 만난 사람은 나야'
# 같은 남 얘기와 갈린다.
_NOW_ME = ("지금", "방금", "아까", "이것", "이거", "이번", "여기", "내가", "나도")


def is_voice_correction(t: str) -> bool:
    if t in _VOICE_FIX:
        return True
    # 길면 정정이 아니다. 정정은 짧게 튀어나온다.
    if len(t) > 16 or any(k in t for k in _QUOTED):
        return False
    if not t.endswith(_ME_END):
        return False
    # 아주 짧으면("이것도 나야") 그 자체로 지금을 가리킨다.
    return len(t) <= 6 or any(k in t for k in _NOW_ME)


def is_voice_status(t: str) -> bool:
    return any(k in t for k in _VOICE_STAT)


# "지금 목소리 기억해" — 정정("나야")과는 다른 요청이다. 되살릴 발화가
# 아니라 '지금 이 목소리' 를 지문에 넣어 달라는 뜻이다.
#
# 2026-08-12 새벽에 사장님이 다섯 번 말씀하셨는데 표현이 매번 달랐고
# 하나도 안 잡혔다:
#   "지금 목소리 나라고, 기억하라고" / "목소리 말하고 기억하라고" /
#   "지금 목소리 내 목소리니까 등록해놔"
# is_voice_correction 은 '라고' 를 인용(_QUOTED)으로 보고 버리는데, 정작
# 구어에서 "기억하라고" 의 '라고' 는 인용이 아니라 강조다. 그래서 여기서는
# '목소리' + '기억/등록' 조합만 본다 — 인용 검사를 아예 타지 않는다.
_VOICE_LEARN_OBJ = ("목소리", "음성")
_VOICE_LEARN_ACT = ("기억", "등록", "학습", "외워", "저장", "담아")
# 요청이 아니라 '상태를 묻는' 꼴 — "기억하고 있는 거지?" 에 "기억했습니다"
# 로 답하면 질문이 영영 답을 못 받는다 (2026-08-13 09:13 실사례 ×2:
# "기존 목소리도 다 기억하고 있는 거지?" → 매번 "기억했습니다").
_VOICE_LEARN_STATE = ("하고있", "되어있", "돼있", "하고잇")
_VOICE_LEARN_Q_END = ("지", "냐", "니", "거야", "는가", "은가", "을까", "까")


def is_voice_enroll_request(t: str) -> bool:
    """'지금 목소리를 기억해 달라' 는 요청인가.

    조합은 넓게 잡되(닿는 곳이 _speaker_ok 통과 뒤라 안전), 상태를 묻는
    질문꼴은 뺀다 — 그건 등록이 아니라 답을 원하는 말이고, 빠지면
    큐웬·클로드가 제대로 답한다.
    """
    s = t.replace(" ", "")
    if not (any(o in s for o in _VOICE_LEARN_OBJ)
            and any(a in s for a in _VOICE_LEARN_ACT)):
        return False
    if any(k in s for k in _VOICE_LEARN_STATE):
        return False                     # "기억하고 있…" = 진행 상태 질문
    if s.endswith(_VOICE_LEARN_Q_END):
        return False                     # "…기억하는 거지/할까" = 질문
    return True


# "너한테 한 말 아니야" — 사람끼리 하던 말을 동백이 가로챘을 때의 정정.
# 부분일치다. 통화 중에 튀어나오는 말이라 문장에 섞여 온다.
# "너한테 한 말 아니야" — 사람끼리 하던 말을 동백이 가로챘다는 신호.
#
# ⚠ 정규식으로 뼈대를 잡으려다 두 번 놓쳤다. 실제 발화는 매번 조금씩
#   다르다: "너한테 한 말 아니야", "너한테 한 얘기 아니야", "너한테 한
#   말이 아닌데". 지금은 낱말 조합으로 본다 — 2인칭 + (말/얘기) + 부정.
#   조금 넓게 잡혀도 괜찮다. 그런 말은 어차피 동백에게 하는 명령이 아니다.
_NOT_FOR_YOU_WORDS = ("너안불렀", "너부른거아니", "너부르는거아니",
                      "통화중이야", "통화중이니", "전화중이야", "전화중이니",
                      "너말고", "너한테말고")
_YOU = ("너한테", "너에게", "너보고", "너랑", "니한테", "너")
_SPEECH = ("말", "얘기", "이야기", "소리")
# ⚠ 한글은 음절 단위다. "아닌데" 에는 "아니" 가 없다 (아+닌+데).
#   이 함정으로 "너한테 한 말이 아닌데" 를 놓쳤다 — 활용형을 다 적는다.
_DENY = ("아니", "아닌", "아냐", "아녜", "안한", "안했", "말고")
# ⚠ 아래 조합 판정(_YOU × _SPEECH × _DENY)은 짧은 말에만 쓴다.
#   세 무리에서 하나씩 '문장 어디에든' 있으면 걸리는 방식이라, 말이
#   길수록 우연히 셋 다 들어갈 확률이 올라간다. 실사례 2026-08-12 23:02 —
#   사장님이 오판을 항의하신 "아니 통화가 끝이 났는데 … 너랑 얘기를
#   하는데 … 너한테 하는 얘기인지 모르는 거야?" (90자) 가 아니·너랑·얘기
#   로 걸려 전화 모드 10분. 항의조차 오판당하는 구조였다.
#   진짜 정정은 짧다 — "너한테 한 말 아니야". 긴 문장으로 귀를 닫게
#   하려면 위 명시 목록("너말고"·"통화중이야")이 있고, 그쪽은 길이 무관.
_NOT_FOR_YOU_MAX_CHARS = 30


def is_not_for_you(t: str) -> bool:
    """'너한테 한 말 아니야' — 사람끼리 하던 말을 동백이 가로챘다는 신호."""
    if any(k in t for k in _NOT_FOR_YOU_WORDS):
        return True
    if len(t) > _NOT_FOR_YOU_MAX_CHARS:
        return False
    return (any(k in t for k in _YOU)
            and any(k in t for k in _SPEECH)
            and any(k in t for k in _DENY))


def wants_memory(t: str) -> bool:
    return any(k in t for k in _RECALL_HINT)


# 시각 질문에 다른 용건이 섞였다는 신호 — 이때는 추론 질문이라 Claude 몫.
_TIME_BLOCK = ("미팅", "일정", "약속", "회의", "까지", "나가", "도착", "출발",
               "후에", "전에", "되면", "걸려", "만나", "했지", "하기로", "했었")


def _is_time_query(t: str) -> bool:
    """시각 질문인가 — '그것만 물었을 때' 원칙은 지키되 현실에 견디게.

    전체일치만 믿었더니 whisper 가 '지금'을 '10분'으로 적은 "10분 몇 시야"
    가 Claude 로 가서 "시계 확인할 도구가 없어" 라는 반말을 들었다 (실사례).
    재질문("몇 시냐고")도 마찬가지. 짧고 다른 용건이 없으면 시각 질문이다.
    """
    if any(k in t for k in _TIME_BLOCK):
        return False
    if _TIME_ONLY.fullmatch(t):
        return True
    return "몇시" in t and len(t) <= 12


# 스킬 다루는 말들 (PLAN-skills 2단계) — 만들기는 dongbaek.handle 이
# 클로드 초안을 거쳐 처리하고, 목록·빼기는 handle_local 이 즉답한다.
# ⚠ normalize 는 공백까지 지운다 ('스킬 목록'→'스킬목록') — 패턴도 그렇게.
_SKILL_CREATE = ("스킬로만들", "스킬만들", "스킬로저장", "스킬로등록")
_SKILL_LIST = ("스킬목록", "스킬뭐", "스킬몇", "무슨스킬", "스킬보여", "스킬알려")
_SKILL_REMOVE = re.compile(r"([가-힣A-Za-z0-9-]{2,20})스킬(?:을|를)?(?:지워|삭제|빼|제거)")


def is_skill_create(t: str) -> bool:
    return any(k in t for k in _SKILL_CREATE)


def is_skill_list(t: str) -> bool:
    return any(k in t for k in _SKILL_LIST)


# 미팅 모드 (2026-08-13) — 시작·종료 문구. normalize 는 공백을 지운다.
# 종료는 미팅 소리에 우연히 섞이기 어려운 명시 꼴만 — "회의 끝나고 보자"
# 같은 말에 열리면 전화 모드의 10:25 사고를 반복한다.
_MEETING_START = ("미팅모드", "회의모드", "미팅시작해", "회의시작해", "미팅모드시작")
_MEETING_END = ("미팅끝났", "회의끝났", "미팅모드해제", "회의모드해제",
                "미팅종료", "회의종료", "미팅모드꺼", "미팅마쳤")


def is_meeting_start(t: str) -> bool:
    s = t.replace(" ", "")
    return any(k in s for k in _MEETING_START)


def is_meeting_end(t: str) -> bool:
    s = t.replace(" ", "")
    return any(k in s for k in _MEETING_END)


# GPT 모드 (2026-08-14 사장님 지시) — 미팅 모드와 같은 기계를 쓰되, 사장님이
# 지피티와 나누는 대화를 통째로 옵시디언에 남긴다. 동백은 한마디도 안 한다.
#
# ⚠ whisper 는 'GPT' 를 한글로 여러 갈래로 적는다. 실측된 것과 흔한 오기를
#   같이 넣는다 — 'QN'(사장님 실사례), '지피티', '집티', '쥐피티'.
#   영문 'gpt' 는 normalize 뒤에도 남으므로 소문자로 함께 본다.
_GPT_START = ("gpt모드", "지피티모드", "쥐피티모드", "집티모드", "qn모드",
              "gpt모드시작", "지피티모드시작", "gpt시작할게", "지피티랑얘기")
_GPT_END = ("gpt끝났", "지피티끝났", "쥐피티끝났", "gpt모드해제", "지피티모드해제",
            "gpt모드꺼", "지피티모드꺼", "gpt종료", "지피티종료", "gpt끝")


def is_gpt_start(t: str) -> bool:
    s = t.replace(" ", "").lower()
    return any(k in s for k in _GPT_START)


def is_gpt_end(t: str) -> bool:
    s = t.replace(" ", "").lower()
    return any(k in s for k in _GPT_END)


# 그만 — 읽던 것을 멈춘다 (2026-08-14 실사례).
#
# ⚠ 이게 없어서 사고가 났다. "그만해" 가 정지가 아니라 **재실행** 이었다:
#   MERGE_ON_INTERRUPT 이 말 보탬으로 보고 앞 명령에 이어 붙여 통째로 다시
#   물었고, 같은 482자 답이 또 나왔다. 사장님이 "그만해" 할 때마다 반복 —
#   15:01~15:04 사이에 같은 답을 여섯 번 읽었다.
#
# ⚠ 낱말 포함으로만 보면 안 된다 ('그만큼'·'그만두고'). 그리고 정지는 짧게
#   튀어나온다 — 절 단위로 쪼개 짧은 절에서만 찾는다. 그래야
#   "그만 읽어 달라는 게 아니고 …" 같은 긴 설명이 정지로 읽히지 않는다.
_STOP_WORDS = re.compile(
    r"(그만해|그만하라|그만(?![큼두둬])|됐어|됐다|멈춰|스톱|"
    r"읽지마|그만읽|안읽어도|조용히해|입다물)")
_STOP_CLAUSE_MAX = 8


# 메일 보고 (2026-08-14 지시) — "업체별로 분류해서 정리후 나한테 보고해".
#
# ⚠ 조회('안 읽은 메일 몇 통')와 갈라야 한다. 그건 이미 로컬이 0초에 답한다.
#   여기는 본문까지 읽고 클로드가 판단하는 무거운 작업이라(30초~1분),
#   명시적으로 청하실 때만 돈다.
_MAIL_REPORT = ("메일정리", "메일보고", "메일분석", "받은메일정리", "받은메일보고",
                "메일업체별", "업체별로정리", "메일취합")


def is_mail_report(t: str) -> bool:
    return any(k in t.replace(" ", "") for k in _MAIL_REPORT)


def is_stop_speaking(t: str) -> bool:
    """지금 읽는 것을 멈추라는 말인가.

    ⚠ 쪼개는 것은 구두점뿐이다. 공백으로도 쪼개면 "조용히 해" 가 '조용히'
      와 '해' 로 갈려 안 잡히고, 반대로 "음악 그만 틀어줘도 될 만큼" 의
      '그만' 한 낱말이 절로 서서 정지로 읽힌다. 절 안의 공백은 지우고
      길이로 거른다 — 정지는 짧게 튀어나온다.
    """
    for clause in re.split(r"[.!?,·]+", t or ""):
        c = clause.replace(" ", "").strip()
        if c and len(c) <= _STOP_CLAUSE_MAX and _STOP_WORDS.search(c):
            return True
    return False


# "내 목소리 잘 들려?" — 동백이 원거리를 인지하고 있는지 사장님이 확인하는 말.
# (사장님 지시 2026-08-13: 인지 여부를 확인할 수 있게 해달라)
_FAR_ASK = ("잘들려", "잘들리", "내목소리어때", "목소리어때", "내가먼가",
            "멀리있는거알아", "멀리있는거아", "소리작아", "소리잘들려",
            "내목소리잘", "목소리작")


def is_far_status_query(t: str) -> bool:
    s = t.replace(" ", "")
    return any(k in s for k in _FAR_ASK)


# 실측 증폭 배수는 데몬이 들고 있다. router 가 dongbaek 을 import 하면
# 순환이 되므로, 데몬이 기동할 때 함수를 꽂아 준다 (speak 와 같은 방식).
_far_status_fn = None


def set_far_status(fn) -> None:
    global _far_status_fn
    _far_status_fn = fn


def handle_local(text: str, *, elevated: bool = False) -> str | None:
    """로컬에서 끝낼 수 있으면 답변 문자열 반환. 아니면 None → Claude로.

    elevated=True 는 위험 게이트를 이미 통과했다는 뜻이다.
    쓰기 작업(일정 등록·삭제)은 이때만 수행한다.
    """
    if not config.LOCAL_ENABLED:
        return None
    t = normalize(text)

    # 시각·날짜는 '그것만 물었을 때'만 로컬로 답한다.
    #
    # "몇시" 가 들어 있다는 이유로 시계를 읽어주면
    # "내일 미팅이 2시인데 몇 시에 나가야 해?" 에도 "지금 10시 54분입니다" 가
    # 나간다. 실제로 겪은 오답이다. 문장에 다른 말이 붙어 있으면
    # 단순 조회가 아니라 추론 질문이므로 Claude 에 넘긴다.
    if _is_time_query(t):
        now = datetime.now()
        return f"지금 {now.hour}시 {now.minute}분입니다."

    # "뭐라고?" — 방금 한 말을 그대로 다시 읽는다. Claude 에 물을 일이 아니다
    # (실측 6초·$0.08). 이미 말한 문장이 speak 에 남아 있다.
    if is_repeat_request(text):
        import speak

        said = speak.last_reply().strip()
        return said or "아직 드린 말씀이 없습니다."

    if _DATE_ONLY.fullmatch(t):
        now = datetime.now()
        yo = "월화수목금토일"[now.weekday()]
        return f"오늘은 {now.month}월 {now.day}일 {yo}요일입니다."

    # 날씨 — open-meteo 직접 조회 (0.5초, 0 토큰). 지금까진 답할 곳이
    # 없었다: 클로드는 날씨 도구가 없고, 게이트키퍼는 실데이터라 물러난다.
    if _is_weather_query(t):
        import weather_local

        answer = (weather_local.tomorrow() if "내일" in t
                  else weather_local.today())
        if answer is not None:
            return answer

    # 브리핑 — 날씨·일정·메일·광고를 한 호흡에 (전부 로컬, 0 토큰)
    if _is_briefing(t):
        return _briefing_text()

    music = _handle_music(text, t)
    if music is not None:
        return music

    m = re.search(r"(\d+)\s*(분|초)\s*(타이머|알람)", t) or re.search(
        r"(타이머|알람)\s*(\d+)\s*(분|초)", t
    )
    if m:
        nums = [g for g in m.groups() if g and g.isdigit()]
        unit = "분" if "분" in m.group(0) else "초"
        if nums:
            n = int(nums[0])
            sec = n * 60 if unit == "분" else n
            # 알림도 재생 큐를 거친다(/say). say 를 직접 띄우면 그 순간
            # 나가던 말과 '겹친다' — 실제로 겪은 소리다. 게다가 직접 say 는
            # 출력 장치(-a)를 못 골라 스피커 없는 모니터로 새기도 한다.
            # 데몬이 죽어 있을 때만 say 로 직접 말한다 — 그땐 겹칠 상대도 없다.
            import shlex

            import speak

            msg = f"{n}{unit} 지났습니다"
            try:
                dev = speak.output_device()   # 데몬에서는 기동 때 이미 캐시돼 있다
            except Exception:
                dev = []                      # 못 알아내면 기본 장치로라도 말한다
            fallback = " ".join(
                ["say", *map(shlex.quote, dev),
                 "-v", config.TTS_VOICE, shlex.quote(msg)]
            )
            try:
                tok = config.TOKEN_FILE.read_text().strip()
                announce = (
                    "curl -s -m 5 -X POST "
                    f"'http://{config.CONTROL_HOST}:{config.CONTROL_PORT}/say?token={tok}' "
                    "-H 'Content-Type: application/json' "
                    f"-d '{{\"text\": \"{msg}\"}}' || {fallback}"
                )
            except OSError:
                announce = fallback
            # 완전히 분리해서 띄운다. 부모 파이프를 물고 있으면
            # 데몬을 감싼 셸/런처가 타이머가 끝날 때까지 종료되지 않는다.
            subprocess.Popen(
                ["bash", "-c", f"sleep {sec}; {announce}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return f"{n}{unit} 타이머 맞췄습니다."

    if "볼륨" in t or "소리" in t:
        if any(k in t for k in ("올려", "키워", "크게")):
            _osa("set volume output volume (output volume of (get volume settings) + 15)")
            return "볼륨 올렸습니다."
        if any(k in t for k in ("내려", "줄여", "작게")):
            _osa("set volume output volume (output volume of (get volume settings) - 15)")
            return "볼륨 내렸습니다."
        if any(k in t for k in ("음소거", "꺼", "무음")):
            _osa("set volume with output muted")
            return "음소거했습니다."

    # "내 목소리 잘 들려?" — 지금 잘 들리는지. 현황 낭독보다 먼저 본다.
    # (데몬이 실측 증폭 배수를 안다 — 여기서는 부르기만 한다)
    if is_far_status_query(t) and _far_status_fn is not None:
        return _far_status_fn()

    # 목소리 인식 현황 — 학습이 잘 되고 있는지 귀로 확인한다 (0 토큰)
    if is_voice_status(t):
        import json as _json

        import voiceprint

        people = voiceprint.enrolled()
        if not people:
            return "등록된 목소리가 없습니다."
        learned = voiceprint.learned()
        parts = [f"{n} 등록 {c}개" + (f", 학습 {learned[n]}개" if learned.get(n) else "")
                 for n, c in people.items()]
        line = "목소리는 " + ", ".join(parts) + "입니다."
        line += f" 남 목소리는 {voiceprint.strangers()}개 기억하고 있습니다."
        try:
            rows = [_json.loads(l) for l in
                    (config.STATE / "voice_scores.jsonl").read_text().splitlines()[-40:]]
            ok = sum(1 for r in rows if r.get("name"))
            if rows:
                line += f" 최근 {len(rows)}번 중 {ok}번 통과했습니다."
        except (OSError, ValueError):
            pass
        return line

    # 무거운 서비스 껐다 켜기 (오픈클로 등) — 사장님 지시로 목록에 있는 것만.
    # whisper 가 '오픈클로' 를 여러 갈래로 적어서 변형을 함께 잡는다.
    if any(k in t for k in ("오픈클로", "오픈클라우", "오픈크로", "오픈컬로",
                            "openclaw", "오픈클", "오픈크라우")):
        import memory_guard

        if any(k in t for k in ("꺼", "끄", "종료", "중지", "내려", "정지")):
            return memory_guard.service_switch("오픈클로", False)
        if any(k in t for k in ("켜", "시작", "올려", "실행", "가동")):
            return memory_guard.service_switch("오픈클로", True)
        on = memory_guard.service_running("오픈클로")
        return ("오픈클로는 지금 켜져 있습니다." if on
                else "오픈클로는 꺼져 있습니다.")

    # 메모리 — 현황 보고와 허용목록 정리 (memory_guard, 0 토큰)
    if _is_mem_status(t) or _is_mem_clean(t):
        import memory_guard

        if _is_mem_clean(t):
            acts = memory_guard.reclaim("음성 지시")
            head = (" ".join(a + "." for a in acts)
                    if acts else "지금은 정리할 것이 없습니다.")
            return head + " " + memory_guard.status_speak()
        return memory_guard.status_speak()

    if any(k in t for k in ("토큰얼마", "토큰사용", "얼마나썼", "토큰현황")):
        import bridge

        return bridge.usage_summary()

    # 대화 채점 — "잘 알아듣고 있어?" (0 토큰).
    # 동백이 자기 성적을 스스로 말한다. 2026-08-12 새벽처럼 씹기 시작하면
    # 사장님이 물어보시는 즉시 숫자로 드러나야 한다.
    if _is_score_query(t):
        import score

        days = 7 if any(k in t for k in ("이번주", "일주일", "최근")) else 1
        return score.speak_report(days)

    # 층별 빠르기 — "속도 어때", "느려?" (0 토큰)
    if _is_perf_query(t):
        import perf

        days = 7 if any(k in t for k in ("이번주", "일주일", "최근")) else 1
        return perf.speak_report(days)

    # 업체별 상세 성과 — "자세히 보고해줘" (0 토큰).
    # 기간을 말 안 하면 오늘, 오늘이 비어 있으면 어제로 내려간다.
    if _is_ads_detail(t):
        import ads_local
        from datetime import date as _date, timedelta as _td

        p = ads_local.period(text)
        explicit = p is not None
        if p is None:
            today = _date.today()
            p = (today, today, "오늘")
        answer = ads_local.detail(*p)
        if answer and "데이터가 없습니다" in answer and not explicit:
            y = _date.today() - _td(days=1)
            answer = ads_local.detail(y, y, "어제")
        if answer is not None:
            return answer

    # 광고 실적 — 광고플랫폼 DB 직접 조회 (1초, 0 토큰)
    if _is_ads_query(t):
        import ads_local

        answer = ads_local.speak(text)
        if answer is not None:
            return answer

    # 최근 커밋 — git log 직접 읽기 (0.2초, 0 토큰). 실사용 단골 질문인데
    # 실측 한 번에 6~8초·$0.1~0.3 이 나가던 것. 저장소를 직접 불렀을 때만.
    if _is_commit_query(t):
        repo = _commit_query_repo(text)
        if repo:
            answer = _recent_commits(repo)
            if answer is not None:
                return answer

    # 메일 조회 — Mail.app 직접 조회 (0.4초, 0 토큰)
    if _is_mail_unread(t):
        import mail_local

        return mail_local.unread_count()
    if _is_mail_recent(t):
        import mail_local

        return mail_local.recent()

    # 일정 조회 — EventKit 직접 읽기 (0.3초, 0 토큰)
    if _is_schedule_query(text):
        import calendar_local

        # 특정 하루를 물었으면 그 날만 읽는다.
        # 그 날짜까지 닿도록 조회 범위를 함께 넓힌다 — 다음 주 목요일을
        # 물었는데 7일치만 긁어오면 정작 그 날이 안 들어온다.
        only = schedule_only_date(text)
        days = _schedule_days(t)
        if only is not None:
            from datetime import date

            days = max(days, (only - date.today()).days + 1)

        # 이름을 대고 물었으면 그것만 찾는다 ("강남 미팅 언제야").
        # 언제인지 모르니 기간을 넓게 잡는다 — 어차피 이름으로 걸러진다.
        keyword = schedule_keyword(text)
        if keyword:
            days = max(days, config.SCHEDULE_SEARCH_DAYS)

        # 권한이 없거나 실패하면 None → Claude 경로로 폴백된다.
        return calendar_local.speak_events(days, only_date=only, keyword=keyword)

    # 일정 쓰기. 말이 정형화돼 확실히 해석될 때만 로컬로 처리하고,
    # 조금이라도 애매하면 None 을 돌려 Claude 가 판단하게 한다.
    #
    # ⚠ 등록과 삭제는 위험도가 다르다. is_safe_query 는 등록을 이미
    #   '승인 불필요' 로 통과시키는데(is_calendar_create), 여기서는 elevated
    #   를 요구하고 있었다. 두 정책이 서로 어긋나 있었던 것이다.
    #
    #   결과: 등록 명령은 승인을 안 거치니 elevated 가 안 서고 → 로컬을 못
    #   타고 → Claude 로 갔는데 → normal 등급에는 Bash 가 없어 osascript 를
    #   못 돌린다. 그래서 동백이 "캘린더 쓰는 도구가 꺼져 있어서 등록이 안
    #   됩니다" 라고 답했다 (2026-08-12, 사장님이 그 말이 무슨 뜻이냐고
    #   물으셔서 찾았다). 세션 경로 문제가 아니라 이 어긋남이었다.
    #
    #   삭제는 되돌릴 수 없으므로 그대로 승인을 요구한다.
    out = _handle_schedule_write(text, t, allow_delete=elevated)
    if out is not None:
        return out

    # 마지막 층 — 스킬 (skills/*.md, 사장님·동백이 만든 선언형 능력).
    # 내장 규칙이 다 놓친 말만 여기 온다. 걸리면 0 토큰으로 끝나고,
    # 안 걸리면 그대로 큐웬 → 클로드로 흐른다 (PLAN-skills 1단계).
    import skills_local

    if is_skill_list(t):
        return skills_local.listing()
    m = _SKILL_REMOVE.search(t)
    if m and m.group(1).strip():
        return skills_local.remove(m.group(1).strip().replace(" ", "-"))
    return skills_local.match(t, text)


# ─────────────────────────────────────────────────────────
# 목소리 등록 요청
# ─────────────────────────────────────────────────────────
# 마이크를 쥐고 있는 건 데몬이라, 등록도 데몬 안에서 해야 한다.
# 별도 프로세스로 --enroll 을 돌리면 같은 장치를 두고 다투고,
# 사장님이 터미널 앞에 앉아 있어야 한다. 말로 시키는 게 맞다.
_ENROLL = re.compile(r"(목소리|음성|화자)\s*(를|을|도)?\s*(등록|추가|기억)")
_ENROLL_NAME_TAG = re.compile(r"이름은\s*([가-힣A-Za-z0-9]{1,12})")
_ENROLL_NAME_PRE = re.compile(r"([가-힣A-Za-z0-9]{2,12})\s*(?:의|님의|님)?\s*(?:목소리|음성)")
# 이름 자리에 올 수 없는 말 — "내 목소리 등록해줘" 의 '내' 를 이름으로 삼으면 안 된다
_NOT_A_NAME = {"내", "제", "나", "저", "나의", "저의", "새", "이", "그", "우리",
               "사람", "다른", "새로운", "당신"}


# 이름 뒤에 붙어 오는 꼬리. 받아쓰기는 '김철수님', '김철수이야' 처럼
# 조사·호칭을 붙여 적는데 그대로 저장하면 등록 이름이 지저분해진다.
_NAME_TAIL_LONG = re.compile(r"(이라고|라고|이야|이에요|예요|입니다|이라|님|씨)$")
# 한 글자 조사는 이름 일부일 수도 있다 ('동백이'). 충분히 길 때만 뗀다.
_NAME_TAIL_SHORT = re.compile(r"(으로|로|이|가|은|는)$")


def _trim_name(s: str) -> str:
    s = s.strip()
    for _ in range(4):
        cut = _NAME_TAIL_LONG.sub("", s)
        if cut != s and len(cut) >= 2:
            s = cut
            continue
        cut = _NAME_TAIL_SHORT.sub("", s)
        if cut != s and len(cut) >= 3:
            s = cut
            continue
        break
    return s[:12]


def enroll_request(text: str) -> str | None:
    """목소리 등록 요청이면 이름을 반환 (이름을 못 찾으면 빈 문자열). 아니면 None."""
    if not _ENROLL.search(normalize(text)):
        return None
    m = _ENROLL_NAME_TAG.search(text)
    if m:
        return _trim_name(m.group(1))
    m = _ENROLL_NAME_PRE.search(text)
    if m and _trim_name(m.group(1)) not in _NOT_A_NAME:
        return _trim_name(m.group(1))
    return ""


def clean_name(text: str) -> str:
    """이름만 물었을 때의 대답에서 이름을 뽑는다. '이름은 김철수이야' → '김철수'."""
    m = _ENROLL_NAME_TAG.search(text)
    if m:
        return _trim_name(m.group(1))
    t = re.sub(r"(이름은|이름이|이름|해줘|입니다)", " ", text)
    t = re.sub(r"[^\w가-힣 ]", " ", t)
    for w in t.split():
        name = _trim_name(w)
        if name and name not in _NOT_A_NAME:
            return name
    return ""


# ─────────────────────────────────────────────────────────
# 음악 — 유튜브 (0 토큰)
# ─────────────────────────────────────────────────────────
# "아이유 밤편지 틀어줘" 를 Claude 에 보내면 수백 원이 든다.
# 유튜브 검색의 첫 곡을 긁어 기본 브라우저로 열면 공짜고 2초다.
_MUSIC_STOP = re.compile(r"(음악|노래|뮤직|유튜브).{0,4}?(꺼|끄|멈춰|정지|스톱|그만)")
# "아이유 틀어줘" 처럼 음악 단어 없이도 재생 의도인 말이 많다.
# 이 맥에는 TV·IoT 가 없어 '틀어/재생' 이 음악 외의 뜻일 일이 없다.
# 반면 '켜/들려' 는 다른 문장에도 흔해서 음악 단어와 함께일 때만 잡는다.
_MUSIC_PLAY_STRONG = re.compile(r"(틀어|재생)")
_MUSIC_PLAY_WEAK = re.compile(r"(들려|켜)")
_MUSIC_WORD = re.compile(r"(음악|노래|뮤직)")
# 곡명만 남기기 위해 원문에서 걷어낼 말
_MUSIC_STRIP = re.compile(
    r"(유튜브\s*뮤직|유튜브|음악|노래|뮤직|좀|하나|아무거나|아무)"
    r"|(틀어|재생|들려|켜)\s*(줘|줄래|주세요|봐|바|라)?"
)


def _open_url(url: str) -> None:
    subprocess.Popen(
        ["open", url],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _close_youtube() -> None:
    # 'is running' 가드가 없으면 osascript 가 닫으라는 앱을 오히려 실행한다
    for app in ("Google Chrome", "Safari", "Whale", "Arc"):
        _osa(
            f'if application "{app}" is running then\n'
            f'  tell application "{app}" to close '
            f'(every tab of every window whose URL contains "youtube.com")\n'
            f"end if"
        )


def _youtube_first_video(query: str) -> str | None:
    """검색 결과 첫 영상의 videoId. 유튜브 HTML 이 바뀌면 None → 검색 페이지 폴백."""
    import urllib.parse
    import urllib.request

    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=4).read().decode("utf-8", "replace")
    except Exception:
        return None
    m = re.search(r'"videoId":"([\w-]{11})"', html)
    return m.group(1) if m else None


def _handle_music(text: str, t: str) -> str | None:
    import urllib.parse

    if _MUSIC_STOP.search(t):
        _close_youtube()
        return "음악 껐습니다."
    is_play = _MUSIC_PLAY_STRONG.search(t) or (
        _MUSIC_WORD.search(t) and _MUSIC_PLAY_WEAK.search(t)
    )
    if not is_play:
        return None

    query = _MUSIC_STRIP.sub(" ", text)
    query = re.sub(r"\s+", " ", query).strip(" .,?!")
    if not query:
        _open_url("https://music.youtube.com")
        return "유튜브 뮤직 열었습니다."

    vid = _youtube_first_video(query)
    if vid:
        _open_url(f"https://www.youtube.com/watch?v={vid}")
        return f"{query} 틀었습니다."
    _open_url("https://www.youtube.com/results?search_query=" + urllib.parse.quote(query))
    return f"유튜브에서 {query} 검색을 열었습니다."


# ─────────────────────────────────────────────────────────
# 로컬 처리 판정
# ─────────────────────────────────────────────────────────
# 실적 질문 — 지표 낱말 하나만으로는 안 잡는다.
# "매출 어떻게 올리지" 같은 상담까지 DB 조회로 가로채면 답이 엉뚱해진다.
# 기간이나 광고주 이름이 함께 나와야 '조회'로 본다.
_ADS_METRIC = ("매출", "실적", "광고비", "성과", "로아스", "전환", "roas", "집행")
_ADS_ADVICE = ("올리", "높이", "늘리", "어떻게", "왜", "전략", "분석해", "개선")


def wants_ads_context(t: str) -> bool:
    """클로드에 광고 숫자를 실어 보낼 질문인가.

    _is_ads_query 보다 넓다. 저쪽은 '로컬이 통째로 답할 수 있나' 를 보는데,
    이쪽은 '클로드가 답하려면 숫자가 필요한가' 를 본다. 그래서 조언·분석
    요청("분석해서 보고해", "왜 이래", "어떻게 올려")이 오히려 여기 들어온다 —
    그것들이야말로 숫자 없이는 답할 수 없는 질문이다.

    2026-08-12 에 이 길이 없어서 사장님이 두 번 헛물켜셨다. '분석해' 라는
    말 때문에 클로드로 갔는데 클로드에는 DB 도구가 없었다.
    """
    ads_ctx = any(k in t for k in ("광고", "광고플랫폼", "로아스", "roas",
                                   "캠페인", "광고주", "집행"))
    if not ads_ctx:
        return False
    # 광고 얘기가 분명하면 지표 낱말이 없어도 숫자를 실어 준다.
    # "광고 왜 이렇게 안 나와" 가 딱 그런 질문이다 — 지표를 안 대셨지만
    # 숫자 없이는 아무 말도 할 수 없다.
    return (any(k in t for k in _ADS_METRIC)
            or any(k in t for k in _ADS_ADVICE)
            or any(k in t for k in ("어때", "어떤가", "잘되", "안나", "안돼",
                                    "보고", "알려", "브리핑")))


def _is_ads_query(t: str) -> bool:
    if not any(k in t for k in _ADS_METRIC):
        return False
    if any(k in t for k in _ADS_ADVICE):
        return False
    import ads_local

    return ads_local.period(t) is not None or ads_local.advertiser(t) is not None


# ─── 전화 모드 ───
# 통화가 시작되면 동백이 귀를 닫는다. 상대에게 하는 말("네네, 보내드릴게요")
# 을 자기 명령으로 알아듣는 사고를 막는다 — "전화 받는 것과 동백이와
# 대화하는 걸 구분해 달라" 는 지시의 답이 이것이다. 전부 전체일치다:
# 부분일치로 잡으면 "통화중에 들은 건데 메일 보내줘" 까지 귀를 닫는다.
_HOLD_CMD = re.compile(
    r"(전화|통화)(받을게|받는다|받을거야|받아야돼|왔어|왔다|올거야|중이야|모드)(야|해줘|켜줘|요)?"
    r"|잠깐(만)?쉬어(줘)?"
)
# 호출어 없이도 통화 시작으로 볼 말 — 등록 화자 음성일 때만 인정한다.
_HOLD_BARE = re.compile(r"(여보세요|전화받을게|전화왔다|전화왔어)")
_RESUME = re.compile(
    r"(다시들어(줘)?|다시듣기|이제들어(도돼)?|전화끝(났어)?|통화끝(났어)?"
    r"|그만쉬어|다시일하자|전화모드(꺼줘|해제|끝))"
)


def is_hold_request(t: str) -> bool:
    """'전화 받을게' — 귀를 닫아 달라는 말인가. normalize 된 입력 기준."""
    return bool(_HOLD_CMD.fullmatch(t))


def is_bare_hold(t: str) -> bool:
    """호출어 없이 온 '여보세요' 류 — 통화 시작 신호인가."""
    return bool(_HOLD_BARE.fullmatch(t))


def is_resume_request(t: str) -> bool:
    return bool(_RESUME.fullmatch(t))


# "안 불렀는데" — 잘못 되물었을 때 돌아오는 말. 명령이 아니라 정정이다.
# 되물음 직후에만 본다 (dongbaek 의 nearmiss_until). 아무 때나 잡으면
# "안 불렀는데 왜 왔대" 같은 남 얘기까지 동백이 자기 일로 받는다.
#
# 답답하면 같은 말을 세 번 반복하신다("안 불렀는데 안 불렀는데 안 불렀는데") —
# 정규화하면 한 덩어리로 붙으므로 반복을 허용해야 걸린다.
_NOT_CALLING = re.compile(
    r"^("
    r"(너를?|동백(아|이|을|한테)?)?"
    r"(안불렀|부른거아니|부른적없|안부른|안불러|말안했|혼잣말)"
    r"(는데요?|어요?|다고?|이?야|이?라고|습니다|음)?"
    r"(뭐야|왜|응|어|참)*"
    r")+$"
)


def is_not_calling(t: str) -> bool:
    """'안 불렀다' 는 정정인가. normalize 된 입력 기준."""
    return bool(_NOT_CALLING.match(t))


# 날씨 — 힌트 낱말이 있고, 판단이 필요한 말이 안 섞였을 때만.
# "내일 미팅인데 우산 챙겨야 해?" 는 날씨 조회가 아니라 판단 질문이다 —
# 시각·날짜 처리("그것만 물었을 때")와 같은 원칙.
# 명사형(날씨·기온·우산)은 길이 무관 — 이 낱말이 나오면 날씨 얘기가 맞다.
# 동사꼴(비오·더워)은 짧은 물음일 때만 — 긴 문장 속의 우연한 글자가
# 오발동한다. 실사례 2026-08-13 05:48: "지금 계속 배우고 있는 거야 내
# 목소리" 를 whisper 가 "비오고" 로 적었고, 문장 전체를 안 보는 규칙이
# 날씨를 읽었다. 정정 문자("비오고가 아니고 배우고")에조차 또 발동했다.
_WEATHER_NOUNS = ("날씨", "기온", "몇도", "우산")
_WEATHER_VERBISH = ("비오", "비와", "비올", "눈오", "눈와", "더워", "추워", "덥", "춥")
_WEATHER_BLOCK = ("미팅", "일정", "약속", "회의", "출장", "나가", "가야", "메일",
                  "목소리", "배우", "학습", "아니고")
_WEATHER_VERBISH_MAX = 12          # 동사꼴만으로 발동하는 발화 길이 상한


def _is_weather_query(t: str) -> bool:
    if any(k in t for k in _WEATHER_BLOCK):
        return False
    if any(k in t for k in _WEATHER_NOUNS):
        return True
    return (any(k in t for k in _WEATHER_VERBISH)
            and len(t.replace(" ", "")) <= _WEATHER_VERBISH_MAX)


# 업체별 상세 성과 — 브리핑 뒤 "자세히 보고해" 한 마디의 목적지.
# 다른 대상(메일·코드…)의 '자세히' 와 헷갈리면 안 된다.
_ADS_DETAIL_HINT = ("업체별", "광고주별", "각업체", "자세히", "상세")
_ADS_DETAIL_VERB = ("보고", "성과", "실적", "알려")
_ADS_DETAIL_BLOCK = ("메일", "코드", "문서", "일정", "날씨", "파일", "커밋", "회의")


def _is_ads_detail(t: str) -> bool:
    if not any(k in t for k in _ADS_DETAIL_HINT):
        return False
    if not any(k in t for k in _ADS_DETAIL_VERB):
        return False
    if any(k in t for k in _ADS_DETAIL_BLOCK):
        return False
    # 대상 없는 짧은 지시("자세히 보고해" — 브리핑 후속)거나 광고 문맥이
    # 붙었을 때만. 없으면 "그 계약서 자세히 알려줘" 까지 광고로 오인한다.
    return len(t) <= 8 or any(
        k in t for k in ("광고", "성과", "실적", "업체", "광고주", "매출"))


# 브리핑 — "문서 브리핑해줘" 같은 자료 요약과 헷갈리면 안 된다.
# 대상이 붙은 브리핑은 판단·도구가 필요하므로 클로드 몫이다.
_BRIEF_BLOCK = ("문서", "파일", "코드", "저장소", "프로젝트", "메일",
                "회의록", "계약", "제안", "보고서")


def _is_briefing(t: str) -> bool:
    return "브리핑" in t and not any(k in t for k in _BRIEF_BLOCK)


def _briefing_text() -> str:
    """하루 브리핑 — 이미 있는 로컬 조회를 한 번에 (0 토큰).

    조각 하나가 죽어도 나머지는 말한다. 날씨가 안 나온다고
    일정까지 침묵하면 브리핑이 아니다.
    """
    parts: list[str] = []
    try:
        import weather_local

        w = weather_local.today()
        if w:
            parts.append(w)
    except Exception:
        pass
    try:
        import calendar_local

        evs = calendar_local.events(days=1)
        if evs is not None:
            if not evs:
                parts.append("오늘 일정은 없습니다.")
            else:
                heads = []
                for e in evs[:4]:
                    if e.get("all_day"):
                        when = "종일"
                    else:
                        s = e["start"]
                        when = f"{s.hour}시" + (f" {s.minute}분" if s.minute else "")
                    heads.append(f"{when} {e['title']}")
                parts.append(f"오늘 일정 {len(evs)}건. " + ". ".join(heads) + ".")
    except Exception:
        pass
    try:
        import mail_local

        m = mail_local.unread_count()
        if m:
            parts.append(m)
    except Exception:
        pass
    try:
        import ads_local

        # 브리핑은 대개 하루 시작에 듣는다 — 그때 의미 있는 건 '어제' 실적이다.
        # 오늘 것은 자정 직후엔 늘 비어 있어 "데이터가 없습니다" 소음만 낸다.
        a = ads_local.speak("어제 실적")
        if a and "데이터가 없" not in a:
            parts.append(a)
    except Exception:
        pass
    return " ".join(parts) or "지금은 브리핑할 정보를 가져오지 못했습니다."


# 커밋 '조회' — 행위형(커밋해/커밋 찍어)은 로컬이 아니다. SAFE_OVERRIDE 와
# 같은 기준을 쓴다 — 조회와 행위의 경계가 두 군데서 다르면 반드시 샌다.
_COMMIT_ACT = re.compile(r"커밋\s*(해|하|치|찍|남겨|올려)")


def _is_commit_query(t: str) -> bool:
    if "커밋" not in t:
        return False
    if _COMMIT_ACT.search(t):
        return False
    return any(k in t for k in ("알려", "보여", "확인", "뭐", "최근", "마지막", "이력", "기록", "봐"))


def _commit_query_repo(text: str) -> str | None:
    """발화가 저장소를 '직접' 불렀을 때만 경로를 준다. 아니면 None → Claude.

    이름 없이 "최근 커밋 알려줘" 만 오면 어느 저장소인지는 대화 문맥이
    필요하므로 Claude 가 맞다. 별칭 해석은 dongbaek._guess_target 하나만
    쓴다 — 두 벌이 되면 반드시 갈라진다. (지연 임포트: dongbaek 이 이
    모듈을 임포트하므로 모듈 수준에서 서로 부르면 순환이 된다)
    """
    import dongbaek

    target = dongbaek._guess_target(text)
    # _guess_target 은 못 찾으면 동백 자기 폴더를 돌려준다. 그건 '직접 부른'
    # 게 아니므로, 동백을 정말 지목한 경우만 인정한다.
    if target == str(config.ROOT) and "동백" not in text:
        return None
    return target


def _recent_commits(repo: str) -> str | None:
    """git log 를 읽어 말로 옮긴다. 저장소가 아니거나 실패하면 None → Claude."""
    try:
        out = subprocess.run(
            ["git", "-C", repo, "log", "--oneline", "-3"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    msgs = []
    for line in out.stdout.strip().splitlines():
        m = line.split(" ", 1)[1] if " " in line else line
        # "feat(blog): …" 같은 접두어는 눈으로 볼 때 물건이지 귀로 들을 게 아니다.
        msgs.append(re.sub(r"^\w+(\(.+?\))?!?:\s*", "", m))
    name = repo.rstrip("/").rsplit("/", 1)[-1]
    listed = ". 다음, ".join(msgs)
    return f"{name} 최근 커밋 {len(msgs)}개입니다. {listed}."


def _is_mail_unread(t: str) -> bool:
    if "메일" not in t:
        return False
    return any(k in t for k in ("안읽", "안읽은", "읽지않", "새메일", "새로온", "몇통", "몇개"))


def _is_mail_recent(t: str) -> bool:
    if "메일" not in t:
        return False
    if any(w in t for w in _WRITE_WORDS) or "보내" in t or "발송" in t:
        return False
    return any(k in t for k in ("최근", "최신", "왔", "확인", "뭐와", "뭐가와", "알려"))


def kt_has_time(text: str) -> bool:
    """언제로 옮길지가 말에 들어 있는가. 없으면 옮기기로 보지 않는다.

    ⚠ 날짜 없는 시각도 시각이다. "오후 4시로 옮겨줘" 가 보통이지
      "내일 오후 4시로" 라고 또박또박 말씀하시지 않는다. 안쪽 처리에서는
      오늘로 보정하면서 이 관문에서만 안 했더니, 등록은 되는데 옮기기는
      클로드로 새는 상태가 됐다 (2026-08-12 실측).
    """
    import korean_time as kt
    return (kt.parse_datetime(text) is not None
            or kt.parse_datetime("오늘 " + text) is not None)


_SCHED_VERBS = ("등록", "추가", "생성", "만들", "잡아", "잡아줘", "넣어",
                "옮겨", "미뤄", "당겨", "연기", "변경", "바꿔",
                "취소", "삭제", "지워", "빼줘", "없애")


# 큐웬을 부를지 가르는 데만 쓰는 넓은 목록. _SCHEDULE_WORDS 보다 넓다 —
# 저쪽은 '제목이 껍데기뿐인가' 를 보는 데 쓰여서 좁아야 한다.
# "치과 예약 취소해" 처럼 때를 안 말씀하시는 경우가 여기서 걸린다.
#
# ⚠ 모듈 맨 위에서 _SCHEDULE_WORDS 를 참조하면 안 된다 — 그건 이 아래에
#   정의돼 있어서 import 자체가 터진다. 함수 안에서 합친다.
_SCHEDULEISH_EXTRA = ("예약", "미팅", "면담", "진료", "캘린더", "선약")


def _looks_scheduleish(text: str, t: str) -> bool:
    """큐웬에게 물어볼 만한 '일정 얘기' 인가. 싸게 거른다."""
    if not any(v in t for v in _SCHED_VERBS):
        return False
    if any(w in t for w in _SCHEDULE_WORDS + _SCHEDULEISH_EXTRA):
        return True
    return kt_has_time(text)


# "오늘/내일 (내) 일정 전부 삭제" — 날짜 통째 삭제.
# 단건 삭제(키워드 하나)와 다르게 여러 개가 한 번에 사라지므로,
# 이 꼴은 danger 목록에 넣어 복창+진행 승인을 반드시 거치게 한다.
_CAL_DELETE_ALL = re.compile(
    r"(오늘|내일|모레)\s*(내\s*)?(일정|스케줄|약속)\s*(을|를)?\s*"
    r"(전부|모두|다|싹)\s*(다\s*)?(삭제|지워|취소|없애|빼)")


def is_calendar_delete_all(text: str) -> bool:
    return bool(_CAL_DELETE_ALL.search(normalize(text)))


def _handle_schedule_write(text: str, t: str,
                           allow_delete: bool = False) -> str | None:
    """일정 등록·삭제를 로컬에서. 해석이 확실할 때만.

    allow_delete 는 '음성 승인을 이미 받았는가' 다. 등록은 되돌리기 쉬워
    승인 없이 하고, 삭제는 되돌릴 수 없어 승인을 받은 뒤에만 한다.
    """
    # 날짜 통째 삭제 — 승인(allow_delete) 후에만. 키워드 검색보다 먼저
    # 봐야 한다: "오늘 내 건 일정을 전부 다 삭제해" 가 키워드 검색으로
    # 흘러 "…와 맞는 일정을 찾지 못했습니다" 가 됐다 (실사례 09:02).
    if is_calendar_delete_all(text):
        if not allow_delete:
            return None                  # 게이트로 — 복창+진행을 받는다
        from datetime import date, timedelta

        base = date.today()
        label = "오늘"
        if "내일" in t:
            base, label = base + timedelta(days=1), "내일"
        elif "모레" in t:
            base, label = base + timedelta(days=2), "모레"
        return calendar_local.delete_day(base, label)
    # ⚠ 일정 낱말('미팅'·'회의'…)만 요구하면 제목에 그 말이 없는 일정이
    #   전부 막힌다. "내일 3시 치과 등록해줘", "내일 4시 큐에이확인 등록해줘"
    #   같은 것들이다. 실제로 QA 에서 걸렸다 (2026-08-12) — 앞서 시험할 때
    #   쓴 "테스트회의" 에 '회의' 가 들어 있어서 통과하는 바람에 못 봤다.
    #
    #   그래서 is_calendar_create(등록 의도) 도 함께 받는다. 대신 아래에서
    #   날짜·시각과 제목이 둘 다 확실할 때만 실제로 쓴다 — 하나라도 애매하면
    #   None 을 돌려 Claude 가 되묻는다. "목소리 등록해줘" 처럼 시각이 없는
    #   말은 거기서 걸러진다.
    # 옮기기는 is_calendar_create 가 '파괴적' 으로 걸러내므로 따로 받는다.
    _moving = any(w in t for w in ("옮겨", "미뤄", "당겨", "연기해",
                                   "변경해", "바꿔줘")) and (
        "일정" in t or "미팅" in t or "회의" in t or "약속" in t
        or kt_has_time(text))
    if (not any(w in t for w in _SCHEDULE_WORDS)
            and not is_calendar_create(text) and not _moving):
        # 규칙은 못 읽었다. 일정 얘기 같으면 큐웬에게 넘긴다.
        #
        # ⚠ 아무 말에나 부르지 않는다. 0원이지만 0.6초가 붙고, 잡담까지
        #   여기로 오면 대화가 그만큼 늦어진다. '때를 가리키는 말' 과
        #   '일정을 다루는 동사' 가 둘 다 있을 때만 부른다.
        if _looks_scheduleish(text, t):
            return _schedule_via_qwen(text, t)
        return None

    import calendar_local
    import korean_time as kt

    # 삭제 — 대상이 확실할 때만.
    #
    # 사장님 지시(2026-08-12): "일정 등록, 수정하는데 무슨 승인이 필요해?
    # 내가 등록하라 해서 등록하는 거고 수정하라 해서 수정하는 걸텐데."
    # 이어서 삭제도 같이 하기로 정하셨다. 사장님 캘린더이고 지우라고
    # 말씀하신 것이다 — 한 번 더 묻는 건 도움이 아니라 마찰이다.
    #
    # ⚠ 승인은 뗐지만 '엉뚱한 걸 지우지 않는' 장치는 그대로다.
    #   제목이 껍데기 말뿐이면(아래) 손대지 않고, calendar_local 이 여러 건
    #   걸리면 이름을 대며 되묻는다. 승인이 막던 건 '실수로 지우기' 가
    #   아니라 '지우려는 의사' 였고, 그 의사는 이미 말씀에 있다.
    if any(w in t for w in ("삭제", "취소", "지워", "빼줘", "없애")):
        title = kt.extract_title(text)
        if not title or len(title) < 2:
            return None          # 무엇을 지울지 불분명 → Claude 로
        # 제목 추출이 실패하면 '일정'·'미팅' 같은 껍데기 말만 남는다.
        # 그걸로 지우면 제목에 그 말이 든 엉뚱한 일정이 걸린다.
        # ("내일 미팅 취소해줘" → title='일정' 이었던 실제 사례)
        if title in _SCHEDULE_WORDS or title in ("일정", "미팅", "회의", "약속", "스케줄"):
            return None          # 대상 불명확 → Claude 가 되묻게 한다
        return calendar_local.delete_matching(title)

    # 옮기기 — 삭제와 달리 내용이 사라지지 않는다. 잘못 옮기면 다시 옮기면
    # 되므로 등록과 같은 무게로 다룬다. 대상이 여럿이면 calendar_local 이
    # 손대지 않고 되묻는다.
    # ⚠ 등록이 옮기기보다 먼저다. "등록해줘" 는 애매하지 않다 — 옮겨 달라는
    #   뜻으로 "등록해줘" 라고 하시지 않는다. 반대로 옮기기 동사는 제목 속에
    #   섞여 들어온다.
    #
    #   실측 2026-08-12 10:34 — "오늘 일정에 법무사 대표 변경의 건으로
    #   서류 전달 11시 등록해줘" 가 옮기기로 새어 "'법무사 대표 의 건 어
    #   서류 전달' 와 맞는 일정을 찾지 못했습니다" 로 끝났다.
    #   '대표 변경의 건' 은 법무 서류 이름이지 옮기라는 말이 아니다.
    #
    #   그래서 '변경'·'바꿔' 는 동사꼴일 때만 옮기기로 본다. 명사구에 붙는
    #   '변경의'·'변경 건' 과 갈라야 한다.
    _asked_create = any(w in t for w in ("추가", "생성", "만들", "잡아", "넣어", "등록"))
    _move_verbs = ("옮겨", "미뤄", "당겨", "연기해", "변경해", "바꿔줘", "바꿔주")
    if not _asked_create and any(w in t for w in _move_verbs):
        dt = kt.parse_datetime(text)
        if dt is None:
            # "10시 반으로 옮겨줘" 처럼 날짜를 안 붙이시는 게 보통이다.
            # 날짜가 없으면 오늘로 본다 — 옮기는 건 대개 코앞의 일정이다.
            dt = kt.parse_datetime("오늘 " + text)
        if dt is None:
            return None          # 언제로 옮길지 불분명 → Claude 가 되묻는다
        title = kt.extract_title(text)
        # ⚠ extract_title 이 동사를 남긴다 ('본사 미팅 옮겨'). 그대로
        #   찾으면 제목에 '옮겨' 가 든 일정을 뒤지게 되어 늘 못 찾는다.
        # ⚠ 말끝에 붙은 것만 뗀다. 통째로 지우면 '대표 변경의 건' 같은
        #   제목에서 한가운데를 파먹는다 — 그러면 찾을 수 있는 일정이 없다.
        title = (title or "").strip()
        for verb in ("옮겨줘", "옮겨", "미뤄줘", "미뤄", "당겨줘", "당겨",
                     "연기해줘", "연기해", "연기", "변경해줘", "변경해", "변경",
                     "바꿔줘", "바꿔주", "바꿔"):
            if title.endswith(verb):
                title = title[: -len(verb)]
                break
        title = " ".join(title.split()).strip()
        if not title or len(title) < 2:
            return None
        # 삭제와 같은 이유로, 껍데기 말만 남았으면 대상이 불명확한 것이다.
        if title in _SCHEDULE_WORDS or title in ("일정", "미팅", "회의", "약속", "스케줄"):
            return None
        return calendar_local.reschedule(title, dt)

    # 등록
    if any(w in t for w in ("추가", "생성", "만들", "잡아", "넣어", "등록")):
        dt = kt.parse_datetime(text)
        if dt is None:
            return None          # 날짜·시각이 불명확 → Claude 로
        title = kt.extract_title(text)
        if not title or len(title) < 2:
            return None
        return calendar_local.create(title, dt, kt.parse_duration_hours(text))

    return _schedule_via_qwen(text, t)


def _schedule_via_qwen(text: str, t: str) -> str | None:
    """규칙이 못 읽은 일정 명령을 큐웬이 읽는다 (0원, 약 0.6초).

    규칙(정규식+낱말표)은 새 표현마다 깨진다. 2026-08-12 하루에만 '변경'
    두 글자 때문에 세 곳을 고쳤다 — "법무사 대표 변경의 건" 은 서류
    이름인데 옮기기 명령으로 읽혔다. 사장님 제안으로 큐웬을 사이에 끼운다.

        규칙 0.03초  →  큐웬 0.6초·0원  →  클로드 10초·$0.28

    ⚠ 시각은 큐웬에게 묻지 않는다. 의도와 제목만 받고, 시각은 원문에서
      korean_time 이 뽑는다. 소형 모델에 숫자를 맡기면 창작한다 —
      11시를 1시로 적어놓고도 태연할 수 있고, 잘못 등록된 일정은
      무시당하는 것보다 나쁘다. 사장님이 그 시각을 믿고 움직이시기 때문이다.
    """
    if not getattr(config, "GATEKEEPER_SCHEDULE", False):
        return None
    try:
        import gatekeeper
    except Exception:
        return None
    import perf

    with perf.track("qwen", text, note="일정 명령") as _t:
        parsed = gatekeeper.parse_schedule(text)
        _t.ok = bool(parsed)
    if not parsed:
        return None
    title, intent = parsed["title"], parsed["intent"]
    if len(title) < 2:
        return None

    import calendar_local
    import korean_time as kt

    if intent == "삭제":
        return calendar_local.delete_matching(title)

    # 시각은 코드가 뽑는다. 날짜를 안 붙이시면 오늘로 본다.
    dt = kt.parse_datetime(text) or kt.parse_datetime("오늘 " + text)
    if dt is None:
        return None                  # 언제인지 모르면 클로드가 되묻게 한다
    if intent == "수정":
        return calendar_local.reschedule(title, dt)
    return calendar_local.create(title, dt, kt.parse_duration_hours(text))


_SCHEDULE_WORDS = ("일정", "스케줄", "미팅", "약속", "회의")
# 쓰기 의도를 나타내는 말. 하나라도 있으면 '조회'로 처리하면 안 된다.
# "잡자"가 빠져 있어서 "미팅 잡자"가 조회로 새어나간 적이 있다.
_WRITE_WORDS = ("추가", "생성", "만들", "잡아", "잡자", "잡죠", "잡을",
                "넣어", "넣자", "등록", "변경", "수정", "삭제", "취소",
                "옮겨", "빼줘", "없애")


# 일정 단어가 있어도 이 말이 섞이면 '읽어달라'가 아니라 '따져달라'는 뜻이다.
# "내일 미팅이 2시인데 몇 시에 나가야 해?" 에 내일 일정을 낭독하면 엉뚱하다.
# 로컬은 낭독밖에 못 하므로 추론 질문은 Claude 로 보낸다.
_REASON_WORDS = ("나가", "출발", "도착", "가야", "가려면", "이동", "걸리", "걸려",
                 "타고", "데리", "픽업", "준비", "챙겨", "늦지", "늦으")


def _is_schedule_query(text: str) -> bool:
    """'조회'인 일정 질문만 참. 쓰기 동사가 섞이면 거짓(→ 위험 게이트로)."""
    t = normalize(text)
    if not any(w in t for w in _SCHEDULE_WORDS):
        return False
    if any(w in t for w in _WRITE_WORDS):
        return False
    if any(w in t for w in _REASON_WORDS):
        return False
    # '회의' 라는 낱말이 있어도 일정을 묻는 게 아닐 수 있다 — "회의 정리
    # 끝났어?" 가 7일치 36개 낭독으로 나갔다 (2026-08-13 10:46). 때를
    # 가리키는 말도, 찾을 이름도, 목록을 청하는 어미도 없는 상태 질문은
    # 조회가 아니다 → None 으로 흘려 클로드가 문맥으로 답하게 한다.
    # ("미팅 뭐 있어" 는 목록 어미가 있어 그대로 조회다.)
    if (schedule_only_date(text) is None
            and not _has_time_word(t)
            and not schedule_keyword(text)
            and not _asks_listing(t)):
        return False
    return True


_TIME_WORDS = ("오늘", "내일", "모레", "이번주", "이번 주", "다음주", "다음 주",
               "담주", "이번달", "요일", "주말", "언제")
_LIST_ASK = ("뭐있", "뭐가있", "뭐야", "뭐지", "있어", "있나", "있니",
             "알려줘", "보여줘", "말해줘", "읽어줘", "어떻게돼", "어떻게되")


def _has_time_word(t: str) -> bool:
    s = t.replace(" ", "")
    return any(w.replace(" ", "") in s for w in _TIME_WORDS)


def _asks_listing(t: str) -> bool:
    s = t.replace(" ", "")
    return any(w in s for w in _LIST_ASK)


_WEEKDAYS = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
_WEEKDAY_RX = re.compile(r"([월화수목금토일])요일")

# 일정 질문에 늘 붙는 말. 이걸 걷어내고 남는 낱말이 있으면
# 그게 사장님이 찾는 일정의 이름이다 ("강남 미팅 언제야" → '강남').
#
# ⚠ 정규식으로 문장 전체에서 지우면 안 된다. 한 글자짜리가 고유명사 안에서
#   부분 일치해 낱말을 부순다 — '이' 를 지워 '도도커뮤니케이션' 이
#   '도도커뮤니케' 가 됐다. 낱말로 쪼갠 뒤 통째로 맞을 때만 버린다.
_FILLER_WORDS = {
    "일정", "스케줄", "미팅", "약속", "회의", "캘린더", "행사",
    "언제", "몇시", "며칠", "무슨", "어떻게", "어디", "누구", "뭐", "뭔",
    "알려", "알려줘", "말해", "보여", "보여줘", "확인", "체크",
    "있어", "있나", "있지", "없어", "해줘", "주세요", "부탁",
    "좀", "나", "내", "우리", "저희", "그", "이", "저", "요",
    "오늘", "내일", "모레", "이번", "다음", "담", "주", "달", "월", "년",
    "오전", "오후", "시", "분", "요일", "때",
    "월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일",
}
# 낱말 뒤에 붙는 조사·어미. 떼고 나서 판정한다.
# 순서가 중요하다 — 긴 것부터 적어야 '에서' 가 '에' 로 먼저 잘리지 않는다.
_TAIL = re.compile(r"(에서|에게|한테|으로|이랑|랑|하고|과|와|의|은|는|이|가|을|를|도|로|에)$")
_ENDING = re.compile(r"(이야|이지|예요|이에요|입니까|인가요|인가|야|지|니|냐|까|요)$")


# 말이 아직 안 끝난 것처럼 보이는 꼬리.
#
# 이게 걸리면 더 오래 기다린다. 연결어미('~하고', '~는데')나 조사로 끝나면
# 뒤에 할 말이 남아 있다는 뜻이다. 실제로 '일단 그 부분보다도' 가 그대로
# 명령이 되어 실행됐다.
_UNFINISHED_TAIL = re.compile(
    r"(그리고|그래서|그런데|근데|또한|그러면|그럼|일단|먼저|아니면|하지만|"
    r"보다도|보다|는데|은데|지만|다가|면서|해서|어서|아서|"
    # 조건·의도 연결어미. '들어가면', '확인하려고', '보러' 뒤에는 본론이 남는다
    r"면|려고|러|자|든지|든|거나|"
    r"으로|에서|에게|한테|이랑|"
    # '고' 는 연결어미지만 '~다고/~라고/~냐고' 는 인용형 종결이라 완결된 말이다.
    # ("그래서 어떻게 하겠다고" 는 끝난 질문이다)
    r"(?<![다라자냐느])고|"
    r"서|며|랑|와|과|의|을|를|은|는|이|가|도)$"
)


def looks_unfinished(text: str) -> bool:
    """말이 중간에 끊긴 것처럼 보이는가. 그러면 더 기다려야 한다."""
    t = (text or "").strip().rstrip(".!?…,")
    if not t:
        return True
    return bool(_UNFINISHED_TAIL.search(t))


# 의미적으로 '끝난 말' 의 꼬리 — 명령형·의문형·서술 종결어미.
# 2026 음성 에이전트 표준은 침묵 길이가 아니라 '의미 완결 + 짧은 침묵' 이다
# (LiveKit TurnDetector·AssemblyAI 등). looks_unfinished 가 "미완결이면 더
# 기다림" 의 절반이라면, 이건 "완결이 분명하면 빨리 답함" 의 나머지 절반.
# ⚠ 보수적으로 좁게 잡는다 — 여기 걸리면 대기가 1.4→0.8초로 줄어들 뿐이고,
#   안 걸리면 지금과 똑같다. 오판의 비용이 비대칭이라 확실한 꼬리만 적는다.
_COMPLETE_TAIL = re.compile(
    r"(해줘|해봐|해라|주세요|하세요|보세요|해주세요|해봐요|줄래|볼래|할까|할래|"
    r"어때|어때요|뭐야|뭐니|뭐지|거야|거니|건가|인가|일까|잖아|"
    r"습니다|습니까|입니다|입니까|네요|나요|가요|까요|세요|어요|아요|지요|죠|"
    r"았어|었어|였어|했어|겠어|있어|없어|이야|아니야|맞지|그렇지|되지|안돼|"
    r"알려줘|말해줘|보여줘|읽어줘|불러줘|찾아줘|꺼줘|켜줘|올려줘|내려줘)$"
)


def looks_complete(text: str) -> bool:
    """말이 의미적으로 끝난 게 분명한가. 그러면 덜 기다려도 된다."""
    t = (text or "").strip().rstrip(".!?…~ ")
    if not t or looks_unfinished(t):
        return False
    return bool(_COMPLETE_TAIL.search(t))


# 순수 맞장구 — 동백이 말하는 중에 들어와도 '끼어들기(명령)' 가 아니라
# '듣고 있다는 신호' 다. 2026 표준 턴 판정은 backchannel/barge-in/침묵을
# 갈라 본다. 이걸 명령으로 받으면 "네." 가 클로드까지 가서 헛답이 나간다.
# ⚠ '계속'·'계속해' 는 넣지 않는다 — 그건 무시하면 안 되는 요청이다.
_BACKCHANNELS = (
    "네네", "네", "넵", "넹", "옙", "예예", "예", "응응", "응", "웅", "어",
    "음", "그래그래", "그래", "그치", "그렇지", "그렇구나", "맞아맞아", "맞아",
    "맞지", "오케이", "오케", "좋아", "좋지", "알았어", "알겠어", "알았습니다",
    "알겠습니다", "아하", "하하", "오",
)


def is_bare_backchannel(t: str) -> bool:
    """발화 전체가 맞장구 낱말의 조합뿐인가 (t 는 normalize() 결과)."""
    s = t.replace(" ", "")
    if not s or len(s) > 10:
        return False
    while s:
        for w in _BACKCHANNELS:          # 목록이 이미 긴 것부터다
            if s.startswith(w):
                s = s[len(w):]
                break
        else:
            return False
    return True


def _core(word: str) -> str:
    """조사·어미를 뗀 알맹이. 떼고 너무 짧아지면 원형을 그대로 둔다.

    '강남' 의 '도' 는 조사가 아니라 이름의 일부다. 떼면 '송' 이 되어
    아무것도 못 찾는다. 그래서 두 글자 미만으로 줄어들면 되돌린다.
    """
    for rx in (_ENDING, _TAIL):
        cut = rx.sub("", word)
        if len(cut) >= 2:
            word = cut
    return word


# 동사·서술어 꼬리. 이걸로 끝나는 낱말은 일정 이름이 아니다.
# whisper 오기까지 목록으로 쫓을 수는 없어서 꼴로 거른다.
_VERBISH = re.compile(
    r"(해봐|해줘|해봐요|하봐|보자|보여|알려|말해|찾아|찾지|찾어|"
    r"정리|요약|브리핑|브래핑|브리핑해|읽어|불러|물어|봐봐|"
    # "회의 정리 끝났어?" 의 '끝났어' 가 검색어가 되어 "끝났어 관련 일정은
    # 찾지 못했습니다" 가 나갔다 (2026-08-13 10:46, 두 번). 상태를 묻는
    # 꼬리도 일정 이름이 아니다.
    r"끝났|끝나|끝냈|마쳤|됐어|됐니|됐나|회의록|미팅록|"
    r"어때|어떻|왜|관련|대해서|대해|위해|"
    # 기간을 가리키는 말도 일정 '이름' 이 아니다. "이번주 일정 알려줘" 에
    # '이번주' 가 검색어가 되어 "이번주 관련 일정은 찾지 못했습니다" 가 났다.
    r"이번주|이번달|다음주|다음달|저번주|지난주|주간|월간|하루|이번)")


def schedule_keyword(text: str) -> str | None:
    """특정 일정을 콕 집어 물었으면 그 이름을 반환. 아니면 None.

    "강남 미팅 언제야" 에 전체 일정을 통째로 읽어주면 사장님이 원하는
    한 줄을 직접 골라내야 한다.
    """
    cands = []
    for raw in re.split(r"[^\w가-힣]+", text):
        if len(raw) < 2:
            continue
        w = _core(raw)
        # 조사를 끝까지 뗀 형태도 함께 본다. '시야' 는 알맹이가 '시' 라
        # 한 글자로 줄어 _core 가 되돌리지만, 그래도 버릴 말이다.
        bare = _TAIL.sub("", _ENDING.sub("", raw))
        if len(w) < 2 or {w, raw, bare} & _FILLER_WORDS:
            continue
        # ⚠ 동사꼴은 검색어가 아니다. 일정 이름은 명사다.
        #
        #   실측 2026-08-12 22:31 — "내일 일정에 대해서 브래핑해봐" 에
        #   '브래핑해봐' 가 검색어가 되어 "브래핑해봐 관련 일정은 찾지
        #   못했습니다" 가 나갔다. 이어서 "관련 일정을 왜 못 찾지?" 에는
        #   '관련' 이 검색어가 되어 "관련 관련 일정은 찾지 못했습니다".
        #
        #   필러 목록만으로는 못 막는다. whisper 가 매번 다르게 적기
        #   때문이다('브리핑'→'브래핑'). 꼴로 걸러야 한다.
        if _VERBISH.search(raw):
            continue
        cands.append(w)
    # 가장 긴 낱말이 대개 고유명사다 ('강남', '농특위', '스마트축산')
    return max(cands, key=len) if cands else None


def schedule_only_date(text: str):
    """'그 하루만' 물은 거면 그 날짜를 반환. 아니면 None.

    "목요일 미팅 알려줘" 에 일주일치를 통째로 읽어주던 문제 때문에 생겼다.
    요일을 물었는데 주간 브리핑이 돌아오면 원하는 답을 직접 골라내야 한다.
    """
    from datetime import date, timedelta

    t = normalize(text)
    today = date.today()
    if "오늘" in t:
        return today
    if "내일" in t:
        return today + timedelta(days=1)
    if "모레" in t:
        return today + timedelta(days=2)

    m = _WEEKDAY_RX.search(text)
    if not m:
        return None
    want = _WEEKDAYS[m.group(1)]
    # '다음 주 목요일' 은 이번 주 목요일이 지났든 아니든 다음 주다.
    ahead = (want - today.weekday()) % 7
    if "다음주" in t or "담주" in t:
        ahead += 7
    elif ahead == 0:
        pass          # 오늘이 그 요일이면 오늘
    return today + timedelta(days=ahead)


def _schedule_days(t: str) -> int:
    """말에서 조회 기간을 뽑는다. CLAUDE.md 의 기간 해석 규칙과 맞춘다."""
    if "오늘" in t:
        return 1
    if "내일" in t:
        return 2
    if "이번달" in t or "이번주말" in t:
        return 30
    if "다음주" in t:
        return 14
    return 7
