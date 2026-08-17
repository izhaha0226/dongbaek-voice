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


def _embed(text: str, *, query: bool = False) -> np.ndarray | None:
    """정규화된 임베딩. 실패하면 None.

    ⚠ ollama 를 떠났다 (2026-08-14). 사장님 판단: "임베딩 하나 때문에
      큐웬을 남겨둘 이유가 없다. 올라마가 아예 관여를 안 하게 하자."
      embed_local(multilingual-e5-small ONNX)이 이 프로세스 안에서 한다 —
      4ms, 별도 프로세스 없음. 실측으로 회상 품질은 오히려 올랐다
      ("회의 일정"·"목소리 인식" 에서 큐웬이 물어오던 무관한 항목이 빠졌다).

    ⚠ e5 는 찾는 말과 저장하는 글에 서로 다른 접두어를 요구한다.
      query 인자를 빠뜨리면 에러 없이 품질만 떨어지므로, 부르는 쪽이
      아니라 여기서 갈라 둔다.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        import embed_local

        if embed_local.available():
            return (embed_local.embed_query(text) if query
                    else embed_local.embed_passage(text))
    except Exception:
        pass
    # 모델이 없으면 옛 경로(ollama)로 내려간다 — 벽돌이 되는 것보다 낫다.
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
    q = _embed(query, query=True)      # 찾는 말 — e5 는 'query:' 접두어를 쓴다
    if q is None:
        return []
    try:
        with _conn() as con:
            rows = con.execute("select ts, text, emb, kind from memories").fetchall()
    except sqlite3.Error:
        return []
    if not rows:
        return []
    embs = np.frombuffer(b"".join(r[2] for r in rows), dtype=np.float32)
    embs = embs.reshape(len(rows), -1)
    sims = embs @ q

    # 진행 중인 계획은 지나간 잡담보다 앞에 세운다.
    #
    # ⚠ 2026-08-17. 사장님이 "2단계 진행하자" 하셨는데 동백이 "2단계로 뭘
    #   하기…" 하고 되물었다. "기억이 없어서 2단계가 뭔지 모른다는데.. 이런건
    #   DB를 활용하든 메모리에 저장하든 해야할거 아냐" 는 지적을 받았다.
    #
    #   계획을 넣고 나서도 절반은 옛 잡담에 묻혔다. 당연하다 — dialog 가
    #   1,039건이고 plan 은 5건이다. 닮은 정도만 보면 수가 많은 쪽이 이긴다.
    #
    #   그런데 이 둘은 값어치가 다르다. dialog 는 '그때 그런 말을 했다' 이고
    #   plan 은 '지금 하고 있는 일' 이다. 지금 일을 묻는데 옛 잡담을 꺼내면
    #   그건 기억이 아니라 소음이다. 그래서 얹어 준다.
    #
    #   문턱을 낮추는 게 아니라 순서만 바꾼다 — min_sim 은 그대로라
    #   엉뚱한 계획이 억지로 끌려 나오지는 않는다.
    #   ⚠ 얹은 값으로 문턱을 재면 안 된다. 처음에 sims 자체에 더했더니
    #     "오늘 광고비 얼마야" 에 계획이 딸려 나왔다 — 0.30 짜리가 0.08 을
    #     얻어 0.35 문턱을 넘은 것이다. 그건 순서를 바꾼 게 아니라 문턱을
    #     낮춘 것이다. 그래서 **줄 세우기에만** 쓰고, 통과 여부는 원래
    #     닮은 정도로 판단한다.
    #   ⚠ 얹는 값은 아주 작아야 한다. 이 임베딩(e5)은 닮은 정도가
    #     0.79~0.90 좁은 띠에 몰린다 — 실측. 그 안에서 0.08 은 거의
    #     '무조건 1등' 이라, "오늘 광고비 얼마야" 에 계획이 딸려 나왔다
    #     (관련 기억 0.902 vs 계획 0.823+0.08=0.903). 0.02 면 원래
    #     비슷하던 것들 사이에서만 순서가 뒤집힌다.
    boost = getattr(config, "MEMORY_PLAN_BOOST", 0.02)
    rank = sims
    if boost:
        rank = sims + np.array([boost if r[3] == "plan" else 0.0 for r in rows],
                               dtype=sims.dtype)
    order = np.argsort(-rank)[: k * 3]
    out = []
    for i in order:
        # 문턱은 원래 닮은 정도로 — 얹은 값(rank)이 아니라 sims 로 본다.
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
    #
    # 자리표가 바이트 오프셋('off')에서 DB 행 번호('row_id')로 바뀌었다
    # (2026-08-16, 정본을 DB 로 옮기며). 옛 자리표만 있는 첫 실행에서는
    # **지금까지 쌓인 것을 이미 읽은 것으로 친다** — 안 그러면 2,500건을
    # 통째로 다시 기억에 밀어 넣어 같은 대화가 두 벌이 된다.
    try:
        import dbstore

        if "row_id" not in stamp:
            stamp["row_id"] = dbstore.max_id()
        else:
            for r in dbstore.rows_after(int(stamp["row_id"])):
                stamp["row_id"] = r["id"]
                if r.get("route") not in ("claude", "gatekeeper"):
                    continue
                cmd = (r.get("command") or "").strip()
                rep = (r.get("reply") or "").strip()
                if len(cmd) < 6 or not rep:
                    continue
                text = f"사장님: {cmd[:150]} / 동백: {rep[:250]}"
                if remember("dialog", text, ts=r.get("ts")):
                    added += 1
    except Exception:
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
