#!/usr/bin/env python3
"""카드뉴스 조판기 — 레퍼런스급 뉴스 웹툰을 매일 아침 찍어낸다.

사장님 지시(2026-08-11): 첨부하신 깃허브·프롬프트 카드뉴스처럼 만들 것.

왜 이 구조인가 — 레퍼런스를 뜯어보면 답이 나온다:
  그 이미지들은 **한글이 정확하다.** 이미지 생성 모델은 글자를 못 쓴다
  (쓰면 뭉갠 기호가 나온다). 그러니 실제 제작 방식은 하나뿐이다 —
  **캐릭터만 그림, 레이아웃과 글자는 조판.** 여기서도 그렇게 나눈다:

    캐릭터  Z-Image 로 '한 번' 생성해 assets/cast 에 고정 (매일 그리면
            얼굴이 매일 달라진다. 레퍼런스도 같은 인물이 반복 등장한다)
    글      큐웬이 패널 제목·말풍선·팁을 쓴다 (검증 실패 시 원문 폴백)
    조판    HTML + Pretendard → 헤드리스 크롬 → PNG. 한글 100% 정확

흰 배경 스프라이트는 mix-blend-mode:multiply 로 배경을 지운다 — 누끼
따는 모델을 하나 더 얹지 않고 CSS 한 줄로 끝낸다.
"""

import base64
import json
import math
import re
import subprocess
import tempfile
import time
from datetime import date
from pathlib import Path

import config

ROOT = Path(config.CLAUDE_WORKDIR)
CAST_DIR = ROOT / "assets" / "cast"
CHROME = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

# 패널마다 돌려 쓸 배우들. 파일이 없으면 그 패널은 캐릭터 없이 나간다.
_CAST_ORDER = ["host", "partner", "host_laptop", "mascot"]

# 번호 뱃지 색 — 레퍼런스처럼 패널마다 다른 색으로 리듬을 준다.
_BADGE = ["#2f6fed", "#e8590c", "#0ca678", "#7048e8",
          "#e03131", "#1098ad", "#f08c00", "#5c7cfa"]

_HANGUL = re.compile(r"[가-힣]")


def _b64(path: Path) -> str:
    try:
        return base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return ""


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# 큐웬이 지시문을 값으로 되뇌면 이 말들이 섞인다 — 보이면 통째로 버린다.
# (요약·번역에서 두 번 겪은 그 습성. 형식 지시는 예시로 보여줘야 한다)
_ECHO = ("이내", "소제목", "한 문장", "말풍선", "실무 한 줄", "구어체",
         "글자 수", "자 이하", "예시", "다음과 같", "작성하세요", "입력")


def _clean(v: str, cap: int) -> str:
    v = (v or "").strip().strip('"')
    if not v or len(v) > cap or not _HANGUL.search(v):
        return ""
    if any(w in v for w in _ECHO):
        return ""                      # 지시문 반향 — 값이 아니다
    return v


def panel_content(title_ko: str, source: str = "") -> dict:
    """패널 하나의 글 — 큐웬이 쓴다. 실패하면 뉴스 제목만 쓰는 최소본.

    ⚠ 형식을 '설명' 하면 소형 모델은 그 설명을 값으로 되뱉는다 (실측:
      "6~14자 한국어 소제목" 이 제목 자리에 그대로 박혔다). 그래서 필드
      설명 대신 **완성된 예시 한 벌**을 보여주고, 반향 어휘는 폐기한다.
      패널마다 한 번씩 부르는 것도 같은 이유다 — 묶으면 베낀다.
    """
    fallback = title_ko.strip()
    # 폴백 제목은 첫 구분자 앞까지 — 통째로 자르면 "…30B 파라미터 모델,"
    # 처럼 쉼표로 끝나 카드가 어색해진다.
    head = re.split(r"[:–—·]|,\s", fallback, maxsplit=1)[0].strip()
    if len(head) < 4 or len(head) > 24:
        head = fallback[:22].rstrip(" ,·-–—:") + ("…" if len(fallback) > 22 else "")
    out = {"title": head, "desc": fallback[:58], "bubble": "", "tip": ""}
    try:
        import news_local

        got = news_local._ask(
            "뉴스 한 줄을 카드뉴스 한 칸으로 바꾼다. 아래 예시처럼 채워라.\n\n"
            "뉴스: Docker Sandboxes – disposable isolated sandboxes for AI agents\n"
            '결과: {"title":"도커 샌드박스","desc":"AI 에이전트용 일회용 격리 '
            '환경을 도커가 내놨습니다.","bubble":"이제 안심하고 돌리겠어요!",'
            '"tip":"자동화 작업 격리에 활용"}\n\n'
            "뉴스: 네이버, 검색 광고에 생성형 AI 도입\n"
            '결과: {"title":"네이버 AI 광고","desc":"검색 광고 소재를 생성형 '
            'AI 가 만들어 줍니다.","bubble":"소재 제작이 빨라지겠네요.",'
            '"tip":"광고주 소재 A/B 에 적용"}\n\n'
            "뉴스: " + title_ko + "\n결과:",
            {"type": "object",
             "properties": {"title": {"type": "string"},
                            "desc": {"type": "string"},
                            "bubble": {"type": "string"},
                            "tip": {"type": "string"}},
             "required": ["title", "desc", "bubble", "tip"]},
            num_predict=260, temperature=0.4)
        if got:
            for k, cap in (("title", 22), ("desc", 62), ("bubble", 30), ("tip", 34)):
                v = _clean(got.get(k, ""), cap)
                if v:
                    out[k] = v
    except Exception:
        pass
    return out


