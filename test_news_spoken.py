#!/usr/bin/env python3
"""번역 안 된 영문 제목은 소리로 안 나간다 — 8시 뉴스의 빈 머리말.

2026-08-17 08:00 실측. 올라마가 꺼져 있어(config.OLLAMA_ENABLED=False,
2026-08-14 사장님 지시) news_local.to_korean 이 전부 실패했고, 실패 항목은
영문 원문 그대로 돌아온다. 그게 소리 길목의 speak.korean_only 에 문장째
걸려 이렇게 나갔다:

  만든 말  … 해커뉴스. Firefox for iOS now has a native adblocker.
             Claude: System Prompts. Research papers using "kidney …
  들린 말  … 해커뉴스. Claude: System Prompts. AI 마케팅 소식. …

세 줄 중 두 줄이 통째로 사라지고, 알파벳 19자짜리(문턱 20자 미달) 한
조각만 새어 나갔다. 머리말만 부르고 본문이 없는 꼴이다.

지키려는 것:
  ① 번역된 줄만 읽는다 (영문 원문은 소리에 안 넣는다)
  ② ⚠ 침묵하지 않는다 — 해커뉴스를 가져왔는데 한 줄도 못 읽으면
     왜 없는지 밝힌다. 빠뜨림을 조용히 넘기면 고장으로 보인다
  ③ 글(텔레그램·위키)은 그대로 원문과 링크를 담는다 — 덜어낸 건 소리뿐
  ④ 번역이 되는 날의 동작은 예전 그대로
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import sys
import tempfile
from pathlib import Path

import briefing
import news_local
import speak

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


def truthy(name, got):
    check(name, bool(got), True)


# 실측 그대로 — 2026-08-17 08:00 state/briefing.log
REAL = ["Firefox for iOS now has a native adblocker",
        "Claude: System Prompts",
        'Research papers using "kidney disappointment" instead of "kidney failure"']

briefing.NEWS_WIKI_DIR = Path(tempfile.mkdtemp())
news_local.hn_top = lambda **k: [
    {"title": t, "score": 300 - i, "url": f"https://hn/{i}", "comments": 42}
    for i, t in enumerate(REAL)]
news_local.ai_marketing_news = lambda **k: [
    {"title": "AI 마케팅 시장 급성장 - 전자신문", "url": "https://n/1", "source": "전자신문"}]
news_local.style_of_day = lambda hs: {"c1": "#1d3557", "c2": "#e63946", "lead": "리드 문단."}
news_local.comic_lines = lambda ts: []
briefing._gen_cardnews = lambda style, lead, heads: None
briefing._photo_to_telegram = lambda p, cap: True


print("\n[1] 번역이 통째로 실패한 날 (올라마 꺼짐) — 영문은 소리에 안 들어간다")
news_local.to_korean = lambda ts: list(ts)          # 실패 = 원문 그대로
spoken, full = briefing.news()
check("영문 제목 없음", any(t in spoken for t in REAL), False)
truthy("왜 없는지 밝힌다", "번역이 안 돼서" in spoken)
truthy("건수까지 말한다", f"해커뉴스 {len(REAL)}건" in spoken)
truthy("마케팅 소식은 그대로 나간다", "AI 마케팅 시장 급성장" in spoken)

print("\n[2] 소리에 남은 말은 영어 가드를 통과한다 — 잘려나갈 것이 없다")
check("speak 가 덜어낼 문장 없음", speak.korean_only(spoken), spoken)

print("\n[3] 글은 안 건드렸다 — 원문과 링크는 텔레그램·위키에 그대로")
truthy("전문에 원제", REAL[0] in full)
truthy("전문에 링크", "https://hn/0" in full)

print("\n[4] 번역이 되는 날은 예전 그대로")
news_local.to_korean = lambda ts: ["파이어폭스 iOS 에 광고 차단 내장",
                                   "클로드 시스템 프롬프트 공개", "논문 표현 이야기"]
spoken, _ = briefing.news()
truthy("해커뉴스 머리말", "해커뉴스." in spoken)
truthy("번역된 제목을 읽는다", "파이어폭스 iOS 에 광고 차단 내장" in spoken)
check("변명은 안 붙는다", "번역이 안 돼서" in spoken, False)

print("\n[5] 반쪽만 번역된 날 — 된 것만 읽고 안 된 것은 버린다")
news_local.to_korean = lambda ts: [ts[0], "클로드 시스템 프롬프트 공개", ts[2]]
spoken, _ = briefing.news()
truthy("된 것은 읽는다", "클로드 시스템 프롬프트 공개" in spoken)
check("안 된 것은 안 읽는다", REAL[0] in spoken or REAL[2] in spoken, False)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    sys.exit(1)
print("✅ 전부 통과 — 번역 안 된 영문 제목은 소리로 안 나간다")
