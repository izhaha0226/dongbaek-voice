#!/usr/bin/env python3
"""동백 — 맥미니 음성 비서.

  python dongbaek.py              상시 대기 (마이크 필요)
  python dongbaek.py --devices    입력 장치 목록
  python dongbaek.py --check      클로드 연결 확인
  python dongbaek.py --text "…"   마이크 없이 명령 경로만 테스트
  python dongbaek.py --file a.wav 음성 파일로 STT 테스트
  python dongbaek.py --listen     STT만 (클로드 호출 없이 받아쓰기 확인)
"""

import argparse
import difflib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import bridge
import config
import router
import speak


import call_notes  # noqa: E402
import dblog  # noqa: E402
import dbstore  # noqa: E402  (로컬 SQLite — transcript 미러. 함수 안에서 다시 import 금지: test_import_shadow)
import mail_alert  # noqa: E402  (메일 알림 설정 — 능동 알림 루프와 명령이 함께 본다)
from dblog import log  # noqa: E402  (시각을 붙여 준다. 정의는 dblog.py 한 곳)


# ─────────────────────────────────────────────────────────
# 위험 명령 게이트 (2번 방식)
# ─────────────────────────────────────────────────────────
def _log_misheard(said: str, context: str = "") -> None:
    """승인으로 인정되지 않은 응답을 모아둔다.

    whisper 는 매번 다르게 틀린다. 손으로 쫓아가는 대신 데이터로 쌓아서,
    반복되는 것만 골라 CONFIRM_MISHEARD 에 넣는다.
    """
    if not said or not said.strip():
        return
    try:
        with config.MISHEARD_LOG.open("a") as f:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "said": said.strip(),
                "context": context[:80],
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def show_misheard(limit: int = 20) -> int:
    """모인 오인식을 빈도순으로 보여준다. 목록에 추가할 후보 판단용."""
    import collections

    if not config.MISHEARD_LOG.exists():
        print("아직 수집된 오인식이 없습니다.")
        return 0
    rows = []
    for line in config.MISHEARD_LOG.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    if not rows:
        print("아직 수집된 오인식이 없습니다.")
        return 0

    cnt = collections.Counter(r["said"] for r in rows)
    known = set(config.CONFIRM_MISHEARD)
    print(f"\n승인으로 인정되지 않은 응답 — 전체 {len(rows)}건\n")
    print(f"  {'횟수':>4}  {'표기':<20} 상태")
    print("  " + "─" * 42)
    for said, n in cnt.most_common(limit):
        mark = "이미 등록됨" if any(k in said for k in known) else "← 추가 후보"
        print(f"  {n:>4}  {said[:20]:<20} {mark}")
    print(f"\n  추가하려면 config.py 의 CONFIRM_MISHEARD 에 넣으세요.")
    print(f"  원본: {config.MISHEARD_LOG}")
    return 0


def confirm_by_voice(listener, command: str, hit: str) -> bool:
    # 명령을 되읽는 건 오인식 확인용인데, 길면 사장님이 다 듣기 전에 답하고
    # 그 답은 동백가 말하는 중이라 버려진다. 앞부분만 읽는다.
    echo = command
    if len(echo) > config.CONFIRM_ECHO_MAX:
        echo = echo[: config.CONFIRM_ECHO_MAX].rsplit(" ", 1)[0] + " 등"
    # 논블로킹이어야 아래 next_utterance 가 곧바로 돌면서 끊고 들어오기가
    # 동작한다. 블로킹이면 다 말할 때까지 듣지 못해 사장님이 답을 못 낀다.
    speak.say(config.CONFIRM_QUESTION.format(command=echo), block=False)
    log(f"⚠ 위험 표현 '{hit}' 감지 — 음성 재확인 대기 {config.CONFIRM_TIMEOUT_SEC:.0f}초")

    import audio as audio_mod

    # 대답은 넷이다: 승인 / 거부 / 보류 / 못 알아들을 말.
    # 보류("조금만 기다려")를 취소로 처리하면 명령을 처음부터 다시 말해야 한다.
    # 못 알아들은 말은 오인식 후보로 기록하고 한 번만 되묻는다.
    timeout = config.CONFIRM_TIMEOUT_SEC
    asked_again = False
    strangers = 0
    while True:
        audio = listener.next_utterance(timeout=timeout)
        if audio is None:
            speak.say("응답이 없어서 취소했습니다.")
            log("취소 (무응답)")
            return False

        # 승인이야말로 화자 인증이 가장 중요한 순간이다.
        # 방송에서 나온 "진행" 이 위험 명령을 통과시키면 안 된다.
        ok, who = _speaker_ok(audio)
        if not ok:
            strangers += 1
            log(f"미등록 목소리의 응답(유사도 {who}) — 무시 ({strangers}회)")
            if strangers >= config.VOICE_VERIFY_MAX_STRANGER:
                speak.say("취소했습니다.")
                log("취소 (미등록 목소리 반복)")
                return False
            continue

        said = audio_mod.transcribe(audio)
        log(f"확인 응답: {said!r}")

        if router.is_hold(said):
            log(f"보류: {said!r} — 대기 {config.CONFIRM_HOLD_SEC:.0f}초로 연장")
            speak.say(config.CONFIRM_HOLD_MESSAGE)
            timeout = config.CONFIRM_HOLD_SEC
            continue

        if router.is_confirmation(said):
            speak.say("진행합니다.")
            return True

        if router.is_rejection(said):
            speak.say("취소했습니다.")
            log(f"취소 (응답: {said!r})")
            return False

        # 승인·거부·보류 어느 쪽도 아니다.
        # 같은 표기가 반복되면 whisper 오인식일 가능성이 높고,
        # CONFIRM_MISHEARD 에 추가할 후보가 된다. (dongbaek.py --misheard)
        _log_misheard(said, command)
        if asked_again:
            speak.say("취소했습니다.")
            log(f"취소 (응답: {said!r})")
            return False
        asked_again = True
        log(f"승인어 불명확: {said!r} — 한 번 더 물음")
        speak.say("못 알아들었습니다. 진행할까요?", block=False)
        timeout = config.CONFIRM_TIMEOUT_SEC


# ─────────────────────────────────────────────────────────
# 자기 코드 수정 → 재시작
# ─────────────────────────────────────────────────────────
# 동백은 자기 코드를 고칠 수 있는데, 고쳐도 돌고 있는 프로세스는 옛 코드다.
# 사장님은 "고쳤습니다" 라는 답만 듣고 동작은 그대로인 상태를 겪게 된다.
#
# 게다가 프로세스가 둘(데몬·텔레그램 브릿지)이고 config 는 기동 때 메모리에
# 올라가는 반면 dongbaek 은 명령이 올 때 디스크에서 새로 읽힌다. 한쪽만
# 재시작하면 새 코드가 옛 config 를 참조해 AttributeError 로 죽는다.
# 실제로 텔레그램에서 그렇게 터졌다. 그래서 둘을 항상 같이 갈아끼운다.
_LAUNCHD_LABELS = ("com.dongbaek.dongbaek", "com.dongbaek.dongbaek-telegram")
_restart_pending = False

# 전화 모드 — 0 이면 평소처럼 듣는다. 통화 중에는 해제 요청만 받는다.
_HOLD = {"until": 0.0}

# 최근에 들린 '긴 발화' 들의 시각. 잇달으면 통화로 본다.
_LONG_RUN: list[float] = []

# 동백이 마지막으로 답을 말한 시각. 그 직후에 하시는 말은 호출어가 없어도
# 동백에게 하는 말로 본다 — 방금 동백이 말을 걸었기 때문이다 (사장님 지시).
_REPLIED_AT = {"at": 0.0, "who": ""}   # who: 이 대화창을 연 사람 (H2 다자간)

# 마지막으로 "제게 하신 말씀이 아니군요" 라고 답한 시각. 이게 _REPLIED_AT
# 보다 나중이면 대화창을 열지 않는다.
_DISOWNED = {"at": 0.0}

# 마지막으로 "목소리 확인이 안 돼서 못 받았어요" 라고 되물은 시각.
_RETRY_TOLD = {"at": 0.0}


def _retry_notice_due() -> bool:
    """목소리를 못 알아봤다고 되묻는다. 잇따른 되물음은 삼킨다.

    ⚠ 되물음은 스스로 재시도 창(REPLY_FOLLOWUP_SEC)을 연다. 그래서 다음
      소리가 창 밖 방송이었어도 창 안이 되고, 또 되묻고, 또 창이 열린다 —
      되물음이 되물음을 부르는 고리다. 실측 2026-08-15 19:55: TV 드라마
      (이혼소송 장면)에 대고 9초·19초 간격으로 세 번 연달아 되물었다.
      실측 되물음 94건 중 21건이 이 고리였다.
    ⚠ 되물음 자체는 안 끈다 — 94건 중 38건은 뒤이어 명령이 통과했다.
      삼키는 건 '두 번째부터' 이고, 그때 사장님은 이미 같은 말을 들으셨다.
      창은 그대로 열어 둔다 (다시 말씀하시면 받아야 한다). 입만 다문다.
    """
    now = time.monotonic()
    if now - _RETRY_TOLD["at"] < getattr(
            config, "VOICE_RETRY_NOTICE_COOLDOWN_SEC", 30.0):
        return False
    speak.say("목소리 확인이 안 돼서 못 받았어요. 한 번만 다시 말씀해 주세요.",
              block=False, priority=speak.PRIORITY_NOTICE)
    _RETRY_TOLD["at"] = now
    return True


def _reply_window_until() -> float:
    """답변 직후 대화창(호출어 면제)이 열려 있는 끝 시각.

    ⚠ 방금 한 답이 "제게 하신 말씀이 아니군요" 였으면 창은 없다. 소리를
      냈다는 사실만으로 창이 열리면(speak.last_finished_at) 물러나 놓고
      도로 끼어드는 꼴이 된다 — 입으로는 "다시 불러주세요" 해놓고.
      2026-08-14 실측: 그 답이 28건 $30.11, 그날 비용의 4분의 1이었다.
      푸시투토크 창은 여기서 안 건드린다. 그건 사장님이 버튼으로 명시하신
      것이라 오인이 없다.
    """
    if _DISOWNED["at"] > _REPLIED_AT["at"]:
        return 0.0
    return (max(_REPLIED_AT["at"], speak.last_finished_at())
            + getattr(config, "REPLY_FOLLOWUP_SEC", 0.0))

# 미팅 모드 (2026-08-13 사장님 지시) — 입 봉인 + 기록 + 클로드 회의록.
#
# GPT 모드(2026-08-14 지시)도 같은 기계를 쓴다. "지피티와 대화하는 동안
# 동백이는 답변을 하지 않고 계속 청취하고 내용을 옵시디언에 정리" — 하는
# 일이 미팅 모드와 글자 그대로 같다. 다른 것은 정리 문서의 꼴 하나뿐이라
# 모드를 새로 만들지 않고 'kind' 만 붙인다. 침묵·청취·조용하면 자동 종료·
# 안전 상한이 전부 여기 이미 있고, 복제하면 그 넷을 두 번 관리하게 된다.
_MEETING = {"until": 0.0, "since": 0.0, "last_heard": 0.0, "kind": "미팅"}


def _meeting_active() -> bool:
    return _MEETING["until"] > 0.0


def _meeting_kind() -> str:
    return str(_MEETING.get("kind") or "미팅")


def _meeting_enter(reason: str, kind: str = "미팅") -> None:
    now = time.monotonic()
    _MEETING.update(until=now + getattr(config, "MEETING_MAX_SEC", 10800.0),
                    since=now, last_heard=now, kind=kind)
    _HOLD["until"] = 0.0               # 전화 모드보다 미팅 모드가 우선
    speak.mute(True)
    log(f"{kind} 모드 시작 ({reason}) — 완전 침묵, 청취·기록만")


def _meeting_exit(reason: str) -> None:
    """봉인을 풀고 클로드 회의록을 만든다. 정리는 스레드로 — 클로드가
    10~60초 걸리는 동안 귀가 멎으면 안 된다."""
    kind = _meeting_kind()
    _MEETING.update(until=0.0, since=0.0)
    speak.mute(False)
    log(f"{kind} 모드 종료 ({reason}) — 클로드 정리 시작")
    what = "회의록" if kind == "미팅" else "대화 내용"
    speak.say(f"{kind} 모드를 마칩니다. {what}을 정리하고 있어요.", block=False,
              priority=speak.PRIORITY_NOTICE)

    def _work():
        said, path = call_notes.save_meeting(kind=kind)
        log(f"{kind} 정리: {said[:80]}" + (f" → {path.name}" if path else ""))
        speak.say(said, block=False)
        record(source="voice", heard=f"({kind} 모드)", command=f"{kind} 정리",
               route="claude", reply=said, effective_input=0, cost_usd=0)

    threading.Thread(target=_work, daemon=True).start()


def _phone_enter(reason: str, *, owner: bool = False) -> None:
    """전화 모드 — 미팅 모드와 같은 원칙으로 입을 봉인한다.

    사장님 지시(2026-08-13): "전화통화나 회의모드일 때는 스피커로 얘기하지
    말고, 무음으로 알아서 정리해." 그 전까지는 통화 중에도 원거리 알림·
    타이머가 소리로 새어 나갔다.

    owner — 들어오는 계기가 등록 화자 확인을 이미 통과했는가
    ("여보세요", "전화 모드로 해"). 나갈 때 위키에 남길지의 근거가 된다.
    """
    call_notes.mark_owner(owner)
    _HOLD["until"] = time.monotonic() + config.PHONE_HOLD_SEC
    _HOLD["last_heard"] = time.monotonic()
    speak.mute(True)
    log(f"전화 모드 ({reason}) — 무음")


def _phone_exit(reason: str, *, summarize: bool = True) -> None:
    """봉인을 풀고, 모인 통화를 조용히 정리한다 (위키+텔레그램, 소리 없음).

    "전화내용도 취합 정리해서 옵시디언에 저장해" — 끝났다고 말하는 걸
    잊으셔도 해제되는 순간 알아서 남긴다. 소리로 읽는 건 사장님이
    "전화 끝났어" 로 직접 청하실 때뿐이다 (그 경로는 summarize=False 로
    자기가 말한다).
    """
    _HOLD["until"] = 0.0
    _HOLD["calls"] = 0
    speak.mute(False)
    log(f"전화 모드 해제 ({reason})")
    if summarize and call_notes.pending() >= 3:
        # ⚠ 사장님 목소리가 한 번도 안 섞였으면 통화가 아니라 방송이다.
        #   2026-08-14 18:35 '그것이 알고싶다' 류 재연 방송이 "통화 자동
        #   정리" 로 위키에 들어갔다 (15:12 재혼 이야기도 같은 건).
        #   전화 모드 진입 자체는 안 건드린다 — 그쪽은 무음이 되는 방향이라
        #   조이는 게 안전하다. 손대는 곳은 '남길지' 하나다.
        #   화자 인증이 꺼져 있거나 등록 지문이 없으면 _speaker_ok 가 늘
        #   통과라 이 조건도 늘 참이다 (예전 동작 그대로).
        if not call_notes.owner_heard():
            n_drop = call_notes.pending()
            call_notes.drop("사장님 목소리 없음")
            log(f"통화 자동 정리 건너뜀 — 사장님 목소리가 한 번도 안 섞였습니다 "
                f"({n_drop}조각, 방송으로 봄). 원문은 state 에 남겼습니다")
            return

        def _work():
            said, path = call_notes.save()
            log(f"통화 자동 정리: {said[:70]}" + (f" → {path.name}" if path else ""))
            record(source="voice", heard="(전화 모드)", command="통화 정리(자동)",
                   route="local", reply=said, effective_input=0, cost_usd=0)

        threading.Thread(target=_work, daemon=True).start()


def _calendar_meeting_now() -> bool:
    """지금 캘린더에 진행 중인 일정이 있는가 — 미팅 모드 자동 진입 판단."""
    try:
        import calendar_local

        from datetime import datetime

        now = datetime.now()
        for e in calendar_local.events(days=1, include_past_today=True) or []:
            if e.get("all_day"):
                continue
            st, en = e.get("start"), e.get("end")
            if st and en and st <= now <= en:
                return True
    except Exception:
        pass
    return False

# 마지막으로 거절된 발화. "방금 나야" 정정에 쓴다. 오래된 건 안 받는다 —
# 몇 분 전 TV 소리를 내 목소리로 등록하면 그게 더 큰 사고다.
_LAST_REJECT = {"audio": None, "at": 0.0}


def _self_syntax_error() -> str | None:
    """동백 자신의 .py 가 전부 파싱되는지. 문제 있으면 파일명을 돌려준다.

    깨진 코드로 재시작하면 크래시 루프에 빠지고, 그러면 "되돌려" 라고
    말할 상대조차 없어진다. 재시작 전에 반드시 통과해야 한다.

    ⚠ py_compile 을 쓰지 않는다. 바이트코드를 어딘가에 써야 하는데
      cfile=os.devnull 로 두면 "정규 파일이 아니다" 며 컴파일도 하기 전에
      OSError 를 던진다. 그걸 '읽을 수 없는 파일은 넘어가자' 는 except 가
      삼켜서 게이트 전체가 조용히 무력화돼 있었다 (실측으로 잡음).
      내장 compile() 은 파일을 만들지 않으므로 그 함정이 없다.
    """
    for p in sorted(config.ROOT.glob("*.py")):
        try:
            src = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue          # 읽을 수 없는 파일은 판단 대상이 아니다
        try:
            compile(src, str(p), "exec")
        except SyntaxError:
            return p.name
        except ValueError:
            return p.name     # 널 바이트 등 — 정상 소스가 아니다
    return None


def _arm_self_restart() -> str:
    """동백 코드가 바뀌었으니 재시작을 예약한다. 답변에 덧붙일 말을 반환."""
    global _restart_pending
    if not config.SELF_RESTART_ENABLED:
        return ""

    bad = _self_syntax_error()
    if bad:
        log(f"자기 수정 후 문법 오류 — 재시작하지 않음: {bad}")
        return f" 다만 {bad} 에 문법 오류가 있어 새 코드로 바꾸지 않았습니다."

    _restart_pending = True
    log("자기 코드 수정 감지 — 답변 뒤 재시작 예약")
    return " 새 코드로 다시 시작하겠습니다."