_CSS = """
:root { --ink:#1a2233; --muted:#5b6779; --line:#dfe5ee; --bg:#eef1f6; }
* { box-sizing:border-box; margin:0; padding:0; }
body { font-family:'Pretendard','Apple SD Gothic Neo',sans-serif;
       background:var(--bg); color:var(--ink); -webkit-font-smoothing:antialiased; }
.stage { width:1536px; padding:22px; display:grid; grid-template-columns:repeat(3,1fr);
         gap:18px; align-content:start; }
.card { background:#fff; border:1px solid var(--line); border-radius:16px;
        padding:18px 20px 16px; position:relative; overflow:hidden;
        box-shadow:0 1px 3px rgba(20,30,60,.06); display:flex; flex-direction:column; }
.hero { grid-column:span 1; background:linear-gradient(150deg,var(--c1),var(--c2));
        color:#fff; border:none; }
.hero h1 { font-size:34px; line-height:1.22; font-weight:800; letter-spacing:-.5px; }
.hero .hi { color:#ffe066; }
.hero p { margin-top:12px; font-size:15px; line-height:1.6; opacity:.94; }
.hero .date { margin-top:auto; padding-top:14px; font-size:13px; opacity:.8; }
.head { display:flex; align-items:center; gap:10px; margin-bottom:10px; }
.num { width:28px; height:28px; border-radius:50%; color:#fff; font-size:15px;
       font-weight:700; display:flex; align-items:center; justify-content:center;
       flex:none; }
.title { font-size:19px; font-weight:800; letter-spacing:-.3px; line-height:1.3; }
.desc { font-size:14.5px; line-height:1.62; color:var(--muted); }
.body { display:flex; gap:10px; align-items:flex-end; margin-top:12px; flex:1; }
.who { width:122px; flex:none; mix-blend-mode:multiply; margin-bottom:-6px; }
.bubble { position:relative; background:#f5f7fa; border:1.5px solid var(--line);
          border-radius:14px; padding:11px 13px; font-size:14px; line-height:1.5;
          margin-bottom:14px; }
.bubble:after { content:''; position:absolute; left:-8px; bottom:16px; width:14px;
                height:14px; background:#f5f7fa; border-left:1.5px solid var(--line);
                border-bottom:1.5px solid var(--line); transform:rotate(45deg); }
.tip { margin-top:12px; background:#eef8f1; border-radius:10px; padding:10px 12px;
       font-size:13.5px; line-height:1.5; display:flex; gap:8px; }
.tip b { color:#0ca678; flex:none; }
.foot { grid-column:span 3; display:flex; align-items:center; justify-content:space-between;
        background:#fff; border:1px solid var(--line); border-radius:16px; padding:14px 22px;
        font-size:13.5px; color:var(--muted); }
.flow { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
.chip { background:#f1f4f9; border-radius:999px; padding:6px 13px; font-size:13px;
        font-weight:600; color:var(--ink); }
.arrow { color:#adb5bd; font-size:13px; }
"""


