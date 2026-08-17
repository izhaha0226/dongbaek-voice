#!/usr/bin/env python3
"""등록 이름이 호출어로 오염되지 않는가 — 2026-08-17 사고의 회귀 시험.

사장님이 "얘 지금 좀 이상해" 하셔서 지문을 열어 보니 이렇게 돼 있었다.

    홍길동 15개 · 김철수 5개 · **동백아 5개** · **동백이 1개**

사장님 목소리가 세 이름으로 쪼개져 있었다. 경위는 이렇다.

  1. "동백아, 내 목소리 등록해줘" 에서 이름을 못 뽑았다 ('내' 는 금지어)
  2. 그래서 "등록할 이름을 말씀해 주세요" 하고 되물었다
  3. 그 대답이 '동백이' 로 받아써졌다 — 호출어가 이름이 됐다
  4. 5문장이 그 이름 밑에 저장됐다

지문이 쪼개지면 어느 쪽으로도 문턱을 못 넘어 "목소리를 못 알아봐 무시했다"
가 오히려 늘어난다. 고치려고 등록했는데 더 나빠지는 것이다.

여기서 지키는 것.
  1. 호출어는 이름이 될 수 없다 (어느 경로로 들어오든)
  2. **애초에 되묻지 않는다** — 이 자리는 화자 확인을 통과한 뒤라
     '내 목소리' 가 누구인지 이미 안다. 한 번 더 묻는 건 틀릴 기회를
     한 번 더 주는 것뿐이다.
  3. 멀쩡한 사람 이름은 그대로 통과한다

    python tests/test_enroll_name.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config
import router

FAIL = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))


print("[1] 호출어는 이름이 아니다")
for w in ("동백아", "동백이", "동배가", "똥백아", "동백", "동백이가", "동백아까"):
    check(f"{w!r}", router.is_wake_like(w), True)
check("설정의 호출어 전부가 막힌다",
      all(router.is_wake_like(w) for w in config.WAKE_WORDS
          if "동백" in w or "똥백" in w), True)

print("\n[2] 사람 이름은 통과한다")
for n in ("김철수", "홍길동", "이영희", "박영수", "정민호"):
    check(f"{n!r} 는 이름이다", router.is_wake_like(n), False)
check("빈 값은 이름 아님으로 보지 않는다(따로 처리)", router.is_wake_like(""), False)

print("\n[3] 되물었을 때의 대답에서도 걸러낸다")
# 사고가 난 바로 그 경로다. clean_name 이 호출어를 돌려주면 그대로 저장된다.
check("'동백이' → 이름 없음", router.clean_name("동백이"), "")
check("'동백아' → 이름 없음", router.clean_name("동백아"), "")
check("'이름은 동백이야' → 이름 없음", router.clean_name("이름은 동백이야"), "")
check("'김철수이야' → 김철수", router.clean_name("김철수이야"), "김철수")
check("'이름은 김철수이야' → 김철수", router.clean_name("이름은 김철수이야"), "김철수")

print("\n[4] 지시문에서 이름을 뽑을 때도 마찬가지")
check("'동백아 내 목소리 등록해줘' 는 이름 미지정",
      router.enroll_request("동백아 내 목소리 등록해줘"), "")
check("'김철수 목소리 등록해줘' 는 김철수",
      router.enroll_request("김철수 목소리 등록해줘"), "김철수")

print("\n[5] 이름을 못 뽑으면 되묻지 않고 확인된 화자를 쓴다")
# ⚠ 이게 진짜 고침이다. 되묻는 한 언젠가 또 잘못 듣는다.
#   dongbaek 의 실제 식과 같은 꼴로 흉내 낸다.
def resolve(command, who):
    name = router.enroll_request(command)
    if name is None:
        return None
    if not name and who and not router.is_wake_like(who):
        name = who
    return name

check("'내 목소리 등록해줘' + 화자 홍길동 → 홍길동",
      resolve("내 목소리 등록해줘", "홍길동"), "홍길동")
check("'동백아 내 목소리 등록해줘' + 화자 홍길동 → 홍길동",
      resolve("동백아 내 목소리 등록해줘", "홍길동"), "홍길동")
check("이름을 말씀하셨으면 그쪽이 이긴다",
      resolve("김철수 목소리 등록해줘", "홍길동"), "김철수")
check("화자를 모르면 빈 값 (그때만 되묻는다)",
      resolve("내 목소리 등록해줘", ""), "")
check("화자가 호출어로 잘못 잡혀 있어도 안 쓴다",
      resolve("내 목소리 등록해줘", "동백이"), "")

print("\n[6] 지문에 호출어 이름이 남아 있지 않다")
import numpy as np

p = ROOT / "state" / "voiceprints.npz"
if not p.exists():
    print("  · 지문 파일이 없어 건너뜁니다")
else:
    names = [k.split("#")[0] for k in np.load(p).files]
    dirty = sorted({n for n in names if router.is_wake_like(n)})
    check(f"오염된 이름 없음 (지금: {sorted(set(names))})", dirty, [])

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — 호출어는 이름이 안 되고, 아는 사람은 안 되묻는다")
