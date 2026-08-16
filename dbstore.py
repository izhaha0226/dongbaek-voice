#!/usr/bin/env python3
"""동백 로컬 DB — 대화와 조회 결과가 한 곳에 쌓인다 (SQLite, state/dongbaek.db).

왜 SQLite 인가 (사장님 지시 2026-08-13 "로컬 DB 설치하자"):
  장기 기억(memory_local)이 이미 같은 방식으로 돌고 있다. 서버 프로세스가
  없어 죽을 것도 지킬 것도 없고, 파일 하나라 백업이 복사다. 대화가 맥미니
  밖으로 나가지 않고, 회선이 죽어도 기록은 계속된다.

**이 DB 가 정본이다** (사장님 지시 2026-08-16 "db를 정본으로 해").
  2026-08-13 부터 transcript.jsonl 과 나란히 같은 것을 쌓아 왔는데, 두 곳에
  같은 기록이 있으면 갈라지는 날 어느 쪽이 사실인지 판단할 근거가 없다.
  지금은 읽는 쪽이 전부 이 DB 를 본다.

  jsonl 은 구명정으로만 남긴다 — DB 쓰기가 실패한 건만 거기 적힌다.
  평소에는 한 줄도 안 늘어난다. 그 파일에 줄이 생겼다면 DB 가 아팠다는 뜻이다.

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
  cost_usd REAL,
  who TEXT,
  output INTEGER,
  danger TEXT,
  confirmed INTEGER,
  elevated INTEGER,
  error TEXT
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
    # 뒤늦게 붙는 열들. CREATE TABLE IF NOT EXISTS 는 이미 만들어진 표에
    # 열을 못 붙이므로 여기서 이행한다.
    #   who                              H2 다자간 (2026-08-13)
    #   output·danger·confirmed·elevated·error
    #                                    jsonl 에만 있던 것 (2026-08-16,
    #                                    정본을 DB 로 옮기며 흡수)
    cols = {r[1] for r in c.execute("PRAGMA table_info(transcript)")}
    for name, typ in (("who", "TEXT"), ("output", "INTEGER"),
                      ("danger", "TEXT"), ("confirmed", "INTEGER"),
                      ("elevated", "INTEGER"), ("error", "TEXT")):
        if name not in cols:
            c.execute(f"ALTER TABLE transcript ADD COLUMN {name} {typ}")
    return c


_FIELDS = ("ts", "source", "route", "heard", "command", "reply",
           "effective_input", "cost_usd", "who", "output",
           "danger", "confirmed", "elevated", "error")


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


# ── 읽기 ──────────────────────────────────────────────────
# 읽는 쪽(기억·용어학습·자가정비·브리핑·채점·게이트키퍼·브릿지)은 전부
# jsonl 을 한 줄씩 json.loads 해서 dict 를 만들던 코드였다. 그래서 여기서도
# **같은 모양의 dict** 를 돌려준다 — 부르는 쪽은 파일 여는 두어 줄만 바뀐다.
# 열 이름이 곧 옛 jsonl 의 키다. 하나 더 붙는 건 id 뿐이고, 그건 꼬리를
# 이어 읽을 때 바이트 오프셋 대신 쓰는 자리표다.


def _dicts(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def rows(since: str | None = None, limit: int | None = None,
         newest_first: bool = False) -> list[dict]:
    """문답 기록. since 는 ISO 문자열(그 시각 이후), limit 는 건수.

    limit 를 주면 **최신 것부터** 세어 그만큼 가져오되, 돌려줄 때는 다시
    옛날→최신 순으로 뒤집는다. 옛 코드가 `splitlines()[-500:]` 로 하던 것과
    같은 뜻이고, 부르는 쪽의 순서 가정이 깨지지 않는다.
    """
    where, args = "", []
    if since:
        where = " WHERE ts >= ?"
        args.append(since)
    try:
        with _conn() as c:
            if limit is not None:
                cur = c.execute(
                    f"SELECT * FROM transcript{where} ORDER BY id DESC LIMIT ?",
                    (*args, limit))
                out = _dicts(cur)
                out.reverse()
            else:
                cur = c.execute(
                    f"SELECT * FROM transcript{where} ORDER BY id ASC", args)
                out = _dicts(cur)
    except Exception:
        return []
    if newest_first:
        out.reverse()
    return out


def rows_after(last_id: int, limit: int = 2000) -> list[dict]:
    """자리표 뒤로 새로 쌓인 것만. 기억(memory_local)이 꼬리를 따라올 때 쓴다."""
    try:
        with _conn() as c:
            return _dicts(c.execute(
                "SELECT * FROM transcript WHERE id > ? ORDER BY id ASC LIMIT ?",
                (last_id, limit)))
    except Exception:
        return []


def max_id() -> int:
    """지금까지의 마지막 자리표. 처음 따라붙는 쪽이 '여기부터' 로 쓴다."""
    try:
        with _conn() as c:
            return c.execute(
                "SELECT COALESCE(MAX(id), 0) FROM transcript").fetchone()[0]
    except Exception:
        return 0


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