def build_html(lead: str, panels: list[dict], style: dict) -> str:
    """카드뉴스 HTML — 패널 수에 맞춰 그리드가 늘어난다."""
    cast = {n: _b64(CAST_DIR / f"{n}.png") for n in _CAST_ORDER}
    cards = [
        f'<div class="card hero"><h1>동백 <span class="hi">뉴스 브리핑</span></h1>'
        f'<p>{_esc(lead)}</p>'
        f'<div class="date">{date.today()} · 해커뉴스 &amp; AI 마케팅</div></div>'
    ]
    for i, p in enumerate(panels):
        who = _CAST_ORDER[i % len(_CAST_ORDER)]
        img = cast.get(who) or ""
        sprite = (f'<img class="who" src="data:image/png;base64,{img}">'
                  if img else "")
        bubble = (f'<div class="bubble">{_esc(p["bubble"])}</div>'
                  if p.get("bubble") else "")
        tip = (f'<div class="tip"><b>✓</b><span>{_esc(p["tip"])}</span></div>'
               if p.get("tip") else "")
        cards.append(
            f'<div class="card">'
            f'<div class="head"><div class="num" style="background:{_BADGE[i % len(_BADGE)]}">'
            f'{i + 1}</div><div class="title">{_esc(p["title"])}</div></div>'
            f'<div class="desc">{_esc(p["desc"])}</div>'
            f'<div class="body">{sprite}<div style="flex:1">{bubble}</div></div>'
            f'{tip}</div>')

    chips = "".join(
        f'<span class="chip">{_esc(p["title"][:12])}</span>'
        + ('<span class="arrow">›</span>' if i < len(panels) - 1 else "")
        for i, p in enumerate(panels[:5]))
    cards.append(f'<div class="foot"><div class="flow">{chips}</div>'
                 f'<div>동백 자동 생성 · 글·배색: 큐웬</div></div>')

    return (f'<!doctype html><meta charset="utf-8"><style>{_CSS}'
            f':root{{--c1:{style.get("c1", "#1d3557")};--c2:{style.get("c2", "#e63946")};}}'
            f'</style><div class="stage">{"".join(cards)}</div>')


def render(html: str, out_path: Path, timeout: int = 90) -> bool:
    """헤드리스 크롬으로 PNG. 높이는 패널 수로 계산한다 (스크롤 잘림 방지).

    ⚠ --headless=new 는 스크린샷을 다 쓰고도 프로세스가 살아 있는다.
      subprocess.run 으로 기다리면 멀쩡한 렌더가 타임아웃으로 실패한다
      (실측: 파일은 완성됐는데 90초 매달림). 그래서 Popen 으로 띄우고
      파일이 다 써지면 우리가 직접 내린다 — 내가 띄운 프로세스는 내가
      정리한다는 메모리 지킴이 원칙과도 같다.
    """
    if not Path(CHROME).exists():
        return False
    out_path = Path(out_path).resolve()
    if out_path.exists():
        out_path.unlink()              # 옛 파일을 성공으로 오해하지 않도록
    n_cards = html.count('class="card')
    rows = math.ceil(n_cards / 3)
    # 넉넉히 잡는다. 설명이 두 줄이 되면 패널이 커져 하단 흐름 줄이
    # 잘린다(실측). 남는 여백은 스테이지 배경색이라 티가 나지 않는다.
    height = 40 + rows * 336 + 96
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "card.html"
        page.write_text(html, encoding="utf-8")
        try:
            proc = subprocess.Popen(
                [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                 "--force-device-scale-factor=2",
                 f"--window-size=1536,{height}",
                 f"--screenshot={out_path}", f"--user-data-dir={tmp}/prof",
                 "--virtual-time-budget=4000", page.as_uri()],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return False
        try:
            last = -1
            for _ in range(timeout * 4):
                if out_path.exists():
                    size = out_path.stat().st_size
                    # 두 번 연속 같은 크기면 다 쓴 것이다
                    if size > 10_000 and size == last:
                        return True
                    last = size
                elif proc.poll() is not None:
                    break              # 파일도 없이 죽었다
                time.sleep(0.25)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
    return out_path.exists() and out_path.stat().st_size > 10_000


def make(lead: str, headlines: list[str], style: dict, out_path: Path,
         limit: int = 5) -> bool:
    """뉴스 헤드라인 → 카드뉴스 PNG. 한 번에 끝내는 진입점."""
    heads = [h for h in headlines if h.strip()][:limit]
    if not heads:
        return False
    panels = [panel_content(h) for h in heads]
    return render(build_html(lead, panels, style), out_path)
