#!/usr/bin/env python3
"""채점기 — 씹힌 것을 씹혔다고 세는가, 주변 소리를 억울하게 깎지 않는가.

이 채점기가 존재하는 이유는 2026-08-12 새벽이다. 동백이 30분 넘게
사장님 말을 씹는 동안 자기가 씹고 있다는 걸 몰랐다. 그래서 두 가지를
같은 무게로 검사한다:

  ① 실패를 실패로 셀 것        — 못 세면 만든 의미가 없다
  ② 주변 소리를 감점하지 말 것  — 세면 점수가 '방이 조용한가' 를 잰다

②가 무너지면 점수가 개선의 근거로 못 쓰인다. TV 켜둔 날 점수가 바닥나면
사람은 그 숫자를 무시하게 되고, 무시당하는 지표는 없는 것과 같다.

    python test_score.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import io
from contextlib import redirect_stdout
from datetime import datetime, timedelta

import score

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


def fake(*bodies_with_gap):
    """(초오프셋, 본문) 목록을 score._events 가 주는 형태로."""
    base = datetime.now().replace(microsecond=0) - timedelta(minutes=5)
    return [(base + timedelta(seconds=off), body) for off, body in bodies_with_gap]


def run(evs):
    real = score._events
    score._events = lambda days: evs
    try:
        return score.grade(1)
    finally:
        score._events = real


def kinds(evs):
    return [r["kind"] for r in run(evs)]


print("\n성공을 성공으로 세는가")
check("부르고 명령까지 = 한 번에 통함",
      kinds(fake((0, "호명: '동백아' → 즉시 응답"), (5, "명령: '몇 시야'"))),
      ["one_shot"])
check("호명 없이 바로 명령 = 처리됨",
      kinds(fake((0, "명령: '몇 시야'"))), ["handled"])

print("\n실패를 실패로 세는가 (그날 실제로 있었던 것들)")
check("듣고도 아무 반응 없음",
      kinds(fake((0, "호명: '동백아' → 즉시 응답"),
                 (5, "들림: '지금 몇 시야?'"),
                 (9, "들림: '지금 몇 시야?'")))[:1],
      ["dropped"])
check("대화창이 닫혀 무시",
      kinds(fake((0, "호출어 없음 — 무시 (대화창 5초 지남): '...'"))), ["late"])
check("다시 부르심 = 앞이 먹통",
      kinds(fake((0, "호명: '동백아' → 즉시 응답"),
                 (8, "호명: '동백아' → 즉시 응답"))),
      ["recall"])
check("너무 오래 걸려 버림",
      kinds(fake((0, "오류: 응답이 너무 오래 걸려서 중단했습니다."))), ["timeout"])
check("묵은 명령 폐기도 같은 실패",
      kinds(fake((0, "88초 묵은 명령 — 버림: '...'"))), ["timeout"])
check("되물음은 가볍게 깎는다",
      kinds(fake((0, "호출어 근접: '동대가.' — 되물음"))), ["reask"])
check("목소리를 못 알아봐 무시",
      kinds(fake((0, "미등록 목소리(유사도 0.41) — 무시: '...'"))), ["stranger"])

print("\n⚠ 주변 소리를 억울하게 깎지 않는가 (여기가 무너지면 지표가 죽는다)")
check("창 밖의 잡담은 아예 안 센다",
      kinds(fake((0, "들림: 'TV 에서 나오는 말'"),
                 (5, "들림: '옆 사람이 하는 말'"))), [])
check("'호출어 없음' 이라도 창이 없었으면 안 센다",
      kinds(fake((0, "호출어 없음 — 무시: '주변 대화'"))), [])
check("한참 뒤의 재호명은 재호출이 아니다 (그냥 다음 대화)",
      kinds(fake((0, "호명: '동백아' → 즉시 응답"),
                 (score.RECALL_SEC + 30, "호명: '동백아' → 즉시 응답"))), [])
check("재기동하면 열린 호명이 닫힌다",
      kinds(fake((0, "호명: '동백아' → 즉시 응답"),
                 (3, "대기 중. 호출어: 동백아"),
                 (6, "들림: '아무 말'"))), [])

print("\n거짓말 (-5, 가장 크게 깎는다)")
_LIE = "지금 이 세션에서는 캘린더에 실제로 쓰는 도구가 꺼져 있어서 등록이 안 됩니다."
check("할 수 있는데 못 한다고 하면 거짓말", bool(score.find_lies([_LIE])), True)
check("진짜로 못 하는 건 거짓말이 아니다",
      bool(score.find_lies(["메일 보낼 권한이 없습니다."])), False)
check("모른다고 하는 건 거짓말이 아니다",
      bool(score.find_lies(["등록에 실패했습니다. 왜 안 되는지는 모르겠습니다."])), False)
check("정상 답변은 당연히 아니다",
      bool(score.find_lies(["일정을 등록했습니다."])), False)
check("거짓말이 가장 크게 깎인다",
      min(p for p, _ in score.POINTS.values()), score.POINTS["lie"][0])


print("\n점수가 실제로 더해지는가")
s_rows = run(fake((0, "호명: '동백아' → 즉시 응답"), (4, "명령: '몇 시야'"),
                  (40, "호출어 없음 — 무시 (대화창 3초 지남): '...'")))
check("한 번에 통함(+2) + 창 닫힘(-2) = 0점",
      sum(r["points"] for r in s_rows), 0)

print("\n요약과 음성 보고가 나오는가")
real, real_lie = score._events, score._lie_rows
try:
    score._events = lambda days: fake((0, "호명: '동백아' → 즉시 응답"),
                                      (4, "명령: '몇 시야'"))
    score._lie_rows = lambda days: []   # 실제 기록이 시험에 새지 않게
    s = score.summary(1)
    check("성공률 계산", s["rate"], 100)
    check("시도 수", s["attempts"], 1)
    buf = io.StringIO()
    with redirect_stdout(buf):
        line = score.speak_report(1)
    check("음성 보고에 마크다운·기호가 없다",
          any(c in line for c in "*#`|"), False)
    check("부호를 말로 푼다 (TTS 가 마이너스를 못 읽는다)",
          "-" in line, False)
    check("음성 보고가 한 문단", "\n" in line, False)
finally:
    score._events, score._lie_rows = real, real_lie

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과")
