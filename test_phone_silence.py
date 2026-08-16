#!/usr/bin/env python3
"""전화 모드 무음 — "통화 중엔 스피커로 얘기하지 말고, 무음으로 알아서 정리해"
(사장님 지시 2026-08-13). 미팅 모드와 같은 봉인 원칙인지 검사한다.
"""
import time

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
    call_notes.note(f"통화 조각 {i}")
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

print("[3] '전화 끝났어' 명시 경로는 자동 정리를 안 겹친다 (직접 말한다)")
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
