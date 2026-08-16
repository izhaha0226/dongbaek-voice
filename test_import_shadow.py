#!/usr/bin/env python3
"""함수 안 import 가 전역 이름을 가려 데몬을 죽이는 일 — 다시 없게.

2026-08-12 22:43~22:47, 동백이 30초마다 죽고 다시 떴다. 사장님께는
"동백 준비되었습니다" 가 반복되는 걸로 보였다.

    UnboundLocalError: cannot access local variable 'call_notes'

모듈 맨 위에 import call_notes 를 두고, 함수 안에도 하나 더 넣었다.
파이썬은 함수 안에 import X 가 있으면 X 를 그 함수의 '지역변수' 로 잡는다.
그래서 import 줄보다 앞에서 쓰는 코드가 전부 터진다.

문법 검사(ast.parse)로는 안 잡히고, 테스트로도 안 잡혔다 — 그 분기를
실제로 타야만 드러나기 때문이다. 통화 중에만 지나가는 줄이었다.
그래서 '실행' 이 아니라 '구조' 로 검사한다.

    python test_import_shadow.py
"""
import ast
import pathlib

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


def shadowed(path: pathlib.Path) -> list[str]:
    """이 파일에서 '가려진 import' 를 찾는다."""
    out = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top = {a.asname or a.name.split(".")[0]
           for n in tree.body if isinstance(n, ast.Import) for a in n.names}
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        local = {}
        for n in ast.walk(fn):
            if isinstance(n, ast.Import):
                for a in n.names:
                    local.setdefault(a.asname or a.name.split(".")[0], n.lineno)
        for nm, line in local.items():
            if nm not in top:
                continue          # 전역에 없으면 가릴 것도 없다
            before = [n.lineno for n in ast.walk(fn)
                      if isinstance(n, ast.Name) and n.id == nm and n.lineno < line]
            if before:
                out.append(f"{path.name}:{fn.name} — '{nm}' 을 {min(before)}행에서 "
                           f"쓰는데 import 는 {line}행")
    return out


print("\n함수 안 import 가 전역 이름을 가리지 않는가")
# 2026-08-13 정리 후 구조: 모듈은 루트, 단독 유틸은 tools/ — 둘 다 본다.
files = sorted(p for p in
               list(pathlib.Path(".").glob("*.py")) + list(pathlib.Path("tools").glob("*.py"))
               if not p.name.startswith("test_"))
print(f"  · 검사 대상 {len(files)}개 파일")
found = []
for f in files:
    found += shadowed(f)
for msg in found:
    print(f"    ⚠ {msg}")
check("가려진 import 가 없다", found, [])

print("\n데몬이 실제로 뜨는가 (임포트만이라도)")
import subprocess
r = subprocess.run([".venv/bin/python", "-c", "import dongbaek"],
                   capture_output=True, text=True, timeout=120)
check("dongbaek 임포트 성공", r.returncode, 0)
if r.returncode:
    print("   ", (r.stderr or "")[-300:])

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 가려진 import 없음")
