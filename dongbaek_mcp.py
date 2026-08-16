#!/usr/bin/env python3
"""동백 본체 도구 — 광고 DB·캘린더·기억·채점·대화 이력을 클로드에 노출하는 MCP 서버.

왜 (PLAN-unify 1단계, 2026-08-13 승인):
  "광고플랫폼 성과 분석해줘" 가 클로드 경로에 걸리면 "DB 도구가
  없습니다" 로 끝났다 — 데이터는 로컬이 갖고, 분석력은 클로드가 갖는데
  둘이 만나지 않았다 (08-12 22:03 실사례). 이 서버가 그 다리다.

왜 .venv-mcp 로 도는가 (⚠ .venv 로 실행하면 ImportError):
  mail_mcp 와 같다 — mcp 2.x 전용 API 를 쓰는데 본 venv 의
  claude-agent-sdk 가 mcp<2.0.0 을 강제한다. mcp-dongbaek.json 참조.

실행부는 mcp_runner.py (본 venv) 다 — 이 파일은 얇은 껍데기라
.venv-mcp 에 새 의존성이 하나도 안 생긴다. 이유는 그 파일 머리말에.

전부 읽기 도구다 — .claude/settings.json allow 에 등록해 평상시 사용.
쓰기(일정 등록 등)는 음성 루프의 승인 게이트를 거치는 기존 경로 그대로.
"""

import subprocess

from mcp.server.mcpserver import MCPServer

server = MCPServer(
    name="dongbaek-core",
    instructions=(
        "동백 본체의 로컬 데이터 도구. 광고 성과(레일웨이 DB)·맥 캘린더·"
        "장기 기억·오늘 채점·지난 대화가 여기서 나온다. 숫자를 지어내지 말고 "
        "이 도구로 가져와 해석만 얹을 것."
    ),
)

VENV_PY = "~/dongbaek/.venv/bin/python"
RUNNER = "~/dongbaek/mcp_runner.py"
TIMEOUT = 40


def _run(tool: str, **args) -> str:
    import json as _json

    try:
        p = subprocess.run(
            [VENV_PY, RUNNER, tool, _json.dumps(args, ensure_ascii=False)],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: {tool} 응답 없음 ({TIMEOUT}초)"
    out = (p.stdout or "").strip()
    if p.returncode != 0:
        return out or f"ERROR: {tool} 실패: {(p.stderr or '').strip()[:200]}"
    return out


@server.tool(description="광고 성과를 레일웨이 DB에서 조회한다. 자연어 질문 그대로 (예: '오늘 광고플랫폼 성과', '이번달 상위 키워드').")
def ads(question: str) -> str:
    return _run("ads", question=question)


@server.tool(description="맥 캘린더 일정 조회. days=며칠치, keyword=제목 검색어(선택).")
def calendar(days: int = 7, keyword: str = "") -> str:
    return _run("calendar", days=days, keyword=keyword)


@server.tool(description="동백 장기 기억 검색 ('지난번 그 업체' 류). query=찾을 내용.")
def recall(query: str) -> str:
    return _run("recall", query=query)


@server.tool(description="동백이 오늘 말을 얼마나 알아들었는지 채점 요약. days=며칠치(기본 1).")
def score_today(days: int = 1) -> str:
    return _run("score_today", days=days)


@server.tool(description="지난 대화 기록 검색 (로컬·큐웬·클로드 전 층). query=검색어(비우면 최근 문답), days=범위.")
def history(query: str = "", days: int = 7) -> str:
    return _run("history", query=query, days=days)


@server.tool(description="동백 데몬 일지(daemon.log) 검색 — 호명·들림·무시·오류가 다 여기 있다. query=찾을 문구(정규식 가능), lines=끝 몇 줄.")
def logs(query: str = "", lines: int = 40) -> str:
    return _run("logs", query=query, lines=lines)


@server.tool(description="특정 발신·제목의 메일이 오면 30분 이내로 알려주는 감시를 등록한다 (예: '강남 한빛건설에서 메일 오면 알려줘'). keyword=찾을 이름, note=알림 문구(선택, 비우면 keyword 그대로).")
def mail_watch_add(keyword: str, note: str = "") -> str:
    return _run("mail_watch_add", keyword=keyword, note=note)


if __name__ == "__main__":
    server.run()
