#!/usr/bin/env python3
"""로컬 DB(dbstore) — 저장·검색·요약이 약속대로 도는지.

⚠ 실사용 DB(state/dongbaek.db)를 건드리면 안 된다 — DB_PATH 를 임시로
  바꿔 시험한다. record() 의 테스트 오염 가드와 같은 원칙.
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import tempfile
from pathlib import Path

import dbstore

FAIL = []


def check(name, got, want=True):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


# 임시 DB 로 갈아끼운다
dbstore.DB_PATH = Path(tempfile.mkdtemp()) / "t.db"

print("[1] 문답 저장 → 요약·검색")
check("저장", dbstore.save({
    "ts": "2026-08-13T05:00:00", "source": "voice", "route": "local",
    "heard": "오늘 일정 알려줘", "command": "오늘 일정 알려줘",
    "reply": "일정 3건 있습니다", "effective_input": 0, "cost_usd": 0.0}))
check("저장2", dbstore.save({
    "ts": "2026-08-13T05:01:00", "source": "voice", "route": "claude",
    "heard": "광고플랫폼 성과 분석", "command": "광고플랫폼 성과 분석",
    "reply": "이번달 광고비 251만원입니다", "effective_input": 5000,
    "cost_usd": 0.27}))

brief = dbstore.recent_brief(2)
check("요약에 두 건 다", "일정 3건" in brief and "251만원" in brief, True)
check("요약에 경로 표시", "(local)" in brief and "(claude)" in brief, True)

found = dbstore.search("광고플랫폼", days=3650)
check("검색 적중", "251만원" in found, True)
check("검색 무적중 안내", "없습니다" in dbstore.search("없는말9999", days=1), True)

print("[2] 결과 저장 (조회 결과 아카이브)")
check("결과 저장", dbstore.save_result("ads", "오늘 성과", "광고비 8만원"))

print("[3] 빈 답변은 요약에서 뺀다")
dbstore.save({"ts": "2026-08-13T05:02:00", "source": "voice", "route": "blocked",
              "heard": "잡음", "command": "잡음", "reply": "",
              "effective_input": 0, "cost_usd": 0.0})
check("빈 답 제외", "잡음" not in dbstore.recent_brief(3), True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 대화가 로컬 DB 에 남고, 찾아진다")
