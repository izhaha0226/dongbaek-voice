#!/usr/bin/env python3
"""교정표 자동 학습 — 반복되는 받아쓰기 오기를 스스로 찾아 규칙으로 만든다.

왜 필요한가. whisper 는 고유명사를 매번 다르게 적는다. '한빛리조트' 하나가
한 세션에서 '용탐 밸리'·'용전 밸리'·'영팁밸리' 로 갈렸다. 사람이 그 갈래를
미리 다 적을 수는 없고, 틀린 걸 알아채는 것도 매번 사장님 몫이었다.

어떻게 배우는가. 자유롭게 짝을 지으면 안 된다 — '멀쩡한 말'이 '아는 이름'으로
바뀌는 사고가 못 알아듣는 것보다 나쁘다. 그래서 세 가지를 모두 만족할 때만
규칙이 된다:

  1) config.TERM_VOCAB 의 '아는 이름' 에 충분히 닮았다 (TERM_LEARN_RATIO)
  2) 서로 다른 발화에서 반복됐다 (TERM_LEARN_MIN_COUNT)
  3) 두 이름 사이에서 헷갈리지 않는다 (TERM_LEARN_MARGIN)

3번은 남 지문 코호트와 같은 원리다. 절대 점수만 보면 흔들리지만,
'1등과 2등의 격차' 는 흔들리지 않는다.

배운 규칙은 코드가 아니라 state/term_fixes_learned.json 에 쌓이고,
config 가 그걸 읽어 TERM_FIXES 뒤에 붙인다 — 손으로 적은 규칙이 항상 먼저다.

    python term_learn.py            # 무엇을 배울지 보여만 준다 (안전)
    python term_learn.py --apply    # 실제로 규칙을 쌓는다
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

import config

# 한글 낱말 덩어리. 조사가 붙은 채로 들어오므로 뒤에서 떼어낸다.
_WORD = re.compile(r"[가-힣]{2,}")
# 오기 뒤에 붙는 조사. "홍배가" 의 '가' 까지 규칙에 넣으면 "홍배는" 은 못 잡는다.
# 겹쳐 붙는다 ("동백이가", "레일웨이에서는") — 그래서 한 번이 아니라 끝까지 뗀다.
_PARTICLE = re.compile(
    r"(에게서|한테서|이랑|에서|으로|까지|부터|처럼|보다|마다|조차|라도|하고"
    r"|이|가|은|는|을|를|아|야|에|의|도|만|께|랑|로|와|과|한테|에게)$")

# 이름과 닮아 보여도 배우면 안 되는 흔한 말. 조사를 뗀 뒤 이것과 같으면 버린다.
# ('동백' 과 '동생' 은 0.5, '클로드' 와 '클라우드' 는 0.86 으로 닮았다)
_STOP = {
    "동생", "동네", "동안", "동시", "동의", "클라우드", "메일", "일정",
    "그거", "저거", "이거", "그럼", "그때", "지금", "오늘", "내일",
}


def _strip_particle(w: str, vocab: list[str] | None = None) -> str:
    """조사를 끝까지 뗀다. 두 글자 아래로 줄어들면 거기서 멈춘다 (이름이 사라진다).

    ⚠ 아는 이름에 닿으면 거기서 멈춘다. '이' 는 조사이면서 이름의 끝글자이기도
      해서, 계속 떼면 '레일웨이에서' 가 '레일웨' 까지 깎인다 — 멀쩡히 들은 말을
      오기로 만들어 놓고 그걸 배우는 꼴이다.
    """
    known = set(vocab if vocab is not None else config.TERM_VOCAB)
    while True:
        if w in known:
            return w
        cut = _PARTICLE.sub("", w)
        if cut == w or len(cut) < 2:
            return w
        w = cut


def _is_noise(word: str, right: str) -> str | None:
    """배우면 안 되는 짝인가. 이유를 돌려주고, 괜찮으면 None.

    실제 기록에서 걸러낸 두 가지 사고를 막는다.
    """
    # ① 이름이 통째로 들어 있으면 잘못 들은 게 아니다 — 바로 듣고 뭔가 붙였을 뿐.
    #    '동백동'(사장님 동네)을 '동백'으로 바꾸면 주소가 뭉개진다. 실제 후보였다.
    if right in word:
        return f"'{right}' 를 이미 제대로 들었다"
    # ② 이름의 앞토막인데 너무 짧으면 그냥 그 말일 수 있다.
    #    '한빛' 두 글자를 '한빛기획' 으로 바꾸는 건 넘겨짚는 것이다.
    if word in right and len(word) <= 2:
        return "너무 짧은 앞토막"
    return None


def _heard_lines(days: int) -> list[str]:
    """최근 며칠치 받아쓰기 원문. 큰 파일이므로 한 줄씩 흘려 읽는다."""
    import dbstore

    since = datetime.now() - timedelta(days=days)
    out: list[str] = []
    for row in dbstore.rows(since=since.isoformat()):
        heard = row.get("heard") or ""
        if heard:
            out.append(heard)
    return out


def _already_fixed(word: str, known: list[tuple[str, str]]) -> bool:
    """손으로 적은 규칙이 이미 고치고 있는 말인가."""
    for pattern, right in known:
        try:
            if re.search(pattern, word):
                return True
        except re.error:
            continue
    return False


def mine(days: int = 14) -> tuple[list[dict], list[dict]]:
    """(규칙이 될 것, 아직 증거가 모자란 것) 을 돌려준다."""
    vocab = [v for v in config.TERM_VOCAB if v]
    manual = list(config.TERM_FIXES)
    # 오기 → {닮은 이름: 유사도}, 그리고 몇 번 나왔는지
    hits: dict[str, dict[str, float]] = defaultdict(dict)
    counts: dict[str, int] = defaultdict(int)

    for line in _heard_lines(days):
        for raw in _WORD.findall(line):
            word = _strip_particle(raw)
            if word in _STOP or len(word) < 2:
                continue
            if word in vocab:                   # 바르게 들린 것
                continue
            if _already_fixed(word, manual):    # 이미 고치고 있다
                continue
            scored = {
                v: difflib.SequenceMatcher(None, word, v).ratio() for v in vocab
            }
            best = max(scored, key=scored.get)
            if scored[best] < config.TERM_LEARN_RATIO:
                continue
            hits[word] = scored
            counts[word] += 1

    rules: list[dict] = []
    weak: list[dict] = []
    for word, scored in hits.items():
        ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)
        best, best_score = ranked[0]
        second = ranked[1][1] if len(ranked) > 1 else -1.0
        row = {
            "wrong": word,
            "right": best,
            "score": round(best_score, 3),
            "count": counts[word],
        }
        noise = _is_noise(word, best)
        if noise:
            row["skip"] = noise
            weak.append(row)
        # 두 이름 사이에서 헷갈리면 배우지 않는다 — 어느 쪽인지 모른다는 뜻이다
        elif best_score - second < config.TERM_LEARN_MARGIN:
            row["skip"] = f"{ranked[1][0]} 와도 비슷함"
            weak.append(row)
        elif counts[word] < config.TERM_LEARN_MIN_COUNT:
            row["skip"] = "반복이 모자람"
            weak.append(row)
        else:
            rules.append(row)
    rules.sort(key=lambda r: -r["count"])
    weak.sort(key=lambda r: -r["count"])
    return rules, weak


def apply(rules: list[dict], weak: list[dict]) -> int:
    """배운 규칙을 파일에 쌓는다. 이미 있는 규칙은 건드리지 않는다."""
    try:
        cur = json.loads(config.TERM_LEARNED_FILE.read_text(encoding="utf-8"))
    except Exception:
        cur = {"rules": []}
    have = {r["wrong"] for r in cur.get("rules", []) if "wrong" in r}

    added = 0
    for r in rules:
        if r["wrong"] in have:
            continue
        cur.setdefault("rules", []).append({
            # 낱말 통째로일 때만 바꾼다. 부분일치로 두면 긴 말 속에서 터진다.
            "pattern": rf"\b{re.escape(r['wrong'])}\b",
            "right": r["right"],
            "wrong": r["wrong"],
            "count": r["count"],
            "score": r["score"],
            "learned_at": datetime.now().isoformat(timespec="seconds"),
        })
        added += 1
    cur["updated_at"] = datetime.now().isoformat(timespec="seconds")
    config.TERM_LEARNED_FILE.write_text(
        json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    config.TERM_CANDIDATES_FILE.write_text(
        json.dumps({"candidates": weak,
                    "updated_at": cur["updated_at"]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return added


def main() -> int:
    if not config.TERM_LEARN_ENABLED:
        print("교정표 학습이 꺼져 있습니다.")
        return 0
    rules, weak = mine()
    if not rules and not weak:
        print("새로 배울 오기가 없습니다.")
        return 0
    for r in rules:
        print(f"  마바: {r['wrong']} → {r['right']} ({r['count']}회, 유사도 {r['score']})")
    for r in weak:
        print(f"  보류: {r['wrong']} → {r['right']} ({r['count']}회, {r['skip']})")
    if "--apply" in sys.argv:
        n = apply(rules, weak)
        print(f"규칙 {n}개를 쌓았습니다.")
    else:
        print("(보여주기만 했습니다. 실제로 쌓으려면 --apply)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
