#!/usr/bin/env python3
"""시험이 사장님 캘린더를 건드리지 않는가 — 2026-08-17 사고의 회귀 시험.

그날 사장님이 캘린더를 보시고 물으셨다. "QA 일정이 왜 이렇게 많아????
그리고 QA는 일정이 아닌데 왜 이렇게 일정으로 잡혀있어???"

'큐에이확인' 이 47건 있었다. 범인은 tests/test_tonight_qa.py 였다.

    router.handle_local("내일 오후 4시 큐에이확인 등록해줘")   # 가로채기 없음

시험을 돌릴 때마다 진짜 일정이 하나씩 생겼다. 시험 끝에서 지우기도 하니
본전이어야 하는데, 한 번이라도 어긋나 2건이 되는 순간 delete_matching 이
"여러 건이라 애매하다" 며 손을 뗀다 — 그 판단 자체는 옳다. 문제는 그 뒤로
만들기만 하고 못 지운다는 것이다. 되돌아오지 않는 톱니바퀴다.

고친 자리는 시험 파일이 아니라 calendar_local 이다. 아홉 개 시험이 이
경로를 태우고 있어서, 파일마다 가로채기를 넣는 건 규율에 기대는 것이고
새 시험이 하나 생길 때마다 다시 뚫린다. 문을 잠그는 게 맞다.

여기서 지키는 것.
  1. 시험으로 돌 때 쓰기 함수 셋이 전부 막힌다
  2. 막히되 조용히 실패하지 않는다 — 왜 안 썼는지 말해 준다
  3. 읽기는 막지 않는다 (막으면 멀쩡한 시험들이 다 깨진다)
  4. 데몬으로 돌 때는 당연히 열려 있다

    python tests/test_calendar_test_guard.py
"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import calendar_local as C

FAIL = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))


print("[1] 지금이 시험 실행으로 판정된다")
# 이 파일 이름이 test_ 로 시작하므로 참이어야 한다. 이게 거짓이면
# 아래 시험들이 전부 '통과' 로 보이면서 실제로는 아무것도 안 지킨다.
check(f"argv[0]={os.path.basename(sys.argv[0])!r} → 시험", C._is_test_run(), True)

print("\n[2] 쓰기 함수 셋이 전부 막힌다")
tomorrow = datetime.now() + timedelta(days=1)
r_create = C.create("큐에이확인", tomorrow.replace(hour=16, minute=0), 1.0)
r_del1 = C.delete_matching("큐에이확인")
r_del2 = C.delete_day(tomorrow.date())
check("create 가 캘린더에 쓰지 않는다", "시험" in (r_create or ""), True)
check("delete_matching 이 지우지 않는다", "시험" in (r_del1 or ""), True)
check("delete_day 가 지우지 않는다", "시험" in (r_del2 or ""), True)

print("\n[3] 조용히 실패하지 않는다")
# ⚠ None 을 돌려주면 '권한 없음' 과 구별이 안 된다. 그러면 호출한 쪽이
#   Claude 로 폴백해서 결국 다른 길로 진짜 일정을 만들 수도 있다.
#   왜 안 썼는지 말로 남겨야 사람이 읽고 안다.
for name, r in (("create", r_create), ("delete_matching", r_del1), ("delete_day", r_del2)):
    check(f"{name} 는 None 이 아니라 이유를 돌려준다", r is not None, True)

print("\n[4] 읽기는 막지 않는다")
# 읽기까지 막으면 브리핑·일정조회 시험이 전부 깨진다. 사고는 쓰기에서 났다.
evs = C.events(days=1)
check("events() 는 그대로 돈다 (None 은 권한 문제일 뿐 빗장 아님)",
      evs is None or isinstance(evs, list), True)

print("\n[5] 실제로 아무것도 안 생겼다")
# 위에서 create 를 불렀다. 정말 안 만들어졌는지 눈으로 확인한다 —
# 반환값만 믿으면 '말은 안 썼다고 하고 실제로는 쓴' 경우를 못 잡는다.
after = C.events(days=2)
if after is None:
    print("  · 캘린더 권한이 없어 확인을 건너뜁니다 (CI 등)")
else:
    made = [e for e in after if "큐에이" in (e.get("title") or "")]
    check(f"방금 만든 '큐에이확인' 이 캘린더에 없다 ({len(made)}건)", made, [])

print("\n[6] 데몬으로 돌 때는 열려 있다")
_real_argv0 = sys.argv[0]
try:
    sys.argv[0] = "~/dongbaek/dongbaek.py"
    check("dongbaek.py 로 돌면 빗장이 풀린다", C._is_test_run(), False)
    sys.argv[0] = "/usr/bin/python3"
    check("이름 없는 실행도 빗장 아님", C._is_test_run(), False)
finally:
    sys.argv[0] = _real_argv0
check("원래대로 돌려놨다", C._is_test_run(), True)

print("\n[7] 사고를 낸 그 시험이 지금은 안전하다")
# tests/test_tonight_qa.py 는 여전히 가로채기 없이 handle_local 을 태운다.
# 그래도 되는 이유는 문이 잠겼기 때문이다. 그 전제가 사라지면 알아야 한다.
# ⚠ 경로를 박아 두면 안 된다. 공개판은 평평해서 시험이 최상위에 있다 —
#   'tests/' 를 못 찾아 시험이 통째로 죽었다 (2026-08-17 공개판 반영 때).
_qa = ROOT / "tests" / "test_tonight_qa.py"
if not _qa.exists():
    _qa = ROOT / "test_tonight_qa.py"
src = _qa.read_text(encoding="utf-8") if _qa.exists() else ""
check("사고를 낸 시험을 찾았다", bool(src), True)
check("그 시험은 아직 등록 지시를 태운다", "큐에이확인 등록해줘" in src, True)
check("그런데도 캘린더는 안전하다 (빗장 덕분)", C._is_test_run(), True)

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — 시험은 캘린더를 읽기만 한다")
