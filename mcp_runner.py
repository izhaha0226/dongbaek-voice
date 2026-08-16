#!/usr/bin/env python3
"""동백 MCP 도구의 실제 실행부 — 본 venv(.venv) 에서 돈다.

왜 서버와 실행부를 가르나 (PLAN-unify 1단계):
  MCP 서버(dongbaek_mcp.py)는 mcp 2.x 라 .venv-mcp 에서 돌아야 하는데,
  도구가 부르는 로컬 모듈들은 본 venv 것이다 — EventKit(pyobjc)·numpy 를
  .venv-mcp 에 또 깔면 venv 둘을 계속 맞춰야 한다. 실행부를 이 파일로 빼
  서버가 `.venv/bin/python mcp_runner.py <도구> <json인자>` 로 부르면
  의존성은 한 곳에만 있고, 호출마다 새 프로세스라 상태도 늘 신선하다.
  기동 ~0.3초는 도구 호출 빈도(하루 수십 회)에서 문제가 안 된다.

  덤: EventKit(캘린더) 권한은 바이너리에 붙는다 — 데몬과 같은
  .venv/bin/python 이라 이미 허가된 권한을 그대로 쓴다.

직접 시험:
  .venv/bin/python mcp_runner.py calendar '{"days": 1}'
"""

from __future__ import annotations

import json
import sys


def run(tool: str, args: dict) -> str:
    if tool == "ads":
        import ads_local

        q = str(args.get("question", ""))
        out = ads_local.speak(q)
        if not out:
            pr = ads_local.period(q)
            if pr:
                out = ads_local.analysis(pr[0], pr[1], pr[2])
        return out or "광고 질문으로 못 읽었습니다. 기간(오늘/이번달)이나 업체명을 넣어주세요."

    if tool == "calendar":
        import calendar_local

        return calendar_local.speak_events(
            days=int(args.get("days", 7)),
            limit=int(args.get("limit", 10)),
            keyword=args.get("keyword") or None,
        )

    if tool == "recall":
        import memory_local

        hits = memory_local.recall(str(args.get("query", "")), k=int(args.get("k", 5)))
        return " / ".join(hits) if hits else "관련 기억이 없습니다."

    if tool == "score_today":
        import score

        return score.speak_report(days=int(args.get("days", 1)))

    if tool == "history":
        import dbstore

        q = str(args.get("query", "")).strip()
        if q:
            return dbstore.search(q, days=int(args.get("days", 7)))
        return dbstore.recent_brief(int(args.get("n", 10)), max_chars=100) or "기록 없음"

    if tool == "mail_watch_add":
        import mail_watch

        keyword = str(args.get("keyword", "")).strip()
        if not keyword:
            return "키워드가 없습니다."
        note = str(args.get("note", "")).strip()
        w = mail_watch.add(keyword, note)
        return f"'{w['keyword']}' 메일 오면 알려드릴게요. 30분마다 확인합니다."

    if tool == "logs":
        # 데몬 일지 검색 — "내가 몇 번 불렀어?" 에 답하려면 이게 있어야
        # 한다 (2026-08-13 06:0x 실사례: "로그를 직접 볼 도구가 없어서").
        import re as _re

        import config as _config

        q = str(args.get("query", "")).strip()
        n = min(int(args.get("lines", 40)), 200)
        path = _config.STATE / "daemon.log"
        if not path.exists():
            return "일지 파일이 없습니다."
        lines = path.read_text(errors="replace").splitlines()
        if q:
            rx = _re.compile(q) if any(c in q for c in "|[](){}.*+?^$\\") else None
            lines = [l for l in lines
                     if (rx.search(l) if rx else q in l)]
        picked = lines[-n:]
        return (f"(해당 {len(lines)}줄 중 끝 {len(picked)}줄)\n" + "\n".join(picked)
                if picked else "해당하는 줄이 없습니다.")

    return f"모르는 도구: {tool}"


def main() -> int:
    tool = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    except json.JSONDecodeError:
        print("인자가 JSON 이 아닙니다")
        return 2
    try:
        print(run(tool, args if isinstance(args, dict) else {}))
        return 0
    except Exception as e:  # 도구 하나의 실패가 서버를 죽이면 안 된다
        print(f"ERROR: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
