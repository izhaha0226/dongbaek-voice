#!/usr/bin/env python3
"""동백 스킬 — 선언 파일(skills/*.md)로 늘어나는 로컬 능력 (PLAN-skills 1단계).

왜 코드 생성이 아니라 선언인가:
  스스로 만든 코드는 도구 권한 3단을 안에서 뚫을 수 있다. 선언은 이
  실행기가 해석하므로 스킬이 아무리 늘어도 권한은 실행기 것뿐이다.
  action 은 아래 SAFE_ACTIONS 화이트리스트 안에서만 고른다 — 목록에
  없는 이름은 적재 때 거부되고, Bash·임의 코드는 표현 자체가 안 된다.

파일 꼴 (yaml 의존 없음 — 단순 키: 값):

    ---
    name: 광고-주간요약
    triggers: 주간 광고 요약 | 이번주 광고 어땠
    action: ads_week
    approved: true
    ---
    설명 자유 텍스트 (사람용)

- triggers 는 | 로 구분한 문구들. normalize(구두점 제거·소문자) 후
  부분일치로 잰다 — router 의 다른 규칙들과 같은 잣대.
- approved: false 면 실려 있어도 안 돈다 (위험 스킬은 승인 후 활성).
- 같은 트리거가 두 스킬에 있으면 나중 파일이 지고 로그로 알린다.

만드는 손(2단계)은 dongbaek.handle 의 "스킬로 만들어" 경로 — 직전 문답을
클로드(ask_once)가 이 선언으로 변환해 저장한다. 셋째 손(자동 후보)은
self_improve 가 3시간마다 rule_gaps 를 보고 초안을 만든다 (3단계, 예정).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import config

SKILLS_DIR = config.ROOT / "skills"
REMOVED_DIR = SKILLS_DIR / "removed"


# ─────────────────────────────────────────────────────────
# 실행 화이트리스트 — 스킬이 고를 수 있는 전부.
# 조회 또는 승인 없이 해도 되는 일만 넣는다. 함수는 (원문) -> 답 문자열.
# ─────────────────────────────────────────────────────────
def _ads_today(_text: str) -> str:
    import ads_local

    return ads_local.speak("오늘 성과") or "오늘 광고 자료를 못 가져왔습니다."


def _ads_week(_text: str) -> str:
    from datetime import date, timedelta

    import ads_local

    end = date.today()
    start = end - timedelta(days=6)
    return (ads_local.analysis(start, end, "최근 일주일")
            or "이번 주 광고 자료를 못 가져왔습니다.")


def _calendar_today(_text: str) -> str:
    import calendar_local

    return calendar_local.speak_events(days=1, limit=10)


def _calendar_week(_text: str) -> str:
    import calendar_local

    return calendar_local.speak_events(days=7, limit=10)


def _weather_today(_text: str) -> str:
    import weather_local

    return weather_local.today() or "날씨 자료를 못 가져왔습니다."


def _score_today(_text: str) -> str:
    import score

    return score.speak_report(days=1)


def _history_search(text: str) -> str:
    import dbstore

    # "…에 대해 지난 대화 찾아줘" 류 — 트리거 문구를 뺀 나머지가 검색어
    q = re.sub(r"(지난|이전|전에|대화|기록|찾아줘|찾아봐|검색해줘)", "", text).strip()
    return dbstore.search(q or text, days=14)


def _mail_recent(_text: str) -> str:
    import mail_local

    rows = mail_local.received_brief() or []
    if not rows:
        return "최근 새 메일이 없습니다."
    heads = [str(r.get("subject") or r.get("from") or "?")[:30] for r in rows[:5]]
    return f"최근 메일 {len(rows)}통: " + ", ".join(heads)


SAFE_ACTIONS = {
    "ads_today": _ads_today,
    "ads_week": _ads_week,
    "calendar_today": _calendar_today,
    "calendar_week": _calendar_week,
    "weather_today": _weather_today,
    "score_today": _score_today,
    "history_search": _history_search,
    "mail_recent": _mail_recent,
}


# ─────────────────────────────────────────────────────────
# 적재 — 파일이 바뀌었을 때만 다시 읽는다 (명령마다 불리므로)
# ─────────────────────────────────────────────────────────
_cache: dict = {"stamp": None, "skills": [], "warns": []}


def _dir_stamp() -> tuple:
    if not SKILLS_DIR.exists():
        return ()
    return tuple(sorted((p.name, p.stat().st_mtime)
                        for p in SKILLS_DIR.glob("*.md")))


def _parse(path: Path) -> dict | None:
    """front-matter 만 읽는다. 못 읽으면 None — 한 파일이 전체를 못 죽인다."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.match(r"\s*---\s*\n(.*?)\n\s*---", text, re.S)
    if not m:
        return None
    meta: dict = {"file": path.name}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    if not meta.get("name") or not meta.get("triggers") or not meta.get("action"):
        return None
    meta["triggers"] = [t.strip() for t in meta["triggers"].split("|") if t.strip()]
    meta["approved"] = str(meta.get("approved", "true")).lower() != "false"
    return meta


