#!/usr/bin/env python3
"""토큰 회계가 같은 돈을 여러 번 세지 않는지 고정한다.

CLI·SDK 가 주는 total_cost_usd 는 이어받은 **세션 전체의 누적**이다.
동백은 --resume 으로 붙으므로 한 세션에서 열 번 물으면 누적값 열 개가
기록에 남고, 그걸 더하면 같은 돈을 열 번 센다.

2026-08-15 실측: 08-14 하루가 $119.81 로 보고됐지만 실제는 $23.81 였다.
그 숫자로 "어제 얼마 썼어" 에 답하고, 그 숫자를 근거로 밤마다 무엇을 고칠지
정하고 있었다. 비용을 부풀려 말하는 것도 거짓말이다.

Claude 를 부르지 않는다 (usage 딕셔너리만 넣는다).
    python test_token_cost.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import json
import tempfile
from pathlib import Path

import bridge
import config

FAIL = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAIL.append(f"{label}: 기대={want!r} 실제={got!r}")
    print(f"  {'✓' if ok else '✗'} {label}")


def near(label, got, want, tol=1e-6):
    ok = abs(got - want) <= tol
    if not ok:
        FAIL.append(f"{label}: 기대={want!r} 실제={got!r}")
    print(f"  {'✓' if ok else '✗'} {label} ({got:.4f})")


def usage(inp=0, cw=0, cr=0, out=0):
    return {"input_tokens": inp, "cache_creation_input_tokens": cw,
            "cache_read_input_tokens": cr, "output_tokens": out}


# ─────────────────────────────────────────────────────────
print("\n1. 단가표가 CLI 실측과 맞는가")
# 이어받기가 없는(캐시읽기 0) 호출은 누적이 곧 그 턴이라 CLI 값이 정답이다.
# 아래 셋은 2026-08-14 tokens.jsonl 에서 그대로 가져온 실측 줄이다.
near("소넷 캐시쓰기 6,867 · 출력 243 → CLI 가 준 $0.0449",
     bridge.call_cost({"model": "claude-sonnet-5", "input": 2,
                       "cache_write": 6867, "cache_read": 0, "output": 243}),
     0.0449, tol=5e-5)
near("소넷 캐시쓰기 138,114 · 출력 27 → CLI 가 준 $0.8291",
     bridge.call_cost({"model": "claude-sonnet-5", "input": 2,
                       "cache_write": 138114, "cache_read": 0, "output": 27}),
     0.8291, tol=5e-5)
near("오퍼스 캐시쓰기 18,493 · 출력 803 → CLI 가 준 $0.2050",
     bridge.call_cost({"model": "claude-opus-5", "input": 2,
                       "cache_write": 18493, "cache_read": 0, "output": 803}),
     0.2050, tol=5e-5)

# 모르는 모델을 싸게 치면 비용이 실제보다 작게 보고된다 — 안심시키는 거짓말이
# 놀래키는 거짓말보다 나쁘다.
opus = bridge.call_cost({"model": "claude-opus-5", "cache_read": 1_000_000})
unknown = bridge.call_cost({"model": "무슨모델", "cache_read": 1_000_000})
check("모르는 모델은 비싼 쪽 단가로 친다", unknown, opus)

# ─────────────────────────────────────────────────────────
print("\n2. 세션 누적이 그 턴 비용으로 둔갑하지 않는가")
tmp = Path(tempfile.mkdtemp(prefix="dongbaek-cost-")) / "tokens.jsonl"
_real_log = config.TOKEN_LOG
try:
    config.TOKEN_LOG = tmp
    # 한 세션에서 세 번. 캐시읽기가 21만이라 턴마다 약 $0.065 씩 드는데,
    # CLI 는 그걸 누적해 1.20 → 1.27 → 1.33 으로 준다.
    rows = [bridge._log_tokens("물음", usage(2, 400, 217_000, 60), c,
                               False, "claude-sonnet-5", "normal")
            for c in (1.20, 1.27, 1.33)]
    each = [r["cost_usd"] for r in rows]
    near("한 턴 값은 누적이 아니라 그 턴 것", each[0], 0.0684, tol=5e-4)
    check("세 턴 모두 같은 값 (누적처럼 불어나지 않는다)",
          each[0] == each[1] == each[2], True)
    check("CLI 가 준 누적은 버리지 않고 옆에 남긴다",
          [r["session_cost_usd"] for r in rows], [1.20, 1.27, 1.33])
    near("세 턴 합계가 마지막 누적($1.33)만큼 부풀지 않는다",
         sum(each), 0.2052, tol=2e-3)
finally:
    config.TOKEN_LOG = _real_log

# ─────────────────────────────────────────────────────────
print("\n3. 오늘 요약이 옛 줄(누적이 박힌 줄)까지 바로잡는가")
tmp2 = Path(tempfile.mkdtemp(prefix="dongbaek-cost2-")) / "tokens.jsonl"
_real_tr = config.TRANSCRIPT_LOG
try:
    config.TOKEN_LOG = tmp2
    config.TRANSCRIPT_LOG = tmp2.parent / "없는파일.jsonl"
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).isoformat()
    with tmp2.open("w") as f:
        for cum in (1.20, 1.27, 1.33):
            f.write(json.dumps({
                "ts": today, "model": "claude-sonnet-5", "input": 2,
                "cache_write": 400, "cache_read": 217_000, "output": 60,
                "effective_input": 22_200,
                "cost_usd": cum,           # 고치기 전 형식 — 세션 누적이 박혀 있다
            }, ensure_ascii=False) + "\n")
    said = bridge.usage_summary()
    check("옛 형식 세 줄을 더해도 $1.33 이 아니라 $0.21 로 말한다",
          "0.21 달러" in said, True)
    check("$3.80(누적 세 개를 더한 값)은 나오지 않는다", "3.80" in said, False)
finally:
    config.TOKEN_LOG = _real_log
    config.TRANSCRIPT_LOG = _real_tr

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    for f in FAIL:
        print("  " + f)
    raise SystemExit(1)
print("✅ 전부 통과 — 비용은 세션 누적이 아니라 호출 한 번의 값으로 센다")
