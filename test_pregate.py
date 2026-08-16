#!/usr/bin/env python3
"""받아쓰기 앞단 거르개 — 싸게 버리되, 절대 귀를 잃지 않는다.

받아쓰기(1474ms)가 화자확인(18.5ms)보다 80배 비싸서 순서를 당겼다.
이득은 명확하지만 위험도 명확하다: 여기가 조금이라도 과하게 잠기면
동백이 사장님 말을 아예 안 듣는다. 그래서 '통과해야 하는 경우' 를
'버려야 하는 경우' 보다 훨씬 많이 적어 둔다.

가장 무서운 것은 지문 미등록이다 — verify 가 (None, 0.0) 을 주는데
그 0.0 을 '남' 으로 읽으면 모든 소리를 버려 완전히 귀머거리가 된다.

    python test_pregate.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import numpy as np

import config
config.DAWN_FAR_ENABLED = False   # 시간 무관 검증 — 새벽 완화는 test_dawn 이 검증
import dongbaek

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


CLIP = np.zeros(16000, dtype=np.float32)


class _VP:
    """voiceprint 대역. 어떤 (이름, 점수) 를 주는지만 흉내낸다."""

    def __init__(self, enrolled, result, boom=False):
        self._enrolled = enrolled
        self._result = result
        self._boom = boom
        self.remembered = 0

    def enrolled(self):
        return self._enrolled

    def verify(self, audio):
        if self._boom:
            raise RuntimeError("모델이 죽었다")
        return self._result

    def remember_stranger(self, audio, score):
        self.remembered += 1


def run(vp, **cfg):
    """가짜 voiceprint 를 꽂고 판정을 받아온다."""
    import sys

    old_mod = sys.modules.get("voiceprint")
    old_cfg = {k: getattr(config, k) for k in cfg}
    sys.modules["voiceprint"] = vp
    for k, v in cfg.items():
        setattr(config, k, v)
    try:
        return dongbaek._skip_transcribe(CLIP)
    finally:
        for k, v in old_cfg.items():
            setattr(config, k, v)
        if old_mod is not None:
            sys.modules["voiceprint"] = old_mod
        else:
            del sys.modules["voiceprint"]


print(f"\n지금 거르개는 {'켜짐' if config.PREGATE_ENABLED else '꺼짐'} "
      f"(2026-08-12 사고 후 꺼둠)")

# 아래는 '켰을 때 이렇게 동작해야 한다' 를 잰다. 지금 꺼져 있어도 로직은
# 살아 있어야 다시 켤 때 믿을 수 있으므로, 이 묶음만 강제로 켜고 검사한다.
print("\n버려야 하는 것 (켰을 때)")
vp = _VP(["홍길동"], (None, 0.12))
check("확실한 남 목소리는 버린다", run(vp, PREGATE_ENABLED=True), True)
check("버리면서 남 지문 코호트를 먹인다", vp.remembered, 1)
check("되살리기용 원본을 들고 있는다 ('방금 나야')",
      dongbaek._LAST_REJECT["audio"] is not None, True)
# ⚠ 점수를 남겨야 한다. 안 남기면 문턱을 정할 근거(voice_scores.jsonl)가
#   거르개가 일할수록 사라진다 — 2026-08-12 에 실제로 그랬다.
check("버린 발화의 점수를 기록에 남긴다",
      hasattr(dongbaek, "_log_voice_score"), True)

print("\n통과해야 하는 것 (귀를 잃지 않는 쪽)")
check("아는 사람은 통과", run(_VP(["홍길동"], ("홍길동", 0.81))), False)
check("문턱 바로 아래(애매)는 통과",
      run(_VP(["홍길동"], (None, config.PREGATE_STRANGER_MAX + 0.01))), False)
check("문턱과 같으면 통과 (경계는 통과 쪽)",
      run(_VP(["홍길동"], (None, config.PREGATE_STRANGER_MAX))), False)
check("_speaker_ok 문턱 근처(0.44)는 통과 — 감기 든 날의 사장님",
      run(_VP(["홍길동"], (None, 0.44))), False)
check("⚠ 지문 미등록이면 무조건 통과 (여기가 무너지면 완전 귀머거리)",
      run(_VP([], (None, 0.0))), False)
check("모델이 죽으면 통과 ('(검증불가)')",
      run(_VP(["홍길동"], ("(검증불가)", 1.0))), False)
check("verify 가 터져도 통과", run(_VP(["홍길동"], None, boom=True)), False)
check("거르개를 끄면 통과", run(_VP(["홍길동"], (None, 0.01)),
                          PREGATE_ENABLED=False), False)
check("화자확인 자체가 꺼져 있으면 통과",
      run(_VP(["홍길동"], (None, 0.01)), VOICE_VERIFY_ENABLED=False), False)

print("\n문턱이 안전한 범위에 있는가")
check("거르개 문턱 < 화자확인 문턱 (부분집합이어야 판정이 안 바뀐다)",
      config.PREGATE_STRANGER_MAX < config.VOICE_VERIFY_THRESHOLD, True)

# ── 2026-08-12 04:05 사고 ────────────────────────────────
# 문턱을 0.38 로 올린 첫날 새벽, 유사도 0.37 짜리 발화가 버려졌고 12초 뒤에
# "동백," 하고 다시 부르는 기록이 남았다. 무시당해서 다시 부른 정황이고,
# 그 시각 깨어 있던 사람은 한 명이다.
#
# 이 값이 중요한 이유: 문턱을 정할 때 근거로 쓴 117건은 전부 낮에 잰 것이라
# '막 깬 목소리' 가 없었다. 관측된 '버리면 안 되는' 최저는 0.451 이 아니라
# 0.37 이다. 낮 표본만 보고 다시 올리는 일을 여기서 막는다.
INCIDENT_LOW = 0.37
check(f"2026-08-12 사고값({INCIDENT_LOW})보다 문턱이 낮다 — 낮 표본만 보고 올리지 말 것",
      config.PREGATE_STRANGER_MAX < INCIDENT_LOW, True)

# ── 표류 감시 ────────────────────────────────────────────
# 문턱은 2026-08-11 실측 117건을 보고 정했다. 그런데 목소리는 변한다 —
# 감기·마이크 교체·계절. 통과 점수의 왼쪽 꼬리가 문턱 쪽으로 내려오면
# 어느 날 갑자기 사장님 말이 받아쓰기 전에 버려지기 시작한다.
# 그 날을 조용히 맞지 않으려고, 실제 기록으로 매번 다시 잰다.
print("\n표류 감시 (실측 기록으로 문턱을 다시 잰다)")
SCORES = config.STATE / "voice_scores.jsonl"
if not SCORES.exists():
    print("  · 기록이 없어 건너뜀 (state/voice_scores.jsonl)")
else:
    import json

    rows = []
    for line in SCORES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    ok = sorted(r["score"] for r in rows if r.get("name") and "score" in r)
    if len(ok) < 20:
        print(f"  · 통과 기록이 {len(ok)}건뿐이라 판단 보류 (20건 이상 필요)")
    else:
        lo, T = ok[0], config.PREGATE_STRANGER_MAX
        print(f"  · 통과 {len(ok)}건, 최저 {lo:.3f} / 문턱 {T:.2f} / 여유 {lo - T:+.3f}")
        check("실측에서 문턱 아래로 통과한 목소리가 하나도 없다",
              [f"{s:.3f}" for s in ok if s < T], [])
        # 여유가 0.03 아래로 좁아지면 아직 사고는 없어도 시간문제다.
        check("여유가 0.03 이상 남아 있다 (좁아지면 문턱을 낮출 때다)",
              lo - T >= 0.03, True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과")
