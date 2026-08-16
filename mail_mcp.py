#!/usr/bin/env python3
"""동백 메일 도구 — macOS Mail.app 을 AppleScript 로 조작하는 MCP 서버.

왜 MCP 인가:
  동백은 평상시 Bash 가 차단이라 스크립트를 직접 못 부른다.
  MCP 로 노출하면 캘린더·Gmail 과 똑같이 '도구 단위'로 권한을 나눌 수 있다.
    - 읽기 도구 → .claude/settings.json 의 allow 에 등록 → 평상시 사용
    - 쓰기 도구 → allow 에도 deny 에도 없음 → 음성 '진행' 승인 후에만

왜 Mail.app 인가:
  claude.ai Gmail 커넥터에는 발송·삭제 도구가 아예 없다.
  Mail.app 은 네이버웍스를 포함한 계정 전부를 이미 들고 있고,
  AppleScript 로 발송·휴지통 이동이 된다.

왜 .venv-mcp 로 도는가 (⚠ .venv 로 실행하면 ImportError):
  이 파일은 mcp 2.x 전용 API(mcp.server.mcpserver)를 쓰는데, 본 venv 의
  claude-agent-sdk 가 mcp<2.0.0 을 강제한다. 한 venv 에서 공존이 불가능해
  이 서버만 mcp 2.x 단독의 .venv-mcp 로 돈다 — mcp-dongbaek.json 참조.
"""

import json
import subprocess

from mcp.server.mcpserver import MCPServer

server = MCPServer(
    name="dongbaek-mail",
    instructions="macOS Mail.app 기반 메일 조회·발송 도구. 계정에는 네이버웍스와 지메일이 함께 들어있다.",
)

TIMEOUT = 60


def osa(script: str) -> str:
    """AppleScript 실행. 실패하면 사람이 읽을 수 있는 오류를 돌려준다."""
    try:
        p = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return "ERROR: Mail 응답이 없습니다 (동기화 중일 수 있습니다)"
    if p.returncode != 0:
        return f"ERROR: {(p.stderr or '').strip()[:300]}"
    return (p.stdout or "").strip()


