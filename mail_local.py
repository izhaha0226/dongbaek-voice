"""Mail.app 조회 — AppleScript. 토큰 0, 약 0.4초.

mail_mcp.py 와 기능이 겹치지만 역할이 다르다.
  - 여기(mail_local): 정형화된 질문을 라우터가 가로채 0 토큰으로 답한다.
  - mail_mcp:        Claude 가 판단이 필요할 때 쓰는 도구 (검색어 조합, 본문 요약 등)

캘린더와 달리 EventKit 같은 데이터 계층이 없어서 AppleScript 를 쓴다.
조회는 0.4초로 충분히 빠르다.
"""

import subprocess

TIMEOUT = 20
# 본문까지 긁는 조회는 훨씬 오래 걸린다 (40통에 20초 가까이).
RECENT_TIMEOUT = 180


def _osa(script: str, timeout: int = TIMEOUT) -> str | None:
    try:
        p = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    return (p.stdout or "").strip()


def unread_count() -> str | None:
    out = _osa('''
tell application "Mail"
  set t to 0
  set detail to ""
  repeat with a in every account
    if enabled of a then
      try
        set n to unread count of (mailbox "INBOX" of a)
        if n > 0 then
          set t to t + n
          set detail to detail & (name of a) & ":" & (n as string) & "|"
        end if
      end try
    end if
  end repeat
  return (t as string) & "||" & detail
end tell''')
    if out is None:
        return None
    total_s, _, detail = out.partition("||")
    try:
        total = int(total_s)
    except ValueError:
        return None
    if total == 0:
        return "안 읽은 메일이 없습니다."

    parts = [p for p in detail.split("|") if p]
    if len(parts) == 1:
        acct, _, n = parts[0].partition(":")
        return f"안 읽은 메일 {total}통입니다. 전부 {acct} 계정입니다."
    top = sorted(parts, key=lambda x: -int(x.split(":")[1]))[:2]
    where = ", ".join(f"{p.split(':')[0]} {p.split(':')[1]}통" for p in top)
    return f"안 읽은 메일 {total}통입니다. {where} 순으로 많습니다."


def recent_messages(hours: int = 26, max_scan: int = 40) -> list[dict] | None:
    """최근 메일을 본문까지 가져온다. 실패하면 None.

    ⚠ `messages whose date received > ...` 를 쓰지 않는다. 캘린더에서 배운
      것과 같은 함정이다 — 메시지마다 AppleEvent 를 왕복해 받은편지함이
      크면 몇 분씩 걸린다. 대신 최신 것부터 훑다가 기준 시각보다 오래되면
      멈춘다. 훑는 개수에도 상한을 둔다.

    본문은 앞부분만 자른다. 일정 정보는 대개 위쪽에 있고, 통째로 넘기면
    Claude 입력이 커져 비용이 뛴다.

    ⚠ 타임아웃을 넉넉히 준다. 조회(0.4초)와 달리 본문까지 읽으면 40통에
      20초 가까이 걸려서, 기본값 20초에 걸려 "메일을 읽지 못했습니다" 로
      끝나던 적이 있다. 하루 한 번 도는 작업이라 느린 건 문제가 아니다.
    """
    out = _osa(timeout=RECENT_TIMEOUT, script=f'''
tell application "Mail"
  set cutoff to (current date) - ({int(hours)} * hours)
  set out to ""
  set k to 0
  repeat with a in every account
    if enabled of a then
      try
        set mb to mailbox "INBOX" of a
        set total to count of messages of mb
        repeat with i from 1 to total
          if k ≥ {int(max_scan)} then exit repeat
          set m to message i of mb
          if (date received of m) < cutoff then exit repeat
          set k to k + 1
          set bodyText to ""
          try
            set bodyText to content of m
            if (length of bodyText) > 1200 then
              set bodyText to text 1 thru 1200 of bodyText
            end if
          end try
          set out to out & "---MSG---" & linefeed
          set out to out & "ID:" & (id of m as string) & linefeed
          set out to out & "FROM:" & (sender of m) & linefeed
          set out to out & "SUBJ:" & (subject of m) & linefeed
          set out to out & "DATE:" & ((date received of m) as string) & linefeed
          set out to out & "BODY:" & bodyText & linefeed
        end repeat
      end try
    end if
    if k ≥ {int(max_scan)} then exit repeat
  end repeat
  return out
end tell''')
    if out is None:
        return None

    msgs = []
    for chunk in out.split("---MSG---"):
        if not chunk.strip():
            continue
        rec = {"id": "", "from": "", "subject": "", "date": "", "body": ""}
        lines = chunk.splitlines()
        for idx, line in enumerate(lines):
            for key, tag in (("id", "ID:"), ("from", "FROM:"),
                             ("subject", "SUBJ:"), ("date", "DATE:")):
                if line.startswith(tag) and not rec[key]:
                    rec[key] = line[len(tag):].strip()
            if line.startswith("BODY:"):
                rec["body"] = "\n".join([line[5:]] + lines[idx + 1:]).strip()
                break
        if rec["id"] or rec["subject"]:
            msgs.append(rec)
    return msgs


