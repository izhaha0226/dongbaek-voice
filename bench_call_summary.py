#!/usr/bin/env python3
"""통화 정리를 로컬 모델에 맡겨도 되는가 — 지난 통화 원문으로 A/B.

사장님 지시(2026-08-16): "통화정리까지 넘겨서 테스트해보자".

⚠ 바로 켜지 않는다. 통화 요약은 **나중에 사실로 믿고 움직이는 문서** 라
  지어내면 캘린더 쓰레기보다 나쁘다. 다행히 견줄 것이 이미 있다 —
  옵시디언 통화기록 32건에 '들린 대로' 원문이 그대로 남아 있고, 그 위에
  클로드가 만든 요약이 붙어 있다. 같은 원문을 30B 에 다시 물으면
  **같은 입력·다른 두뇌** 의 정직한 비교가 된다.

무엇을 보는가 (통화 요약에서 실제로 무서운 것들):
  절 구성   시킨 절이 다 있나. 없으면 뒤에서 파싱이 깨진다
  지어냄    원문에 없는 고유명사·숫자가 요약에 나오는가 ← 가장 무섭다
  길이      소리로 읽을 첫 줄이 나오는가
  시간      클로드는 10~60초 걸린다. 로컬이 더 느리면 값어치가 없다

    python tools/bench_call_summary.py            # 3건
    python tools/bench_call_summary.py --n 6
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import call_notes  # noqa: E402
import config      # noqa: E402

WIKI = call_notes.WIKI_DIR
MODEL = getattr(config, "GATEKEEPER_MODEL", "hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL")


def past_calls(n: int) -> list[tuple[Path, str, list[str]]]:
    """(파일, 클로드 요약, 들린 원문). 원문이 넉넉한 것만 고른다."""
    out = []
    for p in sorted(WIKI.glob("*.md"), reverse=True):
        body = p.read_text(encoding="utf-8")
        if "## 들린 대로" not in body:
            continue
        head, raw = body.split("## 들린 대로", 1)
        lines = [re.sub(r"^- `\d\d:\d\d:\d\d` ", "", l).strip()
                 for l in raw.splitlines() if l.startswith("- `")]
        if len(lines) < 12:
            continue
        summary = head.split("## 요약", 1)[-1].strip() if "## 요약" in head else ""
        out.append((p, summary, lines))
        if len(out) >= n:
            break
    return out


def ask_local(prompt: str, timeout: int = 180) -> tuple[str, float]:
    body = json.dumps({
        "model": MODEL,
        "prompt": prompt + " /no_think",
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        # 통화는 길다. 32K 를 다 쓰되 답은 짧게 — 요약이 원문만큼 길면 뜻이 없다.
        "options": {"num_ctx": 32768, "num_predict": 900, "temperature": 0.3},
    }).encode()
    t0 = time.monotonic()
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
                                     data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = json.loads(r.read()).get("response") or ""
        if "</think>" in txt:
            txt = txt.rsplit("</think>", 1)[1]
        return txt.strip(), time.monotonic() - t0
    except Exception as e:
        return f"(실패: {type(e).__name__})", time.monotonic() - t0


# 원문에 없는 고유명사가 요약에 나오면 지어낸 것이다. 두 글자 이상 한글
# 낱말만 본다 — 한 글자는 우연이 너무 잦고, 조사가 붙어 흔들린다.
_WORD = re.compile(r"[가-힣]{2,}")
_COMMON = set("""그리고 그래서 하지만 그런데 이렇게 저렇게 우리 저희 오늘 내일 어제
말씀 얘기 이야기 관련 확인 진행 준비 정리 요약 내용 부분 경우 대해 대한 통해 위해
있습니다 없습니다 합니다 했습니다 됩니다 같습니다 사장님 동백 회의 통화 미팅 일정
없음 논의 결정 사항 다음 약속 해야 필요 가능 이번 다시 먼저 지금 나중 서로 각각""".split())


def invented(summary: str, source: str) -> list[str]:
    src = source.replace(" ", "")
    bad = []
    for w in set(_WORD.findall(summary)):
        if w in _COMMON or len(w) < 3:
            continue
        if w not in src:
            bad.append(w)
    return sorted(bad)[:8]


def main() -> int:
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 3
    calls = past_calls(n)
    if not calls:
        print("견줄 통화 기록이 없습니다.")
        return 1
    print(f"모델 {MODEL} · 지난 통화 {len(calls)}건으로 A/B\n")

    for p, claude_sum, lines in calls:
        text = "\n".join(lines[:400])[:14000]
        prompt = call_notes._CALL_SHAPE + text
        got, sec = ask_local(prompt)
        src = "\n".join(lines)

        secs = [l for l in got.splitlines() if l.startswith("## ")]
        inv = invented(got, src)
        inv_claude = invented(claude_sum, src)

        print(f"── {p.name}  (원문 {len(lines)}조각)")
        print(f"   로컬 {sec:.0f}초 · 절 {len(secs)}개 {[s[3:] for s in secs]}")
        print(f"   지어낸 낱말  로컬 {len(inv)}개 {inv[:5]}")
        print(f"                클로드 {len(inv_claude)}개 {inv_claude[:5]}")
        first = next((l for l in got.splitlines() if l.strip()
                      and not l.startswith("#")), "")
        print(f"   로컬 첫 줄   {first[:74]}")
        cfirst = next((l for l in claude_sum.splitlines() if l.strip()
                       and not l.startswith("#")), "")
        print(f"   클로드 첫 줄 {cfirst[:74]}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
