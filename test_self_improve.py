#!/usr/bin/env python3
"""야간 자가 정비 검증 — '자율이 감사 위에 있는가'.

실제 클로드·본 저장소 없이, 임시 git 저장소에서 롤백·diff 상한·증거
수집만 본다. 위험한 부분(reset --hard, clean -fd)이 겨눠야 할 곳만
겨누는지가 핵심이다.
    python test_self_improve.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import subprocess
import sys
import tempfile
from pathlib import Path

import self_improve as si

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


def sh(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                   text=True, check=True)


# 임시 git 저장소
repo = Path(tempfile.mkdtemp())
sh(repo, "init", "-q")
sh(repo, "config", "user.email", "t@t")
sh(repo, "config", "user.name", "t")
(repo / "a.py").write_text("x = 1\n")
sh(repo, "add", "-A")
sh(repo, "commit", "-qm", "시작")
base = si._git(repo, "rev-parse", "HEAD")

print("\n[1] 롤백 — 미커밋 수정은 원상복구, 새 파일은 삭제 대신 격리")
# ⚠ 예전엔 '커밋까지 되감기' 를 검사했다. 그 계약은 폐기됐다 — 러너는
#   검증 통과 전에 커밋하지 않으므로, base 이후의 커밋은 전부 남의 것이고
#   절대 되감으면 안 된다 (2026-08-13 사고 ×2, [1b] 참조).
(repo / "a.py").write_text("x = 2\n")              # 러너의 미커밋 수정
(repo / "새파일.py").write_text("y = 3\n")
si._rollback(repo, base)
check("HEAD 그대로", si._git(repo, "rev-parse", "HEAD"), base)
check("수정 복구", (repo / "a.py").read_text(), "x = 1\n")
# ⚠ 예전엔 git clean -fd 로 지웠다 — 러너가 도는 사이 사람이 만든 파일까지
#   삭제됐다 (2026-08-13 dbstore.py 실사례). 이제 격리함으로 옮긴다:
#   원위치에선 사라지되(트리 깨끗) 내용은 복구 가능해야 한다.
check("새 파일이 원위치에 없음", (repo / "새파일.py").exists(), False)
qroot = repo / ".git" / "improve-quarantine"
saved = list(qroot.rglob("새파일.py")) if qroot.exists() else []
check("격리함에 보존됨", len(saved) == 1 and saved[0].read_text() == "y = 3\n", True)
check("트리 깨끗", si._tree_clean(repo), True)

print("\n[1b] ⚠ 러너 도중 남이 커밋하면 그 커밋은 절대 되감지 않는다")
# 2026-08-13 실사고 ×2: 사람이 커밋한 미팅 모드 443줄을 자기 diff 로 세어
# 상한 초과 롤백 → 남의 커밋 두 개가 로컬에서 지워졌다 (원격 덕에 복구).
base2 = si._git(repo, "rev-parse", "HEAD")
(repo / "사람작업.py").write_text("human = 1\n")
sh(repo, "add", "-A"); sh(repo, "commit", "-qm", "사람 커밋 (러너 도중)")
human_head = si._git(repo, "rev-parse", "HEAD")
(repo / "a.py").write_text("x = 99\n")            # 러너 자신의 미커밋 작업
check("남 커밋은 diff 에 안 센다", si._changed_lines(repo, base2) <= 2, True)
si._rollback(repo, base2)
check("남 커밋 생존", si._git(repo, "rev-parse", "HEAD"), human_head)
check("사람 파일 생존", (repo / "사람작업.py").exists(), True)
check("러너 미커밋 작업만 되돌림", (repo / "a.py").read_text(), "x = 1\n")

print("\n[2] diff 줄 수 — 미커밋(러너 자신의 것)만 센다")
(repo / "a.py").write_text("x = 1\ny = 2\nz = 3\n")
n = si._changed_lines(repo, base)
check("미커밋 변경 집계", n >= 2, True)
# 커밋되는 순간 러너의 것이 아니다 (러너는 검증 전에 커밋 안 함) — 0.
sh(repo, "commit", "-aqm", "커밋")
check("커밋된 것은 안 센다", si._changed_lines(repo, base), 0)
si._rollback(repo, base)

print("\n[3] 백로그 — 미완 항목만, 설명 줄은 붙여서")
md = repo / "IMPROVE.md"
md.write_text(
    "# 백로그\n"
    "- [ ] 첫 항목\n"
    "      설명이 이어진다\n"
    "- [x] 끝난 항목\n"
    "- [ ] 둘째 항목\n"
)
items = si._backlog_items(md)
check("미완 2개", len(items), 2)
check("설명 병합", "설명이 이어진다" in items[0], True)
check("끝난 건 제외", any("끝난" in i for i in items), False)

print("\n[4] 오류 꼬리 — 첫 실행은 기준점만 잡고, 이후 새 줄만, 소음 제외")
log = repo / "x.err.log"
log.write_text("해묵은 옛 오류\n")
stamps = {}
first = si._tail_new(log, stamps)
check("첫 실행은 과거를 안 판다", first, [])
with log.open("a") as f:
    f.write("진짜 새 오류\nFetching 4 files: 진행바\n")
second = si._tail_new(log, stamps)
check("새 줄만, 소음 걸러냄", second, ["진짜 새 오류"])
third = si._tail_new(log, stamps)
check("같은 내용 재보고 안 함", third, [])

print("\n[4b] 관찰 — 아이디어 모드의 재료가 실데이터에서 나온다")
import json as _json
from datetime import datetime as _dt

# 정본이 DB 로 옮겨졌다(2026-08-16). 관찰 재료도 DB 에서 나오므로 임시
# DB 를 갈아 끼운다 — 사장님 진짜 기록을 읽거나 더럽히면 안 된다.
import dbstore  # noqa: E402

now = _dt.now().isoformat(timespec="seconds")
rows = ([{"ts": now, "route": "claude", "command": "레일웨이 상태 알려줘"}] * 3
        + [{"ts": now, "route": "local", "command": "지금 몇 시야"}] * 2
        + [{"ts": "2000-01-01T00:00:00", "route": "claude", "command": "옛날 명령"}])
_real_db = dbstore.DB_PATH
dbstore.DB_PATH = repo / "테스트.db"
for _r in rows:
    dbstore.save(_r)
obs = si._observations()
dbstore.DB_PATH = _real_db
check("분포 관찰", any("claude 3건" in o for o in obs), True)
check("반복 명령 후보", any("레일웨이 상태 알려줘" in o for o in obs), True)
check("옛 기록 제외", any("옛날" in o for o in obs), False)

print("\n[5] 고칠 증거도 관찰도 없으면 클로드를 부르지 않는다")
# ⚠ 증거만 비우면 안 된다. 증거가 없을 때 '관찰 기반 아이디어 모드' 로
#   넘어가 클로드를 부르는 길이 나중에 생겼기 때문이다. 둘 다 비워야
#   "오늘은 쉰다" 경로를 검사한다.
#   (그 전까지 이 검사가 통과하던 건 작업 트리가 더러워 main() 이 그보다
#    앞의 '사람 작업을 밟지 않는다' 가드에서 되돌아왔기 때문이다 — 개발
#    중엔 늘 초록이고 커밋 직후에만 빨개지는, 가장 헷갈리는 위양성이었다.)
called = {"n": 0}
si.run_claude = lambda prompt: (called.update(n=called["n"] + 1), (True, ""))[1]
_real_collect = si.collect_evidence
si.collect_evidence = lambda stamps: []
_real_obs = si._observations
si._observations = lambda: []
_real_save = si._save_stamps
si._save_stamps = lambda stamps: None
rc = si.main()
si.collect_evidence = _real_collect
si._observations = _real_obs
si._save_stamps = _real_save
check("종료 코드 0", rc, 0)
check("클로드 호출 0회", called["n"], 0)

print("\n[6] 러너가 사람 작업을 자기 커밋에 담지 않는다")
# ⚠ `git add -A` 였다. 사람이 편집 중이던 미커밋 파일까지 통째로 담아,
#   커밋 메시지와 내용물이 어긋난 커밋이 됐다 — 2026-08-14 하루에 다섯 번,
#   한 번은 편집 도중에 걸려 반쪽 상태로 커밋됐다.
import inspect
_src = inspect.getsource(si)
check("add -A 를 쓰지 않는다", '"add", "-A"' in _src, False)
check("바뀐 파일만 담는다", "_changed_since" in inspect.getsource(si.main), True)
# 파일명 파싱 — _git 이 출력을 strip 해서 첫 줄 앞 공백이 사라진다.
# 고정 위치로 자르면 'config.py' 가 'onfig.py' 가 된다 (실제로 그랬다).
import re as _re
for raw, want in [("M config.py", "config.py"), ("?? new.py", "new.py"),
                  ("R  a.py -> b.py", "b.py")]:
    m = _re.match(r"^\s*\S{1,2}\s+(.*)$", raw)
    got = m.group(1).strip().strip('"') if m else None
    if got and " -> " in got:
        got = got.split(" -> ", 1)[1]
    check(f"파일명 파싱: {raw!r}", got, want)

print("\n[7] 검증 도구(테스트)는 스스로 못 고친다")
# 검증이 테스트뿐인데 그 테스트를 자기가 고치면 검증이 무의미해진다.
# 실제로 자가개선 10건 중 2건이 테스트 수정이었고 둘 다 "시계를 믿던 것" 이었다.
check("tests/ 변경을 막는 자리가 있다", "touched_tests" in inspect.getsource(si.main), True)

print("\n[8] 시도한 것을 기억한다 — 롤백이 지식이 되게")
# ⚠ 이게 없어서 일곱 번 롤백하고 일곱 번 다 잊었다. 어젯밤 깨진 방법을
#   오늘 밤 또 시도한다. 시행착오는 있는데 착오가 쌓이지 않았다.
check("_history_add 있음", callable(getattr(si, "_history_add", None)), True)
check("_history_lines 있음", callable(getattr(si, "_history_lines", None)), True)
check("FIX 프롬프트가 지난 시도를 싣는다", "{history}" in si._PROMPT_FIX, True)
check("IDEA 프롬프트도 싣는다", "{history}" in si._PROMPT_IDEA, True)
# ⚠ 테스트 프로세스는 이력을 더럽히지 않는다 (_report·_write_note 와 같은 가드)
_before = si.HISTORY_FILE.exists() and si.HISTORY_FILE.stat().st_size or 0
si._history_add("fix", "테스트가남긴것", ["x.py"])
_after = si.HISTORY_FILE.exists() and si.HISTORY_FILE.stat().st_size or 0
check("테스트가 이력을 안 남긴다", _after, _before)

print("\n[9] 트리가 더러우면 물러나지 않고 자기 자리를 만든다")
# 사장님 지시 (2026-08-15): "트리가 지저분하다고 자가정비가 작동을 안 한다.
# 스스로 신규 트리를 만들어서 하든 트리를 정리하고 하든 스스로 결정하게 해줘."
# 예전엔 그냥 물러났고, 사람이 코드를 만지는 날은 통째로 쉬었다 (37회 중 8회).
#
# ⚠ 사람 작업은 건드리지 않는다. 스태시도 커밋도 하지 않는다 — 남의 작업에
#   손대는 순간 '정비' 가 아니라 사고다. 자기 자리를 새로 만들 뿐이다.
_wsrc = inspect.getsource(si)
check("스태시로 남의 작업을 치우지 않는다", "stash" in _wsrc, False)
check("워크트리를 만든다", "worktree" in _wsrc, True)
check("_workspace_open 있음", callable(getattr(si, "_workspace_open", None)), True)
check("_workspace_land 있음", callable(getattr(si, "_workspace_land", None)), True)
# 깨끗하면 제자리에서 — 굳이 워크트리를 파지 않는다
_real_clean = si._tree_clean
si._tree_clean = lambda repo: True
w, b, why = si._workspace_open()
check("깨끗하면 제자리", (w, b), (si.ROOT, ""))
si._tree_clean = _real_clean
# 가져오기는 --ff-only 다. 사람 작업 위에 병합 커밋을 얹지 않는다.
check("ff-only 로만 가져온다", "--ff-only" in inspect.getsource(si._workspace_land), True)
check("클로드를 그 자리에서 돌린다", "cwd=work" in inspect.getsource(si.main), True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과 — 자율은 감사 위에서만")
