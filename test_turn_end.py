#!/usr/bin/env python3
"""말끝 판정 검증 — 전처리가 참조 구현과 어긋나면 잡는다.

이 모듈의 값어치는 전부 전처리에 걸려 있다. Whisper 특징 추출은 어긋나도
예외가 안 나고 그냥 '그럴듯한 확률' 을 돌려주기 때문이다 — 실제로 처음
붙였을 때 출력에 시그모이드를 한 번 더 씌워 모든 값이 0.66~0.73 으로
뭉쳤는데, 코드는 멀쩡히 돌았고 로그에도 아무 표시가 없었다.
그래서 골든값을 박아둔다. 여기가 깨지면 판정을 믿으면 안 된다.
    python test_turn_end.py
"""
import sys

import numpy as np

import config
import turn_end

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


def close(name, got, want, tol):
    ok = got is not None and abs(got - want) <= tol
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want}±{tol} 실제={got}"))


# ⚠ 모델(8.7MB)은 state/ 에 있고 state/ 는 커밋되지 않는다 — 공개판을 받은
#   사람에게는 모델이 없다. 그때 이 검사가 죽으면 "받자마자 테스트가 깨진다"
#   가 되어, 진짜 고장과 구분이 안 된다. 없으면 건너뛰되 왜 건너뛰는지 말한다.
if not config.TURN_END_MODEL.exists():
    print("⏭  말끝 판정 모델이 없어 건너뜁니다.")
    print(f"   받는 곳: huggingface.co/pipecat-ai/smart-turn-v3")
    print(f"   둘 곳:   {config.TURN_END_MODEL}")
    sys.exit(0)

print("[1] 모델이 실린다")
check("모델 파일 있음", config.TURN_END_MODEL.exists(), True)
check("preload", turn_end.preload(), True)
check("available", turn_end.available(), True)

print("\n[2] 골든값 — 전처리가 참조 구현과 같은가")
# 참조(transformers WhisperFeatureExtractor + 같은 onnx)와 같은 오디오에서
# 소수점 5자리까지 맞는 것을 확인하고 박은 값이다 (2026-08-13).
# 깨졌다면 셋 중 하나다: 패딩 방향, 오디오 정규화, 출력에 시그모이드 중복.
sig0 = (np.random.default_rng(0).standard_normal(16000 * 3) * 0.05).astype(np.float32)
sig7 = (np.random.default_rng(7).standard_normal(16000 * 1) * 0.05).astype(np.float32)
close("seed0 3초", turn_end.probability(sig0), 0.805015, 0.002)
close("seed7 1초", turn_end.probability(sig7), 0.972992, 0.002)

print("\n[3] 패딩은 '앞쪽' 이다")
# 뒤에 채우면 모델이 보는 마지막 순간이 무음이라 늘 '끝' 쪽으로 쏠린다.
# 앞뒤를 바꾸면 값이 달라져야 한다 — 안 달라지면 패딩이 안 먹은 것이다.
NEED = 16000 * 8
short = (np.random.default_rng(3).standard_normal(16000 * 2) * 0.05).astype(np.float32)
front = turn_end.probability(short)                      # 모듈이 앞에 채운다
back = turn_end.probability(np.pad(short, (0, NEED - len(short))))
check("앞/뒤 패딩 결과가 다르다", abs(front - back) > 0.01, True)

print("\n[4] 이상한 입력에도 귀가 멎지 않는다")
for name, sig in [("빈 배열", np.zeros(0, dtype=np.float32)),
                  ("한 샘플", np.zeros(1, dtype=np.float32)),
                  ("8초 초과(20초)", np.zeros(16000 * 20, dtype=np.float32)),
                  ("2차원", np.zeros((2, 16000), dtype=np.float32))]:
    p = turn_end.probability(sig)
    check(f"{name} → 확률", isinstance(p, float) and 0.0 <= p <= 1.0, True)

print("\n[5] 기본값은 섀도 — 검증 전에는 사장님 말을 자르지 않는다")
# 실측으로 오탐률을 보기 전에 실전으로 켜면, 틀렸을 때 그 대가를 사장님이
# 치른다 (말이 잘린다). 켜는 것은 사람이 숫자를 보고 할 결정이다.
check("TURN_END_SHADOW 기본 True", config.TURN_END_SHADOW, True)
check("묻는 시점이 기존 대기보다 이르다",
      config.TURN_END_ASK_BLOCKS < config.VAD_END_BLOCKS_SHORT, True)

print("\n[6] 청취 루프가 이 모듈을 문다")
import audio
check("_ask_turn_end 있음", callable(getattr(audio, "_ask_turn_end", None)), True)
check("_log_turn_end 있음", callable(getattr(audio, "_log_turn_end", None)), True)
check("모델이 죽어도 None 으로 넘어간다",
      audio._ask_turn_end(np.zeros(16000, dtype=np.float32)) is not None, True)

print("\n[7] 채점은 '소리' 가 아니라 '사장님 목소리' 로 한다")
# ⚠ 이게 이 섀도 계측의 생명이다. 사장님 자리엔 옆 통화·아파트 방송이 상시
#   있어서, '소리가 이어졌다' 를 그대로 '사장님이 말을 이었다' 로 세면
#   오탐이 부풀어 오른다 (774건 실측 오탐률 36% 중 얼마가 주변 소리인지
#   가릴 수 없었다). 계측기가 판단을 그르치는 쪽으로 틀리면 안 된다.
import numpy as _np

import audio as _audio
import voiceprint as _vp

_cap = []
_real_log, _audio._log = _audio._log, lambda s: _cap.append(s)
_real_verify = _vp.verify
_tail = (_np.random.default_rng(1).standard_normal(config.SAMPLE_RATE) * 0.05).astype("float32")

_vp.verify = lambda a: (None, 0.2)              # 이어진 게 남 목소리였다
_cap.clear(); _audio._log_turn_end(0.9, 20, True, 55, 44, tail=_tail)
check("남 목소리면 오탐이 아니다", "오탐" in _cap[0], False)

_vp.verify = lambda a: ("홍길동", 0.8)           # 진짜 사장님이 이었다
_cap.clear(); _audio._log_turn_end(0.9, 20, True, 55, 44, tail=_tail)
check("본인 목소리면 오탐이 맞다", "오탐" in _cap[0], True)

# 가릴 수 없을 때는 예전 그대로 — 모르면서 봐주면 오탐률이 낮게 보인다
_vp.verify = _real_verify
_cap.clear(); _audio._log_turn_end(0.9, 20, True, 55, 44, tail=None)
check("가릴 수 없으면 오탐 유지", "오탐" in _cap[0], True)
_audio._log = _real_log

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과 — 말끝을 묻되, 아직 자르지는 않는다")
