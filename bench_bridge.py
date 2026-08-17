#!/usr/bin/env python3
"""브릿지가 얼마나 빨리 답하기 시작하는지 잰다.

    .venv/bin/python bench_bridge.py            # 둘 다
    .venv/bin/python bench_bridge.py --bridge   # 첫 글자까지만
    .venv/bin/python bench_bridge.py --sound    # 첫 소리까지만

두 가지를 따로 잰다. 헷갈리면 안 되는 게, 브릿지가 빨라진 만큼 소리가
빨라지지는 않는다.

  ① 첫 글자까지 — 브릿지가 첫 글자를 준 시각.
     CLI 는 답을 다 만들 때까지 아무것도 안 주므로 첫 글자 = 총 소요다.

  ② 첫 소리까지 — 실제로 재생이 시작된 시각. 사용자가 겪는 것.
     문장 하나가 완성돼야 말을 시작하므로(speak._STREAM_MIN) ①보다 늦다.
     답변이 짧으면 쪼갤 것도 없어서 이득이 줄어든다 — CLAUDE.md 가
     '3문장 이내' 를 지시하므로 동백 답변은 원래 짧다.

⚠ 실제로 Claude 를 여러 번 부른다. 공짜가 아니다 (실측 회당 $0.05~0.13).

⚠ 한쪽을 몰아서 돌리면 안 된다. 프롬프트 캐시는 세션이 아니라 프롬프트
  앞부분으로 잡히므로, 나중에 도는 쪽이 앞사람이 데워둔 캐시를 그냥 읽는다.
  실제로 그렇게 재서 SDK 가 비용 41% 싸게 나온 적이 있다 — 순서 탓이었다.
  번갈아 돌려서 양쪽이 같은 캐시 상태를 겪게 한다.
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import statistics
import sys
import time

import bridge
import config
import speak

RUNS = 3

# ① 은 도구를 안 쓰는 짧은 질문으로 잰다. 도구를 쓰면 모델이 매번 다른 횟수로
#    탐색해서 지연 차이에 그게 섞인다.
Q_BRIDGE = "'하나 둘 셋 넷 다섯' 이라고만 답해줘. 다른 말은 붙이지 마."
# ② 는 문장이 여럿인 현실적인 답변이어야 한다. 마침표가 없으면 쪼갤 자리가
#    없어서 스트리밍이 아무 일도 안 한 것과 같아진다.
Q_SOUND = "커피랑 녹차 중에 아침에 뭐가 나은지 이유까지 짧게 말해줘"


# ─────────────────────────────────────────────────────────
# ① 첫 글자까지 — 브릿지 수준
# ─────────────────────────────────────────────────────────
def bridge_cli():
    t0 = time.monotonic()
    _, meta = bridge._ask_cli(Q_BRIDGE)
    total = time.monotonic() - t0
    return total, total, meta


def bridge_sdk():
    import bridge_sdk

    t0 = time.monotonic()
    first = None

    def on_text(_chunk):
        nonlocal first
        if first is None:
            first = time.monotonic() - t0

    _, meta = bridge_sdk.ask(Q_BRIDGE, on_text=on_text)
    return first, time.monotonic() - t0, meta


# ─────────────────────────────────────────────────────────
# ② 첫 소리까지 — 실행기가 하는 일 그대로 (Ack 만 뺀다)
# ─────────────────────────────────────────────────────────
# Ack 는 0.8초에 "네, 확인하고 있습니다" 를 내므로 답변 소리를 가린다.
_STARTS: list[float] = []


def _fake_play(_body: str) -> None:
    _STARTS.append(time.monotonic())
    time.sleep(0.05)


def sound(mode: str, stream: bool):
    import dongbaek

    config.BRIDGE, config.STREAM_REPLY = mode, stream
    config.CODE_EDIT_ENABLED = False        # 스냅샷은 이 측정과 무관하다
    bridge.reset_session()
    _STARTS.clear()

    t0 = time.monotonic()
    sp = speak.Stream(lead="홍길동님 답변드리겠습니다. ") if stream else None
    reply = dongbaek.handle(Q_SOUND, confirm=lambda c, h: True, source="cli", speaker=sp)
    if sp:
        sp.close()
        if not sp.spoke() and reply:
            speak.say(reply, block=False)
    else:
        speak.say(reply, block=False)
    while speak.is_speaking():
        time.sleep(0.02)

    first = (_STARTS[0] - t0) if _STARTS else float("nan")
    return first, time.monotonic() - t0, len(_STARTS)


def run_bridge():
    print(f"\n① 첫 글자까지 — 브릿지 수준\n   질문: {Q_BRIDGE}\n")
    cli, sdk = [], []
    for i in range(RUNS):
        bridge.reset_session()
        f, t, m = bridge_cli()
        cli.append(f)
        print(f"   CLI #{i+1}: 첫글자 {f:5.2f}s · 총 {t:5.2f}s · "
              f"문맥 {m['cache_write'] + m['cache_read']:>6,}")
        bridge.reset_session()
        f, t, m = bridge_sdk()
        sdk.append(f)
        print(f"   SDK #{i+1}: 첫글자 {f:5.2f}s · 총 {t:5.2f}s · "
              f"문맥 {m['cache_write'] + m['cache_read']:>6,}")
    c, s = statistics.median(cli), statistics.median(sdk)
    print(f"\n   중앙값: {c:.2f}s → {s:.2f}s  ({(c - s) / c * 100:+.0f}%)")


def run_sound():
    speak._play = _fake_play          # type: ignore[assignment]
    print(f"\n② 첫 소리까지 — 사용자가 겪는 것\n   질문: {Q_SOUND}\n")
    cli, sdk = [], []
    for i in range(RUNS):
        f, t, n = sound("cli", False)
        cli.append(f)
        print(f"   CLI #{i+1}: 첫소리 {f:5.2f}s · 총 {t:5.2f}s · 조각 {n}개")
        f, t, n = sound("sdk", True)
        sdk.append(f)
        print(f"   SDK #{i+1}: 첫소리 {f:5.2f}s · 총 {t:5.2f}s · 조각 {n}개")
    c, s = statistics.median(cli), statistics.median(sdk)
    print(f"\n   중앙값: {c:.2f}s → {s:.2f}s  ({(c - s) / c * 100:+.0f}%)")


if __name__ == "__main__":
    want = set(sys.argv[1:]) or {"--bridge", "--sound"}
    if "--bridge" in want:
        run_bridge()
    if "--sound" in want:
        run_sound()
