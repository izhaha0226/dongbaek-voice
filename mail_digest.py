"""받은 메일에서 업무 일정을 찾아 캘린더에 자동 등록. 매일 11시.

비용 구조가 이 파일의 설계 전부다. 할 수 있는 건 전부 로컬에서 한다.

  ① 메일 읽기      — AppleScript (mail_local)        로컬, 0 토큰
  ② 후보 고르기    — 정규식 (아래 _looks_scheduley)   로컬, 0 토큰
  ③ 일정 뽑기      — Claude 소넷 1회                  ← 여기서만 돈이 든다
  ④ 캘린더 등록    — EventKit (calendar_local)        로컬, 0 토큰

②가 핵심이다. 하루 메일이 40통이어도 일정 얘기가 있는 건 보통 두세 통이라,
정규식으로 걸러 그것만 넘기면 ③의 입력이 10분의 1로 줄어든다.
후보가 하나도 없으면 Claude 를 아예 부르지 않는다 (0원으로 끝).

자동 등록이라 되돌리기가 번거롭다. 그래서 좁게 잡는다:
  - 날짜와 시각이 '메일에 명시된' 것만. 추측하면 버린다.
  - 지난 일정은 등록하지 않는다.
  - 한 번에 MAX_EVENTS 건까지만.
  - 이미 처리한 메일과 이미 넣은 일정은 다시 넣지 않는다 (state 파일).
"""

import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

import config

STATE_FILE = config.STATE / "mail_digest.json"

# 메일에서 뽑은 일정을 캘린더에 자동으로 넣을지. 사장님 지시(2026-08-14):
# 메일만 보고 뽑은 일정은 정확하지 않다 — 정크 정리는 계속하되 등록은 끈다.
CALENDAR_ENABLED = False

# 한 번에 등록할 수 있는 상한. 넘치면 뭔가 잘못 읽은 것이다.
MAX_EVENTS = 8
# 몇 시간치 메일을 볼지. 하루 한 번 도는데 26시간을 보는 건 겹치게 두어
# 실행이 한 번 밀려도 빠지는 메일이 없게 하려는 것 (중복은 state 로 막는다).
LOOKBACK_HOURS = 26
MAX_SCAN = 40

# ── 로컬 선별 ─────────────────────────────────────────────
# 기준은 '날짜·시각이 적혀 있는가' 하나다.
#
# 처음에는 일정 낱말(미팅·회의·세미나…)도 함께 요구했는데 너무 좁았다.
# 실측해보니 '스마트축산 경진대회 3차콜', '농업박람회 부스 시공' 같은 진짜
# 업무 메일이 낱말 목록에 없다는 이유로 빠졌다. 업무 어휘는 무한해서
# 목록으로 따라잡을 수 없다 — 위험 표현을 쫓다 실패했던 것과 같은 함정이다.
#
# 반대로 날짜가 없는 메일은 애초에 일정을 만들 수 없다(등록에 날짜와 시각이
# 반드시 필요하다). 그래서 날짜 유무로만 거른다. 넓게 통과시켜도 판단은
# 소넷이 하고, 소넷이 아니라고 하면 등록되지 않는다.
_WHEN_HINT = re.compile(
    r"(\d{1,2}\s*월\s*\d{1,2}\s*일|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|"
    r"\d{1,2}\s*시\s*\d{0,2}\s*분?|\d{1,2}:\d{2}|오전|오후|"
    r"[월화수목금토일]\s*요일|오늘|내일|모레|다음\s*주|이번\s*주|차주)"
)
# 보낸 사람이 이렇게 생겼으면 사람이 보낸 업무 메일이 아니다.
# 슬랙·구글 알림처럼 날짜가 잔뜩 든 자동 메일을 여기서 걷어낸다.
_NOREPLY = re.compile(r"(no-?reply|noreply|donotreply|newsletter|notification[s]?@|"
                      r"mailer-daemon|bounce|@slack\.com|@google\.com|"
                      r"@accounts\.google|@facebookmail|@linkedin)", re.I)


def _looks_scheduley(msg: dict) -> bool:
    if _NOREPLY.search(msg.get("from", "")):
        return False
    text = f"{msg.get('subject', '')}\n{msg.get('body', '')}"
    return bool(_WHEN_HINT.search(text))


# ── 처리 기록 ─────────────────────────────────────────────
def _load_state() -> dict:
    try:
        d = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {"seen": [], "registered": []}
    d.setdefault("seen", [])
    d.setdefault("registered", [])
    return d


def _save_state(st: dict) -> None:
    # 무한정 쌓이지 않게 최근 것만 남긴다
    st["seen"] = st["seen"][-500:]
    st["registered"] = st["registered"][-500:]
    try:
        STATE_FILE.write_text(json.dumps(st, ensure_ascii=False))
    except OSError:
        pass


def _save_state_unless_dry(st: dict, dry_run: bool) -> None:
    """시험 실행은 기록을 남기지 않는다.

    남기면 '본 메일' 로 찍혀서, 정작 진짜 실행 때 그 메일들이 건너뛰어진다.
    시험해보고 나면 그날 일정이 통째로 등록 안 되는 셈이다.
    """
    if not dry_run:
        _save_state(st)


