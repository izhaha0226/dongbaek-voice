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
import re
import subprocess
from pathlib import Path

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


# 날짜를 정렬 가능한 꼴로 뽑는 AppleScript 앞머리.
#
# ⚠ `date received of m as string` 을 쓰면 안 된다. 이 맥에서는
#   "2026년 8월 16일 토요일 오후 1:47:50" 로 나온다 — 사람은 읽지만
#   정렬은 못 한다. 여러 계정을 한 줄로 세우려면 정렬이 필요하다.
STAMP_PRELUDE = '''
on pad2(n)
  set s to ((n as integer) as string)
  if (length of s) < 2 then set s to "0" & s
  return s
end pad2

on stamp(d)
  return (year of d as string) & my pad2((month of d) as integer) & my pad2(day of d) ¬
    & my pad2(hours of d) & my pad2(minutes of d) & my pad2(seconds of d)
end stamp
'''


def _rows(raw: str, limit: int) -> list[str]:
    """stamp\\t보낸사람\\t제목 줄들을 최신순으로 세워 사람이 읽는 꼴로 돌려준다."""
    rows = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[0][:4].isdigit():
            rows.append(parts)
    rows.sort(key=lambda r: r[0], reverse=True)
    out = []
    for i, (ts, sender, subject) in enumerate(rows[:limit], 1):
        when = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}"
        out.append(f"{i}. {when} | {sender} | {subject}")
    return out


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


@server.tool(description=(
    "전 계정을 통틀어 최근 받은 메일 목록을 반환한다. limit 은 최대 20. "
    "⚠ 이건 '메일 뭐 왔어?' 처럼 대상을 지목하지 않을 때만 쓴다. "
    "특정 사람·회사·주제를 말씀하시면(예: '한빛건설 강남에서 온 메일') "
    "반드시 mail_search 를 쓸 것 — 최근 목록에는 그 메일이 없을 수 있다."))
def mail_recent(limit: int = 5) -> str:
    """전 계정의 받은함을 한 줄로 세워 최신순으로 돌려준다.

    ⚠ 2026-08-17 사고. 사장님이 "한빛건설 강남에서 온 메일 확인해" 하셨는데
      동백이 이 도구로 최근 3건을 읽고 엉뚱한 메일 셋을 보고했다. 그중
      한빛건설은 없었다. 원인이 둘이었다.

      첫째, 이 도구가 계정마다 limit 개를 담고 마지막에 앞에서 limit 개를
      잘랐다. 계정이 다섯이라 **언제나 첫 계정(dongbaek0803) 것만** 나왔다.
      한빛건설 메일은 yourname00 으로 왔으니 구조적으로 보일 수가 없었다.
      한 계정만 있으면 멀쩡히 도는 코드라 개발 중에는 절대 안 걸린다.

      둘째, 오류를 안 냈다. 형식 맞는 메일 세 건을 돌려주니 부른 쪽은
      틀린 줄 모른다. 조용히 틀리는 것이 시끄럽게 실패하는 것보다 나쁘다.

      지금은 전 계정에서 모아 날짜로 세운다. 대상을 지목하신 경우는
      애초에 이 도구가 아니라 mail_search 로 가야 한다 — 설명에 박아 뒀다.
    """
    limit = max(1, min(int(limit), 20))
    # 계정마다 넉넉히 퍼 온 뒤 파이썬에서 날짜로 세운다. 어느 계정에
    # 최신 메일이 몰려 있어도 놓치지 않도록 계정당 limit 만큼 받는다.
    raw = osa(f'''{STAMP_PRELUDE}
tell application "Mail"
  set out to ""
  repeat with a in every account
    if enabled of a then
      try
        set mb to mailbox "INBOX" of a
        set c to count of messages of mb
        repeat with i from 1 to (({limit}) as integer)
          if i > c then exit repeat
          set m to message i of mb
          set out to out & (my stamp(date received of m)) & tab & (sender of m) & tab & (subject of m) & linefeed
        end repeat
      end try
    end if
  end repeat
  return out
end tell''')
    if raw.startswith("ERROR"):
        return raw
    rows = _rows(raw, limit)
    return "\n".join(rows) if rows else "받은 메일이 없습니다"


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


