#!/usr/bin/env python3
"""스스로 이상을 알아채는가 — 2026-08-16 지시의 시험.

그날 저녁 실제로 있었던 일이 이 시험의 뼈대다. 들림 83건 중 명령 6건이었고
(마이크가 목소리를 거의 못 잡았다) 동백은 한 시간 넘게 아무 말도 안 했다.
사장님이 "텔레그램에 전혀 안 들어오네" 하셔서야 알았다. 그동안 채점은
계속 돌고 있었다 — 숫자가 있는데 안 보는 것은 숫자가 없는 것과 같다.

여기서 지키는 것.
  1. 이상하면 알아챈다 (귀 먹음·낮은 음량·환청)
  2. **멀쩡하면 아무 말도 안 한다** — 거짓 경보가 나면 다음부터 안 듣는다
  3. 조용한 날을 이상으로 착각하지 않는다 — 건수가 아니라 비율로 본다

    python tests/test_selfcheck.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import selfcheck

FAIL = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))


def fake_log(heard: int, cmds: int, level: float, thanks: int = 0) -> list[str]:
    """로그 몇 줄을 흉내 낸다. 시각은 지금 기준 — 시간 창에 들어가야 한다."""
    t = (datetime.now() - timedelta(minutes=5)).strftime("%m-%d %H:%M:%S")
    out = []
    for i in range(heard):
        word = "감사합니다." if i < thanks else f"명령 {i}"
        out.append(f"{t} [동백] 들림: '{word}'")
        out.append(f"{t} [동백] 먼 소리 증폭 2.0배 (원본 {level:.4f})")
    for i in range(cmds):
        out.append(f"{t} [동백] 명령: '오늘 일정 알려줘'")
    return out


_real_tail = selfcheck._tail
_real_score = None
try:
    import score as _s
    _real_score = _s.summary
    _s.summary = lambda days=1: {"attempts": 0, "rate": 100, "kinds": {}}
except Exception:
    pass

print("[1] 귀가 먹으면 알아챈다")
selfcheck._tail = lambda hours=2.0: fake_log(heard=80, cmds=5, level=0.0130)
bad = selfcheck.check()
check("이상을 잡는다", len(bad) >= 1, True)
check("귀 먹음을 말한다", any("명령까지 못" in b for b in bad), True)
check("음량이 낮은 것도 말한다", any("소리가 작" in b for b in bad), True)

print("\n[2] 멀쩡하면 아무 말도 안 한다")
selfcheck._tail = lambda hours=2.0: fake_log(heard=40, cmds=30, level=0.0600)
check("조용하다", selfcheck.check(), [])

print("\n[3] 조용한 날을 이상으로 보지 않는다")
# ⚠ 건수로 재면 "오늘 명령이 2건뿐" 이 늘 경보가 된다. 들림이 적으면
#   비율을 따지지 않는다 — 표본이 없는데 판단하면 그게 거짓 경보다.
selfcheck._tail = lambda hours=2.0: fake_log(heard=6, cmds=0, level=0.0600)
check("표본이 적으면 판단하지 않는다",
      any("명령까지 못" in b for b in selfcheck.check()), False)

print("\n[4] 환청이 잦으면 알아챈다")
selfcheck._tail = lambda hours=2.0: fake_log(heard=40, cmds=30, level=0.0600, thanks=12)
check("환청을 말한다", any("환청" in b for b in selfcheck.check()), True)

selfcheck._tail = _real_tail
if _real_score:
    _s.summary = _real_score

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — 이상하면 먼저 말하고, 멀쩡하면 조용하다")