def q(s: str) -> str:
    """AppleScript 문자열 리터럴로 안전하게 감싼다."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


# ─────────────────────────────────────────────────────────
# 읽기 도구 — allow 에 등록해 평상시 사용
# ─────────────────────────────────────────────────────────
@server.tool(description="Mail.app 에 설정된 메일 계정 목록과 각 계정의 안 읽은 메일 수를 반환한다.")
def mail_accounts() -> str:
    return osa('''
tell application "Mail"
  set out to ""
  repeat with a in every account
    if enabled of a then
      set n to 0
      try
        set n to unread count of (mailbox "INBOX" of a)
      end try
      set out to out & (name of a) & " | 안읽음 " & (n as string) & linefeed
    end if
  end repeat
  return out
end tell''')


@server.tool(description="전체 계정의 안 읽은 메일 총 개수를 반환한다. 가장 가벼운 조회.")
def mail_unread_count() -> str:
    return osa('''
tell application "Mail"
  set t to 0
  repeat with a in every account
    if enabled of a then
      try
        set t to t + (unread count of (mailbox "INBOX" of a))
      end try
    end if
  end repeat
  return "안 읽은 메일 " & (t as string) & "통"
end tell''')


@server.tool(description="최근 받은 메일 목록을 반환한다. limit 은 최대 20. 각 줄은 번호·날짜·보낸사람·제목.")
def mail_recent(limit: int = 5) -> str:
    limit = max(1, min(int(limit), 20))
    return osa(f'''
tell application "Mail"
  set out to ""
  set msgs to {{}}
  repeat with a in every account
    if enabled of a then
      try
        set mb to mailbox "INBOX" of a
        set c to count of messages of mb
        if c > 0 then
          repeat with i from 1 to (({limit}) as integer)
            if i > c then exit repeat
            set m to message i of mb
            set end of msgs to ((date received of m) as string) & " | " & (sender of m) & " | " & (subject of m)
          end repeat
        end if
      end try
    end if
  end repeat
  set k to 0
  repeat with s in msgs
    set k to k + 1
    if k > {limit} then exit repeat
    set out to out & (k as string) & ". " & s & linefeed
  end repeat
  if out is "" then return "받은 메일이 없습니다"
  return out
end tell''')


@server.tool(description="보낸 메일 목록을 반환한다. '메일 제대로 갔는지 확인해줘' 는 이 도구로 답한다. limit 은 최대 20. 각 줄은 번호·보낸시각·받는사람·제목.")
def mail_sent(limit: int = 5) -> str:
    """보낸 편지함을 읽는다. "메일 제대로 갔는지 확인해줘" 가 이 도구다.

    ⚠ 이게 없어서 "보낸 메일함을 넘겨보는 도구가 없다" 고 답한 적이 있다
      (2026-08-14). 지어낸 말이 아니라 사실이었다 — mail_recent 도
      mail_search 도 mailbox "INBOX" 를 하드코딩해서 받은함만 본다.
      mail_local.sent_recent() 는 파이썬 쪽에만 있어 클로드가 부를 수 없었다.
      사장님은 Mail.app 으로 보내셨는데 확인할 방법이 없던 것이다.

    ⚠ 계정별 `sent mailbox of a` 는 쓰면 안 된다. 이 맥의 Mail 에서는
      다섯 계정 전부 "가져올 수 없습니다" 로 죽고, try 가 그걸 삼켜서
      '보낸 메일이 없습니다' 라는 거짓 답이 나온다. 계정 아래의 실제 이름도
      '보낸편지함' 과 'Sent Messages' 로 갈려 이름으로 찾기도 위태롭다.
      전역 `sent mailbox` 하나가 다섯 계정을 모두 들고 있다 (실측 2,716건).
    """
    limit = max(1, min(int(limit), 20))
    return osa(f'''
tell application "Mail"
  set out to ""
  set k to 0
  set mb to sent mailbox
  set c to count of messages of mb
  repeat with i from 1 to c
    if k ≥ {limit} then exit repeat
    set m to message i of mb
    set k to k + 1
    set rcpt to ""
    try
      set rcpt to address of to recipient 1 of m
    end try
    set out to out & (k as string) & ". " & ((date sent of m) as string) & " | " & rcpt & " | " & (subject of m) & linefeed
  end repeat
  if out is "" then return "보낸 메일이 없습니다"
  return out
end tell''')


@server.tool(description="제목·보낸사람·본문에서 키워드로 메일을 검색한다. 받은함만 본다 — 보낸 메일은 mail_sent 를 쓸 것. limit 은 최대 15.")
def mail_search(keyword: str, limit: int = 5) -> str:
    limit = max(1, min(int(limit), 15))
    return osa(f'''
tell application "Mail"
  set out to ""
  set k to 0
  repeat with a in every account
    if enabled of a then
      try
        set mb to mailbox "INBOX" of a
        set found to (messages of mb whose subject contains {q(keyword)})
        repeat with m in found
          set k to k + 1
          if k > {limit} then exit repeat
          set out to out & (k as string) & ". " & ((date received of m) as string) & " | " & (sender of m) & " | " & (subject of m) & linefeed
        end repeat
      end try
    end if
    if k > {limit} then exit repeat
  end repeat
  if out is "" then return "검색 결과 없음: " & {q(keyword)}
  return out
end tell''')


@server.tool(description="제목으로 메일 하나를 찾아 본문을 반환한다. 본문은 2000자까지.")
def mail_read(subject_contains: str) -> str:
    return osa(f'''
tell application "Mail"
  repeat with a in every account
    if enabled of a then
      try
        set found to (messages of (mailbox "INBOX" of a) whose subject contains {q(subject_contains)})
        if (count of found) > 0 then
          set m to item 1 of found
          set b to content of m
          if (length of b) > 2000 then set b to (text 1 thru 2000 of b) & "…(이하 생략)"
          return "보낸사람: " & (sender of m) & linefeed & "날짜: " & ((date received of m) as string) & linefeed & "제목: " & (subject of m) & linefeed & linefeed & b
        end if
      end try
    end if
  end repeat
  return "해당 제목의 메일을 찾지 못했습니다"
end tell''')


# ─────────────────────────────────────────────────────────
# 쓰기 도구 — allow 에 넣지 않는다.
#   평상시에는 거부되고, 음성 '진행' 승인을 통과한
#   elevated 호출에서만 실행된다.
# ─────────────────────────────────────────────────────────
# 발신 계정을 반드시 지정해야 한다.
# 비워두면 Mail 이 임의의 계정을 고르는데, 발신 서버가 지정되지 않은
# 계정이 걸리면 "발신 메일 서버를 선택하십시오" 대화창이 뜨고 발송이 멈춘다.
# 이 주소는 발신 서버(smtp.worksmobile.com)가 물려 있는 것으로 확인된 계정이다.
DEFAULT_SENDER = "yourname@dongbaek.ai"


# 발송 성공을 프로그램으로 판정하는 방법이 없다. 세 가지를 시도해 전부 실패했다:
#   1. send 가 오류 없이 반환  → 실패해도 오류를 안 낸다. 거짓 성공.
#   2. 보낸편지함(Sent) 적재    → 네이버웍스는 로컬 Sent 에 안 남긴다. 거짓 실패.
#   3. 미발송 대기열에서 제거   → 전달된 메일도 잔재로 남는다. 신호가 안 된다.
# 그래서 성공을 단정하지 않고 '넘겼다'까지만 보고한다.
# 대신 보내기 전에 확실히 실패할 조건(발신 서버 없음)은 미리 걸러낸다.
@server.tool(description="메일을 발송한다. 되돌릴 수 없다. sender 는 보낼 계정 주소(생략 시 기본 업무 계정). 전달 성공 여부는 확인되지 않는다.")
def mail_send(to: str, subject: str, body: str, sender: str = "") -> str:
    from_addr = sender or DEFAULT_SENDER

    # 발신 계정에 발신 서버가 물려 있는지 먼저 확인한다.
    # 없으면 send 는 조용히 멈추고 사용자 화면에만 대화창이 뜬다.
    # 발신 서버 확인 + '표시이름 <주소>' 형태의 정식 발신자 문자열을 받아온다.
    # 맨 주소만 넣으면 Mail 이 계정 신원과 매칭하지 못해 발신 서버를 못 찾고
    # 조용히 대기 상태로 멈춘다 (화면에만 대화창이 뜬다).
    check = osa(f'''
tell application "Mail"
  repeat with a in every account
    if enabled of a then
      if (email addresses of a as string) contains {q(from_addr)} then
        try
          set srv to name of (smtp server of a)
        on error
          return "NOSMTP:" & (name of a)
        end try
        return "OK:" & (full name of a) & " <" & {q(from_addr)} & ">"
      end if
    end if
  end repeat
  return "NOACCOUNT"
end tell''')
    if check.startswith("ERROR"):
        return check
    if check == "NOACCOUNT":
        return f"ERROR: {from_addr} 계정을 Mail.app 에서 찾지 못했습니다"
    if check.startswith("NOSMTP"):
        return (f"ERROR: '{check.split(':',1)[1]}' 계정에 발신 서버가 지정되어 있지 않습니다. "
                f"Mail → 설정 → 계정 → 서버 설정에서 발신 서버를 지정해야 발송됩니다.")
    sender_string = check.split(":", 1)[1]

    sent = osa(f'''
tell application "Mail"
  set m to make new outgoing message with properties {{subject:{q(subject)}, content:{q(body)}, visible:false}}
  tell m
    make new to recipient at end of to recipients with properties {{address:{q(to)}}}
  end tell
  set sender of m to {q(sender_string)}
  send m
  return "HANDED_OFF"
end tell''')

    if sent.startswith("ERROR"):
        return sent
    return (f"Mail 에 발송을 넘겼습니다: {to} / {subject} / 보낸계정 {from_addr}. "
            f"실제 전달 여부는 여기서 확인할 수 없습니다. "
            f"실패했다면 Mail 화면에 오류 대화창이 뜹니다.")


@server.tool(description="제목으로 메일을 찾아 휴지통으로 옮긴다. IMAP 계정이면 서버에도 반영된다.")
def mail_trash(subject_contains: str) -> str:
    return osa(f'''
tell application "Mail"
  repeat with a in every account
    if enabled of a then
      try
        set found to (messages of (mailbox "INBOX" of a) whose subject contains {q(subject_contains)})
        if (count of found) > 0 then
          set m to item 1 of found
          set s to subject of m
          delete m
          return "휴지통으로 이동: " & s
        end if
      end try
    end if
  end repeat
  return "해당 제목의 메일을 찾지 못했습니다"
end tell''')


if __name__ == "__main__":
    server.run("stdio")
