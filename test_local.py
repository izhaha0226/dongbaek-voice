#!/usr/bin/env python3
"""로컬 처리 검증 — 특히 '쓰기가 위험 게이트를 우회하지 않는가'.

로컬 처리를 확대하면서 가장 위험한 실수는, 되돌릴 수 없는 작업이
음성 확인 없이 바로 실행되는 것이다. 그 경로를 집중적으로 막았는지 본다.
    python test_local.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import router

FAIL = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAIL.append(f"{label}: 기대={want!r} 실제={got!r}")
    print(f"  {'✓' if ok else '✗'} {label}")


print("\n[1] 조회는 로컬이 처리 (0 토큰)")
for q in ["안 읽은 메일 몇 통이야", "새 메일 있어?", "최근 메일 알려줘",
          "이번 주 일정 뭐야", "오늘 일정", "지금 몇 시야", "오늘 며칠이야"]:
    check(f"{q!r} → 로컬", router.handle_local(q) is not None, True)

print("\n[1b] 시각 질문은 오인식·재질문에도 견딘다 (반말 사건의 뿌리)")
for q, local, why in [
    ("10분 몇 시야", True, "whisper 가 '지금'을 '10분'으로 적은 실사례"),
    ("몇 시냐고", True, "짜증 섞인 재질문 — 게이트에 막혔던 실사례"),
    ("몇시", True, "최소형"),
    ("몇시에 만나기로 했지", False, "기억 질문 — Claude"),
    ("강남 미팅 몇 시야", False, "일정 질문 — 시계가 아니라 캘린더"),
]:
    got = router._is_time_query(router.normalize(q))
    check(f"{q!r} → {'시계' if local else '시계 아님'} ({why})", got, local)
check("'몇 시냐고' → 게이트 없음", router.danger_hit("몇 시냐고."), None)

print("\n[1c] 회상 낌새 — 이때만 장기 기억을 뒤진다")
for q, want in [
    ("지난번에 얘기한 업체 뭐였지", True),
    ("그때 그 캠페인 기억나", True),
    ("오늘 일정 알려줘", False),
    ("메일 보내줘", False),
]:
    check(f"{q!r} → 회상 {want}", router.wants_memory(router.normalize(q)), want)

print("\n[2f] 광고 이상 감지 — 로아스 반토막·매출 두 배만")
import ads_local as _ads  # noqa: E402
from datetime import date as _d2  # noqa: E402

_real_rows = _ads._rows
def _fake_rows(start, end, name):
    if start == end:                       # 그날 하루
        return [["(주)심플리케어", "20000", "20000", "1"],
                ["(주)미라클", "500", "0", "0"]]
    return [["(주)심플리케어", "140000", "560000", "7"]]
_ads._rows = _fake_rows
warns = _ads.anomalies(_d2.today())
_ads._rows = _real_rows
check("반토막 경보 1건", len(warns), 1)
check("경보 내용", "심플리케어" in warns[0] and "절반" in warns[0], True)

print("\n[2] 판단이 필요한 건 Claude 로 넘김")
for q in ["레일웨이 로그 확인해줘", "이 코드 리뷰해줘", "매출 분석해줘",
          "홍길동한테 뭐라고 답장할까"]:
    check(f"{q!r} → Claude", router.handle_local(q) is None, True)

print("\n[2b] 최근 커밋 조회 — 저장소를 직접 불렀을 때만 로컬")
for q, local, why in [
    ("동백 최근 커밋 알려줘", True, "자기 저장소"),
    ("광고플랫폼 최근 커밋 알려줘", True, "별칭 해석"),
    ("최근 커밋 알려줘", False, "저장소 이름 없음 — 문맥은 Claude 몫"),
    ("광고플랫폼에 커밋해줘", False, "조회가 아니라 행위"),
    ("정리해서 커밋 남겨줘", False, "행위형 — 로컬이 손대면 안 됨"),
]:
    got = router.handle_local(q) is not None
    check(f"{q!r} → {'로컬' if local else 'Claude'} ({why})", got, local)

print("\n[2c] 날씨 — 로컬 API (모형, 네트워크 없음)")
import weather_local  # noqa: E402

weather_local._fetch = lambda: {
    "current": {"temperature_2m": 27.3, "weather_code": 1},
    "daily": {"weather_code": [1, 61],
              "temperature_2m_max": [31.2, 24.0],
              "temperature_2m_min": [24.1, 19.5],
              "precipitation_probability_max": [10, 70]},
}
for q, local, why in [
    ("오늘 날씨 어때", True, "날씨 질문"),
    ("지금 몇 도야", True, "기온"),
    ("내일 비 와?", True, "내일 강수"),
    ("내일 미팅인데 우산 챙겨야 해?", False, "판단 질문 — Claude"),
]:
    got = router.handle_local(q) is not None
    check(f"{q!r} → {'로컬' if local else 'Claude'} ({why})", got, local)
check("오늘 답에 현재 기온", "27도" in router.handle_local("오늘 날씨 어때"), True)
check("내일 답에 비 확률", "70%" in router.handle_local("내일 날씨 어때"), True)
check("조사 처리 (서울는 ✗)", "서울은" in router.handle_local("내일 날씨 어때"), True)
check("낮은 확률(10%)은 안 읽음", "10%" in router.handle_local("오늘 날씨 어때"), False)
# 게이트: 날씨·브리핑은 승인 없이 통과하되, 행위어가 섞이면 얄짤없다.
check("'내일 비 와?' → 게이트 없음", router.danger_hit("내일 비 와?"), None)
check("'브리핑해줘' → 게이트 없음", router.danger_hit("브리핑해줘"), None)
check("⚠ '추워도 배포 진행해' → 게이트", router.danger_hit("추워도 배포 진행해") is not None, True)

print("\n[2d] 브리핑 — 로컬 조합 (모형)")
import calendar_local  # noqa: E402
import mail_local  # noqa: E402
from datetime import datetime as _dt  # noqa: E402

weather_local.today = lambda: "지금 서울 27도, 맑음."
calendar_local.events = lambda days=1: [
    {"title": "강남 미팅", "start": _dt(2026, 8, 11, 14, 0), "all_day": False, "location": ""},
]
mail_local.unread_count = lambda: "안 읽은 메일이 3통 있습니다."
import ads_local  # noqa: E402

_real_ads_speak = ads_local.speak
ads_local.speak = lambda text: None          # 광고 조각이 죽어도 브리핑은 나온다
b = router.handle_local("브리핑해줘")
ads_local.speak = _real_ads_speak
check("날씨 포함", "27도" in b, True)
# ⚠ 시각은 사람이 말하듯 읽는다 (2026-08-16 사장님 교정 "십이오팔이 아니고
#   열두시오십팔분"). 브리핑은 귀로 듣는 자리라 24시간 표기를 그대로 읽으면
#   '십사시' 가 된다 — 아무도 그렇게 말하지 않는다.
check("일정 포함", "오후 두시 강남 미팅" in b, True)
check("메일 포함", "3통" in b, True)
check("'문서 브리핑'은 Claude 로", router.handle_local("이 문서 브리핑해줘"), None)

print("\n[2e] 업체별 상세 성과 — '자세히 보고해'")
_real_detail = ads_local.detail
ads_local.detail = lambda s, e, l: f"{l} 업체별 상세."
for q, local, why in [
    ("자세히 보고해줘", True, "브리핑 후속 지시"),
    ("업체별 성과 알려줘", True, "명시 요청"),
    ("어제 업체별로 자세히 보고해", True, "기간 지정"),
    ("그 계약서 자세히 알려줘", False, "대상이 광고가 아님 — Claude"),
    ("코드 자세히 보고해줘", False, "코드 얘기 — Claude"),
]:
    got = router.handle_local(q) is not None
    check(f"{q!r} → {'로컬' if local else 'Claude'} ({why})", got, local)
check("'그 메일 자세히 알려줘' → 광고 상세 아님 (메일 로컬이 받음)",
      router._is_ads_detail(router.normalize("그 메일 자세히 알려줘")), False)
check("'자세히 보고해줘' → 게이트 없음", router.danger_hit("자세히 보고해줘"), None)
ads_local.detail = _real_detail

print("\n[3] ⚠ 쓰기는 승인 전(elevated=False)에 절대 실행되면 안 됨")
# ⚠ 일정은 등록·수정·삭제 전부 승인에서 빠졌다 (사장님 지시 2026-08-12):
#   "일정 등록, 수정하는데 무슨 승인이 필요해? 내가 등록하라 해서 등록하는
#    거고 수정하라 해서 수정하는 걸텐데." 삭제도 같이 빼기로 정하셨다.
#   여기 남는 건 캘린더 밖의, 되돌리기 어려운 것뿐이다.
WRITES = [
    "그 파일 삭제해",
    "환경 변수 삭제해",
]

# 등록형은 이제 승인 없이 통과해야 한다 (그래도 로컬이 함부로 만들진 않는다)
for q in ["내일 오후 3시에 마바공방 미팅 잡아줘", "8월 20일 2시 회의 등록해줘"]:
    check(f"{q!r} → 승인 없이 통과", router.danger_hit(q), None)
for q in WRITES:
    check(f"{q!r} → 로컬이 실행 안 함", router.handle_local(q, elevated=False), None)

print("\n[4] ⚠ 그 쓰기들은 위험 게이트에 반드시 걸려야 함")
for q in WRITES:
    hit = router.danger_hit(q)
    check(f"{q!r} → 게이트 감지", hit is not None, True)

print("\n[5] 승인 후에도 애매하면 Claude 로 (엉뚱한 날짜 방지)")
for q, why in [
    ("일정 하나 잡아줘", "날짜·시각 없음"),
    ("내일 미팅 잡아줘", "시각 없음"),
    ("3시에 미팅 잡아줘", "날짜 없음"),
    ("언제 시간 될 때 미팅 잡자", "전부 불명확"),
]:
    check(f"{q!r} ({why}) → Claude", router.handle_local(q, elevated=True), None)

print("\n[5b] ⚠ 삭제 대상이 불명확하면 로컬이 손대지 않음")
for q, why in [
    ("내일 미팅 취소해줘", "제목이 '일정'으로만 추출됨 → 엉뚱한 걸 지울 위험"),
    ("일정 삭제해", "대상 없음"),
    ("회의 취소해", "대상 없음"),
]:
    check(f"{q!r} ({why[:22]}…) → Claude", router.handle_local(q, elevated=True), None)

print("\n[6] 메일 발송은 로컬이 건드리지 않음 (게이트+MCP 경로 유지)")
for q in ["홍길동한테 메일 보내줘", "메일 발송해", "이 메일에 답장해줘"]:
    check(f"{q!r} → 로컬 미처리", router.handle_local(q, elevated=False), None)
    check(f"{q!r} → 게이트 감지", router.danger_hit(q) is not None, True)

print("\n[볼륨 — 되돌리기 어려운 쪽일수록 좁게]")
# ⚠ 2026-08-14 18:04 실사고. 사장님이 옆에서 사적인 대화를 하시는 중에
#   "혹시 빨간색 이미 꺼져 있어? 뭔 소리야…" 가 명령으로 잡혔고,
#   '소리' + '꺼' 두 낱말이 맞아떨어져 음소거가 걸렸다. MERGE 가 같은
#   판정을 네 번 반복했고, 그대로 13시간 소리가 꺼져 있었다.
#   사장님은 당신이 끈 줄도 모르셨고, 동백은 불러도 대답이 안 들렸다.
#   소리를 끄면 동백이 "껐습니다" 라고 알릴 방법조차 사라진다.
import router as _r
_calls = []
_real_osa, _r._osa = _r._osa, lambda s: _calls.append(s)
for said, want in [("소리 좀 키워줘", True), ("볼륨 올려", True), ("소리 줄여", True),
                   ("음소거해줘", True), ("무음으로", True),
                   # 사고 문장 그대로
                   ("혹시 빨간색 이미 꺼져 있어? 뭔 소리야. 근데 왜 그렇게 말 쉽게 해?", False),
                   ("불 꺼", False),
                   ("소리가 안 들리는데 뭐가 문제야", False),
                   ("그 소리 듣고 깜짝 놀랐잖아", False)]:
    _calls.clear()
    _r.handle_local(_r.normalize(said))
    check(f"{'실행' if want else '무시'}: {said[:26]!r}", bool(_calls), want)
_r._osa = _real_osa

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    for f in FAIL:
        print("  " + f)
    raise SystemExit(1)
print("✅ 전부 통과 — 로컬 확대가 안전 게이트를 우회하지 않음")

# ── 광고 질문에 숫자가 실리는가 ────────────────────────────
# 2026-08-12 22:03·22:07, 사장님이 "광고플랫폼 광고성과 분석해서 보고해"
# 를 두 번 하셨는데 둘 다 "광고 데이터에 접근할 도구가 없습니다" 로 끝났다.
# '분석해' 가 조언 요청으로 분류돼 클로드로 갔는데 클로드에는 DB 도구가
# 없었다 — 데이터는 동백에게 있고 분석력은 클로드에게 있는데 안 만났다.
print("\n[9] 광고 질문에는 숫자를 실어 클로드로 보낸다")
for said in [
    "광고플랫폼 광고성과 분석해서 보고해",
    "광고 왜 이렇게 안 나와",
    "로아스 어떻게 올려",
    "광고주 성과 브리핑해봐",
]:
    check(f"{said!r} → 숫자 첨부",
          router.wants_ads_context(router.normalize(said)), True)

# ⚠ 광고와 무관한 말에 붙이면 토큰만 늘고 답이 흐려진다
for said in ["오늘 일정 알려줘", "고마워", "매출 얘기 좀 하자"]:
    check(f"{said!r} → 첨부 안 함",
          router.wants_ads_context(router.normalize(said)), False)

# 데이터가 실제로 뽑히는지 (기간을 안 말해도 어제로)
import ads_local as _al
from datetime import date as _dt, timedelta as _td
_y = _dt.today() - _td(days=1)
check("기간을 안 말해도 어제 데이터가 나온다",
      bool(_al.analysis(_y, _y, "어제")), True)

# ── 일정 조회에서 동사를 검색어로 쓰지 않는다 ──────────────
# 2026-08-12 22:31 실측:
#   "내일 일정에 대해서 브래핑해봐" → "브래핑해봐 관련 일정은 찾지 못했습니다"
#   "관련 일정을 왜 못 찾지?"      → "관련 관련 일정은 찾지 못했습니다"
# schedule_keyword 가 '필러에 없는 가장 긴 낱말' 을 검색어로 삼는데,
# whisper 오기('브리핑'→'브래핑')는 필러 목록으로 못 쫓는다. 꼴로 걸러야 한다.
print("\n[10] 일정 조회 — 동사·기간은 검색어가 아니다")
for said in [
    "내일 일정에 대해서 브래핑해봐",
    "관련 일정을 왜 못 찾지",
    "오늘 일정 정리해줘",
    "이번주 일정 알려줘",
    "다음주 일정 뭐 있어",
]:
    check(f"{said!r} → 검색어 없음", router.schedule_keyword(said), None)

# ⚠ 진짜 이름은 살아야 한다. 이걸 놓치면 "강남 미팅 언제야" 에 주간
#   브리핑이 통째로 돌아온다 — 그것 때문에 이 기능이 생겼다.
for said, want in [("강남 미팅 언제야", "강남"),
                   ("본사 미팅 몇시야", "본사"),
                   ("마바공방 일정 알려줘", "마바공방")]:
    check(f"{said!r} → {want!r}", router.schedule_keyword(said), want)

print("\n[10b] 오늘·이름 조회는 이미 지난 일정도 본다")
# "오늘 일정 알려줘" 를 저녁에 물으셨는데 "잡힌 일정이 없습니다" 가 나가면
# 거짓말처럼 들린다 — 오늘 세 건이 있었는데 다 지났을 뿐이다.
# 반면 주간 브리핑은 앞만 본다. 지난 것까지 읽으면 지금 뭘 해야 하는지가 묻힌다.
_src = open("calendar_local.py").read()
check("이름·하루 조회면 지난 것도 본다",
      "include_past_today=bool(keyword) or only_date is not None" in _src, True)