def _event_key(ev: dict) -> str:
    """같은 일정인지 판단할 열쇠. 제목이 조금 달라도 시각이 같으면 같은 것으로 본다."""
    title = re.sub(r"[^\w가-힣]", "", ev.get("title", ""))[:12]
    return f"{ev.get('start', '')}|{title}"


# ── Claude 추출 ───────────────────────────────────────────
# 일정 추출과 정크 판정을 '한 번의 호출' 로 함께 한다. 어차피 같은 메일을 읽는
# 일이라 프롬프트만 늘리면 되고, 호출을 나누면 입력이 통째로 두 번 실려 간다.
_PROMPT = """다음은 오늘 받은 메일이다. 두 가지를 판단해라.

[1] 업무 일정으로 캘린더에 등록할 것 (events)
- 날짜와 시각이 메일에 명시된 것만. 추측하지 마라. 애매하면 넣지 마라.
- 지난 일정은 넣지 마라. 오늘은 {today} 이다.
- 광고·뉴스레터·자동알림·영수증은 제외.
- kind 는 사람이 모여 이야기하는 자리면 "미팅", 그 외 업무 일이면 "업무".
- title 은 소리 내어 읽을 것이므로 짧고 명확하게. 회사명이나 상대를 넣어라.

[2] 휴지통으로 보낼 것 (junk)
스팸·광고·정크, 그리고 읽을 필요가 없는 메일을 고른다.
- 광고성 홍보 메일, 마케팅 뉴스레터, 프로모션·쿠폰·할인 안내
- 스팸, 피싱으로 보이는 것, 알 수 없는 발신자의 대량 발송
- 이미 지난 웨비나·행사 홍보, 구독 매체의 정기 소식

**확신이 없으면 넣지 마라. 남겨두는 쪽이 항상 낫다.** 특히 아래는 절대 넣지 마라:
- 사람이 직접 쓴 메일 (문의·회신·협의)
- 거래·계약·세금계산서·입금·견적·청구·급여·법률·관공서
- 채용 지원, 고객사·파트너사에서 온 것
- 인증번호·보안 알림·비밀번호 재설정
- 위 [1] 에서 일정으로 뽑은 메일

why 는 왜 버리는지 다섯 글자 안팎으로.

JSON 만 출력해라. 설명·마크다운·코드펜스 금지.
{{"events":[{{"title":"...","kind":"미팅","start":"2026-08-12T14:00","hours":1.0}}],
 "junk":[{{"id":"12345","why":"광고"}}]}}
해당 없으면 {{"events":[],"junk":[]}}

메일:
{mails}
"""


