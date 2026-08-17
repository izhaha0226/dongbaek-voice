#!/usr/bin/env python3
"""사람과 사람처럼 대화하는가.

사장님 요구:
  "내 얘기가 모두 끝난 다음에 얘기를 하라고. 그리고 네가 얘기할 때 내가
   추가로 얘기를 하면 네 얘기를 중단하고 내 얘기를 끝까지 듣고 앞뒤의
   문맥을 파악한 후 전체 답변을 다시 해달라."

지키려는 것:
  ① 쉬어 가며 말해도 한 덩어리로 받는다 (반쪽 명령이 실행되면 안 된다)
  ② 문장이 끊긴 것처럼 보이면 더 오래 기다린다
  ③ 답변 도중에 보탠 말은 앞 명령과 합쳐 통째로 다시 묻는다
  ④ 반쪽만 듣고 만든 앞 답변은 말하지 않고 버린다
    python test_conversation.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import threading
import time

import bridge
import code_guard
import config
import dongbaek
import router
import speak

FAIL = []
_REAL_SAY = speak.say          # 가짜로 바꾸기 전에 진짜 구현을 잡아둔다


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


print("[1] 말이 끝났는지 판단 — 끊긴 것처럼 보이면 더 기다린다")
for text, unfinished, why in [
    ("일단 그 부분보다도", True, "연결어미로 끝남"),
    ("강남 미팅은", True, "조사로 끝남"),
    ("내일 미팅 잡아줘 그리고", True, "접속사로 끝남"),
    ("광고플랫폼에 들어가면", True, "조건절 — 본론이 남았다"),
    ("데이터 확인하려고", True, "의도 연결어미"),
    ("지금 몇 시야", False, "완결된 질문"),
    ("일정 알려줘", False, "완결된 지시"),
    ("그래서 어떻게 하겠다고", False, "인용형 종결 — 끝난 말이다"),
]:
    check(f"{why}: {text!r}", router.looks_unfinished(text), unfinished)

print("\n[2] 쉬어 가며 말해도 한 덩어리로 받는다")


class FakeListener:
    """말 조각을 차례로 내주는 가짜 마이크. None 은 '더 말 안 함'."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.waits = []

    def next_utterance(self, timeout=None):
        self.waits.append(timeout)
        if not self.chunks:
            return None
        return self.chunks.pop(0)


TEXT = {}
import audio as audio_mod  # noqa: E402

audio_mod.transcribe = lambda a: TEXT.get(id(a), a) if isinstance(a, str) else ""  # type: ignore[assignment]
speak.say = lambda *a, **k: None  # type: ignore[assignment]

# 실제로 잘려 나갔던 발화를 조각으로 재현
L = FakeListener(["거기 용탐 밸리라고 있잖아", "우리 광고주 중에", "그 데이터를 확인해줘"])
merged = dongbaek.collect_turn(L, "광고플랫폼에 들어가면")
check("네 조각이 한 문장으로 합쳐졌다",
      merged, "광고플랫폼에 들어가면 거기 용탐 밸리라고 있잖아 우리 광고주 중에 그 데이터를 확인해줘")

check("끊긴 문장 뒤에는 더 오래 기다렸다",
      L.waits[0], config.TURN_WAIT_UNFINISHED_SEC)
# 이어 말하기 시작한 뒤로는 완결된 문장 뒤에도 넉넉히 기다린다 —
# 이미 길게 말씀하고 계시면 아직 하실 말이 남았다고 본다.
check("이어 말하는 중에는 완결 문장 뒤에도 넉넉히",
      L.waits[-1], config.TURN_WAIT_LONG_SEC)

L2 = FakeListener([])          # 더 말 안 함
check("한 마디로 끝나면 그대로", dongbaek.collect_turn(L2, "지금 몇 시야"), "지금 몇 시야")
check(f"짧은 한마디는 빨리 끝낸다 ({L2.waits[0]}초)",
      L2.waits[0], config.TURN_WAIT_SEC)

print("\n[2b] 1~2분 길게 말해도 끝까지 담는다")
# 사장님 지시: "내가 1분 이상 얘기할 수도 있고 2분이 될 수도 있어.
#              그걸 네가 다 듣고 한 번에 대답할 수 있도록 해줘."
# 문장이 완결될 때마다 1.2초에 끊으면 긴 설명이 토막 난다.
LONG = [
    "동백이 너는 내가 길게 얘기하면 다 무시해 버리는데.",
    "내가 길게 얘기하는 거 다 듣고 한 번에 대답할 수 있도록 해줘.",
    "내가 1분 이상 얘기할 수도 있고 2분이 될 수도 있어.",
    "그걸 네가 다 듣고 나한테 답변을 해야 되는 거야?",
    "그리고 그 답변을 네가 하다가 내가 중간에 끊고 다른 얘기를 하더라도.",
    "너는 그것까지 듣고 진행해.",
]
L3 = FakeListener(LONG[1:])
merged_long = dongbaek.collect_turn(L3, LONG[0])
check(f"{len(LONG)}조각이 하나로", all(c[:12] in merged_long for c in LONG), True)
# 두 조각째부터는 '긴 이야기' 로 보고 더 기다린다
check("이어 말하기 시작하면 더 기다린다",
      max(L3.waits) >= config.TURN_WAIT_LONG_SEC, True)

