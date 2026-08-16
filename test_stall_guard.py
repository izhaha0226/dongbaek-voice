#!/usr/bin/env python3
"""멈춤·오탐 방지 — 2026-08-11 밤에 실제로 터진 세 가지.

  ① 재생이 안 끝나면 워커가 영영 갇힌다 → 이후 모든 답이 큐에만 쌓이고
     한마디도 안 나간다. 마이크는 멀쩡히 듣고 있어서 더 헷갈린다.
  ② 텔레그램이 포괄 사유('확인이 필요한 요청')로도 승인을 요구한다 →
     명령도 아닌 되물음에 "'진행해' 라고 답장해 주세요" 가 나간다.
  ③ 메모리 여유를 free+speculative 로만 세면 48기가 머신이 영영
     '메모리 부족' 이다 → 멀쩡한데 모델을 내린다.

셋 다 예외가 안 나서 로그만 봐서는 죽은 줄도 모른다. 그래서 테스트로 박는다.

    python test_stall_guard.py
"""
import re
import threading
import time

import config
import memory_guard
import router
import speak

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


# ─────────────────────────────────────────────────────────
# ① 재생이 안 끝나도 워커는 풀려나야 한다
# ─────────────────────────────────────────────────────────
print("\n① 재생 멈춤에서 빠져나오는가")


class _StuckPlayback:
    """영영 '재생 중' 이라고 말하는 가짜 재생 — 실제 증상 그대로."""

    def __init__(self, started: bool):
        self._started = started
        self.stopped = False

    def is_active(self) -> bool:
        return not self.stopped

    def started(self) -> bool:
        return self._started

    def stop(self) -> None:
        self.stopped = True


_base, _per, _max = (config.TTS_PLAY_BASE_SEC,
                     config.TTS_PLAY_SEC_PER_CHAR,
                     config.TTS_PLAY_MAX_SEC)
config.TTS_PLAY_BASE_SEC = 0.2
config.TTS_PLAY_SEC_PER_CHAR = 0.0
config.TTS_PLAY_MAX_SEC = 0.2
try:
    # 소리가 한 번도 안 났으면 내장 음성으로 다시 말해야 한다 (False = 폴백)
    stuck = _StuckPlayback(started=False)
    t0 = time.monotonic()
    got = speak._await_playback(stuck, "안녕하세요")
    took = time.monotonic() - t0
    check("소리 없이 멎으면 폴백을 부른다", got, False)
    check("멎은 재생을 끊었다", stuck.stopped, True)
    check("상한 안에 빠져나온다", took < 3.0, True)

    # 이미 소리가 나갔으면 다시 말하면 겹친다 (True = 폴백 없음)
    stuck2 = _StuckPlayback(started=True)
    check("소리가 나간 뒤 멎으면 다시 말하지 않는다",
          speak._await_playback(stuck2, "안녕하세요"), True)
    check("그래도 끊기는 한다", stuck2.stopped, True)
finally:
    config.TTS_PLAY_BASE_SEC = _base
    config.TTS_PLAY_SEC_PER_CHAR = _per
    config.TTS_PLAY_MAX_SEC = _max

# 상한은 말 길이를 따라가되 천장이 있어야 한다
check("긴 말일수록 더 기다린다",
      speak._play_budget("가" * 200) > speak._play_budget("가"), True)
check("천장을 넘지 않는다",
      speak._play_budget("가" * 100000) <= config.TTS_PLAY_MAX_SEC, True)

# 큐가 실제로 풀리는가 — 갇혔을 때 나던 증상이 '두 번째 말이 영영 안 나감' 이다
print("\n  큐가 풀리는가 (갇혔을 때 실제로 못 했던 것)")
_real_play = speak._play
_spoken: list[str] = []


def _fake_play(body: str) -> None:
    _spoken.append(body)


speak._play = _fake_play
try:
    speak.say("첫 마디", block=True)
    speak.say("둘째 마디", block=True)
    check("두 마디가 다 나갔다", _spoken, ["첫 마디", "둘째 마디"])
    check("말이 끝나면 말하는 중이 풀린다", speak.is_speaking(), False)
finally:
    speak._play = _real_play

# ─────────────────────────────────────────────────────────
# ② 텔레그램이 되물음에 승인을 요구하면 안 된다
# ─────────────────────────────────────────────────────────
print("\n② 텔레그램 승인 게이트")

import telegram_bridge  # noqa: E402  (config 를 먼저 읽어야 해서 여기서)

_GATE = re.compile(r"진행해' 라고 답장")


def _reply(text: str) -> str:
    """실제 실행까지 가지 않게 handle 을 막아두고 게이트만 잰다."""
    import dongbaek

    real = dongbaek.handle
    dongbaek.handle = lambda *a, **k: "(처리됨)"
    try:
        return telegram_bridge.handle_command(text, chat_id=1, pending={})
    finally:
        dongbaek.handle = real


check("불평에 승인을 묻지 않는다",
      bool(_GATE.search(_reply("아니 지금 맥미니가 48기간데 그게 안된다고???"))),
      False)
check("되물음에 승인을 묻지 않는다",
      bool(_GATE.search(_reply("뭔가 계속 불안정하게 움직인다"))), False)
# 명시적 위험은 그대로 막혀야 한다 — 여기가 무너지면 승인 없이 배포까지 간다
check("명시적 위험은 여전히 막는다",
      bool(_GATE.search(_reply("지금 바로 배포해"))), True)
check("포괄 사유는 그대로 남아 있다 (음성 경로가 쓴다)",
      router.danger_hit("아니 지금 맥미니가 48기간데 그게 안된다고???"),
      router.SAFE_ONLY_REASON)

# ─────────────────────────────────────────────────────────
# ③ 48기가 머신을 '부족' 이라 하면 안 된다
# ─────────────────────────────────────────────────────────
print("\n③ 메모리 여유 계산")

s = memory_guard.snapshot()
check("총량을 읽었다", s["total_gb"] > 1, True)
check("여유가 총량을 넘지 않는다", s["free_gb"] <= s["total_gb"] + 0.1, True)
# 파일 캐시를 '쓰는 중' 으로 세던 시절엔 여유가 총량의 10% 아래로 나왔다.
# 압박이 '보통' 인데 여유가 그렇게 적게 잡히면 계산이 틀린 것이다.
if s["pressure"] < 2:
    check("압박이 보통인데 여유가 총량의 15% 미만이면 계산이 틀린 것",
          s["free_gb"] >= s["total_gb"] * 0.15, True)
    check("압박이 보통이면 회수하지 않는다", memory_guard.needs_reclaim(), False)
else:
    print("  · 지금 실제로 메모리 압박 상태라 ③ 판정은 건너뜀")

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과")
