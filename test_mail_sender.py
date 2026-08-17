#!/usr/bin/env python3
"""보낸이 낭독 검증 — 메일 주소가 소리로 나가지 않는다.

2026-08-16 12:56 실측: "최근 메일 3건" 답의 첫 건이
"noreply-apps-scripts-notifications@google.com님의" 로 나갔다.
Mail.app 도 네트워크도 안 쓴다. 조합 논리만 본다.
    python test_mail_sender.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import sys

import mail_local

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


def truthy(name, got):
    check(name, bool(got), True)


print("\n[1] 표시 이름이 있으면 그대로 — 예전 동작 그대로다")
check("이름만", mail_local.spoken_sender("이영희"), "이영희")
check("이름 <주소>", mail_local.spoken_sender('"이영희" <choi@example.com>'),
      "이영희")
check("알림도 이름이 있으면 그 이름",
      mail_local.spoken_sender("KB국민카드 <noreply@kbcard.com>"), "KB국민카드")

print("\n[2] 이름 없이 주소만 온 발신 — 주소를 읽지 않는다")
for addr in ("noreply-apps-scripts-notifications@google.com",
             "noreply@kakaopaycorp.com",
             "google.account.support.B@abc-amega.com"):
    said = mail_local.spoken_sender(addr)
    check(f"주소가 안 읽힘: {addr[:24]}", "@" in said, False)
    truthy(f"그래도 누군지는 남음: {addr[:24]}", len(said) >= 2)
check("알림은 서비스 이름으로",
      mail_local.spoken_sender("noreply-apps-scripts-notifications@google.com"),
      "Google")
check("모르는 회사는 도메인으로",
      mail_local.spoken_sender("google.account.support.B@abc-amega.com"),
      "Abc-amega")

print("\n[3] 개인 메일은 도메인이 아니라 아이디가 사람이다")
check("지메일", mail_local.spoken_sender("hong1234@gmail.com"), "hong1234")
check("네이버", mail_local.spoken_sender("kim.sangmu@naver.com"), "kim.sangmu")

print("\n[4] 낭독문 전체 — recent() 에 주소가 섞이지 않는다")
mail_local._osa = lambda *a, **k: (
    "noreply-apps-scripts-notifications@google.com ▸ Summary of failures||"
    '"이영희" <choi@example.com> ▸ 행사 행사 추가안||')
said = mail_local.recent()
check("주소가 안 읽힘", "@" in said, False)
truthy("사람 이름은 그대로", "이영희님의" in said)
truthy("알림도 누군지 말한다", "Google님의" in said)
truthy("제목은 살아 있다", "행사" in said)

print("\n[5] 알림 묶기(브리핑)는 그대로 — 넓힌 게 아니라 부르는 법만 바꿨다")
check("표시 이름 알림", mail_local.notice_service("LinkedIn", "x@linkedin.com"),
      "LinkedIn")
check("사람은 여전히 사람",
      mail_local.notice_service("이영희", "choi@example.com"), None)
check("개인 도메인이 알림이 되지 않는다",
      mail_local.notice_service("", "hong1234@gmail.com"), None)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과 — 보낸이는 이름으로, 주소는 소리에서 뺀다")
