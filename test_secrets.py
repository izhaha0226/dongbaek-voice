#!/usr/bin/env python3
"""키체인 비밀 저장(secrets_local) — 넣고 꺼내고, 없으면 조용히 default.

⚠ 실사용 계정을 건드리지 않는다 — 시험 전용 이름을 쓰고 끝나면 지운다.
"""
import subprocess

import secrets_local

FAIL = []


def check(name, got, want=True):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


TEST_KEY = "test-roundtrip-임시"

print("[1] 넣고 꺼내기")
check("저장", secrets_local.put(TEST_KEY, "값-123 한글도"))
check("회수 일치", secrets_local.get(TEST_KEY), "값-123 한글도")
check("갱신(-U)", secrets_local.put(TEST_KEY, "둘째값"))
check("갱신 반영", secrets_local.get(TEST_KEY), "둘째값")

print("[2] 없는 이름은 default")
check("default 반환", secrets_local.get("없는-이름-9999", "기본"), "기본")

# 청소 — 시험 계정을 키체인에 남기지 않는다
subprocess.run(["security", "delete-generic-password", "-s", secrets_local.SERVICE,
                "-a", TEST_KEY], capture_output=True)
check("청소 확인", secrets_local.get(TEST_KEY, ""), "")

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 비밀은 키체인에 산다")
