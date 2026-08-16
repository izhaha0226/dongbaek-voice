#!/usr/bin/env python3
"""오늘의 뉴스 — 해커뉴스 + AI 마케팅 (무료 공개 API/RSS, 키 없음, 0 토큰).

수집은 로컬 HTTP 두 곳이다:
  해커뉴스  공식 Firebase API (topstories → item)
  AI 마케팅  구글 뉴스 RSS 한국어 검색 ("AI 마케팅")

한국어로 옮기는 것만 큐웬(gatekeeper)이 한다. 큐웬이 죽으면 영문 제목
그대로 쓴다 — 뉴스가 아예 안 오는 것보단 낫다 (fail-open).
"""

import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

_HN = "https://hacker-news.firebaseio.com/v0"
TIMEOUT = 8


def _get(url: str, timeout: int = TIMEOUT) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()


def hn_top(hours: int = 26, limit: int = 7, scan: int = 45,
           min_score: int = 60) -> list[dict]:
    """해커뉴스 상위 — 최근 `hours` 안에 올라온 이야기만, 점수순.

    상위 목록 앞 `scan` 개만 훑는다. 전부 돌면 API 왕복이 500번이다.
    """
    ids = json.loads(_get(f"{_HN}/topstories.json"))[:scan]
    cutoff = datetime.now(timezone.utc).timestamp() - hours * 3600
    out: list[dict] = []
    for i in ids:
        try:
            it = json.loads(_get(f"{_HN}/item/{i}.json", timeout=5))
        except Exception:
            continue
        if not it or it.get("type") != "story":
            continue
        if it.get("time", 0) < cutoff or it.get("score", 0) < min_score:
            continue
        out.append({
            "title": it.get("title", ""),
            "score": it.get("score", 0),
            "url": it.get("url") or f"https://news.ycombinator.com/item?id={i}",
            "comments": it.get("descendants", 0),
        })
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def ai_marketing_news(limit: int = 6, hours: int = 36) -> list[dict]:
    """구글 뉴스 RSS — 'AI 마케팅' 한국어 검색, 최근 것만.

    ⚠ 외부 XML 이다. defusedxml 을 안 쓰는 대신(이 저장소는 새 의존성 금지)
    같은 급의 방어를 직접 한다: DTD·ENTITY 선언이 보이면 파싱 자체를 거부
    (XXE·billion-laughs 는 전부 그 문으로 들어온다 — 정상 RSS 에는 없다).
    엔티티 폭탄의 나머지 절반은 파이썬 3.12 의 expat(2.4+)가 기본
    증폭 한도로 막는다.
    """
    url = ("https://news.google.com/rss/search?"
           "q=AI%20%EB%A7%88%EC%BC%80%ED%8C%85&hl=ko&gl=KR&ceid=KR:ko")
    data = _get(url, timeout=10)
    if b"<!DOCTYPE" in data[:4096] or b"<!ENTITY" in data:
        return []
    root = ET.fromstring(data)
    out: list[dict] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        pub = item.findtext("pubDate") or ""
        try:
            dt = datetime.strptime(pub, "%a, %d %b %Y %H:%M:%S %Z").replace(
                tzinfo=timezone.utc)
            if dt < datetime.now(timezone.utc) - timedelta(hours=hours):
                continue
        except ValueError:
            pass                       # 날짜를 못 읽으면 버리지 않고 싣는다
        out.append({
            "title": title,
            "url": (item.findtext("link") or "").strip(),
            "source": (item.findtext("source") or "").strip(),
        })
        if len(out) >= limit:
            break
    return out


_KO_SCHEMA = {
    "type": "object",
    "properties": {"ko": {"type": "string"}},
    "required": ["ko"],
}
_HANGUL = __import__("re").compile(r"[가-힣]")
_HEX = __import__("re").compile(r"^#[0-9a-fA-F]{6}$")


