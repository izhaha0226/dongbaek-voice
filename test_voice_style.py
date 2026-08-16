#!/usr/bin/env python3
"""말투 통일 검증 — 목소리는 하나, 뜻은 그대로.

답을 만드는 곳이 넷이다(고정 문구·로컬 규칙·큐웬·클로드). 각자 고치면
새 문구가 생길 때마다 또 어긋나므로 speak.say 길목에서 한 번에 맞춘다.

여기서 지켜야 할 것은 두 가지다.
  ① 문어체가 구어체로 바뀐다
  ② **뜻은 한 글자도 안 바뀐다** — 숫자·이름·조사·문장 구조 그대로.
     소리로만 듣는 사장님은 숫자가 틀린 걸 알아챌 방법이 없다.
    python test_voice_style.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import re
import sys
from pathlib import Path

import voice_style as v

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"\n      기대={want!r}\n      실제={got!r}"))


print("[1] 네 경로의 실제 출력이 한 문체로 모인다")
for src, want in [
    # 로컬 규칙
    ("8월 14일 금요일에 일정 2개 있습니다.", "8월 14일 금요일에 일정 2개 있어요."),
    ("맞는 일정을 찾지 못했습니다.", "맞는 일정을 찾지 못했어요."),
    # 데몬 고정 문구
    ("멀리 계신 것 같아 소리를 키워 듣고 있습니다.", "멀리 계신 것 같아 소리를 키워 듣고 있어요."),
    ("동백 준비됐습니다.", "동백 준비됐어요."),
    # 복명복창·승인
    ("확인하겠습니다.", "확인할게요."),
    ("홍길동님 답변드리겠습니다.", "홍길동님 답변드릴게요."),
    # 큐웬이 잘 내는 꼴
    ("다시 한번 확인해 보시기 바랍니다.", "다시 한번 확인해 보세요."),
    ("참고하십시오.", "참고하세요."),
]:
    check(f"{src[:22]}…", v.apply(src), want)

print("\n[2] 받침에 따라 이에요/예요 가 갈린다")
# 규칙 표로는 못 한다 — 앞 글자 받침을 봐야 한다.
check("받침 있음", v.apply("일정입니다."), "일정이에요.")
check("받침 없음", v.apply("회의입니다."), "회의예요.")
# 숫자는 '읽은 소리' 의 받침이다. 5는 '오'(없음), 1은 '일'(있음).
check("숫자 5", v.apply("소넷 5입니다."), "소넷 5예요.")
check("숫자 1", v.apply("채널은 1입니다."), "채널은 1이에요.")

print("\n[3] 뜻은 한 글자도 안 바뀐다")
# ⚠ 이 검사가 이 모듈의 존재 이유다. 어미를 고치랬더니 내용을 바꾸는 것이
#   소형 모델에 맡겼을 때의 실패 방식이라, 규칙으로 하고 여기서 못박는다.
src = "김현진부장 미팅은 오후 4시 30분이고 매출은 1,234만원 늘었습니다."
out = v.apply(src)
check("숫자 보존", re.findall(r"[\d,:]+", out), re.findall(r"[\d,:]+", src))
check("이름 보존", "김현진부장" in out, True)
check("끝만 바뀜", out, "김현진부장 미팅은 오후 4시 30분이고 매출은 1,234만원 늘었어요.")

print("\n[4] 이미 구어체면 건드리지 않는다")
for s in ["아, 큰 티비 사야 되는 거 아니냐는 말씀이셨네요. 어떤 거 알아봐 드릴까요?",
          "네, 세 건 있네요.",
          "그 파일이 없는데요?"]:
    check(f"무변경: {s[:18]}…", v.apply(s), s)

print("\n[5] 이상한 입력에도 안 터진다")
for s in ["", "습니다", "...", "12345"]:
    got = v.apply(s)
    check(f"{s!r} → 문자열", isinstance(got, str), True)

print("\n[6] 길목(speak.say)에 실제로 걸려 있다")
import config
import speak
check("VOICE_STYLE_UNIFY 기본 켜짐", getattr(config, "VOICE_STYLE_UNIFY", None), True)
# ⚠ 여기서 _Path 를 쓰면 안 된다. 공개판은 테스트가 저장소 루트에 있어
#   '루트 임포트' 3줄을 떼고 옮기는데, 그 3줄에 _Path 임포트가 들어 있다.
#   본체에서만 돌고 공개판에서 NameError 로 죽는 검사가 된다 (실제로 겪음).
check("speak 가 voice_style 을 문다",
      "voice_style" in Path(speak.__file__).read_text(encoding="utf-8"), True)

print("\n[7] 큐웬이 흘리는 것도 걷어낸다")
# 소형 모델은 지시를 글자 그대로 따라 문장 뒤에 "요." 를 한 낱말로 붙이고
# ("모르겠어요. 요." 가 실제로 나갔다), 호칭도 스스로 붙인다 — 호칭은
# 동백 본체가 붙이므로 겹치면 "홍길동님, 사장님, …" 이 된다.
import gatekeeper
check("끝의 낱말 요 제거", gatekeeper._tidy("정확한 이유는 모르겠어요. 요."),
      "정확한 이유는 모르겠어요.")
check("앞 호칭 제거", gatekeeper._tidy("홍길동님, 지금 확인했어요."), "지금 확인했어요.")
check("멀쩡한 말은 그대로", gatekeeper._tidy("네, 세 건 있어요."), "네, 세 건 있어요.")

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과 — 목소리는 하나, 뜻은 그대로")
