#!/usr/bin/env python3
"""하루 세 번 브리핑 — 아침 7:30 · 점심 13:00 · 퇴근 18:00 (launchd, 평일).

사장님 지시(2026-08-11):
  아침: 기존 브리핑(날씨·일정·메일) + 광고플랫폼 성과 분석(퇴근과 같은 깊이)
  점심: 오전에 한 업무와 오전에 주고받은 메일 정리
  퇴근: 하루 업무 보고 + 발송 메일 정리 → 위키(업무일지) 저장 + 성과 분석
        (전체 광고비 → 매출 top2 광고주 → 캠페인 → 그룹 → 키워드 순)

처리 주체 — 클로드를 쓰지 않는다 (0 토큰):
  수집은 전부 로컬이다: git 커밋 · 동백 처리 기록(transcript) · Mail.app ·
  EventKit · 광고 DB 집계 SQL · open-meteo. 큐웬(gatekeeper.summarize)은
  모은 항목을 '문장으로 잇는 것' 만 한다 — 숫자·집계를 모델에 맡기면
  창작하기 때문에, 숫자는 코드와 SQL 이 만들고 모델은 접속사만 보탠다.
  큐웬이 죽어 있으면 목록을 그대로 읽는다 (fail-open).

말과 글의 분업:
  소리(재생 큐 /say)는 요지만 — 퇴근 브리핑 전체를 읽으면 3분이 넘는다.
  전문은 텔레그램으로, 퇴근의 상세는 위키(업무일지)로 남긴다.

위키 = 옵시디언 볼트 (~/Documents/Obsidian Vault/Work/업무일지/YYYY-MM-DD.md).
회사 지식이 이미 그 볼트에 있고, 로컬 마크다운이라 저장에 아무 의존성이 없다.

시각 변경: com.dongbaek.dongbaek-briefing.plist. 끄기: config.MORNING_BRIEFING=False.
지금 한 번(소리 없이): .venv/bin/python briefing.py --mode lunch --test
"""

import json
import re
import subprocess
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

import config
import dbstore

WIKI_DIR = Path.home() / "Documents/Obsidian Vault/Work/업무일지"
WORKDAY_START_HOUR = 5          # '오늘 업무' 는 새벽 5시부터 센다
# 야간 자가 정비(self_improve.py)가 남기는 결과 노트 — 아침에 읽어드린다.
NOTE_FILE = config.STATE / "self_improve_note.json"
# 8시 뉴스 브리핑이 저장되는 곳 — 볼트에 이미 있던 폴더와 파일 규칙을
# 그대로 따른다 (6월 말에 끊긴 수동 브리핑을 동백이 이어받는다).
NEWS_WIKI_DIR = (Path.home()
                 / "Documents/Obsidian Vault/Work/마케팅/AI 마케팅 데일리 브리핑")
VAULT_NAME = "Obsidian Vault"
VAULT_ROOT = Path.home() / "Documents" / VAULT_NAME


def _obsidian_link(path: Path) -> str:
    """위키 노트의 공유 링크 (obsidian:// URI).

    로컬 볼트라 웹 URL 은 없다 — 이 URI 는 옵시디언이 깔리고 같은 볼트가
    동기화된 기기(맥·아이폰)에서 그 노트를 바로 연다. 마크다운 노트는
    확장자를 떼야 열린다 (옵시디언 관례).
    """
    try:
        rel = path.relative_to(VAULT_ROOT)
    except ValueError:
        return f"file://{path}"        # 볼트 밖 경로(테스트 등) — 로컬 링크로
    target = rel.with_suffix("") if path.suffix == ".md" else rel
    return ("obsidian://open?vault=" + urllib.parse.quote(VAULT_NAME)
            + "&file=" + urllib.parse.quote(str(target)))


