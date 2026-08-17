#!/usr/bin/env python3
"""로컬 즉답이 '메일 읽어 달라' 는 말을 가로채면 안 된다.

2026-08-17 09:16~09:20 실측 4건. "어제 더비안 강남에서 온 메일이 있어. 이거
확인해 가지고 니즈 파악하고 내용 분석 좀 해줘" 가 '확인' 두 글자에 걸려
최신 3건 목록으로 답했다. 사장님이 말을 바꿔 세 번 더 이르시는 동안 네 번 다
똑같은 목록이 나갔고 ("왜 얘기해 말을 하다 말어"), 09:42 에 '확인' 을 빼고
"첨부 받아줘" 라고 하셔서야 클로드로 올라가 첨부 7개를 제대로 받았다.

로컬이 가진 답은 통수와 최신 3건의 보낸이·제목뿐이다. 본문·파일을 물으시거나
보낸 이를 짚으시면 그 답으로는 답이 안 되므로 물러나야 한다.

지키려는 것:
  ① 본문·파일을 청하는 말은 로컬이 물러난다 (전부 실측 문구)
  ② 보낸 이를 짚는 말도 물러난다 — 읽어 달라는 낱말이 없어도
  ③ ⚠ '안 읽은 메일 몇 통' · '최근 메일 알려줘' 는 그대로 0초에 답한다.
     좁히다 이걸 놓치면 매일 쓰는 즉답이 통째로 느려지고 비싸진다
  ④ 발송 명령이 조회로 새지 않는다 (옛 가드가 살아 있는가)
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import router

FAIL = []


def check(name, got, want=True):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


def local_mail(said):
    """이 말을 로컬 메일 즉답이 먹는가."""
    t = router.normalize(said)
    return router._is_mail_unread(t) or router._is_mail_recent(t)


print("[1] 본문·파일을 청하는 말은 클로드로 (state/dongbaek.db 실측 문구)")
for said in [
    "어제 더비안 강남에서 온 메일이 있어. 이거 확인해 가지고 니즈 파악하고 내용 분석 좀 해줘.",
    "오늘 오후에 들어온 메일들 확인해가지고 요약정리해줘.",
    "최근 메일 확인해서 첨부파일 다운받아줘.",
    "새로 온 메일 내용 좀 정리해줘.",
    "메일 왔는지 확인하고 업체별로 보고해줘.",
]:
    check(f"물러남: {said[:34]}", local_mail(said), False)

print("[2] 보낸 이를 짚으면 낱말이 없어도 물러난다")
for said in [
    "왜 얘기해 말을 하다 말어. 말을 하다 마라. 더비안 강남에서 보낸 메일을 확인하라고.",
    "한빛건설 강남에서 온 메일 확인해줘.",
    "김정연한테서 온 메일 왔어?",
    "광고플랫폼로부터 받은 메일 알려줘.",
]:
    check(f"물러남: {said[:34]}", local_mail(said), False)

print("[3] ⚠ 매일 쓰는 즉답은 그대로 로컬이다 (0.4초·0토큰)")
for said in [
    "안 읽은 메일 몇 통이야",
    "이번 주는 안 읽은 메일 몇 통이야?",
    "최근 메일 알려줘",
    "메일 온 거 확인해줘.",
    "새 메일 왔어?",
    "메일 뭐 왔는지 알려줘",
]:
    check(f"로컬: {said[:30]}", local_mail(said), True)

print("[4] 발송 명령은 여전히 조회로 새지 않는다")
for said in [
    "고객 전체에게 메일 보내",
    "김정연한테 메일 하나만 써줘. 테스트 메일.",
    "한빛건설 강남에 메일 보내줘. 내용은 회의 자료 잘 받았다고.",
]:
    check(f"조회 아님: {said[:30]}", router._is_mail_recent(router.normalize(said)), False)

print("[5] ⚠ 무르게 만든 게 아니라 좁힌 것이다 — 가드를 없애면 옛 오답이 돌아온다")
_saved = router._is_mail_deep
try:
    router._is_mail_deep = lambda t: False           # 가드를 없앤 변이
    check("가드 없으면 09:16 발화를 다시 먹는다",
          local_mail("어제 더비안 강남에서 온 메일이 있어. 이거 확인해 가지고 니즈 파악하고 내용 분석 좀 해줘."),
          True)
finally:
    router._is_mail_deep = _saved

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    for f in FAIL:
        print("  " + f)
    raise SystemExit(1)
print("✅ 전부 통과 — 읽어 달라는 말은 넘기고, '몇 통이야' 는 그대로 0초에 답한다")
