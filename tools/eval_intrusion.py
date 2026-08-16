#!/usr/bin/env python3
"""끼어듦 평가 — 동백이 '나에게 한 말' 을 가려내는가를 실데이터로 잰다.

왜 필요한가:
  "TV 소리에 대답하지 마라" 는 고치기 쉬운 말이지만, 문턱을 잘못 조이면
  정작 쓰던 말이 막힌다. 실측해 보면 답한 것의 91%가 호출어 없이 들어왔고
  그 대부분("너 뭐하냐?"·"내 말 듣고 있니?")은 **정당한 말**이다.
  그래서 감으로 고치면 안 되고, 고칠 때마다 이 평가로 두 숫자를 같이 본다.

정답표를 어떻게 만드는가 — 문장 모양이 아니라 **행동 증거**로 잡는다.
  자기가 만든 규칙으로 정답을 매기면 시험이 자기 얼굴을 비추는 거울이 된다.
  그래서 사람과 동백이 그때 실제로 한 행동만 쓴다.

  ✗ 끼어들지 말았어야 (NEG)
     - 사장님이 바로 물리셨다 — 직후 두 발화 안에 "안 불렀어"·"그만"·
       "너한테 안 했어" 가 나온 것
     - 동백이 제 답에서 헤맸다 — "잘 안 들렸습니다"·"제게 하신 건지" 류
     - 받아쓰기가 통째로 명령이 된 것 (300자 넘게, 호출어 없이)
  ✓ 답했어야 (POS)
     - 호출어가 분명히 있었다
     - 시키는 말로 끝나고(ECHO_TAIL) 짧다 — 그리고 위 NEG 증거가 없다

  둘 중 어디에도 안 걸리는 애매한 것은 세지 않는다. 억지로 편을 갈라
  숫자를 만들면 그 숫자를 믿고 잘못 조이게 된다.

쓰는 법:
    .venv/bin/python tools/eval_intrusion.py           # 현재 규칙 채점
    .venv/bin/python tools/eval_intrusion.py --list    # 정답표 열어보기
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import dbstore

SINCE = "2026-08-12T00:00"
ECHO = re.compile(config.ECHO_TAIL)
WAKE = ("동백", "똥백", "동배", "공대가", "홍백", "동급", "골박")

CONFUSED = ("잘 안 들렸", "못 알아들었", "제게 하신 건지", "다시 한 번 말씀",
            "정확히 못", "다시 말씀해")
REBUKE = ("안 불렀", "그만", "아니야", "하지마", "조용히", "너말고", "너 말고",
          "안 했어", "꺼져")


def _has_wake(t: str) -> bool:
    return any(w in t for w in WAKE)


def labelled() -> tuple[list[dict], list[dict]]:
    """(NEG, POS). 행동 증거만 쓴다."""
    rows = dbstore.rows(since=SINCE)
    order = {r["id"]: i for i, r in enumerate(rows)}
    answered = [r for r in rows
                if (r.get("reply") or "").strip() and r.get("route") != "blocked"]

    neg, pos = [], []
    for r in answered:
        cmd = (r.get("command") or "").strip()
        rep = (r.get("reply") or "")
        if not cmd:
            continue

        # ① 사장님이 바로 물리셨나 — 직후 두 발화 안
        rebuked = False
        i = order.get(r["id"], -1)
        for nxt in rows[i + 1:i + 3]:
            c = (nxt.get("command") or "")
            if len(c) < 30 and any(k in c for k in REBUKE):
                rebuked = True
                break

        # ⚠ '동백이 헤맸다' 를 통째로 NEG 로 세면 안 된다. 헤맨 이유가 둘이고
        #   고칠 곳이 서로 다르기 때문이다.
        #     헛들음  — 받아쓰기가 깨진 것 ("공개가.", "한.") → 받아쓰기 문제
        #     엉뚱상대 — 남에게 한 말을 주워들은 것        → 지금 고치는 문제
        #   "남산 미팅 확인하라고, 일정 등록하라고" 는 분명한 지시인데 동백이
        #   못 알아들은 것뿐이다. 이걸 '끼어들지 말았어야' 로 세면 정작 지시를
        #   막는 쪽으로 문턱을 조이게 된다.
        confused = any(k in rep for k in CONFUSED)
        why = None
        if rebuked:
            why = "즉시 물림"
        elif len(cmd) > 300:
            # ⚠ 호출어 유무를 따지면 안 된다. 300자 넘게 이어 붙은 덩어리에는
            #   중간에 "동백아" 가 섞여 들어가는 일이 흔하다 — 그걸 근거로
            #   POS 로 넘기면, 사고를 낸 3,499자 전사본이 '답했어야 할 말' 로
            #   둔갑해 시험이 거꾸로 선다. 실제로 그렇게 됐다.
            why = "받아쓰기가 명령이 됨"
        elif confused and not ECHO.search(cmd) and not _has_wake(cmd):
            # 지시 꼴도 호출어도 없는데 헤맸다 — 주워들은 쪽에 가깝다
            why = "헤맴(지시 아님)"

        if why:
            neg.append({**r, "왜": why})
        elif _has_wake(cmd[:20]):
            # ⚠ 호출어가 '어딘가에' 있는 걸로는 부른 게 아니다. 부를 때는 맨
            #   앞에서 부른다 ("동백아, …"). 문장 한가운데의 '동백' 은
            #   지명이거나("동백동, 호수마을") 이어 붙은 TV 소리다. 그걸
            #   POS 로 세면 시험이 '받아쓰기를 받아야 한다' 고 가르친다.
            pos.append({**r, "왜": "앞머리에서 부름"})
        elif ECHO.search(cmd) and len(cmd) <= 120:
            pos.append({**r, "왜": "짧고 시키는 말"})
        # 나머지는 애매 — 세지 않는다
    return neg, pos


def score(decide) -> dict:
    """decide(text) -> True(답한다) / False(가만히 있는다)."""
    neg, pos = labelled()
    fp = [r for r in neg if decide(r["command"])]          # 끼어들면 안 되는데 끼어듦
    fn = [r for r in pos if not decide(r["command"])]      # 답해야 하는데 침묵
    return {"neg": len(neg), "pos": len(pos),
            "끼어듦": len(fp), "놓침": len(fn), "fp": fp, "fn": fn}


def _current(text: str) -> bool:
    """지금 코드가 텍스트만 보고 내리는 판정 (2026-08-16 기준).

    호출어가 있으면 받고, 없으면 길 때만 '시키는 말' 을 따진다
    (dongbaek.py 의 COMMAND_MAX_CHARS_NO_WAKE 관문과 같은 뜻).
    짧으면 그냥 받는다 — 여기가 TV 소리가 새어 들어오는 구멍이다.
    """
    if _has_wake(text):
        return True
    if len(text) > config.COMMAND_MAX_CHARS_NO_WAKE:
        return bool(ECHO.search(text))
    return True


def main() -> int:
    if "--list" in sys.argv:
        neg, pos = labelled()
        print(f"■ 끼어들지 말았어야 {len(neg)}건")
        for r in neg:
            print(f"  [{r['ts'][5:16]}] ({r['왜']}) {r['command'][:64]}")
        print(f"\n■ 답했어야 {len(pos)}건 (앞 20)")
        for r in pos[:20]:
            print(f"  [{r['ts'][5:16]}] ({r['왜']}) {r['command'][:64]}")
        return 0

    fns = [("현재 규칙", _current)]
    try:
        import router
        if hasattr(router, "is_for_me"):
            fns.append(("새 판정 is_for_me", lambda t: router.is_for_me(t)))
    except Exception:
        pass

    for name, fn in fns:
        s = score(fn)
        print(f"\n── {name}")
        print(f"   정답표: 끼어들면 안 됨 {s['neg']}건 / 답해야 함 {s['pos']}건")
        print(f"   ✗ 끼어듦 {s['끼어듦']}/{s['neg']}"
              f"   ✗ 놓침 {s['놓침']}/{s['pos']}")
        for r in s["fp"][:6]:
            print(f"      끼어듦: ({r['왜']}) {r['command'][:56]}")
        for r in s["fn"][:6]:
            print(f"      놓침:   {r['command'][:56]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
