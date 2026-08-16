#!/usr/bin/env python3
"""무거운 명령이 도는 동안에도 계속 듣는가.

예전에는 Claude 응답을 기다리는 동안 대기 루프가 통째로 멈춰 있었다.
무거운 명령 하나가 3분을 쓰면 그동안 "동백아" 를 불러도 반응이 없어
고장 난 줄 알게 된다 — 실제로 타임아웃이 하루 세 번 났고, 사장님은
'연결이 안 되네' 라고 하셨다.

지키려는 것:
  ① 명령을 넘기는 데 걸리는 시간이 0에 가깝다 (듣는 쪽이 안 막힌다)
  ② 처리 중에 들어온 새 명령도 접수된다
  ③ 그래도 실행은 하나씩 순서대로 (같은 Claude 세션을 쓰므로)
  ④ 밀리면 거절하고 알린다 (무한정 쌓이면 한참 뒤에 엉뚱하게 실행된다)
    python test_concurrency.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import threading
import time

import bridge
import code_guard
import dongbaek
import speak

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


# 실제 소리·git·Claude 없이 흐름만 본다
speak.say = lambda *a, **k: None  # type: ignore[assignment]
code_guard.guard = lambda t, n="": (True, "", {"repo": t, "label": "t",
                                               "fingerprint": "f0"})  # type: ignore[assignment]
code_guard.tree_fingerprint = lambda r: "f0"  # type: ignore[assignment]

STARTED, DONE = [], []
_lock = threading.Lock()
_max_concurrent = 0
_running = 0
SLOW = 1.5


def slow_ask(prompt, elevated=False, dev=False, on_text=None):
    global _running, _max_concurrent
    with _lock:
        _running += 1
        _max_concurrent = max(_max_concurrent, _running)
        STARTED.append(prompt)
    try:
        time.sleep(SLOW)          # 무거운 Claude 호출 흉내
    finally:
        with _lock:
            _running -= 1
            DONE.append(prompt)
    return ("했습니다.", {"effective_input": 0, "cache_read": 0,
                       "cache_write": 0, "output": 0, "cost_usd": 0})


bridge.ask = slow_ask  # type: ignore[assignment]
threading.Thread(target=dongbaek._run_jobs, daemon=True).start()

print("[1] 넘기는 즉시 돌아온다 — 듣는 쪽이 안 막힌다")
t0 = time.monotonic()
dongbaek.submit_command("무거운 첫 명령", heard="x")
handoff = time.monotonic() - t0
check(f"넘기는 데 {handoff * 1000:.0f}ms (Claude 는 {SLOW}초 걸린다)", handoff < 0.3, True)

print("\n[2] 처리 중에 들어온 명령도 받는다")
time.sleep(0.4)                    # 첫 명령이 도는 중
check("실행 중임을 확인", len(STARTED), 1)
check("새 명령 접수됨", dongbaek.submit_command("처리 중 둘째 명령", heard="y"), True)

print("\n[3] 대기열이 밀리면 거절하고 알린다")
accepted = [dongbaek.submit_command(f"밀어넣기{i}", heard="z") for i in range(5)]
check("일부는 거절된다", False in accepted, True)
check(f"대기열 상한 {dongbaek._MAX_PENDING} 을 넘지 않는다",
      dongbaek._JOBS.qsize() <= dongbaek._MAX_PENDING, True)

print("\n[4] 실행은 하나씩 — 같은 Claude 세션을 나란히 부르면 기록이 엉킨다")
deadline = time.monotonic() + 20
while dongbaek._JOBS.qsize() and time.monotonic() < deadline:
    time.sleep(0.1)
time.sleep(SLOW + 0.5)
check(f"동시 실행이 없었다 (최대 {_max_concurrent})", _max_concurrent, 1)
check("첫 명령이 맨 먼저 처리됐다", DONE[0] if DONE else None, "무거운 첫 명령")

print("\n[5] 실행 중 예외가 나도 실행기가 죽지 않는다")


def boom(prompt, elevated=False, dev=False, on_text=None):
    raise RuntimeError("일부러 낸 오류")


bridge.ask = boom  # type: ignore[assignment]
dongbaek.submit_command("터지는 명령", heard="x")
time.sleep(1.0)
bridge.ask = slow_ask  # type: ignore[assignment]
before = len(DONE)
dongbaek.submit_command("그다음 명령", heard="x")
deadline = time.monotonic() + 15
while len(DONE) == before and time.monotonic() < deadline:
    time.sleep(0.1)
check("오류 뒤에도 다음 명령이 처리된다", len(DONE) > before, True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    raise SystemExit(1)
print("✅ 전부 통과 — 무거운 명령이 돌아도 귀는 열려 있다")
