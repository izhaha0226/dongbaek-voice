#!/usr/bin/env python3
"""오래 걸릴 때 살아 있다고 말하는가 — 2026-08-17 지시의 시험.

사장님: "작업을 계속 하고 있으면 중간중간 작업중임을 알려줘. 그래야 또
안 부르지."

침묵이 길면 다시 부르신다. 그러면 앞 명령과 뒤 명령이 합쳐져 눈덩이가
굴러가고 같은 일이 두 번 돈다. 오늘 로그에도 "동백아?" 를 세 번 부르신
기록이 있다. 말 한마디로 막을 수 있는 일이다.

⚠ 이 기능은 이미 한 번 사장님을 화나게 했다. 2026-08-12 04:18, 잡담이
  dev 로 잘못 분류돼 2분 넘게 돌았고 그동안 "조금만 더 기다려 주세요"
  만 나갔다 — "조금만더 기다려주세요 하고 또 생까네".
  그래서 여기서 지키는 것은 '알린다' 만이 아니다.

  1. 짧은 일에는 입을 열지 않는다      (대부분의 답이 여기 해당한다)
  2. 답이 나오기 시작하면 즉시 다문다
  3. **같은 말을 되풀이하지 않는다**    — 되풀이는 알림이 아니라 소음이고,
     게다가 speak.say 가 같은 문장을 통째로 버려서 오히려 감감무소식이 된다
  4. 끝없이 떠들지 않는다               (상한이 있다)
  5. 터져도 멈춘다                      (예외로 빠져나가도 입을 다문다)

    python tests/test_working_notice.py
"""
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config
import dongbaek
import speak

FAIL = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))


# speak.say 를 가로채 무엇이 나갔는지만 모은다. 소리는 내지 않는다.
SAID: list[tuple[str, int]] = []
_real_say = speak.say


def fake_say(text, **kw):
    SAID.append((text, kw.get("priority", speak.PRIORITY_REPLY)))


speak.say = fake_say

# 시험이 실제로 12초씩 기다릴 수는 없다. 간격만 줄이고 규칙은 그대로 본다.
config.WORKING_NOTICE_FIRST = 0.10
config.WORKING_NOTICE_EVERY = 0.10
config.WORKING_NOTICE_MAX = 4

SPEAKER = object()   # '소리 낼 상대가 있다' 는 뜻만 있으면 된다


print("[1] 짧은 일에는 입을 열지 않는다")
SAID.clear()
with dongbaek.WorkingNotice(SPEAKER):
    time.sleep(0.02)          # 첫 알림 시각(0.10초) 전에 끝난다
check("아무 말도 안 했다", SAID, [])

print("\n[2] 오래 걸리면 알린다")
SAID.clear()
with dongbaek.WorkingNotice(SPEAKER):
    time.sleep(0.35)
check("한 번 이상 알렸다", len(SAID) >= 2, True)
check("알림 우선순위로 보낸다",
      all(p == speak.PRIORITY_NOTICE for _, p in SAID), True)
check("첫 마디가 '작업 중' 이다", SAID[0][0].startswith("작업 중입니다"), True)

print("\n[3] 같은 말을 되풀이하지 않는다")
# ⚠ 이게 이 시험의 핵심이다. 문구가 같으면 speak.say 의 중복 차단에
#   걸려 두 번째 알림이 조용히 사라진다 — 알린 줄 알았는데 침묵이 된다.
first_four = [t for t, _ in SAID[:4]]
check(f"연속 문구가 서로 다르다 ({len(set(first_four))}/{len(first_four)} 가지)",
      len(set(first_four)), len(first_four))

print("\n[4] 답이 나오기 시작하면 즉시 다문다")
SAID.clear()
n = dongbaek.WorkingNotice(SPEAKER)
with n:
    time.sleep(0.15)          # 한 번쯤 나간 뒤
    got_first = len(SAID)
    n.hush()                  # 첫 답 조각 도착
    time.sleep(0.35)          # 여기서는 더 나오면 안 된다
check("다문 뒤로는 늘지 않는다", len(SAID), got_first)

print("\n[5] 끝없이 떠들지 않는다")
SAID.clear()
with dongbaek.WorkingNotice(SPEAKER):
    time.sleep(0.10 + 0.10 * (config.WORKING_NOTICE_MAX + 3))
check(f"상한 {config.WORKING_NOTICE_MAX}회를 넘지 않는다",
      len(SAID) <= config.WORKING_NOTICE_MAX, True)

print("\n[6] 터져도 멈춘다")
SAID.clear()
try:
    with dongbaek.WorkingNotice(SPEAKER):
        time.sleep(0.15)
        raise RuntimeError("호출 실패")
except RuntimeError:
    pass
after = len(SAID)
time.sleep(0.35)
check("예외로 빠져나가도 입을 다문다", len(SAID), after)

print("\n[7] 소리 낼 상대가 없으면 아예 돌지 않는다")
SAID.clear()
with dongbaek.WorkingNotice(None) as n2:
    time.sleep(0.35)
check("스레드를 띄우지 않는다", n2.enabled, False)
check("아무 말도 안 했다", SAID, [])

print("\n[8] 끄면 꺼진다")
config.WORKING_NOTICE = False
SAID.clear()
with dongbaek.WorkingNotice(SPEAKER):
    time.sleep(0.35)
check("설정으로 끌 수 있다", SAID, [])
config.WORKING_NOTICE = True

print("\n[9] 오래 걸리면 몇 분 지났는지 같이 말한다")
# 기다리라는 말만 반복하면 그게 2026-08-12 의 그 소음이다.
check("1분 전에는 안 붙인다", "분" in dongbaek._working_line(1, 30.0), False)
check("1분 넘으면 붙인다", "일분 넘었습니다" in dongbaek._working_line(2, 75.0), True)
check("2분", "이분 넘었습니다" in dongbaek._working_line(3, 130.0), True)
check("숫자가 아니라 한글로", any(c.isdigit() for c in dongbaek._working_line(3, 130.0)), False)
check("표를 넘어가도 안 죽는다", bool(dongbaek._working_line(99, 9999.0)), True)

print("\n[9-b] 실제 간격에서 여섯 번이 전부 다른 말이다")
# ⚠ 이 항목이 없어서 버그를 놓칠 뻔했다. 위 시험들은 간격을 0.1초로
#   줄여 돌리는데, 그러면 '분' 이 한 번도 안 붙어 뒷 문구들이 구별된다.
#   실제 간격(12초 + 20초씩)에서는 4·5·6번째가 전부 '일분' 구간이라,
#   문구까지 같으면 speak.say 가 통째로 버린다 — 오래 기다리실 때
#   딱 침묵하는 것이다. 그래서 **진짜 초시계로** 만들어 본다.
FIRST, EVERY = 12.0, 20.0
lines = [dongbaek._working_line(i, FIRST + EVERY * (i - 1)) for i in range(1, 7)]
for i, s in enumerate(lines, 1):
    print(f"       {int(FIRST + EVERY * (i - 1)):>3}초  {s}")
check("여섯 마디가 서로 다르다", len(set(lines)), len(lines))
check("1분 넘어가면 경과를 말한다", "분 넘었습니다" in lines[-1], True)

print("\n[10] 뒤에 남은 스레드가 없다")
time.sleep(0.3)
alive = [t.name for t in threading.enumerate()
         if t is not threading.current_thread() and t.is_alive()
         and "_loop" in str(getattr(t, "_target", ""))]
check(f"살아남은 알림 스레드 없음 ({alive})", alive, [])

speak.say = _real_say

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — 짧으면 조용하고, 길면 알리되 같은 말은 안 한다")
