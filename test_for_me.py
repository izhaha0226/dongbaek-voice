#!/usr/bin/env python3
"""나에게 한 말인가 — 끼어들지 않기 판정의 회귀 시험.

여태 있던 장치는 전부 사후였다. 사장님이 "너한테 한 말 아니야" 하시거나,
클로드가 "제게 하신 말씀이 아닌 것 같아요" 하고 물러나거나. 둘 다 이미
끼어든 뒤다. router.is_for_me 는 답하기 전에 스스로 묻는 자리다.

여기서 지키는 균형이 이 판정의 전부다.
  세게 조이면 정작 쓰던 말이 막힌다 — 실측 425건에서 답한 것의 91%가
  호출어 없이 들어왔고 그 대부분이 정당했다("너 뭐하냐?", "지금 몇 시니?").
  느슨하면 TV·통화가 새어 든다.

그래서 양쪽을 같이 본다. 아래 [1]이 막혀야 할 것, [2]가 통과해야 할 것이다.
실데이터 채점은 tools/eval_intrusion.py (2026-08-16 기준 끼어듦 29→18건).

    python tests/test_for_me.py
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


print("[1] 남에게 한 말은 받지 않는다")
for t in [
    # 전언·3인칭 서사 — 동백에게 시키는 말에는 안 나오는 꼴
    "그러니까 본인이 광고 파트가 답답해서 직접 뛰어들었다고 하시더라고",
    "홍 회장이 그러시더라고. 견적은 다음 주에 보내면 된대요",
    # 타인 호칭
    "여보 그거 어디 뒀더라",
    "형님 그건 제가 확인해 보고 말씀드릴게요",
    # 맞장구 — 옆 사람과 주고받는 소리
    "그러니까 사람의 뇌가 참 신기하고. 그렇죠?",
]:
    check(f"{t[:34]}…", router.is_for_me(t), False)

print("\n[2] 나에게 한 말은 그대로 받는다")
for t in [
    "너 뭐하냐?",                      # 2인칭 — 대화창 안의 정상 발화
    "내 말 듣고 있니?",
    "지금 몇 시니?",                    # 표시가 없어도 짧으면 받는다
    "동백아 오늘 일정 알려줘",             # 호출어
    "내일 3시 치과 등록해줘",             # 시키는 말
    "남산 미팅 확인하라고, 일정 등록하라고",  # ECHO_TAIL 이 못 잡던 전달형 지시
]:
    check(f"{t[:34]}", router.is_for_me(t), True)

print("\n[3] 긴 말은 '지시로 끝날 때만' 받는다")
long_talk = ("어제 3시 반부터 미팅했는데 키워드 광고 얘기를 4시간 동안 들었어. "
             "본인이 광고 파트가 답답해서 직접 뛰어들었고 키워드 15만 개를 "
             "세팅해놨다고 하시더라고. 저녁까지 먹고 가라고 하셔서 그러기로 했어. "
             "홈페이지 문제도 물어보셨고. 그 얘기를 계속 하시더라니까.")
check(f"긴 잡담({len(long_talk)}자)은 안 받는다", router.is_for_me(long_talk), False)
check("같은 길이여도 지시로 끝나면 받는다",
      router.is_for_me(long_talk + " 이거 정리해서 알려줘"), True)
# ⚠ 문장 한가운데의 '해줘' 는 남에게 한 부탁이다. 끝만 본다.
check("한가운데 '해줘' 는 지시가 아니다",
      router.is_for_me("그래서 내가 김 부장한테 해줘 하고 말했는데 " + long_talk), False)

print("\n[4] 사람 말이 아닌 것")
check("빈 말", router.is_for_me(""), False)
check("한 글자", router.is_for_me("동"), False)
check("반복 환청", router.is_for_me("취소취소취소취소취소"), False)
check("방송 상투구", router.is_for_me("구독과 좋아요 부탁드립니다"), False)

print("\n[5] 가전 음성 안내 — 기계가 하는 말")
# 실사례 2026-08-16. 에어컨 안내가 세 조각으로 이어 붙어 클로드까지 갔다.
#   09:12 "여기 내 컴퓨터. 수긍 더... 생활 온도를 25도로 설정합니다."
#   10:25 "…희망 온도를 27도로 설치합니다. 비와? 이제 안 와?"
for t in ["여기 내 컴퓨터. 수긍 더... 생활 온도를 25도로 설정합니다",
          "희망 온도를 27도로 설치합니다", "전원을 켭니다", "운전을 시작합니다"]:
    check(f"기계 안내: {t[:24]}…", router.is_for_me(t), False)
# ⚠ 사람이 온도 얘기를 하실 수도 있다. 기계는 제가 한 일을 알리는 평서형으로
#   끝나고("설정합니다"), 사람은 시키는 말로 끝난다("맞춰줘").
for t in ["에어컨 25도로 맞춰줘", "온도 좀 낮춰줘", "지금 몇 도야?"]:
    check(f"사람 말: {t}", router.is_for_me(t), True)

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — 부르면 답하고, 남 얘기엔 가만히 있는다")
