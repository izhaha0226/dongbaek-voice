#!/usr/bin/env python3
"""워크트리에서도 테스트가 돌아야 한다 (2026-08-16 실측 사고).

사람이 코드를 만지는 밤이면 자가 정비는 별도 워크트리에서 일한다. 그런데
run_suite 가 `<저장소>/.venv/bin/python` 만 찾았다 — .venv 는 git 이 추적하지
않으니 새 체크아웃엔 없다. 그래서 워크트리 회차는 매번 여기서
FileNotFoundError 로 터졌고, 검증도 되돌리기도 보고도 못 하고 끝났다
(state/self_improve.err.log 의 13:30 역추적이 그것이다).

가상환경은 체크아웃이 아니라 기계에 딸린 것이라 본 트리 것을 빌리면 된다.
"""
import inspect
import subprocess
import sys as _sys
import tempfile
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import self_improve as si

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


print("[1] .venv 가 없는 저장소(=워크트리)에서도 터지지 않고 돈다")
with tempfile.TemporaryDirectory() as d:
    repo = _Path(d)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_pass.py").write_text("raise SystemExit(0)\n")
    try:
        passed, where = si.run_suite(repo)          # 예전엔 여기서 FileNotFoundError
    except FileNotFoundError as e:
        passed, where = None, f"터짐({e.filename})"
    check("통과", (passed, where), (True, ""))

print("\n[2] 실패는 그대로 실패로 잡는다 — 무르게 만든 게 아니다")
with tempfile.TemporaryDirectory() as d:
    repo = _Path(d)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_fail.py").write_text("raise SystemExit(1)\n")
    check("실패를 알린다", si.run_suite(repo), (False, "test_fail.py"))

print("\n[3] 본 트리에 .venv 가 있으면 그걸 쓴다 (워크트리 코드는 cwd 로 본다)")
_root_py = si.ROOT / ".venv" / "bin" / "python"
if _root_py.exists():
    with tempfile.TemporaryDirectory() as d:
        repo = _Path(d)
        (repo / "tests").mkdir()
        # 어느 파이썬으로 돌았는지 스스로 적어서 알려준다
        (repo / "tests" / "test_who.py").write_text(
            "import sys, pathlib\n"
            "pathlib.Path('who.txt').write_text(sys.executable)\n"
        )
        si.run_suite(repo)
        check("본 트리 가상환경", (repo / "who.txt").read_text(), str(_root_py))
else:
    print("  · 본 트리에 .venv 가 없어 건너뜀")

print("\n[4] 워크트리에 본 트리 환경을 이어 붙인다")
_src = inspect.getsource(si._workspace_open)
check("심링크를 건다", "symlink_to" in _src, True)

print("\n[5] 그 심링크는 git 이 무시해야 한다 — 안 그러면 커밋에 담긴다")
# '.venv/' 는 폴더만 걸리는 패턴이라 심링크는 '?? .venv' 로 튀어나온다.
# 마무리 커밋이 _changed_since 로 담으므로 저장소에 심링크가 들어간다.
with tempfile.TemporaryDirectory() as d:
    repo = _Path(d)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".gitignore").write_text((si.ROOT / ".gitignore").read_text())
    (repo / ".venv").symlink_to(repo / "없는곳")      # 심링크면 족하다
    st = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                        capture_output=True, text=True).stdout
    check("무시됨", [l for l in st.splitlines() if ".venv" in l], [])

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 워크트리에서도 자기 검증이 돈다")
