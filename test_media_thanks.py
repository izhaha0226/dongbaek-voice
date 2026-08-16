#!/usr/bin/env python3
"""홀로 선 "감사합니다." 는 방송 소리다 — 명령 조각에 붙이지 않는다.

whisper 는 조용한 구간을 유튜브 자막 말투로 적는다. _MEDIA_NOISE 는
"시청해주셔서 감사합니다" 를 통째로 잡는데, 실제로 올라오는 건 앞머리가
잘린 꼬리뿐이었다. 들림 로그 7,135건 실측(2026-08-16) — 이 꼴이 411건
(5.8%)이고 그중 걸리던 건 4건이다. "감사합니다." 340건은 들림 전체에서
가장 많이 나온 문자열이다.

실사례 2026-08-14 09:22 — "…데스크탑에서 텍스트로 수정하고 있어" 뒤에
환청 "감사합니다." 가 조각으로 붙어 명령에 딸려 들어갔고, 조각이 붙을
때마다 말끝 기다림이 되살아나 덩어리가 09:24 까지 불어났다.

지키려는 것:
  ① 발화 전체가 인사말 하나면 방송 소리로 본다 (전부 들림 로그 실측 문구)
  ② ⚠ 문장에 붙은 인사말은 사장님이 남기시는 말이다 — 파먹으면 안 된다
  ③ 기존 상투구 목록과 평범한 말씀은 그대로다
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


print("[1] 홀로 선 인사말 — 전부 실측 문구 (state/daemon.log 들림)")
for said in [
    "감사합니다.",            # 340건
    "감사합니다?",            # 28건
    "고맙습니다.",            # 22건
    "네, 감사합니다.",        # 12건
    "고맙습니다?",
    "네, 네, 네, 감사합니다.",
    "네, 고맙습니다.",
    "아, 감사합니다.",
    "정말 감사합니다",
]:
    check(f"{said!r} → 방송 소리", router.is_media_noise(said), True)

print("\n[2] ⚠ 문장에 붙은 인사말은 파먹으면 안 된다 — 받아쓰게 하신 말이다")
for said in [
    "내일 뵙겠습니다. 감사합니다",
    "자료 보내주셔서 감사합니다",
    "김철수님께 잘 받았다고 감사합니다 라고 보내줘",
    "감사합니다 라고 답장해줘",
    "고맙습니다 하고 전해",
]:
    check(f"{said[:22]!r} → 그대로 붙인다", router.is_media_noise(said), False)

print("\n[3] 기존 상투구 목록과 평범한 말씀은 그대로다")
for said, media in [
    ("다음 영상에서 만나요", True),
    ("시청해주셔서 감사합니다", True),
    ("구독과 좋아요 부탁드립니다", True),
    ("동백아 몇시야", False),
    ("오늘 일정 알려줘", False),
    ("고마워", False),               # 인사말 목록에 없는 짧은 말은 안 건드린다
    ("감사", False),
]:
    check(f"{said!r} → {'방송' if media else '사람 말'}",
          router.is_media_noise(said), media)

print("\n" + ("실패 " + str(len(FAIL)) + "건: " + ", ".join(FAIL) if FAIL else "전부 통과"))
_sys.exit(1 if FAIL else 0)
