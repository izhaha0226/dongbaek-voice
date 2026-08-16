#!/usr/bin/env python3
"""되물음이 되물음을 부르는 고리를 끊는다.

"목소리 확인이 안 돼서 못 받았어요" 는 스스로 재시도 창을 연다. 그래서
다음 소리가 창 밖 방송이었어도 창 안이 되고, 또 되묻고, 또 창이 열린다.
실측 2026-08-15 19:55 — TV 드라마(이혼소송 장면)에 대고 9초·19초 간격으로
세 번 연달아 되물었다. daemon.log 되물음 94건 중 21건이 이 고리다.

⚠ 되물음 자체는 살아 있어야 한다. 94건 중 38건은 뒤이어 명령이 통과했다.
"""
import sys as _sys
import time
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import config
import dongbaek
import speak

FAIL = []
SPOKEN = []


def check(name, got, want=True):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


# 소리는 내지 않고 무엇을 말하려 했는지만 잡는다
speak.say = lambda text, **kw: SPOKEN.append(text)

COOL = getattr(config, "VOICE_RETRY_NOTICE_COOLDOWN_SEC", 30.0)


def reset():
    SPOKEN.clear()
    dongbaek._RETRY_TOLD["at"] = 0.0


def rewind(sec):
    """되물은 시각을 sec 초 전으로 밀어 시간이 흐른 것처럼 만든다."""
    dongbaek._RETRY_TOLD["at"] -= sec


print("[1] 처음 막혔을 때는 되묻는다 (침묵은 고장으로 보인다)")
reset()
check("말했다", dongbaek._retry_notice_due(), True)
check("한 마디만", len(SPOKEN), 1)
check("내용", "목소리 확인" in SPOKEN[0], True)

print("[2] 08-15 19:55 재연 — TV 드라마 삼연타가 한 번으로 줄어든다")
reset()
dongbaek._retry_notice_due()          # 19:55:03 '그 메일웨이에서 뭐라고…'
rewind(9)
dongbaek._retry_notice_due()          # 19:55:12 'TV 보는 중이야.'
rewind(19 - 9)
dongbaek._retry_notice_due()          # 19:55:31 '다음 선배가 준비 중인…'
check("세 번 중 한 번만 말한다", len(SPOKEN), 1)

print("[3] 창은 그대로 열어 둔다 — 입만 다물지, 귀는 안 닫는다")
# 삼켰다고 재시도 창까지 닫으면 사장님이 다시 말씀하셔도 호출어를 또
# 요구하게 된다. 창을 여는 줄은 되물음 성공 여부와 무관해야 한다.
src = (_Path(__file__).resolve().parent / "dongbaek.py").read_text(encoding="utf-8")
i = src.find("_retry_notice_due()          # 잇따른 되물음은 삼킨다")
check("호출부를 찾았다", i > 0, True)
check("바로 뒤에서 창을 연다",
      '_REPLIED_AT["at"] = time.monotonic()' in src[i:i + 200], True)
check("되물음을 내는 자리는 한 곳뿐이다",
      src.count('speak.say("목소리 확인이 안 돼서'), 1)

print("[4] 목소리가 통과하면 고리가 끊긴다 — 다음 막힘은 새 사건이다")
reset()
dongbaek._retry_notice_due()
dongbaek._RETRY_TOLD["at"] = 0.0      # 통과 지점이 하는 일
check("쿨다운을 안 기다리고 다시 알린다", dongbaek._retry_notice_due(), True)
check("배선 — 통과 지점이 리셋한다", src.count('_RETRY_TOLD["at"] = 0.0') >= 2, True)

print("[5] 쿨다운이 지나면 다시 알린다 (영구 침묵이 아니다)")
reset()
dongbaek._retry_notice_due()
rewind(COOL - 1)
check("아직은 삼킨다", dongbaek._retry_notice_due(), False)
rewind(2)
check("지나면 말한다", dongbaek._retry_notice_due(), True)

print("[6] ⚠ 무르게 만든 게 아니다 — 화자 인증은 한 줄도 안 건드렸다")
check("문턱 그대로", config.VOICE_VERIFY_THRESHOLD, 0.45)
check("인증 켜짐", config.VOICE_VERIFY_ENABLED, True)
# 거절 분기는 여전히 '막힘' 으로 적고 continue 로 끝난다 — 되물음을
# 넣느라 실행 경로가 열리면 그게 진짜 완화다.
j = src.find('log(f"미등록 목소리(유사도 {who}) — 무시')
blk = src[j:src.find("continue", j) + len("continue")]
check("막힘으로 기록한다", 'route="blocked"' in blk, True)
check("실행 없이 끝난다", blk.rstrip().endswith("continue"), True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 한 번은 되묻고, 잇따른 되물음은 삼킨다")
