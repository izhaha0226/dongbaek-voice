#!/usr/bin/env python3
"""전화 모드 무음 — "통화 중엔 스피커로 얘기하지 말고, 무음으로 알아서 정리해"
(사장님 지시 2026-08-13). 미팅 모드와 같은 봉인 원칙인지 검사한다.
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import tempfile
import time
from pathlib import Path

import call_notes
import config
import dongbaek
import speak

FAIL = []


def check(name, got, want=True):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


_played = []
speak._play = lambda body: _played.append(body)

print("[1] 통화 중엔 아무 소리도 안 나간다")
dongbaek._phone_enter("시험")
check("전화 모드 활성", dongbaek._HOLD["until"] > 0, True)
check("입 봉인", speak.is_muted(), True)
check("무음 시계 시작", dongbaek._HOLD.get("last_heard", 0) > 0, True)
# 30초 무음 = 종료 (사장님 지시 2026-08-13). 틱(10초)이 약속보다 촘촘해야
# "30초" 가 실제로 30~40초가 된다.
check("무음 종료 30초", config.CALL_QUIET_EXIT_SEC, 30.0)
check("회의도 30초", config.MEETING_QUIET_EXIT_SEC, 30.0)
check("판정 틱이 충분히 촘촘함", config.NUDGE_CHECK_SEC <= 15, True)
speak.say("통화 중 알림은 나가면 안 된다", block=True)
speak.say("타이머 알림도 안 된다", block=True, priority=speak.PRIORITY_NOTICE)
check("전부 삼킴", _played, [])

print("[2] 해제되면 입이 열리고, 모인 통화는 조용히 정리된다")
saved = {}
call_notes.clear()
for i in range(4):
    # 진짜 통화라면 사장님 목소리가 한 번은 섞인다 (귀에 대고 하시니까).
    call_notes.note(f"통화 조각 {i}", owner=(i == 1))
_orig_save = call_notes.save
call_notes.save = lambda: (saved.setdefault("done", True) and "", None) or ("통화 4조각 정리", None)
_orig_record = dongbaek.record
dongbaek.record = lambda **k: saved.setdefault("recorded", k)
dongbaek._phone_exit("시험 해제")
time.sleep(0.3)                      # 정리는 스레드로 돈다
check("입 열림", speak.is_muted(), False)
check("자동 정리 실행", saved.get("done"), True)
check("기록·텔레그램 미러 경유", saved.get("recorded") is not None, True)
check("정리를 소리로 안 읽음", _played, [])

print("[3] 사장님 목소리가 한 번도 안 섞인 판은 위키에 안 남는다")
# 2026-08-14 18:35 — '그것이 알고싶다' 류 재연 방송이 "통화 자동 정리" 로
# 위키에 들어갔다 (15:12 재혼 이야기도 같은 건). 자동 정리 9건 중 3건이
# 방송·잡담이었고, 한 건마다 클로드 문서 정리가 한 번씩 돌았다.
saved.clear()
call_notes.clear()
for i in range(5):
    call_notes.note(f"방송 조각 {i}")            # owner 없음 = 사람이 아니다
check("사장님 목소리 없음", call_notes.owner_heard(), False)
_state_real = config.STATE
config.STATE = Path(tempfile.mkdtemp())
try:
    dongbaek._phone_exit("30초 조용 — 종료로 인지")
    time.sleep(0.3)
    check("클로드 정리 안 돎", saved.get("done"), None)
    dropped = config.STATE / "call_dropped.jsonl"
    # ⚠ 버리되 없애지는 않는다. 방송으로 오판해 진짜 통화를 버렸는지
    #   확인할 길이 있어야 문턱을 숫자로 조정한다.
    check("버린 원문은 state 에 남는다", dropped.exists(), True)
    check("원문이 통째로 들어 있다",
          "방송 조각 4" in dropped.read_text(encoding="utf-8"), True)
    check("버퍼는 비워진다", call_notes.pending(), 0)
finally:
    config.STATE = _state_real

print("[3-2] 진입할 때 이미 확인된 통화는 한 마디도 안 잡혀도 남는다")
# "여보세요"·"전화 모드로 해" 는 그 말 자체가 화자 확인을 통과한 것이다.
saved.clear()
dongbaek._phone_enter("자동: '여보세요'", owner=True)
for i in range(4):
    call_notes.note(f"통화 조각 {i}")            # 이후엔 확인 못 했다 치자
check("진입 시점 표시가 산다", call_notes.owner_heard(), True)
dongbaek._phone_exit("30초 조용 — 종료로 인지")
time.sleep(0.3)
check("정리해 남긴다", saved.get("done"), True)

print("[3-3] 앞 통화의 표시를 다음 판이 물려받지 않는다")
# 조각이 MIN_PARTS 미만이면 정리 없이 버퍼가 남는다. 그 잔재로 방송이
# 통화 표시를 훔쳐 오면 안 된다.
call_notes.mark_owner(True)
dongbaek._phone_enter("통화 감지 — 158자 발화 연속")
check("진입 때 다시 놓는다", call_notes.owner_heard(), False)
dongbaek._phone_exit("정리", summarize=False)
call_notes.clear()

print("[4] '전화 끝났어' 명시 경로는 자동 정리를 안 겹친다 (직접 말한다)")
saved.clear()
call_notes.clear()
for i in range(4):
    call_notes.note(f"조각 {i}")
dongbaek._phone_enter("시험2")
dongbaek._phone_exit("전화 끝났어", summarize=False)
time.sleep(0.2)
check("자동 정리 안 돎", saved.get("done"), None)
check("입은 열림", speak.is_muted(), False)

call_notes.save = _orig_save
dongbaek.record = _orig_record
call_notes.clear()

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 통화 중엔 침묵, 끝나면 조용히 옵시디언에 남는다")
