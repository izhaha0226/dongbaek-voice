#!/usr/bin/env python3
"""호칭 — 처음과 화자가 바뀔 때만 부르고, 이어지는 대화에서는 안 부른다.

사장님 지시(2026-08-12):
  "이건 처음에만 확인하고, 이후 대화가 연결되면 굳이 복명복창,
   '홍길동님 말씀드리겠습니다' 하지 말고 자연스럽게 이어서 대화될 수 있게
   해줘. 중간에 김철수이 끼어서 질문하면 그때 화자가 바뀐 걸 인식하고
   '김철수님 답변드리겠습니다' 라고 할 수 있도록 하자."

즉 호칭의 쓸모는 '누구에게 하는 답인지 알리는 것' 하나다. 혼자 이어서
말씀하시는 동안에는 알릴 게 없으니 붙이지 않는다. 매번 붙으면 대화가
아니라 방송이 된다.

    python test_address.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import config
import dongbaek

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


def reset():
    dongbaek._LAST_ADDRESSED["who"] = ""
    dongbaek._LAST_ADDRESSED["at"] = 0.0


print("\n혼자 이어서 말씀하실 때")
reset()
first = dongbaek._address("홍길동")
check("첫 답에는 이름을 부른다", "홍길동" in first, True)
check("이어지는 답에는 안 부른다", dongbaek._address("홍길동"), "")
check("계속 이어져도 안 부른다", dongbaek._address("홍길동"), "")

print("\n중간에 김철수이 끼어들면")
second = dongbaek._address("김철수")
check("화자가 바뀌면 바로 부른다", "김철수" in second, True)
check("김철수이 이어 말하면 안 부른다", dongbaek._address("김철수"), "")
back = dongbaek._address("홍길동")
check("다시 홍길동로 바뀌면 또 부른다", "홍길동" in back, True)

print("\n한동안 조용했다가 다시 부르시면")
reset()
dongbaek._address("홍길동")
# 대화가 끊긴 상황을 만든다 (마지막 응대 시각을 과거로)
dongbaek._LAST_ADDRESSED["at"] -= config.ADDRESS_REPEAT_SEC + 1
check("새 대화의 첫 답에는 다시 부른다",
      "홍길동" in dongbaek._address("홍길동"), True)

print("\n복명복창도 같은 규칙 (사장님: '홍길동님 답변드리겠습니다 하고 동일한 경우')")
dongbaek._LAST_ECHOED["who"], dongbaek._LAST_ECHOED["at"] = "", 0.0
check("첫 명령은 되읊어 확인한다", dongbaek._should_echo("홍길동"), True)
check("이어지는 명령은 안 되읊는다", dongbaek._should_echo("홍길동"), False)
check("계속 이어져도 안 되읊는다", dongbaek._should_echo("홍길동"), False)
check("김철수이 끼어들면 다시 확인한다", dongbaek._should_echo("김철수"), True)
check("김철수이 이어 말하면 안 되읊는다", dongbaek._should_echo("김철수"), False)
dongbaek._LAST_ECHOED["at"] -= config.ADDRESS_REPEAT_SEC + 1
check("한동안 조용했다 다시 시키면 되읊는다",
      dongbaek._should_echo("김철수"), True)

print("\n호칭과 복명복창이 서로의 시계를 밀지 않는다")
# 통을 하나로 쓰면 복명복창이 호칭의 시계를 갱신해 호칭이 영영 안 붙는다
dongbaek._LAST_ADDRESSED["who"], dongbaek._LAST_ADDRESSED["at"] = "", 0.0
dongbaek._LAST_ECHOED["who"], dongbaek._LAST_ECHOED["at"] = "", 0.0
dongbaek._should_echo("홍길동")                      # 명령을 받는 순간
check("복명복창을 했어도 그 턴의 호칭은 붙는다",
      "홍길동" in dongbaek._address("홍길동"), True)  # 답을 내보내는 순간


print("\n모르는 사람에게는 이름을 붙이지 않는다")
reset()
check("화자 미상", dongbaek._address(""), "")
check("검증 불가", dongbaek._address("(검증불가)"), "")

print("\n설정으로 끌 수 있다")
reset()
old = config.ADDRESS_BY_NAME
config.ADDRESS_BY_NAME = False
try:
    check("꺼두면 아무에게도 안 붙인다", dongbaek._address("홍길동"), "")
finally:
    config.ADDRESS_BY_NAME = old

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 처음과 화자 전환에만 부르고, 이어지는 대화는 자연스럽습니다")
