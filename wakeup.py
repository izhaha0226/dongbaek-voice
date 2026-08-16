#!/usr/bin/env python3
"""기상 알람 — 전화 + 음성 + 텔레그램.

알람은 '한 채널이 실패해도 깨어나야' 의미가 있다. 그래서 세 갈래로 동시에 친다.

  ① 전화  — 맥이 연속성으로 아이폰을 빌려 건다 (`open tel://`).
            어느 쪽이 발신 아이폰인지 몰라도 되도록 **등록된 번호 전부**에 순서대로 건다.
            자기 자신에게 건 건 그냥 실패하고, 나머지 한 대가 울린다.
  ② 음성  — 맥 스피커로 반복해서 부른다. 볼륨을 강제로 올린다.
  ③ 텔레그램 — 알림이 남아 있어 나중에라도 보인다.

한 번만 울리고 스스로 무장 해제한다 (`state/wakeup.json` 의 `date` 가 오늘일 때만).
매일 울리게 하려면 `repeat: true`.
"""

import json
import os
import subprocess
import sys
import time
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402

STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "wakeup.json")

CALL_GAP_SEC = 45      # 한 번호가 울릴 시간을 준 뒤 다음 번호로
SPEAK_ROUNDS = 12      # 음성 반복 횟수
SPEAK_GAP_SEC = 20


def load() -> dict | None:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save(conf: dict) -> None:
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(conf, f, ensure_ascii=False, indent=2)


def due(conf: dict) -> bool:
    """오늘 울려야 하는 알람인가."""
    if not conf.get("enabled", True):
        return False
    if conf.get("repeat"):
        return True
    return conf.get("date") == date.today().isoformat()


def dial(number: str) -> None:
    """연속성 통화로 발신. 클릭 없이 바로 걸린다 (통화 기록으로 확인됨)."""
    subprocess.run(["open", f"tel://{number}"], check=False)


def loud() -> None:
    subprocess.run(
        ["osascript", "-e", "set volume output volume 95 without output muted"],
        check=False,
    )


def announce(text: str) -> None:
    """동백 목소리로. 실패하면 macOS say 로 내려간다."""
    try:
        import tts

        if tts.speak(text, block=True):
            return
    except Exception:
        pass
    subprocess.run(["say", "-v", "Yuna", text], check=False)


def telegram(text: str) -> None:
    try:
        import telegram_bridge as tg

        conf = tg.load_conf()
        if not conf:
            return
        for chat_id in conf.get("allowed_chat_ids", []):
            tg.send_text(conf["bot_token"], chat_id, text)
    except Exception:
        pass


def fire(conf: dict) -> None:
    when = conf.get("time", "")
    label = conf.get("label") or f"{when} 기상"
    numbers = conf.get("numbers") or []

    loud()
    telegram(f"⏰ {label} — 지금 전화를 겁니다.")

    # 전화가 가장 확실하니 먼저 건다.
    for number in numbers:
        dial(number)
        time.sleep(CALL_GAP_SEC)

    for i in range(SPEAK_ROUNDS):
        announce(config.WAKEUP_PHRASE if hasattr(config, "WAKEUP_PHRASE") else
                 "사장님, 일어나실 시간입니다.")
        if i < SPEAK_ROUNDS - 1:
            time.sleep(SPEAK_GAP_SEC)


def test() -> int:
    """지금 한 번 걸어본다 — 4시에 진짜 울리는지 미리 확인하는 용도.

    알람은 '그날 안 울렸다' 를 알 방법이 없어서, 시험 발신이 유일한 검증이다.
    무장 상태(state/wakeup.json)는 건드리지 않는다.
    """
    conf = load() or {}
    numbers = conf.get("numbers") or []
    for number in numbers:
        print(f"발신: {number}")
        dial(number)
        time.sleep(CALL_GAP_SEC)
    announce("전화 시험입니다. 울리면 되었습니다.")
    return 0


def main() -> int:
    if "--test" in sys.argv:
        return test()

    conf = load()
    if not conf:
        return 0
    if not due(conf):
        return 0

    conf["last_fired_at"] = datetime.now().isoformat(timespec="seconds")
    if not conf.get("repeat"):
        conf["enabled"] = False        # 한 번 울렸으면 스스로 내려간다
    save(conf)

    fire(conf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
