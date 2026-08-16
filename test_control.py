#!/usr/bin/env python3
"""제어 서버 검증 — 토큰 인증과 '위험 명령 HTTP 우회 불가'를 확인.

이 경로로 안전 게이트가 뚫리면 아이폰을 주운 사람이 프로덕션을 배포할 수 있다.
    python test_control.py
"""
import json
import urllib.error
import urllib.request

import bridge
import config
import speak
import dongbaek

FAIL = []
CALLED = []

# 실제 Claude 호출·발화 없이 로직만 본다
bridge.ask = lambda prompt, elevated=False, dev=False, on_text=None: (  # type: ignore[assignment]
    CALLED.append((prompt, elevated)),
    ("처리했습니다.", {"effective_input": 0, "cache_read": 0, "cache_write": 0, "output": 0}),
)[1]
speak.say = lambda *a, **k: None  # type: ignore[assignment]
speak.beep = lambda *a, **k: None  # type: ignore[assignment]

config.CONTROL_PORT = 8799
import control  # noqa: E402

PTT = []
host, port, token = control.start(
    {
        "ptt": lambda: PTT.append(1),
        "say": lambda t: None,
        "command": dongbaek.handle_http_command,
        "status": lambda: {"ok": True, "mic": False},
    }
)
BASE = f"http://{host}:{port}"


def req(path, *, tok=token, body=None):
    url = f"{BASE}{path}"
    if tok is not None:
        url += ("&" if "?" in url else "?") + f"token={tok}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check(label, got, want):
    ok = got == want
    if not ok:
        FAIL.append(f"{label}: 기대={want!r} 실제={got!r}")
    print(f"  {'✓' if ok else '✗'} {label}")


print("\n[1] 인증 — 토큰 없거나 틀리면 존재조차 숨겨야 함")
check("토큰 없음 → 404", req("/status", tok=None)[0], 404)
check("틀린 토큰 → 404", req("/status", tok="wrong-token")[0], 404)
check("정상 토큰 → 200", req("/status")[0], 200)

print("\n[2] 푸시투토크")
check("/ptt → 200", req("/ptt")[0], 200)
check("훅 호출됨", len(PTT), 1)

print("\n[3] 안전 명령은 그냥 통과 (Claude 로 감)")
CALLED.clear()
# 일정·시간 등은 로컬에서 가로채므로, Claude 경로를 보려면 그 밖의 질문이어야 한다
code, res = req("/command", body={"text": "이 코드 리뷰해줘"})
check("200 반환", code, 200)
check("Claude 호출됨", len(CALLED), 1)
check("권한 상승 안 됨", CALLED[0][1] if CALLED else None, False)

print("\n[3b] 일정 조회는 로컬이 가로채 Claude 를 안 부른다 (0 토큰)")
CALLED.clear()
code, res = req("/command", body={"text": "오늘 일정 알려줘"})
check("200 반환", code, 200)
check("Claude 호출 안 됨", len(CALLED), 0)

print("\n[4] 위험 명령은 HTTP로도 막혀야 함  ← 핵심")
for danger in ["프로덕션 배포해", "디비 전부 삭제해", "고객 전체에게 메일 보내"]:
    CALLED.clear()
    code, res = req("/command", body={"text": danger})
    check(f"{danger!r} → 409 보류", code, 409)
    check(f"{danger!r} → Claude 호출 안 됨", len(CALLED), 0)

print("\n[5] 엉터리 confirm 으로는 못 뚫음")
# "어 그래" 는 뺐다. 음성 대화에서 명백한 동의라 승인으로 처리하도록 바꿨다.
# (CONFIRM_WORDS 에 '그래' 추가 — 사람이 실제로 쓰는 말로 넓힘)
for bad in ["", "네", "어", "아니 취소", "진행하지마", "그래 근데 나중에"]:
    CALLED.clear()
    code, _ = req("/command", body={"text": "프로덕션 배포해", "confirm": bad})
    check(f"confirm={bad!r} → 409", code, 409)
    check(f"confirm={bad!r} → 호출 안 됨", len(CALLED), 0)

print("\n[6] 올바른 confirm 이면 권한 상승해서 실행")
CALLED.clear()
code, res = req("/command", body={"text": "프로덕션 배포해", "confirm": "진행"})
check("200 반환", code, 200)
check("Claude 호출됨", len(CALLED), 1)
check("elevated=True 로 호출", CALLED[0][1] if CALLED else None, True)

print("\n[7] 없는 경로")
check("/hack → 404", req("/hack")[0], 404)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    for f in FAIL:
        print("  " + f)
    raise SystemExit(1)
print("✅ 전부 통과 — HTTP 경로로 안전 게이트 우회 불가")
