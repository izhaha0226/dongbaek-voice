#!/usr/bin/env python3
"""Claude Code Stop 훅 — 마지막 답변을 동백 목소리로 읽어준다.

마이크가 없어도 '출력 절반'은 오늘부터 쓸 수 있게 하는 조각.
stdin으로 훅 JSON을 받아 transcript에서 마지막 assistant 텍스트를 꺼내 읽는다.

끄고 싶으면:  touch ~/dongbaek/state/voice_off
다시 켜려면:  rm ~/dongbaek/state/voice_off
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import json
import os
import re
import subprocess
import sys
from pathlib import Path

VOICE = "Yuna"
RATE = 195

# 답변 전문을 읽으면 소음이다. 결론 한두 문장만 읽는다.
# 요약을 위해 Claude 를 한 번 더 부르면 토큰과 지연이 붙으므로,
# '첫 문단 = 결론' 이라는 글쓰기 규칙에 기대어 로컬에서 잘라낸다. (0 토큰)
SUMMARY_MAX = 120
SUMMARY_SENTENCES = 2

# 기본 출력이 스피커 없는 모니터(HDMI)로 잡혀 있으면 훅이 조용히 무용지물이 된다.
# 실제로 소리가 나는 장치를 우선순위대로 고른다.
DEVICE_PREFERENCE = [
    "PowerConf", "Anker",        # 마이크와 같은 기기로 내보내야 AEC 가 산다
    "외장 헤드폰", "External Headphones",
    "Mac mini 스피커", "Mac mini Speakers",
]
VIRTUAL_HINTS = ["teams", "zoom", "blackhole", "loopback", "aggregate"]

STATE = Path.home() / "dongbaek" / "state"
OFF_SWITCH = STATE / "voice_off"

_CODE_FENCE = re.compile(r"```.*?```", re.S)
_INLINE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_PATH = re.compile(r"(?:/[\w.\-]+){2,}")
_TABLE = re.compile(r"^\s*\|.*\|\s*$", re.M)
_MD = re.compile(r"[*_#>`\[\]|]")
_WS = re.compile(r"\s+")


_HEADER = re.compile(r"^\s{0,3}#{1,6}\s")
_BULLET = re.compile(r"^\s*([-*+]|\d+\.)\s")
_QUOTE = re.compile(r"^\s*>")
_HR = re.compile(r"^\s*([-*_]\s*){3,}$")
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

# 정보가 없는 맞장구. 요약 자리를 차지하므로 뒤에 내용이 더 있으면 버린다.
_FILLER = re.compile(
    r"^(네|예|아|음|오)?[,.\s]*"
    # 어간만 적고 뒤의 어미(입니다/이네요/…)는 짧게 허용한다.
    # 통째로 적으면 '좋은 지적입니다' 처럼 어미가 붙은 변형을 놓친다.
    r"(좋은\s*(지적|질문|생각)|맞습니|맞아|맞네|그렇습니|정확합니"
    r"|알겠습니|이해했습니|말씀하신\s*대로)"
    r"[가-힣]{0,4}[.!~\s]*$"
)


def lead_paragraph(text: str) -> str:
    """결론이 담긴 첫 문단만 뽑는다.

    헤더·목록·표·코드블록·인용은 건너뛴다. 본문 문장이 나오기 시작하면
    빈 줄이 나올 때까지 모은다.
    """
    body = _CODE_FENCE.sub("", text)
    lines = body.splitlines()
    picked: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            if picked:
                break          # 첫 문단 끝
            continue
        if (
            _HEADER.match(s)
            or _BULLET.match(s)
            or _QUOTE.match(s)
            or _HR.match(s)
            or s.startswith("|")
        ):
            if picked:
                break          # 문단 뒤에 구조가 오면 거기까지
            continue           # 문단 전의 구조는 건너뜀
        picked.append(s)
    return " ".join(picked)


def clean(text: str) -> str:
    t = _CODE_FENCE.sub(" ", text)
    t = _TABLE.sub("", t)
    t = _LINK.sub(r"\1", t)
    t = _INLINE.sub(r"\1", t)
    t = _URL.sub(" 링크 ", t)
    t = _PATH.sub(" 경로 ", t)
    t = _MD.sub("", t)
    return _WS.sub(" ", t).strip()


def summarize(text: str) -> str:
    """읽어줄 한두 문장. 없으면 빈 문자열."""
    lead = clean(lead_paragraph(text))
    if not lead:
        lead = clean(text)
    if not lead:
        return ""

    sentences = [s.strip() for s in _SENT_SPLIT.split(lead) if s.strip()]
    # 앞의 맞장구는 버린다. 단, 그게 답변 전부면 남긴다.
    while len(sentences) > 1 and _FILLER.match(sentences[0]):
        sentences.pop(0)

    out = ""
    for s in sentences[:SUMMARY_SENTENCES]:
        cand = f"{out} {s}".strip()
        if out and len(cand) > SUMMARY_MAX:
            break
        out = cand
    if not out:
        out = lead

    if len(out) > SUMMARY_MAX:
        cut = out[:SUMMARY_MAX]
        i = max(cut.rfind("다."), cut.rfind("요."), cut.rfind(". "))
        out = cut[: i + 2].strip() if i > SUMMARY_MAX * 0.4 else cut.rsplit(" ", 1)[0] + "."
    return out


def last_assistant_text(transcript: Path) -> str:
    text = ""
    try:
        for line in transcript.read_text(encoding="utf-8", errors="ignore").splitlines():
            if '"assistant"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") != "assistant":
                continue
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            joined = "\n".join(p for p in parts if p).strip()
            if joined:
                text = joined
    except OSError:
        return ""
    return text


def pick_output() -> list[str]:
    """`say -a` 인자를 만든다. 마땅한 게 없으면 빈 리스트(시스템 기본)."""
    try:
        listing = subprocess.run(
            ["say", "-a", "?"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in listing.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            names.append(parts[1].strip())
    for want in DEVICE_PREFERENCE:
        for n in names:
            if want.lower() in n.lower():
                return ["-a", n]
    for n in names:
        if not any(h in n.lower() for h in VIRTUAL_HINTS):
            return ["-a", n]
    return []


def main() -> int:
    if OFF_SWITCH.exists():
        return 0
    # 동백 데몬이 부른 claude --print 는 데몬이 직접 읽어준다.
    # 여기서 또 읽으면 같은 답을 두 번 말하게 된다.
    if os.environ.get("DONGBAEK_DAEMON"):
        return 0
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0

    tp = payload.get("transcript_path")
    if not tp:
        return 0

    body = summarize(last_assistant_text(Path(tp).expanduser()))
    if not body:
        return 0

    # 이전 발화를 끊는다 (say 프로세스 + 분리해 띄운 재생 프로세스 둘 다)
    subprocess.run(["pkill", "-x", "say"], capture_output=True)
    subprocess.run(["pkill", "-f", "speak_last.py --play"], capture_output=True)

    # 재생이 끝날 때까지 훅이 붙잡고 있으면 세션이 멈춘다.
    # 자기 자신을 --play 모드로 분리해 띄우고 즉시 반환한다.
    p = subprocess.Popen(
        [sys.executable, __file__, "--play"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        p.stdin.write(body.encode("utf-8"))
        p.stdin.close()
    except OSError:
        pass
    return 0


def play(body: str) -> None:
    """분리된 프로세스에서 실제 재생. Supertonic → 실패 시 say."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        import config
        import tts

        if config.TTS_ENGINE == "supertonic" and tts.speak(body, block=True):
            return
    except Exception:
        pass
    subprocess.run(["say", *pick_output(), "-v", VOICE, "-r", str(RATE), body])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--play":
        play(sys.stdin.read())
        sys.exit(0)
    sys.exit(main())
