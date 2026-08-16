#!/usr/bin/env python3
"""붙여 쓴 반복 환청은 사람 말이 아니다 — "HeyHeyHey…", "취소취소취소…".

whisper 는 잡음에서 짧은 조각을 수백 번 되풀이해 적는다. is_noise 의
기존 두 규칙은 이걸 못 잡았다 — 한 글자 반복이 아니고(_REPEAT_CHAR),
사이에 공백·쉼표가 없어 낱말 반복(_REPEAT_WORD)에도 안 걸린다.

놓치면 길이가 부풀어 안전 판단을 흔든다. 2026-08-15 12:35 실측 —
669자짜리 "HeyHey…" 가 '긴 발화' 로 집계돼 전화 모드 진입 표를 던졌다
(100자 발화가 2회 이어지면 10분 무음). 통화 조각으로도 모여
클로드 정리에 섞였다 (state/call_dropped.jsonl 의 "정취소취소취소…").

지키려는 것:
  ① 붙여 쓴 반복은 잡음으로 본다 (전부 들림 로그 실측 문구)
  ② ⚠ 띄어 말한 진짜 반복은 사람 말이다 — 여기서 넓히면 말씀이 사라진다
  ③ 기존 두 규칙과 평범한 말씀은 그대로다
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import router

FAIL = []


def check(name, got, want=True):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


print("[1] 붙여 쓴 반복 환청 — 전부 실측 문구 (state/daemon.log 들림)")
for said in [
    "Hey" * 223,                                   # 08-15 12:29·12:35, 669자
    "정" + "취소" * 74,                            # 08-15 14:14 외 6건
    "그뿐" + "gard" * 224,                          # 08-15 12:34
    "얄아주기" + "aja" * 218,                       # 658자
    "oten" * 223,
    "닉" + "lı" * 221,
    "일단 옆에" + "miş" * 6 + "?",                  # 24자짜리 짧은 것도 잡는다
    "ierung" * 5,
    "gua" * 6,
]:
    check(f"{said[:14]!r}… ({len(said)}자) → 잡음", router.is_noise(said), True)

print("\n[2] ⚠ 띄어 말한 반복까지 잡으면 안 된다 — 새 규칙만 따로 본다")
# ⚠ 여기서는 is_noise 가 아니라 새 규칙 하나만 본다. "네 네 네" 같은
#   세 글자 이하 반복은 예전부터 _REPEAT_WORD 가 잡던 것이라, is_noise
#   로 재면 누가 잡았는지가 섞인다. 새 규칙이 넓어졌는지만 재야 한다.
for said in [
    # 실측 문구 (들림 로그). 공백까지 허용하면 이 한 건이 걸렸다.
    "아이파로. 200씩 200씩 200씩 200씩",
    "감사합니다 감사합니다 감사합니다 감사합니다",
    "괜찮아요 괜찮아요 괜찮아요 괜찮아요",
]:
    check(f"{said[:20]!r} → 새 규칙 밖",
          router._REPEAT_CHUNK.search(said) is None, True)

print("\n[3] 평범한 말씀과 기존 두 규칙은 그대로다")
for said, noise in [
    ("동백아 몇시야", False),
    ("오늘 일정 알려줘", False),
    ("내일 오전 10시에 승례문 피팅 일정 잡아줘", False),
    # 받침·모음이 겹쳐도 4회 연속이 아니면 말이다
    ("감사합니다 감사합니다", False),
    ("문문문문문문문문", True),          # _REPEAT_CHAR
    ("다윗, 다윗, 다윗, 다윗.", True),   # _REPEAT_WORD
    ("동.", True),
]:
    check(f"{said[:16]!r} → {'잡음' if noise else '사람 말'}",
          router.is_noise(said), noise)

print("\n[4] 긴 환청도 제때 끝난다 — 되짚기로 굳으면 귀가 멎는다")
import time
t0 = time.monotonic()
for _ in range(50):
    router.is_noise("Hey" * 500)
    router.is_noise("안녕하세요 오늘 일정 좀 알려주시고 메일도 확인해 주세요 " * 20)
took = time.monotonic() - t0
check(f"100번 검사 {took:.3f}초 — 0.5초 미만", took < 0.5, True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 붙여 쓴 반복 환청은 사람 말이 아니다")
