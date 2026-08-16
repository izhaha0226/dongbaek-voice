#!/usr/bin/env python3
"""두 브릿지가 같은 정책을 따르는지 고정한다.

동백은 Claude 를 두 경로로 부를 수 있다 (config.BRIDGE):

    "cli" — bridge.py      `claude --print` 를 띄우고 CLI 인자로 정책을 준다
    "sdk" — bridge_sdk.py  claude_agent_sdk 에 ClaudeAgentOptions 로 준다

경로가 갈리면 정책도 갈릴 수 있다. 그게 위험한 이유는 하나다 —
조회 등급에 셸이 안 실리는 것이 '도구로 그은 경계' 인데, 한쪽 브릿지에서만
그 경계가 서면 어느 쪽으로 부르느냐에 따라 배포가 가능해진다.

그래서 여기서는 양쪽을 서로 비교하지 않고, **둘 다 config.TOOL_POLICY 와
맞는지** 를 따로 확인한다. 표가 유일한 진실이어야 한다.

Claude 를 실제로 부르지 않는다 (subprocess 를 가로채고 옵션 객체만 만든다).
    python test_bridge_parity.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import dataclasses as dc
import json
import subprocess
import tempfile
from pathlib import Path

import config

FAIL = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAIL.append(f"{label}: 기대={want!r} 실제={got!r}")
    print(f"  {'✓' if ok else '✗'} {label}")


# MCP 설정 파일은 setup.sh 가 만든다 (저장소에는 없다).
# 두 브릿지가 똑같이 '있을 때' 를 다루는지 봐야 하므로 시험용을 만들어 쓴다.
_mcp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
json.dump({"mcpServers": {}}, _mcp)
_mcp.close()
config.MCP_CONFIG = Path(_mcp.name)

import bridge          # noqa: E402  (config.MCP_CONFIG 를 바꾼 뒤에 읽혀야 한다)
import bridge_sdk      # noqa: E402

# ─────────────────────────────────────────────────────────
# CLI 인자 가로채기 — 실제로 claude 를 띄우지 않는다
# ─────────────────────────────────────────────────────────
ARGV: list[list[str]] = []
_real_run = subprocess.run


def _capture(cmd, *a, **kw):
    ARGV.append(list(cmd))
    raise RuntimeError("가로챔")


def cli_argv(**kw) -> list[str]:
    ARGV.clear()
    subprocess.run = _capture
    try:
        bridge._ask_cli(".", **kw)
    except Exception:
        pass
    finally:
        subprocess.run = _real_run
    return ARGV[0] if ARGV else []


def between(argv, flag):
    """flag 뒤에 이어지는 값들(다음 --옵션 전까지)."""
    if flag not in argv:
        return []
    i = argv.index(flag) + 1
    out = []
    while i < len(argv) and not argv[i].startswith("--"):
        out.append(argv[i])
        i += 1
    return out


def every(argv, flag):
    """같은 flag 가 여러 번 나오는 경우 (--add-dir)."""
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag and i + 1 < len(argv)]


def sdk_opts(tier, sid=None):
    policy = config.TOOL_POLICY[tier]
    o = bridge_sdk._options(policy, policy.get("model"), sid, stream=False)
    return {f.name: getattr(o, f.name) for f in dc.fields(o)}


TIERS = [("normal", {}), ("dev", {"dev": True}), ("elevated", {"elevated": True})]

for tier, kw in TIERS:
    policy = config.TOOL_POLICY[tier]
    argv = cli_argv(**kw)
    opt = sdk_opts(tier)

    want_tools = list(policy.get("tools") or config.CLAUDE_TOOLS)
    want_disallowed = list(policy.get("disallowed") or [])

    print(f"\n[{tier}] 모델")
    check("CLI --model", between(argv, "--model"), [policy["model"]])
    check("SDK model", opt["model"], policy["model"])

    print(f"[{tier}] 싣는 도구 — 매 호출의 고정 비용을 가르는 목록")
    check("CLI --tools", between(argv, "--tools"), want_tools)
    check("SDK tools", opt["tools"], want_tools)

    print(f"[{tier}] 차단 도구")
    check("CLI --disallowed-tools", between(argv, "--disallowed-tools"), want_disallowed)
    check("SDK disallowed_tools", opt["disallowed_tools"], want_disallowed)

    print(f"[{tier}] 권한 모드 — 없으면 헤드리스에서 조용히 거부된다")
    check("CLI --permission-mode", between(argv, "--permission-mode"),
          [policy["permission_mode"]])
    check("SDK permission_mode", opt["permission_mode"], policy["permission_mode"])

print("\n[공통] 접근 범위·MCP")
argv = cli_argv()
opt = sdk_opts("normal")
check("CLI --add-dir", every(argv, "--add-dir"), list(config.CLAUDE_EXTRA_DIRS))
check("SDK add_dirs", opt["add_dirs"], list(config.CLAUDE_EXTRA_DIRS))
check("CLI --strict-mcp-config", "--strict-mcp-config" in argv, True)
check("SDK strict_mcp_config", opt["strict_mcp_config"], True)
check("SDK mcp_servers", opt["mcp_servers"], str(config.MCP_CONFIG))

print("\n[SDK] 회귀 — CLI 와 기본값이 다른 지점")
# SDK 의 system_prompt 기본값은 None 이다. 명시하지 않으면 CLAUDE.md 의
# '3문장 이내·마크다운 금지' 가 통째로 빠지고 TTS 가 마크다운을 읽는다.
check("claude_code 프리셋을 명시한다",
      opt["system_prompt"], {"type": "preset", "preset": "claude_code"})
# setting_sources 는 None 이어야 CLI 와 같다 (None = 전부, project 포함 →
# CLAUDE.md 로드). []  로 두면 CLAUDE.md 가 안 실린다.
check("setting_sources 를 건드리지 않는다 (None = CLI 와 동일)",
      opt["setting_sources"], None)
# tools 와 allowed_tools 는 다른 것이다. 도구 목록을 allowed_tools 에 넣으면
# '싣는 도구' 가 안 좁혀져서 기본 문맥이 35,767 로 돌아간다.
check("도구 목록을 allowed_tools 에 넣지 않는다", opt["allowed_tools"], [])

print("\n[공통] 세션 재개 — 세션 재사용이 최대 절약 레버다")
# state/session.json 을 읽게 두면 앞선 실행이 남긴 세션에 결과가 흔들린다.
# 어떤 상태를 만나든 두 브릿지가 똑같이 굴어야 하므로 여기서 직접 심는다.
_real_load = bridge._load_session
try:
    bridge._load_session = lambda model: None
    check("세션이 없으면 CLI 에 --resume 없음", "--resume" in cli_argv(), False)
    check("세션이 없으면 SDK resume=None", sdk_opts("normal")["resume"], None)

    bridge._load_session = lambda model: "sess-abc"
    check("세션이 있으면 CLI --resume 에 실린다",
          between(cli_argv(), "--resume"), ["sess-abc"])
    check("세션이 있으면 SDK resume 에 실린다",
          sdk_opts("normal", sid=bridge._load_session(""))["resume"], "sess-abc")
finally:
    bridge._load_session = _real_load

Path(_mcp.name).unlink(missing_ok=True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    for f in FAIL:
        print("  " + f)
    raise SystemExit(1)
print("✅ 전부 통과 — 두 브릿지가 같은 정책 표를 따른다")
