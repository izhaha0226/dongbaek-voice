#!/usr/bin/env python3
"""스트리밍 답변 검증 — 문장이 완성되는 대로 말하되, 서로 끊지 않는다.

여기서 지키려는 것 셋:

  ① 이어지는 조각이 앞 조각을 끊지 않는다.
     say(PRIORITY_REPLY) 는 원래 '대기열을 비우고 지금 나가는 소리를 끊는' 다.
     그게 겹침을 구조적으로 막는 장치인데, 스트리밍에는 그대로 쓰면 안 된다 —
     두 번째 문장이 첫 문장을 죽여서 마지막 문장만 들린다.

  ② 자기 목소리 필터가 답변 전체를 기억한다.
     recent_text() 가 마지막 조각만 담으면, 앞 문장의 에코가 마이크로
     되돌아왔을 때 사용자 말로 착각해 그대로 명령으로 실행한다.

  ③ 두 번 말하지 않는다.
     스트리밍이 이미 말했으면 실행기가 또 말하면 안 된다.

    python test_stream_reply.py
"""
import threading
import time

import config
import speak

FAIL = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAIL.append(f"{label}: 기대={want!r} 실제={got!r}")
    print(f"  {'✓' if ok else '✗'} {label}")


def truthy(label, got):
    ok = bool(got)
    if not ok:
        FAIL.append(f"{label}: 거짓 (값={got!r})")
    print(f"  {'✓' if ok else '✗'} {label}")


# ─────────────────────────────────────────────────────────
# 실제 소리 없이 워커만 돌린다
# ─────────────────────────────────────────────────────────
PLAYED: list[str] = []
_play_lock = threading.Lock()


def _fake_play(body: str) -> None:
    with _play_lock:
        PLAYED.append(body)
    # 재생에 시간이 걸려야 '끊기' 가 관측된다. 짧게라도 걸어둔다.
    for _ in range(10):
        if speak._interrupt.is_set():
            return
        time.sleep(0.01)


speak._play = _fake_play          # type: ignore[assignment]


def drain(timeout=3.0):
    """큐가 빌 때까지 기다린다."""
    end = time.monotonic() + timeout
    while speak.is_speaking() and time.monotonic() < end:
        time.sleep(0.02)
    time.sleep(0.05)


def spoken(s: str) -> str:
    """say() 가 실제로 재생하는 형태.

    ⚠ clean() 만으로는 모자란다. say() 는 그 뒤에 말끝을 한 문체로 맞추므로
      (voice_style — 로컬·큐웬·클로드가 한 목소리로 나가게 하는 장치)
      기대값도 같은 길을 거쳐야 한다. 여기서 clean() 만 쓰면 이 테스트가
      순서가 아니라 '말투가 그대로인가' 를 검사하게 된다.
    """
    import voice_style

    return voice_style.apply(speak.clean(s))


print("\n[1] 이어지는 조각은 앞말을 끊지 않는다  ← 핵심")
PLAYED.clear()
speak.say("첫 문장입니다.", block=False)
speak.say("둘째 문장입니다.", block=False, follow=True)
speak.say("셋째 문장입니다.", block=False, follow=True)
drain()
check("세 문장이 전부 재생됐다", len(PLAYED), 3)
# clean() 이 말끝 마침표를 뗀다. 재생되는 것은 그 결과물이므로 같은 기준으로 본다.
check("순서가 유지됐다", PLAYED,
      [spoken(s) for s in ("첫 문장입니다.", "둘째 문장입니다.", "셋째 문장입니다.")])

print("\n[2] follow 없는 새 답변도 앞 답변을 끊지 않는다 — 하나씩, 끝까지")
PLAYED.clear()
speak.say("먼저 온 답변입니다.", block=False)
time.sleep(0.02)
speak.say("새 답변입니다.", block=False)      # 답변끼리는 줄을 선다
drain()
check("둘 다 순서대로 재생됐다", PLAYED,
      [spoken(s) for s in ("먼저 온 답변입니다.", "새 답변입니다.")])

print("\n[3] 자기 목소리 필터가 답변 전체를 기억한다")
PLAYED.clear()
speak.say("일정은 세 건 있습니다.", block=False)
drain()
speak.say("화요일이 가장 가깝습니다.", block=False, follow=True)
drain()
recent = speak.recent_text()
truthy("앞 문장이 남아 있다", "일정은 세 건" in recent)
truthy("뒤 문장도 있다", "화요일" in recent)
truthy("무한정 자라지 않는다", len(recent) <= speak._RECENT_MAX)

print("\n[4] Stream — 문장 경계에서 끊어 말한다")
sent: list[tuple[str, bool]] = []
_real_say = speak.say
speak.say = lambda text, **kw: sent.append((text, bool(kw.get("follow"))))  # type: ignore[assignment]
try:
    s = speak.Stream(lead="홍길동님 답변드리겠습니다. ")
    for chunk in ["일정은 ", "세 건 있", "습니다. 화요일 ", "오후 두 시입니다."]:
        s.feed(chunk)
    s.add("두 파일을 고쳤습니다.")
    spoken = s.close()
