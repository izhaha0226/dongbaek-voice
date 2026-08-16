#!/usr/bin/env python3
"""뉴스 브리핑 검증 — 네트워크·큐웬 없이 파싱·조합·저장만.

핵심: 소스 하나가 죽어도 나머지는 나간다, 번역이 어긋나면 원문이 낫다,
위키 파일은 볼트의 기존 이름 규칙을 따른다.
    python test_news.py
"""
import json
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import briefing
import news_local

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


def truthy(name, got):
    check(name, bool(got), True)


now = int(datetime.now(timezone.utc).timestamp())

print("\n[1] 해커뉴스 — 오늘 것·점수순, 옛날 것과 저점수는 거른다")
FAKE_ITEMS = {
    "topstories.json": [1, 2, 3, 4],
    "item/1.json": {"type": "story", "title": "New LLM", "score": 300, "time": now - 3600},
    "item/2.json": {"type": "story", "title": "Old news", "score": 500, "time": now - 60 * 3600},
    "item/3.json": {"type": "story", "title": "Low score", "score": 10, "time": now - 3600},
    "item/4.json": {"type": "story", "title": "Rust tool", "score": 150, "time": now - 7200},
}
news_local._get = lambda url, timeout=8: json.dumps(
    next(v for k, v in FAKE_ITEMS.items() if url.endswith(k))).encode()
hn = news_local.hn_top()
check("2건만 남는다", [h["title"] for h in hn], ["New LLM", "Rust tool"])

print("\n[2] 구글 뉴스 RSS — 제목·출처 파싱")
RSS = """<?xml version="1.0"?><rss><channel>
<item><title>AI 마케팅 시장 급성장 - 전자신문</title>
<link>https://n.example/1</link><source url="x">전자신문</source>
<pubDate>%s</pubDate></item>
</channel></rss>""" % datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
news_local._get = lambda url, timeout=8: RSS.encode()
ai = news_local.ai_marketing_news()
check("1건 파싱", len(ai), 1)
truthy("제목", "AI 마케팅 시장" in ai[0]["title"])

print("\n[2b] ⚠ DTD·ENTITY 가 든 XML 은 파싱하지 않는다 (XXE·엔티티 폭탄)")
EVIL = b"""<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x "boom">]><rss><channel>
<item><title>&x;</title></item></channel></rss>"""
news_local._get = lambda url, timeout=8: EVIL
check("거부하고 빈 목록", news_local.ai_marketing_news(), [])

print("\n[3] 번역 — 항목별 검증, 베껴 쓴 항목만 원문 유지")
import gatekeeper  # noqa: E402

_answers = {"New LLM": "새 LLM 공개", "Rust tool": "Rust tool"}


def _fake_gen(prompt, **kw):
    for en, ko in _answers.items():
        if en in prompt:
            return json.dumps({"ko": ko}, ensure_ascii=False)
    return json.dumps({"ko": ""})


gatekeeper._generate = _fake_gen
out = news_local.to_korean(["New LLM", "Rust tool"])
check("번역된 건 번역으로", out[0], "새 LLM 공개")
check("영어 베껴쓰기는 그 항목만 원문", out[1], "Rust tool")

print("\n[3b] 스타일 — 색은 헥스만 믿는다, 리드는 한국어만")
news_local._ask = lambda *a, **k: {"c1": "빨강", "c2": "#12ab34", "lead": "오늘은 AI 모델 소식이 많습니다. 마케팅도 두 건입니다."}
st = news_local.style_of_day(["헤드라인"])
check("깨진 색은 기본값", st["c1"], news_local._DEFAULT_STYLE["c1"])
check("멀쩡한 색은 적용", st["c2"], "#12ab34")
truthy("리드 적용", "AI 모델 소식" in st["lead"])

print("\n[3c] 네컷 — 대사는 큐웬, 그림은 코드 (길면 자른다)")
news_local._ask = lambda *a, **k: {"line": "로컬 에이전트 시대가 왔다!", "caption": "해커뉴스 1위"}
cuts = news_local.comic_lines(["로컬 에이전트 모델 공개"])
check("대사 채택", cuts[0]["line"], "로컬 에이전트 시대가 왔다!")
tmp0 = Path(tempfile.mkdtemp())
svg_path = tmp0 / "네컷.svg"
ok = news_local.comic_svg(cuts, news_local._DEFAULT_STYLE, svg_path)
truthy("SVG 생성", ok and svg_path.exists())
svg_body = svg_path.read_text(encoding="utf-8")
truthy("대사가 그림에", "로컬 에이전트 시대가" in svg_body)
truthy("캡션이 그림에", "해커뉴스 1위" in svg_body)
check("긴 줄 줄바꿈", news_local._wrap("가" * 50)[-1].endswith("…"), True)

