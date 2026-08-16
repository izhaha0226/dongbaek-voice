#!/usr/bin/env python3
"""화자 판정 검증 — "여자 목소리라고 모두 김철수이 아니다".

모델 없이 돈다. 임베딩을 수학적으로 조작해 문턱·격차(margin) 규칙만 본다.
    python test_voiceprint.py
"""
import sys

import numpy as np

import config
config.DAWN_FAR_ENABLED = False   # 시간 무관 검증 — 새벽 완화는 test_dawn 이 검증
import voiceprint

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


# 정규직교 축으로 코사인을 정확히 통제한다: cos(x, a)=α, cos(x, b)=β
D = 8
A = np.zeros(D, dtype=np.float32); A[0] = 1.0        # 홍길동 지문
B = np.zeros(D, dtype=np.float32); B[1] = 1.0        # 김철수 지문
voiceprint._prints = {"홍길동": A[None, :], "김철수": B[None, :]}
voiceprint.embed = lambda audio: audio               # 검사가 벡터를 직접 준다


def utt(alpha: float, beta: float, pad: int = 2) -> np.ndarray:
    """cos(x, 홍길동)=alpha, cos(x, 김철수)=beta 인 발화 벡터.

    pad 는 나머지 성분을 실을 축이다. 서로 다른 발화에 다른 축을 주면
    '두 발화가 서로 얼마나 닮았나' 까지 통제할 수 있다 — 같은 축에 몰면
    실제 임베딩에는 없는 높은 유사도가 생겨 검사가 거짓으로 깨진다.
    """
    x = np.zeros(D, dtype=np.float32)
    x[0], x[1] = alpha, beta
    x[pad] = np.sqrt(max(0.0, 1.0 - alpha * alpha - beta * beta))
    return x


print("\n[1] 문턱 — 0.45 아래는 남이다")
check("본인(0.62) 통과", voiceprint.verify(utt(0.62, 0.10))[0], "홍길동")
check("동성 타인(0.40) 차단 ← '여자 목소리 = 김철수' 사고의 그 지점",
      voiceprint.verify(utt(0.10, 0.40))[0], None)
check("문턱 회귀 금지 (0.30 으로 되돌리면 실패)",
      config.VOICE_VERIFY_THRESHOLD >= 0.45, True)

print("\n[2] 격차 — 두 사람 모두에 어중간하면 남이다")
check("0.50 vs 0.47 → 미확인", voiceprint.verify(utt(0.50, 0.47))[0], None)
check("0.60 vs 0.20 → 확정", voiceprint.verify(utt(0.20, 0.60))[0], "김철수")

print("\n[3] 경계 조건 — 잠그되 벽돌은 되지 않는다")
prints_bak = voiceprint._prints
voiceprint._prints = {}
check("등록 0명 → 열림 신호 (None, 0.0)", voiceprint.verify(utt(0.9, 0.0)), (None, 0.0))
voiceprint._prints = prints_bak
voiceprint.embed = lambda audio: None
check("모델 죽음 → (검증불가) 통과", voiceprint.verify(utt(0.9, 0.0))[0], "(검증불가)")
voiceprint.embed = lambda audio: audio

print("\n[4] 적응 학습 — 목소리를 따라가되 신원은 옮기지 않는다")
# 진짜 지문 파일을 건드리지 않는다 — adapt 는 저장소에 쓴다
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp()) / "voiceprints.npz"
config.VOICEPRINT_FILE = tmp
voiceprint._save_prints({"홍길동": A[None, :], "김철수": B[None, :]})

check("애매한 통과(0.50)는 배우지 않는다",
      voiceprint.adapt("홍길동", utt(0.50, 0.0), 0.50), False)
check("확실한 통과(0.72)는 배운다",
      voiceprint.adapt("홍길동", utt(0.72, 0.0), 0.72), True)
check("등록 지문 수는 그대로", voiceprint.enrolled()["홍길동"], 1)
check("학습 지문이 따로 쌓인다", voiceprint.learned()["홍길동"], 1)
check("미등록 이름은 만들지 않는다",
      voiceprint.adapt("낯선사람", utt(0.9, 0.0), 0.9), False)
