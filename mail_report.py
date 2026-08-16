"""받은 메일을 업체별로 갈라 정리하고, 메일마다 판단을 붙인다.

사장님 지시 (2026-08-14):
  "받은 메일은 업체별로 분류해서 정리후 나한테 보고해. 어제 누구에게서
   어떤 내용으로 메일이 왔다. 예를 들어 협력사 계정이면 그 회사 이름으로
   부르고, 그 계정으로 아무개가 몇 통 보냈는지 묶어서 분석해보니 어떤
   사업 운영에 대한 내용이다. 판단해보니 cc(참조)로 들어온거고 실질적인
   답변을 요하는건 아니다. … 어떤 사람에게서 온 메일은 포워딩 된 것 같다.
   아무래도 확인하고 답변을 주라는 취지인듯 하다."

⚠ 이 파일은 공개판(dongbaek-voice)으로도 옮겨진다. 예시에 실존 인물
  이름이나 회사 도메인을 적지 마라 — 옮길 때마다 손으로 지우게 된다.

그래서 이 모듈이 하는 일은 세 가지다.
  ① 계정을 업체 이름으로 부른다 (config.MAIL_ACCOUNT_LABELS)
  ② 같은 사람이 여러 통 보냈으면 묶는다 — 통수와 흐름이 보여야 판단이 된다
  ③ 묶음마다 '무슨 내용이고, 답을 요하는가' 를 클로드가 판단한다

⚠ 판단은 클로드가 한다. 소형 모델은 애매한 대목에 '모른다' 대신 '지어낸다' —
  메일 판단은 사장님이 사실로 믿고 움직이는 문서라 그 위험을 질 수 없다.
  게이트키퍼를 끈 이유와 같다.

⚠ 소리로는 짧게. 전문은 위키와 텔레그램으로 본다. 34통을 귀로 듣는 건
  아무 쓸모가 없다 — 통화 정리에서 이미 겪었다 ("그거 언제 다 읽어?").
"""

import collections
from datetime import datetime
from pathlib import Path

import config

WIKI_DIR = Path.home() / "Documents/Obsidian Vault/Work/메일정리"


def label_of(account: str) -> str:
    """Mail.app 계정 이름을 업체 이름으로.

    주소가 아니라 계정 '이름' 으로 맞춘다 — 사람이 붙인 이름이라 'Aimtop'
    처럼 주소와 다를 수 있어 부분 일치로 본다.
    """
    low = (account or "").lower()
    for key, name in config.MAIL_ACCOUNT_LABELS.items():
        if key.lower() in low:
            return name
    return config.MAIL_LABEL_FALLBACK


def _sender_name(sender: str) -> str:
    """'홍길동 <hong@example.com>' → '홍길동'. 이름이 없으면 주소를 쓴다."""
    s = (sender or "").strip()
    name = s.split("<")[0].strip().strip('"').strip()
    return name or s


