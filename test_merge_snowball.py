#!/usr/bin/env python3
"""이어말 병합이 눈덩이가 되지 않는가 — 2026-08-14·15 사고의 회귀 시험.

MERGE_ON_INTERRUPT 은 답변 중에 말을 보태시면 앞 명령과 합쳐 다시 묻는
기능이다. 의도는 옳은데 조건이 헐거워 두 번 사고가 났다.

  창이 180초였을 때, TV 를 켜두면 동백이 답하는 동안 늘 소리가 나서
  '말하는 중' 조건이 항상 참이 되고, 붙을 때마다 창이 갱신돼 닫히지
  않았다. 전사본이 3,499자까지 불어나 캘린더에 9건이 박혔고(08-14),
  취소된 "비밀번호…" 가 네 번 되살아났다(08-15).

길이 상한은 원래도 있었지만(COMMAND_MAX_CHARS_NO_WAKE) 사장님 목소리면
통과시키는 예외로 뚫렸다. 화자 인증은 '누가 말했나' 만 알지 '누구에게 한
말인가' 는 모른다. 그래서 목소리와 무관한 문턱 셋을 둔다 — 창·길이·횟수.

    python tests/test_merge_snowball.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import config

FAIL = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))


print("[1] 문턱 셋이 다 있고, 사고 때 값으로 돌아가 있지 않다")
check("창이 60초 이하", config.MERGE_WINDOW_SEC <= 60, True)
check("창이 180초(사고 때 값)가 아님", config.MERGE_WINDOW_SEC != 180.0, True)
check("길이 상한 존재·1000자 미만",
      0 < getattr(config, "MERGE_MAX_CHARS", 0) < 1000, True)
check("횟수 상한 존재·10회 미만",
      0 < getattr(config, "MERGE_MAX_COUNT", 0) < 10, True)

src = (ROOT / "dongbaek.py").read_text(encoding="utf-8")
blk = src[src.index("merged_now = False"):][:2500]

print("\n[2] 두 상한이 실제로 병합 경로에 걸려 있다")
check("길이 상한을 본다", "MERGE_MAX_CHARS" in blk)
check("횟수 상한을 본다", "MERGE_MAX_COUNT" in blk)
check("붙을 때마다 계수를 올린다", "_merge_count += 1" in blk)

print("\n[3] 상한은 화자 검사보다 '앞' 이다")
# 뚫린 곳이 그 화자 예외였다. 뒤에 두면 '사장님 목소리면 통과' 로 또 샌다.
check("MERGE_MAX_CHARS 가 _speaker_ok 보다 먼저",
      blk.index("MERGE_MAX_CHARS") < blk.index("_speaker_ok"))

print("\n[4] 버퍼를 비우는 곳마다 계수도 함께 되돌린다")
# 하나라도 빠뜨리면 계수만 남아 다음 버퍼가 조기에 잘린다 (조용한 고장).
missed = [
    ln.strip()[:60]
    for ln in src.splitlines()
    if ('_last_command, followup_until' in ln or '_last_command, _last_command_at' in ln)
    and '_merge_count' not in ln
    and '"", 0.0' in ln
]
# 초기화 한 줄(바로 다음 줄에서 _merge_count = 0)만 예외로 허용한다
check(f"계수를 안 되돌리는 곳 없음 (발견: {missed})", len(missed) <= 1, True)

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과")
