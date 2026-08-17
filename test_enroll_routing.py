#!/usr/bin/env python3
"""'목소리 등록해줘' 가 정식 등록으로 가는가 — 2026-08-17 사고의 회귀 시험.

그날 사장님께 이렇게 안내했다. "동백아, 내 목소리 등록해줘 하시면 5문장을
불러줍니다. 따라 읽으시면 됩니다." 사장님이 그대로 하셨는데 돌아온 답은

    "지금 목소리를 홍길동 목소리로 기억했습니다."

한마디로 끝이었다. 5문장은 나오지 않았다.

원인은 분기 순서였다. dongbaek 의 청취 고리에 목소리 학습 분기가 둘인데,
가벼운 쪽(is_voice_enroll_request)이 '목소리 + 등록' 을 통째로 삼키고
continue 해서, 정식 등록(enroll_by_voice)에는 **음성으로 도달할 방법이
없었다.** 5문장 등록은 죽은 코드였다.

경계를 말뜻대로 다시 그었다.
    "기억해/외워/저장해"  → 지금 이 한 마디를 지문에 보탠다 (가볍다)
    "등록해/추가해"        → 5문장을 받아 제대로 만든다 (무겁다)
    단, 방금 거절당한 발화가 있으면 복구가 먼저다 (절차보다 급하다)

    python tests/test_enroll_routing.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import router

FAIL = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))


def light(t: str) -> bool:
    """가벼운 분기가 잡는가 (dongbaek 의 조건과 같은 식)."""
    n = router.normalize(t)
    return router.is_voice_correction(n) or router.is_voice_enroll_request(n)


print("[1] '등록/추가' 는 정식 5문장으로 간다")
for t in ("내 목소리 등록해줘",
          "동백아 내 목소리 등록해줘",
          "동백아, 내 목소리 등록해 줘",
          "김철수 목소리 등록해줘",
          "목소리 등록해줘",
          "내 음성 추가해줘"):
    check(f"{t!r}", router.wants_formal_enroll(t), True)

print("\n[2] '기억/외워/저장' 은 가벼운 쪽에 남는다")
# 이쪽까지 5문장으로 보내면, 한 마디 배워 달라는 부탁에 절차를 강요하게 된다.
for t in ("지금 목소리 기억해",
          "내 목소리 외워",
          "목소리 저장해",
          "방금 그거 내 목소리야"):
    check(f"{t!r} 는 정식이 아니다", router.wants_formal_enroll(t), False)
    check(f"    가벼운 쪽이 받는다", light(t), True)

print("\n[3] 묻는 말은 어느 쪽도 아니다")
# ⚠ 2026-08-13 에 "기억하고 있는 거지?" 에 "기억했습니다" 로 답해
#   질문이 영영 답을 못 받은 적이 있다. 같은 자리를 등록에도 판다 —
#   물어보실 때마다 다섯 문장을 읽으라고 하면 그게 더 나쁘다.
for t in ("기존 목소리도 다 기억하고 있는 거지?",
          "내 목소리 등록해놨어?",
          "내 목소리 등록돼 있나?",
          "목소리 등록했어?"):
    check(f"{t!r} 는 정식 등록이 아니다", router.wants_formal_enroll(t), False)

print("\n[4] 두 분기가 겹칠 때 정식이 이긴다")
# 이게 사고의 핵심이다. 겹치는 건 정상이고, 지는 쪽이 정해져 있어야 한다.
for t in ("내 목소리 등록해줘", "김철수 목소리 등록해줘"):
    both = light(t) and router.wants_formal_enroll(t)
    check(f"{t!r} 는 둘 다 걸린다(정상)", both, True)
    # dongbaek 의 실제 조건: 정식이면 가벼운 분기를 건너뛴다
    takes_light = (not (router.wants_formal_enroll(t) and True)) and light(t)
    check(f"    그래도 가벼운 쪽으로 새지 않는다", takes_light, False)

print("\n[5] 거절 복구가 절차보다 먼저다")
# 못 알아들어 답답하신 참에 "다섯 문장 읽으세요" 는 더 답답하다.
# 조건식에서 fresh_reject 가 True 면 정식을 건너뛰고 복구로 간다.
t = "내 목소리 등록해줘"
for fresh_reject, want in ((True, True), (False, False)):
    takes_light = (not (router.wants_formal_enroll(t) and not fresh_reject)) and light(t)
    check(f"거절 있음={fresh_reject} → 가벼운 복구로 간다", takes_light, want)

print("\n[6] 받아쓰기가 흔들려도 등록으로 간다")
# ⚠ 이게 '왜 자꾸 클로드와 로컬이 왔다 갔다' 의 정체였다 (2026-08-17).
#   실측 '내 목소를 등록해줘' — whisper 가 '목소리' 의 끝 글자를 흘렸다.
#   낱말을 통째로 요구하니 판정이 빗나갔고, 그대로 클로드로 샜다.
#   클로드에는 마이크가 없어서 "로컬에서 처리해야 합니다" 하고 멈춘다 —
#   사장님께는 '등록하다가 멈췄다' 로 보인다.
for t in ("내 목소를 등록해줘",
          "내 목소 등록해줘",
          "내 목쏘리 등록해줘",
          "동백아 내 목소를 등록해줘",
          "목소리등록해줘"):
    check(f"{t!r} 도 등록으로", router.enroll_request(t) is not None, True)
    check("    정식 5문장으로", router.wants_formal_enroll(t), True)

print("\n[6-b] 넓혔다고 엉뚱한 말까지 걸리면 안 된다")
# 넓히다 보면 반대편으로 넘어가기 쉽다. '목요일', '목장' 이 걸리면
# 멀쩡한 명령이 등록 절차로 끌려간다.
for t in ("오늘 일정 알려줘", "목요일에 등록해줘", "메일 등록해줘",
          "목장 등록해줘", "목소리 좋다", "모임 등록해줘"):
    check(f"{t!r} 는 등록이 아니다", router.enroll_request(t), None)

print("\n[7] 정식 경로가 이름도 제대로 넘긴다")
check("'김철수 목소리 등록해줘' → 김철수",
      router.enroll_request("김철수 목소리 등록해줘"), "김철수")
check("'내 목소리 등록해줘' → 이름 미지정(빈 문자열, None 아님)",
      router.enroll_request("내 목소리 등록해줘"), "")
check("등록 요청이 아니면 None",
      router.enroll_request("오늘 일정 알려줘"), None)

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — '등록해줘' 는 5문장으로, '기억해' 는 한 번에")
