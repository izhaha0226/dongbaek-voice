#!/usr/bin/env python3
"""2026-08-12 새벽에 고친 것이 전부 실제로 반영됐는가.

사장님 지시: "수정한 게 전부 다 반영됐는지 QA 돌려봐. 분명히 누락된 거
있을 거야."

말로 "고쳤습니다" 라고 보고한 것과 코드에 실제로 들어간 것은 다르다.
그날 밤에만 재기동을 열 번 넘게 했고 수정이 여덟 갈래였다. 하나라도
빠지면 사장님은 또 무시당한다. 그래서 보고한 항목을 전부 여기서 다시 잰다.

    python test_tonight_qa.py
"""
import inspect

import audio
import config
import dongbaek
import router
import score
import speak

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


print("\n① 무음 정지 — 재생이 안 끝나도 워커가 풀려나는가")
check("재생 상한이 있다", hasattr(speak, "_play_budget"), True)
check("상한을 지키는 대기 함수가 있다", hasattr(speak, "_await_playback"), True)
check("내장 음성 폴백에도 상한이 있다",
      "deadline" in inspect.getsource(speak._say_builtin), True)
check("Playback 이 '소리가 났는지' 를 알려준다",
      "def started" in open("tts.py").read(), True)

print("\n② 텔레그램 승인 게이트 — 되물음에 승인을 묻지 않는가")
src = open("telegram_bridge.py").read()
check("포괄 사유 면제가 들어 있다", "SAFE_ONLY_REASON" in src, True)

print("\n③ 메모리 오판 — 48기가를 부족이라 하지 않는가")
import memory_guard
s = memory_guard.snapshot()
check("memory_pressure 를 쓴다", hasattr(memory_guard, "_free_pct"), True)
check("여유가 총량의 15% 이상으로 잡힌다 (압박 보통일 때)",
      s["pressure"] >= 2 or s["free_gb"] >= s["total_gb"] * 0.15, True)

print("\n④ 로그 — 시각이 붙고 무한증식하지 않는가")
import dblog
check("시각 함수가 있다", hasattr(dblog, "log"), True)
check("회전 함수가 있다", hasattr(dblog, "rotate"), True)
check("데몬이 회전을 부른다", "dblog.rotate" in open("dongbaek.py").read(), True)

print("\n⑤ 거르개 — 사고 뒤 꺼져 있는가")
check("거르개가 꺼져 있다", config.PREGATE_ENABLED, False)
check("문턱이 사고값(0.37)보다 낮다", config.PREGATE_STRANGER_MAX < 0.37, True)
check("거르개가 점수를 기록한다",
      "_log_voice_score" in inspect.getsource(dongbaek._skip_transcribe), True)

print("\n⑥ 대화창 — 부르고 대답 듣고 말해도 살아 있는가")
d = open("dongbaek.py").read()
check("바로 부르기('네')가 답변 창을 연다",
      d.count('_REPLIED_AT["at"] = time.monotonic()') >= 3, True)
check("되물음도 답변 창을 연다",
      "되물음도 동백이 한 말이다" in d, True)
check("대화창을 '말이 끝난' 시점부터 잰다",
      "speak.last_finished_at()" in d, True)
check("speak 가 끝난 시각을 알려준다", hasattr(speak, "last_finished_at"), True)

print("\n⑦ 등급 분류 — 잡담이 opus 로 안 가는가")
check("코드 명령일 때만 dev 로 올린다",
      "dev = _is_code_edit(command)" in d, True)
check("'커밋 시켜' 는 여전히 dev", dongbaek._is_code_edit("커밋 시켜"), True)
check("'지금 몇 시야' 는 dev 아님", dongbaek._is_code_edit("지금 몇 시야"), False)

print("\n⑧ 호출어 — 새벽 오인식을 잡는가")
for w in ("공배가", "공백아", "공백이"):
    check(f"{w!r} 를 호명으로 인식", router.match_wake(w) is not None, True)
for w in ("내가", "배가"):
    check(f"{w!r} 는 호출어가 아니다 (일상어)", router.match_wake(w) is None, True)

print("\n⑨ 끊고 들어오기 — 말하는 중에 끼어들면 멈추는가")
asrc = inspect.getsource(audio)
# 2026-08-12 밤: 감쇠를 −1 → −0.5(BARGE_IN_DECAY) 로 눅였다. 취지 동일 —
# 리셋(=0) 은 금지, 감쇠는 증가(+1)보다 작아야 진동해도 계수가 자란다.
check("짧은 숨에서 계수기가 0 으로 리셋되지 않는다",
      'barge - getattr(config, "BARGE_IN_DECAY"' in asrc
      and 0 < config.BARGE_IN_DECAY < 1, True)
check("끊기까지 필요한 블록이 3 이하", config.BARGE_IN_BLOCKS <= 3, True)

print("\n⑩ 목소리 등록 — '지금 목소리 기억해' 가 되는가")
for said in ["지금 목소리 나라고 기억하라고", "목소리 말하고 기억하라고",
             "지금 목소리 기억해"]:
    n = router.normalize(said)
    check(f"{said!r} 를 잡는다",
          router.is_voice_correction(n) or router.is_voice_enroll_request(n), True)
check("거절된 발화가 없어도 지금 목소리를 배운다",
      "elif voiceprint.forgive(who, audio)" in d, True)

print("\n⑪ 캘린더 — 등록이 로컬로 처리되는가")
check("등록은 승인 없이 로컬 처리",
      router.handle_local("내일 오후 4시 큐에이확인 등록해줘") is not None, True)
check("삭제는 승인 전이면 로컬 처리 안 함",
      router.handle_local("내일 큐에이확인 삭제해줘"), None)
# 시험으로 만든 일정은 지운다 (승인된 경로로)
router.handle_local("내일 큐에이확인 삭제해줘", elevated=True)

print("\n⑫ 채점 — 거짓말을 가장 크게 깎는가")
check("거짓말 항목이 있다", "lie" in score.POINTS, True)
check("거짓말이 -5 점", score.POINTS["lie"][0], -5)
check("거짓말이 가장 큰 감점",
      min(p for p, _ in score.POINTS.values()), -5)
check("실제 있었던 거짓말을 잡는다",
      bool(score.find_lies(["지금 이 세션에서는 캘린더에 쓰는 도구가 꺼져 있어서 등록이 안 됩니다"])),
      True)
check("진짜 못 하는 건 안 잡는다",
      bool(score.find_lies(["메일 보낼 권한이 없습니다."])), False)
check("야간 자가정비가 점수를 증거로 읽는다",
      "score" in open("self_improve.py").read(), True)

print("\n⑬ 동백이 거짓말 금지를 기억하는가")
cm = open("CLAUDE.md").read()
check("CLAUDE.md 에 거짓말 금지가 있다", "거짓말" in cm, True)
check("가장 나쁘다고 못 박혀 있다", "세상에서 제일 나쁜" in cm, True)
check("실제 사례가 적혀 있다", "세션" in cm and "캘린더" in cm, True)
check("핑퐁 규칙이 있다", "핑퐁" in cm, True)

print()
if FAIL:
    print(f"❌ 누락 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 오늘 새벽에 고친 것이 전부 반영돼 있습니다")
