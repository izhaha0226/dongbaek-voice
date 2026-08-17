#!/usr/bin/env python3
"""텔레그램 봇 토큰을 안전하게 저장한다.

토큰은 화면에도 대화 기록에도 남지 않는다 (getpass 로 가려서 입력받음).
저장 후 실제로 텔레그램에 물어봐서 살아있는 토큰인지 확인한다.

  .venv/bin/python set_telegram_token.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import getpass
import json
import re
import sys
import urllib.request
from pathlib import Path

import config

CONF = config.STATE / "telegram.json"
DEFAULT_CHAT_ID = 123456789      # Your Name DM


def main() -> int:
    print("텔레그램 봇 토큰 설정")
    print("  BotFather 가 준 토큰을 붙여넣으세요. 화면에 표시되지 않습니다.")
    print()

    token = getpass.getpass("  토큰: ").strip()
    if not token:
        print("  취소했습니다.")
        return 1

    if not re.fullmatch(r"\d{6,}:[A-Za-z0-9_-]{30,}", token):
        print("  ✗ 토큰 형식이 아닙니다. '숫자:영문자' 형태여야 합니다.")
        return 2

    # 살아있는 토큰인지 확인
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getMe", timeout=20
        ) as r:
            info = json.load(r)
    except Exception as e:
        print(f"  ✗ 텔레그램에 연결하지 못했습니다: {type(e).__name__}")
        return 3

    if not info.get("ok"):
        print(f"  ✗ 토큰이 거부됐습니다: {info.get('description')}")
        return 4

    bot = info["result"]
    uname = bot.get("username")
    print(f"  ✓ 봇 확인: @{uname} ({bot.get('first_name')})")

    # 헤리 봇을 실수로 넣으면 헤리가 죽는다. 미리 막는다.
    if uname and "herry" in uname.lower():
        print("  ✗ 헤리 봇입니다. 헤리 게이트웨이가 이 봇을 폴링 중이라")
        print("    같이 쓰면 서로 메시지를 뺏어가 헤리가 죽습니다.")
        return 5

    existing = {}
    if CONF.exists():
        try:
            existing = json.loads(CONF.read_text())
        except ValueError:
            pass

    chat_ids = existing.get("allowed_chat_ids") or [DEFAULT_CHAT_ID]
    CONF.write_text(json.dumps(
        {"bot_token": token, "allowed_chat_ids": chat_ids},
        ensure_ascii=False, indent=2,
    ))
    CONF.chmod(0o600)

    print(f"  ✓ 저장 완료: {CONF}")
    print(f"    허용 chat_id: {chat_ids}")
    print()
    print("  이제 실행하세요:")
    print("    ~/dongbaek/.venv/bin/python ~/dongbaek/telegram_bridge.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
