#!/usr/bin/env python3
"""억양 분석 — 말끝이 올라가면 질문이다 (사장님 지시 2026-08-13).

왜: whisper 가 물음표를 자주 빼먹는다. "동백아 잘 들려?" 가 "잘 들려."
로 적히면 질문이 평서문이 되어 "네, 알겠습니다" 같은 헛답이 나간다.
소리에는 억양이 남아 있다 — 한국어 예/아니오 질문은 말끝 피치가 올라간다.

방법: 자기상관(autocorrelation) 피치 추적. 외부 의존성 없음(numpy).
  30ms 창 · 10ms 간격으로 기본주파수(F0)를 재고, 발화 끝쪽 유성 구간의
  꼬리 피치가 그 앞보다 충분히 높으면 '올라감' 으로 본다.

보수적으로만 쓴다:
  · 물음표를 '붙이기만' 한다 — 지우지 않는다. whisper 가 ? 를 잘못
    붙이는 일은 드물고, 잘못 붙인 ? 보다 빠진 ? 가 훨씬 비싸다.
  · 판단이 서지 않으면(무성음·너무 짧음) None — 아무것도 안 바꾼다.
  · 비교는 화자 절대값이 아니라 같은 발화 안의 상대 변화다 — 남녀·
    거리·감기에 흔들리지 않는다.
"""

from __future__ import annotations

import numpy as np

SR = 16000
FRAME = 480          # 30ms
HOP = 160            # 10ms
F0_MIN, F0_MAX = 70.0, 400.0     # 사람 말소리 기본주파수 범위
VOICED_MIN_STRENGTH = 0.30       # 자기상관 봉우리가 이보다 약하면 무성음


def _frame_f0(x: np.ndarray) -> float:
    """한 프레임의 기본주파수. 무성음이면 0."""
    x = x - float(np.mean(x))
    energy = float(np.dot(x, x))
    if energy < 1e-6:
        return 0.0
    lag_min = int(SR / F0_MAX)
    lag_max = min(int(SR / F0_MIN), len(x) - 1)
    if lag_max <= lag_min:
        return 0.0
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    ac = ac / (ac[0] + 1e-12)
    seg = ac[lag_min:lag_max]
    peak = int(np.argmax(seg))
    if seg[peak] < VOICED_MIN_STRENGTH:
        return 0.0
    return SR / float(lag_min + peak)


def _f0_track(audio: np.ndarray, tail_sec: float = 1.2) -> np.ndarray:
    """발화 끝 tail_sec 구간의 프레임별 F0 (무성음 프레임 제외)."""
    x = np.asarray(audio, dtype=np.float32)
    if len(x) > int(tail_sec * SR):
        x = x[-int(tail_sec * SR):]
    out = []
    for i in range(0, len(x) - FRAME, HOP):
        f0 = _frame_f0(x[i:i + FRAME])
        if f0 > 0:
            out.append(f0)
    return np.asarray(out, dtype=np.float32)


def rising(audio: np.ndarray, *, ratio: float = 1.12,
           min_voiced: int = 8) -> bool | None:
    """말끝 억양이 올라갔는가. 판단 불가면 None.

    같은 발화의 앞부분(기준)과 꼬리(마지막 1/3)를 견준다.
    ratio 1.12 ≈ 2반음 — 평서문의 자연스러운 흔들림보다 크고,
    질문의 상승(보통 3반음 이상)보다 작아 여유가 있다.
    """
    f0 = _f0_track(audio)
    if len(f0) < min_voiced:
        return None
    cut = max(1, len(f0) * 2 // 3)
    head, tail = f0[:cut], f0[cut:]
    if len(tail) < 3:
        return None
    h = float(np.median(head))
    t = float(np.median(tail))
    if h <= 0:
        return None
    return (t / h) >= ratio
