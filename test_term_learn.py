#!/usr/bin/env python3
"""교정표 자동 학습 — 배워도 되는 것만 배우는가.

이 시험의 무게중심은 '무엇을 배우나' 가 아니라 '무엇을 안 배우나' 다.
잘못 배운 규칙은 사장님이 한 말을 소리 없이 다른 말로 바꿔치기한다 —
못 알아듣는 것보다 나쁘다. 아래 세 건은 실제 기록에서 나온 후보들이다.
    python test_term_learn.py
"""
import term_learn

passed = 0


def check(cond: bool, why: str) -> None:
    global passed
    assert cond, f"실패: {why}"
    print(f"  ✓ {why}")
    passed += 1


print("[1] 조사는 끝까지 뗀다 — 겹쳐 붙는다")
check(term_learn._strip_particle("동백이가") == "동백", "'동백이가' → '동백'")
check(term_learn._strip_particle("레일웨이에서") == "레일웨이", "'레일웨이에서' → '레일웨이'")
check(term_learn._strip_particle("에임") == "에임", "더 떼면 이름이 사라지는 건 안 뗀다")

print("\n[2] 배우면 안 되는 짝")
check(term_learn._is_noise("동백동", "동백") is not None,
      "'동백동'(사장님 동네)은 '동백'으로 안 바꾼다")
check(term_learn._is_noise("에임", "한빛기획") is not None,
      "두 글자 앞토막 '에임'은 넘겨짚지 않는다")
check(term_learn._is_noise("광고플랫폼에서", "광고플랫폼") is not None,
      "이름을 제대로 들은 말은 오기가 아니다")

print("\n[3] 배워도 되는 짝")
check(term_learn._is_noise("레일웨", "레일웨이") is None, "'레일웨'는 잘린 오기가 맞다")
check(term_learn._is_noise("홍배", "동백") is None, "닮은 다른 표기는 배울 수 있다")

print("\n[4] 규칙은 낱말 통째로일 때만 문다")
import json, re
raw = json.loads(term_learn.config.TERM_LEARNED_FILE.read_text(encoding="utf-8"))
for r in raw.get("rules", []):
    rx = re.compile(r["pattern"])
    check(not rx.search(r["right"]),
          f"'{r['right']}' 안에서는 '{r['wrong']}' 규칙이 안 터진다")

print(f"\n✅ 전부 통과 — {passed}건. 아는 이름에 닿은 오기만 배운다")
