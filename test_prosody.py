#!/usr/bin/env python3
"""억양 판정(prosody) — 말끝이 올라가면 질문, 아니면 그대로.

실제 목소리 대신 합성 성대파(피치를 정확히 통제한 톤)로 판정 논리만 본다.
whisper·마이크 없이 돈다.
"""
import numpy as np

import prosody

FAIL = []


def check(name, got, want=True):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


SR = prosody.SR


def tone(f0_curve, sec=1.2):
    """주어진 피치 곡선을 따라가는 성대파 흉내 (기본파+배음)."""
    n = int(sec * SR)
    f = np.interp(np.linspace(0, 1, n), np.linspace(0, 1, len(f0_curve)), f0_curve)
    phase = 2 * np.pi * np.cumsum(f) / SR
    x = 0.6 * np.sin(phase) + 0.3 * np.sin(2 * phase) + 0.1 * np.sin(3 * phase)
    return (x * 0.1).astype(np.float32)


print("[1] 판정")
check("끝이 올라가면 질문 (120→170Hz)",
      prosody.rising(tone([120, 120, 120, 170])), True)
check("평탄하면 평서문 (120Hz 유지)",
      prosody.rising(tone([120, 120, 120, 121])), False)
check("끝이 내려가면 평서문 (140→100Hz)",
      prosody.rising(tone([140, 140, 140, 100])), False)
check("여성 음역도 같다 (220→300Hz)",
      prosody.rising(tone([220, 220, 220, 300])), True)
check("자연스러운 흔들림(±5%)은 질문 아님",
      prosody.rising(tone([130, 126, 133, 129])), False)

print("[2] 판단 불가는 None — 아무것도 안 바꾼다")
check("무성음(잡음)", prosody.rising(np.random.default_rng(1)
                                     .normal(0, 0.02, SR).astype(np.float32)), None)
check("너무 짧음", prosody.rising(tone([120, 170], sec=0.05)), None)
check("무음", prosody.rising(np.zeros(SR, dtype=np.float32)), None)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 올라간 말끝만 질문이 된다")