def _ask(prompt: str, schema: dict, num_predict: int = 120,
         temperature: float = 0.4) -> dict | None:
    """큐웬 한 번 + JSON 파싱. 실패는 None — 판단은 부르는 쪽이."""
    try:
        import gatekeeper

        raw = gatekeeper._generate(prompt, timeout=25, num_predict=num_predict,
                                   temperature=temperature, format=schema)
        return json.loads(raw)
    except Exception:
        return None


def to_korean(titles: list[str]) -> list[str]:
    """영문 제목들 → 자연스러운 한국어 한 줄씩. 실패한 항목은 원문 그대로.

    ⚠ 묶음으로 시키면 소형 모델이 번역 대신 원문을 스키마에 베껴 넣는다 —
    예시를 줘도 그랬다 (실측). 한 제목씩 시키면 순순히 번역한다.
    항목마다 한글이 섞였는지 검증하고, 아니면 그 항목만 원문을 쓴다.
    """
    out: list[str] = []
    for t in titles:
        ko = t
        try:
            import gatekeeper

            raw = gatekeeper._generate(
                "IT 뉴스 제목이다. 직역하지 말고 뜻이 통하는 자연스러운 "
                "한국어 한 줄로 번역하라. 제품·회사 이름은 그대로 둔다.\n\n" + t,
                timeout=20, num_predict=120, temperature=0.2, format=_KO_SCHEMA)
            cand = gatekeeper._EMOJI.sub("", (json.loads(raw).get("ko") or "")).strip()
            if cand and _HANGUL.search(cand):
                ko = cand
        except Exception:
            pass
        out.append(ko)
    return out


# ─────────────────────────────────────────────────────────
# 큐웬이 쓰는 것들 — 글·색·대사. 팩트(링크·점수)는 절대 안 맡긴다.
# ─────────────────────────────────────────────────────────
_DEFAULT_STYLE = {"c1": "#1d3557", "c2": "#e63946",
                  "lead": "오늘의 기술과 마케팅 소식입니다."}


def style_of_day(headlines: list[str]) -> dict:
    """오늘의 테마 색 두 개와 리드 문단 — 큐웬의 '디자인 판단'.

    색이 헥스가 아니거나 리드가 이상하면 기본값 — 문서가 색 때문에
    깨지는 일은 없다.
    """
    style = dict(_DEFAULT_STYLE)
    got = _ask(
        "오늘 뉴스 헤드라인들이다. 어울리는 배색(진한 색 c1, 포인트 색 c2, "
        "헥스 6자리)과 두 문장짜리 한국어 리드 문단을 정하라. "
        "리드는 헤드라인에 있는 내용만 언급한다.\n\n"
        + "\n".join(f"- {h}" for h in headlines[:6]),
        {"type": "object",
         "properties": {"c1": {"type": "string"}, "c2": {"type": "string"},
                        "lead": {"type": "string"}},
         "required": ["c1", "c2", "lead"]},
        num_predict=200)
    if got:
        if _HEX.match((got.get("c1") or "").strip()):
            style["c1"] = got["c1"].strip()
        if _HEX.match((got.get("c2") or "").strip()):
            style["c2"] = got["c2"].strip()
        lead = (got.get("lead") or "").strip()
        if lead and _HANGUL.search(lead) and len(lead) < 200 \
                and "헤드라인" not in lead and "문장" not in lead:
            style["lead"] = lead
    return style


def comic_lines(titles_ko: list[str]) -> list[dict]:
    """네컷용 대사·캡션 — 컷마다 큐웬 한 번. 실패한 컷은 제목을 그대로 쓴다.

    묶음 금지·항목별 검증은 to_korean 과 같은 이유다.
    """
    out = []
    for t in titles_ko[:4]:
        line, cap = t[:38], ""
        got = _ask(
            "뉴스 한 줄이다. 네컷 만화의 말풍선 대사(구어체, 25자 이내)와 "
            "컷 밑 캡션(20자 이내)을 지어라. 뉴스에 없는 사실 금지.\n\n" + t,
            {"type": "object",
             "properties": {"line": {"type": "string"}, "caption": {"type": "string"}},
             "required": ["line", "caption"]},
            num_predict=100, temperature=0.6)
        if got:
            cand_l = (got.get("line") or "").strip()
            cand_c = (got.get("caption") or "").strip()
            if cand_l and _HANGUL.search(cand_l) and len(cand_l) <= 34:
                line = cand_l
            if cand_c and len(cand_c) <= 26:
                cap = cand_c
        out.append({"line": line, "caption": cap})
    return out


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap(s: str, width: int = 15, rows: int = 3) -> list[str]:
    chunks = [s[i:i + width] for i in range(0, len(s), width)]
    if len(chunks) > rows:
        chunks = chunks[:rows]
        chunks[-1] = chunks[-1][:width - 1] + "…"
    return chunks


