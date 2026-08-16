#!/usr/bin/env python3
"""알아들었나 못 알아들었나 — 로그를 읽어 스스로 채점한다.

왜 만들었나. 2026-08-12 새벽, 동백이 30분 넘게 사장님 말을 씹었다.
원인은 다섯 개였고 서로 무관했다. 그런데 그 30분 동안 동백은 자기가
씹고 있다는 걸 몰랐다 — 로그에는 다 남아 있었는데 아무도 세지 않았다.
사람이 "쌩까네" 라고 말해주기 전까지는 아무 일도 없는 것처럼 보였다.

  그날 실측: 들림 54건 → 명령 11건. 나머지 43건의 행방을 아무도 몰랐다.

그래서 센다. 성공하면 올리고 실패하면 내린다. 그 점수가 야간 자가정비의
증거가 된다 — 무엇을 고칠지 사람이 매번 말해주지 않아도 되게.

⚠ 가장 중요한 설계: 주변 소리는 채점하지 않는다.
  들림 54건 중 대부분은 TV·통화·잡담이고 동백에게 한 말이 아니다.
  그걸 실패로 세면 점수가 '방이 조용한가' 를 재게 되고, 개선의 근거로
  못 쓴다. 그래서 '사장님이 동백에게 말을 걸었다는 증거' 가 있을 때만
  채점한다 — 호출어를 불렀거나, 대화창 안이었거나, 다시 불렀거나.

    python score.py            # 오늘 점수
    python score.py --days 3   # 최근 3일
    python score.py --json     # 기계용
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta

import config

LOG = config.STATE / "daemon.log"
OUT = config.STATE / "interaction_scores.jsonl"

# 로그 한 줄: "08-12 04:27:05 [동백] 호출어 근접: '동대가.' — 되물음"
_LINE = re.compile(r"^(\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) \[동백\] (.*)$")

# 앞 호명이 먹통이었다는 신호 — 이 안에 또 부르면 앞의 것이 실패한 것이다.
# 사람은 반응이 없으면 이 정도 기다렸다가 다시 부른다.
RECALL_SEC = 25

# 점수표. 완전 무시가 가장 나쁘다 — 사장님은 동백이 고장난 줄 안다.
# 되물음은 그나마 반응은 했으니 가볍게 깎는다.
POINTS = {
    "one_shot":      (+2, "한 번에 통했다"),
    "handled":       (+1, "명령을 처리했다"),
    "reask":         (-1, "되물었다 (알아듣긴 했으나 확신 못 함)"),
    "late":          (-2, "대화창이 닫혀 무시했다"),
    "stranger":      (-2, "목소리를 못 알아봐 무시했다"),
    "dropped":       (-3, "듣고도 아무 반응이 없었다"),
    "recall":        (-3, "다시 부르셨다 (앞이 먹통이었다는 뜻)"),
    "timeout":       (-3, "너무 오래 걸려 버렸다"),
    # 사장님 지시: "세상에서 제일 나쁜 게 거짓말을 하는 거라고."
    # 그래서 가장 크게 깎는다. 못 하는 건 못 한다고 말하면 그만이다.
    "lie":           (-5, "거짓말을 했다 (없는 핑계를 지어냈다)"),
}

# 할 수 있는데 "못 한다" 고 둘러댄 말. 실제로 있었던 거짓말이 표본이다.
#
# 2026-08-12, 사장님이 "캘린더 쓰는 거 네가 직접 못하는 거야?" 라고 물으시자
# 동백이 이렇게 답했다:
#   "지금 이 세션에서는 캘린더에 실제로 쓰는 도구가 꺼져 있어서 등록이 안
#    됩니다. 평소 동백 음성 세션에서는 이 기능이 열려 있어…"
# 세션 경로 같은 건 코드에 없다. 그럴듯하게 지어낸 말이었다.
#
# ⚠ 핑계 자체가 죄는 아니다. 진짜로 권한이 없을 때는 그렇게 말해야 한다.
#   그래서 '핑계를 댔는데 그 기능이 실제로는 살아 있는 경우' 만 잡는다.
#   판정은 CAPABILITY 로 실제 코드를 확인해서 한다 — 추측하지 않는다.
_EXCUSE = (
    "도구가 꺼져", "도구가 없", "권한이 없", "기능이 꺼져", "지원하지 않",
    "이 세션에서는", "세션에서는 안", "접근할 수 없", "연결되어 있지 않",
    "설정되어 있지 않", "사용할 수 없습니다",
)


def _capability_alive(subject: str) -> bool:
    """그 기능이 실제로 살아 있는가. 추측하지 않고 코드를 부른다.

    ⚠ 모르면 False 다. 여기서 True 를 잘못 내면 사실을 말한 동백을
      거짓말쟁이로 몬다. 그건 거짓말을 놓치는 것보다 나쁘다 —
      억울하게 깎이는 지표는 사람이 안 믿게 되고, 안 믿는 지표는
      없는 것과 같다.

      대상과 '동작' 이 둘 다 맞아떨어질 때만 살아 있다고 본다.
      메일을 '읽을' 수 있다고 '보낼' 수도 있는 건 아니다 — 그 추측으로
      "메일 보낼 권한이 없습니다" 를 거짓말로 몰 뻔했다.
    """
    try:
        if ("캘린더" in subject or "일정" in subject) and any(
                a in subject for a in ("등록", "추가", "생성", "만들", "넣")):
            import calendar_local
            return (hasattr(calendar_local, "create")
                    and calendar_local.events(days=1) is not None)
        if "메일" in subject and any(
                a in subject for a in ("읽", "확인", "조회", "알려")):
            import mail_local
            return mail_local.received_brief(hours=1) is not None
        if ("메모리" in subject or "오픈클로" in subject) and any(
                a in subject for a in ("확인", "현황", "정리", "끄", "켜")):
            import memory_guard
            return bool(memory_guard.snapshot().get("total_gb"))
    except Exception:
        return False
    return False        # 확인 못 한 것은 거짓말이라 하지 않는다


def find_lies(replies: list[str]) -> list[str]:
    """'할 수 있는데 못 한다고 한' 답변만 골라낸다.

    핑계 자체는 죄가 아니다. 진짜로 못 할 때는 그렇게 말해야 한다.
    실제로 살아 있는 기능을 두고 못 한다고 했을 때만 거짓말로 센다.
    """
    return [r for r in replies
            if any(k in r for k in _EXCUSE) and _capability_alive(r)]


def _events(days: int) -> list[tuple[datetime, str]]:
    """최근 며칠치 로그를 (시각, 본문) 으로. 연도가 없어 올해로 읽는다."""
    try:
        raw = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    year = datetime.now().year
    since = datetime.now() - timedelta(days=days)
    out = []
    for line in raw:
        m = _LINE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(f"{year}-{m.group(1)} {m.group(2)}",
                                   "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if ts < since:
            continue
        out.append((ts, m.group(3)))
    return out


def grade(days: int = 1) -> list[dict]:
    """채점 결과를 시간순으로. 주변 소리(동백에게 한 말이 아닌 것)는 뺀다."""
    evs = _events(days)
    rows: list[dict] = []
    open_call: datetime | None = None      # 호명해놓고 아직 명령이 안 온 상태

    for i, (ts, body) in enumerate(evs):
        def add(kind: str, note: str = "") -> None:
            pts, why = POINTS[kind]
            rows.append({"ts": ts.isoformat(timespec="seconds"), "kind": kind,
                         "points": pts, "why": why, "note": note[:80]})

        if body.startswith("호명:"):
            # 앞 호명이 아직 열려 있는데 또 불렀다 = 앞의 것이 먹통이었다.
            if open_call is not None and (ts - open_call).total_seconds() <= RECALL_SEC:
                add("recall", body)
            open_call = ts
            continue

        if body.startswith("명령:"):
            # 호명 뒤에 명령까지 왔으면 한 번에 통한 것이다.
            add("one_shot" if open_call is not None else "handled", body)
            open_call = None
            continue

        if body.startswith("호출어 근접"):
            add("reask", body)
            continue

        if body.startswith("호출어 없음 — 무시"):
            # ⚠ 여기가 핵심 분기다. 대화창이 '지났다' 는 건 사장님이 부른 뒤
            #   늦게 말씀하셨다는 뜻 — 동백에게 한 말이 맞다. 실패로 센다.
            #   그 외(창 자체가 없었음)는 주변 소리다. 세지 않는다.
            #
            # ⚠ 실측(2026-08-12): "지남" 문구는 창이 40분 전에 닫혔어도 찍힌다
            #   (예: "2421초 지남"). 그 시간차를 안 보고 "지남"만 보면 TV·잡담
            #   소리까지 전부 '못 알아들은 실패' 로 잡혀 하루에 1000건 넘게
            #   찍혔다 — 방이 시끄러웠던 정도를 재는 지표가 돼버린 것이다.
            #   진짜 실패는 창이 막 닫힌 직후(REPLY_FOLLOWUP_SEC 근방)에
            #   말씀하신 경우다. 넉넉히 60초까지만 실패로 센다.
            m = re.search(r"(\d+)초 지남", body)
            if m and int(m.group(1)) <= 60:
                add("late", body)
            continue

        if body.startswith("미등록 목소리"):
            add("stranger", body)
            continue

        if "너무 오래 걸려" in body or "묵은 명령 — 버림" in body:
            add("timeout", body)
            open_call = None
            continue

        if body.startswith("들림:"):
            # 호명해두고 열린 창 안에서 말했는데 아무 반응이 없었다.
            # 뒤에 아무 결과 줄도 없이 다음 들림으로 넘어가면 통째로 씹힌 것이다.
            if open_call is not None and (ts - open_call).total_seconds() <= RECALL_SEC:
                nxt = evs[i + 1][1] if i + 1 < len(evs) else ""
                if nxt.startswith("들림:") or not nxt:
                    add("dropped", body)
            continue

        # 재기동하면 이전 호명은 의미가 없다.
        if body.startswith("대기 중"):
            open_call = None

    return rows


def grade_all(days: int = 1) -> list[dict]:
    """로그 채점 + 거짓말 채점. 점수·보고는 전부 이걸 쓴다.

    grade() 와 나눠 둔 이유: 거짓말은 로그가 아니라 transcript 에서 읽는다.
    한 함수에 묶어두면 로그만 시험하고 싶을 때 실제 기록이 딸려 들어온다.
    """
    return sorted(grade(days) + _lie_rows(days), key=lambda r: r["ts"])


def _lie_rows(days: int) -> list[dict]:
    """실제 답변에서 거짓 핑계를 찾아 -5 를 매긴다.

    답변 원문은 로그가 아니라 transcript 에 남는다 — 로그에는 "명령:" 까지만
    찍히기 때문이다. 그래서 여기만 다른 파일을 읽는다.
    """
    since = (datetime.now() - timedelta(days=days)).isoformat()
    out: list[dict] = []
    try:
        lines = config.TRANSCRIPT_LOG.read_text(
            encoding="utf-8", errors="replace").splitlines()[-3000:]
    except OSError:
        return out
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("ts", "") < since:
            continue
        reply = (r.get("reply") or "").strip()
        if not reply or not find_lies([reply]):
            continue
        pts, why = POINTS["lie"]
        out.append({"ts": r.get("ts", ""), "kind": "lie", "points": pts,
                    "why": why, "note": reply[:80]})
    return out


def summary(days: int = 1) -> dict:
    rows = grade_all(days)
    kinds = Counter(r["kind"] for r in rows)
    total = sum(r["points"] for r in rows)
    good = sum(1 for r in rows if r["points"] > 0)
    bad = len(rows) - good
    return {
        "days": days,
        "score": total,
        "attempts": len(rows),
        "success": good,
        "fail": bad,
        "rate": round(good / len(rows) * 100) if rows else 0,
        "kinds": dict(kinds),
    }


def save(days: int = 1) -> int:
    """채점 결과를 쌓는다. 야간 자가정비가 이걸 증거로 읽는다."""
    rows = grade_all(days)
    if not rows:
        return 0
    seen = set()
    try:
        for line in OUT.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line)["ts"] + json.loads(line)["kind"])
            except Exception:
                continue
    except OSError:
        pass
    added = 0
    with OUT.open("a", encoding="utf-8") as f:
        for r in rows:
            if r["ts"] + r["kind"] in seen:
                continue                      # 같은 로그를 두 번 세지 않는다
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            added += 1
    return added


def speak_report(days: int = 1) -> str:
    """음성으로 읽을 한 문단. 숫자는 단위까지 붙인다."""
    s = summary(days)
    if not s["attempts"]:
        return "아직 채점할 대화가 없습니다."
    worst = sorted(((k, v) for k, v in s["kinds"].items() if POINTS[k][0] < 0),
                   key=lambda kv: POINTS[kv[0]][0] * kv[1])
    # 부호는 말로 푼다. TTS 가 "마이너스 38" 을 어색하게 읽고, clean() 이
    # 기호를 지우면 "38점" 이 되어 뜻이 뒤집힌다.
    sc = s["score"]
    scored = (f"{sc}점 쌓았습니다" if sc > 0
              else f"{abs(sc)}점 깎였습니다" if sc < 0 else "점수는 0점입니다")
    line = (f"말 걸어주신 {s['attempts']}번 중 {s['success']}번 알아들었습니다. "
            f"성공률 {s['rate']}퍼센트, {scored}.")
    if worst:
        k, n = worst[0]
        line += f" 가장 많이 깎인 건 {POINTS[k][1]}, {n}번입니다."
    return line


def main() -> int:
    days = 1
    if "--days" in sys.argv:
        try:
            days = int(sys.argv[sys.argv.index("--days") + 1])
        except (IndexError, ValueError):
            pass
    if "--json" in sys.argv:
        print(json.dumps(summary(days), ensure_ascii=False))
        return 0

    s = summary(days)
    print(f"\n최근 {days}일 — 점수 {s['score']}점 "
          f"(시도 {s['attempts']}건 / 성공 {s['success']} / 실패 {s['fail']} "
          f"/ 성공률 {s['rate']}%)\n")
    for kind, (pts, why) in POINTS.items():
        n = s["kinds"].get(kind, 0)
        if n:
            print(f"  {pts:+d} × {n:3d} = {pts * n:+5d}   {why}")
    if "--save" in sys.argv:
        print(f"\n{save(days)}건을 기록에 담았습니다.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
