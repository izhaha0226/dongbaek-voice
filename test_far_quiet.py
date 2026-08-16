#!/usr/bin/env python3
"""원거리 안내는 '사장님이 맞다' 가 확인된 뒤에만 나간다.

실측 08-13~16 daemon.log: "멀리 계신 것 같아…" 안내 107번 중 78번이
바로 뒤 '미등록 목소리 — 무시' 로 버려진 소리였다 (TV·환청). 그중
다섯 번은 자정~새벽 2시 — 아무도 안 부른 빈 방에 대고 말한 것이다.
증폭은 받아쓰기 전에 걸리므로, 그 시점엔 누가 낸 소리인지 알 수 없다.
"""
import sys as _sys
import time
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import config
import dongbaek

FAIL = []
SPOKEN = []


def check(name, got, want=True):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


# 소리는 내지 않고 무엇을 말하려 했는지만 잡는다
import speak
speak.say = lambda text, **kw: SPOKEN.append(text)


def reset():
    SPOKEN.clear()
    dongbaek._FAR.update(gain=0.0, rms=0.0, at=0.0, told=0.0)


print("[1] 증폭만으로는 말하지 않는다 (TV·환청이 여기까지 온다)")
reset()
dongbaek._far_heard(0.005, 7.2)          # 08-16 02:41 'prends,' 와 같은 판
check("입을 다문다", SPOKEN, [])
check("상태는 남는다 (잘 들려? 의 답)", dongbaek._FAR["gain"], 7.2)

print("[2] 사장님으로 확인되면 그때 알린다")
dongbaek._far_notice_due()
check("멀다고 말한다", any("멀리" in s for s in SPOKEN), True)

print("[3] 확인 경로도 문턱·쿨다운을 그대로 지킨다")
reset()
dongbaek._far_heard(0.020, 1.4)          # FAR_NOTICE_MIN_GAIN(1.8) 아래
dongbaek._far_notice_due()
check("살짝 작은 소리는 조용히", SPOKEN, [])
reset()
dongbaek._far_heard(0.005, 4.0)
dongbaek._far_notice_due()
dongbaek._far_heard(0.005, 4.0)
dongbaek._far_notice_due()
check("한 번만 말한다", len(SPOKEN), 1)

print("[4] 묵은 증폭은 되살리지 않는다")
reset()
dongbaek._far_heard(0.005, 4.0)
dongbaek._FAR["at"] = time.monotonic() - 60.0   # 1분 전 소리
dongbaek._far_notice_due()
check("한참 전 증폭엔 침묵", SPOKEN, [])
reset()
dongbaek._far_notice_due()                      # 증폭이 아예 없었던 경우
check("증폭 없으면 침묵", SPOKEN, [])

print("[5] ⚠ 배선 — audio 층에 꽂히는 건 기록(_far_heard)이지 안내가 아니다")
# 데몬 본체를 돌릴 수 없으니 배선 한 줄을 본다. 여기가 _on_far_gain 으로
# 되돌아가면 [1] 이 통과해도 실제로는 잡음에 대고 다시 떠들게 된다.
src = (_Path(__file__).resolve().parent / "dongbaek.py").read_text(encoding="utf-8")
check("기록 함수를 꽂는다", "set_gain_reporter(_far_heard)" in src, True)
check("안내 함수는 안 꽂는다", "set_gain_reporter(_on_far_gain)" in src, False)
check("확인 지점에서 부른다", src.count("_far_notice_due()") >= 3, True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 잡음엔 침묵, 사장님으로 확인되면 그때 알린다")
