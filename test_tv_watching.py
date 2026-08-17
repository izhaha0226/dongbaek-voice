#!/usr/bin/env python3
""""지금 티비 보고 있어" 는 통화 선언과 같다 — 끼어들지 말라는 말이다.

2026-08-15 저녁 실측. 사장님이 이 말을 여섯 번 하셨는데 여섯 번 다 클로드로
올라갔다($0.59). 그때마다 대화창이 새로 열려 이어진 TV 소리가 또 달라붙었고,
17:27 에는 "티비 다 볼 때까지는 얘기하지 마" 까지 하셔야 했다. 말귀를 못
알아들으니 같은 말을 되풀이하시게 되는 자리였다.

지키려는 것:
  ① 끝맺는 선언은 '내게 한 말 아님' 으로 본다 (전부 들림 로그 실측 문구)
  ② ⚠ 뒤에 용건이 따라오는 연결어미는 받는다 — 무르게 만든 게 아니라
     귀를 닫는 쪽을 넓힌 것이고, 넓히다 진짜 명령을 삼키면 그게 더 나쁘다
  ③ 기존 판정("너한테 한 말 아니야"·긴 문장 오판 방지)은 그대로다
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


def mine(said):
    return router.is_not_for_you(router.normalize(said))


print("[1] 끝맺는 선언 — 전부 실측 문구 (state/daemon.log 들림 7,400건에서 뽑음)")
for said in [
    "TV 보고 있어.",
    "티비 보고 있다고.",
    "아니 지금 TV 보고 있어.",
    "지금 유튜브 듣고 있어.",
    "TV 보는 중이야.",
    "TV 보는 거야, TV. 갑자기 안 돼? 네.",
    "티비 보는 거야?",
    "TV 보고 있다고? 뭐지. 맞아.",
    "동백아 티비 보고 있어. 얘기 안 해. 배.",   # 호출어가 붙어도 뜻은 같다
]:
    check(f"{said[:24]!r} → 내게 한 말 아님", mine(said), True)

print("\n[2] ⚠ 용건이 따라오면 받는다 — 연결어미(는데·으니까)가 그 표시다")
for said in [
    "티비 보고 있는데 지금 몇 시야?",
    "유튜브 보고 있는데 이거 좀 찾아줘",
    "TV 보고 있는데 소리 좀 줄여줘",
    "드라마 보고 있으니까 이따 알려줘",
    "TV 보는 거 아니야",              # 화면이 아니라는 말이다
    "유튜브 음악 틀어줘",
    "티비 켜줘",
    "뉴스 뭐 나와?",
]:
    check(f"{said[:24]!r} → 정상 명령", mine(said), False)

print("\n[3] ⚠ 긴 말은 선언이 아니라 받아쓰기다 — 30자 상한은 그대로 선다")
long_said = ("아까 그 광고 얘기 말인데 내가 티비 보고 있어서 못 봤거든 "
             "그거 다시 한번 정리해서 알려줄래")
check(f"{long_said[:24]!r}… → 정상 명령", mine(long_said), False)

print("\n[4] 기존 판정은 그대로다")
for said, notmine in [
    ("너한테 한 얘기 아니야", True),
    ("지금 통화중이야", True),
    ("동백아 오늘 일정 알려줘", False),
    ("메일 확인해줘", False),
]:
    check(f"{said!r} → {'내 말 아님' if notmine else '정상 명령'}", mine(said), notmine)

print("\n[5] ⚠ 분기를 없앤 변이는 [1] 이 잡는다 — 그 확인")
_saved = router._MEDIA_WATCH
try:
    router._MEDIA_WATCH = router.re.compile(r"(?!)")     # 아무것도 안 잡는 꼴
    check("판정을 빼면 'TV 보고 있어' 가 명령으로 샌다",
          mine("TV 보고 있어."), False)
finally:
    router._MEDIA_WATCH = _saved

print("\n" + ("실패 " + str(len(FAIL)) + "건: " + ", ".join(FAIL) if FAIL else "전부 통과"))
_sys.exit(1 if FAIL else 0)
