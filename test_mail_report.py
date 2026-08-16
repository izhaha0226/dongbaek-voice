#!/usr/bin/env python3
"""메일 보고 검증 — 업체별로 갈라 묶고, 소리로는 짧게.

사장님 지시 (2026-08-14): "받은 메일은 업체별로 분류해서 정리후 나한테
보고해 … 아무개이 메일을 몇통 보냈다 … 판단해보니 cc(참조)로 들어온거고
실질적인 답변을 요하는건 아니다 … 김대리에게서 온 메일은 포워딩 된것 같다."

판단 자체는 클로드가 하므로 여기서 고정할 수 없다. 대신 판단이 서려면
반드시 맞아야 하는 것들을 고정한다.
  ① 계정이 업체 이름으로 불리는가
  ② 같은 사람이 여러 통 보냈으면 한 묶음이 되는가 (통수가 보여야 판단이 된다)
  ③ 소리로 읽는 말이 짧은가 (34통을 귀로 듣는 건 쓸모가 없다)
    python test_mail_report.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import sys

import config
import mail_report
import router

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


print("[1] 계정을 업체 이름으로 부른다")
# ⚠ 매핑은 이 파일 안에서 정한다. 실제 값은 개인 설정이라 공개판에서는
#   비어 있는데, config 를 그대로 읽으면 이 검사가 거기서만 죽는다.
#   (공개판으로 옮겨 돌려보고서야 알았다 — 본체에서만 도는 검사였다.)
config.MAIL_ACCOUNT_LABELS = {
    "partner.example.com": "협력사",
    "office@example.com": "회사",
    "MyCorp": "본사",
    "me@example.com": "개인",
}
# 주소가 아니라 Mail.app 의 계정 '이름' 으로 맞춘다 — 사람이 붙인 이름이라
# 'MyCorp' 처럼 주소와 다를 수 있어 부분 일치로 본다.
for acct, want in [("partner.example.com", "협력사"),
                   ("office@example.com", "회사"),
                   ("MyCorp", "본사"),
                   ("me@example.com", "개인")]:
    check(f"{acct} → {want}", mail_report.label_of(acct), want)
check("모르는 계정은 기타로", mail_report.label_of("who@nowhere.io"),
      config.MAIL_LABEL_FALLBACK)

print("\n[2] 같은 사람이 보낸 것은 한 묶음 — 통수가 보여야 판단이 된다")
msgs = [
    {"account": "partner.example.com", "sender": "아무개 <a@example.com>",
     "subject": "곤충의 날 포스터", "body": "확인 부탁드립니다", "date": "8월 13일"},
    {"account": "partner.example.com", "sender": "아무개 <a@example.com>",
     "subject": "곤충의 날 초청장", "body": "시안 보냅니다", "date": "8월 13일"},
    {"account": "partner.example.com", "sender": "김대리 <b@example.com>",
     "subject": "FW: 산출내역서 작성 요청", "body": "", "date": "8월 13일"},
    {"account": "MyCorp", "sender": "Instagram <no-reply@mail.instagram.com>",
     "subject": "새 스토리", "body": "", "date": "8월 13일"},
]
g = mail_report.group(msgs)
check("업체가 둘", sorted(g.keys()), ["본사", "협력사"])
check("협력사에 보낸 사람 둘", sorted(g["협력사"].keys()), ["김대리", "아무개"])
check("아무개는 2통으로 묶임", len(g["협력사"]["아무개"]), 2)
check("이름만 뽑는다(주소 제거)", "아무개" in g["협력사"], True)

print("\n[3] 소리로 읽는 말은 짧다")
# ⚠ 통화 정리에서 이미 겪었다 — 482자를 읽어 "그거 언제 다 읽어?" 를 들었다.
analysis = ("## 협력사\n### 아무개 (2통)\n포스터 확인 요청.\n"
            "**판단**: 답 요함.\n\n## 오늘 손댈 것\n- 김대리 산출내역서 작성\n- 이과장 회신")
said = mail_report._spoken(analysis, 34, g)
check("상한을 지킨다", len(said) <= config.MAIL_REPORT_SPOKEN_MAX, True)
check("통수를 말한다", "34통" in said, True)
check("업체별 개수를 말한다", "협력사" in said, True)
check("손댈 것 첫 줄만 덧붙인다", "김대리" in said and "이과장" not in said, True)
check("줄바꿈이 안 섞인다", "\n" not in said, True)

print("\n[4] 분석이 실패해도 보고는 나간다")
# 클로드가 죽어도 목록은 위키에 남아야 한다 — 아무것도 안 나가는 게 최악이다.
said2 = mail_report._spoken("", 5, g)
check("분석 없이도 말이 된다", len(said2) > 0 and "5통" in said2, True)

print("\n[5] 부르는 말 — 조회와 갈린다")
# '안 읽은 메일 몇 통' 은 이미 로컬이 0초에 답한다. 여기는 본문까지 읽고
# 클로드가 판단하는 무거운 작업이라 명시적으로 청할 때만 돈다.
for s in ("메일 정리해줘", "받은 메일 보고해줘", "메일 업체별로 정리해줘"):
    check(f"보고다: {s!r}", router.is_mail_report(router.normalize(s)), True)
for s in ("안 읽은 메일 몇 통이야", "메일 보내줘", "오늘 일정 알려줘"):
    check(f"보고 아니다: {s!r}", router.is_mail_report(router.normalize(s)), False)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과 — 업체별로 갈라 묶고, 소리로는 짧게")
