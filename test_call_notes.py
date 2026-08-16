#!/usr/bin/env python3
"""통화 정리 — "전화 끝났어" 하면 정리해 위키에 남긴다.

사장님 지시(2026-08-12): "내가 전화끝났어 라고 하면, 통화내용을 정리를
해서 위키에 저장을 해야지."

그 전에는 "어, 전화 끝났어" 가 클로드로 가서 $0.22 를 쓰고 쓸모없는 확인
답변이 돌아왔다. 사장님 지적 — "'네 알겠습니다' 아니면 '통화 내용은
뭐였는데요? 요약 정리하겠습니다' 이렇게 나와야지."

    python test_call_notes.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import call_notes
import router

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


print("\n주문을 알아듣는가 (말씀은 매번 조금씩 다르다)")
for said in ["전화 끝났어", "통화 끝났어", "전화 끊었어", "통화 종료했어",
             "어 전화 끝났어", "전화 마쳤어"]:
    check(f"{said!r}", call_notes.is_end_call(router.normalize(said)), True)

print("\n⚠ 엉뚱한 말에 발동하지 않는가")
for said in ["전화 좀 해줘", "통화 기록 알려줘", "오늘 일정 알려줘",
             "전화번호 알려줘"]:
    check(f"{said!r} → 아님", call_notes.is_end_call(router.normalize(said)), False)

print("\n승인 없이 처리하는가 (내 위키에 내 통화를 적는 일이다)")
check("위험 게이트에 안 걸린다", router.danger_hit("전화 끝났어"), None)
check("안전 조회로 통과", router.is_safe_query("전화 끝났어"), True)

print("\n모으고 정리하는가")
call_notes.clear()
check("처음엔 비어 있다", call_notes.pending(), 0)
for t in ["여보세요 네 접니다", "그 건은 다음 주에 보내드리겠습니다",
          "네 알겠습니다 그럼 그렇게 하시죠", "들어가세요"]:
    call_notes.note(t)
check("모인다", call_notes.pending(), 4)

_real_dir = call_notes.WIKI_DIR
import tempfile
from pathlib import Path
call_notes.WIKI_DIR = Path(tempfile.mkdtemp())
try:
    said, path = call_notes.save()
    check("파일이 만들어졌다", path is not None and path.exists(), True)
    check("저장 뒤 버퍼가 비워진다", call_notes.pending(), 0)
    check("음성 보고에 마크다운이 없다",
          any(c in said for c in "*#`|"), False)
    text = path.read_text(encoding="utf-8")
    check("원문이 남는다 (요약만 있으면 확인할 길이 없다)",
          "그 건은 다음 주에" in text, True)
    check("받아쓰기 경고가 있다", "받아쓰기" in text, True)
    check("시각이 남는다", "시작" in text, True)
finally:
    call_notes.WIKI_DIR = _real_dir

print("\n짧으면 정리하지 않는다 (통화가 아니다)")
call_notes.clear()
call_notes.note("여보세요")
said, path = call_notes.save()
check("파일을 안 만든다", path, None)
check("그렇다고 말한다", "없습니다" in said, True)
check("버퍼는 비운다", call_notes.pending(), 0)

print("\n데몬에 연결돼 있는가")
src = open("dongbaek.py").read()
check("통화 중에 모은다", "call_notes.note(text, owner=mine)" in src, True)
# 방송을 통화로 오인해 위키에 남기던 것을 여기서 끊는다 (2026-08-14).
check("누가 말했는지도 함께 적는다", "call_notes.owner_heard()" in src, True)
check("'전화 끝났어' 를 로컬에서 처리한다", "call_notes.is_end_call" in src, True)
# 2026-08-13 무음 통일 이후 해제는 _phone_exit 가 담당한다 (봉인 해제 포함).
check("정리 뒤 전화 모드를 푼다",
      '_phone_exit("전화 끝났어"' in src, True)

print("\n[정지 요청 — '그만' 은 재실행이 아니라 정지다]")
# ⚠ 2026-08-14 15:01~15:04 실사례. MERGE_ON_INTERRUPT 가 "그만해" 를 보탠
#   말로 보고 앞 명령에 이어 붙여 통째로 다시 물었다. 같은 482자를 여섯 번
#   읽었고, 사장님이 "그만해" 하실 때마다 한 번씩 더 읽었다.
#   멈추라는 말이 재실행이 되면 빠져나올 방법이 없다.
import router as _router
for s in ("그만", "그만해", "이제 그만하라고", "됐어", "멈춰", "조용히 해",
          "알았어. 그만해. 그만해. 정리만 해줘. 읽지 않아도 돼."):
    check(f"정지다: {s[:20]!r}", _router.is_stop_speaking(s), True)
# ⚠ 낱말 포함으로만 보면 '그만큼'·'그만두고' 가 걸린다. 절 길이로 거른다.
for s in ("그만큼 좋았어", "그 일은 그만두고 다른 걸 하자", "오늘 일정 알려줘",
          "너한테 그거 다 읽어 달라는 거 아니고 그냥 저장만 하고 요약 정리해 두라고"):
    check(f"정지 아니다: {s[:20]!r}", _router.is_stop_speaking(s), False)
# ⚠ '됐어' 는 무엇이 '되었다' 는 말의 끝이기도 하다. 절 안 아무 데나 있는
#   걸로 잡으면 상태를 묻는 말이 중단 명령이 된다.
#   실사례 2026-08-16 09:27 — "어떤 게 수정됐어?"(공백 빼면 7자)가 정지로
#   읽혀 대화창이 닫혔고, 이어서 하신 "둘다 반영이 뭐 뭔데."가 호출어 없는
#   말이 되어 통째로 무시됐다. "대화가 두 마디를 못 넘어간다" 가 이것이었다.
for s in ("어떤 게 수정됐어?", "반영됐어?", "저장됐어?", "등록됐어?", "배포됐어?",
          "정리됐어", "둘다 반영이 뭐 뭔데."):
    check(f"완료형은 정지 아니다: {s[:20]!r}", _router.is_stop_speaking(s), False)
# 멈추라는 뜻의 '됐어' 는 홀로 서거나 짧은 앞말만 데리고 온다.
for s in ("됐어", "이제 됐어", "다 됐어", "응 됐어"):
    check(f"정지다: {s[:20]!r}", _router.is_stop_speaking(s), True)

print("\n[통화 정리는 소리로 짧게, 전문은 링크로]")
# 사장님 지시: "다 읽으라는 게 아니고 … share note url 로 보내주고
# 전화내용만 요약해서 알려줘." 귀로 482자를 듣는 건 쓸모가 없다.
check("소리 상한이 있다", call_notes.CALL_SPOKEN_MAX <= 120, True)
check("여러 줄 요약에서 한 줄만 뽑는다",
      "\n" not in call_notes._one_line("첫 문장이다\n## 절\n- 불릿"), True)
check("마크다운 기호는 떼고 읽는다",
      call_notes._one_line("## 제목\n- 실제 내용 문장이다").startswith("실제"), True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전화 끝났다고 하시면 정리해 위키에 남깁니다")
