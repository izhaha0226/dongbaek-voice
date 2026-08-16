#!/usr/bin/env python3
"""로그 — 시각이 붙는가, 파일이 무한정 자라지 않는가.

회전은 조심스러운 물건이다. launchd 가 O_APPEND 로 연 fd 를 건드리면
로그가 통째로 사라진다. 그래서 '자르기만' 하고 fd 는 손대지 않는다.
사고 직후에 보는 일이 많으므로 최근 절반은 반드시 남겨야 한다.

    python test_dblog.py
"""
import io
import os
import re
import tempfile
from contextlib import redirect_stdout

import dblog

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


print("\n시각이 붙는가")
buf = io.StringIO()
with redirect_stdout(buf):
    dblog.log("확인")
line = buf.getvalue().strip()
check("MM-DD HH:MM:SS 형식이 앞에 붙는다",
      bool(re.match(r"^\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[동백\] 확인$", line)), True)

buf = io.StringIO()
with redirect_stdout(buf):
    dblog.log("확인", tag="텔레그램")
check("태그를 바꿀 수 있다", "[텔레그램] 확인" in buf.getvalue(), True)

print("\n회전")
d = tempfile.mkdtemp()
p = os.path.join(d, "t.log")

with open(p, "w") as f:
    f.write("작은 로그\n")
check("작으면 안 건드린다", dblog.rotate(p, max_bytes=1000), False)
check("내용 그대로", open(p).read(), "작은 로그\n")

# 줄 번호를 심어 두면 '어디까지 남았는지' 를 정확히 잴 수 있다
with open(p, "w") as f:
    for i in range(20000):
        f.write(f"line-{i:06d} 어쩌고저쩌고 로그 한 줄\n")
before = os.path.getsize(p)
check("크면 자른다", dblog.rotate(p, max_bytes=100_000), True)
after = os.path.getsize(p)
check("자른 뒤엔 비어 있다 (다음 쓰기가 0번지부터)", after, 0)
check("잘린 분량은 .1 로 남는다", os.path.exists(p + ".1"), True)

tail = open(p + ".1").read()
check("최근 줄이 남아 있다 (사고 직후에 보는 게 이 부분)",
      "line-019999" in tail, True)
check("오래된 줄은 버려졌다", "line-000000" in tail, False)
check("남긴 양이 상한의 절반 안쪽", len(tail.encode()) <= 100_000 // 2 + 200, True)
check("원래보다 확실히 작아졌다", len(tail.encode()) < before, True)
check("첫 줄이 잘리다 만 조각이 아니다", tail.startswith("line-"), True)

print("\n없는 파일·못 읽는 경로에서 안 터진다")
check("없는 파일", dblog.rotate(os.path.join(d, "없다.log")), False)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과")