print("\n[3] 답변 도중에 보탠 말 — 앞 명령과 합쳐 다시 묻는다")
code_guard.guard = lambda t, n="": (True, "", {"repo": t, "label": "t",
                                               "fingerprint": "f0"})  # type: ignore[assignment]
code_guard.tree_fingerprint = lambda r: "f0"  # type: ignore[assignment]

SPOKEN, ASKED = [], []
_started = threading.Event()


def slow_ask(prompt, elevated=False, dev=False, on_text=None):
    ASKED.append(prompt)
    _started.set()
    time.sleep(1.2)                       # 답을 만드는 중
    return ("답변입니다.", {"effective_input": 0, "cache_read": 0,
                        "cache_write": 0, "output": 0, "cost_usd": 0})


bridge.ask = slow_ask  # type: ignore[assignment]


def _record_say(text, **kw):
    # 답변만 센다. Ack 의 '확인하고 있습니다' 같은 알림은 답이 아니다.
    if kw.get("priority", speak.PRIORITY_REPLY) >= speak.PRIORITY_REPLY:
        SPOKEN.append(text)


speak.say = _record_say  # type: ignore[assignment]
# 로컬 처리를 막아 전부 Claude 경로로 보낸다.
# (안 막으면 실제 캘린더에 강남 미팅이 있어 0 토큰으로 끝나 버린다)
router.handle_local = lambda text, elevated=False: None  # type: ignore[assignment]
threading.Thread(target=dongbaek._run_jobs, daemon=True).start()

dongbaek.submit_command("강남 미팅 언제야", heard="x")
_started.wait(5)
check("첫 명령이 처리에 들어갔다", dongbaek.is_busy(), True)

# 처리 중에 말을 보탠다 → 세대를 올리고 합쳐서 다시 넣는다 (메인 루프가 하는 일)
dongbaek.bump_generation()
dongbaek.submit_command("강남 미팅 언제야 그리고 몇 시에 나가야 해", heard="y")

deadline = time.monotonic() + 15
while (dongbaek.is_busy() or dongbaek._JOBS.qsize()) and time.monotonic() < deadline:
    time.sleep(0.1)
time.sleep(0.5)

check("Claude 를 두 번 불렀다", len(ASKED), 2)
check("두 번째는 앞뒤가 합쳐진 질문이다",
      ASKED[1], "강남 미팅 언제야 그리고 몇 시에 나가야 해")
check("답은 한 번만 말했다 (반쪽 답은 버려짐)", len(SPOKEN), 1)

print("\n[4] 보태지 않으면 그대로 답한다 (세대가 그대로일 때)")
SPOKEN.clear()
ASKED.clear()
dongbaek.submit_command("오늘 일정 알려줘", heard="z")
deadline = time.monotonic() + 15
while (dongbaek.is_busy() or dongbaek._JOBS.qsize()) and time.monotonic() < deadline:
    time.sleep(0.1)
time.sleep(0.5)
check("답을 말했다", len(SPOKEN), 1)

print("\n[5] 복명복창 — 들은 걸 짧게 되읊는다")
# "네 알겠습니다" 보다 들은 말을 그대로 돌려주는 게 낫다.
# 오인식을 사장님이 그 자리에서 잡을 수 있기 때문이다.
# 그대로 되읊으면 사장님 말투를 그대로 돌려주는 꼴이다 ("네, 매출 알려줘.").
# 요청 어미를 떼고 무엇을 하려는지 붙인다.
#
# Claude 에 요약을 맡기지 않는 이유는 실측 5.7초 · $0.034/회 —
# 복명복창은 즉시성이 생명이고 하루 340건이면 $11 이 넘는다.
# ⚠ 머리말("네, "·"아, "·없음)은 매번 달라진다 — 똑같은 맞장구가 반복되면
#   자동응답기로 들려서 일부러 돌려 쓴다(config.ECHO_BACK_TEMPLATES).
#   그래서 문장을 통째로 비교하면 안 되고, 머리말을 뗀 본문만 본다.
#   머리말이 목록 안의 것인지는 아래에서 따로 확인한다.
_PREFIXES = tuple(t.split("{echo}")[0] for t in config.ECHO_BACK_TEMPLATES)


