#!/usr/bin/env python3
"""장기 기억을 새 임베딩으로 다시 만든다 (ollama → embed_local).

차원이 1024(qwen3-embedding) → 384(e5-small) 로 바뀌어 옛 벡터는 못 쓴다.
원문이 memory.db 에 남아 있어 다시 만들 수 있다 — 실측 930건에 3.5초.

    .venv/bin/python tools/reembed_memory.py          # 실행
    .venv/bin/python tools/reembed_memory.py --dry    # 세어만 본다

⚠ 되돌릴 수 있게 원본을 먼저 복사해 둔다. 임베딩은 다시 만들면 되지만
  원문까지 날리면 끝이다.
"""
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
import embed_local

DB = Path(getattr(config, "MEMORY_DB", config.STATE / "memory.db"))


def main() -> int:
    dry = "--dry" in sys.argv
    if not DB.exists():
        print(f"기억 DB 가 없습니다: {DB}")
        return 1
    if not embed_local.preload():
        print("임베딩 모델을 못 올렸습니다. state/models/embed/ 를 확인하세요.")
        return 1

    con = sqlite3.connect(str(DB))
    rows = con.execute("select id, text from memories").fetchall()
    print(f"기억 {len(rows)}건")
    if dry:
        v = embed_local.embed_passage(rows[0][1]) if rows else None
        print(f"새 차원: {None if v is None else v.shape[0]} (지금 저장된 것과 다르면 교체 대상)")
        return 0

    backup = DB.with_suffix(".db.before-e5")
    if not backup.exists():
        shutil.copy2(DB, backup)
        print(f"원본 복사: {backup.name}")

    done = fail = 0
    for mid, text in rows:
        v = embed_local.embed_passage(text or "")
        if v is None:
            fail += 1
            continue
        con.execute("update memories set emb=? where id=?", (v.tobytes(), mid))
        done += 1
    con.commit()
    con.close()
    print(f"교체 {done}건 / 실패 {fail}건")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
