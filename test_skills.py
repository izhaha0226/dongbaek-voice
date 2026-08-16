#!/usr/bin/env python3
"""스킬 층(skills_local) — 선언이 능력이 되고, 위험은 표현이 안 되는지.

⚠ 실사용 skills/ 를 건드리지 않는다 — SKILLS_DIR 을 임시로 바꿔 시험.
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import tempfile
from pathlib import Path

import router
import skills_local as sk

FAIL = []


def check(name, got, want=True):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


tmp = Path(tempfile.mkdtemp())
sk.SKILLS_DIR = tmp
sk.REMOVED_DIR = tmp / "removed"
sk._cache.update(stamp=None, skills=[], warns=[])

print("[1] 만들기 — 화이트리스트 안에서만")
ok, msg = sk.create("시험-스킬", ["오늘 채점 어때"], "score_today")
check("정상 생성", ok, True)
ok2, msg2 = sk.create("나쁜-스킬", ["아무거나"], "os_system")
check("목록 밖 action 거부", ok2, False)
check("거부 사유에 허용 목록", "허용 목록" in msg2, True)

print("[2] 매칭 — 걸리면 실행, 안 걸리면 None")
check("트리거 적중", sk.match("오늘 채점 어때", "오늘 채점 어때") is not None, True)
check("무관한 말은 통과", sk.match("점심 뭐 먹지", "점심 뭐 먹지"), None)

print("[3] 충돌 — 같은 트리거는 뒤에 실린 쪽이 진다")
sk.create("겹침-스킬", ["오늘 채점 어때", "고유한 말"], "score_today")
sk.load(force=True)
# 파일명 정렬상 '겹침' 이 먼저 실려 '오늘 채점 어때' 를 가져가고,
# '시험-스킬' 쪽 트리거가 겹침 경고를 받는다 — 어느 쪽이든 경고가 남는 게 계약.
check("겹친 트리거 경고", any("겹침" in w or "겹치" in w for w in sk.warnings()), True)
check("고유 트리거는 산다", sk.match("고유한 말", "고유한 말") is not None, True)

print("[4] 승인 안 된 스킬은 안 돈다")
(tmp / "미승인.md").write_text(
    "---\nname: 미승인\ntriggers: 몰래 실행\naction: score_today\napproved: false\n---\n")
sk.load(force=True)
check("미승인 비활성", sk.match("몰래 실행", "몰래 실행"), None)

print("[5] 빼기 — 지우지 않고 보관")
out = sk.remove("시험-스킬")
check("뺐다는 답", "뺐습니다" in out, True)
check("보관됨", len(list(sk.REMOVED_DIR.glob("*시험-스킬*"))), 1)
out2 = sk.remove("겹침-스킬")
check("둘째도 뺌", "뺐습니다" in out2, True)
check("더는 안 걸림", sk.match("오늘 채점 어때", "오늘 채점 어때"), None)

print("[6] 라우터의 스킬 말귀")
for said, want in [("스킬 목록 알려줘", True), ("무슨 스킬 있어", True),
                   ("스킬 만들어", False)]:      # 만들기는 데몬 경로
    check(f"목록 판정 {said!r}", router.is_skill_list(router.normalize(said)), want)
check("만들기 판정", router.is_skill_create(router.normalize("방금 그거 스킬로 만들어줘")), True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 선언이 능력이 되고, 위험은 표현이 안 된다")
