#!/usr/bin/env python3
"""메일 알림 — 새 메일이 오면 먼저 알린다 (사장님 지시 2026-08-16).

지시는 "메일 들어오면 알려줘" 였고, 그때 동백은 "도구가 없어서 안 된다" 고
답했다. 실제로는 알림 기능이 있었는데 VIP 목록이 비어 있어 한 번도 돌지
않았을 뿐이다 (config.MAIL_NUDGE_VIP = []). 없는 게 아니라 꺼져 있었다.

**왜 '전부 알림' 이 아닌가.** 하루 받는 메일이 40통쯤 된다. 그걸 다 소리로
읽으면 알림이 아니라 소음이고, 하루 이틀이면 꺼 달라고 하시게 된다. 그래서
기본값은 '사람이 보낸 것만' 이다 — mail_local.notice_service 가 서비스·자동
발신을 이미 가려내므로 그 판단을 그대로 쓴다.

  끔       아무 말도 안 한다
  vip      config.MAIL_NUDGE_VIP 에 있는 발신만
  사람     사람이 보낸 메일만 (기본) + VIP
  전부     서비스 알림까지 전부

말로 바꾼다 — "메일 오면 알려줘" / "메일 알림 꺼". 설정은 파일에 남아
데몬을 다시 띄워도 그대로다.
"""
from __future__ import annotations

import json

import config

_FILE = config.STATE / "mail_alert.json"

OFF, VIP, PERSON, ALL = "끔", "vip", "사람", "전부"
_MODES = (OFF, VIP, PERSON, ALL)


def mode() -> str:
    """지금 알림 방식. 파일이 없으면 vip — 여태 돌던 대로다."""
    try:
        m = json.loads(_FILE.read_text()).get("mode")
    except (OSError, ValueError):
        m = None
    return m if m in _MODES else VIP


def set_mode(m: str) -> str:
    if m not in _MODES:
        return mode()
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps({"mode": m}, ensure_ascii=False))
    except OSError:
        pass
    return m


def watch_list() -> list[str]:
    """기다리는 발신인·말머리. 여기 걸리면 방식과 무관하게 알린다."""
    try:
        w = json.loads(_FILE.read_text()).get("watch")
    except (OSError, ValueError):
        w = None
    return [str(x) for x in w] if isinstance(w, list) else []


def watch_add(keyword: str) -> list[str]:
    """"강남 한빛건설에서 메일 오면 알려줘" — 그 '강남 한빛건설' 을 적어 둔다.

    실사례 2026-08-16 12:58. 사장님이 "조만간 30분 이내 올 거니까 오는 대로
    바로 알림 줘" 하셨는데 동백은 "혼자 깨어나서 체크할 도구가 없다" 고
    답했다. 능동 루프는 이미 5분마다 깨어나고 있었다 — 없는 건 도구가
    아니라 '무엇을 기다리는지 적어 두는 자리' 였다.
    """
    kw = (keyword or "").strip()
    if len(kw) < 2 or len(kw) > 30:
        return watch_list()
    cur = watch_list()
    if kw not in cur:
        cur.append(kw)
    _write(watch=cur[-10:])          # 열 개까지만 — 기다림은 오래 쌓지 않는다
    return watch_list()


def watch_clear() -> None:
    _write(watch=[])


def _write(**fields) -> None:
    try:
        d = json.loads(_FILE.read_text())
    except (OSError, ValueError):
        d = {}
    d.update(fields)
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps(d, ensure_ascii=False))
    except OSError:
        pass


def should_alert(sender: str, address: str, subject: str) -> bool:
    """이 메일을 알릴 것인가.

    VIP 는 방식과 무관하게 늘 알린다 — 목록에 이름을 올려 두셨다는 것 자체가
    '이건 놓치지 마라' 는 뜻이다. 끔일 때만 예외다.
    """
    m = mode()
    if m == OFF:
        return False

    hay = f"{sender} {address} {subject}".lower()
    # 기다리라고 이르신 것과 VIP 는 방식과 무관하게 알린다.
    # 띄어쓰기는 받아쓰기마다 흔들리므로 지우고 견준다 ("강남 한빛건설"/"강남한빛건설").
    flat = hay.replace(" ", "")
    if any(w.lower().replace(" ", "") in flat for w in watch_list()):
        return True
    if any(v.lower() in hay for v in (config.MAIL_NUDGE_VIP or [])):
        return True
    if m == VIP:
        return False
    if m == ALL:
        return True

    # '사람' — 서비스·자동 발신이면 조용히 지나간다
    try:
        import mail_local

        return mail_local.notice_service(sender, address) is None
    except Exception:
        return True                      # 판단이 안 되면 알리는 쪽으로


def line(sender: str, subject: str) -> str:
    """소리로 읽을 한 줄. 제목은 길어서 자른다 — 전문은 폰으로 본다."""
    who = (sender or "").split("<")[0].strip().strip('"') or "누군가"
    subj = (subject or "").strip()
    return f"{who} 님 메일이 왔어요. {subj[:40]}" if subj else f"{who} 님 메일이 왔어요."