print("\n[3e] 카드뉴스 — 지시문 반향은 값이 아니다 (실측 사고)")
import cardnews  # noqa: E402

# 실제로 카드에 "6~14자 한국어 소제목" 이 제목으로 박혔던 그 사고
cardnews.news_local = news_local
news_local._ask = lambda *a, **k: {
    "title": "6~14자 한국어 소제목", "desc": "뉴스를 설명하는 한 문장(40자 이내)",
    "bubble": "구어체 존댓말(20자 이내)", "tip": "실무 한 줄(24자 이내)"}
got = cardnews.panel_content("Docker Sandboxes for AI agents")
check("반향 제목 폐기 → 원문 앞머리", got["title"], "Docker Sandboxes for A…")
truthy("반향 말풍선 폐기", got["bubble"] == "")
news_local._ask = lambda *a, **k: {
    "title": "도커 샌드박스", "desc": "격리 환경을 도커가 내놨습니다.",
    "bubble": "안심하고 돌리겠어요!", "tip": "자동화 격리에 활용"}
got = cardnews.panel_content("Docker Sandboxes for AI agents")
check("정상 값은 채택", got["title"], "도커 샌드박스")
truthy("팁도 채택", got["tip"] == "자동화 격리에 활용")

html = cardnews.build_html("오늘의 리드", [got], {"c1": "#111111", "c2": "#222222"})
truthy("HTML 에 제목·말풍선·팁", all(
    x in html for x in ("도커 샌드박스", "안심하고 돌리겠어요", "자동화 격리에 활용")))
truthy("색이 적용됨", "--c1:#111111" in html)

print("\n[4] 뉴스 브리핑 조합 + 위키 저장 (볼트 이름 규칙)")
tmp = Path(tempfile.mkdtemp())
briefing.NEWS_WIKI_DIR = tmp
news_local.hn_top = lambda **k: [
    {"title": "New LLM", "score": 300, "url": "https://hn/1", "comments": 42}]
news_local.ai_marketing_news = lambda **k: [
    {"title": "AI 마케팅 시장 급성장 - 전자신문", "url": "https://n/1", "source": "전자신문"}]
news_local.to_korean = lambda ts: ["새 LLM 나왔다"]
news_local.style_of_day = lambda hs: {"c1": "#1d3557", "c2": "#e63946", "lead": "리드 문단."}
news_local.comic_lines = lambda ts: [{"line": "대사", "caption": "캡션"}]
SENT_PHOTOS = []
# 뉴스 브리핑은 카드뉴스를 부른다 — 여기서 진짜 크롬을 돌리면 안 된다
briefing._gen_cardnews = lambda style, lead, heads: "카드뉴스테스트.png"
briefing._photo_to_telegram = lambda p, cap: SENT_PHOTOS.append(str(p)) or True
spoken, full = briefing.news()
truthy("소리에 리드", "리드 문단." in spoken)
truthy("소리에 해커뉴스", "새 LLM 나왔다" in spoken)
truthy("소리에 마케팅 (매체명은 뗀다)", "AI 마케팅 시장 급성장" in spoken and "전자신문." not in spoken)
wiki = tmp / f"{date.today()} AI 마케팅 데일리 브리핑.md"
truthy("위키 파일 이름 규칙", wiki.exists())
body = wiki.read_text(encoding="utf-8")
truthy("리드 카드 (그라디언트)", "linear-gradient" in body and "리드 문단." in body)
truthy("네컷 SVG 생성·삽입", (tmp / f"{date.today()} 뉴스 네컷.svg").exists()
       and f"![[{date.today()} 뉴스 네컷.svg]]" in body)
truthy("마케팅 절 (스타일 제목)", "AI 마케팅</h2>" in body)
truthy("해커뉴스 절 + 원제 병기", "원제 New LLM" in body)
truthy("전문에 링크", "https://hn/1" in full)
truthy("카드뉴스가 노트 맨 위", body.index("![[카드뉴스테스트.png]]") < body.index("뉴스 네컷"))
truthy("카드뉴스 텔레그램 전송", SENT_PHOTOS and "카드뉴스테스트.png" in SENT_PHOTOS[0])
truthy("공유 링크 첨부", "file://" in full or "obsidian://open" in full)

print("\n[5] 소스 하나가 죽어도 나머지는 나간다")
news_local.hn_top = lambda **k: (_ for _ in ()).throw(RuntimeError("죽음"))
spoken, _ = briefing.news()
truthy("마케팅만으로도 브리핑", "AI 마케팅" in spoken)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과 — 아침 8시 뉴스, 로컬+큐웬으로")
