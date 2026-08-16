#!/usr/bin/env python3
"""이어 받기 상한 — 통화가 통째로 명령이 되면 안 된다.

2026-08-12 22:10 로그: "한 덩어리로 받음 — 10조각, 182초".
사장님이 전화 통화를 하시는 3분 내내 동백이 그 말을 한 덩어리로 모아
클로드에 보냈다. TURN_MAX_SEC(180초)이 유일한 상한이었고 조각 수·글자 수
상한은 아예 없었다.

사장님께는 "묻는 질문에 답을 하나도 안 한다" 로 보였다 — 사실은 통화
내용을 붙들고 3분을 기다리고 있었다. 가장 나쁜 종류의 실패다.
답을 못 하는 것보다 '엉뚱한 걸 붙들고 있는 것' 이 알아채기 어렵다.

    python test_turn_cap.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import config

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


print("\n상한이 셋 다 있는가 (하나만으로는 못 막는다)")
check("시간 상한", hasattr(config, "TURN_MAX_SEC"), True)
check("조각 수 상한", hasattr(config, "TURN_MAX_PARTS"), True)
check("글자 수 상한", hasattr(config, "TURN_MAX_CHARS"), True)

print("\n값이 '명령' 의 크기인가")
check("30초 이하 (그날 182초를 모았다)", config.TURN_MAX_SEC <= 30, True)
check("조각 5개 미만 (그날 10조각을 모았다)", config.TURN_MAX_PARTS < 5, True)
check("글자 300자 이하", config.TURN_MAX_CHARS <= 300, True)
# 실제 명령은 짧다. 너무 조이면 긴 지시가 토막 난다.
check("그래도 한 문장은 넉넉히 담긴다 (100자 이상)",
      config.TURN_MAX_CHARS >= 100, True)

print("\n네 조각을 4초 간격으로 붙여도 30초 안에 끝나는가")
# 시간 상한만 믿으면 4초×7조각=28초 동안 통화 한 토막이 다 들어온다.
worst = config.TURN_MAX_PARTS * config.TURN_WAIT_LONG_SEC
print(f"  · 최대 {config.TURN_MAX_PARTS}조각 × {config.TURN_WAIT_LONG_SEC:.0f}초 = {worst:.0f}초")
check("조각 상한이 시간 상한보다 먼저 걸린다 (그게 실질 방어선)",
      worst < config.TURN_MAX_SEC, True)

print("\n⚠ 부르고 길게 말씀하시는 건 그대로 담는가")
# 사장님 지시(2026-08-11): "내가 1분 이상 얘기할 수도 있고 2분이 될 수도
# 있어. 그걸 네가 다 듣고 한 번에 대답할 수 있도록 해줘."
# 길이로 가르면 이 지시와 충돌한다. 호출어로 갈라야 둘 다 지켜진다.
src0 = open("dongbaek.py").read()
check("collect_turn 이 호출어 여부를 받는다", "woke: bool" in src0, True)
check("부르셨으면 상한을 건너뛴다", "if woke:\n            continue" in src0, True)
check("호출부가 호출어 여부를 넘긴다", "woke=woke" in src0, True)

print("\n모으는 중에 부르시면 즉시 멈추는가")
check("호출어가 수집을 끊는다", "모으는 중에 부르심" in src0, True)


print("\n코드에 상한이 실제로 걸려 있는가")
src = open("dongbaek.py").read()
check("조각 수로 멈춘다", "TURN_MAX_PARTS" in src, True)
check("글자 수로 멈춘다", "TURN_MAX_CHARS" in src, True)
check("왜 멈췄는지 로그를 남긴다", "그만 모음" in src, True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 통화가 통째로 명령이 되지 않습니다")
