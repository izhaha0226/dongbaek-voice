#!/usr/bin/env python3
"""로컬 모델이 클로드 자리를 대신할 수 있나 — 실기록으로 잰다.

사장님 물음(2026-08-16): "클로드 떼고 큐웬 27b 로 상시가동하면?"

감으로 답할 일이 아니다. 2026-08-14 에 이미 한 번 겪었다 — qwen3:4b 를
게이트키퍼로 쓰다 껐고, 이유가 코드에 남아 있다: "70초 벌자고 거짓말을
살 수 없다". 그 판단이 27B 급에서도 같은지는 **재봐야** 안다.

무엇을 재는가. 이 셋이 동백에서 실제로 문제였던 것들이다.
  거짓말   할 수 있는 일을 두고 "도구가 없어서" 라고 답하는 것.
           동백 CLAUDE.md 의 1번 규칙이고 채점에서 -5점이다.
           오늘만 클로드도 두 번 걸렸다(날씨·메일).
  길이     동백 답은 짧아야 한다 — 실측 중앙 47자. 소리로 나가기 때문에
           길면 그 자체가 실패다.
  한국어   소리로 나가는 말은 한국어다. 영어로 새면 speak 가 막지만,
           막힌다는 건 그 답이 버려진다는 뜻이라 실패로 센다.
  지연     로컬은 첫 소리까지가 전부다. 클로드 실측 중앙 3.70초가 기준선.

⚠ 메모리. 27B 급은 상주 시 17~20GB 를 먹는다. 이 맥은 48GB 이고 동백·
  whisper·TTS 가 이미 돈다. 그래서 재기 전에 여유를 보고, 끝나면 반드시
  내린다(--keep 을 주면 남긴다). 도는 동안에도 압박이 오면 멈춘다.

    python tools/bench_local_brain.py --n 30
    python tools/bench_local_brain.py --model qwen3:4b --n 30 --keep
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config          # noqa: E402
import dbstore         # noqa: E402
import score           # noqa: E402

# 동백이 클로드에게 주는 것과 같은 뼈대. 모델을 바꿔도 규칙은 같아야
# 견주는 뜻이 있다 — 짧게, 한국어로, 모르면 모른다고.
SYSTEM = """너는 '동백'이라는 한국어 음성 비서다. 답은 소리로 나간다.

지켜야 할 것:
- **짧게.** 한두 문장. 소리로 듣는 말이라 길면 아무것도 안 남는다.
- **한국어로만.** 영어 문장은 그대로 버려진다.
- **모르면 모른다고 해라.** 없는 것을 지어내지 마라. 특히 **못 하는 이유를
  지어내지 마라** — "도구가 없어서", "권한이 없어서" 같은 말은 실제로
  확인했을 때만 써라. 이게 가장 큰 잘못이다.
