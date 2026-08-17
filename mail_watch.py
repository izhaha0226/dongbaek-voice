"""특정 메일이 오면 오는 대로 알린다 ('그 업체 메일 오면 알려줘').

mail_digest.py 와 달리 캘린더에 아무것도 쓰지 않는다. 읽고 이름표를 대
보는 게 전부라 위험이 없다 — 그래서 승인 게이트를 거치지 않고 바로
등록할 수 있다.

  ① 감시 등록  — add(keyword)                       로컬, 0 토큰
  ② 30분마다   — launchd 가 run() 을 깨움             로컬, 0 토큰
  ③ 걸리면     — 데몬에게 말하게 하고 그 감시는 지운다  (한 번 알리면 끝)

Claude 를 부르지 않는다 — 키워드 포함 여부만 보면 되는 일에 굳이 판단을
빌릴 이유가 없다 (mail_digest 의 일정 추출과는 성격이 다르다).
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import config

STATE_FILE = config.STATE / "mail_watch.json"

# 감시 하나가 살아있는 최대 시간. "곧 온다" 는 말은 대개 하루를 안 넘긴다 —
# 등록해두고 잊으면 영원히 도는 게 아니라 이 안에서만 유효하다.
DEFAULT_TTL_HOURS = 48
# 한 번 볼 때 몇 시간치를 훑을지. 30분마다 도는데 겹치게 잡아 실행이
# 한 번 밀려도 그 사이 온 메일을 놓치지 않는다.
SCAN_HOURS = 3
MAX_SCAN = 60


def _load() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {"watches": []}
    d.setdefault("watches", [])
    return d


def _save(d: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(d, ensure_ascii=False))
    except OSError:
        pass


def add(keyword: str, note: str = "", ttl_hours: int = DEFAULT_TTL_HOURS) -> dict:
    """감시를 하나 등록한다. 같은 키워드가 이미 있으면 새로 갈아 끼운다."""
    keyword = keyword.strip()
    d = _load()
    d["watches"] = [w for w in d["watches"] if w.get("keyword") != keyword]
    watch = {
        "keyword": keyword,
        "note": (note or keyword).strip(),
        "added": datetime.now().isoformat(timespec="seconds"),
        "ttl_hours": ttl_hours,
    }
    d["watches"].append(watch)
    _save(d)
    return watch


def _expired(w: dict) -> bool:
    try:
        added = datetime.fromisoformat(w["added"])
    except (KeyError, ValueError):
        return True
    return (datetime.now() - added).total_seconds() > w.get("ttl_hours", DEFAULT_TTL_HOURS) * 3600


def _matches(w: dict, msg: dict) -> bool:
    kw = w["keyword"].lower()
    hay = f"{msg.get('from', '')} {msg.get('addr', '')} {msg.get('subject', '')}".lower()
    return kw in hay


def _notify(text: str) -> None:
    """데몬에게 대신 말하게 한다. 소리를 내는 곳은 데몬 한 곳이어야 한다."""
    try:
        token = config.TOKEN_FILE.read_text().strip()
    except OSError:
        return
    url = f"http://127.0.0.1:{config.CONTROL_PORT}/say?token={urllib.parse.quote(token)}"
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except (urllib.error.URLError, OSError):
        pass


def run() -> str:
    """등록된 감시를 전부 확인한다. 걸린 게 있으면 알리고 그 감시는 지운다."""
    import mail_local

    d = _load()
    live = [w for w in d["watches"] if not _expired(w)]
    if len(live) != len(d["watches"]):
        d["watches"] = live
        _save(d)
    if not live:
        return "감시 중인 메일이 없습니다."

    msgs = mail_local.received_brief(hours=SCAN_HOURS, max_scan=MAX_SCAN)
    if msgs is None:
        return "메일을 읽지 못했습니다."

    hits, remaining = [], []
    for w in live:
        found = next((m for m in msgs if _matches(w, m)), None)
        if found:
            who = found.get("from") or found.get("addr") or ""
            subj = found.get("subject") or ""
            hits.append(f"{w['note']} 메일 왔어요. {who}님이 {subj} 보내셨어요.")
        else:
            remaining.append(w)

    if remaining != live:
        d["watches"] = remaining
        _save(d)

    for text in hits:
        _notify(text)
    return "; ".join(hits) if hits else "아직 안 왔습니다."


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "add":
        keyword = sys.argv[2]
        note = sys.argv[3] if len(sys.argv) > 3 else ""
        w = add(keyword, note)
        print(f"감시 등록: {w['keyword']!r} ({w['note']})")
        return 0

    result = run()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[메일감시 {stamp}] {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
