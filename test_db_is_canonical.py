#!/usr/bin/env python3
"""정본이 DB 인가 — 2026-08-16 "db를 정본으로 해" 지시의 회귀 시험.

2026-08-13 부터 같은 문답이 `state/transcript.jsonl` 과 `state/dongbaek.db`
두 곳에 나란히 쌓였다. 둘이 갈라지는 날 어느 쪽이 사실인지 가릴 근거가
없다는 게 문제였다. 지금은 쓰는 곳도 읽는 곳도 DB 한 곳이다.

jsonl 은 구명정으로만 남는다 — DB 쓰기가 실패한 건만 적힌다. 평소에는 한
줄도 안 늘어나고, 줄이 생겼다면 그 자체가 "DB 가 아프다" 는 신호다.

여기서 지키는 것 셋.
  1. dbstore 가 읽기 API 를 갖고 있고 실제로 돈다
  2. 읽는 쪽 어디에도 TRANSCRIPT_LOG 를 읽는 코드가 남아 있지 않다
  3. record() 는 DB 에 먼저 쓰고, 실패했을 때만 jsonl 로 흘린다

    python tests/test_db_is_canonical.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import dbstore

FAIL = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))


print("[1] 읽기 API 가 옛 jsonl 과 같은 모양을 돌려준다")
_real = dbstore.DB_PATH
with tempfile.TemporaryDirectory() as td:
    dbstore.DB_PATH = Path(td) / "t.db"
    for i, route in enumerate(("local", "claude", "gatekeeper")):
        dbstore.save({"ts": f"2026-08-1{i}T09:00:00", "route": route,
                      "source": "voice", "command": f"명령{i}",
                      "reply": f"답{i}", "output": 10 + i, "danger": None,
                      "confirmed": 1, "elevated": 0, "error": None})
    rows = dbstore.rows()
    check("세 건 다 들어갔다", len(rows), 3)
    check("옛 키가 전부 있다",
          {"ts", "source", "route", "heard", "command", "reply",
           "effective_input", "cost_usd", "who", "output", "danger",
           "confirmed", "elevated", "error"} <= set(rows[0]), True)
    check("옛날 → 최신 순", [r["command"] for r in rows],
          ["명령0", "명령1", "명령2"])
    check("limit 는 최신 것부터 세고 순서는 유지",
          [r["command"] for r in dbstore.rows(limit=2)], ["명령1", "명령2"])
    check("newest_first 뒤집기",
          dbstore.rows(limit=1, newest_first=True)[0]["command"], "명령2")
    check("since 로 자른다", len(dbstore.rows(since="2026-08-11T00:00:00")), 2)
    check("자리표 뒤만", len(dbstore.rows_after(1)), 2)
    check("max_id", dbstore.max_id(), 3)
dbstore.DB_PATH = _real

print("\n[2] 읽는 쪽에 jsonl 이 남아 있지 않다")
# 허용되는 자리는 셋뿐이다. 그 밖에서 TRANSCRIPT_LOG 를 만지면 정본이 둘이 된다.
ALLOWED = {
    "config.py",        # 경로 정의 (구명정)
    "dbstore.py",       # backfill — 옛 기록을 DB 로 옮기는 이행 도구
    "dongbaek.py",      # record() 의 구명정 쓰기
    # 자가 점검은 구명정 파일이 **자랐는지** 만 본다. 그 파일에 줄이 생겼다는
    # 것은 DB 쓰기가 실패했다는 뜻이라, 읽는 게 아니라 신호로 쓰는 것이다.
    "selfcheck.py",
}
offenders = []
for py in sorted(ROOT.glob("*.py")):
    # 시험 파일은 뺀다 — 옛 경로를 "언급" 하는 것과 코드가 그걸
    # "읽는" 것은 다르다. 공개판은 시험이 최상위에 평평하게 있어
    # 이 훑기에 함께 걸린다.
    if py.name.startswith("test_"):
        continue
    if py.name in ALLOWED:
        continue
    if "TRANSCRIPT_LOG" in py.read_text(encoding="utf-8"):
        offenders.append(py.name)
check(f"허용된 셋 밖에는 없다 (발견: {offenders})", offenders, [])

src = (ROOT / "dongbaek.py").read_text(encoding="utf-8")
i = src.index("def record(")
blk = src[i:i + 2000]
print("\n[3] record() 는 DB 먼저, jsonl 은 실패했을 때만")
check("dbstore.save 를 부른다", "dbstore.save(fields)" in blk)
check("실패했을 때만 jsonl", "if not dbstore.save(fields):" in blk)
check("DB 가 jsonl 보다 앞", blk.index("dbstore.save") < blk.index("TRANSCRIPT_LOG"))

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — 정본은 하나다")
