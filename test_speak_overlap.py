#!/usr/bin/env python3
"""speak — 단일 재생 워커 검증. 실제 재생 없이 로직만.

플래그로 겹침을 쫓던 시절의 테스트는 내부 상태를 흉내냈지만,
큐 구조에서는 진짜 워커를 돌리고 '동시에 소리내는 발화 수'를 직접 센다.
이 수가 1을 넘는 순간이 한 번이라도 있으면 실패다 — 그게 겹침이니까.
    python test_speak_overlap.py
"""
import threading
import time

import speak

# 실제 소리 대신 재생을 흉내낸다. 동시 재생 수를 세는 게 목적.
_playing = 0
_max_concurrent = 0
_played: list[str] = []
_count_lock = threading.Lock()


def _fake_play(body: str) -> None:
    global _playing, _max_concurrent
    with _count_lock:
        _playing += 1
        _max_concurrent = max(_max_concurrent, _playing)
        _played.append(body)
    try:
        # interrupt 를 존중하며 0.15초 '재생'
        deadline = time.monotonic() + 0.15
        while time.monotonic() < deadline:
            if speak._interrupt.is_set():
                return
            time.sleep(0.01)
    finally:
        with _count_lock:
            _playing -= 1


speak._play = _fake_play


def reset():
    global _played, _max_concurrent
    speak.stop()
    time.sleep(0.05)
    with _count_lock:
        _played = []
        _max_concurrent = 0


def wait_quiet(timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not speak.is_speaking():
            return
        time.sleep(0.01)
    raise AssertionError("워커가 조용해지지 않음")


passed = 0

# ① 같은 말 중복 병합 — 재생 중에 같은 말이 오면 무시
reset()
speak.say("네", block=False, priority=speak.PRIORITY_NOTICE)
time.sleep(0.03)
speak.say("네", block=False, priority=speak.PRIORITY_NOTICE)
wait_quiet()
assert _played == ["네"], f"중복 병합 실패: {_played}"
passed += 1

# ② 알림은 답변을 밀어내지 않는다 — 답변 재생 중 알림은 침묵
reset()
speak.say("여기 답변이에요", block=False, priority=speak.PRIORITY_REPLY)
time.sleep(0.03)
speak.say("확인하고 있어요", block=False, priority=speak.PRIORITY_NOTICE)
wait_quiet()
assert _played == ["여기 답변이에요"], f"알림이 답변에 끼어듦: {_played}"
passed += 1

# ③ 답변은 알림을 끊는다
reset()
speak.say("네", block=False, priority=speak.PRIORITY_NOTICE)
time.sleep(0.03)
t0 = time.monotonic()
speak.say("여기 답변이에요", block=True, priority=speak.PRIORITY_REPLY)
took = time.monotonic() - t0
assert "여기 답변이에요" in _played, f"답변이 안 나감: {_played}"
assert took < 0.5, f"알림이 끝나길 기다렸음({took:.2f}s) — 끊었어야 한다"
passed += 1

# ④ 답변은 답변 뒤에 줄을 선다 — 하나씩, 끝까지 ("큐에 넣어서 하나씩" 지시)
reset()
t0 = time.monotonic()
speak.say("먼저 답변", block=False, priority=speak.PRIORITY_REPLY)
time.sleep(0.03)
speak.say("나중 답변", block=True, priority=speak.PRIORITY_REPLY)
took = time.monotonic() - t0
assert _played == ["먼저 답변", "나중 답변"], f"줄서기 실패: {_played}"
# 재생을 0.15초씩 흉내내므로, 앞 답변을 끊지 않았다면 두 개에 0.28초는 걸린다
assert took >= 0.28, f"앞 답변을 끊었다({took:.2f}s) — 답변끼리는 줄을 서야 한다"
passed += 1

# ⑤ 알림은 알림 뒤에 줄을 선다 — 끊지 않고 둘 다, 순서대로
reset()
speak.say("네", block=False, priority=speak.PRIORITY_NOTICE)
speak.say("확인하고 있어요", block=False, priority=speak.PRIORITY_NOTICE)
wait_quiet()
assert _played == ["네", "확인하고 있어요"], f"알림 순차 실패: {_played}"
passed += 1

# ⑥ stop() — 대기열과 현재 재생 모두 정리된다
reset()
speak.say("긴 답변이에요", block=False, priority=speak.PRIORITY_REPLY)
time.sleep(0.03)
speak.stop()
time.sleep(0.05)
assert not speak.is_speaking(), "stop 후에도 재생 중"
passed += 1

# ⑦ 난타 — 스레드 8개가 마구 불러도 동시 재생은 절대 1을 넘지 않는다
reset()


def hammer(i):
    for j in range(6):
        pri = speak.PRIORITY_REPLY if (i + j) % 3 == 0 else speak.PRIORITY_NOTICE
        speak.say(f"발화 {i}-{j}", block=False, priority=pri)
        time.sleep(0.005 * (i % 3))


threads = [threading.Thread(target=hammer, args=(i,)) for i in range(8)]
for t in threads:
    t.start()
for t in threads:
    t.join()
wait_quiet(timeout=10.0)
assert _max_concurrent <= 1, f"겹침 발생: 동시 재생 {_max_concurrent}"
passed += 1

# ⑧ stop() 뒤에 도착한 스트림 조각은 버려진다 — 입 다문 뒤 뒤늦게 떠들지 않는다
reset()
s = speak.Stream()
s.feed("첫 문장이에요. 나가야 해요. ")
speak.stop()                        # 끼어들기·말 보탬이 부르는 그 stop
s.feed("이건 늦게 도착한 문장이에요. 나가면 안 돼요. ")
s.close()
time.sleep(0.1)
late = speak.clean("이건 늦게 도착한 문장이에요.")
assert all(late not in p for p in _played), f"죽은 스트림이 말함: {_played}"
passed += 1

print(f"test_speak_overlap: {passed}건 통과 (최대 동시 재생 {_max_concurrent})")
