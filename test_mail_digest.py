#!/usr/bin/env python3
"""메일 → 일정 자동 등록 검증. 실제 메일·Claude·캘린더 없이 로직만.

자동 등록은 되돌리기가 번거롭다. 잘못 넣으면 사장님이 캘린더를 손으로
치워야 하므로, 좁게 잡는 규칙들이 진짜 지켜지는지 여기서 고정한다.

  ① 로컬 선별 — 날짜가 없거나 자동발송이면 Claude 를 부르지도 않는다
  ② 중복 방지 — 같은 메일도, 같은 일정도 두 번 등록되지 않는다
  ③ 지난 일정 — 이미 지난 시각은 넣지 않는다
  ④ 시험 실행 — --dry-run 이 기록을 남기면 안 된다 (진짜 실행이 건너뛴다)
    python test_mail_digest.py
"""
import json
from datetime import datetime, timedelta

import config
import mail_digest as D
import mail_local

# 기본값은 꺼져 있다(2026-08-14, 사장님 지시 — 메일만 보고 뽑은 일정은
# 부정확하다). 등록 로직 자체는 여전히 맞아야 하므로 테스트에서는 켜 둔다.
D.CALENDAR_ENABLED = True

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


SOON = datetime.now() + timedelta(days=3)
PAST = datetime.now() - timedelta(days=3)


def mail(mid, sender, subject, body):
    return {"id": mid, "from": sender, "subject": subject, "body": body, "date": "오늘"}


print("[1] 로컬 선별 — Claude 를 부르기 전에 걸러낸다")
for m, want, why in [
    (mail("a", "김대리 <kim@p.co.kr>", "킥오프 미팅 안내",
          f"{SOON.month}월 {SOON.day}일 오후 3시에 진행합니다"), True, "날짜 있는 업무 메일"),
    (mail("b", "박과장 <p@c.com>", "자료 잘 받았습니다",
          "확인했습니다. 감사합니다."), False, "날짜가 없다 — 일정을 만들 수 없다"),
    (mail("c", "noreply@shop.com", "특가 세일",
          "오늘 오후 3시부터 특가!"), False, "자동발송"),
    (mail("d", "alerts@slack.com", "새 메시지",
          "8월 9일 오전 10시"), False, "슬랙 알림"),
    # 업무 어휘는 무한해서 낱말 목록으로는 못 따라잡는다.
    # 낱말이 아니라 '날짜가 있는가' 로 거르는 이유.
    (mail("e", "이사 <lee@x.kr>", "농업박람회 부스 시공 배치도",
          f"{SOON.month}월 {SOON.day}일 오전 9시 현장 확인"), True, "미팅·회의 같은 말이 없어도"),
]:
    check(f"{why}: {m['subject'][:22]!r}", D._looks_scheduley(m), want)

print("\n[2] 추출 결과 → 등록. 미팅과 업무를 갈라 붙인다")
CREATED = []
D_state = {}

import calendar_local  # noqa: E402

calendar_local.create = lambda title, start, hours=1.0: (  # type: ignore[assignment]
    CREATED.append((title, start)), "ok")[1]

FAKE_EVENTS = [
    {"title": "농특위 킥오프", "kind": "미팅",
     "start": SOON.strftime("%Y-%m-%dT15:00"), "hours": 1.0},
    {"title": "홍보 영상 납품", "kind": "업무",
     "start": SOON.strftime("%Y-%m-%dT10:00"), "hours": 1.0},
    # 지난 일정 — 넣으면 안 된다
    {"title": "지나간 회의", "kind": "미팅",
     "start": PAST.strftime("%Y-%m-%dT10:00"), "hours": 1.0},
    # 날짜를 못 읽은 것 — 추측하지 말고 버린다
    {"title": "언젠가 미팅", "kind": "미팅", "start": "미정", "hours": 1.0},
]

MAILS = [mail("m1", "김대리 <kim@p.co.kr>", "킥오프 미팅",
              f"{SOON.month}월 {SOON.day}일 오후 3시")]
mail_local.recent_messages = lambda hours=26, max_scan=40: MAILS  # type: ignore[assignment]

FAKE_JUNK: list[dict] = []
TRASHED: list[str] = []
D._extract = lambda msgs: (FAKE_EVENTS, FAKE_JUNK)  # type: ignore[assignment]
mail_local.trash = lambda ids: (TRASHED.extend(ids), len(ids))[1]  # type: ignore[assignment]

config.STATE.mkdir(parents=True, exist_ok=True)
D.STATE_FILE = config.STATE / "_test_mail_digest.json"
D.STATE_FILE.unlink(missing_ok=True)

summary = D.run()
titles = [t for t, _ in CREATED]
check("미팅은 [미팅] 으로", "[미팅] 농특위 킥오프" in titles, True)
check("업무는 [업무] 로", "[업무] 홍보 영상 납품" in titles, True)
check("지난 일정은 안 넣는다", any("지나간" in t for t in titles), False)
check("날짜를 못 읽으면 안 넣는다", any("언젠가" in t for t in titles), False)
check("등록 건수", len(CREATED), 2)

print("\n[3] 중복 방지 — 같은 메일, 같은 일정을 두 번 넣지 않는다")
CREATED.clear()
summary2 = D.run()
check("같은 메일은 다시 처리하지 않는다", CREATED, [])
check("  → 안내 문구", "새로 온 메일이 없습니다" in summary2, True)

