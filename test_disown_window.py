#!/usr/bin/env python3
"""물러났으면 창도 닫는다 — "저한테 하신 말씀이 아닌 것 같아요" 뒤의 대화창.

2026-08-14 실측: 그 답 하나가 28건 $30.11 로 그날 비용의 4분의 1을 먹었다.
입으로는 "다시 불러주세요" 해놓고 대화창(REPLY_FOLLOWUP_SEC)은 열어둔 채라,
이어진 옆 대화가 호출어 없이 또 클로드로 올라갔다. 09:44·09:45·09:45,
11:24·11:25·11:25·11:26 처럼 줄줄이 이어졌고 뒤로 갈수록 비쌌다
($0.52 → $2.12 — 문맥이 쌓여서다).

지키려는 것:
  ① 물러나는 답을 알아본다 (문구는 클로드가 매번 새로 쓴다)
  ② 진짜 답변 중에 지나가며 언급한 것은 물러남이 아니다
  ③ 물러난 자리에서는 _REPLIED_AT 을 갱신하지 않는다 = 창이 안 열린다
  ④ 그 다음 진짜 답변이 나오면 창은 도로 열린다
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


print("[1] 물러나는 답을 알아본다 — 전부 실측 문구 (state/transcript.jsonl)")
for said in [
    "이번에도 저한테 하신 말씀이 아닌 것 같아요, 필요하신 거 있으시면 다시 불러주세요.",
    "이번 것도 저한테 하신 말씀이 아닌 것 같아요, 필요하신 거 있으시면 다시 불러주세요.",
    "이번에도 저한테 하신 말씀은 아닌 것 같아요, 필요하신 거 있으시면 다시 불러주세요.",
    "죄송한데 방금 말씀은 저한테 하신 것 같지 않아서요, 필요하신 거 있으시면 다시 불러주세요.",
    "이것도 저한테 하신 말씀 같지 않아서요, 필요하신 거 있으시면 \"동백아\" 하고 다시 불러주세요.",
    "이건 저한테 하신 말씀이 아니고 다른 분과 나누시는 대화 같아요. 필요하신 거 있으시면 불러주세요.",
    "저한테 하신 말씀이 아닌 것 같아 대기하겠습니다.",
    "제게 하신 말씀이 아닌 것 같아 넘기겠습니다.",
    "네, 제게 하신 말씀이 아니군요. 조용히 있겠습니다.",   # dongbaek._not_for_me_reply()
]:
    check(f"{said[:22]!r}… → 물러남", router.is_disown_reply(said), True)

print("\n[2] ⚠ 진짜 답변은 물러남이 아니다 — 여기서 창을 닫으면 사장님 말씀이")
print("     소리 없이 사라진다. 못 알아듣는 건 비용보다 비싸다.")
for said in [
    # 실측 128자 — 일정을 여쭙고 '나머지만' 넘긴 답이다. 창이 닫히면
    # 이어서 말씀하실 장소를 못 받는다.
    "\"동백아\" 하고 부르신 뒤 내일 일정 얘기가 있어서 그 부분만 여쭙겠습니다. "
    "내일 아침 8시에 함께 나가시고, 중기청 관련 업체가 오산에서 올라와 만나시는 "
    "걸로 들었습니다. 다만 장소가 뭉개져서 못 알아들었습니다. 장소만 말씀해 주시면 "
    "캘린더에 넣겠습니다. 나머지 대화는 제게 하신 말씀이 아닌 걸로 보고 넘기겠습니다.",
    # 실측 — '제게 하신 말씀이 아니' 를 인용해 구조를 설명한 답변
    "셋째, 큐원이 감당 못 하는 건 저한테 옵니다. 방금 \"제게 하신 말씀이 아닌 것 "
    "같다\"고 판단해서 넘긴 건 저, 클로드입니다. 대화창이 열려 있는 25초 안에 "
    "들어온 말이라 호출어 없이도 여기까지 올라온 것이고, 마지막 판단은 제가 했습니다.",
    # 실측 — '아니' 와 '하신' 이 우연히 섞였지만 받는 이(저한테·제게)가 없다
    "정확히는 목소리 톤 자체를 분석하는 건 아니고, 말씀하신 내용이나 표현 방식 보고 짐작한 거예요.",
    "\"진행\"이라고 하신 게 음성인식에서 \"지경에\"로 잘못 들렸어요. 취소하려던 게 "
    "아니라 목소리 인식이 어긋난 거라, 지금 다시 \"삭제 진행해줘\"라고 한 번 더 "
    "말씀해 주시면 될 것 같아요.",
    "오늘 일정 3건 있어요.",
    "",
]:
    check(f"{said[:22]!r}… → 정상 답변", router.is_disown_reply(said), False)

print("\n[3] 물러난 자리에서는 대화창을 열지 않는다 (실행기 경로)")
# ⚠ 소리·마이크·클로드를 건드리지 않는다. 실행기가 답을 내놓은 뒤
#   무엇을 갱신하는지만 본다 — 그게 free_pass 를 가르는 유일한 값이다.
import time

import bridge
import config
import dongbaek
import speak

speak.say = lambda text, **kw: None                                  # 소리 없이
router.handle_local = lambda text, elevated=False: None              # 로컬 우회 금지
config.STREAM_REPLY = False
config.ECHO_BACK_ENABLED = False
config.ACK_ENABLED = False
# ⚠ 코드 변경 안내를 끈다. 테스트에서는 스냅샷 지문이 비어 있어(code_guard)
#   늘 "…를 고쳤습니다" 가 답 뒤에 붙는데, 그러면 답이 길어져 여기서 재는
#   것이 작업 트리의 더러움에 좌우된다. 실제로 그 꼴이 되는 답이 하나
#   있었다(2026-08-14 실측 120자) — 그건 자가개선 보고라 창을 열어도 맞다.
config.CODE_SPEAK_DIFF = False

_REPLY = {"text": ""}


def _fake_ask(prompt, elevated=False, dev=False, on_text=None):
    return (_REPLY["text"], {"effective_input": 0, "cache_read": 0,
                             "cache_write": 0, "output": 0, "cost_usd": 0})


bridge.ask = _fake_ask  # type: ignore[assignment]
import threading
threading.Thread(target=dongbaek._run_jobs, daemon=True).start()


def _run(reply: str) -> None:
    _REPLY["text"] = reply
    dongbaek.submit_command("옆에서 들린 말", heard="옆에서 들린 말")
    deadline = time.monotonic() + 15
    while (dongbaek.is_busy() or dongbaek._JOBS.qsize()) and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)


dongbaek._REPLIED_AT.update(at=0.0, who="")
dongbaek._DISOWNED["at"] = 0.0
_run("이번에도 저한테 하신 말씀이 아닌 것 같아요, 필요하신 거 있으시면 다시 불러주세요.")
check("물러남을 기록했다", dongbaek._DISOWNED["at"] > 0.0, True)
check("대화창은 안 열렸다", dongbaek._REPLIED_AT["at"], 0.0)
# ⚠ 여기가 핵심이다 — 방금 소리를 냈으므로 speak.last_finished_at() 만으로도
#   창이 열릴 수 있다. 그래도 0 이어야 한다.
check("호출어 면제 창 없음", dongbaek._reply_window_until(), 0.0)
check("그래서 호출어 없는 말은 안 받는다",
      time.monotonic() < dongbaek._reply_window_until(), False)

print("\n[4] 그 다음 진짜 답변이면 창은 도로 열린다")
_run("오늘 일정은 3건이에요.")
check("대화창이 열렸다", dongbaek._REPLIED_AT["at"] > 0.0, True)
check("호출어 면제 창이 살아 있다",
      time.monotonic() < dongbaek._reply_window_until(), True)

print("\n[5] 스위치를 내리면 예전대로 (되돌릴 구멍은 남긴다)")
config.DISOWN_CLOSES_WINDOW = False
dongbaek._REPLIED_AT.update(at=0.0, who="")
_run("이번에도 저한테 하신 말씀이 아닌 것 같아요, 필요하신 거 있으시면 다시 불러주세요.")
check("창이 열린다", dongbaek._REPLIED_AT["at"] > 0.0, True)
config.DISOWN_CLOSES_WINDOW = True

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 물러난다고 말했으면 창도 닫는다")
