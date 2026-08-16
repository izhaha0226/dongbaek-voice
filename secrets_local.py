#!/usr/bin/env python3
"""비밀 값(API 키·DB 접속 문자열)은 macOS 키체인에 산다.

왜 키체인인가 (PLAN-unify 3단계, 2026-08-13 승인):
  DB 에 '암호화 저장' 하면 복호화 키를 동백이 들어야 한다 — 비밀이
  자리만 옮긴다. 키체인은 이 맥에 하드웨어로 묶여 있고, 로그인 세션에서만
  풀리며, security CLI 로 프로세스가 조용히 읽을 수 있다. 목적(동백이
  키로 레일웨이·검색에 접속해 결과를 가져옴)에는 이걸로 충분하고
  노출면이 가장 작다. 가져온 '결과' 는 dbstore.results 에 쌓인다.

계정 체계: service="dongbaek", account=<이름>. 예:
  .venv/bin/python secrets_local.py put railway-db     # 값은 화면 없이 입력
  .venv/bin/python secrets_local.py get railway-db

읽기 우선순위는 '키체인 → 기존 파일 폴백' — 이관 중에도 아무것도 안 깨진다.
⚠ my-ads 의 .env 는 그 프로젝트 소유라 지우지 않는다 (동백은
  키체인을 먼저 볼 뿐이다).
"""

from __future__ import annotations

import re
import subprocess

SERVICE = "dongbaek"


def get(name: str, default: str = "") -> str:
    """키체인에서 읽는다. 없으면 default — 예외를 밖으로 내지 않는다."""
    try:
        p = subprocess.run(
            ["security", "find-generic-password", "-s", SERVICE,
             "-a", name, "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return default
    if p.returncode != 0:
        return default
    out = (p.stdout or "").strip()
    # ⚠ security -w 는 비ASCII 값을 16진수로 찍는다 (test_secrets 가 잡은
    #   실측 — "값-123" 이 "eab0922d..." 로 돌아왔다). 되돌리되, 복원 결과에
    #   비ASCII 가 있을 때만 쓴다 — "cafe1234" 같은 진짜 16진수 꼴 비밀번호를
    #   잘못 복원하지 않기 위해서다 (ASCII 뿐이면 애초에 hex 로 안 찍는다).
    if out and len(out) % 2 == 0 and re.fullmatch(r"[0-9a-f]+", out):
        try:
            decoded = bytes.fromhex(out).decode("utf-8")
            if any(ord(ch) > 127 for ch in decoded):
                out = decoded
        except (ValueError, UnicodeDecodeError):
            pass
    return out or default


def put(name: str, value: str) -> bool:
    """키체인에 저장한다 (-U: 있으면 갱신)."""
    try:
        p = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", SERVICE,
             "-a", name, "-w", value],
            capture_output=True, text=True, timeout=5,
        )
        return p.returncode == 0
    except Exception:
        return False


if __name__ == "__main__":
    import getpass
    import sys

    if len(sys.argv) >= 3 and sys.argv[1] == "get":
        v = get(sys.argv[2])
        print(v if v else f"(키체인에 '{sys.argv[2]}' 없음)")
    elif len(sys.argv) >= 3 and sys.argv[1] == "put":
        val = getpass.getpass(f"  {sys.argv[2]} 값 (화면에 안 보임): ").strip()
        if not val:
            print("빈 값 — 취소")
            raise SystemExit(1)
        print("저장됨" if put(sys.argv[2], val) else "저장 실패")
    else:
        print("사용법: secrets_local.py get|put <이름>")
        raise SystemExit(2)