# 메일이 새로 왔는데 일정이 같은 경우 (같은 미팅을 두 번 안내받는 일은 흔하다)
MAILS.append(mail("m2", "김대리 <kim@p.co.kr>", "킥오프 미팅 재안내",
                  f"{SOON.month}월 {SOON.day}일 오후 3시"))
CREATED.clear()
D.run()
check("새 메일이라도 같은 일정이면 안 넣는다", CREATED, [])

print("\n[4] 시험 실행은 기록을 남기지 않는다")
# 남기면 '본 메일' 로 찍혀서 진짜 실행이 그 메일을 건너뛴다 —
# 시험해본 날은 일정이 통째로 등록 안 되는 셈이다.
D.STATE_FILE.unlink(missing_ok=True)
MAILS[:] = [mail("m9", "김대리 <kim@p.co.kr>", "킥오프",
                 f"{SOON.month}월 {SOON.day}일 오후 3시")]
CREATED.clear()
D.run(dry_run=True)
check("실제 등록은 안 한다", CREATED, [])
check("기록 파일도 안 만든다", D.STATE_FILE.exists(), False)

# 시험 실행 뒤에도 진짜 실행이 정상 동작해야 한다
CREATED.clear()
D.run()
check("시험 뒤 진짜 실행이 등록한다", len(CREATED), 2)

D.STATE_FILE.unlink(missing_ok=True)

print("\n[5] 정크 버리기 — 고른 것만, 보호 낱말은 남긴다")
# 메일을 버리는 건 되돌리기가 번거롭다(휴지통에서 되찾아야 한다).
# 그래서 모델 판단을 그대로 믿지 않고 한 겹 더 거른다.
JUNK_MAILS = [
    mail("j1", "promo@shop.com", "여름 특가 50% 할인", "지금 구매하세요"),
    mail("j2", "news@media.kr", "오늘의 뉴스레터", "주요 소식입니다"),
    mail("j3", "세무법인 <tax@acc.kr>", "7월 세금계산서 발행 안내", "확인 부탁드립니다"),
    mail("j4", "홍길동 <ceo@client.kr>", "제안서 검토 회신", "잘 봤습니다"),
]
ids, rows = D._trashable(
    [{"id": "j1", "why": "광고"}, {"id": "j2", "why": "뉴스레터"},
     {"id": "j3", "why": "자동발송"}, {"id": "없는id", "why": "광고"}],
    JUNK_MAILS)
check("광고는 버린다", "j1" in ids, True)
check("뉴스레터도 버린다", "j2" in ids, True)
check("세금계산서는 보호 낱말이라 남긴다", "j3" in ids, False)
check("고르지 않은 메일은 손대지 않는다", "j4" in ids, False)
check("목록에 없는 아이디는 무시한다", len(ids), 2)
check("무엇을 버렸는지 기록에 남는다", rows[0]["subject"], "여름 특가 50% 할인")

# 상한 — 하루에 왕창 지우는 건 판단이 어긋난 것으로 본다
many = [mail(f"x{i}", "promo@shop.com", "광고", "광고") for i in range(30)]
ids_many, _ = D._trashable([{"id": f"x{i}", "why": "광고"} for i in range(30)], many)
check(f"하루 상한 {D.MAX_TRASH}통", len(ids_many), D.MAX_TRASH)

# run() 안에서도 실제로 휴지통까지 간다
D.STATE_FILE.unlink(missing_ok=True)
FAKE_EVENTS.clear()
FAKE_JUNK[:] = [{"id": "z1", "why": "광고"}]
TRASHED.clear()
MAILS[:] = [mail("z1", "promo@shop.com", "특가 안내", "할인 중")]
summary5 = D.run()
check("run 이 휴지통으로 옮긴다", TRASHED, ["z1"])
check("  → 안내 문구", "휴지통" in summary5, True)

# 시험 실행에서는 진짜로 버리지 않는다
D.STATE_FILE.unlink(missing_ok=True)
TRASHED.clear()
MAILS[:] = [mail("z2", "promo@shop.com", "특가 안내", "할인 중")]
FAKE_JUNK[:] = [{"id": "z2", "why": "광고"}]
D.run(dry_run=True)
check("시험 실행은 버리지 않는다", TRASHED, [])

D.STATE_FILE.unlink(missing_ok=True)

print("\n[6] 캘린더 등록 끄면 정크 정리는 하되 일정은 안 넣는다")
D.STATE_FILE.unlink(missing_ok=True)
D.CALENDAR_ENABLED = False
FAKE_EVENTS[:] = [{"title": "농특위 킥오프", "kind": "미팅",
                   "start": SOON.strftime("%Y-%m-%dT15:00"), "hours": 1.0}]
FAKE_JUNK[:] = []
CREATED.clear()
MAILS[:] = [mail("m10", "김대리 <kim@p.co.kr>", "킥오프 미팅",
                 f"{SOON.month}월 {SOON.day}일 오후 3시")]
D.run()
check("꺼져 있으면 등록하지 않는다", CREATED, [])
D.CALENDAR_ENABLED = True
D.STATE_FILE.unlink(missing_ok=True)

print("\n[7] 기록이 무한정 커지지 않는다")
st = {"seen": [str(i) for i in range(900)], "registered": [str(i) for i in range(900)]}
D._save_state(st)
saved = json.loads(D.STATE_FILE.read_text())
check("본 메일 상한", len(saved["seen"]), 500)
check("등록 일정 상한", len(saved["registered"]), 500)
D.STATE_FILE.unlink(missing_ok=True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    raise SystemExit(1)
print("✅ 전부 통과 — 자동 등록이 좁게, 중복 없이, 되돌릴 일 없이 돈다")