# 알림성 발신 — 사람이 아니라 서비스가 보낸 메일.
#
# ⚠ 네이버·카카오·지메일 같은 개인 메일 도메인은 절대 넣지 마라. 넣으면
#   사람이 보낸 메일이 '알림' 으로 묻힌다. 알림을 놓쳐 제목까지 읽는 쪽이,
#   사장님께 온 진짜 메일이 개수로만 세어지는 쪽보다 훨씬 낫다.
NOTICE_SERVICES = {
    "linkedin": "LinkedIn",
    "instagram": "Instagram",
    "facebook": "Facebook",
    "threads": "Threads",
    "tiktok": "TikTok",
    "youtube": "YouTube",
    "twitter": "X",
}
# 목록에 없는 서비스라도 '답장할 수 없는 주소' 는 사람이 아니다.
NOTICE_ADDRS = ("no-reply", "noreply", "no_reply", "donotreply", "do-not-reply",
                "notification", "notice@", "alert@")


def notice_service(sender: str = "", address: str = "") -> str | None:
    """알림성 발신이면 묶어 셀 서비스 이름, 사람이 보낸 것이면 None.

    이름과 주소를 둘 다 본다 — 표시 이름이 "LinkedIn" 으로 오는 발신도 있고,
    이름 없이 주소만 오는 발신도 있다.
    """
    hay = f"{sender} {address}".lower()
    for key, label in NOTICE_SERVICES.items():
        if key in hay:
            return label
    if any(k in address.lower() for k in NOTICE_ADDRS):
        name = (sender or "").split("<")[0].strip().strip('"')
        if name and "@" not in name:
            return name[:16]
        # 이름이 없으면 주소의 도메인으로 부른다. "accounts.google.com" 은
        # 맨 앞이 아니라 뒤에서 두 번째 조각이 서비스 이름이다.
        # 첫 글자를 올리는 건 멋이 아니라 중복 방지다 — 같은 구글이 표시
        # 이름으로도 오고 주소로도 와서 "Google, google" 둘로 세어졌다.
        parts = address.rpartition("@")[2].split(".")
        label = (parts[-2] if len(parts) >= 2 else parts[0])[:16]
        return (label[:1].upper() + label[1:]) if label else "알림"
    return None


def received_brief(hours: int = 6, max_scan: int = 30) -> list[dict] | None:
    """최근 받은 메일 — 제목·보낸이만. 본문을 안 읽어 recent_messages 보다
    훨씬 빠르다. 브리핑의 '주고받은 메일 정리' 용.

    주소(addr)도 함께 담는다 — 표시 이름만으로는 사람과 서비스를 못 가른다
    (notice_service 참고)."""
    out = _osa(timeout=RECENT_TIMEOUT, script=f'''
tell application "Mail"
  set cutoff to (current date) - ({int(hours)} * hours)
  set out to ""
  set k to 0
  repeat with a in every account
    if enabled of a then
      try
        set mb to mailbox "INBOX" of a
        set total to count of messages of mb
        repeat with i from 1 to total
          if k ≥ {int(max_scan)} then exit repeat
          set m to message i of mb
          if (date received of m) < cutoff then exit repeat
          set k to k + 1
          set addr to ""
          try
            set addr to extract address from sender of m
          end try
          set out to out & "---MSG---" & linefeed
          set out to out & "FROM:" & (extract name from sender of m) & linefeed
          set out to out & "ADDR:" & addr & linefeed
          set out to out & "SUBJ:" & (subject of m) & linefeed
        end repeat
      end try
    end if
    if k ≥ {int(max_scan)} then exit repeat
  end repeat
  return out
end tell''')
    if out is None:
        return None
    msgs = []
    for chunk in out.split("---MSG---"):
        rec = {"from": "", "addr": "", "subject": ""}
        for line in chunk.splitlines():
            if line.startswith("FROM:") and not rec["from"]:
                rec["from"] = line[5:].strip()
            elif line.startswith("ADDR:") and not rec["addr"]:
                rec["addr"] = line[5:].strip()
            elif line.startswith("SUBJ:") and not rec["subject"]:
                rec["subject"] = line[5:].strip()
        if rec["subject"]:
            msgs.append(rec)
    return msgs


