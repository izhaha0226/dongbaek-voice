#!/usr/bin/env python3
"""상주 vs 일회성 — 실호출 벤치.

⚠ 실제 클로드 호출 5회(약 $1)가 나간다. 습관처럼 돌리지 말 것.

번갈아 재지 않으면 거짓말이 나온다 — 프롬프트 캐시는 세션이 아니라 프롬프트
앞부분으로 잡혀서, 먼저 돈 쪽이 뒤에 도는 쪽 캐시를 데워 준다 (bench_bridge
에서 실제로 겪었다). 순서를 섞고 '온기 오른 상주'끼리만 비교한다.

    일회성 → 상주(연결 포함) → 상주(온기) → 일회성 → 상주(온기)
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import time

import config

config.BRIDGE_RESIDENT_TIERS = ("normal",)

import bridge_sdk

PROMPT = "점검이야. 정확히 '확인' 한 단어로만 답해."


def timed(name: str, resident: bool):
    config.BRIDGE_RESIDENT = resident
    marks = {}

    def tap(chunk):
        marks.setdefault("first", time.time())

    t0 = time.time()
    reply, meta = bridge_sdk.ask(PROMPT, on_text=tap)
    total = time.time() - t0
    first = marks.get("first", t0 + total) - t0
    print(f"  {name:16} 첫 글자 {first:5.2f}초   전체 {total:5.2f}초   "
          f"${meta.get('cost_usd') or 0:.3f}   {reply[:20]!r}")
    return first


print("상주 벤치 — 5회 실호출")
c1 = timed("일회성 #1", False)
r1 = timed("상주(연결+1회)", True)
r2 = timed("상주(온기) #1", True)
c2 = timed("일회성 #2", False)
r3 = timed("상주(온기) #2", True)

cold = sorted([c1, c2])[len([c1, c2]) // 2]
warm = sorted([r2, r3])[len([r2, r3]) // 2]
print()
print(f"일회성 첫 글자 중앙값 {cold:.2f}초 → 상주(온기) {warm:.2f}초 "
      f"({(1 - warm / cold) * 100:+.0f}%)")
