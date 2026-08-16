#!/usr/bin/env python3
"""통화 정리 — "전화 끝났어" 하시면 오간 말을 정리해 위키에 남긴다.

사장님 지시(2026-08-12): "내가 전화끝났어 라고 하면, 통화내용을 정리를
해서 위키에 저장을 해야지."

그날 실제로 있었던 일: "어, 전화 끝났어" 가 클로드로 가서 $0.22 를 쓰고
쓸모없는 확인 답변이 돌아왔다. 사장님 지적 — "전화 끝났어 하면 '네
알겠습니다' 아니면 '통화 내용은 뭐였는데요? 요약 정리하겠습니다' 이렇게
나와야지."

동백은 통화 중에도 계속 듣고 있다. 그 말들을 버리지 말고 모아 두었다가
정리하면 된다. 없던 데이터를 만드는 게 아니라 이미 흘려보내던 것을 줍는 것이다.

  요약은 큐웬이 한다 (0원, 로컬). 통화 내용이 클로드로 나가지 않는다 —
  남의 사적인 대화가 섞여 있고, 그건 이 맥을 벗어나지 않는 게 맞다.

⚠ 받아쓰기는 틀린다. 요약도 그만큼 틀린다. 그래서 원문을 함께 남긴다 —
  나중에 "그때 뭐라 그랬지" 를 볼 때 요약만 있으면 확인할 길이 없다.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import config

WIKI_DIR = Path.home() / "Documents/Obsidian Vault/Work/통화기록"

# 통화 한 건에 모아둘 최대 조각 수. 넘으면 오래된 것부터 버린다.
MAX_PARTS = 400

# 정리해서 남길 값어치가 있는 최소 분량. 이보다 짧으면 통화가 아니다.
MIN_PARTS = 3

# 통화 정리를 소리로 읽을 때의 상한. 전문은 위키·텔레그램으로 본다.
# 2026-08-14: 482자를 여섯 번 읽어 사장님이 "그거 언제 다 읽어?" 하셨다.
CALL_SPOKEN_MAX = 90

_buf: list[tuple[datetime, str]] = []


def note(text: str) -> None:
    """통화 중에 들린 말을 모아 둔다. 실패해도 조용히 넘어간다."""
    t = (text or "").strip()
    if not t:
        return
    _buf.append((datetime.now(), t))
    if len(_buf) > MAX_PARTS:
        del _buf[: len(_buf) - MAX_PARTS]


def clear() -> None:
    _buf.clear()


def pending() -> int:
    return len(_buf)


def _summarize(lines: list[str]) -> str:
    """큐웬으로 세 줄 요약. 실패하면 빈 문자열 — 원문은 그래도 남는다."""
    try:
        import gatekeeper

        s = gatekeeper.summarize("방금 통화", lines[:60])
        return (s or "").strip()
    except Exception:
        return ""


_MEETING_SHAPE = (
    "다음은 회의 자리에서 받아쓴 내용이다. 받아쓰기라 오인식이 섞여 "
    "있다 — 문맥으로 바로잡되 확신 없는 고유명사는 그대로 두라.\n"
    "회의록으로 정리하라. 마크다운, 아래 절 구성 그대로:\n"
    "## 핵심 논의 (불릿 3~6)\n## 결정된 것\n## 해야 할 일 "
    "(맡을 사람이 언급됐으면 함께)\n## 다음 일정·약속\n"
    "들린 것만 쓰고 지어내지 마라. 없는 절은 '(없음)' 으로.\n\n")

# GPT 모드는 회의가 아니다 — 한 사람이 AI 와 주고받은 대화다. '결정된 것'
# '맡을 사람' 같은 절을 그대로 두면 클로드가 없는 것을 채워 넣는다.
# 물어본 것과 얻은 것, 그리고 남은 숙제로 가른다.
_GPT_SHAPE = (
    "다음은 사장님이 지피티(생성형 AI)와 나눈 대화를 옆에서 받아쓴 것이다. "
    "받아쓰기라 오인식이 섞여 있다 — 문맥으로 바로잡되 확신 없는 고유명사는 "
    "그대로 두라. 사장님 말과 지피티 답이 섞여 있으니 문맥으로 가르라.\n"
    "마크다운, 아래 절 구성 그대로:\n"
    "## 무엇을 물었나 (불릿 2~5)\n## 얻은 답 (불릿 3~6)\n"
    "## 써먹을 것 (실제로 적용할 만한 것만)\n## 남은 물음 (안 풀린 것)\n"
    "들린 것만 쓰고 지어내지 마라. 없는 절은 '(없음)' 으로.\n\n")


# 통화는 회의도 지피티 대화도 아니다 — 짧게, 요지만. 소리로 읽을 첫 줄이
# 여기서 나오므로 맨 앞에 한 문장 요약을 강제한다.
_CALL_SHAPE = (
    "다음은 통화를 옆에서 받아쓴 것이다. 받아쓰기라 오인식이 섞여 있다 — "
    "문맥으로 바로잡되 확신 없는 고유명사는 그대로 두라.\n"
    "맨 첫 줄에 통화 전체를 한 문장(60자 이내)으로 요약하라. 그 아래에\n"
    "## 오간 내용 (불릿 2~4)\n## 해야 할 일 (없으면 '(없음)')\n"
    "들린 것만 쓰고 지어내지 마라.\n\n")


def _summarize_claude(lines: list[str], kind: str = "미팅") -> str:
    """클로드 정리 — 미팅·GPT 모드 전용 (사장님 지시: 정리는 무조건 클로드).

    큐웬 세 줄 요약과 달리 문서 꼴로 받는다. 도구·세션 없는 한 번짜리
    (ask_once)라 싸고, 실패하면 빈 문자열 — 원문은 그래도 남는다.
    """
    try:
        import bridge
        import config

        text = "\n".join(lines[:400])[:14000]
        shape = {"미팅": _MEETING_SHAPE, "통화": _CALL_SHAPE}.get(kind, _GPT_SHAPE)
        prompt = shape + text
        return (bridge.ask_once(prompt, model=config.MODEL_CHAT,
                                timeout=180) or "").strip()
    except Exception:
        return ""


def _slug(when: datetime) -> str:
    return when.strftime("%Y-%m-%d %H%M")


def save() -> tuple[str, Path | None]:
    """모아둔 통화를 정리해 위키에 남긴다. (음성으로 읽을 말, 파일경로).

    파일은 하루에 여러 건이 쌓이므로 시각까지 이름에 넣는다.
    """
    if len(_buf) < MIN_PARTS:
        clear()
        return "정리할 만한 통화 내용이 없습니다.", None

    started, ended = _buf[0][0], _buf[-1][0]
    lines = [t for _, t in _buf]
    # ⚠ 큐웬(_summarize)에서 클로드로 옮겼다. 게이트키퍼를 끈 이유와 같다 —
    #   소형 모델은 애매한 대목에 '모른다' 대신 '지어낸다'. 통화 요약은
    #   나중에 사장님이 사실로 믿고 움직이는 문서라 그 위험을 질 수 없다.
    #   즉시성이 필요한 복명복창과 달리 이건 통화가 끝난 뒤 스레드에서 돈다.
    summary = _summarize_claude(lines, kind="통화")

    body = [
        f"# 통화 {_slug(started)}",
        "",
        f"- 시작 {started:%H:%M} / 끝 {ended:%H:%M} "
        f"({(ended - started).seconds // 60}분, {len(lines)}조각)",
        "",
        "## 요약",
        summary or "(요약하지 못했습니다 — 아래 원문을 보세요)",
        "",
        "## 들린 대로",
        "",
        "> ⚠ 받아쓰기라 틀린 곳이 있습니다. 이름·숫자는 원문으로 확인하세요.",
        "",
    ]
    for ts, t in _buf:
        body.append(f"- `{ts:%H:%M:%S}` {t}")
    body.append("")

    path = None
    try:
        WIKI_DIR.mkdir(parents=True, exist_ok=True)
        path = WIKI_DIR / f"{_slug(started)}.md"
        path.write_text("\n".join(body), encoding="utf-8")
    except OSError:
        path = None

    n = len(lines)
    clear()
    if path is None:
        return ("통화 내용을 정리했는데 위키 저장에 실패했어요. "
                + (summary[:120] if summary else "")), None

    # ⚠ 소리로는 짧게. 사장님 지시(2026-08-14): "다 읽으라는 게 아니고,
    #   내용을 간략하게 정리해서 저장 후 share note url 로 보내주고
    #   전화내용만 요약해서 알려줘." 전문은 위키와 텔레그램으로 본다 —
    #   귀로 482자를 듣는 건 아무 쓸모가 없다.
    said = _one_line(summary) if summary else f"통화 {n}조각을 정리해 뒀어요."

    # 공유 링크는 텔레그램으로. 소리로 URL 을 읽는 건 의미가 없다.
    try:
        import briefing

        link = briefing._obsidian_link(path)
        briefing._to_telegram("📞 통화 정리",
                              f"{summary or '(요약 없음)'}\n\n📓 {path.name}\n🔗 {link}")
    except Exception:
        pass                            # 링크 못 보내도 위키에는 남았다
    return said, path


def _one_line(summary: str) -> str:
    """요약에서 소리로 읽을 한 줄만 뽑는다.

    클로드 요약은 마크다운 절이 섞여 온다. 소리로는 첫 실질 문장 하나면
    충분하다 — 나머지는 위키에 있고, 사장님은 그걸 링크로 여신다.
    """
    for raw in (summary or "").splitlines():
        s = raw.strip().lstrip("#-•* ").strip()
        if len(s) >= 8 and not s.startswith("("):
            return s[:CALL_SPOKEN_MAX]
    # ⚠ 폴백도 줄바꿈을 지운다. 그냥 잘라 넘기면 마크다운 절이 통째로
    #   소리로 나가서, 짧게 읽으려던 것이 도로아미타불이 된다.
    flat = " ".join((summary or "").split())
    return flat[:CALL_SPOKEN_MAX] or "통화 내용을 정리해 뒀어요."


def save_meeting(kind: str = "미팅") -> tuple[str, Path | None]:
    """미팅·GPT 모드 정리 — 요약은 무조건 클로드 (사장님 지시 2026-08-13).

    save() 와 같은 저장 구조, 요약기와 문서 꼴만 다르다.

    kind 는 문서 제목·파일명·요약 지시문만 가른다. 받아쓴 원문을 남기는
    방식은 같다 — 회의든 지피티와의 대화든, 나중에 확인할 것은 '무슨 말이
    오갔나' 로 똑같기 때문이다 (2026-08-14 GPT 모드 지시).
    """
    if len(_buf) < MIN_PARTS:
        clear()
        return f"정리할 만한 {kind} 내용이 없어요.", None

    started, ended = _buf[0][0], _buf[-1][0]
    lines = [t for _, t in _buf]
    minutes = max(1, (ended - started).seconds // 60)
    summary = _summarize_claude(lines, kind=kind)

    fallback = "## 회의록" if kind == "미팅" else "## 대화 정리"
    body = [
        f"# {kind} {_slug(started)}",
        "",
        f"- 시작 {started:%H:%M} / 끝 {ended:%H:%M} ({minutes}분, {len(lines)}조각)",
        "",
        summary or f"{fallback}\n(클로드 정리에 실패했습니다 — 아래 원문을 보세요)",
        "",
        "## 들린 대로",
        "",
        "> ⚠ 받아쓰기라 틀린 곳이 있습니다. 이름·숫자는 원문으로 확인하세요.",
        "",
    ]
    for ts, t in _buf:
        body.append(f"- `{ts:%H:%M:%S}` {t}")
    body.append("")

    path = None
    try:
        WIKI_DIR.mkdir(parents=True, exist_ok=True)
        path = WIKI_DIR / f"{kind} {_slug(started)}.md"
        path.write_text("\n".join(body), encoding="utf-8")
    except OSError:
        path = None

    n = len(lines)
    clear()
    if path is None:
        return f"{kind} 내용을 정리했지만 위키 저장에 실패했어요.", None
    said = f"{kind} {minutes}분, {n}조각을 정리해 위키에 남겼어요."
    if summary:
        # 소리로는 핵심만 — 전문은 위키·텔레그램으로 본다.
        head = summary.replace("#", " ").replace("\n", " ").strip()
        said += " " + head[:160]
    return said, path


# 말로 부르는 주문. "전화 끝났어" 말고도 여러 갈래로 말씀하신다.
_END_CALL = re.compile(
    r"(전화|통화).{0,4}(끝났|끝냈|끊었|마쳤|종료)"
    r"|(끊었|끝났)어.{0,3}(전화|통화)")


def is_end_call(t: str) -> bool:
    """'전화 끝났어' 인가. normalize 된 문자열을 받는다."""
    return bool(_END_CALL.search(t or ""))