check("(검증불가)는 사람이 아니다",
      voiceprint.adapt("(검증불가)", utt(0.9, 0.0), 0.9), False)

# 학습 지문이 자기 자신의 '2위' 가 되어 격차 검사에 걸리면 안 된다.
# 사람 단위로 묶지 않으면 본인이 본인 때문에 차단된다.
check("학습 지문이 본인을 막지 않는다",
      voiceprint.verify(utt(0.70, 0.0))[0], "홍길동")

for i in range(config.VOICE_ADAPT_MAX + 3):
    voiceprint.adapt("홍길동", utt(0.75, 0.0), 0.75)
check("학습 지문은 상한에서 멈춘다",
      voiceprint.learned()["홍길동"], config.VOICE_ADAPT_MAX)
check("회전이 등록 지문을 밀어내지 않는다", voiceprint.enrolled()["홍길동"], 1)

check("이름을 바꾸면 학습 지문도 따라온다",
      (voiceprint.rename("홍길동", "사장님"), voiceprint.learned().get("사장님")),
      (True, config.VOICE_ADAPT_MAX))
check("잊으면 학습 지문도 함께 사라진다",
      (voiceprint.forget("사장님"), voiceprint.learned().get("사장님")),
      (True, None))

print("\n[5] 남 지문(임포스터) — 계속 배우면서도 안 헷갈리는 장치")
# ⚠ 기본값은 OFF 다 (2026-08-13 새벽 잠금 사고 — 코호트가 사장님 새벽
#   목소리까지 막았다, config 참조). 기능 자체는 남아 있어야 하므로
#   시험에서만 켜서 회귀를 지킨다 — 재가동할 날의 검증이 이 절이다.
config.VOICE_IMPOSTER_ENABLED = True
voiceprint._save_prints({"홍길동": A[None, :], "김철수": B[None, :]})

# 확실히 낮은 거절만 담는다 — 감기 든 날의 내 목소리를 '남' 으로 박으면
# 그 뒤로 영구히 막힌다
check("애매한 거절(0.42)은 안 담는다",
      voiceprint.remember_stranger(utt(0.42, 0.0), 0.42), False)
stranger = utt(0.30, 0.30, pad=4)   # 둘 다와 어중간하게 닮은 낯선 목소리
check("확실한 거절(0.30)은 담는다",
      voiceprint.remember_stranger(stranger, 0.30), True)
check("남 지문 1개", voiceprint.strangers(), 1)
check("남 지문은 화자로 세지 않는다", sorted(voiceprint.enrolled()), ["김철수", "홍길동"])

# 상대 판정: 나와 닮은 정도가 남과 닮은 정도보다 확실히 커야 통과
check("남과 비슷한 수준이면 거절", voiceprint.verify(stranger)[0], None)
check("확실한 본인은 그대로 통과", voiceprint.verify(utt(0.72, 0.0, pad=3))[0], "홍길동")

# 학습이 남 쪽으로 끌려가지 않는다
check("남과 닮은 발화는 배우지 않는다",
      voiceprint.adapt("홍길동", stranger, 0.62), False)
check("남과 먼 발화는 배운다",
      voiceprint.adapt("홍길동", utt(0.80, 0.0, pad=3), 0.80), True)

print("\n[6] 오인 정정 — '방금 나야'")
before = voiceprint.enrolled()["홍길동"]
mine = utt(0.44, 0.10, pad=5)       # 문턱 아래로 잘못 거절된 내 목소리
voiceprint.remember_stranger(mine, 0.30)   # 남으로 잘못 들어갔다고 치고
check("정정하면 본인 지문이 는다",
      (voiceprint.forgive("홍길동", mine), voiceprint.enrolled()["홍길동"]),
      (True, before + 1))
check("오인의 원인이던 남 지문은 지운다",
      voiceprint.strangers() <= 1, True)
check("정정 뒤에는 통과한다", voiceprint.verify(mine)[0], "홍길동")

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과 — 문턱과 격차가 남의 목소리를 막는다")
