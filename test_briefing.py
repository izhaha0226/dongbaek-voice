#!/usr/bin/env python3
"""세 번 브리핑 검증 — 네트워크·DB·메일 없이 조합 논리만.

핵심: 조각 하나가 죽어도 브리핑은 나온다, 큐웬이 죽어도 목록으로 읽는다,
퇴근 브리핑은 위키(업무일지)를 쓴다.
    python test_briefing.py
"""
import sys
import tempfile
from datetime import date
from pathlib import Path

import briefing

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


def truthy(name, got):
    check(name, bool(got), True)


# 수집기는 전부 모형 — 실제 git·메일·DB 를 건드리지 않는다
briefing._git_today = lambda: ["myshop-site 커밋 3건 (최근: 히어로 수정)"]
briefing._dongbaek_done = lambda since: ["동백 처리: 김상무 답장 초안"]
briefing._meetings_today = lambda: ["미팅: 14시 강남"]
_real_mail_bullets = briefing._mail_bullets      # [7] 에서 진짜를 부른다
briefing._mail_bullets = lambda r, s: ["보낸 메일 2통 — 견적서→kim, 제안서→lee"]
briefing._ads_analysis = lambda d, l: f"{l} 전체 광고비 20만원, 전환매출 100만원."

import gatekeeper  # noqa: E402

gatekeeper.summarize = lambda title, bullets, timeout=25.0: "오전에 커밋 셋과 답장 초안을 처리했습니다."

print("\n[1] 시각 → 모드")
check("8시=아침", briefing._mode_by_hour(8), "morning")
check("13시=점심", briefing._mode_by_hour(13), "lunch")
check("18시=퇴근", briefing._mode_by_hour(18), "evening")

print("\n[2] 점심 — 오전 업무·메일 정리")
spoken, full = briefing.lunch()
truthy("요약이 소리에 들어감", "답장 초안" in spoken)
truthy("전문에 목록 포함", "견적서" in full)

print("\n[3] 큐웬이 죽어도 목록으로 읽는다 (fail-open)")
gatekeeper.summarize = lambda title, bullets, timeout=25.0: None
spoken, _ = briefing.lunch()
truthy("목록 문장으로 대체", "커밋 3건" in spoken)
gatekeeper.summarize = lambda title, bullets, timeout=25.0: "요약."

print("\n[4] 퇴근 — 위키 업무일지 저장")
tmp = Path(tempfile.mkdtemp())
briefing.WIKI_DIR = tmp
import ads_local  # noqa: E402

_real_detail = ads_local.detail
ads_local.detail = lambda s, e, l: f"{l} 업체별: 심플리케어 로아스 500."
spoken, full = briefing.evening()
ads_local.detail = _real_detail
journal = tmp / f"{date.today()}.md"
truthy("일지 파일 생성", journal.exists())
body = journal.read_text(encoding="utf-8")
truthy("요약 절", "## 요약" in body)
truthy("한 일 절", "커밋 3건" in body)
truthy("메일 절", "견적서" in body)
truthy("성과 절", "전체 광고비" in body)
truthy("업체별 상세 절", "업체별" in body)
truthy("소리에 저장 언급", "위키에 저장" in spoken)
truthy("전문에 일지 경로", str(journal) in full)

print("\n[5] 아침 — 조각이 다 죽어도 인사는 한다")
import weather_local  # noqa: E402
import calendar_local  # noqa: E402
import mail_local  # noqa: E402

weather_local.today = lambda: None
calendar_local.events = lambda days=1, calendar_name=None, include_past_today=False: None
mail_local.unread_count = lambda: None
briefing._ads_analysis = lambda d, l: ""
spoken, _ = briefing.morning()
truthy("빈 브리핑도 문장", spoken.startswith("좋은 아침입니다."))

print("\n[6] 밤사이 자가 개선 — 아침 브리핑이 노트를 읽는다")
import json  # noqa: E402

note_file = tmp / "note.json"
briefing.NOTE_FILE = note_file