def group(msgs: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """{업체: {보낸사람: [메일…]}} — 통수와 흐름이 보이게 두 겹으로 묶는다."""
    out: dict[str, dict[str, list[dict]]] = collections.defaultdict(
        lambda: collections.defaultdict(list))
    for m in msgs or []:
        out[label_of(m.get("account", ""))][_sender_name(m.get("sender", ""))].append(m)
    return {k: dict(v) for k, v in out.items()}


def _prompt(grouped: dict) -> str:
    lines = [
        "다음은 사장님이 어제오늘 받은 메일이다. 업체(메일 계정)별로, 그리고",
        "보낸 사람별로 묶여 있다. 받아쓰기가 아니라 실제 메일 본문이다.",
        "",
        "업체마다, 그리고 보낸 사람마다 이렇게 정리하라:",
        "  - 몇 통을 보냈고 무슨 내용인지 (취합해서 한 덩어리로)",
        "  - **판단**: 답을 요하는가, 참조(cc)로 들어온 것인가, 포워딩인가,",
        "    광고·알림이라 볼 것도 없는가. 그렇게 본 근거를 한 마디로.",
        "",
        "마크다운으로. 업체를 ## 로, 보낸 사람을 ### 로. 광고·알림성 발신은",
        "묶어서 한 줄로만 처리하라 — 사장님이 하실 일이 없다.",
        "맨 끝에 '## 오늘 손댈 것' 절을 두고, 실제로 액션이 필요한 것만",
        "3개 이내로 추린다. 없으면 '(없음)'.",
        "",
        "⚠ 들린 것만 쓰고 지어내지 마라. 본문에 없는 배경을 만들지 마라.",
        "",
    ]
    for label, senders in grouped.items():
        total = sum(len(v) for v in senders.values())
        lines.append(f"===== {label} ({total}통) =====")
        for who, items in senders.items():
            lines.append(f"--- {who} ({len(items)}통) ---")
            for m in items:
                lines.append(f"[{m.get('date','')[:24]}] {m.get('subject','')}")
                if m.get("body"):
                    lines.append(f"  본문: {m['body'][:300]}")
        lines.append("")
    return "\n".join(lines)[:60000]


def _analyze(grouped: dict) -> str:
    """클로드에게 판단을 맡긴다. 실패하면 빈 문자열 — 목록은 그래도 남는다."""
    try:
        import bridge

        return (bridge.ask_once(_prompt(grouped), model=config.MODEL_CHAT,
                                timeout=300) or "").strip()
    except Exception:
        return ""


def _spoken(analysis: str, total: int, grouped: dict) -> str:
    """소리로 읽을 한두 마디. 전문은 링크로 여신다."""
    counts = ", ".join(f"{k} {sum(len(v) for v in s.values())}통"
                       for k, s in grouped.items())
    head = f"받은 메일 {total}통이에요. {counts}."
    # 분석의 '오늘 손댈 것' 첫 줄이 있으면 그것만 덧붙인다.
    take = ""
    hit = False
    for raw in (analysis or "").splitlines():
        s = raw.strip()
        if s.startswith("##") and "손댈" in s:
            hit = True
            continue
        if hit:
            s = s.lstrip("#-•*1234567890. ").strip()
            if len(s) >= 6 and not s.startswith("("):
                take = s
                break
    if take:
        head += f" 먼저 보실 건, {take}"
    return head[:config.MAIL_REPORT_SPOKEN_MAX]


def build(hours: int | None = None) -> tuple[str, Path | None]:
    """(소리로 읽을 말, 위키 경로). 전문과 공유 링크는 텔레그램으로 보낸다."""
    import mail_local

    hours = hours or config.MAIL_REPORT_HOURS
    msgs = mail_local.received_by_account(
        hours=hours,
        max_per_account=config.MAIL_REPORT_MAX_PER_ACCOUNT,
        body_chars=config.MAIL_REPORT_BODY_CHARS)
    if msgs is None:
        return "메일을 읽지 못했어요. 메일 앱이 떠 있는지 봐주세요.", None
    if not msgs:
        return f"최근 {hours}시간 안에 받은 메일이 없어요.", None

    grouped = group(msgs)
    analysis = _analyze(grouped)

    now = datetime.now()
    body = [f"# 메일 정리 {now:%Y-%m-%d %H%M}", "",
            f"- 최근 {hours}시간, {len(msgs)}통", ""]
    body.append(analysis or "(클로드 정리에 실패했습니다 — 아래 목록을 보세요)")
    body += ["", "## 받은 대로", ""]
    for label, senders in grouped.items():
        total = sum(len(v) for v in senders.values())
        body.append(f"### {label} ({total}통)")
        for who, items in senders.items():
            body.append(f"- **{who}** ({len(items)}통)")
            for m in items:
                body.append(f"  - `{m.get('date','')[:24]}` {m.get('subject','')}")
        body.append("")

    path = None
    try:
        WIKI_DIR.mkdir(parents=True, exist_ok=True)
        path = WIKI_DIR / f"메일 {now:%Y-%m-%d %H%M}.md"
        path.write_text("\n".join(body), encoding="utf-8")
    except OSError:
        path = None

    said = _spoken(analysis, len(msgs), grouped)
    if path is not None:
        try:
            import briefing

            briefing._to_telegram(
                "📬 메일 정리",
                f"{analysis or '(정리 실패)'}\n\n📓 {path.name}\n"
                f"🔗 {briefing._obsidian_link(path)}")
        except Exception:
            pass
    return said, path
