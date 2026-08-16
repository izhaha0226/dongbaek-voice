#!/usr/bin/env python3
"""한 번짜리 호출(ask_once)이 사장님 전역 설정을 안 싣는지 고정한다.

빈 임시 디렉터리는 '프로젝트' CLAUDE.md 만 떼어낸다. 사용자 층의
CLAUDE.md·훅·스킬은 어디서 부르든 따라와서, 물음 하나에 5,300토큰이
얹혀 있었다 (실측 2026-08-15: 스무 자짜리 물음이 $0.0321 → 설정을 빼면
$0.0014).

돈보다 나쁜 건 내용이 섞이는 쪽이다. 08-15 17:53 통화 정리가 딸려온 그
지시를 보고 "첨부된 시스템 메시지(Skill 실행 지시…)는 무관해 보여
무시하고" 라는 머리말을 요약 첫 줄에 써서, 그게 소리로 나가고 텔레그램
제목과 위키 요약이 됐다. 진짜 요약은 그 아래 묻혀 있었다.

Claude 를 부르지 않는다 (subprocess.run 을 가로챈다).
    python test_ask_once_clean.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import json
import subprocess

import bridge
import config

FAIL = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAIL.append(f"{label}: 기대={want!r} 실제={got!r}")
    print(f"  {'✓' if ok else '✗'} {label}")


class _Proc:
    """subprocess.run 이 돌려주는 것 흉내."""

    def __init__(self, rc=0, out=None, err=""):
        self.returncode = rc
        self.stdout = json.dumps(out or {"result": "요약했다", "usage": {}})
        self.stderr = err


calls = []          # 이번 판에서 실제로 걸린 명령줄들


def val(argv, flag):
    """명령줄에서 옵션 뒤에 붙은 값. 없으면 None (변이가 깨끗이 실패하도록)."""
    return argv[argv.index(flag) + 1] if flag in argv else None


def nth(i):
    return calls[i] if i < len(calls) else []


def fake_run(argv, **kw):
    calls.append(list(argv))
    return _handler(argv)


_handler = lambda argv: _Proc()          # noqa: E731 — 절마다 갈아 끼운다
_real_run = subprocess.run
_real_log = bridge._log_tokens
subprocess.run = fake_run
bridge._log_tokens = lambda *a, **k: None

try:
    print("\n[1] 전역 설정을 빼고 부른다")
    calls.clear()
    got = bridge.ask_once("아무 말", model=config.MODEL_CHAT)
    argv = calls[0]
    check("답은 그대로 돌아온다", got, "요약했다")
    check("--setting-sources 를 붙인다", "--setting-sources" in argv, True)
    check("값은 빈 문자열 — 사용자·프로젝트·로컬 어느 것도 안 싣는다",
          val(argv, "--setting-sources"), "")
    check("MCP·도구를 끄던 것은 그대로다",
          "--strict-mcp-config" in argv and "--tools" in argv, True)
    check("한 번만 부른다", len(calls), 1)

    print("\n[2] 옵션을 모르는 CLI 로 올라가도 정리가 끊기지 않는다")
    calls.clear()
    _handler = lambda argv: (                                    # noqa: E731
        _Proc(rc=1, err="error: unknown option '--setting-sources'")
        if "--setting-sources" in argv else _Proc())
    got = bridge.ask_once("아무 말", model=config.MODEL_CHAT)
    check("답이 돌아온다", got, "요약했다")
    check("두 번 부른다 (거절당한 뒤 한 번 더)", len(calls), 2)
    check("두 번째에는 그 옵션이 없다", "--setting-sources" in nth(1), False)
    check("빈 값도 함께 뺀다 — 짝으로 지워야 --system-prompt 가 안 밀린다",
          val(nth(1), "--tools"), "")

    print("\n[3] 다른 이유로 실패하면 다시 걸지 않는다 (돈이 두 배로 나간다)")
    calls.clear()
    _handler = lambda argv: _Proc(rc=1, err="Error: overloaded")   # noqa: E731
    bridge.ask_once("아무 말", model=config.MODEL_CHAT)
    check("한 번만 부른다", len(calls), 1)
finally:
    subprocess.run = _real_run
    bridge._log_tokens = _real_log

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    for f in FAIL:
        print("  " + f)
    raise SystemExit(1)
print("✅ 전부 통과 — 한 번짜리 호출에 전역 설정·훅이 딸려오지 않는다")