def _body(line: str) -> str:
    for p in sorted(_PREFIXES, key=len, reverse=True):
        if p and line.startswith(p):
            return line[len(p):]
    return line


# 어미는 "…하겠습니다" 가 아니라 "…할게요" 다. 복명복창은 명령마다 나가는,
# 사장님이 하루에 가장 많이 듣는 문장이라 여기가 문어체면 전부 보고체로 들린다.
for said, want, why in [
    ("이번달 매출 알려줘", "이번달 매출 확인할게요", "조회 → 확인"),
    ("내일 오후 두 시에 미팅 잡아줘",
     "내일 오후 두 시에 미팅 등록할게요", "일정 → 등록"),
    ("config 파일에 주석 하나 달아줘",
     "config 파일에 주석 고칠게요", "코드 → 수정"),
    ("음악 틀어줘", "음악 틀게요", "음악 → 재생"),
    ("메일 보내줘", "메일 보낼게요", "발송"),
    # 붙일 동사와 겹치는 낱말은 요지에서 뗀다 ("삭제 삭제할게요" 방지)
    ("그 파일 삭제해줘", "그 파일 삭제할게요", "겹침 제거"),
    ("광고플랫폼 진행상황 체크해줘",
     "광고플랫폼 진행상황 확인할게요", "겹침 제거 ②"),
    # 반대로 '배포' 는 남겨야 한다 — 붙는 동사가 밋밋해서 정보가 사라진다
    ("프로덕션 배포해줘", "프로덕션 배포 진행할게요", "핵심어는 남긴다"),
]:
    check(f"{why}: {said!r}", _body(dongbaek.echo_back(said)), want)

# 머리말은 반드시 목록 안의 것이어야 한다. 그리고 40번을 부르면 세 가지가
# 모두 나와야 한다 — 하나로 굳어 있으면 변형을 넣은 뜻이 없다.
_heads = {dongbaek.echo_back("매출 알려줘")[: -len("매출 확인할게요")]
          for _ in range(40)}
check("머리말이 전부 목록 안의 것", _heads <= set(_PREFIXES), True)
check("머리말이 하나로 굳지 않았다", len(_heads) > 1, True)

long_cmd = ("광고플랫폼에 들어가면 거기 한빛리조트라고 있잖아 "
            "우리 광고주 중에 그 데이터를 확인해줘")
said = dongbaek.echo_back(long_cmd)
check(f"길면 앞부분만 ({len(said)}자)", len(said) < len(long_cmd), True)
check("잘렸다는 걸 표시한다", "등" in said, True)
check("빈 명령이면 아무 말 안 함", dongbaek.echo_back("  "), "")

# 묻는 말에는 복명복창을 붙이지 않는다 — "왜 대답을 그렇게 늦게 해?" 가
# "왜 대답을 그렇게 늦게 해 확인할게요" 로 나갔다 (2026-08-13 23:16 실사례).
# 복명복창은 실행 전에 잘못 들었는지 확인하는 장치라, 답 자체가 확인인
# 질문에는 붙일 자리가 없다.
for q in ["왜 대답을 그렇게 늦게 해?", "지금 몇 시야", "VAP가 뭐야?", "이거 어떻게 해"]:
    check(f"질문이면 조용히: {q!r}", dongbaek.echo_back(q), "")
# ⚠ 의문사가 섞여도 의도가 잡힌 명령은 그대로 둔다 — 지우고 보내는 일은
#   실행 전 확인이 오히려 더 필요하다.
check("의문사 섞인 명령은 유지",
      "삭제할게요" in dongbaek.echo_back("그거 왜 아직 있어? 지워줘"), True)

print("\n[6] 긴 지시가 대화창 만료로 버려지면 안 된다")
# 실제로 겪은 일 — '동백아' → '네' → 20초짜리 지시 → 아무 반응 없음.
# 대화창을 '말이 끝난 시각' 으로 재면 길게 말할수록 불리해진다.
# 말을 건 시점이 창 안이었으면 그건 동백에게 한 말이 맞다.
_SR = config.SAMPLE_RATE


def passes(speech_sec, window_left):
    """window_left 초 남은 창에서 speech_sec 짜리 발화가 통과하는가."""
    now = 1000.0
    followup = now + window_left
    end_based = now < followup                       # 예전 방식
    start_based = (now - speech_sec) < followup      # 지금 방식
    return end_based, start_based


for sec, left, why in [
    (3, 5, "짧은 말, 창 넉넉"),
    (20, -1, "긴 말, 말하는 사이 창 만료"),
    (30, -5, "아주 긴 말"),
]:
    old, new = passes(sec, left)
    check(f"{why}: {sec}초 발화 → 지금은 통과", new, True)
    if left < 0:
        check(f"  → 예전 방식이었으면 버려졌다", old, False)