def restart_if_pending() -> None:
    """예약된 재시작을 실행한다. 답변을 '전달한 뒤에' 부를 것.

    지금 프로세스 안에서 죽는 것이므로, 말하기 전에 부르면 답이 잘린다.
    """
    global _restart_pending
    if not _restart_pending:
        return
    # 답변을 논블로킹으로 내보내므로 아직 말하는 중일 수 있다. 말이 끝나길
    # 기다렸다 죽는다. 상한을 두는 이유는 재생이 멈추지 않는 경우에도
    # 재시작이 영영 미뤄지면 안 되기 때문이다.
    deadline = time.monotonic() + 60
    while speak.is_speaking() and time.monotonic() < deadline:
        time.sleep(0.2)
    _restart_pending = False
    uid = os.getuid()
    script = "; ".join(
        f"launchctl kickstart -k gui/{uid}/{label}" for label in _LAUNCHD_LABELS
    )
    log("재시작합니다.")
    subprocess.Popen(
        ["bash", "-c", f"sleep 1; {script}"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


_SQUASH = re.compile(r"[^\w가-힣]+")


def _is_own_voice(text: str) -> bool:
    """마이크로 되돌아온 동백 자기 목소리인가.

    끊고 들어오기를 켜면 동백이 말하는 중에도 마이크가 열려 있다.
    에코를 사장님 말로 착각해 자기 말을 명령으로 실행하면 사고다 —
    문턱만으로 막으려 하면 반드시 뚫리므로 받아쓴 뒤 한 번 더 거른다.
    """
    recent = _SQUASH.sub("", speak.recent_text() or "")
    said = _SQUASH.sub("", text or "")
    if len(said) < 5 or not recent:
        return False
    if said in recent:
        return True
    return difflib.SequenceMatcher(None, said, recent).ratio() >= 0.65


_ECHO_TAIL = re.compile(config.ECHO_TAIL)

# 묻는 말인가. 의문사가 있거나 물음표로 끝나면 질문으로 본다.
# whisper 가 물음표를 자주 빼먹으므로(prosody.py 참조) 의문사 쪽이 본줄기다.
_ECHO_QUESTION = re.compile(
    r"(왜|뭐야|뭐가|뭐지|뭔|무슨|무엇|어떻게|어떤|어때|언제|어디|누가|누구|"
    r"얼마|몇|맞아|맞나|인가|일까|을까|ㄹ까)|\?\s*$")
_ECHO_INTENTS = [(re.compile(p), v, s) for p, v, s in config.ECHO_INTENTS]
# 요지 끝에 남은 조사. '주석 하나를' 처럼 걸리적거리는 꼬리를 정리한다.
_ECHO_PARTICLE = re.compile(r"(을|를|은|는|이|가|좀|하나|다시|한번)$")


def _strip_action(core: str, stems: tuple) -> str:
    """요지 끝에 남은 행위 낱말을 뗀다.

    붙일 동사와 뜻이 겹치면 "그 파일 삭제 삭제하겠습니다" 처럼 겹쳐 읽는다.
    낱말 단위로 보고, 다 떼서 남는 게 없으면 되돌린다.
    """
    if not stems:
        return core
    words = core.split()
    while len(words) > 1:
        last = _ECHO_PARTICLE.sub("", words[-1])
        if any(last.startswith(s) for s in stems):
            words.pop()
            continue
        break
    out = " ".join(words)
    return _ECHO_PARTICLE.sub("", out).strip() or core


def echo_back(command: str) -> str:
    """복명복창 문장. 요지만 뽑아 되읊는다.

        "이번달 매출 알려줘"  →  "네, 이번달 매출 확인하겠습니다"

    그대로 되읊으면 "네, 이번달 매출 알려줘." 가 되어 사장님 말투를 그대로
    돌려주는 꼴이다. 요청 어미를 떼고 무엇을 하려는지 붙인다.

    Claude 에 요약을 맡기지 않는다 — 실측 5.7초 · $0.034/회.
    복명복창은 즉시성이 생명이고, 하루 340건이면 $11 이 넘는다.
    """
    text = " ".join((command or "").split())
    if not text:
        return ""

    # 의도는 어미를 떼기 전 원문에서 찾는다. 대개 말끝에 있기 때문이다.
    verb, stems = config.ECHO_DEFAULT_VERB, config.ECHO_DEFAULT_STRIP
    for rx, v, s in _ECHO_INTENTS:
        if rx.search(text):
            verb, stems = v, s
            break

    # 묻는 말에는 복명복창을 붙이지 않는다.
    #   "왜 대답을 그렇게 늦게 해?" → "왜 대답을 그렇게 늦게 해 확인할게요"
    # 가 되어 말이 안 됐다 (2026-08-13 23:16 실사례). 복명복창은 '실행하기
    # 전에 잘못 들었는지' 를 확인하는 장치인데, 질문은 답 자체가 확인이라
    # 붙일 자리가 없다.
    #
    # ⚠ 의도가 잡힌 명령(등록·삭제·보내·틀어·고쳐)은 그대로 둔다 —
    #   "그거 왜 아직 있어? 지워줘" 처럼 의문사가 섞인 명령이 있고,
    #   그런 건 실행 전 확인이 오히려 더 필요하다.
    if verb == config.ECHO_DEFAULT_VERB and _ECHO_QUESTION.search(text):
        return ""

    core = _ECHO_TAIL.sub("", text).strip(" .,?!~")
    core = _strip_action(core, stems)
    if len(core) < 2:
        core = text          # 다 떼고 나니 남는 게 없으면 원문을 쓴다
    if len(core) > config.ECHO_BACK_MAX:
        core = core[: config.ECHO_BACK_MAX].rsplit(" ", 1)[0] + " 등"
    return config.pick(config.ECHO_BACK_TEMPLATES).format(echo=core, verb=verb)


def _spoken_model(model: str) -> str:
    return config.MODEL_SPOKEN.get(model, model)


def _ready_line() -> str:
    """기동 인사. 어느 모델로 도는지 함께 알린다.

    모델을 바꿔놓고 재시작을 안 해서 옛 설정으로 돌던 적이 있다.
    부팅할 때 귀로 확인되면 그런 걸 바로 알아챌 수 있다.
    """
    chat = _spoken_model(config.MODEL_CHAT)
    dev = _spoken_model(config.MODEL_DEV)
    if chat == dev:
        return f"동백 준비됐어요. 모델은 {chat}이에요."
    return f"동백 준비됐어요. 대화는 {chat}, 개발은 {dev}예요."


_HONORIFIC_TAIL = re.compile(r"(님|씨|선생|교수|박사|여사)$")


def _honorific(name: str) -> str:
    """이름 뒤에 '님' 을 붙이되, 이미 붙어 있으면 그대로 둔다.

    등록 이름이 '사장님' 이었더니 '사장님님 답변드리겠습니다' 가 나왔다.
    """
    return name if _HONORIFIC_TAIL.search(name) else f"{name}님"


# 마지막으로 응대한 사람과 그 시각. 호칭·복명복창을 붙일지 가르는 데 쓴다.
#
# 통을 둘로 나눈 이유: 복명복창은 '명령을 받은 순간' 에, 호칭은 '답을
# 내보내는 순간' 에 판정한다. 한 통을 같이 쓰면 앞엣것이 뒤엣것의 시계를
# 밀어버려 호칭이 영영 안 붙는다.
_LAST_ADDRESSED = {"who": "", "at": 0.0}
_LAST_ECHOED = {"who": "", "at": 0.0}


def _first_in_conversation(state: dict, who: str, window: float) -> bool:
    """대화의 첫 마디이거나, 말하는 사람이 바뀌었는가.

    호칭("홍길동님 답변드리겠습니다")과 복명복창("네, 매출 확인하겠습니다")이
    같은 규칙을 쓴다. 사장님 지시(2026-08-12): "복명복창 하는 것도 처음에
    한 번만. 제대로 알아듣고 있는지 확인해 보라는 차원에서 하는 거지, 계속
    연결해서 할 때마다 그렇게 할 필요는 없어."

    둘 다 '제대로 알아들었는지 확인시켜 주는' 장치다. 확인이 끝난 뒤로는
    잔소리가 되고, 무엇보다 매번 한 박자씩 늦어져 핑퐁이 깨진다.
    """
    now = time.monotonic()
    continuing = who == state["who"] and now - state["at"] < window
    state["who"], state["at"] = who, now
    return not continuing


def _should_echo(who: str) -> bool:
    """복명복창을 할 차례인가 — 첫 마디이거나 화자가 바뀌었을 때만."""
    return _first_in_conversation(_LAST_ECHOED, who,
                                  getattr(config, "ADDRESS_REPEAT_SEC", 180.0))


def _address(who: str) -> str:
    """누가 말했는지 알면 이름을 불러 답한다. "홍길동님 답변드리겠습니다. …"

    ⚠ 매번 붙이면 대화가 아니라 방송이 된다. 사장님 지시(2026-08-12):
      "이건 처음에만. 이후 대화가 연결되면 굳이 복명복창 하지 말고 자연스럽게
       이어서. 중간에 김철수이 끼어서 질문하면 그때 화자가 바뀐 걸 인식하고
       '김철수님 답변드리겠습니다' 라고 해."

      그래서 붙이는 때는 둘뿐이다.
        ① 대화를 새로 시작할 때 (한동안 조용했다가 다시 부르실 때)
        ② 말하던 사람이 바뀌었을 때 (다자간 대화에서 누구에게 하는 답인지)
      이어지는 대화에서는 안 붙인다.

    화자를 특정하지 못했거나 아직 아무도 등록 안 됐으면 붙이지 않는다 —
    모르는 사람에게 이름을 붙일 수는 없다.
    """
    if not config.ADDRESS_BY_NAME or not who:
        return ""
    if who.startswith("("):          # '(검증불가)' 같은 내부 표시
        return ""

    # 같은 사람과 대화가 이어지는 중이면 이름을 다시 부르지 않는다.
    if not _first_in_conversation(_LAST_ADDRESSED, who,
                                  config.ADDRESS_REPEAT_SEC):
        return ""
    return config.ADDRESS_TEMPLATE.format(name=_honorific(who))


# ─────────────────────────────────────────────────────────
# 명령 실행기 — 무거운 명령이 도는 동안에도 계속 듣는다
# ─────────────────────────────────────────────────────────
# 예전에는 Claude 응답을 기다리는 동안 대기 루프가 통째로 멈춰 있었다.
# 무거운 명령 하나가 3분을 쓰면 그동안 "동백아" 를 불러도 반응이 없어
# 고장 난 줄 알게 된다. 실제로 타임아웃이 하루 세 번 났다.
#
# 실행은 스레드 하나가 순서대로 처리한다. 여럿을 동시에 돌리지 않는 이유:
# 같은 Claude 세션을 공유하고 있어서, 나란히 부르면 세션 기록이 엉킨다.
_JOBS: "queue.Queue[dict]" = queue.Queue()
_MAX_PENDING = 3

# 세대(generation). 사장님이 말을 보태면 올라간다.
#
# 답변 도중에 말을 보태시면 앞 명령은 반쪽만 듣고 만든 것이라 답이 쓸모없다.
# 진행 중인 호출을 중간에 죽이긴 어려우니, 세대가 지난 답변은 말하지 않고
# 버린다. 사람이라면 "아, 그럼 다시" 하고 앞말을 접는 것과 같다.
_generation = 0
_gen_lock = threading.Lock()
_busy = False


def bump_generation() -> int:
    global _generation
    with _gen_lock:
        _generation += 1
        return _generation


def current_generation() -> int:
    with _gen_lock:
        return _generation


def is_busy() -> bool:
    """지금 명령을 처리 중인가 (대기열 포함)."""
    return _busy or not _JOBS.empty()


def submit_command(command: str, *, heard: str, who: str = "",
                   source: str = "voice", approved: bool = True) -> bool:
    """실행을 실행기에 넘긴다. 밀려 있으면 False.

    approved: 위험 게이트를 이미 통과했는지. 승인은 마이크를 쥔 쪽에서
    끝내고 결과만 넘긴다 — 실행 스레드가 마이크를 건드리면 안 된다.
    """
    if _JOBS.qsize() >= _MAX_PENDING:
        log(f"대기열이 가득 참 — 무시: {command!r}")
        return False
    _JOBS.put({"command": command, "heard": heard, "who": who,
               "source": source, "approved": approved,
               "gen": current_generation(), "at": time.monotonic()})
    return True


# 미러로 띄워 둔 메시지 — 답이 나오면 이 자리를 고쳐 채운다.
_MIRROR = {"id": None, "command": ""}


def _mirror_open(command: str) -> None:
    """명령을 알아듣자마자 폰에 띄운다 — 답을 기다리지 않는다.

    ⚠ 이게 없으면 사장님 폰은 '대화가 끝난 뒤에야' 갱신되는 것처럼 보인다.
      동백은 말이 정말 끝났는지 최대 십수 초를 이어 듣고(collect_turn),
      그 다음에야 처리를 시작하기 때문이다. 알아들은 시점과 답이 나온
      시점을 나눠 보여주면 그 사이가 침묵이 아니게 된다.
    """
    _MIRROR["id"], _MIRROR["command"] = None, command
    if not getattr(config, "TELEGRAM_MIRROR", False):
        return
    if os.path.basename(sys.argv[0] or "").startswith("test_"):
        return
    try:
        import telegram_bridge as tb

        conf = tb.load_conf()
        if not conf or not conf.get("allowed_chat_ids"):
            return
        _MIRROR["id"] = tb.send_text(
            conf["bot_token"], conf["allowed_chat_ids"][0],
            f"🎙 {command}\n⏳ 처리 중…")
    except Exception:
        pass


def _mirror_send(command: str, reply: str, source: str = "voice") -> None:
    """답이 나왔다. 띄워 둔 메시지를 고쳐 채운다 (없으면 새로 보낸다).

    실패는 조용히 버린다 — 폰에 기록을 남기자고 음성 응답이 늦거나
    죽으면 본말전도다."""
    if not getattr(config, "TELEGRAM_MIRROR", False):
        return
    try:
        import telegram_bridge as tb

        conf = tb.load_conf()
        if not conf or not conf.get("allowed_chat_ids"):
            return
        token, chat = conf["bot_token"], conf["allowed_chat_ids"][0]
        # 누가 물었는지 표시를 가른다 — 음성은 🎙, 그 외(제어서버·CLI·
        # 개발 시험)는 🔧. 사장님이 안 한 질문이 🎙 로 찍히면 "내가
        # 언제 물었지" 가 된다 (2026-08-13 실사례 — 시험 호출이 그랬다).
        icon = "🎙" if source == "voice" else f"🔧({source})"
        body = f"{icon} {command}\n{reply}"
        mid = _MIRROR["id"] if _MIRROR["command"] == command else None
        if mid and tb.edit_text(token, chat, mid, body):
            _MIRROR["id"] = None
            return
        tb.send_text(token, chat, body)
    except Exception:
        pass


def _mirror_to_telegram(command: str, reply: str, source: str = "voice") -> None:
    """문답을 텔레그램에도 남긴다 — 자동 동기화.

    전송은 스레드로 뗀다 (네트워크 1~2초를 실행기가 물면 다음 명령이 늦는다).
    테스트 실행(test_*)에서는 보내지 않는다 — 몽키패치된 가짜 문답이
    실제 폰으로 날아간다. record() 의 오염 방지와 같은 가드다.
    """
    if os.path.basename(sys.argv[0] or "").startswith("test_"):
        return
    threading.Thread(target=_mirror_send, args=(command, reply, source),
                     daemon=True).start()


def _due_meetings(evs, now_dt, announced: set) -> list[tuple[str, str]]:
    """(중복 방지 키, 말할 문장) — 곧 시작하는데 아직 안 알린 일정.

    종일 일정은 '임박' 개념이 없어 건너뛴다.

    ⚠ 한 번의 호출 안에서도 같은 키를 두 번 담지 않는다. `announced` 는
      호출측이 말한 뒤에야 갱신되므로, 여기서 거르지 않으면 **똑같은 일정이
      달력에 N개 있을 때 N번 말한다.** 2026-08-13 15:50 실측: 같은 제목·같은
      시각 일정 4개에 "9분 뒤 큐 이확인 일정입니다" 를 4초간 4번 말했다.
      같은 날 내일치에는 36개가 쌓여 있었다 (음성으로 잘못 등록된 것).
      달력을 정리해도 또 생길 수 있으니 막는 건 이쪽이다.
    """
    out = []
    seen: set = set()          # 이번 호출 안에서 이미 담은 키
    for e in evs or []:
        if e.get("all_day"):
            continue
        delta = (e["start"] - now_dt).total_seconds()
        if 0 < delta <= config.NUDGE_MEETING_MIN * 60:
            key = f"{e['title']}|{e['start'].isoformat()}"
            if key not in announced and key not in seen:
                seen.add(key)
                m = max(1, int(delta // 60))
                out.append((key, f"{m}분 뒤 {e['title']} 일정입니다."))
    return out


def _nudge_loop() -> None:
    """상황 능동 — 동백이 먼저 말을 거는 유일한 스레드.

    일정 임박 알림과 VIP 메일 알림. 전화 모드 중에는 입을 다문다.
    실패해도 루프는 계속 돈다 — 능동 기능이 데몬을 죽이면 안 된다.
    """
    announced: set = set()
    seen_mail: set = set()
    next_mail = 0.0
    mem_tick = 0
    while True:
        time.sleep(config.NUDGE_CHECK_SEC)
        # 로그 회전은 전화 모드 검사보다 '앞' 이어야 한다 — 통화 중이라고
        # 로그가 안 자라는 것은 아니다. 아래 continue 뒤에 두면 통화가 길수록
        # 정작 회전이 필요한 때 안 돈다.
        try:
            if dblog.rotate(config.STATE / "daemon.log"):
                log("로그가 커져서 최근 절반만 남겼습니다 (이전분은 daemon.log.1)")
        except Exception:
            pass
        try:
            # 미팅 모드 — 오래 조용하면 끝난 것이다. 자동으로 정리한다
            # (끝났다고 말하는 걸 잊으셔도 회의록은 나온다).
            #
            # ⚠ GPT 모드는 예외다. 사장님 지시(2026-08-14): "지피티모드
            #   들어가면 내가 지피티모드 해제할때까지 절대로 임의로 끝내지마."
            #   지피티와의 대화는 답을 읽는 동안 몇 분씩 조용해진다 — 그때
            #   동백이 혼자 끝내고 입을 열면 대화 한복판에 끼어드는 꼴이다.
            #   여는 것은 사장님 말씀 한 가지뿐이다.
            if _meeting_active() and _meeting_kind() != "지피티":
                quiet = time.monotonic() - _MEETING["last_heard"]
                if quiet >= getattr(config, "MEETING_QUIET_EXIT_SEC", 900.0):
                    _meeting_exit(f"{int(quiet // 60)}분 조용 — 자동")
                continue                       # 미팅 중 — 어떤 알림도 안 낸다
            if _HOLD["until"] and time.monotonic() < _HOLD["until"]:
                # 30초간 말이 없으면 통화가 끝난 것이다 (사장님 지시) —
                # "전화 끝났어" 를 안 하셔도 조용히 정리하고 귀를 연다.
                quiet_c = time.monotonic() - _HOLD.get("last_heard", 0.0)
                if quiet_c >= getattr(config, "CALL_QUIET_EXIT_SEC", 30.0):
                    _phone_exit(f"{int(quiet_c)}초 조용 — 종료로 인지")
                continue                       # 통화 중 — 끼어들지 않는다
            import calendar_local

            for key, line in _due_meetings(calendar_local.events(days=1),
                                           datetime.now(), announced):
                announced.add(key)
                log(f"일정 알림: {line}")
                speak.say(line, block=False)
                try:
                    import briefing

                    briefing._to_telegram("⏰ 일정 알림", line)
                except Exception:
                    pass

            # 메모리 지킴이 — 부족하면 허용목록만 정리하고 보고한다
            mem_tick += 1
            if (getattr(config, "MEMGUARD_ENABLED", False)
                    and mem_tick >= config.MEMGUARD_CHECK_EVERY):
                mem_tick = 0
                import memory_guard

                if memory_guard.needs_reclaim():
                    acts = memory_guard.reclaim("자동")
                    if acts:
                        line = "메모리 부족 — " + ", ".join(acts)
                        log(f"메모리 지킴이: {line}")
                        try:
                            import briefing

                            briefing._to_telegram(
                                "🧠 메모리 지킴이",
                                line + "\n" + memory_guard.status_speak())
                        except Exception:
                            pass

            # 메일 알림 — 무엇을 알릴지는 mail_alert 가 정한다 (사장님 지시
            # 2026-08-16 "메일 들어오면 알려줘"). 예전엔 VIP 목록이 비어 있으면
            # 아예 안 돌았고, 그 목록이 실제로 비어 있어 한 번도 울린 적이 없다.
            if mail_alert.mode() != mail_alert.OFF and time.monotonic() >= next_mail:
                next_mail = time.monotonic() + config.MAIL_NUDGE_CHECK_SEC
                import mail_local

                first_pass = not seen_mail
                for m in (mail_local.received_brief(hours=1, max_scan=10) or []):
                    key = m["from"] + "|" + m["subject"]
                    if key in seen_mail:
                        continue
                    seen_mail.add(key)
                    # ⚠ 처음 도는 판은 알리지 않는다. 데몬을 띄운 순간 지난
                    #   한 시간치가 전부 '새 메일' 로 보여 한꺼번에 쏟아진다.
                    if first_pass:
                        continue
                    if not mail_alert.should_alert(m["from"], m.get("addr", ""),
                                                   m["subject"]):
                        continue
                    said = mail_alert.line(m["from"], m["subject"])
                    log(f"메일 알림({mail_alert.mode()}): {said}")
                    speak.say(said, block=False)
                    # 밖에 계실 때는 소리가 소용없다 — 폰으로도 보낸다.
                    try:
                        import briefing

                        briefing._to_telegram("📬 새 메일",
                                              f"{m['from']}\n{m['subject']}")
                    except Exception:
                        pass
        except Exception as e:
            log(f"능동 알림 오류: {type(e).__name__}: {e}")


def _run_jobs() -> None:
    global _busy
    while True:
        job = _JOBS.get()
        _busy = True
        # ⚠ 묵은 명령은 버린다. 밀린 걸 순서대로 처리하면 10분 전 얘기를
        #   지금 답하게 된다 — 그건 대화가 아니라 배치 작업이다.
        age = time.monotonic() - job.get("at", 0)
        if age > getattr(config, "JOB_MAX_AGE_SEC", 45.0):
            log(f"{age:.0f}초 묵은 명령 — 버림: {job['command'][:30]!r}")
            _JOBS.task_done()
            continue

        try:
            # 답변이 다 만들어지기 전에 문장 단위로 말한다.
            # 브릿지가 글자를 흘려주지 않으면(BRIDGE="cli") 아무것도 안 먹으므로
            # 저절로 꺼진 것과 같다 — spoke() 가 False 로 남아 아래 평소 경로를 탄다.
            speaker = speak.Stream(lead=_address(job["who"])) if config.STREAM_REPLY else None

            with Ack():
                reply = handle(
                    job["command"],
                    confirm=lambda c, h: job["approved"],
                    source=job["source"],
                    heard=job["heard"],
                    speaker=speaker,
                    who=job.get("who", ""),
                )

            if job["gen"] != current_generation():
                # 처리하는 사이에 말을 보태셨다. 이 답은 반쪽짜리 질문에
                # 대한 것이라 버린다 — 합쳐진 명령이 뒤이어 처리된다.
                # 스트리밍으로 이미 말하기 시작했으면 지금 나가는 소리도 끊는다.
                if speaker and speaker.spoke():
                    speak.stop()
                log("말을 보태셔서 앞 답변은 버림")
            else:
                if speaker:
                    speaker.close()     # 버퍼에 남은 마지막 문장
                if speaker and speaker.spoke():
                    delivered = speaker.spoken   # 이미 말했다. 다시 말하면 두 번 들린다.
                elif reply:
                    # 논블로킹으로 내보낸다. 블로킹이면 재생이 끝날 때까지
                    # 다음 명령이 시작되지 않는다.
                    speak.say(_address(job["who"]) + reply, block=False)
                    delivered = reply
                else:
                    delivered = ""
                # 음성 문답은 텔레그램 채팅에도 남긴다 (자동 동기화).
                # 텔레그램發 명령은 제외 — 이미 그 채팅에 있다.
                if delivered and job["source"] == "voice":
                    # 답을 말한 시각 — 이 직후의 말은 호출어를 생략해도 된다.
                    # 창 주인도 함께 적는다 — 다른 분 말씀은 이 창을 못 쓴다 (H2).
                    # (텔레그램 미러는 record() 가 한다 — 2026-08-13 부터
                    #  '대답한 것 전부' 로 넓히면서 기록소 한 곳으로 모았다)
                    #
                    # ⚠ 단, "저한테 하신 말씀이 아닌 것 같아요" 라고 답했으면
                    #   창 대신 물러난 시각을 적는다 (_reply_window_until 참고).
                    if (getattr(config, "DISOWN_CLOSES_WINDOW", True)
                            and router.is_disown_reply(delivered)):
                        _DISOWNED["at"] = time.monotonic()
                        log("제게 한 말이 아니라고 답함 — 대화창은 안 연다")
                    else:
                        _REPLIED_AT["at"] = time.monotonic()
                        _REPLIED_AT["who"] = job.get("who", "")
            # 말이 끝난 뒤에 갈아탄다. 먼저 재시작하면 답이 잘린다.
            restart_if_pending()
        except Exception as e:                     # 실행기는 죽으면 안 된다
            log(f"실행 오류: {type(e).__name__}: {e}")
            speak.say("처리 중 문제가 생겼습니다.", block=False)
        finally:
            _busy = False


def collect_turn(listener, first_text: str, woke: bool = True) -> str:
    """사장님 말이 정말 끝날 때까지 이어 듣고 한 덩어리로 만든다.

    침묵 1.4초를 무조건 '끝' 으로 보면 생각하느라 쉬는 사이에 잘린다.
    잘린 반쪽을 명령으로 받아 답하기 시작하니 끼어드는 것처럼 느껴진다.
    문장이 안 끝난 것처럼 보이면 더 오래 기다린다.

    woke — 이 턴이 호출어로 시작했는가. 상한을 여기서 가른다.

      부르고 길게 설명하시는 건 끝까지 담는다. 사장님 지시(2026-08-11):
      "내가 1분 이상 얘기할 수도 있고 2분이 될 수도 있어. 그걸 네가 다
       듣고 한 번에 대답할 수 있도록 해줘."

      안 부르셨는데 길게 들리는 건 통화다. 실측 2026-08-12 22:17 —
      18조각 182초 916자를 모아 통째로 버렸고, 그 안에 사장님이 부르신
      "중간에 내가 너 불러도 대답해" 가 삼켜져 있었다. 사장님께는
      "불러도 대답을 안 한다" 로 보였다.

      길이로 가르면 두 지시가 충돌한다. 호출어로 가르면 둘 다 지켜진다.
    """
    if not config.TURN_ENABLED:
        return first_text

    import audio as audio_mod

    parts = [first_text]
    started = time.monotonic()
    deadline = started + config.TURN_MAX_SEC
    cued = False                       # 경청 신호(H4)는 턴당 한 번만
    while time.monotonic() < deadline:
        # 이미 길게 말씀하고 계시면 더 오래 기다린다. 문장이 완결될 때마다
        # 1.2초에 끊으면 긴 설명이 토막 난다.
        elapsed = time.monotonic() - started
        long_turn = (len(parts) >= config.TURN_LONG_AFTER_PARTS
                     or elapsed >= config.TURN_LONG_AFTER_SEC)
        if router.looks_unfinished(parts[-1]):
            wait = config.TURN_WAIT_UNFINISHED_SEC
        elif long_turn:
            wait = config.TURN_WAIT_LONG_SEC
        elif router.looks_complete(parts[-1]):
            # H1 의미 완결 — "…해줘"/"…이야?" 처럼 끝난 게 분명한 한마디는
            # 덜 기다린다 (1.4→0.8초). 긴 이야기 중 문장 완결은 여기 안
            # 온다(위 long_turn 이 먼저다) — 설명을 토막 내지 않는다.
            wait = getattr(config, "TURN_WAIT_COMPLETE_SEC", config.TURN_WAIT_SEC)
        else:
            wait = config.TURN_WAIT_SEC

        # H4 경청 신호 — 길게 말씀하시는 중 미완결 쉼이 오면 "네" 한 번.
        # 미완결일 때만 낸다: 완결 쉼에 내면 "말 끝났지?" 재촉으로 들린다.
        # 사용자가 이어 말하면 barge-in 이 이 소리를 끊는다 — 겹침 걱정 없음.
        if (getattr(config, "LISTEN_CUE_ENABLED", False) and not cued
                and elapsed >= getattr(config, "LISTEN_CUE_AFTER_SEC", 8.0)
                and len(parts) >= 2 and router.looks_unfinished(parts[-1])):
            speak.say(config.LISTEN_CUE_TEXT, block=False,
                      priority=speak.PRIORITY_NOTICE)
            cued = True

        more = listener.next_utterance(timeout=wait)
        if more is None:
            break                       # 말이 끊겼다 = 진짜 끝
        text = audio_mod.transcribe(more)
        if not text or _is_own_voice(text):
            continue
        # 영상·방송 상투구 조각은 명령에 붙이지 않는다 — 유튜브 끝맺음이
        # "일정 전부 삭제해" 와 합쳐져 키워드를 오염시킨 실사례 (09:02).
        if router.is_media_noise(text):
            log(f"영상 소리 조각 — 안 붙임: {text[:24]!r}")
            continue
        # ⚠ 호출어가 들리면 즉시 멈춘다. 그건 "지금 나한테 답해라" 는 뜻이지
        #   앞말에 이어 붙일 말이 아니다.
        #
        #   실측 2026-08-12 22:17 — 사장님이 통화 중에 "동백아" 하고 부르셨는데,
        #   그 말이 18조각·916자 덩어리에 삼켜졌고 덩어리째 "너무 긴 명령" 으로
        #   버려졌다. 사장님께는 "불러도 대답을 안 한다" 로 보였다.
        #   실제로 그 덩어리 안에 "중간에 내가 너 불러도 대답해" 가 들어 있었다.
        #
        #   모으던 것은 버린다. 부르셨다는 건 앞엣것이 아니라 지금부터가
        #   중요하다는 뜻이다.
        if router.match_wake(text) is not None:
            log(f"모으는 중에 부르심 — 그만 모으고 새로 받습니다: {text[:30]!r}")
            return text
        parts.append(text)
        log(f"이어 말함 ({len(parts)}조각, {elapsed:.0f}초째): {text[:40]!r}")
        # ⚠ 여기서 멈추지 않으면 통화가 통째로 명령이 된다.
        #
        #   실측 2026-08-12 22:10 — "10조각, 182초". 사장님이 전화 통화를
        #   하시는 3분 내내 동백이 그 말을 한 덩어리로 모아 클로드에 보냈다.
        #   TURN_MAX_SEC(180초)이 유일한 상한이었고 조각 수·글자 수 상한은
        #   아예 없었다. 사장님께는 "묻는 질문에 답을 하나도 안 한다" 로
        #   보였다 — 사실은 통화 내용을 붙들고 3분을 기다리고 있었다.
        #
        #   동백에게 하는 명령은 짧다. 길어지면 동백에게 하는 말이 아니다 —
        #   FREEPASS_MAX_CHARS(70자)와 같은 판단이고, 그건 이미 여러 번
        #   사고를 막아 왔다.
        # 부르고 말씀하시는 중이면 끝까지 담는다. 안 부르셨으면 조인다.
        if woke:
            continue
        joined = " ".join(parts)
        if len(parts) >= config.TURN_MAX_PARTS:
            log(f"그만 모음 — 호출어 없이 {len(parts)}조각 (통화로 보임)")
            break
        if len(joined) >= config.TURN_MAX_CHARS:
            log(f"그만 모음 — 호출어 없이 {len(joined)}자 (통화로 보임)")
            break
    if len(parts) > 1:
        log(f"한 덩어리로 받음 — {len(parts)}조각, "
            f"{time.monotonic() - started:.0f}초")
    return " ".join(parts)


def _log_voice_score(name, score) -> None:
    """판정 점수를 남긴다 — 문턱(0.45)은 보수적 시작점이라 실측으로 조정한다.

    며칠 쓰면 본인 점수 분포와 타인 점수 분포가 이 파일에 갈라져 보인다.
    그 사이에 문턱을 놓는 게 감으로 잡는 것보다 낫다. 테스트 실행은
    기록하지 않는다 (record 의 오염 가드와 같은 원리).
    """
    if os.path.basename(sys.argv[0] or "").startswith("test_"):
        return
    try:
        with (config.STATE / "voice_scores.jsonl").open("a") as f:
            f.write(json.dumps(
                {"ts": datetime.now().isoformat(timespec="seconds"),
                 "name": name, "score": round(float(score), 3)},
                ensure_ascii=False) + "\n")
    except (OSError, ValueError):
        pass


def _speaker_ok(audio, bank: bool = True) -> tuple[bool, str]:
    """(통과 여부, 화자 이름 또는 유사도).

    등록된 지문이 없으면 무조건 통과 — 설치 직후에도 동백이 동작해야 하고,
    --enroll 하는 순간부터 잠긴다. 모델이 죽었을 때도 통과다.
    벽돌이 되는 것보다 열려 있는 게 낫고, 상태는 로그로 드러난다.

    bank — 거절 시 '남' 지문으로 담을지. 방금 호명/답변 창 안의 발화는
    본인일 개연성이 높아 담지 않는다 (2026-08-13 05:42 실사례: "동백아" →
    "네" 직후의 새벽 목소리 0.341 이 남으로 박혀 영구차단 코호트에
    들어갔다 — 손으로 꺼냈다).
    """
    if not config.VOICE_VERIFY_ENABLED:
        return True, ""
    import voiceprint

    if not voiceprint.enrolled():
        return True, ""
    voiceprint.LAST["reason"] = ""
    name, score = voiceprint.verify(audio)
    _log_voice_score(name, score)
    if name is None:
        # 거절된 목소리를 '남' 으로 기억해 다음부터 더 확실히 가른다.
        # 동시에 원본을 잠깐 들고 있는다 — 오인이었다면 "방금 나야" 로
        # 되살려야 하기 때문이다 (voiceprint.forgive).
        _LAST_REJECT["audio"], _LAST_REJECT["at"] = audio, time.monotonic()
        if bank:
            try:
                voiceprint.remember_stranger(audio, score)
            except Exception:
                pass
    if name is not None:
        # 확실하게 본인일 때만 지문으로 되먹인다 — 목소리는 날마다 조금씩
        # 달라지고, 등록 때 읽은 다섯 문장은 그날치일 뿐이다.
        try:
            voiceprint.adapt(name, audio, score)
        except Exception:
            pass                # 학습 실패로 명령이 막히면 본말전도다
        return True, name
    # 왜 거절됐는지를 점수에 붙인다 (문턱미달/동률/남과근접) — 새벽 잠금
    # 사고 때 이게 없어 세 층 중 어디가 막았는지 못 갈랐다.
    why = voiceprint.LAST.get("reason", "")
    return False, f"{score:.2f}" + (f"·{why}" if why else "")


def _skip_transcribe(audio) -> bool:
    """받아쓰기 전에 버려도 되는 소리인가 — '확실히 남' 일 때만 True.

    받아쓰기가 화자확인보다 80배 비싸다 (실측 1474ms vs 18.5ms). 그런데
    TV 소리도 통화 상대 목소리도 whisper-large 를 완주한 뒤에야 "호출어 없음"
    으로 버려졌다. 싼 검사를 먼저 하면 되는 일이었다.

    ⚠ fail-open 이다. 애매하면 통과시킨다. 여기를 하드 게이트로 만들면
      감기 든 날 사장님 목소리가 '남' 으로 박혀 동백이 귀를 잃는다.
      그래서 _speaker_ok 의 문턱(0.45)보다 확실히 아래일 때만 버린다 —
      버리는 것은 _speaker_ok 도 어차피 거절했을 소리의 부분집합이다.
      판정 자체는 하나도 안 바뀌고, 순서만 당겨진다.
    """
    # 새벽 원거리 모드에는 버리지 않는다 — 먼 방의 작은 목소리는 유사도가
    # 깎여서, 여기서 버리면 부르는 것 자체가 안 닿는다 (2026-08-13 지시).
    if getattr(config, "DAWN_PREGATE_OFF", False) and config.dawn_far_active():
        return False
    if not getattr(config, "PREGATE_ENABLED", False):
        return False
    if not config.VOICE_VERIFY_ENABLED:
        return False
    try:
        import voiceprint

        # ⚠ 등록된 지문이 없으면 verify 는 (None, 0.0) 을 준다. 그 0.0 을
        #   '남' 으로 읽으면 모든 소리를 버려 완전히 귀머거리가 된다.
        if not voiceprint.enrolled():
            return False
        name, score = voiceprint.verify(audio)
        # 아는 사람이거나 '(검증불가)'(모델이 죽음) 면 통과다.
        if name is not None:
            return False
        if score >= config.PREGATE_STRANGER_MAX:
            return False                # 문턱 근처는 애매하다 — 통과
    except Exception:
        return False                    # 판단 못 하면 통과

    # 확실한 남이다. _speaker_ok 가 하던 뒷정리를 그대로 한다 — 오인이었다면
    # "방금 나야" 로 되살려야 하고(forgive), 남 지문 코호트도 먹어야 한다.
    #
    # ⚠ 점수 기록이 특히 중요하다. 이 거르개를 만든 첫날, verify() 를 직접
    #   부르면서 _log_voice_score 를 빼먹었다. 그래서 거르개가 처리한 발화는
    #   voice_scores.jsonl 에 하나도 안 남았다 — 문턱을 정할 때 근거로 쓴
    #   바로 그 파일이고, test_pregate 의 표류 감시가 읽는 파일이다.
    #   거르개가 많이 일할수록 판단 근거가 사라지는 구조였다.
    _log_voice_score(name, score)
    _LAST_REJECT["audio"], _LAST_REJECT["at"] = audio, time.monotonic()
    try:
        voiceprint.remember_stranger(audio, score)
    except Exception:
        pass
    log(f"남 목소리 — 받아쓰기 건너뜀 (유사도 {score:.2f})")
    return True


def show_voices() -> int:
    import voiceprint

    people = voiceprint.enrolled()
    if not people:
        print("등록된 목소리가 없습니다 — 누구의 말이든 실행됩니다.")
        print("등록: dongbaek.py --enroll   (기본 이름: 사장님)")
        return 0
    learned = voiceprint.learned()
    print("등록된 목소리 (지문이 많을수록 안정적):")
    for name, n in people.items():
        extra = learned.get(name, 0)
        tail = f" + 학습 {extra}개" if extra else ""
        print(f"  {name}: 지문 {n}개{tail}")
    print(f"\n판정 문턱: {config.VOICE_VERIFY_THRESHOLD} (config.VOICE_VERIFY_THRESHOLD)")
    return 0


def enroll_by_voice(listener, name: str) -> str:
    """데몬이 마이크를 쥔 채로 목소리를 등록한다. 음성으로 읽을 문장을 반환.

    별도 프로세스로 --enroll 을 돌리면 같은 입력 장치를 두고 다투고,
    사장님이 터미널 앞에 앉아 있어야 한다. 말로 시키는 게 맞다.
    """
    import voiceprint

    if not voiceprint.preload():
        return "화자 인증 모델을 불러오지 못했습니다."

    # 이름을 못 알아들었으면 먼저 묻는다
    if not name:
        speak.say("등록할 이름을 말씀해 주세요.")
        audio = listener.next_utterance(timeout=config.ENROLL_TIMEOUT_SEC)
        if audio is None:
            return "이름을 못 들어서 등록을 멈췄습니다."
        import audio as audio_mod

        name = router.clean_name(audio_mod.transcribe(audio))
        if not name:
            return "이름을 알아듣지 못했습니다. 다시 말씀해 주세요."

    total = len(config.ENROLL_SENTENCES)
    speak.say(f"{name} 목소리를 등록하겠습니다. {total}문장을 따라 말씀해 주세요.")
    log(f"목소리 등록 시작: {name}")

    done = miss = 0
    while done < total:
        speak.say(f"{done + 1}번. {config.ENROLL_SENTENCES[done]}")
        audio = listener.next_utterance(timeout=config.ENROLL_TIMEOUT_SEC)
        if audio is None or not voiceprint.enroll_sample(name, audio):
            miss += 1
            log(f"등록 {done + 1}/{total} 실패 ({miss}회)")
            if miss >= config.ENROLL_MAX_MISS:
                return f"{done}문장만 받고 멈췄습니다. 마이크를 확인하고 다시 불러주세요."
            speak.say("못 들었습니다. 한 번만 더요.", priority=speak.PRIORITY_NOTICE)
            continue
        miss = 0
        done += 1
        log(f"등록 {done}/{total} 저장")

    # 등록 직후 실측 — 문턱이 맞는지는 숫자로 확인해야 안다
    speak.say("확인하겠습니다. 아무 말이나 한 문장 해주세요.")
    audio = listener.next_utterance(timeout=config.ENROLL_TIMEOUT_SEC)
    people = voiceprint.enrolled()
    roster = ", ".join(f"{k} {v}개" for k, v in people.items())
    log(f"등록 완료 — {roster}")

    if audio is None:
        return f"{name} 목소리를 등록했습니다. 확인은 못 했습니다."
    who, score = voiceprint.verify(audio)
    log(f"등록 확인: {who or '미등록'} 유사도 {score:.2f} "
        f"(문턱 {config.VOICE_VERIFY_THRESHOLD})")
    if who:
        return f"{name} 목소리를 등록했고 확인도 됐습니다. 유사도는 {score:.2f}입니다."
    return (f"{name} 목소리를 등록했지만 확인에서 {score:.2f}로 문턱에 못 미쳤습니다. "
            f"한 번 더 등록하시면 정확해집니다.")


def do_enroll(name: str) -> int:
    """터미널에서 등록. 데몬이 안 떠 있을 때 쓴다."""
    import audio as audio_mod
    import voiceprint

    print(f"'{name}' 목소리 등록을 시작합니다. 모델 준비 중…")
    if not voiceprint.preload():
        print("✗ 화자 인증 모델을 불러오지 못했습니다. (최초 1회는 다운로드가 필요합니다)")
        return 1

    sentences = config.ENROLL_SENTENCES
    with audio_mod.Listener() as listener:
        done = 0
        while done < len(sentences):
            print(f"\n[{done + 1}/{len(sentences)}] 이렇게 말해보세요: \"{sentences[done]}\"")
            audio = listener.next_utterance(timeout=config.ENROLL_TIMEOUT_SEC)
            if audio is None:
                print("  …못 들었습니다. 다시 한 번.")
                continue
            if not voiceprint.enroll_sample(name, audio):
                print("  …임베딩 실패. 다시 한 번.")
                continue
            done += 1
            print("  ✓ 저장")

        # 등록 직후 본인 목소리 실측 — 문턱 조정의 근거가 된다
        print("\n확인차 아무 말이나 한 번 더 해보세요.")
        audio = listener.next_utterance(timeout=config.ENROLL_TIMEOUT_SEC)
        if audio is not None:
            who, score = voiceprint.verify(audio)
            mark = "통과" if who else "차단"
            print(f"  판정: {who or '미등록'} (유사도 {score:.2f}, 문턱 "
                  f"{config.VOICE_VERIFY_THRESHOLD}) → {mark}")
            if not who:
                print("  ⚠ 본인인데 차단됐다면 config.VOICE_VERIFY_THRESHOLD 를 낮추거나"
                      " --enroll 로 지문을 더 쌓으세요.")

    people = voiceprint.enrolled()
    print(f"\n등록 완료. 현재: " + ", ".join(f"{k} {v}개" for k, v in people.items()))
    print("지금부터 등록된 목소리만 명령으로 받습니다.")
    return 0


def confirm_by_text(command: str, hit: str) -> bool:
    print(f"\n⚠ 위험 표현 '{hit}' 감지")
    print(f"  실행 대상: {command}")
    ans = input("  진행하려면 '진행' 입력 (그 외 전부 취소): ").strip()
    return router.is_confirmation(ans)


# ─────────────────────────────────────────────────────────
# 명령 처리
# ─────────────────────────────────────────────────────────
# 마지막으로 수정한 저장소. "되돌려" 가 어디를 되돌릴지 알아야 한다.
_last_repo: str | None = None

_UNDO = re.compile(r"(되돌|돌려놔|취소해|원래대로|복구|undo|롤백해)")
_CODE_EDIT = re.compile(
    r"(코드|파일|함수|클래스|변수|주석|테스트|스타일|css|html|import|버그)"
    r"|리팩[토터]|refactor|커밋|commit"
)


def _remember_repo(repo: str) -> None:
    """'되돌려' 가 어디를 되돌릴지 기억해둔다."""
    global _last_repo
    _last_repo = repo


def _is_undo(text: str) -> bool:
    """되돌리기 요청인가. 일정 취소 같은 건 제외한다."""
    if any(w in text for w in ("일정", "미팅", "약속", "회의", "메일")):
        return False
    m = _UNDO.search(text)
    if not m:
        return False
    # "왜 자꾸 되돌려??" (2026-08-13 15:17) 는 내 행동에 대한 항의지
    # 명령이 아닌데 롤백을 실행해버렸다. 같은 절에 '왜/자꾸'가 있으면
    # 질문/불만으로 보되, 명령형 꼬리(줘/놔/해라)가 붙으면 명령으로 믿는다
    # — "네가 자꾸 바꾸는데 되돌려줘" 는 명령이다.
    clause = re.split(r"[.?!…]", text[: m.start()])[-1]
    imperative = re.search(r"줘|주세요|놔|해라", text[m.start():])
    if ("자꾸" in clause or "왜" in clause) and not imperative:
        return False
    return True


def _is_code_edit(text: str) -> bool:
    return bool(_CODE_EDIT.search(text))


def _guess_target(text: str) -> str:
    """명령에서 대상 저장소를 추측한다.

    "myshop-site 헤더 고쳐줘" → ~/projects/myshop-site
    못 찾으면 동백 자신을 기준으로 한다 (최소한 git 저장소이므로 안전망이 산다).
    """
    from pathlib import Path

    projects = Path(config.PROJECT_ROOT)
    if not projects.exists():
        return str(config.ROOT)

    norm = text.replace(" ", "").lower()
    flat = norm.replace("-", "").replace("_", "")

    # ① 한국어로 부른 이름부터 본다. 음성은 폴더명(영문)을 그대로 말하지
    #    않는다 — "광고플랫폼" 라고 하지 "my-ads" 라고 하지 않는다.
    #    긴 별칭부터 봐야 '광고플랫폼오에스' 가 '광고플랫폼' 에 먼저
    #    잡히지 않는다.
    for alias in sorted(config.REPO_ALIASES, key=len, reverse=True):
        if alias.replace(" ", "").lower() in flat:
            target = projects / config.REPO_ALIASES[alias]
            if target.is_dir():
                return str(target)

    # ② 폴더 이름을 그대로 말한 경우
    #    이름이 긴 것부터 봐야 'ads-platform' 이 'ads' 보다 먼저 잡힌다
    for d in sorted((p for p in projects.iterdir() if p.is_dir()),
                    key=lambda p: -len(p.name)):
        if d.name.lower().replace("-", "").replace("_", "") in flat:
            return str(d)
    return str(config.ROOT)


def record(**fields) -> None:
    """동백이 무엇을 듣고 무엇을 했는지 한 줄씩 남긴다.

    데몬 콘솔은 창을 닫으면 사라지고, tokens.jsonl 은 비용만 담는다.
    '무슨 일이 있었나'를 나중에 되짚으려면 이 기록이 필요하다.
    """
    # 테스트(test_*.py)가 몽키패치로 부른 것까지 남기면 실사용 통계가 오염된다.
    # 실제로 transcript 반복 명령 상위가 전부 테스트 문장이었다 ("무거운 첫 명령"
    # 141회짜리 주석 명령 등). 진짜 기록만 남아야 '로컬로 몇 건 막았나'가 보인다.
    if os.path.basename(sys.argv[0] or "").startswith("test_"):
        return
    fields["ts"] = datetime.now().isoformat(timespec="seconds")
    # 정본은 DB 다 (사장님 지시 2026-08-16 "db를 정본으로 해").
    #
    # 전에는 jsonl 에 먼저 쓰고 DB 에 미러했다. 같은 것이 두 곳에 쌓이면
    # 갈라지는 날 어느 쪽이 사실인지 가릴 근거가 없다. 읽는 쪽은 전부
    # dbstore 로 옮겼고, 이제 쓰는 곳도 한 곳이다.
    #
    # ⚠ 다만 기록을 통째로 잃지는 않는다. DB 가 실패한 건만 jsonl 에
    #   구명정으로 적는다 — 평소엔 그 파일에 한 줄도 안 늘어난다.
    #   줄이 생겼다면 DB 가 아팠다는 뜻이고, 그 자체가 신호다.
    if not dbstore.save(fields):
        log("⚠ DB 기록 실패 — transcript.jsonl 로 흘려둡니다")
        try:
            with config.TRANSCRIPT_LOG.open("a") as f:
                f.write(json.dumps(fields, ensure_ascii=False) + "\n")
        except OSError:
            pass
    # 대답한 것은 전부 텔레그램에도 남는다 (사장님 지시 2026-08-13
    # "대답한 로그는 모두 텔레그램으로"). 전에는 음성 경로만 미러라
    # 직접 발화(통화 정리 등)·HTTP 답변이 폰에 안 남았다.
    # 텔레그램發은 제외(이미 그 채팅에 있다) · 무응답·차단도 제외.
    # _mirror_to_telegram 은 스레드 전송 + test_* 가드가 이미 있다.
    if (fields.get("reply") and fields.get("source") != "telegram"
            and fields.get("route") != "blocked"):
        _mirror_to_telegram(fields.get("command", ""), fields["reply"],
                            fields.get("source", "voice"))


class Ack:
    """처리가 길어지면 '듣고 있다'고 알린다.

    로컬 처리는 0.3초라 알릴 필요가 없다. 그래서 바로 말하지 않고
    ACK_DELAY_SEC 만큼 기다렸다가, 그때도 안 끝났으면 그제서야 말한다.
    짧은 명령은 조용히 지나가고 긴 명령만 반응하는 효과.
    """

    def __init__(self):
        self._done = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if not config.ACK_ENABLED:
            return

        def speak_after(delay: float, msg: str):
            if self._done.wait(delay):
                return          # 그 전에 끝났으면 아무 말도 안 한다
            speak.say(msg, block=False, priority=speak.PRIORITY_NOTICE)

        # 복명복창을 이미 했으면 짧은 알림은 생략한다. 바로 앞에서
        # "네, …확인하겠습니다" 라고 해놓고 0.8초 뒤에 또 "네, 확인하고
        # 있습니다" 하면 잔소리가 된다.
        # 말할 문구는 여기서 고른다 — 명령 한 건마다 한 번. 스레드 안에서
        # 고르면 12초 뒤에야 정해져서, 직전 회피(config.pick)가 다른 명령의
        # 맞장구와 뒤엉킨다.
        stages = [(config.ACK_LONG_SEC, config.pick(config.ACK_LONG_MESSAGES))]
        if not config.ECHO_BACK_ENABLED:
            stages.insert(0, (config.ACK_DELAY_SEC, config.pick(config.ACK_MESSAGES)))

        for delay, msg in stages:
            t = threading.Thread(target=speak_after, args=(delay, msg), daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._done.set()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()


def _not_for_me_reply() -> str:
    """'너한테 한 말 아니야' 에 대한 답. 어느 경로로 오든 같다."""
    return "네, 제게 하신 말씀이 아니군요. 조용히 있겠습니다."


# 멀리 계신 걸 알아챈 상태 — 마지막 증폭 배수·원본 크기와 안내 시각.
_FAR = {"gain": 0.0, "rms": 0.0, "at": 0.0, "told": 0.0}


def _far_heard(rms: float, gain: float) -> None:
    """증폭이 걸렸다 — 로그와 상태만 남긴다 (audio 층이 부른다).

    ⚠ 여기서는 말하지 않는다. 이 시점엔 방금 잡은 소리가 사장님 말씀인지
      TV 소리인지 whisper 의 환청인지 아직 모른다 — 받아쓰기도 화자 확인도
      뒤에 온다. 실측 08-13~16 안내 107번 중 78번이 그 뒤 '미등록·무시' 로
      버려진 소리였고, 그중 다섯 번은 자정~새벽 2시였다. 아무도 안 부른
      빈 방에 대고 "멀리 계신 것 같아요" 라고 말한 셈이다.
      "방송에 대고 떠들면 그게 더 소음" 이라는 원칙은 이 안내에도 같다.
    """
    log(f"먼 소리 증폭 {gain:.1f}배 (원본 {rms:.4f})")
    _FAR["gain"], _FAR["rms"], _FAR["at"] = gain, rms, time.monotonic()


def _far_notice_due() -> None:
    """방금 들은 말이 등록 화자로 확인됐다 — 아까 증폭이 걸렸으면 이제 알린다.

    증폭 시점과 여기 사이에는 받아쓰기가 끼어 몇 초가 흐른다. 묵은 증폭까지
    되살리지 않도록 방금(15초) 것만 본다.
    """
    if _FAR["at"] and time.monotonic() - _FAR["at"] <= 15.0:
        _on_far_gain(_FAR["rms"], _FAR["gain"])


def _on_far_gain(rms: float, gain: float) -> None:
    """멀리 계신 걸 말로 알린다 — 사장님 말씀인 게 확인된 뒤에만 부른다.

    사장님이 "동백이 내가 멀리 있는 걸 아는지" 확인할 수 있어야 한다는
    지시(2026-08-13). 로그만으로는 그걸 알 수 없다.
    ⚠ 안내는 알림(NOTICE) 우선순위 — 답변을 끊지 않는다. 그리고 쿨다운을
      둔다. 멀리 계시는 내내 매 발화마다 말하면 그게 소음이다.
    """
    _FAR["gain"], _FAR["at"] = gain, time.monotonic()
    if not getattr(config, "FAR_NOTICE_ENABLED", False):
        return
    if gain < getattr(config, "FAR_NOTICE_MIN_GAIN", 1.8):
        return
    now = time.monotonic()
    if now - _FAR["told"] < getattr(config, "FAR_NOTICE_COOLDOWN_SEC", 600.0):
        return
    _FAR["told"] = now
    log(f"멀리 계신다고 알림 ({gain:.1f}배, 원본 {rms:.4f})")
    speak.say(config.FAR_NOTICE_TEXT, block=False, priority=speak.PRIORITY_NOTICE)


def far_status() -> str:
    """"내 목소리 잘 들려?" 의 답.

    ⚠ 짧게 답한다. 사장님 교정(2026-08-13): 등록 지문 개수를 읊지 말고
      "네, 잘 들려요" 아니면 "아니요, 잘 안 들려서 키워 듣겠습니다" 면
      된다. 물으신 건 현황 보고가 아니라 지금 상태다.
    """
    gain, at = _FAR["gain"], _FAR["at"]
    if not at or time.monotonic() - at > 120:
        return "네, 잘 들립니다."
    if gain >= getattr(config, "FAR_NOTICE_MIN_GAIN", 1.8):
        return f"아니요, 잘 안 들려서 {gain:.1f}배로 키워 듣고 있습니다."
    return "네, 들립니다. 조금 작지만 괜찮습니다."


def _skill_from_recent(command: str) -> str:
    """직전 문답을 선언형 스킬로 바꾼다 (PLAN-skills 2단계).

    초안은 클로드 한 번짜리(ask_once — 도구·세션 없이 JSON 만) 이고,
    검증·저장은 skills_local 이 한다. action 이 화이트리스트 밖이면
    저장 자체가 안 된다 — 스킬로는 위험한 일을 표현할 수 없다.
    """
    import json as _json
    import re as _re

    import bridge
    import skills_local

    recent = dbstore.recent_brief(2, max_chars=120)
    if not recent:
        return "직전 문답이 없어서 스킬로 만들 게 없습니다."
    actions = ", ".join(skills_local.SAFE_ACTIONS)
    prompt = (
        "아래 문답을 반복 사용할 음성 스킬 선언으로 바꿔라. JSON 하나만 출력:\n"
        '{"name": "짧은-한글-이름", "triggers": ["부르는 말", "비슷한 말"], '
        '"action": "아래 목록 중 하나"}\n'
        f"action 은 반드시 이 중에서만: {actions}\n"
        '맞는 것이 없으면 {"action": "none"} 만 출력.\n'
        f"직전 문답: {recent}\n"
        f"방금 지시: {command}")
    raw = bridge.ask_once(prompt, model=config.MODEL_CHAT) or ""
    m = _re.search(r"\{.*\}", raw, _re.S)
    if not m:
        return "스킬 초안을 못 만들었습니다. 잠시 뒤 다시 말씀해 주세요."
    try:
        d = _json.loads(m.group(0))
    except _json.JSONDecodeError:
        return "스킬 초안이 형식에 안 맞았습니다. 다시 한번 말씀해 주세요."
    if str(d.get("action", "none")) in ("", "none"):
        return ("이 문답에 맞는 안전한 동작이 없습니다. 지금은 광고·일정·"
                "날씨·메일·기록 조회형 스킬만 만들 수 있습니다.")
    ok, msg = skills_local.create(str(d.get("name", "")),
                                  [str(t) for t in (d.get("triggers") or [])],
                                  str(d["action"]))
    log(f"스킬 생성 {'성공' if ok else '실패'}: {msg}")
    return msg


def handle(command: str, *, confirm, source: str = "voice", heard: str = "",
           speaker=None, who: str = "") -> str:
    """명령 하나를 끝까지 처리하고 사용자에게 말할 문장을 반환.

    speaker 를 주면(speak.Stream) Claude 답변을 다 기다리지 않고 문장이
    완성되는 대로 말한다. 그 경우 반환값은 이미 말한 것이므로 호출한 쪽이
    다시 말하면 안 된다 — speaker.spoke() 로 확인한다.
    음성 경로에서만 준다. 텔레그램·HTTP 는 텍스트로 돌려주므로 필요 없다.
    """
    command = command.strip()
    if not command:
        return ""
    # who: 누가 물었나 (화자 인증 이름). 다자간 기록의 기반 — DB 에서
    # "김철수님이 어제 뭐 물으셨지" 가 갈라진다 (H2).
    base = {"source": source, "heard": heard or command, "command": command,
            "who": who}

    # 대기 중인 질문이 없는데 "진행" 만 들어온 경우.
    # 명령으로 취급하면 "진행. 진행할까요?" 하고 되묻게 된다.
    if router.is_bare_response(command):
        msg = "지금은 확인을 기다리는 작업이 없습니다."
        record(**base, route="local", reply=msg, effective_input=0, cost_usd=0)
        return msg

    # 1) 위험 판정을 '가장 먼저' 한다.
    #
    # ⚠ 로컬 처리를 앞에 두면 안 된다. 일정 등록처럼 되돌릴 수 없는 작업을
    #   로컬로 빼는 순간, 음성 확인을 건너뛰고 바로 실행되어 버린다.
    #   로컬이냐 Claude냐는 '어떻게 실행할지'의 문제고,
    #   위험한가는 '실행해도 되는지'의 문제다. 후자가 먼저다.
    hit = router.danger_hit(command)
    elevated = dev = False
    if hit:
        if config.DEV_MODE and hit == router.SAFE_ONLY_REASON:
            # 포괄 사유로만 걸렸다 = 명시적 위험(배포·삭제·집행…)은 아니다.
            # 개발 모드에서는 승인을 묻지 않고, 대신 아래에서 스냅샷을 강제해
            # "되돌려" 로 복구할 수 있게 한다.
            # 매 수정마다 "진행할까요?" 를 거치면 음성 개발이 불가능하다.
            #
            # ⚠ 다만 '안전 목록에 없다' 와 '개발 명령이다' 는 다른 말이다.
            #   여기서 무조건 dev 로 올리면 잡담 한마디까지 opus 로 간다
            #   (TOOL_POLICY: dev=opus, normal=sonnet). 실측 2026-08-12 04:18,
            #   "지금 얘기하는 것도 나야" 가 dev 로 분류돼 2분 넘게 돌았고
            #   그 사이 "조금만 더 기다려 주세요" 만 나갔다. 사장님이
            #   "조금만더 기다려주세요 하고 또 생까네" 라고 하신 게 이것이다.
            #   덤으로 잡담마다 스냅샷이 쌓여 '되돌려' 목록도 더럽혔다.
            #
            #   코드를 건드리는 말일 때만 dev 로 올린다. 나머지는 normal
            #   (sonnet) 로 간다 — 승인을 안 묻는 건 똑같고, 모델만 빨라진다.
            #   명시적 위험은 위 elif 로 빠지므로 여기서 느슨해지지 않는다.
            dev = _is_code_edit(command)
        elif not confirm(command, hit):
            record(**base, route="blocked", danger=hit, confirmed=False, reply="")
            return ""
        else:
            elevated = True

    # 2) 로컬 처리 — 0 토큰. 승인이 끝난 뒤라 쓰기 작업도 여기서 할 수 있다.
    import perf

    with perf.track("local", command) as _pt:
        local = router.handle_local(command, elevated=elevated)
        _pt.ok = local is not None
    if local is not None:
        log(f"로컬 처리 (0 토큰): {local}")
        record(**base, route="local", danger=hit, confirmed=bool(hit),
               reply=local, effective_input=0, cost_usd=0)
        return local

    # 2.5) 되돌리기 요청은 Claude 를 거치지 않는다.
    #      "방금 수정 되돌려" 는 급한 상황에서 나오는 말이다.
    #      토큰도 시간도 쓰지 않고 즉시 처리해야 한다.
    if _is_undo(command):
        import code_guard

        msg = code_guard.restore(_last_repo or str(config.ROOT))
        log(f"되돌리기: {msg}")
        record(**base, route="local", reply=msg, effective_input=0, cost_usd=0)
        return msg

    # 2.6) "너한테 한 말 아니야" — 음성 루프뿐 아니라 텔레그램·HTTP 로 와도
    #      알아듣는다. 여기에 승인을 묻는 건 우스운 일이다.
    if router.is_not_for_you(router.normalize(command)):
        reply = _not_for_me_reply()
        record(**base, route="local", reply=reply, effective_input=0, cost_usd=0)
        return reply

    # ("내 목소리 잘 들려?" 는 router.handle_local 이 먼저 받는다 —
    #  거기서 목소리 현황 낭독보다 앞에 두어야 가로채이지 않는다)

    # 2.63b) 모드 해제 — 글로 들어온 것도 받는다 (텔레그램·제어 서버).
    #
    # ⚠ 안전밸브다. 음성 루프는 모드 중이면 일찍 continue 하므로, 종료
    #   문구를 whisper 가 못 알아들으면 여기까지 오지 못한다. GPT 모드는
    #   자동 종료가 없어서(사장님 지시: "절대로 임의로 끝내지마") 그 경우
    #   재시작 말고는 빠져나올 길이 없어진다. 글로 한 줄이면 풀린다:
    #     curl -sS -X POST "http://127.0.0.1:8765/command?token=$(cat state/token.txt)" \
    #          -H 'content-type: application/json' -d '{"text":"지피티 끝났어"}'
    #   자동 종료가 아니라 사장님이 여시는 것이므로 지시에 어긋나지 않는다.
    if _meeting_active():
        _k = _meeting_kind()
        _norm = router.normalize(command)
        if (router.is_gpt_end(_norm) if _k == "지피티"
                else router.is_meeting_end(_norm)):
            _meeting_exit(f"{base.get('source', '글')} 명령")
            reply = f"네, {_k} 모드 껐어요."
            record(**base, route="local", reply=reply,
                   effective_input=0, cost_usd=0)
            return reply

    # 2.63c) "메일 정리해줘" — 업체별로 갈라 판단까지 (2026-08-14 지시).
    #        본문을 읽고 클로드가 판단하는 무거운 작업(30초~1분)이라
    #        스레드로 돌린다. 기다리는 동안 귀가 멎으면 안 된다.
    if router.is_mail_report(router.normalize(command)):
        reply = "네, 받은 메일 정리할게요. 조금 걸려요."
        record(**base, route="local", reply=reply, effective_input=0, cost_usd=0)
        speak.say(reply, block=True)

        def _mail_work():
            import mail_report

            said, path = mail_report.build()
            log(f"메일 정리: {said[:70]}" + (f" → {path.name}" if path else ""))
            speak.say(said, block=False)
            record(source=base.get("source", "voice"), heard="(메일 정리)",
                   command="메일 정리", route="claude", reply=said,
                   effective_input=0, cost_usd=0)

        threading.Thread(target=_mail_work, daemon=True).start()
        return ""                       # 이미 말했다 — 실행기가 또 말하면 겹친다

    # 2.64) "미팅 모드" — 입 봉인·기록·클로드 회의록 (2026-08-13 지시).
    #        확인 멘트를 먼저 말하고 나서 봉인한다 — 순서를 바꾸면
    #        확인 멘트부터 삼켜져 시작됐는지 알 수 없다.
    if (getattr(config, "MEETING_MODE_ENABLED", False)
            and router.is_meeting_start(router.normalize(command))):
        reply = "네, 미팅 모드 시작합니다. 끝나면 미팅 끝났어, 라고 해주세요."
        record(**base, route="local", reply=reply, effective_input=0, cost_usd=0)
        speak.say(reply, block=True)
        _meeting_enter("음성 명령")
        return ""                       # 이미 말했다 — 실행기가 또 말하면 겹친다

    # 2.64b) "GPT 모드" (2026-08-14 지시) — 지피티와 얘기하는 동안 동백은
    #        한마디도 안 하고 듣기만 하다가, 끝나면 옵시디언에 정리한다.
    #        미팅 모드와 같은 기계를 쓰고 정리 문서 꼴만 다르다.
    if (getattr(config, "GPT_MODE_ENABLED", False)
            and router.is_gpt_start(router.normalize(command))):
        reply = ("네, 지피티 모드 시작할게요. 저는 듣고만 있을 테니 "
                 "끝나면 지피티 끝났어, 라고 해주세요.")
        record(**base, route="local", reply=reply, effective_input=0, cost_usd=0)
        speak.say(reply, block=True)
        _meeting_enter("음성 명령", kind="지피티")
        return ""

    # 2.65) "방금 그거 스킬로 만들어" — 직전 문답을 선언형 스킬로
    #        (PLAN-skills 2단계). 초안만 클로드 한 번짜리, 저장은 로컬.
    if router.is_skill_create(router.normalize(command)):
        reply = _skill_from_recent(command)
        record(**base, route="skill", reply=reply, effective_input=0, cost_usd=0)
        return reply

    # 2.7) 가벼운 대화는 로컬 소형 모델이 즉답한다 — 클로드를 아껴 쓴다.
    #      위험 게이트에 걸린 명령(elevated/dev = 실행 목적)은 제외.
    #      분류·생성이 실패하면 조용히 클로드로 간다 (gatekeeper.py 참조).
    if config.GATEKEEPER_ENABLED and not hit:
        import gatekeeper

        chat = gatekeeper.try_chat(command)
        if chat is not None:
            log(f"게이트키퍼 즉답 (0 토큰): {chat}")
            record(**base, route="gatekeeper", reply=chat,
                   effective_input=0, cost_usd=0)
            return chat

    # 2.6) 파일을 고칠 수 있는 호출이면 먼저 스냅샷을 남긴다.
    #
    # ⚠ 등급 이름으로 판단하면 안 된다. 도구 제한을 열면서 조회 등급까지
    #   쓰기가 가능해졌는데, 스냅샷은 dev·elevated 에서만 찍고 있었다.
    #   그래서 조회 등급으로 코드를 고치면 되돌릴 수도 없고 재시작도 안 되는
    #   사각지대가 생겼다 (텔레그램이 옛 config 로 죽은 원인).
    #   정책 표의 실제 권한을 보고 판단한다 — 다시 좁히면 자동으로 따라간다.
    import code_guard

    tier = "elevated" if elevated else ("dev" if dev else "normal")
    blocked = set(config.TOOL_POLICY[tier].get("disallowed") or ())
    can_write = not {"Write", "Edit"} <= blocked

    # ⚠ 쓸 수 있으면 무조건 스냅샷을 남긴다. 명령이 잡담처럼 보여도 그렇다.
    #
    #   2026-08-12 새벽에 이걸 '코드 명령일 때만' 으로 좁히려다 test_dev_mode
    #   [7b] 에 걸렸다. 그 검사는 과거 사고 기록이다 — 조회 등급도 쓰기가
    #   가능해졌는데 스냅샷이 dev·elevated 에서만 걸려 있었고, 그 사각지대로
    #   config 가 바뀌었는데 재시작이 안 돼 텔레그램 브릿지가 옛 config +
    #   새 코드로 터졌다.
    #
    #   '쓸 수 있는데 되돌릴 수 없는' 상태를 만들면 안 된다. 스냅샷이 쌓이는
    #   건 code_guard 쪽에서 다룰 문제다 (지문이 같으면 실제 변경이 아니다).
    snap = None
    if can_write and config.CODE_EDIT_ENABLED:
        ok, why, snap = code_guard.guard(_guess_target(command), command)
        if not ok:
            log(f"코드 수정 거부: {why}")
            record(**base, route="blocked", reply=why)
            return why
        log(f"스냅샷 저장: {snap['label']}")

    # 동백 자신이 바뀌었는지는 따로 본다.
    # _guess_target 이 다른 저장소를 골랐어도 동백 코드가 바뀌면 재시작해야
    # 새 코드로 돈다 — 스냅샷 대상과 재시작 판단은 별개다.
    own_before = code_guard.tree_fingerprint(str(config.ROOT)) if can_write else None

    # 3) Claude 호출 — 회상 낌새("지난번"·"그때")가 있으면 장기 기억을 찾아
    #    앞에 붙인다. 항상 붙이면 토큰만 늘고, 이럴 때만 붙이면 싸다.
    prompt = command
    # 직전 문답 몇 건을 같이 보낸다 (PLAN-unify 2단계 — 층 사이 기억 잇기).
    # 로컬·큐웬이 방금 답한 걸 클로드는 모른다 — "아까 내가 뭐 물었지" 가
    # 층이 바뀌는 순간 끊기던 원인. 상주 세션이 있어도 재시작이 잦아
    # 매번 붙이는 쪽이 안전하다. 실측 ~300토큰, 문맥 대비 1% 미만.
    # ⚠ 테스트에서는 끈다 — record() 의 오염 가드와 같은 이유의 역방향:
    #   실사용 DB 의 진짜 대화가 테스트 프롬프트에 섞여 단정이 깨진다
    #   (test_gatekeeper '원문 그대로' 가 실제로 그렇게 깨졌다).
    if (getattr(config, "HISTORY_ATTACH_N", 0) > 0
            and not os.path.basename(sys.argv[0] or "").startswith("test_")):
        recent = dbstore.recent_brief(config.HISTORY_ATTACH_N)
        if recent:
            prompt = f"(직전 문답: {recent})\n{prompt}"
    if getattr(config, "MEMORY_ENABLED", False) and router.wants_memory(router.normalize(command)):
        try:
            import memory_local

            hits = memory_local.recall(command)
            if hits:
                prompt = "(관련 기억: " + " / ".join(hits) + ")\n" + command
                log(f"기억 {len(hits)}건 첨부")
        except Exception:
            pass

    # 광고 얘기면 숫자를 뽑아 같이 보낸다.
    #
    # ⚠ 이게 없어서 사장님이 두 번 헛물켜셨다 (2026-08-12 22:03, 22:07).
    #   "광고플랫폼 광고성과 분석해서 보고해" → '분석해' 가 조언 요청으로
    #   분류돼 클로드로 갔는데, 클로드에는 DB 도구가 없어서 "광고 데이터에
    #   접근할 도구가 없습니다" 로 끝났다. 데이터는 동백에게 있고 분석력은
    #   클로드에게 있는데 둘이 만나지 않았다.
    #
    #   숫자는 ads_local 이 DB 에서 뽑고, 클로드는 그걸 읽고 해석만 한다.
    #   소형 모델이든 큰 모델이든 숫자를 지어내게 두지 않는다는 원칙은 같다.
    try:
        if router.wants_ads_context(router.normalize(command)):
            import ads_local
            from datetime import date as _d, timedelta as _td

            p = ads_local.period(command)
            if p is None:                     # 기간을 안 말씀하시면 어제
                y = _d.today() - _td(days=1)
                p = (y, y, "어제")
            data = ads_local.analysis(*p)
            if data:
                prompt = f"(광고 실적 데이터 — 이 숫자만 쓰고 지어내지 마라)\n{data}\n\n{command}"
                log(f"광고 데이터 첨부 ({p[2]})")
    except Exception:
        pass
    _ct = perf.track("claude", command, note=("dev" if dev else
                                              "elevated" if elevated else "normal"))
    _ct.__enter__()
    # 첫 조각이 도착한 순간을 남긴다. 그때부터 소리가 나가므로 사장님이
    # 느끼는 시간은 여기까지다 — perf 의 sec(전체 완료)이 아니다.
    #
    # ⚠ speaker 가 없으면 콜백도 넘기지 않는다. bridge_sdk 는
    #   stream=(on_text is not None) 으로 스트리밍 여부를 정하므로,
    #   재보겠다고 콜백을 항상 넘기면 소리를 낼 상대도 없는 호출까지
    #   스트리밍으로 바뀐다. 계측이 동작을 바꾸면 안 된다.
    def _feed(text):
        _ct.mark_first()
        speaker.feed(text)

    try:
        reply, meta = bridge.ask(prompt, elevated=elevated, dev=dev,
                                 on_text=_feed if speaker else None)
        _ct.ok = True
    except bridge.AuthError as e:
        log(f"인증 오류: {e}")
        record(**base, route="error", error=str(e))
        return str(e)
    except bridge.ClaudeError as e:
        log(f"오류: {e}")
        record(**base, route="error", error=str(e))
        return f"문제가 생겼습니다. {e}"
    finally:
        # ⚠ finally 로 닫는다. 예외로 빠져나가도 시간은 남아야 한다 —
        #   느려서 터진 호출이야말로 기록될 값어치가 있다.
        _ct.__exit__(None, None, None)

    log(
        f"토큰 실효입력 {meta['effective_input']:,} "
        f"(캐시읽기 {meta['cache_read']:,} / 캐시쓰기 {meta['cache_write']:,}) "
        f"출력 {meta['output']:,}"
        + (f" / ${meta['cost_usd']:.4f}" if meta.get("cost_usd") else "")
    )
    # 무엇이 바뀌었는지 알려준다. 음성은 diff 를 볼 수 없으므로
    # 이 한 문장이 유일한 확인 수단이다.
    #
    # 스냅샷 전후 지문이 같으면 아무 말도 보태지 않는다 — 개발 모드에선
    # 잡담에도 스냅샷이 뜨는데, 그때 작업 트리 diff 를 읽으면 사장님이
    # 원래 수정 중이던 파일까지 "고쳤습니다" 로 잘못 보고하게 된다.
    #
    # '되돌리려면 되돌려라고 하세요' 는 붙이지 않는다 — 사장님 지시.
    # 방법은 이미 아시고, 필요할 때 직접 "되돌려" 라고 말씀하신다.
    if snap and code_guard.tree_fingerprint(snap["repo"]) != snap.get("fingerprint"):
        _remember_repo(snap["repo"])
        if config.CODE_SPEAK_DIFF:
            summary = code_guard.diff_summary(snap["repo"])
            reply = f"{reply} {summary}"
            # 답변은 이미 말하는 중이다. 요약은 뒤에 이어 붙인다.
            if speaker and speaker.spoke():
                speaker.add(summary)

    # 동백이 자기 코드를 고쳤으면 새 코드로 갈아타야 한다.
    # 안 그러면 "고쳤습니다" 라는 답만 듣고 동작은 그대로고, 프로세스마다
    # 옛 config 와 새 코드가 섞여 AttributeError 로 죽는다.
    if own_before is not None and code_guard.tree_fingerprint(str(config.ROOT)) != own_before:
        notice = _arm_self_restart()
        reply += notice
        if speaker and speaker.spoke():
            speaker.add(notice)

    record(
        **base,
        route="claude",
        danger=hit,
        confirmed=bool(hit),
        elevated=elevated,
        reply=reply,
        effective_input=meta["effective_input"],
        output=meta["output"],
        cost_usd=meta.get("cost_usd"),
    )
    return reply


# ─────────────────────────────────────────────────────────
# 상시 대기 루프
# ─────────────────────────────────────────────────────────
def handle_http_command(text: str, confirm_word: str) -> dict:
    """아이폰 등 외부에서 들어온 텍스트 명령.

    음성 재확인을 못 쓰는 경로라 confirm 파라미터로 대신 받는다.
    이 경로로 안전 게이트를 우회할 수 없어야 한다.
    """
    hit = router.danger_hit(text)
    if hit and not router.is_confirmation(confirm_word or ""):
        log(f"⚠ HTTP 위험 명령 보류: {text!r} (걸린 표현 '{hit}')")
        # 차단된 위험 명령이야말로 기록에 남아야 한다.
        # handle() 을 거치지 않는 경로라 여기서 직접 남긴다.
        record(source="http", heard=text, command=text, route="blocked",
               danger=hit, confirmed=False, reply="")
        return {
            "_code": 409,
            "confirmation_required": True,
            "matched": hit,
            "message": f"'{hit}' 는 되돌리기 어려운 작업입니다. confirm=진행 을 붙여 다시 보내세요.",
        }
    reply = handle(text, confirm=lambda c, h: True, source="http")  # 위에서 이미 판정 완료
    # ⚠ 소리로 내지 않는다. 호출한 쪽이 답을 글로 받아 갔는데 스피커로도
    #   읽으면, 사장님은 한참 전에 화면으로 본 결과를 뒤늦게 듣게 된다
    #   (실제로 겪음 — 점검용 명령이 방 안에서 소리로 나왔다).
    #   소리가 필요하면 부르는 쪽이 /say 를 따로 쓴다.
    # (소리가 필요하면 부르는 쪽이 /say 를 따로 쓴다 — 브리핑이 그렇게 한다)
    # 응답은 이 함수가 돌려준 뒤에 나가므로, 재시작은 조금 미룬다.
    # (여기서 바로 죽으면 아이폰이 빈 응답을 받는다)
    if _restart_pending:
        threading.Timer(3.0, restart_if_pending).start()
    return {"ok": True, "reply": reply}


def _start_control(state: dict, *, has_mic: bool) -> None:
    if not config.CONTROL_ENABLED:
        return
    import control

    def on_ptt():
        if not has_mic:
            return
        state["ptt_until"] = time.monotonic() + config.PTT_WINDOW_SEC
        speak.beep()
        log(f"푸시투토크 — {config.PTT_WINDOW_SEC:.0f}초 동안 호출어 없이 받습니다")

    host, port, token = control.start(
        {
            "ptt": on_ptt,
            "say": lambda t: speak.say(t, block=False),
            "command": handle_http_command,
            "status": lambda: {
                "ok": True,
                "mic": has_mic,
                "device": state.get("device"),
                "speaking": speak.is_speaking(),
            },
        }
    )
    log(f"제어 서버: http://{host}:{port}  (토큰: {config.TOKEN_FILE})")
    if host not in ("127.0.0.1", "localhost"):
        log("⚠ 외부 접속 허용 상태입니다. 같은 네트워크의 누구나 명령을 보낼 수 있습니다.")


def _near_miss(text: str) -> bool:
    """호출어로는 못 잡았지만 비슷하게 들렸는가.

    whisper 는 '동백아'를 '홍배가'·'동백은' 처럼 흔들리게 적는다.
    변형 목록을 아무리 늘려도 다 못 잡으므로, 놓쳤을 때 되묻는 편이 낫다.

    ⚠ 한 글자짜리 감탄사("아", "야", "마")는 되묻지 않는다. '아' 는
      '동백아' 와 겹치는 글자가 하나라 유사도가 딱 0.5 로 잡혀 문턱을
      넘었다 — 옆에서 대화만 해도 동백이 "부르셨나요" 하고 끼어들었다.
      글자 하나가 겹친 걸 '비슷하다' 고 부를 수는 없다.
    """
    if not config.NEARMISS_ENABLED:
        return False
    head = router.normalize(text)[:4]
    if len(head) < config.NEARMISS_MIN_LEN:
        return False
    for w in config.WAKE_WORDS[:6]:
        wake = router.normalize(w)
        m = difflib.SequenceMatcher(None, head[: len(wake)], wake)
        if m.ratio() < config.NEARMISS_RATIO:
            continue
        # 겹친 글자가 둘은 돼야 한다. 비율만 보면 짧은 말일수록
        # 한 글자만 스쳐도 통과한다 ("백만" 이 '동백' 으로 잡히는 식).
        if sum(b.size for b in m.get_matching_blocks()) >= config.NEARMISS_MIN_LEN:
            return True
    return False


def run_daemon() -> int:
    import audio as audio_mod

    ok, msg = bridge.healthcheck()
    if not ok:
        log(f"클로드 연결 실패: {msg}")
        speak.say(msg)
        return 3
    log(f"클로드 연결 확인: {msg}")

    # 상주 브릿지를 미리 연결해 둔다 — 첫 명령부터 스폰 없이 받는다.
    # 연결만 하고 아무것도 묻지 않아 토큰은 0. 실패해도 조용히 넘어간다 —
    # 어차피 첫 호출이 알아서(lazy) 연결한다.
    if config.BRIDGE == "sdk" and getattr(config, "BRIDGE_RESIDENT", False):
        def _warm_bridge():
            import bridge_sdk

            bridge_sdk.warm_up()

        threading.Thread(target=_warm_bridge, daemon=True).start()

    # 상황 능동 — 일정 임박·VIP 메일을 동백이 먼저 알린다.
    if getattr(config, "NUDGE_MEETING_ENABLED", False):
        threading.Thread(target=_nudge_loop, daemon=True).start()

    # 화자 인증 모델은 백그라운드로 예열한다.
    # 첫 명령에서 몇 초를 기다리게 하지 않으려는 것뿐, 실패해도 동백은 뜬다.
    if config.VOICE_VERIFY_ENABLED:
        import voiceprint

        if voiceprint.enrolled():
            threading.Thread(target=voiceprint.preload, daemon=True).start()
            log("화자 인증: 켜짐 (" + ", ".join(voiceprint.enrolled()) + ")")
        else:
            log("화자 인증: 등록된 목소리 없음 — 열림 (--enroll 로 잠글 수 있습니다)")

    state: dict = {"ptt_until": 0.0, "device": None}

    try:
        dev = audio_mod.pick_device()
    except RuntimeError as e:
        # 마이크가 없어도 죽지 않는다. 아이폰 텍스트 직송은 이 상태로도 된다.
        log("⚠ " + str(e).splitlines()[0])
        log("→ 마이크 없이 제어 서버만 띄웁니다. 아이폰 텍스트 직송은 지금도 됩니다.")
        _start_control(state, has_mic=False)
        speak.say("동백 대기 중입니다. 마이크는 아직 없습니다.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    state["device"] = audio_mod.Listener._name_of(dev)
    log(f"입력 장치: {state['device']}")
    # 미리 올려둔다. 안 하면 기동 후 첫 "동백아" 가 모델 로딩까지 떠안아
    # 2.4초를 더 기다리게 된다 (실측 2.82초 → 0.42초).
    # 백그라운드로 도는 이유: 로딩을 기다리느라 대기 시작이 늦어지면
    # 그 사이에 부른 말을 통째로 놓친다.
    log("whisper 모델 준비 중… (최초 1회 다운로드가 있을 수 있습니다)")
    threading.Thread(target=audio_mod.preload, daemon=True).start()
    # 말끝 판정(8MB)도 같이 올린다. 청취 루프 안에서 불리므로 첫 발화에서
    # 로딩이 걸리면 그 발화만 늦어진다. 실패해도 조용히 넘어간다 —
    # available() 이 False 가 되고 지금까지의 무음 세기로 돌아갈 뿐이다.
    threading.Thread(target=lambda: __import__("turn_end").preload(),
                     daemon=True).start()

    with audio_mod.Listener(dev) as listener:
        listener.gate = speak.is_speaking
        # 사장님이 말을 시작하면 동백이 입을 다문다.
        # stop() 은 대기열까지 비우므로 뒤에 밀린 말도 같이 사라진다.
        # 끊고 들어왔다는 사실을 남겨둔다 — 그 말은 새 명령이 아니라
        # 앞서 하신 말에 이어 붙일 말이기 때문이다.
        barged = {"hit": False}
        listener.on_barge_in = lambda: (log("끊고 들어옴 — 발화 중단"),
                                        speak.stop(), barged.update(hit=True))

        # 못 끊었을 때 '왜' 를 남긴다. 들린 최댓값이 문턱에 얼마나 못 미쳤는지
        # 알아야 조정을 숫자로 한다 — 추측으로 올렸다 내렸다 하다 두 번 어긋났다.
        def _gate_report(peak: float, threshold: float,
                         streak: float = 0.0, loud: int = 0) -> None:
            if not barged["hit"] and peak > threshold * 0.35:
                why = "넘었는데 길이가 모자람" if peak > threshold else "문턱 미달"
                # 계수기 숫자가 있어야 '한 블록 소음' 과 '진동 사각' 이 갈린다.
                extra = ""
                if peak > threshold:
                    extra = (f" (계수 {streak:g}/{config.barge_in_blocks()}"
                             f" · 큰소리 연속 {loud}/"
                             f"{getattr(config, 'BARGE_IN_LOUD_BLOCKS', 2)})")
                log(f"말하는 중 들린 최대 {peak:.4f} / 끊기 문턱 {threshold:.4f}"
                    f" — {why}{extra}")

        listener.on_gate_report = _gate_report
        listener.on_disconnect = lambda n: log(f"⚠ 마이크 '{n}' 사라짐 — 돌아올 때까지 대기")

        # 멀리서 부르신 소리에 증폭이 걸리면 남긴다 — 놓친 호출이 '작아서'
        # 였는지 이 줄로 갈린다 (2026-08-13 '크기가' 사고).
        import audio as _audio_mod

        _audio_mod.set_gain_reporter(_far_heard)
        # "내 목소리 잘 들려?" 를 router 가 받아 이 함수로 답한다
        # (router → dongbaek 직접 import 는 순환이라 꽂아 준다).
        router.set_far_status(far_status)
        listener.on_reconnect = lambda n: log(f"✓ 마이크 '{n}' 재연결")

        _start_control(state, has_mic=True)
        # 자주 쓰는 짧은 말은 미리 합성해둔다. 부르자마자 대답하려면
        # 그때 합성해선 늦는다 (0.4초).
        #
        # 호명 대답은 변형을 돌려 쓰므로 **목록 전체**를 캐시해야 한다. 하나만
        # 캐시하면 그 변형만 즉답이고 나머지는 0.4초 늦어, 변형이 없느니만
        # 못한 들쭉날쭉이 된다.
        #
        # ACK 는 일부러 뺐다. 0.8초·12초나 기다렸다 나가는 말이라 합성 시간이
        # 묻히고, 여기 담으면 기동이 그만큼 늦어진다 (변형 수 × 0.4초).
        speak.precache(*config.CALL_ANSWERS, config.NEARMISS_MESSAGE)
        # 실행기를 띄운다. 이 스레드가 명령을 도맡으므로 아래 루프는
        # Claude 응답을 기다리지 않고 계속 듣는다.
        threading.Thread(target=_run_jobs, daemon=True).start()
        speak.say(_ready_line())
        log("대기 중. 호출어: " + " / ".join(config.WAKE_WORDS[:4]))

        followup_until = 0.0
        # "부르셨나요" 하고 되물은 직후인가. 이때 돌아온 "안 불렀는데" 를
        # 새 명령으로 받으면, 잘못 끼어든 것도 모자라 대답까지 한다.
        nearmiss_until = 0.0
        # 직전에 처리한 명령. 답변 도중에 말을 보태시면 여기에 이어 붙인다.
        _last_command, _last_command_at = "", 0.0
        # 그 버퍼에 지금까지 몇 번 붙었나. 눈덩이를 세는 자다.
        _merge_count = 0
        while True:
            # timeout 을 두는 이유: 푸시투토크 요청이 들어왔는지
            # 주기적으로 확인해야 하기 때문
            audio = listener.next_utterance(timeout=1.0)
            # 이 말이 동백을 끊고 들어온 것인지 여기서 집어둔다.
            # 아래에서 '앞 명령에 이어 붙일 말' 인지 판단하는 근거가 된다.
            was_barge, barged["hit"] = barged["hit"], False
            if audio is None:
                continue

            # 확실한 남 목소리면 받아쓰기 자체를 건너뛴다 (80배 싼 검사가 먼저).
            if _skip_transcribe(audio):
                continue

            text = audio_mod.transcribe(audio)
            if not text:
                continue
            # 끊고 들어오기 때문에 마이크가 항상 열려 있다.
            # 되돌아온 자기 목소리를 명령으로 실행하면 안 된다.
            if _is_own_voice(text):
                log(f"자기 목소리 되울림 — 무시: {text!r}")
                continue
            # whisper 가 잡음을 글자로 적은 것 — 사람 말이 아니면 안 본다.
            if router.is_noise(text):
                continue
            log(f"들림: {text!r}")

            # 미팅 모드 — 전화 모드보다 먼저, 더 굳게 닫는다.
            # 호명으로도 안 열린다 (미팅 소리엔 호명 비슷한 말이 섞여
            # 10:25 에 귀가 열렸고 줌미팅에 "메모리 48기가…" 를 낭독했다).
            # 여는 건 미팅 종료 문구 + 등록 화자 목소리뿐.
            if _meeting_active():
                call_notes.note(text)
                _MEETING["last_heard"] = time.monotonic()
                rest_me = router.match_wake(text)
                probe_me = router.normalize(rest_me if rest_me is not None else text)
                # 종료 문구는 지금 어느 모드인지에 맞는 것만 받는다.
                # 지피티 모드에서 "미팅 끝났어" 로 닫히면, 사장님은 지피티와
                # 얘기하는 중인데 동백이 갑자기 입을 여는 꼴이 된다.
                _kind = _meeting_kind()
                _ended = (router.is_gpt_end(probe_me) if _kind == "지피티"
                          else router.is_meeting_end(probe_me))
                if _ended:
                    ok_me, _ = _speaker_ok(audio, bank=False)
                    if ok_me:
                        _meeting_exit("종료 문구")
                    else:
                        log(f"{_kind} 종료 문구 들렸으나 목소리 확인 실패 — 유지")
                    continue
                # ⚠ 상한도 GPT 모드에는 안 건다 (사장님 지시 2026-08-14:
                #   "절대로 임의로 끝내지마"). 미팅은 잊고 놔둬도 3시간 뒤
                #   회의록이 나오는 게 이득이지만, 지피티 모드는 끝을
                #   사장님이 정한다. 상한이 지나도 계속 듣는다.
                if (_meeting_kind() != "지피티"
                        and time.monotonic() > _MEETING["until"]):
                    _meeting_exit("상한 초과")
                continue

            # 전화 모드 — 통화 중에는 해제 요청만 듣고 나머지는 전부 무시한다.
            # "네네, 그렇게 보내드릴게요" 가 명령이 되는 사고를 여기서 끊는다.
            if _HOLD["until"]:
                # 통화 중에 들린 말을 모아 둔다. "전화 끝났어" 하시면
                # 이걸 정리해 위키에 남긴다 — 이미 흘려보내던 것을 줍는 것이다.
                # ⚠ 여기서 import 하면 안 된다. 함수 안에 import 가 있으면
                #   파이썬이 그 이름을 함수 전체의 '지역변수' 로 잡아서,
                #   이 줄에 닿기 전에 쓰는 아래쪽(1868행)이 UnboundLocalError
                #   로 터진다. 실제로 데몬이 30초마다 죽고 다시 떴다 —
                #   사장님께는 "동백 준비되었습니다" 가 반복되는 걸로 보였다.
                #   모듈 맨 위의 import 로 충분하다.
                try:
                    # 사장님 목소리가 섞였는지도 같이 적어 둔다 — 나갈 때
                    # 이걸 보고 위키에 남길지 정한다 (방송 오인 방지).
                    # 한 번 확인되면 더 안 본다: 답은 이미 나왔고, 통화
                    # 내내 지문 검사를 돌릴 이유가 없다.
                    # ⚠ bank=False — 통화 상대 목소리를 '남' 지문으로 쌓으면
                    #   코호트가 통화 한 통에 뒤덮인다. 여기 판정은 '남길지'
                    #   에만 쓰고 문을 여닫지 않으므로 담을 이유가 없다.
                    mine = (call_notes.owner_heard()
                            or _speaker_ok(audio, bank=False)[0])
                    call_notes.note(text, owner=mine)
                except Exception:
                    pass
                _HOLD["last_heard"] = time.monotonic()   # 30초 무음 판정용
                if time.monotonic() >= _HOLD["until"]:
                    _phone_exit("시간 초과 — 통화 끝난 것으로 보고 정리")
                else:
                    rest = router.match_wake(text)
                    # 말끝에서 부르셔도 열어야 한다. 통화 중에는 오히려
                    # 그쪽이 흔하다 — 상대에게 말하다 동백을 부르시니까.
                    # ("...그렇지? 동백아")
                    if rest is None and router.wake_at_end(text):
                        rest = ""
                    probe = router.normalize(rest if rest is not None else text)
                    # ⚠ 호출어로 부르면 전화 모드여도 깨어난다.
                    #   "동백아" 는 통화 상대가 아니라 동백에게 하는 말이다.
                    #   이게 없으면 사장님이 네 번을 불러도 무시당한다 —
                    #   해제 주문("다시 들어")을 모르면 30분간 갇힌다. 실제로 겪었다.
                    if rest is not None or router.is_resume_request(probe):
                        ok_r, why_r = _speaker_ok(audio)
                        # ⚠ 화자 인증이 이 문을 다시 잠그고 있었다.
                        #   위 주석대로 호명 탈출구를 만들어 놨는데, 인증에
                        #   실패하면 로그 한 줄 없이 continue 로 빠졌다.
                        #   부르는 쪽에서는 완전한 무응답이고, 10분을 갇힌다.
                        #
                        #   실측 2026-08-12 08:19 — 뉴스 낭독(176자)을 통화로
                        #   오인해 전화 모드로 들어간 뒤, 08:20:30 과 08:20:55
                        #   에 "동백아" 를 세 번 부르셨는데 전부 조용히 버려졌다.
                        #
                        #   그래서 두 가지를 둔다.
                        #     · 왜 안 열렸는지 로그로 남긴다
                        #     · 거듭 부르시면 인증과 무관하게 연다. TV 는
                        #       30초 안에 "동백아" 를 세 번 부르지 않는다.
                        #       사람은 부른다 — 그 반복이 곧 신원이다.
                        if not ok_r:
                            now_r = time.monotonic()
                            if now_r - _HOLD.get("call_at", 0.0) > config.PHONE_WAKE_WINDOW_SEC:
                                _HOLD["calls"] = 0
                            _HOLD["calls"] = _HOLD.get("calls", 0) + 1
                            _HOLD["call_at"] = now_r
                            if _HOLD["calls"] < config.PHONE_WAKE_TRIES:
                                log(f"전화 모드 — 호명했지만 목소리 확인 실패"
                                    f"(유사도 {why_r}), {_HOLD['calls']}번째. "
                                    f"{config.PHONE_WAKE_TRIES}번 부르시면 엽니다")
                                continue
                            log(f"전화 모드 해제 — {_HOLD['calls']}번 부르셨습니다 "
                                f"(목소리 확인은 실패했지만 사람이 거듭 부른 신호)")
                        _phone_exit("호명 또는 해제 요청" if ok_r else "연속 호명")
                        if router.is_resume_request(probe) or router.is_bare_call(rest or ""):
                            speak.say("네, 다시 듣고 있습니다.", block=False)
                            _REPLIED_AT["at"] = time.monotonic()
                            followup_until = time.monotonic() + config.FOLLOWUP_WINDOW_SEC
                            continue
                        # 호출어 뒤에 명령이 붙어 있으면 그대로 처리한다
                        command = rest
                    else:
                        continue

            # 푸시투토크 창 또는 직전 명령 직후면 호출어를 면제.
            #
            # ⚠ '말을 시작한 시각' 으로 판단한다. 끝난 시각으로 재면 길게
            #   말씀하실수록 불리해진다 — 20초를 말하면 창이 15초라 그 사이
            #   만료돼, 다 듣고도 조용히 버렸다. 실제로 겪은 일이다
            #   ('동백아' → '네' → 긴 지시 → 아무 반응 없음).
            #   말을 건 시점이 창 안이었으면 그건 동백에게 한 말이 맞다.
            spoke_at = time.monotonic() - len(audio) / config.SAMPLE_RATE
            # 호출어 필수 모드에서는 대화창을 쓰지 않는다. 푸시투토크는 남긴다 —
            # 그건 사장님이 버튼으로 명시한 것이라 오인이 없다.
            # 답변 직후 창은 호출어 필수 모드에서도 살린다 — 동백이 방금
            # 말을 걸었으니 대답이 돌아오는 건 자연스럽다. 이게 핑퐁이다.
            # 대화창은 동백이 '말을 마친' 시점부터 잰다. 말하기 시작한 때부터
            # 재면 긴 답변일수록 사장님께 남는 시간이 줄어든다 — 10초짜리
            # 답변이면 15초 창에서 5초만 남는다. 사장님 지적: "내가 또 거기에
            # 대해서 대답을 하면 또 말이 없어. 두 마디 연결이 안 돼."
            reply_win = _reply_window_until()
            if getattr(config, "REQUIRE_WAKE_ALWAYS", False):
                free_pass = spoke_at < max(state["ptt_until"], reply_win)
            else:
                free_pass = spoke_at < max(followup_until, state["ptt_until"], reply_win)
            norm_all = router.normalize(text)

            # ⓪ H3 맞장구 — 동백 말을 끊고 들어온 것이 "네"·"그래" 같은
            #   순수 맞장구뿐이면 명령이 아니라 '듣고 있다' 는 신호다.
            #   명령으로 받으면 "네." 가 클로드까지 가서 헛답이 나간다
            #   (backchannel/barge-in 구분 — 위키 '대화 알고리즘 연구' H3).
            if (was_barge and getattr(config, "BACKCHANNEL_GUARD", True)
                    and router.is_bare_backchannel(norm_all)):
                log(f"맞장구로 들음 — 명령 아님: {text!r}")
                continue

            # ① "너한테 한 말 아니야" — 가로챈 걸 알아차리면 즉시 물러난다.
            #    말하던 것도 멈추고, 통화 중으로 보고 귀를 닫는다.
            if router.is_not_for_you(norm_all):
                speak.stop()
                bump_generation()
                _phone_enter(f"내게 한 말 아님: {text[:24]!r}")
                followup_until = 0.0
                continue

            # ② 통화 감지 — 긴 발화가 잇달으면 사람끼리 얘기하는 중이다.
            #    "여보세요" 를 놓쳐도 여기서 스스로 귀를 닫는다.
            #
            # ⚠ 길이만 보면 안 된다 — 대화창 안에서 사장님이 길게 말씀하신
            #   것까지 통화로 오인해 귀를 닫았다. 실사례 2026-08-12 22:56 —
            #   동백과 문답 중이던 "이거를 하나로 통합할 수 없어? …" (119자)
            #   가 두 번째 긴 발화로 집계돼 전화 모드 10분. FREEPASS 와 같은
            #   원칙으로 고친다: 길이로 막던 자리를 '누구 목소리인가' 로
            #   옮긴다(18밀리초). 대화창이 열려 있고 사장님 목소리면 그건
            #   통화가 아니라 대화다 — 집계에도 넣지 않는다(넣으면 다음
            #   긴 말씀에서 또 걸린다).
            # 캘린더에 회의가 잡혀 있는 시간대라면, 긴 말 **한 번**이면
            #   미팅이다. 두 번을 기다릴 이유가 없다 — 일정이 이미 그렇다고
            #   말하고 있고, 미팅 모드는 더 굳게 닫히는 쪽이라 늦게 들어갈수록
            #   앞부분이 새어 나간다. 실측: 미팅 모드가 켜진 흔적은 사흘 통틀어
            #   19번뿐이었다. 회의 중에 켜지지 않으면 있으나 마나다.
            if (len(text) >= config.CALL_DETECT_CHARS
                    and getattr(config, "MEETING_MODE_ENABLED", False)
                    and getattr(config, "MEETING_AUTO_FROM_CALENDAR", False)
                    and router.match_wake(text) is None
                    and not _HOLD["until"] and not _meeting_active()
                    and _calendar_meeting_now()):
                ok_mt, _why_mt = _speaker_ok(audio)
                if ok_mt:
                    call_notes.note(text)
                    _meeting_enter(f"캘린더 일정 중 긴 말 ({len(text)}자)")
                    followup_until = 0.0
                    continue

            if len(text) >= config.CALL_DETECT_CHARS:
                in_dialog = False
                if free_pass and router.match_wake(text) is None:
                    ok_c, _why_c = _speaker_ok(audio)
                    if ok_c:
                        in_dialog = True
                        log(f"대화 중 긴 말씀 ({len(text)}자, 목소리 확인) "
                            f"— 통화로 세지 않음")
                # ⚠ 통화의 증거는 '긴 소리' 가 아니라 '사장님이 길게 말씀하시는
                #   것' 이다. 통화 중이면 말하는 사람은 사장님이다.
                #
                #   길이만 세면 TV 가 전화 모드를 켠다. 실측 2026-08-15,
                #   자동 진입 70회 중 3회는 바로 앞이 '미등록 목소리' 였고
                #   46회는 화자를 보지도 않았다. 그렇게 귀를 닫으면 최장
                #   11.2분(중앙값 105초) 동안 사장님이 두 번 불러야 열린다.
                #
                #   남 목소리는 어차피 발화 하나하나가 무시된다. 무시되는
                #   소리 때문에 귀까지 닫을 이유가 없다.
                if not in_dialog and getattr(config, "CALL_DETECT_OWNER_ONLY", True):
                    ok_l, why_l = _speaker_ok(audio)
                    if not ok_l:
                        log(f"긴 소리지만 사장님 목소리가 아님(유사도 {why_l}) "
                            f"— 통화로 세지 않음 ({len(text)}자)")
                        in_dialog = True      # 집계에서 뺀다
                if not in_dialog:
                    now_m = time.monotonic()
                    _LONG_RUN[:] = [t0 for t0 in _LONG_RUN
                                    if now_m - t0 < config.CALL_DETECT_WINDOW_SEC]
                    _LONG_RUN.append(now_m)
                    if (len(_LONG_RUN) >= config.CALL_DETECT_COUNT
                            and router.match_wake(text) is None):
                        _LONG_RUN.clear()
                        # 캘린더에 진행 중 일정이 있으면 전화가 아니라
                        # 미팅이다 — 더 굳게 닫는 미팅 모드로 (2026-08-13).
                        if (getattr(config, "MEETING_MODE_ENABLED", False)
                                and getattr(config, "MEETING_AUTO_FROM_CALENDAR", False)
                                and _calendar_meeting_now()):
                            call_notes.note(text)
                            _meeting_enter("캘린더 일정 중 긴 대화 감지")
                            followup_until = 0.0
                            continue
                        _phone_enter(f"통화 감지 — {len(text)}자 발화 연속")
                        followup_until = 0.0
                        continue

            command = router.match_wake(text)
            if command is None:
                # ③ 대화창이 열려 있어도, 호출어 없는 '긴' 발화는 안 받는다.
                #    동백에게 하는 명령은 짧다 — 길면 남에게 하는 말이다.
                if free_pass and len(text) > config.FREEPASS_MAX_CHARS:
                    # ⚠ 버리기 전에 누구 목소리인지 본다.
                    #
                    #   이 상한은 통화 소리가 새어드는 걸 막으려는 것이다.
                    #   그런데 누가 말했는지 보지도 않고 버려서, 대화창 안에서
                    #   사장님이 길게 말씀하신 것까지 통째로 사라졌다.
                    #   실측 2026-08-12 22:36 — 98자짜리 지시가 버려졌고
                    #   사장님께는 "또 아무말이 없어" 로 보였다.
                    #
                    #   화자 확인은 18밀리초다(받아쓰기의 80분의 1). 이미 받아쓴
                    #   뒤라 추가 비용도 없다. 사장님이면 받고, 아니면 버린다.
                    ok_long, why_long = _speaker_ok(audio)
                    if not ok_long:
                        log(f"대화창이지만 너무 길고 남 목소리(유사도 {why_long}) "
                            f"— 무시 ({len(text)}자): {text[:30]!r}")
                        followup_until = 0.0     # 창도 닫는다 — 통화가 시작된 것
                        continue
                    log(f"대화창에서 길게 말씀하심 ({len(text)}자) — 받습니다")
                if not free_pass:
                    # 왜 버렸는지 남긴다. 길게 말한 게 통째로 사라지면
                    # 사장님은 '응답을 안 한다' 로만 보이고 원인을 알 수 없다.
                    if len(text) > 12:
                        # ⚠ '창이 있었는데 지났다' 와 '창이 아예 없었다' 는
                        #   전혀 다른 일이다. 앞은 사장님이 부른 뒤 늦게
                        #   말씀하신 것(우리 잘못)이고, 뒤는 그냥 주변 소리다.
                        #
                        #   창이 한 번도 안 열렸으면 followup_until 이 0 이라
                        #   빼기 결과가 부팅 이후 시간이 된다. 실제로
                        #   "대화창 69881초 지남"(19시간)이 찍혔다. 채점기가
                        #   그걸 실패로 세면 점수가 '방이 조용한가' 를 잰다.
                        win = max(followup_until, reply_win, state["ptt_until"])
                        gap = time.monotonic() - win
                        if gap > 3600:
                            log(f"호출어 없음 — 무시 (대화창 없음): {text[:30]!r}")
                        else:
                            log(f"호출어 없음 — 무시 (대화창 {gap:.0f}초 지남): {text[:30]!r}")
                    # 호출어 없이 "여보세요" — 통화가 시작됐다. 등록 화자
                    # 목소리일 때만 인정하고 조용히 귀를 닫는다 (말대꾸하면
                    # 통화에 끼어드는 셈이다).
                    if router.is_bare_hold(router.normalize(text)):
                        ok_h, _ = _speaker_ok(audio)
                        if ok_h:
                            # 목소리 확인을 통과한 "여보세요" 다 — 사장님이
                            # 전화를 받으신 게 확실하니 통화로 인정하고 시작한다.
                            _phone_enter(f"자동: {text[:20]!r}", owner=True)
                            continue
                    # 호출어가 아니어도 '비슷하게' 들렸으면 되묻는다.
                    # 조용히 버리면 사장님은 고장난 줄 안다. ('홍배가' 실제 사례)
                    if _near_miss(text):
                        log(f"호출어 근접: {text!r} — 되물음")
                        speak.say(config.NEARMISS_MESSAGE, block=False, priority=speak.PRIORITY_NOTICE)
                        # 되물음도 동백이 한 말이다. 바로 부르기의 "네" 와
                        # 똑같이 답변 창을 열어야 한다 — 안 그러면 "부르셨나요"
                        # 를 듣고 대답하는 사이에 3초 창이 닫힌다.
                        # 실측 04:27:05 되물음 → 04:27:13 대답이 "대화창 5초
                        # 지남" 으로 버려졌다. 바로 부르기만 고치고 이 경로를
                        # 빠뜨린 탓이다.
                        _REPLIED_AT["at"] = time.monotonic()
                        followup_until = time.monotonic() + config.FOLLOWUP_WINDOW_SEC
                        nearmiss_until = followup_until
                    continue
                command = text
            # 되물은 직후의 "안 불렀는데" 는 명령이 아니라 정정이다.
            # 조용히 물러난다 — 잘못 끼어든 마당에 해명까지 하면 두 번 시끄럽다.
            if time.monotonic() < nearmiss_until and router.is_not_calling(router.normalize(command)):
                log(f"오되물음 정정: {text!r} — 조용히 물러남")
                followup_until = nearmiss_until = 0.0
                continue
            # 이번 턴이 호출어로 시작했는가 — 아래 두 관문의 기준이 된다.
            woke = router.match_wake(text) is not None

            # 말끝에서 부르셨는가 — "...그렇지? 동백아"
            #
            # 말하다가 부르실 때는 호출어가 끝에 온다. 맨 앞만 보면 그걸
            # 통째로 놓친다. 실측 2026-08-12 22:24, 통화 중에
            # "이제서야 얘가 바뀐 거지? 동백아." 하고 부르셨는데 버려졌다.
            #
            # 앞말은 버린다. 말하다가 부르셨다는 건 앞엣것이 동백에게 한
            # 말이 아니라는 뜻이다. 부름만 받고 "네" 한 뒤 다음 말을 기다린다.
            if not woke and router.wake_at_end(text):
                log(f"말끝에서 부르심 — 앞말은 버립니다: {text[:34]!r}")
                if config.CALL_ANSWER_ENABLED:
                    speak.say(config.pick(config.CALL_ANSWERS), block=False,
                              priority=speak.PRIORITY_NOTICE)
                    _REPLIED_AT["at"] = time.monotonic()
                # 부른 사람이 새 창의 주인이다 (H2). 못 알아보면 비워 둔다 —
                # 빈 주인은 판정하지 않으므로 지금까지와 같다.
                ok_w, who_w = _speaker_ok(audio)
                _REPLIED_AT["who"] = who_w if ok_w else ""
                if ok_w:
                    _far_notice_due()
                if _HOLD["until"]:            # 전화 모드였어도 부르셨으면 연다
                    _phone_exit("말끝 호명")
                followup_until = time.monotonic() + config.FOLLOWUP_WINDOW_SEC
                continue

            # 이름만 불렀으면 즉시 대답하고 다음 말을 기다린다.
            # 뒤에 명령이 붙어 있으면 "네" 하지 않는다 — 답변과 겹쳐서 시끄럽다.
            if router.is_bare_call(command):
                if config.CALL_ANSWER_ENABLED:
                    log(f"호명: {text!r} → 즉시 응답")
                    speak.say(config.pick(config.CALL_ANSWERS), block=False,
                              priority=speak.PRIORITY_NOTICE)
                    # "네" 도 동백이 한 '답변' 이다. 사장님 지시: "동백이가
                    # 답변 후에 내가 이어서 얘기하면 '동백아' 가 생략된 걸로
                    # 간주해줘." 그 창(REPLY_FOLLOWUP_SEC)이 여기서는 안 열려
                    # 있었다.
                    #
                    # 그래서 부르고 → "네" 듣고 → 말하는 사이에 3초 창이
                    # 닫혀 버렸다. 3초는 동백이 "네" 를 말하기도 전부터
                    # 흐른다. 실측(2026-08-12 04:12): 호명 8초 뒤 명령이
                    # "대화창 5초 지남" 으로 버려졌고, 사장님이 "계속 못 들은
                    # 척한다" 고 하셨다. 심지어 정정 주문 "방금 나야" 조차
                    # 같은 이유로 씹혔다.
                    #
                    # FOLLOWUP_WINDOW_SEC(3초)는 사장님이 정하신 값이라
                    # 건드리지 않는다. 여기는 '답변 뒤' 라 성격이 다르다.
                    _REPLIED_AT["at"] = time.monotonic()
                # 부른 사람이 새 창의 주인 (H2) — 못 알아보면 비워 둔다.
                ok_c, who_c = _speaker_ok(audio)
                _REPLIED_AT["who"] = who_c if ok_c else ""
                if ok_c:
                    _far_notice_due()
                    _RETRY_TOLD["at"] = 0.0   # 통과했으니 되물음 고리는 끊겼다
                followup_until = time.monotonic() + config.FOLLOWUP_WINDOW_SEC
                continue

            # 누가 말했는지 확인한다. TV·유튜브·옆 사람이 "동백아" 해도
            # 등록된 목소리가 아니면 실행하지 않는다. 조용히 무시하는 이유:
            # 방송에 대고 "등록되지 않은 목소리입니다" 라고 떠들면 그게 더 소음이다.
            # 답변/호명 창 안의 발화, 그리고 호출어를 직접 부른 발화는
            # 본인일 개연성이 높다 — 거절돼도 '남' 으로 학습하지 않는다
            # (새벽 목소리가 코호트에 박힌 사고 + 08:45 "동백아 감사합니다"
            # 0.27 이 또 박힌 사고. 이름을 부르는 건 대개 집안 사람이다).
            in_window = time.monotonic() < reply_win or woke
            ok, who = _speaker_ok(audio, bank=not in_window)
            if not ok:
                log(f"미등록 목소리(유사도 {who}) — 무시: {command!r}")
                record(source="voice", heard=text, command=command,
                       route="blocked", danger="미등록 화자", confirmed=False, reply="")
                # ⚠ 방금 부른 사람에게 침묵은 고장이다. 실사례 2026-08-13
                #   05:42 — "동백아" → "네" 직후 "한 시간 있다가 깨워줄 수
                #   있어?" 가 3연속 거절(0.341·0.446·0.397)로 조용히 무시됐고
                #   사장님은 "대답이 없어". 창 안의 애매한 거절은 알리고
                #   재시도 창을 연다. 창 밖(방송·통화 소음)은 침묵 유지 —
                #   그쪽에 대고 떠들면 그게 소음이다.
                try:
                    # who 는 "0.48·남과근접(0.47)" 꼴일 수 있다 — 앞 숫자만
                    ambiguous = float(who.split("·")[0]) >= 0.33
                except ValueError:
                    ambiguous = False
                # 이름을 부른 발화(woke)는 점수와 무관하게 되묻는다 —
                # 08:45 "동백아 감사합니다" 가 0.27 로 조용히 무시돼
                # "3번 불러야 대답한다" 가 됐다. 부르셨는데 침묵은 고장이다.
                if (in_window and ambiguous) or woke:
                    _retry_notice_due()          # 잇따른 되물음은 삼킨다
                    _REPLIED_AT["at"] = time.monotonic()
                continue

            # 사장님 말씀인 게 확인됐다 — 아까 증폭이 걸렸으면 여기서 알린다.
            _far_notice_due()
            # 목소리가 통과했으니 되물음 고리는 끊겼다. 다음에 또 막히면
            # 그건 새 사건이라 쿨다운을 기다리지 않고 알린다.
            _RETRY_TOLD["at"] = 0.0

            # H2 다자간 — 호출어 면제 창은 그 창을 연 사람의 것이다.
            # 다른 등록 화자는 자기 호출어로 시작해야 한다 (홍길동 창에
            # 김철수 말씀이 섞여 들어가던 문제 — 위키 '대화 알고리즘 연구').
            # 주인이나 화자 이름을 모르면 판정하지 않는다 — 느슨한 쪽이
            # 잘못 잠그는 쪽보다 낫다.
            #
            # ⚠ 원거리 완화가 켜진 시간대에는 이 가드를 쓰지 않는다.
            #   문턱이 0.35 로 내려가 있으면 등록자끼리도 헷갈린다 —
            #   실사례 2026-08-13 08:33, 멀리서 부르신 말씀이 '김철수' 으로
            #   찍혀 조용히 막혔다. 이름을 못 믿을 때 이름으로 잠그면
            #   사장님이 말씀을 잃는다.
            if (not woke and getattr(config, "WINDOW_OWNER_GUARD", True)
                    and not config.dawn_far_active()):
                owner = _REPLIED_AT.get("who", "")
                if owner and who and who != owner:
                    log(f"대화창 주인은 {owner} — {who} 님 말씀은 호출어가 "
                        f"필요합니다: {command[:24]!r}")
                    record(source="voice", heard=text, command=command,
                           route="blocked", danger="다른 화자", confirmed=False, reply="")
                    continue

            # "방금 나야" — 직전에 거절된 발화를 본인 지문으로 되살린다.
            #
            # ⚠ 이 말이 통과한 뒤에만 받는다. 거절된 사람이 정정할 수 있으면
            #   아무나 "방금 나야" 한마디로 자기 목소리를 등록하게 된다.
            #   본인 목소리가 아예 안 통하는 상황이면 정정이 아니라
            #   "목소리 등록해줘" 로 다시 등록하는 게 맞다.
            # "전화 끝났어" — 통화를 정리해 위키에 남긴다 (0 토큰).
            #
            # 전에는 이 말이 클로드로 가서 $0.22 를 쓰고 쓸모없는 확인 답변이
            # 돌아왔다. 사장님 지적(2026-08-12): "전화 끝났어 하면 '네
            # 알겠습니다' 아니면 '통화 내용은 뭐였는데요? 요약 정리하겠습니다'
            # 메일 알림 켜고 끄기 — 로컬에서 0초에 끝난다.
            #
            # ⚠ 이걸 클로드로 보내면 안 된다. 사장님이 "메일 들어오면 알려줘"
            #   하셨을 때 나온 답이 "도구가 없어서 안 된다" 였다 (2026-08-16).
            #   기능은 있었고 꺼져 있었을 뿐인데, 설정을 만질 길이 없으니
            #   클로드가 제 도구 목록만 보고 없다고 답한 것이다.
            _mail_mode = router.mail_alert_intent(command)
            if _mail_mode:
                # 누구를 기다리시는지 말씀하셨으면 그것부터 적는다.
                # 실사례 2026-08-16 12:58 — "강남 한빛건설에서 메일 온 거 없어?
                # 조만간 30분 이내 올 거니까 오는 대로 바로 알림 줘."
                _who = router.mail_watch_target(command)
                if _who and _mail_mode != "끔":
                    mail_alert.watch_add(_who)
                    said = f"{_who} 메일 오면 바로 알려드릴게요. 5분마다 봅니다."
                    log(f"메일 기다림 추가: {_who}")
                    speak.say(said, block=False)
                    _REPLIED_AT["at"] = time.monotonic()
                    record(source="voice", heard=text, command=command,
                           route="local", reply=said, effective_input=0, cost_usd=0)
                    followup_until = time.monotonic() + config.FOLLOWUP_WINDOW_SEC
                    continue
                if _mail_mode == "끔":
                    mail_alert.watch_clear()
                now_mode = mail_alert.set_mode(_mail_mode)
                said = {
                    "끔": "메일 알림을 껐어요.",
                    "vip": "중요한 발신만 알려드릴게요.",
                    "사람": "사람이 보낸 메일이 오면 알려드릴게요. "
                            "서비스 알림은 빼고요.",
                    "전부": "메일이 오면 전부 알려드릴게요.",
                }.get(now_mode, "메일 알림을 바꿨어요.")
                log(f"메일 알림 설정: {now_mode}")
                speak.say(said, block=False)
                _REPLIED_AT["at"] = time.monotonic()
                record(source="voice", heard=text, command=command,
                       route="local", reply=said, effective_input=0, cost_usd=0)
                followup_until = time.monotonic() + config.FOLLOWUP_WINDOW_SEC
                continue

            # 정리 끝에 여쭤 둔 일정 후보가 있으면, 승인 한마디로 넣는다.
            #
            # ⚠ 여기가 '바로 등록' 을 대신하는 자리다. 통화 받아쓰기는 메일보다
            #   험해서 그대로 넣으면 캘린더가 더러워진다 — 2026-08-15 에 그렇게
            #   생긴 56건을 지웠다. 뽑아 두고 한마디를 받는다.
            # ⚠ 후보가 있을 때만 '네' 를 승인으로 읽는다. 평소의 '네' 까지
            #   승인으로 보면 엉뚱한 것이 들어간다. 30분이 지나면 후보는
            #   스스로 사라진다.
            if call_notes.has_pending() and router.is_confirmation(command):
                import calendar_local

                events, src = call_notes.take_events()
                done = []
                for ev in events:
                    try:
                        when = datetime.fromisoformat(ev["when"])
                    except (ValueError, KeyError):
                        continue
                    if calendar_local.create(ev["title"], when,
                                             float(ev.get("hours") or 1.0)):
                        done.append(ev["title"])
                said = (f"{len(done)}건 등록했어요. " + ", ".join(done[:3])
                        if done else "등록할 일정이 남아 있지 않네요.")
                log(f"{src} 일정 후보 등록: {len(done)}/{len(events)}건")
                speak.say(said, block=False)
                _REPLIED_AT["at"] = time.monotonic()
                record(source="voice", heard=text, command=command,
                       route="local", reply=said, effective_input=0, cost_usd=0)
                followup_until = time.monotonic() + config.FOLLOWUP_WINDOW_SEC
                continue

            if call_notes.is_end_call(router.normalize(command)):
                if _HOLD["until"]:            # 전화 모드였으면 푼다 —
                    # 정리는 아래에서 직접 말로 하므로 자동 정리는 끈다
                    _phone_exit("전화 끝났어", summarize=False)
                said, path = call_notes.save()
                log(f"통화 정리: {said[:60]}" + (f" → {path.name}" if path else ""))
                speak.say(said, block=False)
                _REPLIED_AT["at"] = time.monotonic()
                record(source="voice", heard=text, command=command,
                       route="local", reply=said, effective_input=0, cost_usd=0)
                followup_until = time.monotonic() + config.FOLLOWUP_WINDOW_SEC
                continue

            if (router.is_voice_correction(router.normalize(command))
                    or router.is_voice_enroll_request(router.normalize(command))):
                import voiceprint

                aud, at = _LAST_REJECT["audio"], _LAST_REJECT["at"]
                fresh = aud is not None and time.monotonic() - at < config.VOICE_FORGIVE_WINDOW_SEC
                if fresh and voiceprint.forgive(who, aud):
                    _LAST_REJECT["audio"] = None
                    reply = f"방금 그 말씀을 {who} 목소리로 새로 배웠습니다."
                # 거절된 발화가 없으면 '지금 이 목소리' 를 배운다.
                #
                # 전에는 여기서 "되살릴 발화가 없습니다" 로 끝났다. 그런데
                # 사장님이 원하시는 건 "지금 목소리를 기억해" 이지 "아까 그거
                # 되살려" 가 아니다. 2026-08-12 새벽에 다섯 번을 말씀하셨는데
                # 매번 같은 거절만 돌아갔다 — 거절된 발화가 없으면 영영 못
                # 배우는 구조였다.
                #
                # 안전하다: 이 자리는 _speaker_ok 를 이미 통과한 뒤라 who 가
                # 확정돼 있다. 남이 자기 목소리를 밀어넣을 수는 없다.
                # 그리고 지금 이 목소리야말로 배워야 할 것이다 — 새벽이든
                # 감기든, 지금 조건의 목소리가 지문에 없어서 막히는 것이니까.
                elif voiceprint.forgive(who, audio):
                    reply = f"지금 목소리를 {who} 목소리로 기억했습니다."
                else:
                    reply = "목소리를 기억하지 못했습니다. 다시 말씀해 주세요."
                log(f"목소리 정정: {reply}")
                speak.say(reply, block=False)
                record(source="voice", heard=text, command=command,
                       route="local", reply=reply, effective_input=0, cost_usd=0)
                followup_until = time.monotonic() + config.FOLLOWUP_WINDOW_SEC
                continue

            # 목소리 등록은 마이크를 쥔 여기서만 할 수 있다.
            # handle() 로 넘기면 listener 를 못 잡는다.
            #
            # 위의 _speaker_ok 를 이미 통과했다는 점이 중요하다 — 등록은
            # 명령 권한을 나눠주는 일이라, 이미 등록된 사람만 할 수 있어야 한다.
            # (아직 아무도 등록 안 된 상태에서는 열려 있어 첫 등록이 가능하다)
            enroll_name = router.enroll_request(command)
            if enroll_name is not None:
                state["ptt_until"] = 0.0
                log(f"목소리 등록 요청: {enroll_name or '(이름 미지정)'}")
                reply = enroll_by_voice(listener, enroll_name)
                speak.say(reply)
                record(source="voice", heard=text, command=command,
                       route="local", reply=reply, effective_input=0, cost_usd=0)
                followup_until = time.monotonic() + config.FOLLOWUP_WINDOW_SEC
                continue

            state["ptt_until"] = 0.0

            # 전화 모드 요청 — 이어 듣기 전에 바로 닫는다. 전화벨이 울리는
            # 중에 collect_turn 으로 뜸을 들이면 이미 늦는다.
            if router.is_hold_request(router.normalize(command)):
                # 안내는 봉인 '전에' 큐에 넣는다 — 이미 큐에 든 말은 나간다.
                speak.say("네, 전화 끝나면 다시 들어라고 해주세요.", block=False)
                # 사장님이 직접 시키신 것이다 (이 자리는 화자 확인을 이미
                # 지났다) — 통화가 맞으니 정리해 남긴다.
                _phone_enter("명령", owner=True)
                record(source="voice", heard=text, command=command,
                       route="local", reply="전화 모드", effective_input=0, cost_usd=0)
                continue

            # ① 말이 정말 끝날 때까지 이어 듣는다.
            command = collect_turn(listener, command, woke=woke)

            # ② 답변 중이거나 처리 중에 말을 보태셨으면 앞 명령과 합친다.
            #    사람이라면 "아까 그거 말인데" 를 알아듣는다. 반쪽만 듣고
            #    만들던 답은 버리고(세대를 올려), 합친 걸로 통째로 다시 묻는다.
            # ⚠ "그만" 은 보탠 말이 아니라 정지 신호다. 병합에 앞서 본다.
            #   여기서 안 가르면 "그만해" 가 앞 명령에 이어 붙어 통째로 다시
            #   실행된다 — 멈추라는 말이 재실행이 되는 것이다.
            #   2026-08-14 15:01~15:04 실사례: 같은 482자를 여섯 번 읽었고,
            #   사장님이 "그만해" 하실 때마다 한 번씩 더 읽었다.
            if router.is_stop_speaking(command):
                speak.stop()
                bump_generation()          # 처리 중이던 앞 답도 버린다
                _last_command, followup_until, _merge_count = "", 0.0, 0
                log(f"정지 요청 — 읽던 것을 멈춥니다: {command[:30]!r}")
                continue

            merged_now = False
            if (config.MERGE_ON_INTERRUPT and _last_command
                    and (was_barge or is_busy())
                    and time.monotonic() - _last_command_at < config.MERGE_WINDOW_SEC):
                merged = f"{_last_command} {command}"
                # ⚠ 목소리와 무관한 두 문턱. 아래 화자 검사보다 '앞' 이어야
                #   한다 — 뚫린 곳이 바로 그 화자 예외였다. 넘으면 붙이지
                #   않고 버퍼를 비운다. 버리는 게 아니라 이번 말만 새 명령으로
                #   시작하는 것이다.
                if len(merged) > config.MERGE_MAX_CHARS:
                    log(f"보탠 말이 상한을 넘음 ({len(merged)}자 > "
                        f"{config.MERGE_MAX_CHARS}) — 합치지 않고 새로 시작")
                    _last_command, _last_command_at, _merge_count = "", 0.0, 0
                elif _merge_count >= config.MERGE_MAX_COUNT:
                    log(f"한 버퍼에 {_merge_count}번 붙었음 — 그만 붙이고 "
                        f"새로 시작: {command[:30]!r}")
                    _last_command, _last_command_at, _merge_count = "", 0.0, 0
                else:
                    merged_now = True
            if merged_now:
                # ⚠ 합치면 길어진다. 호출어 없이 시작한 말이 상한을 넘으면
                #   합치지 않고 통째로 버린다 — 통화 소리가 앞 명령에 계속
                #   달라붙어 매번 클로드로 가던 그 경로다 (실측 20:25).
                #
                # ⚠ 단, 버리기 전에 누구 목소리인지 본다 (FREEPASS 와 같은
                #   원칙). 실사례 2026-08-12 22:55 — 답변 중에 말을 보태신
                #   것이 132자가 됐다고 통째로 버려져 "질문했는데 조용"
                #   이 됐다. 사장님 목소리면 길어도 받는다.
                if not woke and len(merged) > config.COMMAND_MAX_CHARS_NO_WAKE:
                    ok_m, why_m = _speaker_ok(audio)
                    if not ok_m:
                        log(f"보탠 말이 너무 길고 남 목소리(유사도 {why_m}) "
                            f"— 버림 ({len(merged)}자)")
                        _last_command, followup_until, _merge_count = "", 0.0, 0
                        _LONG_RUN.append(time.monotonic())
                        continue
                    log(f"보탠 말이 길지만 사장님 목소리 ({len(merged)}자) — 받습니다")
                log(f"말을 보태심 — 앞 명령과 합쳐 다시 묻는다: {_last_command[:30]!r}")
                command = merged
                _merge_count += 1
                speak.stop()
                bump_generation()

            # ⚠ 마지막 관문. 호출어 없이 들어온 말이 여기까지 길어졌다면
            #   동백에게 한 말이 아니다 — 조각은 짧아도 이어 붙으면 길어진다.
            #   여기도 버리기 전에 화자를 본다. 실사례 2026-08-12 22:50 —
            #   대화창 안에서 하신 132자 말씀이 통째로 버려졌다.
            #   (화자 확인은 첫 조각 기준이다 — 뒤에 붙은 조각까지 다
            #    사장님이라는 보장은 없지만, FREEPASS 관문과 같은 기준이고
            #    남 목소리로 시작한 덩어리는 여기서 확실히 걸러진다.)
            if not woke and len(command) > config.COMMAND_MAX_CHARS_NO_WAKE:
                ok_nw, why_nw = _speaker_ok(audio)
                if not ok_nw:
                    log(f"호출어 없이 너무 길고 남 목소리(유사도 {why_nw}) "
                        f"— 버림 ({len(command)}자): {command[:30]!r}")
                    _last_command, followup_until, _merge_count = "", 0.0, 0
                    continue
                # ⚠ 사장님 목소리인 것만으로는 모자라다. 남과 대화하실 때도
                #   사장님 목소리다 — 화자로는 '누가 말했나' 만 알지 '누구에게
                #   한 말인가' 는 모른다. 2026-08-14 18:04 사고가 그 구멍으로
                #   들어왔다: 옆 대화가 300자짜리 명령이 되어 음소거가 걸렸고
                #   13시간 소리가 꺼져 있었다.
                #
                #   그래서 하나 더 본다 — 시키는 말로 끝나는가.
                #   동백에게 하는 말은 결국 뭘 시키므로 요청 어미로 끝난다.
                #   옆 사람과의 대화는 서술·질문이 섞이다 아무렇게나 끝난다.
                #
                #   실측(transcript 전량): 호출어 없이 120자 넘는 84건 중
                #   83건이 여기서 걸린다. 49종을 눈으로 확인했고 전부 통화·
                #   잡담·TV 였다 — 정당한 지시는 한 건도 섞이지 않았다.
                #   2026-08-16: 이 판단을 router.is_for_me 로 옮겼다. 하는 일은
                #   같되 두 가지가 엄해졌다 — 시키는 말을 **문장 끝에서만**
                #   보고(한가운데의 '해줘' 는 남에게 한 부탁이다), 남에게 하는
                #   말의 표시(전언·타인 호칭·맞장구)도 함께 본다.
                #   실측 시험대: tools/eval_intrusion.py
                if not router.is_for_me(command):
                    log(f"호출어 없이 길고 나에게 한 말도 아님 — 버림 "
                        f"({len(command)}자): {command[:30]!r}")
                    _last_command, followup_until, _merge_count = "", 0.0, 0
                    continue
                log(f"호출어 없이 길지만 사장님이 시키는 말 ({len(command)}자) — 받습니다")

            # ⚠ 짧은 말에도 '남에게 한 말' 표시는 있다 — "여보, 그거 어딨어?",
            #   "형님 그러시더라고", "그렇죠?". 위 관문은 길이가 넘을 때만
            #   보므로 이런 건 그대로 통과해 대답이 나갔다. 대화창이 열려
            #   있으면 더 잘 통과한다.
            #
            #   호출어로 부르신 말은 여기서 보지 않는다 — 부르셨으면 나에게
            #   하신 말이 맞고, 그 판단을 낱말로 뒤집으면 안 된다.
            if not woke and not router.is_for_me(command):
                log(f"나에게 한 말로 보이지 않음 — 가만히 있습니다: {command[:40]!r}")
                _last_command, followup_until, _merge_count = "", 0.0, 0
                continue

            _last_command = command
            _last_command_at = time.monotonic()
            # 붙여서 만든 말이 아니면 눈덩이 계수를 처음으로 되돌린다.
            if not merged_now:
                _merge_count = 0
            log(f"명령: {command!r}")
            # 폰에 먼저 띄운다. 답은 나오는 대로 이 메시지를 채운다.
            threading.Thread(target=_mirror_open, args=(command,),
                             daemon=True).start()

            # ③ 복명복창 — 알아들었다는 걸 바로 알린다.
            #    알림(NOTICE)이라 답이 빨리 나오는 로컬 명령에서는 답변이
            #    이걸 끊고 나간다. "지금 몇 시야" 까지 되읊으면 되레 느리다.
            #
            #    ⚠ 호칭과 같은 규칙이다. 사장님 지시(2026-08-12):
            #      "복명복창 하는 것도 처음에 한 번만. 제대로 알아듣고 있는지
            #       확인해 보라는 차원에서 하는 거지, 계속 연결해서 할 때마다
            #       그렇게 할 필요는 없어. '홍길동님 답변드리겠습니다' 하고
            #       동일한 경우인 거야."
            #
            #    쓸모는 '제대로 들었는지 확인' 하나뿐이다. 대화가 이어지는
            #    동안에는 이미 확인이 된 상태라 매번 되읊으면 잔소리가 된다.
            if config.ECHO_BACK_ENABLED and _should_echo(who):
                said = echo_back(command)
                if said:
                    speak.say(said, block=False, priority=speak.PRIORITY_NOTICE)

            # 위험 판정과 음성 승인은 '여기서' 끝낸다.
            #
            # 승인은 listener 로 대답을 받아야 하는데, 실행 스레드에서
            # 부르면 이 루프와 같은 오디오 큐를 두고 다투게 된다.
            # 누가 어느 블록을 가져갈지 모르니 둘 다 말을 놓친다.
            hit = router.danger_hit(command)
            gated = bool(hit) and not (config.DEV_MODE
                                       and hit == router.SAFE_ONLY_REASON)
            if gated and not confirm_by_voice(listener, command, hit):
                record(source="voice", heard=text, command=command,
                       route="blocked", danger=hit, confirmed=False, reply="")
                # ⚠ 거절당한 말은 죽은 말이다. 버퍼에 남겨두면 다음에 들린
                #   소리가 여기에 달라붙어(MERGE_ON_INTERRUPT) 같은 위험
                #   명령을 처음부터 다시 물어본다.
                #
                #   2026-08-15 21:55~21:57 실사례. TV 를 켜둔 거실에서
                #   "이거 비밀번호가 뭐더라" 가 버퍼에 남아, 그 뒤에 들린
                #   "유튜브 뭐야?"·"로그인을 바꾸자"·"그냥 조용히 있어?" 가
                #   차례로 붙으며 네 번 되살아났다. 사장님은 "아니 하지마",
                #   "그냥 조용히 있으라고" 하며 네 번 거절하셨는데 네 번 다시
                #   물었다. 게이트는 제 일을 했지만 거절이 기억되지 않았다.
                #
                #   이어말 창도 닫는다. 거절 직후에는 호출어를 다시 받는 게
                #   맞다 — 거절한 말에 무언가 이어 붙는 건 대개 그 말이
                #   동백에게 한 말이 아니었다는 뜻이다.
                _last_command, _last_command_at, _merge_count = "", 0.0, 0
                followup_until = 0.0
                continue

            # 실행은 넘기고 곧바로 다시 듣는다.
            # 예전엔 여기서 Claude 응답을 기다리느라 최대 3분간 귀를 닫았다.
            # 그동안 "동백아" 를 불러도 아무 반응이 없어 고장 난 줄 알게 된다.
            if not submit_command(command, heard=text, who=who):
                speak.say("처리할 일이 밀려 있습니다. 잠시 후에 말씀해 주세요.",
                          block=False, priority=speak.PRIORITY_NOTICE)
            followup_until = time.monotonic() + config.FOLLOWUP_WINDOW_SEC


# ─────────────────────────────────────────────────────────
_ROUTE_MARK = {
    "local": ("·", "로컬"),
    "claude": ("→", "클로드"),
    "blocked": ("✕", "차단"),
    "error": ("!", "오류"),
}


def show_log(n: int) -> int:
    """동백이 무슨 일을 했는지 사람이 읽을 수 있게 출력."""
    rows = dbstore.rows()
    if not rows:
        print("아직 기록이 없습니다.")
        return 0

    total_cost = sum(r.get("cost_usd") or 0 for r in rows)
    print(f"\n동백 활동 기록 — 전체 {len(rows)}건 중 최근 {min(n, len(rows))}건\n")
    for r in rows[-n:]:
        mark, label = _ROUTE_MARK.get(r.get("route", ""), ("?", r.get("route", "")))
        ts = r.get("ts", "")[5:19].replace("T", " ")
        src = {"voice": "음성", "http": "아이폰", "cli": "터미널"}.get(r.get("source"), r.get("source", ""))
        cost = r.get("cost_usd")
        tail = f"  {cost * 1400:,.0f}원" if cost else ("  0원" if r.get("route") == "local" else "")
        print(f"  {ts}  [{src}] {mark} {label}{tail}")
        heard, cmd = r.get("heard", ""), r.get("command", "")
        if heard and heard != cmd:
            print(f"      들림: {heard}")
        print(f"      명령: {cmd}")
        if r.get("danger"):
            state = "승인됨" if r.get("confirmed") else "취소됨"
            print(f"      ⚠ 위험표현 '{r['danger']}' → {state}")
        if r.get("error"):
            print(f"      오류: {r['error']}")
        elif r.get("reply"):
            print(f"      답변: {r['reply'][:100]}")
        print()

    blocked = sum(1 for r in rows if r.get("route") == "blocked")
    local = sum(1 for r in rows if r.get("route") == "local")
    print(f"누적 — 총 {len(rows)}건 / 로컬처리 {local}건(0원) / 차단 {blocked}건")
    print(f"누적 비용 약 {total_cost * 1400:,.0f}원  (${total_cost:.2f})")
    print(f"\n원본: {dbstore.DB_PATH} (transcript 표)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--devices", action="store_true", help="입력 장치 목록")
    ap.add_argument("--check", action="store_true", help="클로드 연결 확인")
    ap.add_argument("--text", metavar="명령", help="마이크 없이 명령 경로 테스트")
    ap.add_argument("--file", metavar="WAV", help="음성 파일 STT 테스트")
    ap.add_argument("--listen", action="store_true", help="받아쓰기만 (클로드 미호출)")
    ap.add_argument("--log", nargs="?", type=int, const=20, metavar="N",
                    help="동백이 한 일 보기 (기본 최근 20건)")
    ap.add_argument("--misheard", nargs="?", type=int, const=20, metavar="N",
                    help="승인이 안 된 응답 모아보기 (whisper 오인식 후보)")
    ap.add_argument("--reset", action="store_true", help="세션 초기화")
    ap.add_argument("--quiet", action="store_true", help="TTS 끄고 텍스트만")
    ap.add_argument("--enroll", nargs="?", const="사장님", metavar="이름",
                    help="목소리 등록 (같은 이름이면 지문 누적)")
    ap.add_argument("--voices", action="store_true", help="등록된 목소리 목록")
    ap.add_argument("--forget", metavar="이름", help="등록된 목소리 삭제")
    ap.add_argument("--rename", nargs=2, metavar=("옛이름", "새이름"),
                    help="지문은 그대로 두고 이름만 바꾼다")
    ap.add_argument("--mail-digest", action="store_true",
                    help="메일에서 업무 일정을 찾아 캘린더에 등록 (매일 11시 자동)")
    ap.add_argument("--mail-digest-dry", action="store_true",
                    help="위와 같되 등록하지 않고 무엇을 넣을지만 보여준다")
    args = ap.parse_args()

    if args.quiet:
        speak.say = lambda *a, **k: None  # type: ignore[assignment]

    if args.log is not None:
        return show_log(args.log)

    if args.misheard is not None:
        return show_misheard(args.misheard)

    if args.reset:
        bridge.reset_session()
        log("세션 초기화 완료")
        return 0

    if args.devices:
        import audio as audio_mod

        print("입력 장치:")
        print(audio_mod.describe_devices())
        try:
            print(f"\n자동 선택: [{audio_mod.pick_device()}]")
        except RuntimeError as e:
            print(f"\n자동 선택 실패: {e}")
        return 0

    if args.check:
        ok, msg = bridge.healthcheck()
        print(("✓ " if ok else "✗ ") + msg)
        return 0 if ok else 1

    if args.file:
        import audio as audio_mod

        text = audio_mod.transcribe_file(args.file)
        print(f"인식: {text!r}")
        return 0

    if args.text:
        reply = handle(args.text, confirm=confirm_by_text, source="cli")
        if reply:
            print(f"\n동백: {reply}")
            speak.say(reply)
        return 0

    if args.listen:
        import audio as audio_mod

        with audio_mod.Listener() as listener:
            log("받아쓰기 모드. Ctrl+C 로 종료.")
            for a in listener.utterances():
                text = audio_mod.transcribe(a)
                if text:
                    cmd = router.match_wake(text)
                    tag = f"  → 호출어 인식! 명령={cmd!r}" if cmd is not None else ""
                    print(f"{text!r}{tag}")
        return 0

    if args.mail_digest or args.mail_digest_dry:
        import mail_digest

        summary = mail_digest.run(dry_run=args.mail_digest_dry)
        print(summary)
        if not args.mail_digest_dry:
            speak.say(summary)
        return 0

    if args.voices:
        return show_voices()

    if args.rename:
        import voiceprint

        old, new = args.rename
        if voiceprint.rename(old, new):
            print(f"'{old}' → '{new}' 으로 바꿨습니다. 지문은 그대로입니다.")
            return show_voices()
        print(f"'{old}' 는 등록돼 있지 않습니다. --voices 로 확인하세요.")
        return 1

    if args.forget:
        import voiceprint

        if voiceprint.forget(args.forget):
            print(f"'{args.forget}' 목소리를 지웠습니다.")
            return 0
        print(f"'{args.forget}' 는 등록돼 있지 않습니다. --voices 로 확인하세요.")
        return 1

    if args.enroll:
        return do_enroll(args.enroll)

    return run_daemon()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        speak.stop()
        print("\n[동백] 종료")
        sys.exit(0)
