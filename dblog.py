#!/usr/bin/env python3
"""로그 한 줄 — 시각을 붙이고, 파일이 무한정 자라지 않게 한다.

왜 만들었나. 첫째, 로그에 시각이 없었다. "자가개선 건너뜀이 왜 저녁에
일곱 번 왔지" 같은 질문에 답하려면 매번 다른 기록과 대조해야 했다.
한 줄 앞에 시각만 붙으면 끝나는 일이었다.

둘째, 같은 log() 가 세 군데(dongbaek·speak·telegram_bridge)에 복사돼
있었다. speak 는 dongbaek 을 import 할 수 없어서(순환) 각자 짰던 것이다.
의존이 없는 이 모듈로 빼면 셋 다 쓸 수 있다.

⚠ 회전은 launchd 가 연 fd 를 건드리지 않는다. StandardOutPath 는 O_APPEND
  로 열려 있어서, 파일을 잘라도 다음 쓰기는 0번지부터 이어진다. 잘린 내용은
  .1 로 남긴다. 열려 있는 fd 를 닫거나 바꾸려 들면 로그가 통째로 사라진다.
"""

from __future__ import annotations

import os
import sys
import time

# 이 크기를 넘으면 뒤쪽 절반만 남기고 잘라낸다.
MAX_BYTES = 8 * 1024 * 1024


def log(msg: str, tag: str = "동백") -> None:
    print(f"{time.strftime('%m-%d %H:%M:%S')} [{tag}] {msg}", flush=True)


def rotate(path: str | os.PathLike, max_bytes: int = MAX_BYTES) -> bool:
    """로그가 너무 크면 최근 절반만 남긴다. 잘랐으면 True.

    통째로 지우지 않는 이유: 사고가 난 직후에 로그를 보는 일이 많은데,
    그때 방금 것까지 날아가 있으면 원인을 못 찾는다.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size <= max_bytes:
        return False
    try:
        keep = max_bytes // 2
        with open(path, "rb") as f:
            f.seek(size - keep)
            f.readline()                 # 잘린 첫 줄은 버린다
            tail = f.read()
        with open(str(path) + ".1", "wb") as f:
            f.write(tail)
        # 자르기만 한다. O_APPEND 로 열린 fd 는 다음 쓰기를 0번지부터 이어간다.
        with open(path, "r+b") as f:
            f.truncate(0)
        return True
    except OSError as e:
        print(f"[동백] 로그 회전 실패: {e}", file=sys.stderr, flush=True)
        return False
