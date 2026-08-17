#!/usr/bin/env python3
"""스스로 이상을 알아채고 먼저 말한다 — 사장님이 물어보기 전에.

사장님 지시(2026-08-16) 중 둘째. 그날 하루를 돌아보면 고친 것 대부분이
같은 꼴로 발견됐다:

    "이 일정들 뭐냐"            → 캘린더 쓰레기 56건
    "대화가 두마디를 못넘어가네"  → '됐어' 오판
    "메일 받았어, 꺼도 돼"       → 알림이 안 울린 걸 그때 알았다
    "자꾸 못 알아듣는다"         → 목소리 학습 기억이 3.5시간뿐이었다
    "텔레그램에 전혀 안 들어오네"  → 들림 78건 중 명령 6건 (마이크가 안 잡힘)

전부 **사장님이 겪고 물어봐야** 시작됐다. 동백은 그동안 채점을 하고
있었는데(7일 −582점) 그 숫자를 아무도 안 봤다. 숫자가 있는데 안 보는 것과
숫자가 없는 것은 같다.

여기서 보는 것은 '평소와 다른가' 뿐이다. 절대값으로 문턱을 박으면 환경이
바뀔 때마다 거짓 경보가 나고, 그러면 다음부터 아무도 안 본다.

    python selfcheck.py            # 지금 상태를 사람이 읽는 꼴로
    python selfcheck.py --speak    # 이상이 있으면 소리로도 알린다
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from dblog import log

LOG = config.STATE / "daemon.log"

# 귀가 먹은 것으로 볼 선. 들린 소리 중 명령까지 간 비율이다.
#
# ⚠ 절대 건수가 아니라 비율로 본다. 조용한 날은 들림도 명령도 적다 —
#   건수로 재면 "오늘 명령이 3건뿐" 이 늘 경보가 된다.
#   실측(2026-08-16 저녁): 들림 78건 중 명령 6건(8%) 일 때 사장님이
#   "텔레그램에 전혀 안 들어오네" 하셨다. 그때 마이크가 사장님 목소리를
#   거의 못 잡고 있었다(원본 음량 중앙 0.0138, 문턱 0.006).
HEARD_MIN = 12          # 이만큼은 들려야 비율을 따진다
PASS_RATE_LOW = 0.20    # 들린 것의 20% 도 명령이 안 되면 이상하다
LEVEL_LOW = 0.020       # 원본 음량 중앙이 이보다 낮으면 마이크가 먼 것이다

# 한 갈래가 이만큼 내리 안 보이면 나은 것으로 보고 다시 말할 수 있게 한다.
# 1 로 두면 문턱 언저리에서 깜빡이는 갈래가 매 판 새로 보여 되풀이가 돌아온다.
FORGET_AFTER = 2


def _tail(hours: float) -> list[str]:
    since = datetime.now() - timedelta(hours=hours)
    out = []
    try:
        for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-6000:]:
            try:
                t = datetime.strptime(f"{datetime.now().year}-{line[:14]}",
                                      "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if t >= since:
                out.append(line)
    except OSError:
        pass
    return out


def check_kinds(hours: float = 2.0) -> list[tuple[str, str]]:
    """지금 이상한 것들을 (갈래, 말) 로. 없으면 빈 목록.

    갈래는 숫자가 빠진 고정 이름이다. 되풀이를 가리는 쪽이 문장을 통째로
    비교하면 "들림 102건 중 명령 6건" 과 "들림 91건 중 명령 0건" 이 서로
    다른 이상으로 보인다 — 숫자는 볼 때마다 바뀌니 중복 제거가 한 번도
    안 걸린다. 2026-08-16 22:29~08-17 01:05 에 같은 점검이 텔레그램으로
    11번 나간 것이 그래서다. 소음이 되면 다음부터 안 보신다.
    """
    lines = _tail(hours)
    bad: list[tuple[str, str]] = []

    heard = sum(1 for l in lines if "들림:" in l)
    cmds = sum(1 for l in lines if "] 명령:" in l)
    levels = [float(m) for m in
              re.findall(r"원본 (0\.\d+)", "\n".join(lines))]

    # ① 귀가 먹었나 — 들리는데 명령이 안 된다
    if heard >= HEARD_MIN and cmds / max(heard, 1) < PASS_RATE_LOW:
        bad.append(("귀먹음",
                    f"소리는 들리는데 명령까지 못 갑니다. "
                    f"최근 {hours:.0f}시간 들림 {heard}건 중 명령 {cmds}건."))

    # ② 마이크가 먼가 — 음량 자체가 낮다
    if len(levels) >= 8:
        levels.sort()
        mid = levels[len(levels) // 2]
        if mid < LEVEL_LOW:
            bad.append(("음량낮음",
                        f"마이크에 잡히는 소리가 작습니다(중앙 {mid:.4f}). "
                        f"입력 볼륨이나 거리를 확인해 주세요."))

    # ③ 환청 — 무음에서 whisper 가 지어내는 인사말이 잦다
    thanks = sum(1 for l in lines
                 if "들림:" in l and re.search(r"'(감사합니다|thank you)[.!]?'", l))
    if heard >= HEARD_MIN and thanks >= max(3, heard * 0.15):
        bad.append(("환청",
                    f"빈 소리에서 인사말 환청이 잦습니다({thanks}건). "
                    f"마이크가 멀거나 주변이 시끄러운 신호입니다."))

    # ④ 대화 채점이 나쁜가 — 어제와 견준다
    try:
        import score

        s = score.summary(days=1)
        if s["attempts"] >= 10 and s["rate"] < 35:
            worst = sorted(((k, n) for k, n in s["kinds"].items()
                            if score.POINTS[k][0] < 0),
                           key=lambda kv: score.POINTS[kv[0]][0] * kv[1])
            why = f" 가장 잦은 실패는 '{score.POINTS[worst[0][0]][1]}'" if worst else ""
            bad.append(("채점낮음", f"오늘 대화 성공률이 {s['rate']}% 입니다.{why}"))
    except Exception:
        pass

    # ⑤ 구명정에 줄이 생겼나 — DB 쓰기가 실패했다는 뜻
    try:
        if config.TRANSCRIPT_LOG.stat().st_size > 0:
            bad.append(("구명정",
                        "기록 DB 쓰기가 실패해 구명정 파일에 쌓이고 있습니다."))
    except OSError:
        pass

    return bad


def pick_fresh(bad: list[tuple[str, str]],
               said: dict[str, int]) -> list[tuple[str, str]]:
    """이번에 **말할 것**만 고른다. said 는 갈래→사라진 뒤 지난 판 수로, 여기서 고쳐진다.

    잊는 것도 갈래마다 따로여야 한다. 억제는 갈래로 하면서 해제만 '이상이
    하나도 없을 때 통째로' 였다 — 만성 이상 하나가 걸려 있으면 나머지가
    영영 입을 못 연다. 08-16 22:29~08-17 01:05 이 그 판이다: 음량낮음이
    열한 판 내내 켜져 있어 bad 가 한 번도 안 비었고, 01:05 에 나은 귀먹음은
    그 뒤로 다시 도져도 못 알린다.

    ⚠ 한 판 사라졌다고 바로 잊지는 않는다. 귀먹음은 '들림 12건' 문턱
      언저리에서 켜졌다 꺼졌다 한다(그날 밤 들림 14→12→12). 한 판만 보고
      잊으면 15분마다 되풀이하던 어제로 되돌아간다. 두 판 내리 없을 때만
      나은 것으로 본다.

    ⚠ 통째로 멀쩡한 판은 한 번으로 충분하다 — 갈래 하나가 깜빡이는 것과
      달리 '다 나았다' 는 흔들릴 여지가 없다.
    """
    if not bad:
        said.clear()
        return []

    now = {k for k, _ in bad}
    for k in list(said):
        if k in now:
            said[k] = 0
        elif said[k] + 1 >= FORGET_AFTER:
            del said[k]          # 나았으니 다시 말할 수 있다
        else:
            said[k] += 1

    fresh = [(k, t) for k, t in bad if k not in said]
    said.update((k, 0) for k, _ in fresh)
    return fresh


def check(hours: float = 2.0) -> list[str]:
    """지금 이상한 것들을 말로만. 없으면 빈 목록 — 그때는 아무 말도 안 한다."""
    return [text for _, text in check_kinds(hours)]


def main() -> int:
    bad = check()
    if not bad:
        print("✓ 이상 없음")
        return 0
    for b in bad:
        print("⚠ " + b)
        log(f"자가점검: {b}")
    if "--speak" in sys.argv:
        try:
            import speak

            speak.say("점검 결과를 알려드릴게요. " + " ".join(bad), block=False)
        except Exception:
            pass
    # 밖에 계실 때는 소리가 소용없다 — 폰으로도 보낸다.
    try:
        import briefing

        briefing._to_telegram("🩺 동백 자가점검", "\n".join("· " + b for b in bad))
    except Exception:
        pass
    return 1


if __name__ == "__main__":
    sys.exit(main())