# ─────────────────────────────────────────────────────────
# 내보내기 — 소리는 데몬 큐, 글은 텔레그램
# ─────────────────────────────────────────────────────────
def _speak_via_daemon(text: str) -> bool:
    try:
        token = config.TOKEN_FILE.read_text().strip()
    except OSError:
        return False
    url = f"http://127.0.0.1:{config.CONTROL_PORT}/say?token={urllib.parse.quote(token)}"
    req = urllib.request.Request(
        url, data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except (urllib.error.URLError, OSError):
        return False


def _to_telegram(prefix: str, text: str) -> bool:
    """텔레그램으로 보낸다. 테스트가 돌 때는 보내지 않는다.

    ⚠ 이 저장소는 같은 사고를 이미 여러 번 겪었다 — 테스트가 사장님 폰으로
      쏘는 것. 그래서 record()·perf.record()·self_improve._report()·
      _write_note()·_history_add() 에 전부 같은 가드가 박혀 있다.
      그런데 정작 **텔레그램으로 나가는 유일한 문**인 여기에는 없었다.

      2026-08-15 06:17~06:47 실사례: call_notes.save() 에 공유 링크 전송을
      붙였는데, test_call_notes 가 그 함수를 부른다. 스위트를 돌릴 때마다
      "📞 통화 정리" 가 폰으로 갔고, 위키 경로가 임시폴더
      (/var/folders/.../tmpXXXX/)라 사장님이 이상함을 알아채셨다.
      하루에 열 번 넘게 돌렸으니 그만큼 나갔다.

      한 곳만 막으면 통화·미팅·메일·자가개선 보고가 전부 함께 막힌다.
    """
    if os.path.basename(sys.argv[0] or "").startswith("test_"):
        return False
    try:
        import telegram_bridge as tb

        conf = tb.load_conf()
        if not conf or not conf.get("allowed_chat_ids"):
            return False
        tb.send_text(conf["bot_token"], conf["allowed_chat_ids"][0],
                     f"{prefix}\n{text}")
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────
# 수집 — 전부 로컬, 실패한 조각은 조용히 빠진다
# ─────────────────────────────────────────────────────────
def _git_today() -> list[str]:
    """오늘 커밋 — ~/dongbaek 과 ~/projects 의 저장소들."""
    out: list[str] = []
    roots = [Path.home() / "dongbaek"]
    projects = Path.home() / "projects"
    if projects.is_dir():
        roots += sorted(p for p in projects.iterdir() if p.is_dir())
    for repo in roots:
        if not (repo / ".git").exists():
            continue
        try:
            r = subprocess.run(
                ["git", "-C", str(repo), "log",
                 f"--since={WORKDAY_START_HOUR}:00", "--oneline", "--no-merges"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        lines = [l for l in r.stdout.strip().splitlines() if l]
        if lines:
            head = lines[0].split(" ", 1)[1] if " " in lines[0] else lines[0]
            head = re.sub(r"^\w+(\(.+?\))?!?:\s*", "", head)
            out.append(f"{repo.name} 커밋 {len(lines)}건 (최근: {head[:44]})")
    return out


def _dongbaek_done(since: str) -> list[str]:
    """동백이 처리한 업무 명령 — 클로드 경로만. 잡담·단순 조회는 일지감이 아니다."""
    items: list[str] = []
    try:
        for r in dbstore.rows(since=since):
            if r.get("ts", "") < since or r.get("route") != "claude":
                continue
            cmd = (r.get("command") or "").strip()
            if len(cmd) >= 6:
                items.append(cmd[:60])
    except OSError:
        return []
    # 같은 말 반복은 하나로
    seen: list[str] = []
    for c in items:
        if c not in seen:
            seen.append(c)
    return [f"동백 처리: {c}" for c in seen[-8:]]


def _meetings_today() -> list[str]:
    import speak

    try:
        import calendar_local

        evs = calendar_local.events(days=1, include_past_today=True)
    except Exception:
        return []
    if not evs:
        return []
    out = []
    today = date.today()
    for e in evs:
        if e["start"].date() != today:
            continue
        when = "종일" if e.get("all_day") else speak.clock_ampm(e["start"].hour, e["start"].minute)
        out.append(f"미팅: {when} {e['title']}")
    return out[:6]


def _mail_bullets(recv_hours: int, sent_hours: int) -> list[str]:
    out: list[str] = []
    try:
        import mail_local

        received = mail_local.received_brief(hours=recv_hours)
        if received:
            # 알림성 메일은 제목을 읽으면 소음이 된다 — 2026-08-13 아침에
            # "Keesei Lee님 등 여러 사람들이 Li(LinkedIn)" 로 나갔다. 24자에서
            # 잘려 뜻이 사라진 데다, 어차피 사장님이 하실 일이 없는 메일이다.
            # 서비스명으로 묶어 개수만 세고, 사람이 보낸 것만 제목을 읽는다.
            humans, services = [], []
            for m in received:
                svc = mail_local.notice_service(m.get("from", ""),
                                                m.get("addr", ""))
                if not svc:
                    humans.append(m)
                elif svc not in services:
                    services.append(svc)
            parts = []
            if humans:
                tops = ", ".join(f"{m['subject'][:24]}({m['from'][:10]})"
                                 for m in humans[:4])
                parts.append(f"받은 메일 {len(humans)}통 — {tops}")
            if services:
                parts.append(f"알림 {len(received) - len(humans)}통"
                             f"({', '.join(services[:3])})")
            if parts:
                out.append(" / ".join(parts))
        sent = mail_local.sent_recent(hours=sent_hours)
        if sent:
            tops = ", ".join(f"{m['subject'][:24]}→{m['to'].split('@')[0]}"
                             for m in sent[:5])
            out.append(f"보낸 메일 {len(sent)}통 — {tops}")
    except Exception:
        pass
    return out


def _summarize(title: str, bullets: list[str]) -> str:
    """큐웬으로 두세 문장 보고. 죽어 있으면 목록을 그대로 잇는다."""
    if not bullets:
        return "특별한 업무 기록이 없습니다."
    try:
        import gatekeeper

        s = gatekeeper.summarize(title, bullets)
        if s:
            return s
    except Exception:
        pass
    return " ".join(b + "." for b in bullets[:5])


def _improve_note_today() -> str | None:
    """오늘 새벽 자가 정비 노트 — 아침 브리핑에서 보고한다 (사장님 지시:
    가설 기반 개선도 아침 브리핑으로). 개선이 없던 밤은 조용히 넘어간다."""
    try:
        n = json.loads(NOTE_FILE.read_text())
    except (OSError, ValueError):
        return None
    if n.get("date") != date.today().isoformat():
        return None
    if not n.get("ok", False) and not n.get("changed", False):
        return "밤사이 자가 개선을 시도했지만 검증에 실패해 되돌렸습니다."
    if not n.get("changed", False):
        return None                     # 변경 없는 밤 — 낭독은 소음이다
    hypo = (n.get("hypothesis") or "").strip()
    result = (n.get("result") or "").strip()
    head = f"가설: {hypo} " if hypo else ""
    return f"밤사이 자가 개선 한 건 — {head}{result} 테스트 전부 통과했습니다."


def _ads_analysis(day: date, label: str) -> str:
    try:
        import ads_local

        a = ads_local.analysis(day, day, label)
        if a:
            return a
    except Exception:
        pass
    return ""


# ─────────────────────────────────────────────────────────
# 모드별 구성 — (소리로 읽을 것, 텔레그램 전문)
# ─────────────────────────────────────────────────────────
def morning() -> tuple[str, str]:
    parts: list[str] = []
    try:
        import weather_local

        w = weather_local.today()
        if w:
            parts.append(w)
    except Exception:
        pass
    try:
        import calendar_local
        import speak

        evs = calendar_local.events(days=1)
        if evs is not None:
            if not evs:
                parts.append("오늘 일정은 없습니다.")
            else:
                heads = []
                for e in evs[:4]:
                    when = "종일" if e.get("all_day") else speak.clock_ampm(e["start"].hour, e["start"].minute)
                    heads.append(f"{when} {e['title']}")
                parts.append(f"오늘 일정 {len(evs)}건. " + ". ".join(heads) + ".")
    except Exception:
        pass
    try:
        import mail_local

        m = mail_local.unread_count()
        if m:
            parts.append(m)
    except Exception:
        pass
    ads = _ads_analysis(date.today() - timedelta(days=1), "어제")
    if ads:
        parts.append(ads)
    try:
        import ads_local

        for warn in ads_local.anomalies(date.today() - timedelta(days=1)):
            parts.append("⚠ " + warn if False else warn)   # 소리엔 기호 무의미
    except Exception:
        pass
    note = _improve_note_today()
    if note:
        parts.append(note)
    text = "좋은 아침입니다. " + " ".join(parts)
    return text, text


def lunch() -> tuple[str, str]:
    since = f"{date.today()}T0{WORKDAY_START_HOUR}:00"
    bullets = _git_today() + _dongbaek_done(since) + _mail_bullets(6, 6)
    summary = _summarize("오전 업무 보고", bullets)
    spoken = "점심 브리핑입니다. " + summary
    full = spoken
    if bullets:
        full += "\n\n" + "\n".join(f"· {b}" for b in bullets)
    return spoken, full


def evening() -> tuple[str, str]:
    since = f"{date.today()}T0{WORKDAY_START_HOUR}:00"
    work = _git_today() + _dongbaek_done(since) + _meetings_today()
    mails = _mail_bullets(12, 12)
    summary = _summarize("오늘 업무 보고", work + mails)
    ads_a = _ads_analysis(date.today(), "오늘")
    ads_d = ""
    try:
        import ads_local

        ads_d = ads_local.detail(date.today(), date.today(), "오늘") or ""
    except Exception:
        pass

    wiki_path = _write_journal(summary, work, mails, ads_a, ads_d)
    saved = f" 업무일지는 위키에 저장했습니다." if wiki_path else ""

    # 오늘 문답·일지를 장기 기억에 넣는다 — 하루 한 번이면 충분하다.
    try:
        import memory_local

        memory_local.index_new()
    except Exception:
        pass

    spoken = "퇴근 브리핑입니다. " + summary + (" " + ads_a if ads_a else "") + saved
    full = spoken
    if work or mails:
        full += "\n\n" + "\n".join(f"· {b}" for b in work + mails)
    if wiki_path:
        full += f"\n📓 {wiki_path}\n🔗 {_obsidian_link(wiki_path)}"
    return spoken, full


def _write_journal(summary: str, work: list[str], mails: list[str],
                   ads_a: str, ads_d: str) -> Path | None:
    """일일 업무일지 — 옵시디언 볼트에 마크다운으로."""
    try:
        WIKI_DIR.mkdir(parents=True, exist_ok=True)
        today = date.today()
        yo = "월화수목금토일"[today.weekday()]
        lines = [
            f"# {today} ({yo}) 업무일지",
            "",
            "## 요약",
            summary,
            "",
            "## 오늘 한 일",
            *([f"- {b}" for b in work] or ["- 기록 없음"]),
            "",
            "## 메일",
            *([f"- {b}" for b in mails] or ["- 기록 없음"]),
            "",
            "## 광고플랫폼 성과",
            ads_a or "- 데이터 없음",
        ]
        if ads_d:
            lines += ["", "### 업체별 상세", ads_d]
        lines += ["", f"*동백 자동 생성 · {datetime.now().strftime('%H:%M')}*"]
        path = WIKI_DIR / f"{today}.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    except OSError:
        return None


def _gen_cardnews(style: dict, lead: str, heads: list[str]) -> str | None:
    """카드뉴스 한 장 — 사장님이 보여주신 레퍼런스 형식(cardnews.py).

    캐릭터는 assets/cast 의 고정 배우, 글은 큐웬, 조판은 크롬. 실패하면
    옛 방식(단일 웹툰 그림)으로 물러나고, 그것도 안 되면 SVG 네컷이 받는다.
    """
    if not getattr(config, "NEWS_CARDNEWS", True):
        return None
    try:
        import cardnews

        out = NEWS_WIKI_DIR / f"{date.today()} 카드뉴스.png"
        NEWS_WIKI_DIR.mkdir(parents=True, exist_ok=True)
        if cardnews.make(lead, heads, style, out):
            return out.name
    except Exception:
        pass
    return None                        # 실패하면 SVG 네컷이 받는다


def _photo_to_telegram(path: Path, caption: str) -> bool:
    try:
        import telegram_bridge as tb

        conf = tb.load_conf()
        if not conf or not conf.get("allowed_chat_ids"):
            return False
        return tb.send_photo(conf["bot_token"], conf["allowed_chat_ids"][0],
                             str(path), caption)
    except Exception:
        return False


def news() -> tuple[str, str]:
    """8시 뉴스 — 해커뉴스 오늘자 + AI 마케팅 소식 (사장님 지시 2026-08-11).

    소리는 헤드라인 몇 개만, 전문은 텔레그램, 기록은 위키.
    소스 하나가 죽어도 나머지는 나간다.
    """
    import news_local

    hn: list[dict] = []
    ai: list[dict] = []
    try:
        hn = news_local.hn_top()
    except Exception:
        pass
    try:
        ai = news_local.ai_marketing_news()
    except Exception:
        pass
    ko = news_local.to_korean([h["title"] for h in hn]) if hn else []

    # 문서의 글·색·네컷 대사는 큐웬이 정한다 (팩트·링크는 코드 몫 —
    # 모델에 맡기면 창작한다). 전부 검증 실패 시 기본값 폴백.
    ai_heads = [a["title"].rsplit(" - ", 1)[0] for a in ai]
    style = news_local.style_of_day(ko + ai_heads)
    comic_name = None
    try:
        cuts = news_local.comic_lines((ko or ai_heads)[:4])
        if cuts:
            NEWS_WIKI_DIR.mkdir(parents=True, exist_ok=True)
            comic_file = NEWS_WIKI_DIR / f"{date.today()} 뉴스 네컷.svg"
            if news_local.comic_svg(cuts, style, comic_file):
                comic_name = comic_file.name
    except Exception:
        pass

    sp = ["여덟 시 뉴스 브리핑입니다.", style["lead"]]
    if ko:
        sp.append("해커뉴스. " + " ".join(f"{k}." for k in ko[:3]))
    if ai:
        heads = [a["title"].rsplit(" - ", 1)[0] for a in ai[:2]]
        sp.append("AI 마케팅 소식. " + " ".join(f"{h}." for h in heads))
    if len(sp) == 1:
        sp.append("오늘은 가져올 뉴스가 없습니다.")
    spoken = " ".join(sp)

    webtoon_name = _gen_cardnews(style, style["lead"], ko + ai_heads)
    path = _write_news_wiki(hn, ko, ai, style=style, comic_name=comic_name,
                            webtoon_name=webtoon_name)
    if webtoon_name:
        _photo_to_telegram(NEWS_WIKI_DIR / webtoon_name, "오늘의 뉴스 웹툰 🎨")

    full = spoken
    if hn:
        full += "\n\n해커뉴스:"
        for i, h in enumerate(hn):
            k = ko[i] if i < len(ko) else h["title"]
            full += f"\n{i + 1}. {k} ({h['score']}점, 댓글 {h['comments']})\n   {h['url']}"
    if ai:
        full += "\n\nAI 마케팅:"
        for a in ai:
            full += f"\n· {a['title']}\n  {a['url']}"
    if path:
        full += f"\n📓 {path}\n🔗 {_obsidian_link(path)}"
    return spoken, full


def _write_news_wiki(hn: list[dict], ko: list[str], ai: list[dict],
                     style: dict | None = None,
                     comic_name: str | None = None,
                     webtoon_name: str | None = None) -> Path | None:
    """볼트 규칙 그대로: 'YYYY-MM-DD AI 마케팅 데일리 브리핑.md'.

    스타일은 인라인 HTML 로 입힌다 — 옵시디언 읽기 화면이 그대로 렌더링하고,
    스니펫 설정 없이 어느 볼트에서 열어도 같은 모습이다. 색·리드는 큐웬이
    정한 값(style_of_day), 링크·점수는 코드가 꽂는다.
    """
    if not hn and not ai:
        return None                    # 빈 날은 빈 파일을 만들지 않는다
    if style is None:
        import news_local

        style = dict(news_local._DEFAULT_STYLE)
    c1, c2 = style["c1"], style["c2"]

    def h2(label: str) -> str:
        return (f'<h2 style="color:{c1};border-bottom:3px solid {c2};'
                f'padding-bottom:4px;margin-top:1.2em">{label}</h2>')

    try:
        NEWS_WIKI_DIR.mkdir(parents=True, exist_ok=True)
        today = date.today()
        lines = [
            f"# {today} AI 마케팅 데일리 브리핑",
            "",
            f'<div style="background:linear-gradient(135deg,{c1},{c2});'
            'color:#ffffff;padding:18px 22px;border-radius:14px;'
            f'font-size:1.05em;line-height:1.55">{style["lead"]}</div>',
            "",
        ]
        if webtoon_name:
            lines += [f"![[{webtoon_name}]]", ""]
        if comic_name:
            lines += [f"![[{comic_name}]]", ""]
        if ai:
            lines += [h2("AI 마케팅")]
            lines += [f"- [{a['title']}]({a['url']})" for a in ai]
            lines += [""]
        if hn:
            lines += [h2("해커뉴스 (기술)")]
            for i, h in enumerate(hn):
                k = ko[i] if i < len(ko) else h["title"]
                lines.append(f"- [{k}]({h['url']}) — {h['score']}점"
                             + (f" · 원제 {h['title']}" if k != h["title"] else ""))
            lines += [""]
        lines += [f'<div style="color:#888;font-size:0.85em">동백 자동 생성 · '
                  f'{datetime.now().strftime("%H:%M")} · 글·배색·네컷 대사: 큐웬</div>']
        path = NEWS_WIKI_DIR / f"{today} AI 마케팅 데일리 브리핑.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        return path
    except OSError:
        return None


# ─────────────────────────────────────────────────────────
_MODES = {"morning": (morning, "🌅 아침 브리핑"),
          "lunch": (lunch, "🍚 점심 브리핑"),
          "evening": (evening, "🌆 퇴근 브리핑"),
          # news 는 시각 추론에 안 걸린다 — plist 가 --mode news 로 부른다.
          "news": (news, "📰 뉴스 브리핑")}


def _mode_by_hour(hour: int) -> str:
    if hour < 11:
        return "morning"
    if hour < 16:
        return "lunch"
    return "evening"


def main() -> int:
    if not getattr(config, "MORNING_BRIEFING", True):
        print("MORNING_BRIEFING=False — 건너뜀")
        return 0

    mode = None
    for i, a in enumerate(sys.argv):
        if a == "--mode" and i + 1 < len(sys.argv):
            mode = sys.argv[i + 1]
    if mode not in _MODES:
        mode = _mode_by_hour(datetime.now().hour)
    test = "--test" in sys.argv

    build, prefix = _MODES[mode]
    spoken, full = build()

    spoke = False if test else _speak_via_daemon(spoken)
    sent = _to_telegram(prefix + (" (시험)" if test else ""), full)

    print(f"[브리핑:{mode}] 소리={'생략' if test else ('OK' if spoke else '데몬 없음')} "
          f"텔레그램={'OK' if sent else '실패'}")
    print(full)
    return 0


if __name__ == "__main__":
    sys.exit(main())
