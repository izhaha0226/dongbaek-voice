#!/usr/bin/env python3
"""미팅 모드 — 입 봉인·기록·클로드 회의록 (사장님 지시 2026-08-13).

실사고가 설계 근거다: 전화 모드는 호명에 열리는데, 줌미팅 소리에 호명
비슷한 말이 섞여 귀가 열렸고 동백이 미팅 한복판에 "메모리 48기가…" 를
낭독했다. 소리·클로드 없이 판정과 봉인 논리만 검사한다.
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import config
import router
import speak

FAIL = []


def check(name, got, want=True):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


print("[1] 시작·종료 문구")
for said, start in [("동백아 미팅 모드", True), ("회의 모드 시작해", True),
                    ("미팅 모드 시작", True), ("미팅 잡아줘", False),
                    ("오늘 미팅 뭐야", False)]:
    check(f"{said!r} → 시작 {start}",
          router.is_meeting_start(router.normalize(said)), start)
for said, end in [("미팅 끝났어", True), ("회의 끝났습니다", True),
                  ("미팅 모드 해제", True), ("미팅 종료", True),
                  ("회의 끝나고 보자", False),      # 미팅 소리에 섞이는 말 ← 핵심
                  ("미팅 끝나면 알려줘", False)]:
    check(f"{said!r} → 종료 {end}",
          router.is_meeting_end(router.normalize(said)), end)

print("[2] 입 봉인 — 무엇이든 소리로 안 나간다")
_played = []
speak._play = lambda body: _played.append(body)
speak.mute(True)
speak.say("이 말은 나가면 안 된다", block=True)
speak.say("알림도 안 된다", block=True, priority=speak.PRIORITY_NOTICE)
speak.beep()
check("발화 삼킴", _played, [])
speak.mute(False)
speak.say("이제는 나간다", block=True)
check("해제 후 정상", len(_played), 1)

print("[3] 회의록은 무조건 클로드")
import call_notes

calls = {}


def _fake_ask_once(prompt, *, model, timeout=0):
    calls["model"] = model
    calls["prompt"] = prompt
    return "## 핵심 논의\n- 시험\n## 결정된 것\n(없음)\n## 해야 할 일\n(없음)\n## 다음 일정·약속\n(없음)"


import bridge
bridge.ask_once = _fake_ask_once
out = call_notes._summarize_claude(["여행 일정 논의", "말레이시아 기관 방문"])
check("클로드 모델 사용", calls.get("model"), config.MODEL_CHAT)
check("회의록 절 구성 요구", "해야 할 일" in calls.get("prompt", ""), True)
check("결과 회수", "핵심 논의" in out, True)

print("[4] 상태 전이 — 미팅이 전화보다 굳다")
import dongbaek

speak.say = lambda *a, **k: None            # 전이 멘트 무음
dongbaek._meeting_enter("시험")
check("활성", dongbaek._meeting_active(), True)
check("입 봉인됨", speak.is_muted(), True)
check("전화 모드는 밀려남", dongbaek._HOLD["until"], 0.0)
dongbaek._MEETING.update(until=0.0, since=0.0)
speak.mute(False)
check("정리 후 해제", dongbaek._meeting_active(), False)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 미팅 중엔 조용히 듣고, 정리는 클로드가 한다")
