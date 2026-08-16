#!/usr/bin/env python3
"""장기 기억 검증 — 임베딩·네트워크 없이 저장·회상·증분 색인만.

핵심: 문턱 아래 기억은 입 밖에 내지 않는다, 색인은 증분이다,
잡동사니(로컬 시각 조회 따위)는 기억하지 않는다.
    python test_memory.py
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

import config
import memory_local

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


tmp = Path(tempfile.mkdtemp())
memory_local.DB = tmp / "memory.db"
memory_local.STAMP = tmp / "stamp.json"

A = np.zeros(8, dtype=np.float32); A[0] = 1.0
B = np.zeros(8, dtype=np.float32); B[1] = 1.0
memory_local._embed = lambda text: (A if "한빛" in text else B).copy()

print("\n[1] 저장·회상 — 닮은 것만, 문턱 아래는 침묵")
memory_local.remember("dialog", "사장님: 한빛리조트 CPC 낮추자 / 동백: 네",
                      ts="2026-08-01T10:00:00")
memory_local.remember("dialog", "사장님: 점심 뭐 먹지 / 동백: 국밥이요",
                      ts="2026-08-02T12:00:00")
hits = memory_local.recall("한빛 얘기 뭐였지")
check("정확히 한 건", len(hits), 1)
check("날짜 표기", hits[0].startswith("[8월 1일]"), True)
check("내용 포함", "한빛리조트 CPC" in hits[0], True)

print("\n[2] 증분 색인 — 잡동사니 제외, 두 번째 호출은 0건")
tr = tmp / "transcript.jsonl"
rows = [
    {"ts": "2026-08-11T09:00:00", "route": "claude",
     "command": "한빛리조트 제안서 초안 잡아줘", "reply": "초안 만들었습니다."},
    {"ts": "2026-08-11T09:01:00", "route": "local",
     "command": "지금 몇 시야", "reply": "9시입니다."},
    {"ts": "2026-08-11T09:02:00", "route": "gatekeeper",
     "command": "고마워 오늘도 부탁해", "reply": "별말씀을요."},
]
tr.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
_real_log = config.TRANSCRIPT_LOG
config.TRANSCRIPT_LOG = tr
import briefing  # noqa: E402

_real_wiki = briefing.WIKI_DIR
briefing.WIKI_DIR = tmp / "일지없음"
n1 = memory_local.index_new()
n2 = memory_local.index_new()
config.TRANSCRIPT_LOG = _real_log
briefing.WIKI_DIR = _real_wiki
check("잡동사니 빼고 2건", n1, 2)
check("증분 — 재호출 0건", n2, 0)

print("\n[3] 임베딩 실패 = 조용한 빈손")
memory_local._embed = lambda text: None
check("회상 빈 목록", memory_local.recall("아무거나"), [])
check("저장 거부", memory_local.remember("dialog", "x"), False)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과 — 기억은 확실할 때만 말한다")
