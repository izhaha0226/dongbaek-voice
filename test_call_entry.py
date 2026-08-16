#!/usr/bin/env python3
"""전화·미팅 모드로 들어가는 근거 — 2026-08-16 개선의 회귀 시험.

전화 모드는 들어가면 귀를 닫는다. 실측 81개 구간의 중앙값이 105초, 최장
11.2분이었다. 그동안 사장님이 부르시면 두 번 불러야 열린다. 그러니 **들어가는
근거가 정확해야** 한다 — 나가는 길은 이미 잘 돌고 있었다(30초 조용 62회,
호명 해제 18회, 구간 안에서 놓친 호명 0건).

여기서 지키는 것 둘.
  1. 통화의 증거는 '긴 소리' 가 아니라 '사장님이 길게 말씀하시는 것' 이다.
     길이만 세면 TV 가 전화 모드를 켠다 — 실측 자동 진입 70회 중 3회가
     바로 앞이 미등록 목소리였고, 46회는 화자를 보지도 않았다.
     남 목소리는 발화 하나하나가 어차피 무시된다. 무시되는 소리 때문에
     귀까지 닫을 이유가 없다.
  2. 캘린더에 회의가 잡힌 시간이면 긴 말 한 번으로 미팅 모드다.
     두 번을 기다리면 회의 앞부분이 새어 나간다.

이 시험은 마이크·오디오를 끌고 오지 않는다. 그 경로를 다 흉내 내면 시험이
실제보다 무겁고 덜 정확해진다. 대신 판단이 서는 자리(설정과 코드의 순서)를
본다 — 이 개선은 그 세 줄이 있느냐로 갈린다.

    python tests/test_call_entry.py
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


src = (ROOT / "dongbaek.py").read_text(encoding="utf-8")

print("[1] 통화 감지는 사장님 목소리일 때만 센다")
check("설정이 켜져 있다", getattr(config, "CALL_DETECT_OWNER_ONLY", False), True)
i = src.index("CALL_DETECT_OWNER_ONLY")
blk = src[i:i + 700]
check("화자를 확인한다", "_speaker_ok(audio)" in blk)
check("아니면 집계에서 뺀다", "통화로 세지 않음" in blk)

# 화자 확인이 _LONG_RUN 집계보다 앞이어야 한다. 뒤에 두면 이미 세어진 뒤라
# 두 번째 TV 소리에서 그대로 전화 모드가 켜진다.
check("화자 확인이 집계보다 앞",
      blk.index("_speaker_ok(audio)") < blk.index("_LONG_RUN.append"))

print("\n[2] 캘린더에 회의가 있으면 긴 말 한 번으로 미팅 모드")
j = src.index("캘린더 일정 중 긴 말")
mblk = src[max(0, j - 900):j + 300]
check("캘린더를 본다", "_calendar_meeting_now()" in mblk)
check("여기서도 화자를 본다", "_speaker_ok(audio)" in mblk)
check("호출어면 이 길로 가지 않는다", "router.match_wake(text) is None" in mblk)
check("이미 모드 중이면 다시 안 들어간다",
      "_meeting_active()" in mblk and '_HOLD["until"]' in mblk)
check("기록부터 남기고 들어간다", mblk.index("call_notes.note") < mblk.index("_meeting_enter"))

print("\n[3] 미팅 모드가 전화 모드보다 먼저 잡힌다")
# 캘린더 회의 중이라면 더 굳게 닫는 쪽(미팅)이 우선이어야 한다.
# ⚠ 주석이 아니라 '실제로 들어가는 줄' 로 견준다. "통화 감지" 라는 말은
#   위쪽 설명 주석에도 있어서, 그걸 잡으면 순서를 거꾸로 읽는다.
check("미팅 판단이 전화 모드 진입보다 앞",
      src.index("캘린더 일정 중 긴 말") < src.index("_phone_enter(f\"통화 감지"))

print("\n[4] 나가는 길은 그대로 열려 있다")
check("조용하면 나온다", getattr(config, "CALL_QUIET_EXIT_SEC", 0) > 0, True)
check("미팅도 조용하면 나온다", getattr(config, "MEETING_QUIET_EXIT_SEC", 0) > 0, True)
check("안전 상한이 있다", getattr(config, "MEETING_MAX_SEC", 0) > 0, True)

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — 귀는 통화일 때만 닫고, 회의는 첫 마디에 잡는다")
