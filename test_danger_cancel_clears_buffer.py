#!/usr/bin/env python3
"""거절당한 위험 명령이 버퍼에 남지 않는가 — 2026-08-15 사고의 회귀 시험.

그날 밤 TV 를 켜둔 거실에서 "이거 비밀번호가 뭐더라" 가 버퍼에 남아,
뒤이어 들린 "유튜브 뭐야?"·"로그인을 바꾸자"·"그냥 조용히 있어?" 가 차례로
달라붙으며 네 번 되살아났다. 사장님은 네 번 거절하셨는데 네 번 다시 물었다.
위험 게이트는 매번 제 일을 했지만, 거절이 기억되지 않은 게 문제였다.

이 시험은 주 루프를 돌리지 않는다. 그 루프는 listener·오디오·화자인증을
전부 끌고 와야 해서 시험이 실제보다 무겁고 덜 정확해진다. 대신 소스에서
'거절 분기' 를 찾아 거기서 버퍼와 이어말 창이 확실히 닫히는지 본다 —
이 사고는 그 세 줄이 있느냐 없느냐로 갈렸다.

    python tests/test_danger_cancel_clears_buffer.py
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

FAIL = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))


src = (ROOT / "dongbaek.py").read_text(encoding="utf-8")
tree = ast.parse(src)


def _calls_confirm(node) -> bool:
    """이 조건식이 confirm_by_voice 거절을 판정하는가."""
    return any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "confirm_by_voice"
               for n in ast.walk(node))


branch = None
for node in ast.walk(tree):
    if isinstance(node, ast.If) and _calls_confirm(node.test):
        branch = node
        break

print("[1] 거절 분기를 찾는다")
check("confirm_by_voice 거절 분기 존재", branch is not None)
if branch is None:
    print("\n✗ 분기를 못 찾았다 — 시험이 낡았거나 루프가 바뀌었다")
    raise SystemExit(1)

# 분기 본문에서 대입문만 모은다 (targets 이 여럿인 튜플 대입 포함)
assigned = {}
for stmt in ast.walk(branch):
    if isinstance(stmt, ast.Assign):
        tgts, vals = stmt.targets[0], stmt.value
        pairs = (zip(tgts.elts, vals.elts)
                 if isinstance(tgts, ast.Tuple) and isinstance(vals, ast.Tuple)
                 else [(tgts, vals)])
        for t, v in pairs:
            if isinstance(t, ast.Name) and isinstance(v, ast.Constant):
                assigned[t.id] = v.value

print("\n[2] 거절하면 뒤에 아무것도 못 붙는다")
check("_last_command 를 비운다", assigned.get("_last_command"), "")
check("_last_command_at 을 0 으로", assigned.get("_last_command_at"), 0.0)
check("이어말 창(followup_until)을 닫는다", assigned.get("followup_until"), 0.0)

print("\n[3] 분기는 실행으로 새지 않고 빠져나간다")
check("continue 로 끝난다",
      any(isinstance(n, ast.Continue) for n in ast.walk(branch)))

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과")
