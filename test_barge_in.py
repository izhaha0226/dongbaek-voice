#!/usr/bin/env python3
"""끊고 들어오기 검증 — 사장님이 말하면 동백이 입을 다문다.

이 기능은 마이크를 '동백이 말하는 동안에도' 열어두기 때문에 위험이 따라온다.
되돌아온 자기 목소리를 사장님 말로 착각하면 자기 말을 끊고, 그 소리를
명령으로 실행한다. 그래서 방어가 두 겹이다:
  ① 문턱 — 평상시보다 높고 더 오래 이어져야 사람 말로 인정 (에코 잔향 무시)
  ② 대조 — 받아쓴 문장이 '방금 자기가 한 말' 과 비슷하면 버린다

여기에 더해, 게이트에 영원히 갇히지 않는지도 본다. 재생이 안 끝나면
동백은 영구히 귀를 잃는데, 소리 없이 죽는 고장이라 눈치채기 어렵다.
    python test_barge_in.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import queue
import time

import numpy as np

import audio as A
import config
import dongbaek
import speak

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


def mk_listener(gate, on_barge):
    """실제 오디오 장치 없이 큐에 직접 블록을 밀어 넣는 Listener."""
    L = A.Listener.__new__(A.Listener)
    L._q = queue.Queue()
    L._floor = config.VAD_ABS_FLOOR
    L._floor_hist = []
    L._dead = False
    L._stream = None
    L.on_disconnect = None
    L.on_reconnect = None
    L.on_gate_report = None
    L.gate = gate
    L.on_barge_in = on_barge
    return L


def blocks(q, level, n):
    for _ in range(n):
        q.put(np.full(config.BLOCK, level, dtype=np.float32))


print("[1] 에코 2차 방어 — 되돌아온 자기 목소리를 명령으로 삼지 않는다")
speak._recent = "오늘 일정은 오후 두 시 미팅 하나입니다"
for said, want, why in [
    ("오늘 일정은 오후 두 시 미팅 하나입니다", True, "그대로 되울림"),
    ("오늘 일정은 오후 두시 미팅 하나입니다", True, "받아쓰기가 조금 다르게 적음"),
    ("오늘 일정은 오후 두 시", True, "앞부분만 되울림"),
    ("아니 그거 말고 목요일 거", False, "사장님이 실제로 한 말"),
    ("네", False, "너무 짧아 판단하지 않음"),
]:
    check(f"{why}: {said[:20]!r}", dongbaek._is_own_voice(said), want)

speak._recent = ""
check("동백이 말한 게 없으면 판단 안 함", dongbaek._is_own_voice("아무 말"), False)

print("\n[2] 말하는 중 사람 목소리 → 끊고, 그 발화를 이어받는다")
speaking = {"on": True}
fired = []
L = mk_listener(lambda: speaking["on"],
                lambda: (fired.append(1), speaking.update(on=False)))
blocks(L._q, 0.15, 40)                      # 사장님이 말하기 시작
blocks(L._q, 0.0, config.VAD_END_BLOCKS + 20)   # 그리고 말이 끝남
out = L.next_utterance(timeout=5.0)
check("동백을 끊었다", len(fired), 1)
check("끊은 뒤 발화를 잡았다", out is not None and len(out) > 0, True)
# 끊기 전 preroll 을 이어받으므로 첫 음절이 살아 있어야 한다
check("앞부분이 날아가지 않았다",
      out is not None and len(out) / config.SAMPLE_RATE > 0.5, True)

print("\n[3] 에코 잔향 수준에는 끊기지 않는다")
quiet_fired = []
L2 = mk_listener(lambda: True, lambda: quiet_fired.append(1))
# BARGE_IN_ABS_FLOOR(0.020) 아래 — 하드웨어 AEC 를 통과한 잔향 수준
blocks(L2._q, 0.010, 80)
_hold = config.GATE_MAX_HOLD_SEC
config.GATE_MAX_HOLD_SEC = 1.0          # 시험용으로 상한을 줄인다
t0 = time.monotonic()
r = L2.next_utterance(timeout=1.0)
elapsed = time.monotonic() - t0
check("자기 말을 끊지 않았다", quiet_fired, [])

print("\n[4] 재생이 안 끝나도 영원히 갇히지 않는다")
# 게이트가 계속 닫혀 있으면 타임아웃 시계가 매번 초기화돼 next_utterance 가
# 영영 안 돌아온다. 그러면 동백은 소리 없이 귀를 잃는다 — 실제로 겪은 행.
check("상한을 넘기면 None 을 돌려준다", r, None)
check(f"  → {elapsed:.1f}초 만에 빠져나옴", elapsed < 6.0, True)
config.GATE_MAX_HOLD_SEC = _hold

print("\n[5] 끊기를 끄면 예전처럼 말하는 동안 안 듣는다")
config.BARGE_IN_ENABLED = False
off_fired = []
L3 = mk_listener(lambda: True, lambda: off_fired.append(1))
blocks(L3._q, 0.15, 60)                 # 아주 큰 소리인데도
config.GATE_MAX_HOLD_SEC = 1.0
r3 = L3.next_utterance(timeout=1.0)
config.GATE_MAX_HOLD_SEC = _hold
config.BARGE_IN_ENABLED = True
check("끊기지 않는다", off_fired, [])
check("발화도 잡지 않는다", r3, None)

print("\n[6] 발화 길이에 따라 끝을 다르게 판단한다")
# "동백아" 한마디에 1.4초를 기다리면 못 알아들은 줄 알게 되고,
# 긴 지시를 0.8초에 끊으면 말이 끝나기 전에 잘린다. 둘 다 겪은 문제라
# 발화 길이로 가른다. 여기가 무너지면 둘 중 하나가 반드시 재발한다.
def wait_after(speech_sec):
    L = mk_listener(None, None)
    blocks(L._q, 0.002, 30)                       # 조용한 도입부 (잡음 바닥)
    blocks(L._q, 0.15, int(speech_sec * config.SAMPLE_RATE / config.BLOCK))
    blocks(L._q, 0.0, 200)
    out = L.next_utterance(timeout=10.0)
    return None if out is None else len(out) / config.SAMPLE_RATE - speech_sec


short_wait = wait_after(0.6)
long_wait = wait_after(3.0)
check(f"짧은 호출은 빨리 끝낸다 ({short_wait:.2f}초)", short_wait < 1.1, True)
check(f"긴 지시는 넉넉히 기다린다 ({long_wait:.2f}초)", long_wait > 1.3, True)
check("짧은 쪽이 확실히 더 빠르다", short_wait < long_wait - 0.4, True)

print("\n[7] 받아쓰기 환각 — 버릴 것과 살릴 것")
# 마이크가 늘 열려 있으니 잡음도 계속 받아쓴다. 환각이 승인 대기 중에
# 들어오면 사장님 대답으로 오인된다. 전부 실측에서 나온 것들이다.
for text, want, why in [
    ("많이 " * 200, True, "한 낱말 200회 반복"),
    ("vents " * 180, True, "영문 반복"),
    # 띄어쓰기가 없어 한 낱말로 세어지던 것 — 낱말 기준만으론 못 잡았다
    ("시" * 200, True, "글자만 반복 (띄어쓰기 없음)"),
    ("가나" * 20, True, "두 글자 순환"),
    ("일정, 캘린더, 메일, 진행, 승인, 취소, 되돌려.", True, "프롬프트 그대로"),
    ("네온, 캘린더, 메일, 진행, 승인, 취소, 되돌려.", True, "프롬프트 변형"),
    ("동백아 오늘 일정 알려줘", False, "평범한 명령"),
    ("진행해", False, "승인"),
    ("config 파일에 주석 하나 달아줘", False, "프롬프트 낱말이 섞인 진짜 명령"),
    ("내일 미팅이 오후 두 시인데 몇 시에 나가야 해", False, "긴 질문"),
    ("네", False, "아주 짧은 대답"),
]:
    got = A._looks_like_prompt(text) or A._is_repetition(text)
    check(f"{'버림' if want else '살림'} — {why}", got, want)

print("\n[8] 고유명사 교정 — 프롬프트 힌트만으로는 안 잡힌다")
# initial_prompt 는 '그쪽으로 쏠리게' 할 뿐 보장이 아니다.
# '한빛리조트' 가 한 세션에서 세 갈래로 적힌 게 실측이다.
for said, want, why in [
    ("용전 밸리 배출 확인해", "한빛리조트 배출 확인해", "실제 로그 표기 ①"),
    ("거기 용탐 밸리라고 있잖아", "거기 한빛리조트라고 있잖아", "실제 로그 표기 ②"),
    ("영팁밸리 데이터 확인해줘", "한빛리조트 데이터 확인해줘", "실제 로그 표기 ③"),
    ("한빛리조트 매출 알려줘", "한빛리조트 매출 알려줘", "이미 맞으면 그대로"),
    ("그로스 메이트 커밋 보여줘", "광고플랫폼 커밋 보여줘", "띄어쓰기 교정"),
    ("오늘 일정 알려줘", "오늘 일정 알려줘", "관계없는 말은 안 건드린다"),
]:
    check(f"{why}: {said[:18]!r}", A.fix_terms(said), want)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    raise SystemExit(1)
print("✅ 전부 통과 — 사람 말은 끊고, 자기 목소리엔 속지 않는다")