def comic_svg(lines: list[dict], style: dict, out_path) -> bool:
    """네컷 카드뉴스 SVG — 틀·마스코트는 코드가, 대사는 큐웬이.

    큐웬은 그림을 못 그린다(텍스트 모델). 그래서 그리는 건 코드가 하고,
    '무슨 말을 하는가'만 모델의 몫이다 — 렌더링이 절대 깨지지 않는 분업.
    """
    if not lines:
        return False
    c1, c2 = style["c1"], style["c2"]
    P = 450                            # 컷 한 변
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {P*2} {P*2+70}" '
           f'font-family="Apple SD Gothic Neo, sans-serif">',
           f'<rect width="{P*2}" height="{P*2+70}" fill="#fffdf8"/>',
           f'<rect width="{P*2}" height="58" fill="{c1}"/>',
           f'<text x="24" y="38" font-size="26" fill="#ffffff" font-weight="700">'
           f'동백 네컷 뉴스 · {datetime.now().strftime("%m월 %d일")}</text>']
    for i, item in enumerate(lines[:4]):
        x, y = (i % 2) * P, 58 + (i // 2) * P + 6
        tint = c2 if i % 3 == 0 else c1
        svg.append(f'<g transform="translate({x},{y})">')
        svg.append(f'<rect x="10" y="10" width="{P-20}" height="{P-20}" rx="18" '
                   f'fill="#ffffff" stroke="{c1}" stroke-width="3"/>')
        svg.append(f'<circle cx="46" cy="52" r="20" fill="{tint}"/>'
                   f'<text x="46" y="59" font-size="18" text-anchor="middle" '
                   f'fill="#ffffff" font-weight="700">{i+1}</text>')
        # 동백꽃 마스코트 — 꽃잎 다섯 + 수술
        mx, my = P - 70, P - 84
        for ang in (0, 72, 144, 216, 288):
            svg.append(f'<ellipse cx="{mx}" cy="{my}" rx="16" ry="9" '
                       f'fill="{c2}" opacity="0.85" '
                       f'transform="rotate({ang} {mx} {my})"/>')
        svg.append(f'<circle cx="{mx}" cy="{my}" r="7" fill="#f4a300"/>')
        # 말풍선
        bx, by, bw = 34, 92, P - 110
        rows = _wrap(_esc(item["line"]))
        bh = 34 + 30 * len(rows)
        svg.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="16" '
                   f'fill="#f7f3ec" stroke="{c1}" stroke-width="2"/>')
        svg.append(f'<path d="M {bx+40} {by+bh} l 12 22 l 8 -22 z" fill="#f7f3ec" '
                   f'stroke="{c1}" stroke-width="2"/>')
        for r, row in enumerate(rows):
            svg.append(f'<text x="{bx+18}" y="{by+34+30*r}" font-size="22" '
                       f'fill="#222222">{row}</text>')
        if item.get("caption"):
            svg.append(f'<rect x="10" y="{P-52}" width="{P-20}" height="34" '
                       f'fill="{c1}" opacity="0.92"/>')
            svg.append(f'<text x="24" y="{P-29}" font-size="17" fill="#ffffff">'
                       f'{_esc(item["caption"])}</text>')
        svg.append('</g>')
    svg.append('</svg>')
    try:
        out_path.write_text("\n".join(svg), encoding="utf-8")
        return True
    except OSError:
        return False
