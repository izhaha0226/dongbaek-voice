#!/usr/bin/env python3
"""메일 도구가 찾아야 할 것을 찾는가 — 2026-08-17 사고의 회귀 시험.

그날 아침 사장님이 "한빛건설 강남에서 온 메일 확인해가지고 파일 다 다운받고
내용 정리해서 보고해" 하셨다. 동백은 엉뚱한 메일 세 건(구글, 이영희,
KB국민카드)을 읽어 드렸다. 사장님이 "이게 맞아????" 하셔서 들통났다.

원인이 넷이었고 전부 조용한 실패였다 — 오류를 낸 것이 하나도 없다.

  1. mail_recent 가 계정마다 limit 개를 담고 마지막에 앞에서 limit 개를
     잘랐다. 계정이 다섯이라 언제나 첫 계정 것만 나왔다. 한빛건설 메일은
     둘째 계정으로 와서 구조적으로 보일 수가 없었다.
  2. mail_search 의 설명은 "제목·보낸사람·본문" 인데 코드는 제목뿐이었다.
  3. 사장님은 "한빛건설 강남", 제목은 "한빛건설강남". 공백 하나로 못 찾는다.
  4. 첨부를 만지는 도구가 아예 없었다.

여기서 지키는 것은 Mail.app 없이도 검증되는 부분 — 여러 계정을 어떻게
세우는가, 이름을 어떻게 지키는가, 어떤 조건으로 찾는가.

    python tests/test_mail_tools.py
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# mail_mcp 는 mcp 2.x 전용이라 본 venv 에 없다(.venv-mcp 로 돈다).
# 도구 본체가 아니라 순수 헬퍼를 보려는 것이므로 서버만 흉내 낸다.
if "mcp.server.mcpserver" not in sys.modules:
    class _Server:
        def __init__(self, **kw):
            pass

        def tool(self, **kw):
            return lambda f: f

        def run(self, *a):
            pass

    mod = types.ModuleType("mcp.server.mcpserver")
    mod.MCPServer = _Server
    pkg = types.ModuleType("mcp")
    srv = types.ModuleType("mcp.server")
    sys.modules.setdefault("mcp", pkg)
    sys.modules.setdefault("mcp.server", srv)
    sys.modules["mcp.server.mcpserver"] = mod

import mail_mcp as M

FAIL = []


def check(name, got, want=True):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))


def row(stamp, sender, subject):
    return f"{stamp}\t{sender}\t{subject}"


print("[1] 여러 계정을 날짜로 세운다 — 첫 계정이 목록을 독차지하지 않는다")
# 실제 사고 재현: 첫 계정에 최신이 아닌 메일이 잔뜩, 둘째 계정에 진짜 최신.
raw = "\n".join([
    row("20260814161900", "이영희", "행사 제안"),      # 계정1
    row("20260814164200", "이영희", "안내 자료 추가안"),  # 계정1
    row("20260817040400", "구글", "색인 생성 안 됨"),        # 계정1
    row("20260816134800", "한빛건설강남", "광고 운영 정책"),    # 계정2 ← 이게 보여야 한다
])
got = M._rows(raw, 3)
check("세 줄만 돌려준다", len(got), 3)
check("가장 최근이 맨 위", "구글" in got[0], True)
check("둘째 계정 메일이 잘려나가지 않는다",
      any("한빛건설강남" in g for g in got), True)
check("사람이 읽는 날짜로 바꾼다", got[0].startswith("1. 2026-08-17 04:04"), True)

print("\n[2] 한 계정만 있어도 멀쩡하다 — 옛 코드가 통과하던 조건")
# ⚠ 옛 버그는 계정이 하나면 절대 드러나지 않았다. 그래서 개발 중 안 걸렸다.
one = "\n".join([row("20260101090000", "가", "첫째"),
                 row("20260102090000", "나", "둘째")])
check("최신순", [g.split("| ")[2] for g in M._rows(one, 2)], ["둘째", "첫째"])

print("\n[3] 쓰레기 줄은 버린다")
check("빈 줄·형식 안 맞는 줄 무시",
      M._rows("\n\n엉뚱한 줄\n" + row("20260102090000", "나", "둘째"), 5),
      ["1. 2026-01-02 09:00 | 나 | 둘째"])

print("\n[4] 첨부 이름을 그대로 믿지 않는다 — 이름은 보낸 사람이 정한다")
check("경로 구분자를 걷어낸다", "/" not in M._safe_name("../../etc/passwd", "x"), True)
check("역슬래시도", "\\" not in M._safe_name("a\\b.pdf", "x"), True)
check("앞의 점을 떼어 숨김파일을 막는다",
      M._safe_name("...hidden", "x").startswith("."), False)
check("빈 이름이면 대체 이름", M._safe_name("   ", "첨부1"), "첨부1")
check("제어문자 제거", "\n" not in M._safe_name("a\nb.pdf", "x"), True)
check("멀쩡한 한글 이름은 그대로", M._safe_name("1번 자료.pdf", "x"), "1번 자료.pdf")

print("\n[5] 내려받는 자리는 고정이다 — 부르는 쪽이 경로를 못 정한다")
import inspect

sig = inspect.signature(M.mail_download)
check("경로 인자가 없다", [p for p in sig.parameters if "dir" in p or "path" in p], [])
check("Downloads 아래", "Downloads" in str(M.DOWNLOAD_ROOT), True)

print("\n[6] 검색은 제목만 보지 않는다")
src = inspect.getsource(M.mail_search)
check("보낸사람도 본다", '"sender"' in src, True)
check("본문은 선택이다(느려서)", '"content"' in src and "deep" in src, True)
check("공백 뗀 꼴도 찾는다 ('한빛건설 강남' → '한빛건설강남')",
      'replace(" ", "")' in src, True)

print("\n[7] 최근 목록은 지목된 요청을 가로채지 않는다")
# 이게 사고의 핵심이었다. 도구는 멀쩡히 도는데 **부르는 쪽이 잘못 골랐다**.
# 코드로 막을 수 없는 자리라, 도구 설명이 유일한 방어선이다.
# 설명에서 mail_search 로 보내는 문장이 사라지면 같은 사고가 다시 난다.
mod_src = Path(M.__file__).read_text(encoding="utf-8")
recent_decl = mod_src[mod_src.index('def mail_recent') - 700:mod_src.index('def mail_recent')]
check("최근 목록 설명이 mail_search 로 넘긴다", "mail_search" in recent_decl, True)
check("지목했을 때를 예시로 든다", "한빛건설" in recent_decl or "특정 사람" in recent_decl, True)

print()
if FAIL:
    print(f"✗ 실패 {len(FAIL)}건: {FAIL}")
    sys.exit(1)
print("✓ 전부 통과 — 지목하면 찾고, 첨부는 안전한 자리에 내려받는다")