def _extract(msgs: list[dict]) -> tuple[list[dict], list[dict]]:
    """(일정, 버릴 메일) 을 한 번의 호출로 뽑는다."""
    import bridge

    blob = "\n\n".join(
        f"[아이디] {m.get('id', '')}\n[보낸이] {m['from']}\n[제목] {m['subject']}\n"
        f"[받은시각] {m['date']}\n[본문] {m['body'][:800]}"
        for m in msgs
    )
    prompt = _PROMPT.format(today=datetime.now().strftime("%Y-%m-%d (%a)"), mails=blob)
    out = bridge.ask_once(prompt, model=config.MODEL_CHAT)
    if not out:
        return [], []
    # 코드펜스를 씌워 보내는 경우가 있어 한 번 벗겨준다
    out = re.sub(r"^```(?:json)?|```$", "", out.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return [], []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return [], []
    return (data.get("events") or []), (data.get("junk") or [])


# ── 정크 처리 ─────────────────────────────────────────────
MAX_TRASH = 15          # 하루에 이보다 많이 버리는 건 판단이 어긋난 것으로 본다

# 판단이 틀려도 이 낱말이 있으면 남긴다. 모델 앞에 두는 마지막 안전망이다.
_KEEP = re.compile(
    r"(계약|세금계산서|계산서|입금|송금|청구|견적|발주|납품|급여|정산|"
    r"소송|법무|국세청|홈택스|관공서|지원사업|과태료|인증번호|보안|"
    r"비밀번호|OTP|로그인)", re.I)


def _trashable(junk: list[dict], msgs: list[dict]) -> tuple[list[str], list[dict]]:
    """모델이 고른 것 중 실제로 버릴 것만 남긴다."""
    by_id = {str(m.get("id", "")): m for m in msgs}
    ids, kept = [], []
    for j in junk:
        mid = str(j.get("id", "")).strip()
        msg = by_id.get(mid)
        if not msg:
            continue                      # 우리가 보낸 목록에 없는 id 는 무시
        if _KEEP.search(f"{msg.get('subject', '')} {msg.get('from', '')}"):
            continue                      # 보호 낱말이 걸리면 남긴다
        ids.append(mid)
        kept.append({"id": mid, "subject": msg.get("subject", ""),
                     "from": msg.get("from", ""), "why": j.get("why", "")})
        if len(ids) >= MAX_TRASH:
            break
    return ids, kept


def _log_trash(rows: list[dict]) -> None:
    """무엇을 버렸는지 남긴다 — 되찾으려면 무엇이 사라졌는지부터 알아야 한다."""
    path = config.STATE / "mail_trash.jsonl"
    stamp = datetime.now().isoformat(timespec="seconds")
    try:
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({"at": stamp, **r}, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ── 등록 ──────────────────────────────────────────────────
def _parse_start(s: str):
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except (ValueError, TypeError):
            continue
    return None


def run(dry_run: bool = False, hours: int = LOOKBACK_HOURS) -> str:
    """한 번 돌리고 사람이 읽을 요약을 반환."""
    import mail_local

    msgs = mail_local.recent_messages(hours=hours, max_scan=MAX_SCAN)
    if msgs is None:
        return "메일을 읽지 못했습니다. 메일 앱 접근 권한을 확인해 주세요."

    st = _load_state()
    seen = set(st["seen"])
    fresh = [m for m in msgs if m.get("id") and m["id"] not in seen]
    if not fresh:
        return "새로 온 메일이 없습니다."

    # 일정 후보만 세어 둔다 (안내 문구용). 정크 판정은 온 메일 전부를 봐야
    # 하므로, 예전처럼 여기서 걸러 호출을 건너뛰지는 않는다.
    cands = [m for m in fresh if _looks_scheduley(m)]
    for m in fresh:
        st["seen"].append(m["id"])

    events, junk = _extract(fresh)

    # ── 정크 버리기 ──
    trashed = 0
    ids, rows = _trashable(junk, fresh)
    if ids and not dry_run:
        import mail_local as _ml

        trashed = _ml.trash(ids)
        if trashed:
            _log_trash(rows[:trashed])
    elif ids and dry_run:
        trashed = len(ids)

    junk_note = f" 광고·정크 {trashed}통은 휴지통으로 옮겼습니다." if trashed else ""

    if not CALENDAR_ENABLED or not events:
        _save_state_unless_dry(st, dry_run)
        if not cands:
            return f"메일 {len(fresh)}통을 봤고 일정으로 볼 만한 건 없었습니다.{junk_note}"
        return (f"메일 {len(fresh)}통 중 {len(cands)}통을 살펴봤지만 "
                f"등록할 일정은 없었습니다.{junk_note}")

    done = set(st["registered"])
    added, skipped = [], 0
    now = datetime.now()
    for ev in events[:MAX_EVENTS]:
        start = _parse_start(ev.get("start", ""))
        title = (ev.get("title") or "").strip()
        if not start or not title:
            skipped += 1
            continue
        if start < now - timedelta(minutes=5):
            skipped += 1          # 지난 일정은 넣지 않는다
            continue
        key = _event_key({"start": start.isoformat(timespec="minutes"), "title": title})
        if key in done:
            skipped += 1
            continue

        kind = "미팅" if "미팅" in (ev.get("kind") or "") else "업무"
        label = f"[{kind}] {title}"
        if dry_run:
            added.append((label, start))
            continue

        import calendar_local

        try:
            hours = float(ev.get("hours") or 1.0)
        except (TypeError, ValueError):
            hours = 1.0
        if calendar_local.create(label, start, hours=max(0.25, min(hours, 8.0))):
            st["registered"].append(key)
            added.append((label, start))
        else:
            skipped += 1

    _save_state_unless_dry(st, dry_run)

    if not added:
        return f"메일 {len(cands)}통을 살펴봤지만 등록한 일정은 없습니다.{junk_note}"

    import speak

    lines = []
    for label, start in added[:3]:
        yo = "월화수목금토일"[start.weekday()]
        ampm = "오전" if start.hour < 12 else "오후"
        h12 = start.hour if 1 <= start.hour <= 12 else (start.hour - 12 or 12)
        lines.append(f"{start.month}월 {start.day}일 {yo}요일 {ampm} "
                      f"{speak.clock_words(h12, 0)} {label}")
    tail = f" 그 외 {len(added) - 3}건 더 등록했습니다." if len(added) > 3 else ""
    return (f"메일에서 일정 {len(added)}건을 등록했습니다. "
            + ", ".join(lines) + "." + tail + junk_note)


def _notify(text: str) -> None:
    """데몬에게 대신 말하게 한다.

    이 스크립트는 별도 프로세스라, 여기서 직접 소리를 내면 데몬과
    출력 장치를 두고 다툰다. 소리를 내는 곳은 데몬 한 곳이어야 한다.
    """
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
        pass          # 데몬이 안 떠 있어도 등록 자체는 끝났다


def main() -> int:
    dry = "--dry-run" in sys.argv
    quiet = "--quiet" in sys.argv
    hours = LOOKBACK_HOURS
    for i, a in enumerate(sys.argv):
        if a == "--hours" and i + 1 < len(sys.argv):
            hours = int(sys.argv[i + 1])
    summary = run(dry_run=dry, hours=hours)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[메일정리 {stamp}] {summary}")
    if not dry and not quiet:
        _notify(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
