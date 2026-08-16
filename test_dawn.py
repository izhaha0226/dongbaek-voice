#!/usr/bin/env python3
"""새벽 원거리 모드 — 시간대에 따라 귀·호출어·화자 문턱이 갈리는지.

시간을 조작하지 않는다 — DAWN_HOURS 를 '지금 포함/제외' 로 바꿔 양쪽을
다 지나가게 한다 (끝나면 원복).
"""
from datetime import datetime

import numpy as np

import audio
import config

FAIL = []


def check(name, got, want=True):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


_orig_hours, _orig_enabled = config.DAWN_HOURS, config.DAWN_FAR_ENABLED
h = datetime.now().hour
IN = (h, h + 1)                      # 지금이 새벽인 셈
OUT = ((h + 2) % 24, (h + 3) % 24)   # 지금이 낮인 셈

try:
    config.DAWN_FAR_ENABLED = True

    print("[1] 시간대 스위치")
    config.DAWN_HOURS = IN
    check("새벽 판정", config.dawn_far_active(), True)
    config.DAWN_HOURS = OUT
    check("낮 판정", config.dawn_far_active(), False)

    print("[2] 문턱이 실제로 갈린다")
    config.DAWN_HOURS = IN
    check("새벽 VAD 완화", config.vad_trigger_mult(), config.DAWN_VAD_TRIGGER_MULT)
    check("새벽 화자 문턱", config.voice_verify_threshold(), config.DAWN_VOICE_THRESHOLD)
    config.DAWN_HOURS = OUT
    check("낮 VAD 원래대로", config.vad_trigger_mult(), config.VAD_TRIGGER_MULT)
    check("낮 화자 문턱 원래대로", config.voice_verify_threshold(),
          config.VOICE_VERIFY_THRESHOLD)

    print("[3] 증폭 — 시간대 무관, 작은 소리만, 상한 안에서")
    # ⚠ 증폭은 일부러 시간대에 안 묶었다 (2026-08-13 08:29 '크기가' 사고 —
    #   새벽 모드가 8시에 꺼진 직후 침대에서 부른 호출이 버려졌다).
    #   멀리서 부르는 일은 시계를 안 보고 일어난다.
    quiet = (np.ones(16000, dtype=np.float32) * 0.005)
    config.DAWN_HOURS = IN
    boosted = audio.dawn_boost(quiet)
    r = float(np.sqrt(np.mean(boosted**2)))
    check("작은 소리 증폭", r > 0.02, True)
    check("상한 준수 (6배)", r <= 0.005 * config.DAWN_GAIN_MAX + 1e-6, True)
    loud = (np.ones(16000, dtype=np.float32) * 0.2)
    check("큰 소리는 안 건드림",
          abs(float(np.max(audio.dawn_boost(loud))) - 0.2) < 1e-6, True)
    config.DAWN_HOURS = OUT
    check("낮에도 작은 소리는 증폭한다",
          float(np.max(audio.dawn_boost(quiet))) > float(np.max(quiet)), True)
    config.FAR_GAIN_ENABLED = False
    check("스위치로 끌 수 있다",
          float(np.max(audio.dawn_boost(quiet))), float(np.max(quiet)))
    config.FAR_GAIN_ENABLED = True

    print("[3b] 끊고 들어오기도 원거리 시간대엔 완화 (2026-08-13 계측 6건)")
    config.DAWN_HOURS = IN
    check("블록 요구 완화", config.barge_in_blocks(), config.DAWN_BARGE_IN_BLOCKS)
    check("큰소리 기준 완화", config.barge_in_loud_mult(), config.DAWN_BARGE_IN_LOUD_MULT)
    config.DAWN_HOURS = OUT
    check("낮엔 원래대로", config.barge_in_blocks(), config.BARGE_IN_BLOCKS)
    check("완화해도 문턱은 그대로", config.BARGE_IN_ABS_FLOOR, 0.022)

    print("[4] 호출어 유사도는 새벽에도 안 바꾼다 (완화 실효 없음 — config 참조)")
    import router
    config.DAWN_HOURS = IN
    check("새벽에도 '동백한테' 는 호출이 아니다",
          router.match_wake("어제 동백한테 말했는데"), None)
    check("유사도 잣대 동일", config.wake_fuzzy_ratio(), config.WAKE_FUZZY_RATIO)
finally:
    config.DAWN_HOURS, config.DAWN_FAR_ENABLED = _orig_hours, _orig_enabled

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 새벽엔 멀어도 듣는다, 낮에는 원래 잣대다")