note_file.write_text(json.dumps({
    "date": date.today().isoformat(), "ok": True, "changed": True,
    "hypothesis": "커밋 조회를 로컬로 돌리면 6초가 준다",
    "result": "커밋 조회 로컬화 완료",
}, ensure_ascii=False))
spoken, _ = briefing.morning()
truthy("가설이 아침에 보고됨", "밤사이 자가 개선" in spoken and "가설" in spoken)

note_file.write_text(json.dumps({
    "date": "2000-01-01", "ok": True, "changed": True, "result": "옛날 것",
}, ensure_ascii=False))
spoken, _ = briefing.morning()
truthy("날짜 지난 노트는 침묵", "밤사이" not in spoken)

note_file.write_text(json.dumps({
    "date": date.today().isoformat(), "ok": False, "changed": False,
    "result": "테스트 실패 — 되돌림",
}, ensure_ascii=False))
spoken, _ = briefing.morning()
truthy("롤백은 보고됨", "되돌렸습니다" in spoken)

note_file.write_text(json.dumps({
    "date": date.today().isoformat(), "ok": True, "changed": False,
    "result": "변경 없음: 확신 있는 아이디어 없음",
}, ensure_ascii=False))
spoken, _ = briefing.morning()
truthy("변경 없는 밤은 침묵", "밤사이" not in spoken)

print("\n[7] 알림성 메일은 묶어 세고, 사람 메일만 제목을 읽는다")
# 2026-08-13 아침 사고: "Keesei Lee님 등 여러 사람들이 Li(LinkedIn)"
mail_local.received_brief = lambda hours=6, max_scan=30: [
    {"from": "LinkedIn", "addr": "notifications-noreply@linkedin.com",
     "subject": "Keesei Lee님 등 여러 사람들이 회원님의 활동에 반응했습니다"},
    {"from": "Instagram", "addr": "no-reply@mail.instagram.com",
     "subject": "새로운 로그인 알림"},
    {"from": "김상무", "addr": "kim@dongbaek.ai", "subject": "견적서 회신드립니다"},
]
mail_local.sent_recent = lambda hours=12, max_scan=30: []
line = " ".join(_real_mail_bullets(6, 6))
truthy("사람 메일만 제목을 읽는다", "견적서 회신드립니다" in line)
truthy("알림 제목은 안 읽는다", "Keesei" not in line)
truthy("알림은 서비스명으로 묶어 센다", "알림 2통(LinkedIn, Instagram)" in line)
check("사람 메일 수만 센다", "받은 메일 1통" in line, True)

# 개인 도메인이 알림으로 묻히면 안 된다 — 놓치는 것보다 나쁘다
check("네이버 개인 메일은 사람", mail_local.notice_service("김철수", "hong@naver.com"), None)
check("카카오 개인 메일은 사람", mail_local.notice_service("이부장", "lee@kakao.com"), None)
check("링크드인은 알림", mail_local.notice_service("LinkedIn", "x@linkedin.com"), "LinkedIn")
check("모르는 서비스도 noreply 면 알림",
      mail_local.notice_service("당근마켓", "noreply@daangn.com"), "당근마켓")
check("이름 없는 noreply 는 도메인으로",
      mail_local.notice_service("", "no-reply@accounts.google.com"), "Google")
check("표시 이름과 주소가 같은 서비스를 둘로 세지 않는다",
      mail_local.notice_service("Google", "noreply-accounts@google.com"),
      mail_local.notice_service("noreply-x@google.com", "noreply-x@google.com"))

mail_local.received_brief = lambda hours=6, max_scan=30: [
    {"from": "LinkedIn", "addr": "noreply@linkedin.com", "subject": "알림"},
]
line = " ".join(_real_mail_bullets(6, 6))
check("전부 알림인 날엔 '받은 메일 0통' 을 안 만든다", "받은 메일" in line, False)
truthy("그래도 알림 개수는 남는다", "알림 1통" in line)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과 — 세 번 브리핑, 로컬+큐웬으로")