def sent_recent(hours: int = 12, max_scan: int = 30) -> list[dict] | None:
    """최근 보낸 메일 — 제목·받는이. '메일 발송 내용 정리' 용.

    ⚠ 계정별 `sent mailbox of a` 로 읽고 있었는데, 이 맥의 Mail 에서는 다섯
      계정 전부 "가져올 수 없습니다" 로 죽는다. try 가 그 오류를 삼켜서
      이 함수는 **여태 늘 빈 목록을 돌려주고 있었다** — 실패한 줄도 모르고
      "보낸 메일이 없다" 로 읽혔다 (2026-08-14 발견).
      전역 `sent mailbox` 하나가 다섯 계정을 모두 들고 있다 (실측 2,716건).
    """
    out = _osa(timeout=RECENT_TIMEOUT, script=f'''
tell application "Mail"
  set cutoff to (current date) - ({int(hours)} * hours)
  set out to ""
  set k to 0
  set mb to sent mailbox
  set total to count of messages of mb
  repeat with i from 1 to total
    if k ≥ {int(max_scan)} then exit repeat
    set m to message i of mb
    if (date sent of m) < cutoff then exit repeat
    set k to k + 1
    set rcpt to ""
    try
      set rcpt to address of to recipient 1 of m
    end try
    set out to out & "---MSG---" & linefeed
    set out to out & "TO:" & rcpt & linefeed
    set out to out & "SUBJ:" & (subject of m) & linefeed
  end repeat
  return out
end tell''')
    if out is None:
        return None
    msgs = []
    for chunk in out.split("---MSG---"):
        rec = {"to": "", "subject": ""}
        for line in chunk.splitlines():
            if line.startswith("TO:") and not rec["to"]:
                rec["to"] = line[3:].strip()
            elif line.startswith("SUBJ:") and not rec["subject"]:
                rec["subject"] = line[5:].strip()
        if rec["subject"]:
            msgs.append(rec)
    return msgs


def trash(message_ids: list[str]) -> int:
    """지정한 메일을 휴지통으로 옮긴다. 옮긴 개수를 돌려준다.

    Mail.app 의 `delete` 는 영구 삭제가 아니라 휴지통 이동이다. 되돌릴 수
    있는 쪽만 쓴다 — 판단이 틀렸을 때 복구할 길을 남겨두어야 한다.

    id 로 계정을 특정할 수 없어 활성 계정의 받은편지함을 훑는다. 하루 한 번
    도는 작업이고 대상이 몇 통뿐이라 이 정도 비용은 문제가 되지 않는다.
    """
    wanted = [str(i).strip() for i in message_ids if str(i).strip()]
    if not wanted:
        return 0
    ids = ", ".join(f'"{i}"' for i in wanted)
    out = _osa(timeout=RECENT_TIMEOUT, script=f'''
set wanted to {{{ids}}}
set moved to 0
tell application "Mail"
  repeat with a in every account
    if enabled of a then
      try
        set mb to mailbox "INBOX" of a
        set total to count of messages of mb
        repeat with i from total to 1 by -1
          try
            set m to message i of mb
            if wanted contains ((id of m) as string) then
              delete m
              set moved to moved + 1
            end if
          end try
        end repeat
      end try
    end if
  end repeat
end tell
return moved as string''')
    try:
        return int((out or "0").strip())
    except ValueError:
        return 0


