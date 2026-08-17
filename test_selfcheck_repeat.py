#!/usr/bin/env python3
"""자가점검이 같은 말을 되풀이하지 않는가 — 2026-08-17.

실제로 있었던 일이 이 시험의 뼈대다. 08-16 22:29 부터 08-17 01:05 까지
15분마다, 뜻이 똑같은 자가점검이 텔레그램으로 **11번** 나갔다.

    22:29  들림 102건 중 명령 6건 … 중앙 0.0149
    22:44  들림  91건 중 명령 0건 … 중앙 0.0144
    22:59  들림  98건 중 명령 0건 … 중앙 0.0146
    …                                     (여덟 번 더)

되풀이 방지(dongbaek.said_checks)는 이미 있었는데, 거르는 열쇠가 문장
전체였다. 문장에는 볼 때마다 바뀌는 숫자가 박혀 있어 같은 이상이 매번
새것으로 보였다 — 중복 제거가 한 번도 안 걸렸다.

여기서 지키는 것.
  1. 숫자가 달라도 같은 이상이면 갈래가 같다
  2. 그 갈래로 거르면 저 열한 번이 한 번이 된다
  3. 나아졌다 다시 나빠지면 다시 말한다 (입을 영영 막는 게 아니다)

    python tests/test_selfcheck_repeat.py
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


def fake_log(heard: int, cmds: int, level: float) -> list[str]:
    t = (datetime.now() - timedelta(minutes=5)).strftime("%m-%d %H:%M:%S")
    out = []
    for i in range(heard):
        out.append(f"{t} [동백] 들림: '명령 {i}'")
        out.append(f"{t} [동백] 먼 소리 증폭 2.0배 (원본 {level:.4f})")
    for i in range(cmds):
        out.append(f"{t} [동백] 명령: '오늘 일정 알려줘'")
    return out


def sent(rounds):
    """dongbaek 의 자가점검 대목을 그대로 흉내 낸다 — 실제로 나간 것만 센다."""
    said, out = set(), []
    for heard, cmds, level in rounds:
        selfcheck._tail = lambda hours=2.0, h=heard, c=cmds, l=level: fake_log(h, c, l)
        bad = selfcheck.check_kinds()
        fresh = [(k, t) for k, t in bad if k not in said]
        if fresh:
            said.update(k for k, _ in fresh)
            out.append([k for k, _ in fresh])
        elif not bad:
            said.clear()
    return out


_real_tail = selfcheck._tail
_real_score = None
try:
    import score as _s
    _real_score = _s.summary
    _s.summary = lambda days=1: {"attempts": 0, "rate": 100, "kinds": {}}
except Exception:
    pass

# 08-16 22:29 ~ 08-17 01:05 에 실제로 찍힌 열한 판. 숫자만 조금씩 다르다.
그날밤 = [(102, 6, 0.0149), (91, 0, 0.0144), (98, 0, 0.0146), (92, 0, 0.0143),
          (79, 0, 0.0142), (59, 0, 0.0141), (48, 0, 0.0139), (31, 0, 0.0133),
          (14, 0, 0.0136), (12, 0, 0.0121), (12, 0, 0.0114)]

print("[1] 숫자가 달라도 같은 이상이면 갈래가 같다")
selfcheck._tail = lambda hours=2.0: fake_log(102, 6, 0.0149)
첫판 = [k for k, _ in selfcheck.check_kinds()]
selfcheck._tail = lambda hours=2.0: fake_log(59, 0, 0.0141)
넷째판 = [k for k, _ in selfcheck.check_kinds()]
check("귀먹음·음량낮음을 잡는다", 첫판, ["귀먹음", "음량낮음"])
check("숫자가 달라도 갈래는 그대로", 넷째판, 첫판)

print("\n[2] 그날 밤 열한 판이 한 번으로 줄어든다")
나간것 = sent(그날밤)
check("텔레그램은 한 번만", len(나간것), 1)
check("그 한 번에 두 갈래가 함께", 나간것[0], ["귀먹음", "음량낮음"])

print("\n[3] 문장으로 거르면 열한 번 다 나간다 — 고치기 전이 그랬다")
# ⚠ 되돌아가지 않는지 보는 자리다. 갈래가 아니라 말로 거르면 몇 번
#   나가는지를 같은 판으로 직접 센다.
said_text, 샜다 = set(), 0
for heard, cmds, level in 그날밤:
    selfcheck._tail = lambda hours=2.0, h=heard, c=cmds, l=level: fake_log(h, c, l)
    texts = [t for _, t in selfcheck.check_kinds()]
    fresh = [t for t in texts if t not in said_text]
    if fresh:
        said_text.update(fresh)
        샜다 += 1
check("옛 방식이면 열한 번 다 샌다", 샜다, 11)

print("\n[4] 나아졌다 다시 나빠지면 다시 말한다")
나간것 = sent([(102, 6, 0.0149), (40, 30, 0.0600), (91, 0, 0.0144)])
check("멀쩡한 판을 지나면 입이 다시 열린다", len(나간것), 2)

print("\n[5] check() 는 여전히 말만 돌려준다")
selfcheck._tail = lambda hours=2.0: fake_log(102, 6, 0.0149)
말들 = selfcheck.check()
check("문자열 목록이다", all(isinstance(t, str) for t in 말들) and len(말들) == 2)

selfcheck._tail = _real_tail
if _real_score:
    _s.summary = _real_score

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — 같은 이상은 한 번만, 나아지면 다시")
