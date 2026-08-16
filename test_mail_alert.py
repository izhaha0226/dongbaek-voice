#!/usr/bin/env python3
"""메일 알림 — 2026-08-16 지시("메일 들어오면 알려줘")의 시험.

그때 동백은 "도구가 없어서 안 된다" 고 답했다. 실제로는 알림 코드가 있었고
VIP 목록이 비어 있어 한 번도 돌지 않았을 뿐이다. 없는 게 아니라 꺼져
있었고, 말로 켤 길도 없어서 클로드가 제 도구 목록만 보고 없다고 답했다.

여기서 지키는 것 셋.
  1. 말로 켜고 끈다 — 그리고 '조회' 와 갈린다
     ("메일 왔어?" 는 지금 확인해 달라는 말이지 설정이 아니다)
  2. 기본은 사람이 보낸 것만 — 하루 40통을 다 읽으면 알림이 아니라 소음이다
  3. VIP 는 방식과 무관하게 알린다 (끔일 때만 예외)

    python tests/test_mail_alert.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config
import mail_alert
import router

FAIL = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))


print("[1] 말로 켜고 끈다")
for t, want in [("메일 오면 알려줘", "사람"),
                ("메일 들어오면 알려줘", "사람"),
                ("새 메일 알림 켜줘", "사람"),
                ("메일 알림 전부로 해줘", "전부"),
                ("메일 알림 중요한 사람만", "vip"),
                ("메일 알림 꺼", "끔"),
                ("메일 알림 그만", "끔")]:
    check(f"{t!r} → {want}", router.mail_alert_intent(t), want)

print("\n[2] 조회는 설정이 아니다")
# ⚠ 이걸 설정으로 읽으면, 메일 몇 통이냐고 물으셨는데 알림만 켜고 끝난다.
for t in ("메일 왔어?", "안 읽은 메일 몇 통이야", "메일 정리해줘",
          "오늘 온 메일 읽어줘"):
    check(f"{t!r} 는 설정 아님", router.mail_alert_intent(t), None)

print("\n[3] 무엇을 알릴지 — 기본은 사람이 보낸 것만")
_real = mail_alert._FILE
mail_alert._FILE = ROOT / "state" / "mail_alert_test.json"
_vip = config.MAIL_NUDGE_VIP
try:
    config.MAIL_NUDGE_VIP = ["김철수"]

    mail_alert.set_mode(mail_alert.PERSON)
    check("사람이 보낸 메일은 알린다",
          mail_alert.should_alert("김부장", "kim@company.co.kr", "견적서"), True)
    check("서비스 알림은 조용히",
          mail_alert.should_alert("Google", "no-reply@accounts.google.com",
                                  "보안 알림"), False)
    check("VIP 는 방식과 무관하게 알린다",
          mail_alert.should_alert("김철수", "hong@x.com", "연락 주세요"), True)

    mail_alert.set_mode(mail_alert.VIP)
    check("vip 방식에선 보통 사람은 조용히",
          mail_alert.should_alert("김부장", "kim@company.co.kr", "견적서"), False)
    check("vip 방식에서도 VIP 는 알린다",
          mail_alert.should_alert("김철수", "hong@x.com", "연락"), True)

    mail_alert.set_mode(mail_alert.ALL)
    check("전부 방식은 서비스도 알린다",
          mail_alert.should_alert("Google", "no-reply@accounts.google.com",
                                  "보안 알림"), True)

    mail_alert.set_mode(mail_alert.OFF)
    check("끔이면 VIP 도 조용히",
          mail_alert.should_alert("김철수", "hong@x.com", "연락"), False)

    print("\n[4] 설정은 파일에 남는다")
    mail_alert.set_mode(mail_alert.PERSON)
    check("다시 읽어도 그대로", mail_alert.mode(), "사람")
    mail_alert._FILE.unlink()
    check("파일이 없으면 여태 돌던 대로(vip)", mail_alert.mode(), "vip")
finally:
    config.MAIL_NUDGE_VIP = _vip
    try:
        mail_alert._FILE.unlink()
    except OSError:
        pass
    mail_alert._FILE = _real

print("\n[5] 기다리는 발신인 — '강남 한빛건설에서 메일 오면 알려줘'")
# 실사례 2026-08-16 12:58. "조만간 30분 이내 올 거니까 오는 대로 바로 알림 줘"
# 하셨는데 동백은 "혼자 깨어나서 체크할 도구가 없다" 고 답했다. 능동 루프는
# 이미 5분마다 깨어나고 있었다 — 없던 건 도구가 아니라 무엇을 기다리는지
# 적어 두는 자리였다.
check("발신인을 뽑는다",
      router.mail_watch_target("강남 한빛건설에서 메일 오면 알려줘"), "강남 한빛건설")
check("호출어가 붙어도 뽑는다",
      router.mail_watch_target("동백아 강남 한빛건설 메일 오면 알려줘"), "강남 한빛건설")
check("발신인이 없으면 None",
      router.mail_watch_target("메일 오면 알려줘"), None)
check("'오늘'·'새' 는 발신인이 아니다",
      router.mail_watch_target("오늘 메일 오면 알려줘"), None)

_real2 = mail_alert._FILE
mail_alert._FILE = ROOT / "state" / "mail_alert_test2.json"
try:
    mail_alert.set_mode(mail_alert.VIP)      # 좁은 방식이어도
    mail_alert.watch_add("강남 한빛건설")
    check("기다리는 발신인은 방식과 무관하게 알린다",
          mail_alert.should_alert("강남 한빛건설 분양팀", "sales@debiang.co.kr",
                                  "계약 안내"), True)
    check("띄어쓰기가 달라도 걸린다",
          mail_alert.should_alert("강남한빛건설", "x@y.com", "안내"), True)
    check("제목에 있어도 걸린다",
          mail_alert.should_alert("아무개", "a@b.com", "강남 한빛건설 관련 회신"), True)
    check("상관없는 메일은 그대로 조용히",
          mail_alert.should_alert("김부장", "kim@co.kr", "주간보고"), False)
    # ⚠ 말씀하신 차례와 발신자가 적은 차례는 다르다. 2026-08-16 실사례 —
    #   "강남 한빛건설에서 메일 오면 알려줘" 하셨는데 발신자는 "한빛건설강남"
    #   였다. 통짜로 견주니 안 걸렸고 기다리시던 메일이 그냥 지나갔다.
    check("차례가 바뀌어도 걸린다",
          mail_alert.should_alert("한빛건설강남", "hanbit-gangnam@naver.com",
                                  "한빛건설강남_광고 운영 정책 회의 자료"), True)
    check("낱말 하나만 겹치면 안 걸린다",
          mail_alert.should_alert("강남구청", "x@y.com", "주민 안내"), False)

    mail_alert.watch_clear()
    check("지우면 비워진다", mail_alert.watch_list(), [])

    print("\n[5-1] 재기동해도 기억한다")
    # ⚠ 예전에는 뜨자마자 첫 판을 통째로 '본 것' 으로 치고 넘겼다. 그 대가로
    #   재기동 직전에 온 메일은 영영 안 알려졌다 — 2026-08-16 에 배포하느라
    #   네 번 재기동했고 기다리시던 메일이 그 틈으로 빠졌다.
    check("처음엔 표가 없다 (첫 판은 안 알림)", mail_alert.seen_known(), False)
    mail_alert.seen_save({"보낸이|제목"})
    check("저장하면 표가 생긴다", mail_alert.seen_known(), True)
    check("다시 읽어도 그대로", mail_alert.seen_load(), {"보낸이|제목"})
    mail_alert.seen_save({f"{i}|x" for i in range(400)})
    check("표는 300개까지만 들고 있는다", len(mail_alert.seen_load()), 300)
finally:
    try:
        mail_alert._FILE.unlink()
    except OSError:
        pass
    mail_alert._FILE = _real2

print("\n[6] 읽어 줄 말")
said = mail_alert.line("홍길동 <hong@co.kr>", "내일 미팅 시간 조정 부탁드립니다")
check("보낸 사람을 말한다", "홍길동" in said, True)
check("주소는 읽지 않는다", "@" in said, False)
check("제목을 말한다", "미팅 시간 조정" in said, True)

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — 사람이 보낸 메일만 알리고, 말로 끄고 켠다")
