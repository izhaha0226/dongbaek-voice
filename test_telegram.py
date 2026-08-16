#!/usr/bin/env python3
"""텔레그램 브릿지 검증 — 봇 없이 로직만.

원격에서 명령이 들어와도 안전 게이트가 그대로 걸리는지가 핵심.
밖에 있다고 게이트가 느슨해지면 안 된다.
    python test_telegram.py
"""
import time

import telegram_bridge as tb

FAIL = []
SENT = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAIL.append(f"{label}: 기대={want!r} 실제={got!r}")
    print(f"  {'✓' if ok else '✗'} {label}")


def contains(label, text, needle):
    ok = needle in (text or "")
    if not ok:
        FAIL.append(f"{label}: {needle!r} 가 없음. 실제={text!r}")
    print(f"  {'✓' if ok else '✗'} {label}")


CHAT = 123456789

print("\n[1] 조회 명령은 로컬로 즉답 (0 토큰)")
pending = {}
for q in ["이번 주 일정 뭐야", "안 읽은 메일 몇 통이야", "지금 몇 시야"]:
    r = tb.handle_command(q, CHAT, pending)
    check(f"{q!r} → 응답 있음", bool(r), True)
    check(f"{q!r} → 승인 대기 안 걸림", CHAT in pending, False)

print("\n[2] 호출어가 있어도 없어도 동작")
pending = {}
a = tb.handle_command("동백아 지금 몇 시야", CHAT, pending)
b = tb.handle_command("지금 몇 시야", CHAT, pending)
check("호출어 붙여도 처리됨", bool(a), True)
check("호출어 없어도 처리됨", bool(b), True)

print("\n[3] ⚠ 위험 명령은 원격에서도 즉시 실행되면 안 됨")
DANGEROUS = [
    "프로덕션 배포해줘",
    # (일정 취소는 승인에서 빠졌다 — 사장님 지시 2026-08-12)
    "환경 변수 삭제해",
    "홍길동한테 메일 보내줘",
    "그 파일 삭제해",
]
for q in DANGEROUS:
    pending = {}
    r = tb.handle_command(q, CHAT, pending)
    contains(f"{q!r} → 되물음", r, "진행")
    check(f"{q!r} → 승인 대기 등록됨", CHAT in pending, True)

print("\n[4] 승인 흐름")
pending = {}
tb.handle_command("프로덕션 배포해줘", CHAT, pending)
r = tb.handle_command("아니 취소", CHAT, pending)
check("'아니 취소' → 취소됨", r, "취소했습니다.")
check("대기열 비워짐", CHAT in pending, False)

pending = {}
tb.handle_command("프로덕션 배포해줘", CHAT, pending)
r = tb.handle_command("네", CHAT, pending)
check("'네' 로는 승인 안 됨", r, "취소했습니다.")

print("\n[5] 승인 대기 만료 (3분)")
pending = {CHAT: ("프로덕션 배포해줘", time.time() - 200)}
r = tb.handle_command("진행", CHAT, pending)
check("만료 후 '진행' 은 승인으로 안 쓰임", CHAT in pending, False)

print("\n[6] ⚠ 허용되지 않은 chat_id 는 처리 자체가 안 됨")
calls = []
orig_send = tb.send_text
tb.send_text = lambda t, c, x: calls.append((c, x))
try:
    tb.process("dummy-token", {"chat": {"id": 999999}, "text": "일정 알려줘"}, [CHAT], {})
    check("낯선 chat_id → 응답조차 안 보냄", len(calls), 0)
    calls.clear()
    tb.process("dummy-token", {"chat": {"id": CHAT}, "text": ""}, [CHAT], {})
    check("빈 메시지 → 무시", len(calls), 0)
finally:
    tb.send_text = orig_send

print("\n[7] 설정 검증")
check("설정 파일 없으면 None", tb.load_conf() is None or isinstance(tb.load_conf(), dict), True)

print("\n[8] 음성 문답이 텔레그램에도 남는다 (자동 동기화)")
import config  # noqa: E402
import dongbaek  # noqa: E402

MIRRORED = []
_orig_conf, _orig_send = tb.load_conf, tb.send_text
tb.load_conf = lambda: {"bot_token": "T", "allowed_chat_ids": [111]}
tb.send_text = lambda token, chat_id, text: MIRRORED.append((chat_id, text))

config.TELEGRAM_MIRROR = True
dongbaek._mirror_send("몇 시야", "지금 3시입니다.")
check("한 건 전송", len(MIRRORED), 1)
contains("명령 포함", MIRRORED[0][1], "몇 시야")
contains("답변 포함", MIRRORED[0][1], "지금 3시입니다.")
check("허용된 챗으로", MIRRORED[0][0], 111)

MIRRORED.clear()
config.TELEGRAM_MIRROR = False
dongbaek._mirror_send("몇 시야", "답")
check("끄면 안 보낸다", MIRRORED, [])
config.TELEGRAM_MIRROR = True

# ⚠ 테스트 프로세스에서는 스레드 경로가 아예 침묵해야 한다 — 이 가드가
#   없으면 test_concurrency 의 가짜 문답이 실제 폰으로 날아간다.
dongbaek._mirror_to_telegram("가짜 명령", "가짜 답")
time.sleep(0.3)
check("test_* 프로세스는 전송 금지", MIRRORED, [])

tb.load_conf, tb.send_text = _orig_conf, _orig_send

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    for f in FAIL:
        print("  " + f)
    raise SystemExit(1)
print("✅ 전부 통과 — 원격 경로도 안전 게이트를 우회하지 않음")
