#!/usr/bin/env python3
"""층별 시간·정확도 — 동백이 스스로 재고, 그 숫자로 자란다.

2026-08-12 하루가 이걸 만든 이유다. 사장님이 "느리다", "못 알아듣는다" 고
하실 때마다 사람이 로그를 뒤져 손으로 쟀다. 규칙이 못 읽는 표현도 사람이
하나씩 찾아 메웠다 — '변경' 두 글자 때문에 세 곳을 고쳤다.

동백은 세 층으로 답한다. 각 층이 얼마나 걸리고 얼마나 맞히는지 알아야
어디를 고칠지 스스로 정할 수 있다.

    규칙   0.03초        정규식·낱말표. 빠르지만 새 표현에 깨진다.
    큐웬   0.6초 · 0원   문맥을 읽는다. 숫자는 못 맡긴다.
    클로드 10초 · 비쌈    마지막 수단.

여기서 재는 것 셋:

  ① 시간   — 층별로 얼마나 걸리나. 느려지면 알아챈다.
  ② 정확도 — 답이 나왔나, 그리고 사장님이 곧바로 고쳐 말씀하셨나.
              바로 다시 말씀하시는 것이 가장 정확한 오답 신호다.
              (score.py 의 '재호명 = 앞이 실패' 와 같은 원리)
  ③ 규칙 구멍 — 큐웬은 읽었는데 규칙은 못 읽은 표현. 다음에 규칙으로
              내릴 후보다. 0.6초를 0.03초로 만드는 목록이다.

⚠ 자동으로 규칙을 고치지는 않는다. 후보만 모아 IMPROVE.md 에 올리고
  판단은 야간 자가정비가 증거를 보고 한다. 오늘 사람이 규칙을 세 번
  고치면서 세 번 다 다른 데를 깨뜨렸다 — 자동화가 더 잘할 리 없다.

    python perf.py              # 오늘
    python perf.py --days 7
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

import config

LOG = config.STATE / "perf.jsonl"

# 이 시간 안에 다시 말씀하시면 '앞 답이 틀렸다' 는 신호로 본다.
# score.RECALL_SEC 과 같은 뜻이고, 같은 근거다 — 사람은 답이 맞으면
# 곧바로 같은 말을 다시 하지 않는다.
CORRECTION_SEC = 25

# 층별로 '이 정도면 느리다' 는 선. 넘으면 보고에 뜬다.
SLOW = {"local": 0.5, "qwen": 3.0, "claude": 20.0}


def _is_test_process() -> bool:
    """지금 도는 게 테스트인가.

    함수로 빼 둔 이유: 테스트가 계측 자체를 검사하려면 이걸 잠깐 꺼야
    한다. 조건을 record 안에 박아 두면 테스트가 자기 기록을 못 남겨
    아무것도 검사할 수 없다 (실제로 그렇게 만들었다가 막혔다).
    """
    import os

    return os.path.basename(sys.argv[0] or "").startswith("test_")


def record(route: str, seconds: float, ok: bool, command: str = "",
           note: str = "", first_sec: float | None = None) -> None:
    """한 건을 남긴다. 실패해도 조용히 넘어간다 — 계측이 본업을 막으면 안 된다.

    ⚠ 테스트가 돌 때는 남기지 않는다. 테스트는 가짜 브릿지를 꽂아 0초에
      끝나므로, 섞이면 '클로드 중앙 0.00초' 같은 거짓 숫자가 나온다.
      실제로 처음 붙였을 때 66건 중 대부분이 테스트분이었다.
      같은 사고가 이 저장소에 이미 있다 — 테스트가 사장님 폰으로 자가개선
      보고를 쐈던 일(self_improve._report 의 같은 가드).
    """
    if _is_test_process():
        return
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "route": route,
                "sec": round(float(seconds), 3),
                "ok": bool(ok),
                "command": (command or "")[:80],
                "note": (note or "")[:60],
                # 첫 소리까지의 시간. sec 은 '다 끝나기까지' 라 사장님이
                # 느끼는 시간이 아니다 — 답은 첫 문장이 도착하면 바로
                # 소리로 나가기 시작한다(bridge 의 on_text → speak.Stream).
                # 08-13 22:56 "왜 이렇게 대답을 안 하냐" 를 받고도 고칠 곳을
                # 못 정했던 건 이 값이 없어서였다. sec 만 보면 6.77초인데
                # 그게 침묵 6.77초인지 아닌지를 구분할 수 없었다.
                **({"first_sec": round(float(first_sec), 3)}
                   if first_sec is not None else {}),
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


class track:
    """with 로 감싸면 시간이 남는다.

        with perf.track("qwen", command) as t:
            result = ...
            t.ok = result is not None
    """

    def __init__(self, route: str, command: str = "", note: str = ""):
        self.route, self.command, self.note = route, command, note
        self.ok = False
        self.first_sec: float | None = None

    def __enter__(self):
        self._t0 = time.monotonic()
        return self

    def mark_first(self) -> None:
        """첫 글자가 도착했다 — 여기서부터 소리가 나가기 시작한다.

        여러 번 불려도 첫 번째만 남는다. 호출측이 스트림 조각마다 부르게
        두는 편이 '첫 조각인가' 를 호출측에서 따지는 것보다 덜 틀린다.
        """
        if self.first_sec is None:
            self.first_sec = time.monotonic() - self._t0

    def __exit__(self, exc_type, exc, tb):
        record(self.route, time.monotonic() - self._t0,
               self.ok and exc_type is None, self.command, self.note,
               first_sec=self.first_sec)
        return False


def _rows(days: int) -> list[dict]:
    since = (datetime.now() - timedelta(days=days)).isoformat()
    out = []
    try:
        for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("ts", "") >= since:
                out.append(r)
    except OSError:
        pass
    return out


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * p))]


def summary(days: int = 1) -> dict:
    """층별 건수·시간·성공률. 중앙값과 95퍼센타일을 같이 본다 —
    평균만 보면 가끔 터지는 느림이 묻힌다."""
    rows = _rows(days)
    by: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by[r.get("route", "?")].append(r)

    out = {}
    for route, rs in by.items():
        secs = [r["sec"] for r in rs if isinstance(r.get("sec"), (int, float))]
        good = sum(1 for r in rs if r.get("ok"))
        out[route] = {
            "count": len(rs),
            "ok": good,
            "rate": round(good / len(rs) * 100) if rs else 0,
            "p50": round(_pct(secs, 0.5), 2),
            "p95": round(_pct(secs, 0.95), 2),
            "slow": round(_pct(secs, 0.5), 2) > SLOW.get(route, 99),
        }
    return {"days": days, "total": len(rows), "routes": out}


def rule_gaps(days: int = 7, min_count: int = 1) -> list[dict]:
    """큐웬은 읽었는데 규칙은 못 읽은 표현 — 규칙으로 내릴 후보.

    0.6초를 0.03초로 만드는 목록이다. 같은 꼴이 거듭 나오면 그만큼
    자주 느려지고 있다는 뜻이다.
    """
    seen: dict[str, dict] = {}
    for r in _rows(days):
        if r.get("route") != "qwen" or not r.get("ok"):
            continue
        key = (r.get("note") or "").strip() or (r.get("command") or "")[:24]
        if not key:
            continue
        e = seen.setdefault(key, {"key": key, "count": 0, "example": r.get("command", "")})
        e["count"] += 1
    return sorted((e for e in seen.values() if e["count"] >= min_count),
                  key=lambda e: -e["count"])


def corrections(days: int = 1) -> list[dict]:
    """답을 드린 직후에 다시 말씀하신 건 — 앞 답이 틀렸다는 신호."""
    rows = sorted(_rows(days), key=lambda r: r.get("ts", ""))
    out = []
    for i, r in enumerate(rows[:-1]):
        nxt = rows[i + 1]
        try:
            gap = (datetime.fromisoformat(nxt["ts"])
                   - datetime.fromisoformat(r["ts"])).total_seconds()
        except (KeyError, ValueError):
            continue
        if 0 <= gap <= CORRECTION_SEC and r.get("ok"):
            out.append({"ts": r["ts"], "route": r.get("route"),
                        "command": r.get("command", ""), "gap": round(gap)})
    return out


def speak_report(days: int = 1) -> str:
    """음성으로 읽을 한 문단. 숫자는 단위까지, 기호는 말로."""
    s = summary(days)
    if not s["total"]:
        return "아직 잰 기록이 없습니다."
    parts = []
    for route, name in (("local", "로컬"), ("qwen", "큐웬"), ("claude", "클로드")):
        d = s["routes"].get(route)
        if d:
            parts.append(f"{name} {d['count']}건 {d['p50']}초")
    line = f"오늘 {s['total']}건 처리했습니다. " + ", ".join(parts) + "입니다."
    slow = [n for r, n in (("local", "로컬"), ("qwen", "큐웬"), ("claude", "클로드"))
            if s["routes"].get(r, {}).get("slow")]
    if slow:
        line += f" {', '.join(slow)}가 평소보다 느립니다."
    gaps = rule_gaps(days=7, min_count=2)
    if gaps:
        line += f" 큐웬이 대신 읽은 표현이 {len(gaps)}가지 있어, 규칙으로 내리면 더 빨라집니다."
    return line


def main() -> int:
    days = 1
    if "--days" in sys.argv:
        try:
            days = int(sys.argv[sys.argv.index("--days") + 1])
        except (IndexError, ValueError):
            pass
    s = summary(days)
    print(f"\n최근 {days}일 — 처리 {s['total']}건\n")
    if not s["total"]:
        print("  (아직 기록이 없습니다)\n")
        return 0
    print(f"  {'층':8} {'건수':>5} {'성공':>5} {'중앙':>7} {'95%':>7}")
    print("  " + "─" * 40)
    for route in ("local", "qwen", "claude"):
        d = s["routes"].get(route)
        if not d:
            continue
        mark = "  ← 느림" if d["slow"] else ""
        print(f"  {route:8} {d['count']:5} {d['rate']:4}% "
              f"{d['p50']:6.2f}초 {d['p95']:6.2f}초{mark}")

    gaps = rule_gaps(days=max(days, 7))
    if gaps:
        print(f"\n  규칙 구멍 — 큐웬이 대신 읽은 표현 (규칙으로 내릴 후보):")
        for g in gaps[:5]:
            print(f"    {g['count']}회  {g['example'][:52]!r}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
