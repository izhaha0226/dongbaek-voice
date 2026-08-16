#!/usr/bin/env python3
"""상주 연결 검증 — '실패해도 사장님 명령이 멈추지 않는가'.

실제 클로드·claude 프로세스 없이 돈다. ClaudeSDKClient 와 _collect 를
몽키패치해 상주 경로의 수명·폴백 규칙만 본다.
    python test_resident.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import asyncio
import sys

import claude_agent_sdk

import bridge
import bridge_sdk
import config

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


# 실제 회계·세션 파일을 건드리면 안 된다 — 실사용 기록이다
bridge._load_session = lambda model: None  # type: ignore[assignment]
bridge._save_session = lambda model, sid: None  # type: ignore[assignment]
bridge._log_tokens = lambda prompt, usage, cost, elevated, model, tier: {  # type: ignore[assignment]
    "effective_input": 0, "cache_read": 0, "cache_write": 0,
    "output": 0, "cost_usd": 0,
}


class FakeResult:
    session_id = "sid-1"
    usage = {}
    total_cost_usd = 0.0
    is_error = False
    num_turns = 1


FAKE = {"connect_fail": False, "collect_mode": "ok"}


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, options):
        self.options = options
        self.queries: list[str] = []
        self.disconnected = False
        FakeClient.instances.append(self)

    async def connect(self):
        if FAKE["connect_fail"]:
            raise RuntimeError("연결 실패")

    async def query(self, prompt, session_id="default"):
        self.queries.append(prompt)

    def receive_response(self):
        return None                      # _collect 가 몽키패치라 안 쓴다

    async def disconnect(self):
        self.disconnected = True


claude_agent_sdk.ClaudeSDKClient = FakeClient  # type: ignore[attr-defined]


async def fake_collect(stream, on_text):
    if FAKE["collect_mode"] == "midfail":
        if on_text:
            on_text("첫 조각")           # 이미 말하기 시작했다
        raise RuntimeError("스트림 도중 사망")
    if on_text:
        on_text("답")
    return "상주 답변.", FakeResult()


bridge_sdk._collect = fake_collect  # type: ignore[assignment]

COLD = {"n": 0}
_real_cold = bridge_sdk._ask_cold


def fake_cold(prompt, **kw):
    COLD["n"] += 1
    return "일회성 답변.", {"cost_usd": 0}


bridge_sdk._ask_cold = fake_cold  # type: ignore[assignment]


def reset():
    FakeClient.instances.clear()
    COLD["n"] = 0
    # 남은 상주 클라이언트를 비운다 — 검사끼리 오염되면 안 된다
    for tier in list(bridge_sdk._residents):
        asyncio.run_coroutine_threadsafe(
            bridge_sdk._drop(tier), bridge_sdk._ensure_loop()
        ).result(timeout=5)


config.BRIDGE_RESIDENT = True
config.BRIDGE_RESIDENT_TIERS = ("normal",)

print("\n[1] 두 번째 호출은 같은 클라이언트를 쓴다 — 스폰 1회")
reset()
r1, _ = bridge_sdk.ask("첫 명령")
r2, _ = bridge_sdk.ask("둘째 명령")
check("답변 정상", (r1, r2), ("상주 답변.", "상주 답변."))
check("클라이언트 생성 1회", len(FakeClient.instances), 1)
check("한 클라이언트에 질의 2회", FakeClient.instances[0].queries,
      ["첫 명령", "둘째 명령"])

print("\n[2] 연결이 안 되면 일회성 경로로 조용히 폴백")
reset()
FAKE["connect_fail"] = True
reply, _ = bridge_sdk.ask("명령")
check("일회성 답변 반환", reply, "일회성 답변.")
check("폴백 1회", COLD["n"], 1)
FAKE["connect_fail"] = False

print("\n[3] ⚠ 말하기 시작한 뒤 끊기면 폴백하지 않는다 — 두 번 말하기 방지")
reset()
FAKE["collect_mode"] = "midfail"
spoken = []
try:
    bridge_sdk.ask("명령", on_text=spoken.append)
    check("ClaudeError 발생", "예외 없음", "ClaudeError")
except bridge.ClaudeError:
    check("ClaudeError 발생", "ClaudeError", "ClaudeError")
check("폴백 안 함", COLD["n"], 0)
check("죽은 클라이언트는 버림", "normal" in bridge_sdk._residents, False)
FAKE["collect_mode"] = "ok"

print("\n[4] 세션 수명을 넘긴 클라이언트는 새로 연결한다")
reset()
bridge_sdk.ask("첫 명령")
bridge_sdk._residents["normal"].last_used -= config.SESSION_MAX_IDLE_SEC + 1
bridge_sdk.ask("한참 뒤 명령")
check("클라이언트 2개째", len(FakeClient.instances), 2)
check("옛 것은 disconnect", FakeClient.instances[0].disconnected, True)

print("\n[5] 끄면 전부 일회성 경로")
reset()
config.BRIDGE_RESIDENT = False
bridge_sdk.ask("명령")
check("상주 미사용", len(FakeClient.instances), 0)
check("일회성 1회", COLD["n"], 1)
config.BRIDGE_RESIDENT = True

print("\n[6] dev 등급은 상주하지 않는다")
reset()
bridge_sdk.ask("코드 명령", dev=True)
check("상주 미사용", len(FakeClient.instances), 0)
check("일회성 1회", COLD["n"], 1)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과")
