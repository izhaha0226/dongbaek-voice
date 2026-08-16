#!/usr/bin/env python3
"""장기 기억 — 문답과 업무일지를 임베딩으로 저장하고 되찾는다. 전부 로컬.

"지난주에 말한 그 업체" 를 풀려면 세션(1시간)보다 긴 기억이 필요하다.
저장은 sqlite(표준 라이브러리), 임베딩은 ollama 의 qwen3-embedding
(0.6b, Apache-2.0 — 큐웬 패밀리), 검색은 numpy 코사인 브루트포스다.
수천 건이면 수 밀리초라 벡터 DB 를 얹을 이유가 없다.

실패는 전부 조용히 빈 결과 — 기억이 안 나서 동백이 멈추면 안 된다.
"""

import json
import sqlite3
import urllib.request
from datetime import datetime
from pathlib import Path

import numpy as np

import config

DB = Path(getattr(config, "MEMORY_DB", config.STATE / "memory.db"))
STAMP = config.STATE / "memory_stamp.json"


def _embed(text: str) -> np.ndarray | None:
    """정규화된 임베딩. 실패하면 None."""
    text = (text or "").strip()
    if not text:
        return None
    # 색인은 하루 한 번, 회상도 가끔이다 — 붙들고 있을 이유가 없다.
    payload = {"model": config.MEMORY_EMBED_MODEL, "input": text[:800],
               "keep_alive": getattr(config, "MEMORY_KEEP_ALIVE", "30s")}
    req = urllib.request.Request(
        f"{config.GATEKEEPER_URL}/api/embed",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        vec = (data.get("embeddings") or [[]])[0]
        v = np.asarray(vec, dtype=np.float32)
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else None
    except Exception:
        return None


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB)
    con.execute("create table if not exists memories ("
                "id integer primary key, ts text, kind text, "
                "text text, emb blob)")
    return con


def remember(kind: str, text: str, ts: str | None = None) -> bool:
    e = _embed(text)
    if e is None:
        return False
    with _conn() as con:
        con.execute("insert into memories (ts, kind, text, emb) values (?,?,?,?)",
                    (ts or datetime.now().isoformat(timespec="seconds"),
                     kind, text, e.tobytes()))
    return True


def recall(query: str, k: int = 3,
           min_sim: float | None = None) -> list[str]:
    """질문과 닮은 기억 — "[7월 3일] 사장님: … / 동백: …" 꼴로.

    문턱 아래는 돌려주지 않는다 — 어설픈 기억을 자신 있게 말하는 것보다
    "기억에 없습니다" 가 낫다 (화자 인증과 같은 철학).
    """
    if min_sim is None:
        min_sim = getattr(config, "MEMORY_MIN_SIM", 0.35)
    q = _embed(query)
    if q is None:
        return []
    try:
        with _conn() as con:
            rows = con.execute("select ts, text, emb from memories").fetchall()
    except sqlite3.Error:
        return []
    if not rows:
        return []
    embs = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32)
    embs = embs.reshape(len(rows), -1)
    sims = embs @ q
    order = np.argsort(-sims)[: k * 3]
    out = []
    for i in order:
        if sims[i] < min_sim or len(out) >= k:
            break
        ts, text = rows[int(i)][0], rows[int(i)][1]
        try:
            d = datetime.fromisoformat(ts)
            when = f"{d.month}월 {d.day}일"
        except ValueError:
            when = ts[:10]
        out.append(f"[{when}] {text}")
    return out


# ─────────────────────────────────────────────────────────
# 색인 — 증분. 퇴근 브리핑이 하루 한 번 부른다.
# ─────────────────────────────────────────────────────────
def _load_stamp() -> dict:
    try:
        return json.loads(STAMP.read_text())
    except (OSError, ValueError):
        return {}


def index_new() -> int:
    """transcript 의 새 문답과 새 업무일지를 색인한다. 넣은 개수 반환."""
    if not getattr(config, "MEMORY_ENABLED", False):
        return 0
    stamp = _load_stamp()
    added = 0

    # 문답 — 클로드·게이트키퍼 경로만. 시각 조회 따위는 기억 가치가 없다.
    try:
        size = config.TRANSCRIPT_LOG.stat().st_size
        start = int(stamp.get("off", 0))
        if start > size:
            start = 0
        with config.TRANSCRIPT_LOG.open("rb") as f:
            f.seek(start)
            chunk = f.read().decode("utf-8", "ignore")
        stamp["off"] = size
        for line in chunk.splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("route") not in ("claude", "gatekeeper"):
                continue
            cmd = (r.get("command") or "").strip()
            rep = (r.get("reply") or "").strip()
            if len(cmd) < 6 or not rep:
                continue
            text = f"사장님: {cmd[:150]} / 동백: {rep[:250]}"
            if remember("dialog", text, ts=r.get("ts")):
                added += 1
    except OSError:
        pass

    # 업무일지 — 파일 하나가 기억 하나. 요약이 이미 돼 있는 문서다.
    try:
        import briefing

        done = set(stamp.get("journals", []))
        for md in sorted(briefing.WIKI_DIR.glob("*.md")):
            if md.name in done:
                continue
            body = md.read_text(encoding="utf-8")[:1500]
            if remember("journal", f"업무일지 {md.stem}: {body}",
                        ts=md.stem + "T18:00:00"):
                added += 1
                done.add(md.name)
        stamp["journals"] = sorted(done)
    except Exception:
        pass

    try:
        STAMP.write_text(json.dumps(stamp, ensure_ascii=False))
    except OSError:
        pass
    return added