def recent(limit: int = 3) -> str | None:
    limit = max(1, min(int(limit), 5))
    out = _osa(f'''
tell application "Mail"
  set out to ""
  set k to 0
  repeat with a in every account
    if enabled of a then
      try
        set mb to mailbox "INBOX" of a
        if (count of messages of mb) > 0 then
          set m to message 1 of mb
          set k to k + 1
          set out to out & (sender of m) & " ▸ " & (subject of m) & "||"
        end if
      end try
    end if
    if k ≥ {limit} then exit repeat
  end repeat
  return out
end tell''')
    if out is None:
        return None
    items = [i for i in out.split("||") if i.strip()]
    if not items:
        return "받은 메일이 없습니다."

    def clean(s):
        # "이름 <메일주소>" 에서 이름만. 주소는 소리로 들어도 못 알아듣는다.
        name = s.split("<")[0].strip().strip('"')
        return name or s.split("<")[0]

    lines = []
    for i in items[:limit]:
        sender, _, subject = i.partition(" ▸ ")
        lines.append(f"{clean(sender)}님의 {subject.strip()}")
    return f"최근 메일 {len(lines)}건입니다. " + ", ".join(lines) + "."


def received_by_account(hours: int = 44, max_per_account: int = 25,
                        body_chars: int = 420) -> list[dict] | None:
    """계정별 받은 메일 — 보낸이·제목·본문 앞머리까지.

    '업체별로 분류해서 보고' 용이다 (사장님 지시 2026-08-14). received_brief
    는 본문을 안 읽어 빠르지만, 판단("참조로 온 것인가, 답을 요하는가")을
    하려면 본문이 있어야 한다.

    ⚠ 계정마다 mailbox "INBOX" 로 읽는다. 보낸함처럼 계정별 접근이 죽는
      곳이 아니다 — sent mailbox 와 헷갈리지 말 것 (그건 전역만 된다).
    ⚠ 본문은 앞머리만 자른다. 통째로 담으면 프롬프트가 터지고, 판단에
      필요한 것은 대개 첫 문단이다.
    """
    out = _osa(timeout=RECENT_TIMEOUT, script=f'''
tell application "Mail"
  set cutoff to (current date) - ({int(hours)} * hours)
  set out to ""
  repeat with a in every account
    if enabled of a then
      set an to name of a
      try
        set mb to mailbox "INBOX" of a
        set c to count of messages of mb
        set k to 0
        repeat with i from 1 to c
          if k ≥ {int(max_per_account)} then exit repeat
          set m to message i of mb
          if (date received of m) < cutoff then exit repeat
          set k to k + 1
          set out to out & "===MSG===" & linefeed
          set out to out & "ACCT:" & an & linefeed
          set out to out & "DATE:" & ((date received of m) as string) & linefeed
          set out to out & "FROM:" & (sender of m) & linefeed
          set out to out & "SUBJ:" & (subject of m) & linefeed
          try
            set b to content of m
            if (count of b) > {int(body_chars)} then set b to (text 1 thru {int(body_chars)} of b)
            set out to out & "BODY:" & b & linefeed
          end try
        end repeat
      end try
    end if
  end repeat
  return out
end tell''')
    if out is None:
        return None
    msgs = []
    for chunk in out.split("===MSG===")[1:]:
        rec = {"account": "", "date": "", "sender": "", "subject": "", "body": ""}
        body_lines, in_body = [], False
        for line in chunk.splitlines():
            if line.startswith("ACCT:") and not rec["account"]:
                rec["account"] = line[5:].strip(); in_body = False
            elif line.startswith("DATE:") and not rec["date"]:
                rec["date"] = line[5:].strip(); in_body = False
            elif line.startswith("FROM:") and not rec["sender"]:
                rec["sender"] = line[5:].strip(); in_body = False
            elif line.startswith("SUBJ:") and not rec["subject"]:
                rec["subject"] = line[5:].strip(); in_body = False
            elif line.startswith("BODY:"):
                body_lines.append(line[5:]); in_body = True
            elif in_body:
                body_lines.append(line)
        rec["body"] = " ".join(" ".join(body_lines).split())[:body_chars]
        if rec["subject"] or rec["sender"]:
            msgs.append(rec)
    return msgs
