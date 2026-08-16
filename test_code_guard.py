#!/usr/bin/env python3
"""음성 코드 수정 안전망 검증.

핵심: 수정한 것이 '반드시 되돌아가는가'. 이게 안 되면 음성 코딩은 위험하다.
    python test_code_guard.py
"""
import subprocess
import tempfile
from pathlib import Path

import code_guard
import dongbaek
import router

FAIL = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAIL.append(f"{label}: 기대={want!r} 실제={got!r}")
    print(f"  {'✓' if ok else '✗'} {label}")


def truthy(label, got):
    ok = bool(got)
    if not ok:
        FAIL.append(f"{label}: 거짓 (값={got!r})")
    print(f"  {'✓' if ok else '✗'} {label}")


print("\n[1] 코드 수정 명령이 위험 게이트에 걸리는가")
for q in [
    "myshop-site 헤더 색을 파랗게 바꿔줘",
    "그 파일에 주석 추가해줘",
    "config 에서 포트를 9000 으로 수정해",
    "테스트 코드 짜줘",
    "이 함수 리팩토링 해줘",
    "버그 고쳐줘",
]:
    truthy(f"{q!r} → 게이트", router.danger_hit(q))

print("\n[2] 조회는 여전히 통과 (과잉 차단 확인)")
for q in ["이 코드 리뷰해줘", "무슨 코드인지 설명해줘", "오늘 일정 뭐야"]:
    check(f"{q!r} → 통과", router.danger_hit(q), None)

print("\n[3] 되돌리기 의도 판별")
for q, want in [
    ("방금 수정 되돌려", True),
    ("원래대로 돌려놔", True),
    ("롤백해", True),
    ("일정 취소해", False),      # 일정 취소는 되돌리기가 아니다
    ("메일 취소해", False),
    ("오늘 일정 뭐야", False),
    ("왜 자꾸 되돌려??", False),  # 항의 질문 — 2026-08-13 롤백 오발
    ("왜 되돌렸어?", False),      # 과거 행동에 대한 질문
    ("아까 왜 그랬어? 그건 됐고 방금 거 되돌려줘", True),  # 왜가 앞 문장에 있어도 명령은 명령
]:
    check(f"{q!r} → 되돌리기={want}", dongbaek._is_undo(q), want)

print("\n[4] 대상 저장소 추측")
# ⚠ 이 검사는 원래 만든 사람 맥에서만 통과했다 — ~/projects 아래에 그 이름의
#   폴더가 실제로 있어야 하기 때문이다. 남의 맥에서는 늘 빨갛다.
#   그래서 폴더를 그 자리에서 만들어 놓고 본다. 시험은 기계를 타면 안 된다.
import config as _cfg  # noqa: E402

with tempfile.TemporaryDirectory() as _projects:
    (Path(_projects) / "myshop-site").mkdir()
    _real_root = _cfg.PROJECT_ROOT
    _cfg.PROJECT_ROOT = _projects
    try:
        t = dongbaek._guess_target("myshop-site 헤더 고쳐줘")
        truthy(f"'myshop-site' 인식 → {Path(t).name}", "myshop-site" in t)
        t2 = dongbaek._guess_target("그냥 이거 고쳐줘")
        truthy(f"못 찾으면 동백 폴더로 폴백 → {Path(t2).name}", Path(t2).exists())
    finally:
        _cfg.PROJECT_ROOT = _real_root

print("\n[5] ⚠ git 저장소가 아니면 수정을 막는가")
with tempfile.TemporaryDirectory() as d:
    ok, why, snap = code_guard.guard(d, "테스트")
    check("비-git 경로 → 거부", ok, False)
    truthy("이유를 설명함", "깃 저장소가 아니" in why)

print("\n[6] ⚠ 스냅샷 → 수정 → 되돌리기 왕복 (핵심)")
with tempfile.TemporaryDirectory() as d:
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    f = Path(d) / "app.py"
    f.write_text("PORT = 8000\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "init"], check=True)

    # 수정 전 상태 저장 (dirty 하게 만든 뒤)
    f.write_text("PORT = 8000\nDEBUG = True\n")
    snap = code_guard.snapshot(d, "포트 변경")
    truthy("스냅샷 생성됨", snap.get("stash"))

    # 동백이 코드를 고쳤다고 가정
    f.write_text("PORT = 9999\nDEBUG = True\nBROKEN = (\n")
    check("수정이 반영됨", "9999" in f.read_text(), True)

    summary = code_guard.diff_summary(d)
    truthy(f"변경 요약 생성 → {summary[:40]}", "app.py" in summary)

    msg = code_guard.restore(d)
    truthy(f"되돌리기 실행 → {msg[:30]}", "되돌렸" in msg)
    content = f.read_text()
    check("PORT 가 원복됨", "PORT = 8000" in content, True)
    check("깨진 코드가 사라짐", "BROKEN" in content, False)
    check("수정 전 작업내용은 보존", "DEBUG = True" in content, True)

print("\n[6b] ⚠ 스냅샷 시점에 '깨끗했을 때'도 되돌아가는가")
# 실제로 놓쳤던 경로. git stash 는 변경사항이 없으면 아무것도 안 만들어서,
# 깨끗한 상태에서 스냅샷 → 수정하면 되돌릴 stash 가 없다.
# 그때는 HEAD 로 되돌려야 한다.
with tempfile.TemporaryDirectory() as d:
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", d, "config", "user.name", "t"], check=True)
    f = Path(d) / "app.py"
    f.write_text("PORT = 8000\n")
    subprocess.run(["git", "-C", d, "add", "-A"], check=True)
    subprocess.run(["git", "-C", d, "commit", "-qm", "init"], check=True)

    snap = code_guard.snapshot(d, "깨끗한 상태")
    check("깨끗함을 기록", snap["dirty"], False)
    check("stash 는 안 만듦", snap["stash"], None)

    f.write_text("PORT = 9999\n")
    (Path(d) / "junk.py").write_text("생성된 쓰레기\n")

    msg = code_guard.restore(d)
    truthy(f"되돌리기 → {msg[:24]}", "되돌렸" in msg)
    check("원본 복구됨", f.read_text().strip(), "PORT = 8000")
    check("새로 만든 파일도 정리됨", (Path(d) / "junk.py").exists(), False)

print("\n[7] 되돌릴 게 없으면 안내만")
with tempfile.TemporaryDirectory() as d:
    subprocess.run(["git", "-C", d, "init", "-q"], check=True)
    msg = code_guard.restore(d)
    truthy(f"안내 메시지 → {msg[:30]}", "없습니다" in msg)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    for x in FAIL:
        print("  " + x)
    raise SystemExit(1)
print("✅ 전부 통과 — 음성 코드 수정은 항상 되돌릴 수 있음")
