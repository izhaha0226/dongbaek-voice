#!/usr/bin/env python3
"""나은 갈래는 다시 말할 수 있어야 한다 — 2026-08-17.

어제 붙인 되풀이 방지(fbd5a06)는 억제를 **갈래**로 했는데, 잊는 쪽은
'이상이 하나도 없을 때 통째로' 그대로였다. 키가 한쪽만 갈래였던 것이다.

그 비대칭이 만든 판이 실제 로그에 있다 — 08-16 22:29~08-17 01:05.

    22:29  귀먹음 + 음량낮음        ← 텔레그램으로 나감
    22:44  귀먹음 + 음량낮음
    …
    01:05  음량낮음만 (귀먹음은 나았다)

음량낮음이 열한 판 내내 켜져 있어 '하나도 없는 판' 이 한 번도 안 왔다.
그래서 said 는 영영 안 비고, 01:05 에 나은 귀먹음은 **다시 도져도**
입을 못 연다. 이상을 먼저 알리라고 만든 기능이 그 자리에서 벙어리가 된다.

여기서 지키는 것.
  1. 만성 이상이 걸려 있어도, 나았다 도진 갈래는 다시 알린다
  2. 그렇다고 한 판 깜빡였다고 잊지는 않는다 (두 판 내리 없어야 잊는다)
  3. 어제 고친 것은 그대로 — 그날 밤 열한 판은 여전히 한 번이다

    python tests/test_selfcheck_forget.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import selfcheck

FAIL = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))


# 갈래 이름만 있으면 되는 자리다 — 문턱·로그 파싱은 test_selfcheck_repeat 이 본다.
귀 = ("귀먹음", "소리는 들리는데 명령까지 못 갑니다. 최근 2시간 들림 91건 중 명령 0건.")
음 = ("음량낮음", "마이크에 잡히는 소리가 작습니다(중앙 0.0144).")


def sent(rounds):
    """판마다 실제로 텔레그램에 나간 갈래들. 데몬이 부르는 그 함수를 그대로 쓴다."""
    said, out = {}, []
    for bad in rounds:
        fresh = selfcheck.pick_fresh(list(bad), said)
        if fresh:
            out.append([k for k, _ in fresh])
    return out


print("[1] 만성 이상이 걸려 있어도 나았다 도진 갈래는 다시 알린다")
# 음량낮음은 내내 켜져 있다. 귀먹음만 두 판 쉬었다 돌아온다 — 그날 밤 그대로다.
나간것 = sent([(귀, 음), (음,), (음,), (귀, 음)])
check("두 번 나간다", len(나간것), 2)
check("첫 판은 둘 다", 나간것[0], ["귀먹음", "음량낮음"])
check("도진 뒤에는 귀먹음만", 나간것[1], ["귀먹음"])

print("\n[2] 고치기 전이면 두 번째는 영영 안 나간다")
# ⚠ 되돌아가지 않는지 보는 자리. 옛 방식(하나라도 남으면 안 잊는다)을
#   같은 판으로 직접 돌려 한 번뿐인 것을 센다.
said_old, 옛것 = set(), []
for bad in [(귀, 음), (음,), (음,), (귀, 음)]:
    fresh = [(k, t) for k, t in bad if k not in said_old]
    if fresh:
        said_old.update(k for k, _ in fresh)
        옛것.append([k for k, _ in fresh])
    elif not bad:
        said_old.clear()
check("옛 방식은 한 번뿐", len(옛것), 1)

print("\n[3] 한 판 깜빡였다고 잊지는 않는다")
# 귀먹음은 '들림 12건' 문턱 언저리에서 켜졌다 꺼졌다 한다. 한 판만 보고
# 잊으면 15분마다 되풀이하던 어제로 되돌아간다.
나간것 = sent([(귀, 음), (음,), (귀, 음), (음,), (귀, 음)])
check("깜빡임은 다시 안 알린다", len(나간것), 1)

print("\n[4] 어제 고친 것은 그대로 — 같은 이상이 이어지면 한 번")
나간것 = sent([(귀, 음)] * 11)
check("열한 판이 한 번", len(나간것), 1)

print("\n[5] 통째로 멀쩡한 판을 지나면 곧바로 입이 열린다")
# 갈래 하나가 깜빡이는 것과 달리 '다 나았다' 는 흔들릴 여지가 없다.
나간것 = sent([(귀, 음), (), (귀, 음)])
check("한 판만 멀쩡해도 다시 알린다", len(나간것), 2)

print("\n[6] 잊은 갈래는 said 에서 실제로 빠진다")
said = {}
selfcheck.pick_fresh([귀, 음], said)
selfcheck.pick_fresh([음], said)
selfcheck.pick_fresh([음], said)
check("귀먹음은 빠지고", "귀먹음" not in said)
check("음량낮음은 남는다", said.get("음량낮음"), 0)

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — 만성 이상에 묻혀 벙어리가 되지 않는다")
