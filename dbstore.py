#!/usr/bin/env python3
"""동백 로컬 DB — 대화와 조회 결과가 한 곳에 쌓인다 (SQLite, state/dongbaek.db).

왜 SQLite 인가 (사장님 지시 2026-08-13 "로컬 DB 설치하자"):
  장기 기억(memory_local)이 이미 같은 방식으로 돌고 있다. 서버 프로세스가
  없어 죽을 것도 지킬 것도 없고, 파일 하나라 백업이 복사다. 대화가 맥미니
  밖으로 나가지 않고, 회선이 죽어도 기록은 계속된다.

원본은 여전히 transcript.jsonl 이다. 이 DB 는 미러 + 검색면이다.
  쓰기 실패는 조용히 무시한다(fail-open) — 기록 하나를 잃는 것보다
  명령 처리가 멎는 쪽이 훨씬 나쁘다.

세 테이블:
  transcript — record() 가 남기는 문답 전부 (들림·명령·경로·답·비용)
  results    — 동백이 밖에서 가져온 조회 결과 (광고·검색 등, kind 로 구분)
  meta       — 백필 커서 같은 내부 상태

⚠ 이 파일은 2026-08-13 새벽에 한 번 지워졌다 — 자가개선 롤백의
  git clean -fd 가 미커밋 상태이던 이 파일을 쓸어갔다 (self_improve.py
  _rollback 참조). 새 파일은 만들었으면 바로 커밋할 것.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import config

DB_PATH = config.STATE / "dongbaek.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcript(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  source TEXT,
  route TEXT,
  heard TEXT,
  command TEXT,
  reply TEXT,
  effective_input INTEGER,
  cost_usd REAL
);
CREATE INDEX IF NOT EXISTS idx_transcript_ts ON transcript(ts);
CREATE TABLE IF NOT EXISTS results(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  query TEXT,
  content TEXT
);
CREATE INDEX IF NOT EXISTS idx_results_ts ON results(ts);
CREATE TABLE IF NOT EXISTS meta(
  k TEXT PRIMARY KEY,
  v TEXT
);
"""


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=5)
    # WAL: 데몬이 쓰는 동안 MCP 셸아웃이 읽는다 — 잠금 충돌을 없앤다.
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(_SCHEMA)
    # who 열 (H2 다자간, 2026-08-13 추가) — 이미 만들어진 DB 에는
    # CREATE TABLE IF NOT EXISTS 가 열을 못 붙이므로 여기서 이행한다.
    cols = {r[1] for r in c.execute("PRAGMA table_info(transcript)")}
    if "who" not in cols:
        c.execute("ALTER TABLE transcript ADD COLUMN who TEXT")
    return c


_FIELDS = ("ts", "source", "route", "heard", "command", "reply",
           "effective_input", "cost_usd", "who")


def save(fields: dict) -> bool:
    """record() 한 건을 미러한다. 실패해도 조용하다 — 원본은 jsonl."""
    try:
        row = tuple(fields.get(k) for k in _FIELDS)
        with _conn() as c:
            c.execute(
                f"INSERT INTO transcript({','.join(_FIELDS)}) "
                f"VALUES({','.join('?' * len(_FIELDS))})", row)
        return True
    except Exception:
        return False


def save_result(kind: str, query: str, content: str) -> bool:
    """조회 결과를 남긴다 (광고 숫자·검색 결과 등). 사장님 지시의
    '결과를 가져와서 저장' 이 이 자리다."""
    from datetime import datetime

    try:
        with _conn() as c:
            c.execute("INSERT INTO results(ts,kind,query,content) VALUES(?,?,?,?)",
                      (datetime.now().isoformat(timespec="seconds"),
                       kind, query, content[:4000]))
        return True
    except Exception:
        return False


def recent_brief(n: int = 4, max_chars: int = 70) -> str:
    """직전 문답 n건을 한 줄로 — 클로드 프롬프트 첨부용.

    로컬·큐웬이 방금 답한 걸 클로드도 알아야 층이 갈라져 보이지 않는다
    (PLAN-unify 2단계). 길면 자른다 — 문맥이지 본문이 아니다.
    """
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT command, route, reply FROM transcript "
                "WHERE reply != '' ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    except Exception:
        return ""
    out = []
    for cmd, route, reply in reversed(rows):
        cmd = (cmd or "").strip()[:max_chars]
        reply = (reply or "").strip()[:max_chars]
        if cmd and reply:
            out.append(f"'{cmd}'→({route}) {reply}")
    return " / ".join(out)


def search(query: str, days: int = 7, limit: int = 8) -> str:
    """지난 대화에서 찾는다 — MCP history 도구가 쓴다."""
    from datetime import datetime, timedelta

    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    like = f"%{query}%"
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT ts, command, route, reply FROM transcript "
                "WHERE ts >= ? AND (command LIKE ? OR reply LIKE ?) "
                "ORDER BY id DESC LIMIT ?", (since, like, like, limit)).fetchall()
    except Exception:
        return "기록 조회 실패"
    if not rows:
        return f"지난 {days}일 대화에 '{query}' 가 없습니다."
    out = []
    for ts, cmd, route, reply in reversed(rows):
        out.append(f"[{ts[5:16]}] '{(cmd or '')[:60]}' → ({route}) {(reply or '')[:120]}")
    return "\n".join(out)


def backfill() -> int:
    """transcript.jsonl 의 기존 기록을 DB 로 옮긴다. 몇 번 돌려도 안전 —
    meta 에 몇 줄까지 읽었는지 적어두고 그 뒤만 마저 넣는다."""
    path = Path(config.TRANSCRIPT_LOG)
    if not path.exists():
        return 0
    lines = path.read_text(errors="replace").splitlines()
    with _conn() as c:
        done = int((c.execute("SELECT v FROM meta WHERE k='backfill_lines'")
                    .fetchone() or ["0"])[0])
        added = 0
        for ln in lines[done:]:
            try:
                f = json.loads(ln)
            except Exception:
                continue
            row = tuple(f.get(k) for k in _FIELDS)
            c.execute(
                f"INSERT INTO transcript({','.join(_FIELDS)}) "
                f"VALUES({','.join('?' * len(_FIELDS))})", row)
            added += 1
        c.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('backfill_lines',?)",
                  (str(len(lines)),))
    return added


if __name__ == "__main__":
    n = backfill()
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM transcript").fetchone()[0]
    print(f"백필 {n}건, DB 누적 {total}건 — {DB_PATH}")
