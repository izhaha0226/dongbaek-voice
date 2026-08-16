#!/usr/bin/env python3
"""speak — 단일 재생 워커 검증. 실제 재생 없이 로직만.

플래그로 겹침을 쫓던 시절의 테스트는 내부 상태를 흉내냈지만,
큐 구조에서는 진짜 워커를 돌리고 '동시에 소리내는 발화 수'를 직접 센다.
이 수가 1을 넘는 순간이 한 번이라도 있으면 실패다 — 그게 겹침이니까.
    python test_speak_overlap.py

⚠ 시계에 기대지 않는다. 예전에는 "0.15초 재생 흉내" 를 걸어두고 0.03초 자면
   재생 중이겠거니 했는데, 기계가 바쁘면 그 0.03초 잠이 0.2초가 되어 재생이
   이미 끝나 있었다 — 손대지 않은 코드에서도 5회 중 4회 실패했다
   (2026-08-14, "알림이 답변에 끼어듦"). 야간 정비가 이걸 밟으면 멀쩡한
   수정까지 통째로 되돌아간다.
   그래서 hold()/release() 로 재생을 붙잡아 두고, 잠 대신 wait_start() 로
   '정말 재생이 시작됐다' 는 사실을 기다린다. 끊겼는지도 시간을 재지 않고
   _cut 기록으로 본다.
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import threading
import time

import speak

# 실제 소리 대신 재생을 흉내낸다. 동시 재생 수를 세는 게 목적.
_playing = 0
_max_concurrent = 0
_played: list[str] = []
_cut: list[str] = []                # 끝까지 못 가고 끊긴 발화
_count_lock = threading.Lock()
# 내려가 있으면 재생을 붙잡아 둔다 (hold). 올라가 있으면 흘려보낸다.
_gate = threading.Event()
_gate.set()
_DWELL = 0.02       # 붙잡지 않았을 때의 재생 길이 — 겹침이 드러날 만큼만
_MAX_HOLD = 5.0     # 안전장치: release 를 잊어도 테스트가 매달리지 않게


def _fake_play(body: str) -> None:
    global _playing, _max_concurrent
    with _count_lock:
        _playing += 1
        _max_concurrent = max(_max_concurrent, _playing)
        _played.append(body)
    try:
        started = time.monotonic()
        deadline = started + _MAX_HOLD
        while time.monotonic() < deadline:
            if speak._interrupt.is_set():
                with _count_lock:
                    _cut.append(body)       # 끊겼다 — 시간을 재는 대신 이걸 본다
                return
            if _gate.is_set() and time.monotonic() - started >= _DWELL:
                return
            time.sleep(0.005)
    finally:
        with _count_lock:
            _playing -= 1


speak._play = _fake_play


def hold():
    """release() 할 때까지 재생이 안 끝나게 붙잡는다 — '재생 중' 을 확실히 만든다."""
    _gate.clear()


def release():
    _gate.set()


def reset():
    global _played, _max_concurrent, _cut
    release()
    speak.stop()
    wait_quiet()
    with _count_lock:
        _played = []
        _cut = []
        _max_concurrent = 0


def wait_quiet(timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not speak.is_speaking():
            return
        time.sleep(0.01)
    raise AssertionError("워커가 조용해지지 않음")


def wait_start(body, timeout=3.0):
    """그 말이 '재생을 시작했다' 까지 기다린다. 시계로 어림하지 않는다."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _count_lock:
            if body in _played:
                return
        time.sleep(0.005)
    raise AssertionError(f"재생이 시작되지 않음: {body} (실제 {_played})")


passed = 0

# ① 같은 말 중복 병합 — 재생 중에 같은 말이 오면 무시
reset()
hold()
speak.say("네", block=False, priority=speak.PRIORITY_NOTICE)
wait_start("네")
speak.say("네", block=False, priority=speak.PRIORITY_NOTICE)
release()
wait_quiet()
assert _played == ["네"], f"중복 병합 실패: {_played}"
passed += 1

# ② 알림은 답변을 밀어내지 않는다 — 답변 재생 중 알림은 침묵
reset()
hold()
speak.say("여기 답변이에요", block=False, priority=speak.PRIORITY_REPLY)
wait_start("여기 답변이에요")
speak.say("확인하고 있어요", block=False, priority=speak.PRIORITY_NOTICE)
release()
wait_quiet()
assert _played == ["여기 답변이에요"], f"알림이 답변에 끼어듦: {_played}"
passed += 1

# ③ 답변은 알림을 끊는다 — 붙잡아 둔 알림이 끊겨야 답변이 시작될 수 있다
reset()
hold()
speak.say("네", block=False, priority=speak.PRIORITY_NOTICE)
wait_start("네")
speak.say("여기 답변이에요", block=False, priority=speak.PRIORITY_REPLY)
wait_start("여기 답변이에요")     # 안 끊었다면 여기서 시간 초과로 걸린다
release()
wait_quiet()
assert "네" in _cut, f"알림이 안 끊겼다: {_played}"
passed += 1

# ④ 답변은 답변 뒤에 줄을 선다 — 하나씩, 끝까지 ("큐에 넣어서 하나씩" 지시)
reset()
hold()
speak.say("먼저 답변", block=False, priority=speak.PRIORITY_REPLY)
wait_start("먼저 답변")
speak.say("나중 답변", block=False, priority=speak.PRIORITY_REPLY)
release()
wait_quiet()
assert _played == ["먼저 답변", "나중 답변"], f"줄서기 실패: {_played}"
assert "먼저 답변" not in _cut, "앞 답변을 끊었다 — 답변끼리는 줄을 서야 한다"
passed += 1

# ⑤ 알림은 알림 뒤에 줄을 선다 — 끊지 않고 둘 다, 순서대로
reset()
hold()
speak.say("네", block=False, priority=speak.PRIORITY_NOTICE)
wait_start("네")
speak.say("확인하고 있어요", block=False, priority=speak.PRIORITY_NOTICE)
release()
wait_quiet()
assert _played == ["네", "확인하고 있어요"], f"알림 순차 실패: {_played}"
assert "네" not in _cut, "앞 알림을 끊었다 — 알림끼리는 줄을 서야 한다"
passed += 1

# ⑥ stop() — 대기열과 현재 재생 모두 정리된다
reset()
hold()
speak.say("긴 답변이에요", block=False, priority=speak.PRIORITY_REPLY)
speak.say("뒤에 선 답변이에요", block=False, priority=speak.PRIORITY_REPLY)
wait_start("긴 답변이에요")
speak.stop()
wait_quiet(timeout=1.0)             # 붙잡아 뒀어도 stop 이면 끊겨야 한다
assert not speak.is_speaking(), "stop 후에도 재생 중"
assert "뒤에 선 답변이에요" not in _played, f"stop 뒤에 대기열이 나갔다: {_played}"
release()
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
wait_quiet()                        # 조각이 큐에 들어갔다면 여기서 재생된다
late = speak.clean("이건 늦게 도착한 문장이에요.")
assert all(late not in p for p in _played), f"죽은 스트림이 말함: {_played}"
passed += 1

print(f"test_speak_overlap: {passed}건 통과 (최대 동시 재생 {_max_concurrent})")
