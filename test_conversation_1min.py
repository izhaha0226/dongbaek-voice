#!/usr/bin/env python3
"""1분 넘게 대화가 이어지는가 — 사장님 지시로 만든 QA.

  "동백아 부르고 난 다음에 답변을 하는지 체크하고, 그다음에 나랑 1분 이상
   대화를 할 수 있도록 QA 확인해 봐."

단위 테스트로는 이걸 증명 못 한다. 한 판 한 판은 다 통과하는데 이어붙이면
끊기는 게 어제 밤에 실제로 일어난 일이다. 그래서 여기서는 '시간' 을 넣고
여러 판을 이어서 돌린다.

재는 것은 두 가지다.
  ① 부르면 답하는가 — 호명 → "네" → 이어 말하기가 열리는가
  ② 그 상태로 1분을 버티는가 — 매 판마다 창이 다시 열려야 한다

창 계산은 dongbaek 메인 루프와 같은 식을 쓴다:
    free_pass = 말을 시작한 시각 < max(호출어 창, 답변 창)
답변 창은 '동백이 말을 마친 시각 + REPLY_FOLLOWUP_SEC' 이다.

    python test_conversation_1min.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import config
import router

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


# 실측값 (2026-08-12 05:00, 제어 서버로 잰 왕복 시간)
LOCAL_ANSWER_SEC = 0.2      # 시각·일정·점수 같은 로컬 처리
CLAUDE_ANSWER_SEC = 10.0    # Claude 경로 (normal 등급, sonnet)
SPEAK_SEC = 6.0             # 두세 문장을 소리내어 읽는 시간
USER_TALK_SEC = 3.0         # 사장님이 한 마디 하시는 시간
USER_THINK_SEC = 4.0        # 답을 듣고 다음 말을 고르는 시간


print("\n① 부르면 답하는가")


def heard(text):
    """데몬과 같은 순서로 판정한다 — 호출어를 떼고, 남은 꼬리를 본다.

    ⚠ is_bare_call 은 '꼬리' 를 받는다. 전체 문장을 넣으면 늘 False 가
      나온다. 처음에 그렇게 시험했다가 멀쩡한 코드를 버그로 볼 뻔했다.
    """
    tail = router.match_wake(text)
    return {"woke": tail is not None,
            "bare": router.is_bare_call(tail if tail is not None else text),
            "tail": tail}


for said in ("동백아", "동백", "공배가", "동백이"):
    r = heard(said)
    check(f"{said!r} → 호명으로 알아듣는다", r["woke"] and r["bare"], True)

check("호명에 즉시 답하도록 켜져 있다", config.CALL_ANSWER_ENABLED, True)
check("답할 말이 있다", bool(config.CALL_ANSWER), True)
check("호명 뒤에 명령이 붙으면 '네' 하지 않고 바로 처리한다",
      heard("동백아 지금 몇 시야")["bare"], False)
check("군말이 붙어도 호명이다 ('동백아 어')", heard("동백아 어")["bare"], True)
check("짧아도 질문이면 호명이 아니다 ('동백아 뭐라고')",
      heard("동백아 뭐라고")["bare"], False)


print("\n② 1분 대화 — 판을 이어붙여 창이 계속 열리는가")

REPLY_WIN = config.REPLY_FOLLOWUP_SEC
CALL_WIN = config.FOLLOWUP_WINDOW_SEC


def converse(turns, answer_sec, *, label):
    """대화를 시간축 위에서 돌린다. 끊긴 판의 번호를 돌려준다."""
    now = 0.0
    dropped = []

    # 호명 — "동백아"
    now += USER_TALK_SEC
    spoke_end = now + 1.0                 # "네" 는 짧다
    call_win_until = now + CALL_WIN       # 호출어 창
    reply_win_until = spoke_end + REPLY_WIN   # 답변 창 (말을 마친 뒤부터)

    for i in range(1, turns + 1):
        # 사장님이 생각하고 말을 시작한다
        start = now + USER_THINK_SEC
        if start >= max(call_win_until, reply_win_until):
            dropped.append(i)
        now = start + USER_TALK_SEC        # 말이 끝난 시각

        # 동백이 생각하고 답한다
        now += answer_sec
        spoke_end = now + SPEAK_SEC
        now = spoke_end
        call_win_until = 0.0               # 호출어 창은 이미 지났다
        reply_win_until = spoke_end + REPLY_WIN

    return dropped, now


for label, ans in (("로컬 처리", LOCAL_ANSWER_SEC), ("Claude 경로", CLAUDE_ANSWER_SEC)):
    dropped, elapsed = converse(5, ans, label=label)
    print(f"\n  [{label}] 5판 / {elapsed:.0f}초")
    check(f"{label} — 1분 이상 이어진다", elapsed >= 60, True)
    check(f"{label} — 끊긴 판이 없다", dropped, [])


print("\n③ 여유가 얼마나 있는가 (빠듯하면 실사용에서 깨진다)")
# 답을 다 듣고 몇 초를 더 뜸들여도 되는가
slack = REPLY_WIN - USER_THINK_SEC
print(f"  · 답변 창 {REPLY_WIN:.0f}초 - 생각 {USER_THINK_SEC:.0f}초 = 여유 {slack:.0f}초")
check("답을 듣고 뜸들일 여유가 5초 이상", slack >= 5, True)

# 묵은 명령 버리기에 걸리지 않는가
cycle = USER_THINK_SEC + USER_TALK_SEC + CLAUDE_ANSWER_SEC + SPEAK_SEC
print(f"  · 한 판에 {cycle:.0f}초 / 명령 폐기 문턱 {config.JOB_MAX_AGE_SEC:.0f}초")
check("한 판이 명령 폐기 문턱 안에 든다", cycle < config.JOB_MAX_AGE_SEC, True)


print("\n④ 말하는 도중에 끼어들 수 있는가 (없으면 대화가 아니라 방송이다)")
check("끊고 들어오기가 켜져 있다", config.BARGE_IN_ENABLED, True)
# 2026-08-12 밤: 한 칸(−1) 감쇠도 컸다 — +1 −1 진동이면 문턱 3배 소리도
# 영영 3 에 못 닿았다. BARGE_IN_DECAY(0.5) 로 더 눅였다. 리셋(=0) 금지라는
# 원래 취지는 그대로다 — 감쇠량이 증가량(1)보다 작은지로 본다.
check("짧은 숨에 계수기가 죽지 않는다 (감쇠 < 증가)",
      'barge - getattr(config, "BARGE_IN_DECAY"' in open("audio.py").read()
      and 0 < config.BARGE_IN_DECAY < 1, True)
check("큰 소리는 길이를 기다리지 않는다 (즉시 끊는 지름길)",
      "BARGE_IN_LOUD_BLOCKS" in open("audio.py").read()
      and config.BARGE_IN_LOUD_BLOCKS <= 2, True)
blocks_ms = config.BARGE_IN_BLOCKS * config.BLOCK / config.SAMPLE_RATE * 1000
print(f"  · 끊기까지 {config.BARGE_IN_BLOCKS}블록 = 약 {blocks_ms:.0f}밀리초")
check("0.2초 안에 끊긴다", blocks_ms <= 200, True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 부르면 답하고, 1분 넘게 대화가 이어집니다")
