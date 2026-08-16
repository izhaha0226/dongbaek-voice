#!/usr/bin/env python3
"""GPT 모드 검증 — 듣기만 하고, 끝나면 옵시디언에 남긴다.

사장님 지시 (2026-08-14):
  "gpt 모드는 지피티와 대화를 하는 동안 동백이는 답변을 하지 않고 계속
   청취하고 내용을 옵시디언에 정리하도록 해줘. 즉 '잠깐 쉬어' 모드와 같은
   기능이되 청취된 모든 걸 옵시디언에 저장하도록 하는거지"

하는 일이 미팅 모드와 글자 그대로 같아서 모드를 새로 만들지 않고 kind 만
붙였다. 그래서 이 파일이 지키는 것은 두 가지다.
  ① GPT 모드가 실제로 입을 봉인하는가 (안 그러면 지피티와 말이 겹친다)
  ② 미팅과 지피티가 서로의 종료 문구로 닫히지 않는가
    python test_gpt_mode.py
"""
import sys

import call_notes
import config
import dongbaek
import router
import speak

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


print("[1] 부르는 말 — whisper 가 흔드는 표기까지 받는다")
# 'GPT' 는 받아쓰기가 여러 갈래로 적는다. 'QN' 은 사장님 실사례다.
for s in ("gpt 모드", "지피티 모드", "쥐피티 모드", "QN 모드", "GPT모드 시작"):
    check(f"시작: {s!r}", router.is_gpt_start(router.normalize(s)), True)
for s in ("지피티 끝났어", "gpt 끝났어", "지피티 모드 해제", "GPT 종료"):
    check(f"종료: {s!r}", router.is_gpt_end(router.normalize(s)), True)

print("\n[2] 헛걸림 — 평범한 말에 모드가 열리면 안 된다")
for s in ("오늘 일정 알려줘", "지피티가 뭐야", "미팅 모드", "회의 끝났어"):
    check(f"시작 아님: {s!r}", router.is_gpt_start(router.normalize(s)), False)
    check(f"종료 아님: {s!r}", router.is_gpt_end(router.normalize(s)), False)

print("\n[3] 미팅과 지피티는 서로의 종료 문구로 닫히지 않는다")
# ⚠ 섞이면 사고다. 지피티와 얘기하는 중에 "미팅 끝났어" 로 닫히면
#   동백이 갑자기 입을 열어 지피티 대화에 끼어든다.
check("'미팅 끝났어' 는 지피티 종료가 아니다",
      router.is_gpt_end(router.normalize("미팅 끝났어")), False)
check("'지피티 끝났어' 는 미팅 종료가 아니다",
      router.is_meeting_end(router.normalize("지피티 끝났어")), False)

print("\n[4] 들어가면 입이 봉인된다 — 이게 이 모드의 전부다")
was = speak.is_muted()
dongbaek._meeting_enter("테스트", kind="지피티")
check("입 봉인됨", speak.is_muted(), True)
check("모드 이름이 지피티", dongbaek._meeting_kind(), "지피티")
check("모드 살아 있음", dongbaek._meeting_active(), True)
# 봉인 중에는 무엇을 말하려 해도 소리가 안 나간다
_played = []
_real = speak._play
speak._play = lambda body: _played.append(body)  # type: ignore[assignment]
speak.say("이건 나가면 안 된다", block=False)
speak._play = _real  # type: ignore[assignment]
check("봉인 중엔 재생 큐로 안 간다", _played, [])
# 정리 없이 조용히 나온다 (call_notes 버퍼가 비어 있어 저장도 안 한다)
call_notes.clear()
dongbaek._MEETING.update(until=0.0, since=0.0)
speak.mute(was)
check("봉인 해제됨", speak.is_muted(), was)

print("\n[5] 정리 문서는 회의록이 아니라 대화 꼴이다")
# 회의록 절('결정된 것'·'맡을 사람')을 그대로 쓰면 클로드가 없는 것을
# 채워 넣는다. 지피티 대화는 물음과 답으로 가른다.
check("지피티 지시문이 따로 있다", "무엇을 물었나" in call_notes._GPT_SHAPE, True)
check("회의 지시문과 다르다", call_notes._GPT_SHAPE != call_notes._MEETING_SHAPE, True)
check("설정 켜짐", getattr(config, "GPT_MODE_ENABLED", None), True)

print("\n[6] 임의로 끝나지 않는다 — 사장님이 끄실 때까지")
# 사장님 지시 (2026-08-14): "지피티모드 들어가면 내가 지피티모드 해제할때까지
# 절대로 임의로 끝내지마." 지피티 답을 읽는 동안 몇 분씩 조용해지는데,
# 그때 동백이 혼자 끝내고 입을 열면 대화 한복판에 끼어드는 꼴이다.
import time

import dblog

_logged = []
_real_log = dblog.log
dongbaek._MEETING.update(until=time.monotonic() - 1,          # 상한을 이미 넘긴 상태
                         since=time.monotonic(), last_heard=0.0,  # 아주 오래 조용
                         kind="지피티")
check("상한을 넘겨도 살아 있다", dongbaek._meeting_active(), True)
# 조용함 자동 종료가 지피티를 건너뛰는지 — 판정 조건을 그대로 확인한다
quiet = time.monotonic() - dongbaek._MEETING["last_heard"]
would_exit_meeting = quiet >= config.MEETING_QUIET_EXIT_SEC
check("미팅이었다면 끝났을 만큼 조용하다", would_exit_meeting, True)
check("그래도 지피티는 살아 있다",
      dongbaek._meeting_active() and dongbaek._meeting_kind() == "지피티", True)
dongbaek._MEETING.update(until=0.0, since=0.0, kind="미팅")
speak.mute(False)

print("\n[7] 내용이 없으면 빈 문서를 만들지 않는다")
call_notes.clear()
said, path = call_notes.save_meeting(kind="지피티")
check("저장 안 함", path, None)
check("그렇다고 말한다", "지피티" in said, True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과 — 듣기만 하고, 끝나면 남긴다")
