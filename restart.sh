#!/bin/bash
# 동백 재시작 — 데몬과 텔레그램 브릿지를 '항상 같이' 갈아끼운다.
#
# 하나만 재시작하면 반드시 터진다. config 는 프로세스가 뜰 때 메모리에
# 올라가는데 dongbaek 은 명령이 올 때마다 디스크에서 새로 읽히기 때문이다.
# 그래서 재시작 안 한 쪽에서 '새 코드 + 옛 config' 조합이 만들어지고,
# 첫 명령에서 AttributeError 로 죽는다.
#
# 실제로 세 번 겪었다 — TOOL_POLICY, MCP_CONFIG, ECHO_TAIL.
# 매번 텔레그램이 밖에 계신 사장님 앞에서 죽었다.
# 사람이 기억할 일이 아니라 스크립트가 할 일이라 여기 묶어둔다.
set -e

UID_NUM=$(id -u)
LABELS=(com.dongbaek.dongbaek com.dongbaek.dongbaek-telegram)

# 문법이 깨진 채로 재시작하면 크래시 루프에 빠지고, 그러면 음성으로
# "되돌려" 라고 말할 상대조차 없어진다. 올리기 전에 확인한다.
cd "$(dirname "$0")"
if ! .venv/bin/python -c "
import pathlib, sys
bad = []
for p in sorted(pathlib.Path('.').glob('*.py')):
    try:
        compile(p.read_text(encoding='utf-8'), str(p), 'exec')
    except SyntaxError as e:
        bad.append(f'{p.name}:{e.lineno}')
if bad:
    print('문법 오류:', ', '.join(bad)); sys.exit(1)
"; then
  echo "✗ 문법 오류가 있어 재시작하지 않았습니다."
  exit 1
fi

for label in "${LABELS[@]}"; do
  launchctl kickstart -k "gui/${UID_NUM}/${label}"
  printf "  ↻ %s\n" "$label"
done

sleep 12
echo
for label in "${LABELS[@]}"; do
  pid=$(launchctl list | awk -v l="$label" '$3==l {print $1}')
  printf "  %-32s PID %s\n" "$label" "${pid:-없음}"
done