@server.tool(description=(
    "제목과 보낸사람에서 키워드로 메일을 찾는다. 사람·회사·주제를 지목한 "
    "요청은 전부 이 도구로 답한다. 띄어쓰기는 알아서 무시한다. "
    "deep 을 켜면 본문까지 뒤지지만 느리다. 받은함만 본다 — 보낸 메일은 "
    "mail_sent 를 쓸 것. limit 은 최대 15."))
def mail_search(keyword: str, limit: int = 5, deep: bool = False) -> str:
    """제목·보낸사람(옵션으로 본문)에서 찾는다.

    ⚠ 설명과 코드가 어긋나 있었다 (2026-08-17 발견). 설명은
      "제목·보낸사람·본문에서 검색한다" 였는데 코드는 `subject contains`
      하나뿐이었다. 부르는 쪽은 설명을 믿고 보낸사람으로 찾으려 했을 테니,
      안 나오면 도구가 아니라 메일이 없는 줄 안다.

    ⚠ 띄어쓰기가 사람과 제목에서 다르다. 사장님은 "한빛건설 강남" 라고
      말씀하시는데 제목은 "한빛건설강남_광고 운영 정책" 이다. 공백 하나
      때문에 못 찾으면 도구가 없는 것과 같다. 그래서 공백을 뗀 꼴도 같이
      찾는다.

    본문 검색을 기본에서 뺀 이유: 받은함 전체 본문을 훑으면 osascript 가
    60초를 넘겨 죽는다. 제목·보낸사람으로 먼저 찾고, 정말 없을 때만 deep.
    """
    limit = max(1, min(int(limit), 15))
    kw = (keyword or "").strip()
    if not kw:
        return "검색어가 비었습니다"

    # 공백을 뗀 꼴도 같이 본다 ("한빛건설 강남" → "한빛건설강남").
    variants = [kw]
    if " " in kw:
        variants.append(kw.replace(" ", ""))

    fields = ["subject", "sender"] + (["content"] if deep else [])
    cond = " or ".join(f"{f} contains {q(v)}" for v in variants for f in fields)

    raw = osa(f'''{STAMP_PRELUDE}
tell application "Mail"
  set out to ""
  repeat with a in every account
    if enabled of a then
      try
        set found to (messages of (mailbox "INBOX" of a) whose {cond})
        repeat with m in found
          set out to out & (my stamp(date received of m)) & tab & (sender of m) & tab & (subject of m) & linefeed
        end repeat
      end try
    end if
  end repeat
  return out
end tell''')
    if raw.startswith("ERROR"):
        return raw

    # 같은 메일이 여러 계정·여러 변형에서 겹쳐 나온다. 줄 단위로 걷어낸다.
    seen, uniq = set(), []
    for line in raw.splitlines():
        if line and line not in seen:
            seen.add(line)
            uniq.append(line)
    rows = _rows("\n".join(uniq), limit)
    if rows:
        return "\n".join(rows)
    if not deep:
        return (f"제목·보낸사람에서 '{kw}' 를 찾지 못했습니다. "
                f"본문까지 뒤지려면 deep 을 켜고 다시 부르십시오.")
    return f"검색 결과 없음: {kw}"


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
# 첨부 — 2026-08-17 추가.
#
# 사장님이 "메일 확인하고 파일 다 다운받고 내용 정리해서 보고해" 하셨는데
# 첨부를 만지는 도구가 아예 없었다. 동백은 그걸 "못 한다" 고 말하지도
# 못했다 — 없는 기능은 없는 줄도 모른다.
#
# 내려받기는 파일을 만드는 일이지만 정해진 한 곳에만 쓴다. 경로를 인자로
# 받지 않는 이유가 그것이다. 부르는 쪽이 경로를 정할 수 있으면 덮어쓰기
# 사고가 나고, 그러면 승인 게이트를 태워야 한다. 자리를 고정해 두면
# 평상시 읽기 도구처럼 안전하게 쓸 수 있다.
# ─────────────────────────────────────────────────────────
DOWNLOAD_ROOT = Path.home() / "Downloads" / "동백메일"

# 파일 이름에서 경로가 될 만한 것을 전부 걷어낸다.
# 첨부 이름은 보낸 사람이 정한다 — 즉 우리가 통제하지 못하는 값이다.
_BAD_NAME = re.compile(r"[/\\:\x00-\x1f]")


def _safe_name(name: str, fallback: str) -> str:
    name = _BAD_NAME.sub("_", (name or "").strip()).lstrip(".")
    return name[:120] or fallback


