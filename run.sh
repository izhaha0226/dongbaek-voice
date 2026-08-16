#!/bin/bash
# 동백 실행 래퍼.
#
# 처음에는 반드시 터미널에서 직접 실행할 것.
# macOS가 마이크 권한을 물어보는데, launchd 로 띄우면 그 창이 안 뜨고
# 조용히 무음만 녹음된다. 터미널에서 한 번 허용해 준 뒤에 자동 실행으로 넘길 것.
cd "$(dirname "$0")" || exit 1
export DONGBAEK_DAEMON=1   # Stop 훅이 같은 답을 두 번 읽지 않도록
exec .venv/bin/python dongbaek.py "$@"