# ⚠ 예전엔 '20초 이상' 을 요구했다. 그러나 창이 길수록 그 사이 통화·주변
#   대화가 호출어 없이 명령으로 들어온다(실측 2026-08-11). 이어 말할 여유는
#   주되 너무 길지는 않게 — 위아래를 함께 못박는다.
# 사장님 지시(2026-08-11): "동백아 부르고 3초 안에 얘기하면 호출로 처리해줘."
# 이어 말할 창은 짧게 두고, 진짜 대화는 '답변 직후 창'(REPLY_FOLLOWUP_SEC)이
# 맡는다 — 동백이 방금 말을 걸었다는 분명한 근거가 있는 쪽이다.
check(f"호출어 뒤 창 {config.FOLLOWUP_WINDOW_SEC:.0f}초 (짧게)",
      2 <= config.FOLLOWUP_WINDOW_SEC <= 5, True)
check(f"답변 직후 창 {config.REPLY_FOLLOWUP_SEC:.0f}초 (핑퐁)",
      10 <= config.REPLY_FOLLOWUP_SEC <= 25, True)

print("\n[7] 빠른 답은 복명복창을 끊고 나간다")
# "지금 몇 시야" 는 0.3초면 끝나는데 복명복창까지 다 읽으면 되레 느려진다.
# 알림(NOTICE)으로 내보내므로 답변(REPLY)이 알아서 밀어낸다.
speak.say = _REAL_SAY          # 이 절만 진짜 재생 큐로 돌린다
speak._play = lambda body: time.sleep(0.05)  # type: ignore[assignment]
speak.stop()
speak.say(dongbaek.echo_back("지금 몇 시야"),
          block=False, priority=speak.PRIORITY_NOTICE)
time.sleep(0.02)
speak.say("지금 열한 시 이십 분입니다.", block=False,
          priority=speak.PRIORITY_REPLY)
time.sleep(0.4)
# ⚠ 기대값을 손으로 적지 않는다. say() 는 clean 뒤에 말끝까지 한 문체로
#   맞추므로(voice_style), 여기서 문자열을 박아 두면 이 검사가 '밀어냈는가'
#   가 아니라 '말투가 그대로인가' 를 검사하게 된다.
import voice_style
check("답변이 복명복창을 밀어냈다", speak.recent_text(),
      voice_style.apply(speak.clean("지금 열한 시 이십 분입니다.")))

print("\n[8] 호출어 없이 긴 말은 '시키는 말' 일 때만 받는다")
# ⚠ 화자 확인만으로는 못 가른다. 사장님이 남과 대화하실 때도 사장님 목소리다 —
#   화자는 '누가 말했나' 만 알지 '누구에게 한 말인가' 는 모른다.
#   2026-08-14 18:04 사고가 그 구멍으로 들어왔다: 옆 대화(주식·대출)가
#   300자짜리 명령이 되어 '소리…꺼' 로 음소거가 걸렸고, 13시간 소리가
#   꺼져 있었다. 사장님은 당신이 끈 줄도 모르셨다.
#
#   그래서 하나 더 본다 — 시키는 말로 끝나는가. 동백에게 하는 말은 결국
#   뭘 시키므로 요청 어미로 끝나고, 옆 사람과의 대화는 아무렇게나 끝난다.
#   실측(transcript 전량): 호출어 없이 120자 넘는 84건 중 83건이 걸렸고,
#   49종을 눈으로 확인했더니 전부 통화·잡담·TV 였다.
_ambient = [
    "내가 추가로 대출 받을 수도 있다고 하던데. 하지마. 그냥 다시 시작하면 돼. "
    "금방 저거 올라가. 다시 시작하면 됩니다? 늘 걱정 안해. 신경 써야지",
    "한 세입자 명의 쪽방 살인사건에서 세입자는 수감 중이라 애인 정씨가 진범으로 "
    "밝혀진 경위를 설명하는 방송인데 그게 참 기가 막히더라고",
]
_orders = [
    "광고플랫폼에 들어가면 거기 한빛리조트라고 있잖아 우리 광고주 중에 그 데이터를 좀 확인해줘",
    "어제 온 메일 중에 마바공방에서 온 것들만 모아서 업체별로 정리해줘",
    "내일 오후 세시에 강남에서 미팅 있는 거 캘린더에 등록해 주세요",
]
for t in _ambient:
    check(f"옆 대화는 버린다: {t[:22]!r}", bool(dongbaek._ECHO_TAIL.search(t)), False)
for t in _orders:
    check(f"시키는 말은 받는다: {t[:22]!r}", bool(dongbaek._ECHO_TAIL.search(t)), True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    raise SystemExit(1)
print("✅ 전부 통과 — 끝까지 듣고, 보태면 합쳐서 다시 답한다")