def load(force: bool = False) -> list[dict]:
    stamp = _dir_stamp()
    if not force and stamp == _cache["stamp"]:
        return _cache["skills"]
    skills, warns, seen_triggers = [], [], {}
    for p in sorted(SKILLS_DIR.glob("*.md")) if SKILLS_DIR.exists() else []:
        meta = _parse(p)
        if meta is None:
            warns.append(f"{p.name}: 형식을 못 읽어 건너뜀")
            continue
        if meta["action"] not in SAFE_ACTIONS:
            warns.append(f"{p.name}: 허용 목록에 없는 action '{meta['action']}' — 비활성")
            continue
        dup = [t for t in meta["triggers"] if t in seen_triggers]
        if dup:
            warns.append(f"{p.name}: 트리거 {dup} 가 {seen_triggers[dup[0]]} 와 겹침 — 이 파일이 짐")
            meta["triggers"] = [t for t in meta["triggers"] if t not in seen_triggers]
        for t in meta["triggers"]:
            seen_triggers[t] = meta["name"]
        if meta["triggers"]:
            skills.append(meta)
    _cache.update(stamp=stamp, skills=skills, warns=warns)
    return skills


def warnings() -> list[str]:
    load()
    return list(_cache["warns"])


def match(t: str, original: str) -> str | None:
    """정규화된 발화 t 가 스킬 트리거에 걸리면 실행해 답을 돌려준다."""
    for sk in load():
        if not sk["approved"]:
            continue
        for trig in sk["triggers"]:
            if trig.replace(" ", "") in t.replace(" ", ""):
                try:
                    return SAFE_ACTIONS[sk["action"]](original)
                except Exception as e:      # 스킬 하나의 실패가 전체를 못 죽인다
                    return f"스킬 '{sk['name']}' 실행 중 문제: {type(e).__name__}"
    return None


# ─────────────────────────────────────────────────────────
# 만들기·지우기·목록 — 음성 경로(dongbaek.handle)가 부른다
# ─────────────────────────────────────────────────────────
def create(name: str, triggers: list[str], action: str,
           note: str = "") -> tuple[bool, str]:
    if action not in SAFE_ACTIONS:
        return False, f"'{action}' 은 허용 목록에 없습니다. 가능한 것: {', '.join(SAFE_ACTIONS)}"
    triggers = [t.strip() for t in triggers if t.strip()]
    if not triggers:
        return False, "트리거 문구가 없습니다."
    safe_name = re.sub(r"[^가-힣a-zA-Z0-9-]", "-", name)[:40] or "스킬"
    SKILLS_DIR.mkdir(exist_ok=True)
    path = SKILLS_DIR / f"{safe_name}.md"
    if path.exists():
        return False, f"'{safe_name}' 스킬이 이미 있습니다."
    body = ("---\n"
            f"name: {safe_name}\n"
            f"triggers: {' | '.join(triggers)}\n"
            f"action: {action}\n"
            "approved: true\n"
            "---\n"
            f"{note or '음성으로 만든 스킬'} "
            f"({time.strftime('%Y-%m-%d %H:%M')})\n")
    path.write_text(body, encoding="utf-8")
    load(force=True)
    return True, f"스킬 '{safe_name}' 을 만들었습니다. 이제 '{triggers[0]}' 라고 하면 됩니다."


def remove(name_like: str) -> str:
    """지우지 않고 removed/ 로 옮긴다 — 복구 가능해야 한다."""
    for p in SKILLS_DIR.glob("*.md") if SKILLS_DIR.exists() else []:
        meta = _parse(p)
        if meta and (name_like in meta["name"] or name_like in p.name):
            REMOVED_DIR.mkdir(parents=True, exist_ok=True)
            p.rename(REMOVED_DIR / f"{int(time.time())}-{p.name}")
            load(force=True)
            return f"스킬 '{meta['name']}' 을 뺐습니다 (skills/removed 에 보관)."
    return f"'{name_like}' 이름의 스킬을 못 찾았습니다."


def listing() -> str:
    skills = load()
    if not skills:
        return "만들어진 스킬이 없습니다."
    out = [f"{s['name']} ('{s['triggers'][0]}'" +
           ("" if s["approved"] else ", 승인 대기") + ")" for s in skills]
    return f"스킬 {len(skills)}개: " + ", ".join(out)