finally:
    speak.say = _real_say          # type: ignore[assignment]

check("첫 조각만 follow 아님 (새 답변 신호 — 필러 정리용)",
      [f for _, f in sent], [False, True, True])
truthy("호칭이 첫 조각에 붙었다", sent[0][0].startswith("홍길동님"))
truthy("호칭만 따로 나가지 않았다", "일정은" in sent[0][0])
truthy("덧붙인 안내가 이어졌다", sent[-1][0].strip() == "두 파일을 고쳤습니다.")
check("말한 전부를 돌려준다", spoken.replace("  ", " ").strip(),
      "홍길동님 답변드리겠습니다. 일정은 세 건 있습니다. 화요일 오후 두 시입니다. "
      "두 파일을 고쳤습니다.".strip())

print("\n[5] 짧은 조각은 혼자 안 내보낸다 (뚝뚝 끊기는 것 방지)")
sent.clear()
speak.say = lambda text, **kw: sent.append((text, bool(kw.get("follow"))))  # type: ignore[assignment]
try:
    s = speak.Stream()
    s.feed("네. ")               # 3자 — 너무 짧다
    check("아직 말하지 않는다", len(sent), 0)
    s.feed("일정은 세 건 있습니다.")
    truthy("문장이 충분히 모이면 말한다", len(sent) == 1)
    truthy("앞의 짧은 조각도 함께 나갔다", sent[0][0].startswith("네."))
finally:
    speak.say = _real_say          # type: ignore[assignment]

print("\n[5b] 문장부호가 없어도 결국 끊어 내보낸다")
# 마침표 없는 나열형 답변이 통째로 close() 까지 밀리면 스트리밍이 무의미해진다.
sent.clear()
speak.say = lambda text, **kw: sent.append((text, bool(kw.get("follow"))))  # type: ignore[assignment]
try:
    s = speak.Stream()
    long_text = " ".join(f"항목{i}번" for i in range(1, 26))   # 문장부호 없이 길게
    assert len(long_text) > speak._STREAM_MAX, "시험 문자열이 상한보다 짧다"
    s.feed(long_text)
    truthy("문장부호 없이도 말하기 시작했다", len(sent) >= 1)
    truthy("한 조각이 너무 길지 않다", all(len(x) <= speak._STREAM_MAX + 1 for x, _ in sent))
    truthy("단어 중간에서 자르지 않았다", all(x.strip().endswith("번") for x, _ in sent))
finally:
    speak.say = _real_say          # type: ignore[assignment]

print("\n[6] 스트리밍이 말했으면 실행기는 다시 말하지 않는다")
# handle 이 speaker 를 받아 델타를 흘리는 경로를 그대로 흉내낸다.
import bridge  # noqa: E402

sent.clear()
speak.say = lambda text, **kw: sent.append((text, bool(kw.get("follow"))))  # type: ignore[assignment]
try:
    stream = speak.Stream(lead="")

    def fake_ask(prompt, *, elevated=False, dev=False, on_text=None):
        for c in ["답변입니다. ", "두 번째 문장입니다."]:
            if on_text:
                on_text(c)
        return "답변입니다. 두 번째 문장입니다.", {
            "effective_input": 0, "cache_read": 0, "cache_write": 0,
            "output": 0, "cost_usd": 0.0,
        }

    _real_ask = bridge.ask
    bridge.ask = fake_ask          # type: ignore[assignment]
    try:
        reply, _ = bridge.ask("무엇이든", on_text=stream.feed)
        stream.close()
    finally:
        bridge.ask = _real_ask     # type: ignore[assignment]

    truthy("스트리밍으로 말했다", stream.spoke())
    # "답변입니다." 는 _STREAM_MIN 보다 짧아 혼자 안 나간다 — 뒤 문장과 합쳐 한 번에.
    # 조각 수를 세는 게 아니라 '전부 나갔는가' 를 본다.
    joined = " ".join(s for s, _ in sent)
    truthy("첫 문장이 나갔다", "답변입니다" in joined)
    truthy("둘째 문장도 나갔다", "두 번째 문장입니다" in joined)
    # 실행기는 spoke() 를 보고 say 를 건너뛴다 — 여기서 세어 확인한다
    before = len(sent)
    if not stream.spoke():
        speak.say(reply, block=False)
    check("다시 말하지 않았다", len(sent), before)
finally:
    speak.say = _real_say          # type: ignore[assignment]

print("\n[7] 설정 — CLI 브릿지에서는 저절로 꺼진 것과 같다")
truthy("STREAM_REPLY 가 있다", hasattr(config, "STREAM_REPLY"))
s = speak.Stream(lead="아무개님 ")
check("아무것도 안 먹이면 말한 적 없다", s.spoke(), False)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    for f in FAIL:
        print("  " + f)
    raise SystemExit(1)
print("✅ 전부 통과 — 문장이 완성되는 대로 말하되 서로 끊지 않는다")