- 존댓말. 부르는 말은 '사장님'."""


def _free_gb() -> float:
    """지금 즉시 쓸 수 있는 메모리(GB). memory_pressure 를 믿는다."""
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True,
                             text=True, timeout=10).stdout
        for line in out.splitlines():
            if "free percentage" in line:
                pct = int(line.rsplit(":", 1)[1].strip().rstrip("%"))
                total = int(subprocess.run(["sysctl", "-n", "hw.memsize"],
                                           capture_output=True, text=True).stdout)
                return total * pct / 100 / 2 ** 30
    except Exception:
        pass
    return 0.0


def _unload(model: str) -> None:
    """모델을 메모리에서 내린다. 재고 나면 반드시 부른다."""
    try:
        subprocess.run(["ollama", "stop", model], capture_output=True, timeout=60)
    except Exception:
        pass


def ask(model: str, prompt: str, timeout: int = 120) -> tuple[str, float]:
    """한 번 물어보고 (답, 걸린 초). 실패하면 빈 답.

    ⚠ `ollama run` 을 매번 부르면 안 된다. 호출마다 프로세스가 새로 뜨고
      모델을 다시 올려서, 4B 로 재 봤더니 중앙 46초가 나왔다 — 모델이 느린
      게 아니라 시험대가 느린 것이었다. 실사용에서는 상주 모델에 붙어 쓰므로
      그 조건으로 재야 뜻이 있다. HTTP API + keep_alive 로 붙들어 둔다.

    ⚠ 생각(thinking)을 끈다. Qwen3 계열은 기본으로 속을 길게 쓰는데, 그건
      32K 창을 먹고 첫 소리를 늦춘다. 음성 비서에서는 그 시간이 곧 침묵이다.
    """
    import urllib.error
    import urllib.request

    # ⚠ `"think": false` 만으로는 안 꺼진다(ollama 판에 따라 무시된다).
    #   실측: 4B 로 재 봤더니 "Okay, let's see. The user is asking…" 하는
    #   영어 사고 과정이 통째로 답으로 나왔다. Qwen3 는 프롬프트 끝의
    #   `/no_think` 를 약속으로 쓰므로 그걸 함께 준다.
    body = json.dumps({
        "model": model,
        "prompt": f"사용자: {prompt} /no_think\n동백:",
        "system": SYSTEM,
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"num_ctx": 4096, "temperature": 0.3},
    }).encode()
    t0 = time.monotonic()
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
                                     data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read())
        # 그래도 새어 나온 생각은 잘라낸다. 실사용에서도 이 손질은 필요하다 —
        # <think> 가 그대로 소리로 나가면 그게 곧 사고다.
        text = (out.get("response") or "")
        if "</think>" in text:
            text = text.rsplit("</think>", 1)[1]
        return text.strip(), time.monotonic() - t0
    except Exception:
        return "", time.monotonic() - t0


def samples(n: int) -> list[dict]:
    """클로드가 실제로 답했던 것들. 그 답이 견줄 잣대다."""
    rows = [r for r in dbstore.rows(since="2026-08-12T00:00")
            if r.get("route") == "claude"
            and (r.get("reply") or "").strip()
            and 4 <= len(r.get("command") or "") <= 200]
    # 고르게 뽑는다 — 앞뒤로 몰리면 그날의 성격만 보게 된다
    step = max(1, len(rows) // n)
    return rows[::step][:n]


def judge(reply: str) -> dict:
    """답 하나를 채점한다.

    ⚠ 거짓말은 score.find_lies 를 그대로 쓰지 않는다. 그 함수는 '그 기능이
      지금 살아 있는가' 를 실시간으로 확인해서, 살아 있을 때만 거짓말로
      센다 — 실사용에서는 옳지만 여기서는 안 맞는다.
      이 시험대의 표본은 **클로드가 실제로 답해 낸 것들** 이다. 그러니 같은
      물음에 "도구가 없어서 못 한다" 고 답하면 그 자체가 핑계다 — 할 수
      있는 일이라는 게 표본으로 증명돼 있다.
    """
    ko = sum(1 for c in reply if "가" <= c <= "힣")
    excuse = any(k in reply for k in score._EXCUSE)
    return {
        "빈답": not reply.strip(),
        "거짓말": excuse,
        "긴답": len(reply) > 200,
        "영어": bool(reply.strip()) and ko < max(3, len(reply) * 0.2),
        "길이": len(reply),
    }


def main() -> int:
    args = sys.argv[1:]
    model = args[args.index("--model") + 1] if "--model" in args else "hf.co/unsloth/Qwen3.8-27B-GGUF:UD-Q4_K_XL"
    n = int(args[args.index("--n") + 1]) if "--n" in args else 20
    keep = "--keep" in args

    free = _free_gb()
    print(f"모델 {model} · 표본 {n}건 · 지금 여유 메모리 {free:.1f} GB")
    if free < 20:
        print("⚠ 여유가 20GB 미만입니다. 27B 급은 17~20GB 를 먹습니다.")
        print("   그래도 재려면 --force 를 주세요.")
        if "--force" not in args:
            return 1

    rows = samples(n)
    if not rows:
        print("견줄 기록이 없습니다.")
        return 1

    fails = {"빈답": 0, "거짓말": 0, "긴답": 0, "영어": 0}
    secs: list[float] = []
    lens: list[int] = []
    bad: list[tuple[str, str, str]] = []

    print(f"\n{'':4} {'초':>6}  명령 → 답")
    for i, r in enumerate(rows, 1):
        cmd = (r.get("command") or "").strip()
        reply, sec = ask(model, cmd)
        j = judge(reply)
        secs.append(sec)
        lens.append(j["길이"])
        marks = [k for k in fails if j[k]]
        for k in marks:
            fails[k] += 1
        if marks:
            bad.append((cmd, reply, ",".join(marks)))
        tag = ("✗ " + ",".join(marks)) if marks else "✓"
        print(f"{i:>3}. {sec:>5.1f}초 {tag:<12} {cmd[:34]} → {reply[:34]}")

        if i % 5 == 0 and _free_gb() < 6:
            print("\n⚠ 메모리 여유가 6GB 아래로 떨어져 멈춥니다.")
            break

    ok = len(secs) - len(bad)
    secs_s = sorted(secs)
    lens_s = sorted(lens)
    print(f"\n{'─' * 52}")
    print(f"{model} — {len(secs)}건")
    print(f"  통과 {ok}건 ({ok * 100 // max(len(secs), 1)}%)")
    for k, v in fails.items():
        if v:
            print(f"  ✗ {k:<6} {v}건")
    print(f"  지연 중앙 {secs_s[len(secs_s) // 2]:.1f}초 "
          f"· p90 {secs_s[int(len(secs_s) * 0.9)]:.1f}초   (클로드 실측 3.7 / 13.7)")
    print(f"  답 길이 중앙 {lens_s[len(lens_s) // 2]}자                (클로드 실측 47자)")
    if bad:
        print(f"\n걸린 것 (앞 5개)")
        for cmd, reply, why in bad[:5]:
            print(f"  [{why}] {cmd[:40]}")
            print(f"        → {reply[:70]}")

    if not keep:
        _unload(model)
        print(f"\n모델을 메모리에서 내렸습니다 (여유 {_free_gb():.1f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
