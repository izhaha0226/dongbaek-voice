#!/bin/bash
# 동백 설치 — 이 저장소를 지금 이 맥에 맞춰 놓는다.
#
# 저장소에는 절대경로가 들어 있지 않다. launchd 와 MCP 설정은 절대경로를
# 요구하는데, 그걸 커밋하면 남의 맥에서 전부 깨지기 때문이다.
# 그래서 templates/ 에 자리표시자로 넣어두고 여기서 채운다.
#
# 두 번 이상 돌려도 안전하다. 이미 있는 파일은 건드리지 않는다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "▶ 동백 설치 — $ROOT"
echo

# ── 1. 파이썬 환경 ────────────────────────────────────────
# uv 가 있으면 uv 로. pip 보다 훨씬 빠르고, 이 프로젝트는 torch 를 받는다.
if [ ! -d .venv ]; then
  echo "  ① 파이썬 환경 만드는 중 (torch 를 받아 5분쯤 걸립니다)"
  if command -v uv >/dev/null 2>&1; then
    uv venv .venv
    uv pip install --python .venv/bin/python -r requirements.txt
  else
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
  fi
else
  echo "  ① .venv 이미 있음 — 건너뜀"
fi

# 메일 MCP 전용 환경. claude-agent-sdk(mcp<2 강제)와 mail_mcp(mcp 2.x 전용)는
# 한 venv 에 공존할 수 없다 — 서버는 별도 프로세스라 인터프리터만 갈라탄다.
if [ ! -d .venv-mcp ]; then
  echo "  ①-2 메일 MCP 환경(.venv-mcp) 만드는 중"
  if command -v uv >/dev/null 2>&1; then
    uv venv .venv-mcp
    uv pip install --python .venv-mcp/bin/python -r requirements-mcp.txt
  else
    python3 -m venv .venv-mcp
    .venv-mcp/bin/pip install --upgrade pip
    .venv-mcp/bin/pip install -r requirements-mcp.txt
  fi
else
  echo "  ①-2 .venv-mcp 이미 있음 — 건너뜀"
fi

# ── 2. 개인 설정 ──────────────────────────────────────────
if [ ! -f config_local.py ]; then
  cp config_local.example.py config_local.py
  echo "  ② config_local.py 생성 — 열어서 자기 것으로 채우세요"
else
  echo "  ② config_local.py 이미 있음 — 건너뜀"
fi

# ── 3. 절대경로 채우기 ────────────────────────────────────
# __ROOT__ → 이 폴더, __HOME__ → 홈 디렉토리
render() {
  sed -e "s|__ROOT__|${ROOT}|g" -e "s|__HOME__|${HOME}|g" "$1" > "$2"
}

render templates/mcp.json           .mcp.json
render templates/mcp-dongbaek.json  mcp-dongbaek.json

mkdir -p launchd
for tpl in templates/*.plist; do
  render "$tpl" "launchd/$(basename "$tpl")"
done
echo "  ③ 경로 채운 설정 생성 — .mcp.json · mcp-dongbaek.json · launchd/*.plist"

mkdir -p state
echo
echo "✓ 설치 끝."
echo
echo "다음 순서로 하세요 — 순서가 중요합니다."
echo
echo "  1) 마이크 권한. 반드시 터미널에서 먼저 한 번 돌릴 것:"
echo "       ./run.sh"
echo "     launchd 는 권한 요청 창을 띄우지 못합니다. 여기서 허용하지 않고"
echo "     자동 실행으로 넘기면 무음만 녹음됩니다."
echo
echo "  2) 목소리 등록. 안 하면 TV·옆사람 말도 명령으로 실행됩니다:"
echo "       .venv/bin/python dongbaek.py --enroll"
echo
echo "  3) 상시 실행 (선택):"
echo "       cp launchd/dongbaek.plist ~/Library/LaunchAgents/"
echo "       launchctl load ~/Library/LaunchAgents/dongbaek.plist"