@server.tool(description="제목으로 메일을 찾아 첨부파일 목록(이름·크기)을 반환한다. 내려받기 전에 무엇이 붙어 있는지 확인하는 용도.")
def mail_attachments(subject_contains: str) -> str:
    raw = osa(f'''
tell application "Mail"
  repeat with a in every account
    if enabled of a then
      try
        set found to (messages of (mailbox "INBOX" of a) whose subject contains {q(subject_contains)})
        if (count of found) > 0 then
          set m to item 1 of found
          set out to "제목: " & (subject of m) & linefeed & "보낸사람: " & (sender of m) & linefeed
          set n to 0
          repeat with t in (every mail attachment of m)
            set n to n + 1
            set sz to "?"
            try
              set sz to ((file size of t) as string)
            end try
            set out to out & "  " & (n as string) & ". " & (name of t) & "  (" & sz & " bytes)" & linefeed
          end repeat
          if n = 0 then return out & "  첨부 없음"
          return out & "첨부 " & (n as string) & "개"
        end if
      end try
    end if
  end repeat
  return "해당 제목의 메일을 찾지 못했습니다"
end tell''')
    return raw


@server.tool(description=(
    "제목으로 메일을 찾아 첨부파일 전부를 ~/Downloads/동백메일/<제목>/ 에 내려받는다. "
    "저장한 파일의 전체 경로를 돌려준다. 저장할 뿐 열거나 실행하지 않는다."))
def mail_download(subject_contains: str) -> str:
    """첨부를 정해진 자리에 내려받는다.

    ⚠ 폴더 이름을 파이썬에서 먼저 만들고 AppleScript 에는 완성된 경로만
      넘긴다. 제목에 슬래시나 따옴표가 들어와도 엉뚱한 곳에 쓰지 않게
      하려는 것이다. 첨부 이름도 마찬가지로 보낸 사람이 정한 값이라
      그대로 믿지 않는다.
    """
    subj = osa(f'''
tell application "Mail"
  repeat with a in every account
    if enabled of a then
      try
        set found to (messages of (mailbox "INBOX" of a) whose subject contains {q(subject_contains)})
        if (count of found) > 0 then return subject of (item 1 of found)
      end try
    end if
  end repeat
  return "NOTFOUND"
end tell''')
    if subj.startswith("ERROR"):
        return subj
    if subj == "NOTFOUND":
        return "해당 제목의 메일을 찾지 못했습니다"

    folder = DOWNLOAD_ROOT / _safe_name(subj, "제목없음")
    folder.mkdir(parents=True, exist_ok=True)

    # 이름 정리는 파이썬이 하고, AppleScript 는 받은 경로에 쓰기만 한다.
    names = osa(f'''
tell application "Mail"
  repeat with a in every account
    if enabled of a then
      try
        set found to (messages of (mailbox "INBOX" of a) whose subject contains {q(subject_contains)})
        if (count of found) > 0 then
          set out to ""
          repeat with t in (every mail attachment of (item 1 of found))
            set out to out & (name of t) & linefeed
          end repeat
          return out
        end if
      end try
    end if
  end repeat
  return ""
end tell''')
    if names.startswith("ERROR"):
        return names

    wanted = [n for n in names.splitlines() if n.strip()]
    if not wanted:
        return f"'{subj}' 에는 첨부가 없습니다"

    saved, failed = [], []
    for i, raw_name in enumerate(wanted, 1):
        target = folder / _safe_name(raw_name, f"첨부{i}")
        r = osa(f'''
tell application "Mail"
  repeat with a in every account
    if enabled of a then
      try
        set found to (messages of (mailbox "INBOX" of a) whose subject contains {q(subject_contains)})
        if (count of found) > 0 then
          repeat with t in (every mail attachment of (item 1 of found))
            if (name of t) is {q(raw_name)} then
              save t in (POSIX file {q(str(target))})
              return "OK"
            end if
          end repeat
        end if
      end try
    end if
  end repeat
  return "MISS"
end tell''')
        if r == "OK" and target.exists():
            saved.append(f"  {target.name}  ({target.stat().st_size:,} bytes)")
        else:
            failed.append(raw_name)

    out = [f"'{subj}' 첨부 {len(saved)}/{len(wanted)}개를 내려받았습니다.",
           f"위치: {folder}"]
    out += saved
    if failed:
        out.append("실패(아직 서버에서 안 내려온 첨부일 수 있습니다): " + ", ".join(failed))
    return "\n".join(out)


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
