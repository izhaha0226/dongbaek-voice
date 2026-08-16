#!/usr/bin/env python3
"""'부르다' 로 물러나도 창은 닫힌다 — "저 부르신 거 아닌 것 같은데요".

is_disown_reply 의 낱말 조합이 '하신' 만 알아서, 클로드가 같은 뜻을
'부르다' 로 말한 날은 물러남이 기록되지 않았다.

2026-08-15 00:12 실측 — TV 소리에 "저 부르신 거 아닌 것 같은데요, 필요하신
거 있으시면 다시 불러주세요" 로 물러났는데 창이 안 닫혔다. 그래서
  00:12:34  이어진 소리가 앞 명령에 달라붙어 클로드로 한 번 더 ($0.19)
  00:12:45  '배포.' 가 명령으로 올라가 위험 확인창까지 열렸다
물러남을 그때 알아봤으면 두 줄 다 호출어 없음으로 끊겼을 자리다.

지키려는 것:
  ① 부름꼴 물러남을 알아본다 (전부 실측 문구)
  ② ⚠ 부르다가 나온다고 다 물러남이 아니다 — 여기서 넓히면 진짜 답변 뒤
     창이 닫혀 사장님 말씀이 소리 없이 사라진다
  ③ 길이 상한은 그대로 걸린다
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


print("[1] 부름꼴 물러남 — 전부 실측 문구 (2026-08-15 state/transcript.jsonl)")
for said in [
    "저 부르신 거 아닌 것 같은데요, 필요하신 거 있으시면 다시 불러주세요.",
    "저 부르신 거 아닌 것 같아요, 필요하신 거 있으시면 다시 불러주세요.",
    "저 부르신 말씀 같지 않은데, 필요하신 거 있으시면 다시 불러주세요.",
    "저 부르신 말씀 아닌 것 같아요.",
    # 같은 뜻의 다른 조사 — 클로드는 매번 새로 쓴다
    "절 부르신 게 아닌 것 같아요.",
    "저를 부르신 게 아닌 것 같은데요, 다시 불러주세요.",
]:
    check(f"{said[:20]!r}… → 물러남", router.is_disown_reply(said), True)

print("\n[2] ⚠ 부르다가 나와도 물러남이 아닌 것 — 넓히면 말씀이 사라진다")
for said in [
    # 부정이 없다. 부름 얘기를 할 뿐 물러나는 게 아니다.
    "저 부르실 때는 \"동백아\" 라고만 하셔도 돼요.",
    "이름을 저 부르신 대로 바꿔뒀어요.",
    # 부른 사람이 동백이 아니다 — normalize 가 공백을 지워도 '저부르신' 이
    # 안 되므로 안 걸린다. 이게 붙여 읽기의 값이다.
    "다만 이번 대화창에서는 동백아 라고 부르신 게 아니라서 못 받았어요.",
    "김철수님이 부르신 거 아닌가요? 저는 그대로 두겠습니다.",
    # 물러남이 인용으로 섞인 긴 답변 — 창을 닫으면 이어질 말씀이 사라진다
    "\"동백아\" 하고 부르신 뒤 내일 일정 얘기가 있어서 그 부분만 여쭙겠습니다. "
    "내일 아침 8시에 함께 나가시는 걸로 들었는데 장소가 뭉개져서 못 알아들었습니다. "
    "장소만 말씀해 주시면 캘린더에 넣겠습니다. 저 부르신 게 아닌 대화는 넘기겠습니다.",
]:
    check(f"{said[:20]!r}… → 정상 답변", router.is_disown_reply(said), False)

print("\n[3] 길이 상한(60자)은 부름꼴에도 그대로 걸린다")
short = "저 부르신 거 아닌 것 같아요."
check("짧으면 물러남", router.is_disown_reply(short), True)
long_one = short + " " + "그리고 오늘 일정을 이어서 말씀드리면, " * 4
check(f"{len(router.normalize(long_one))}자로 늘리면 물러남 아님",
      router.is_disown_reply(long_one), False)

print("\n[4] 한국어 '하신' 꼴과 영어 꼴은 그대로다 (넓히기만 했다)")
check("저한테 하신 말씀 꼴",
      router.is_disown_reply("이번에도 저한테 하신 말씀이 아닌 것 같아요, 다시 불러주세요."), True)
check("영어 꼴",
      router.is_disown_reply("This clearly isn't directed at me — someone else's conversation."), True)
check("영어 오검출 아님",
      router.is_disown_reply("The customer isn't asking for that right now."), False)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — '부르다' 로 물러나도 창은 닫힌다")
