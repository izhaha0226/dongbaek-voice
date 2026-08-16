#!/usr/bin/env python3
"""로컬 소형 모델 게이트키퍼 — 클로드를 아껴 쓴다.

가벼운 대화(인사·잡담·의견·상식)는 맥미니 안의 소형 모델이 즉답하고,
도구·데이터·판단이 필요한 업무만 클로드로 보낸다. 평균 $0.33 짜리 호출 중
'틀려도 해가 없는' 부류를 0원·1초로 바꾸는 층이다.

안전 설계 — 순서가 전부다:
  위험 게이트(dongbaek.handle 1번)와 정규식 로컬(2번)을 '지난 뒤에만' 온다.
  실행 권한이 붙은 명령(elevated/dev)은 여기 오지 않는다. 그래서 분류가
  틀려도 '클로드가 답할 것을 로컬이 답하는' 방향으로만 틀릴 수 있고,
  게이트 우회나 실행 누락은 구조적으로 불가능하다. 이 모듈에는 셸도,
  도구도, 쓰기 권한도 없다 — 문장을 읽고 문장을 돌려줄 뿐이다.

실패는 전부 조용히 클로드로 (fail-open). ollama 가 꺼져 있어도 동백은
예전과 똑같이 동작한다 — 그냥 조금 비싸질 뿐이다.

모델은 ollama 로 돈다 (config.GATEKEEPER_MODEL, 기본 qwen3:4b Apache-2.0).
표준 라이브러리만 쓴다 — .venv 에 의존성을 보태면 mcp 격리(.venv-mcp)
같은 일이 또 생긴다.
"""

import json
import re
import urllib.request

import config

# 생각(<think>) 블록은 소리내어 읽을 것이 아니다. think 를 꺼도 형식이
# 새는 경우가 있어 방어적으로 걷어낸다.
_THINK = re.compile(r"<think>.*?</think>", re.S)

# 즉답 모델이 '이건 내가 답할 게 아니다' 하고 물러나는 신호.
# 분류가 놓친 업무 질문을 잡는 마지막 안전망이다.
_DEFER = "[업무]"

# 이모지는 소리로 읽을 게 아니다. 프롬프트로 금지해도 새는 날이 있다 —
# 실제로 첫 실측에서 "😊" 가 나왔다. 형식과 마찬가지로, 부탁 말고 걷어낸다.
_EMOJI = re.compile("[‍☀-➿️\U0001f000-\U0001faff]")


def _generate(prompt: str, *, timeout: float, num_predict: int,
              temperature: float = 0.0, format: dict | None = None) -> str:
    """ollama /api/generate 한 번. 실패는 예외로 — 판단은 부르는 쪽이 한다.

    format 에 JSON 스키마를 주면 출력이 그 형태로 '강제' 된다(constrained
    decoding). 프롬프트로 "한 단어만" 을 부탁하는 건 소용없었다 — 소형
    모델은 영어로 풀이를 늘어놓다 토큰이 끝난다. 형식은 부탁하는 게 아니라
    강제하는 것이다.
    """
    payload = {
        "model": config.GATEKEEPER_MODEL,
        "prompt": prompt,
        "stream": False,
        "think": False,
        # 안 쓰면 스스로 내려간다 (config.GATEKEEPER_KEEP_ALIVE).
        # 재로딩 1.4초 vs 상주 5.1기가 — 후자가 훨씬 비싸다.
        "keep_alive": getattr(config, "GATEKEEPER_KEEP_ALIVE", "2m"),
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if format is not None:
        payload["format"] = format
    req = urllib.request.Request(
        f"{config.GATEKEEPER_URL}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode())
    return _THINK.sub("", body.get("response") or "").strip()


def _recent_context(max_pairs: int = 4) -> str:
    """최근 주고받은 말 — transcript 꼬리에서 읽는다.

    "아까 그거" 같은 참조를 로컬에서 풀기 위한 최소 문맥.
    파일이 커져도 꼬리 8KB 만 읽는다.
    """
    try:
        with config.TRANSCRIPT_LOG.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 8192))
            tail = f.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return ""
    pairs: list[str] = []
    for line in reversed(tail):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("command") and r.get("reply"):
            pairs.append(f"사장님: {r['command'][:80]}\n동백: {r['reply'][:80]}")
        if len(pairs) >= max_pairs:
            break
    return "\n".join(reversed(pairs))


# 분류는 한 단어만 받는다. JSON 을 시키면 소형 모델이 형식을 흘리는 날이
# 있고, 형식 파싱 실패는 전부 '클로드행' 이 되어 게이트키퍼가 헛돈다.
#
# ⚠ 기본값이 '업무' 다 — "확신이 없으면 업무" 를 프롬프트에도 박고,
#   응답에 '대화' 가 없으면 코드도 업무로 처리한다. 두 겹이다.
_CLASSIFY = """음성 비서로 들어온 한국어 발화를 한 단어로 분류한다.

업무: 파일·코드·저장소·메일·일정·광고·매출·로그·배포·설정 등 도구나 실제
데이터가 필요한 것. 무엇을 실행·조회·작성·수정·요약·번역해 달라는 것 전부.
확신이 없으면 업무다.
대화: 인사·감사·안부·잡담·감정·의견·일반 상식처럼 틀려도 해가 없는 말.

발화: 고마워 잘했어 → 대화
발화: 오늘 기분 어때 → 대화
발화: 점심 뭐 먹을까 → 대화
발화: 광고플랫폼 로그 확인해줘 → 업무
발화: 김상무한테 답장 초안 잡아줘 → 업무
발화: 아까 말한 거 요약해서 보내줘 → 업무
발화: 너 지금 큐엔이야 클로드야 → 업무
발화: {command}
답("대화" 또는 "업무" 한 단어만): """

# 즉답은 소리로 재생된다 — 짧게, 존댓말, 형식 없이.
# 존댓말은 지시만으론 안 지켜져서(실사례) 예시로 보여주고 아래 검증으로 잡는다.
_CHAT = """너는 '동백', 한빛기획 사장님의 한국어 음성 비서다. 답변은 소리로
재생된다. 한두 문장, 존댓말이되 보고체가 아니라 말하듯이 — "…어요/…네요"
로 끝낸다. "…습니다/…겠습니다" 는 쓰지 마라.
마크다운·이모지·목록·영어 금지.
"요." 를 한 낱말로 따로 쓰지 마라. 어미에 붙여 한 단어로 끝낸다.
"사장님" 같은 호칭을 앞에 붙이지 마라 — 그건 동백 본체가 붙인다.
실제 데이터(일정·메일·매출·코드·파일·시각·날씨)가 필요하면 지어내지 말고
정확히 "[업무]" 라고만 답한다.
네가 어떤 모델인지, 큐엔인지 클로드인지, 지금 누가 답하고 있는지 묻는
말에는 절대 지어내지 말고 정확히 "[업무]" 라고만 답한다.

사장님: 고마워 오늘도 수고했어
동백: 별말씀을요. 저야 늘 여기 있어요.
사장님: 피곤하네
동백: 고생 많으셨어요. 잠깐이라도 쉬었다 하세요.
사장님: 옆에 있는데
동백: 아, 가까이 계시는군요. 잘 들려요.

최근 대화:
{context}

사장님: {command}
동백:"""

_POLITE_END = re.compile(r"(요|니다|까요|시죠|죠)$")


# 소형 모델이 프롬프트를 어기고 흘리는 두 가지. 이모지와 마찬가지로
# 부탁 말고 걷어낸다 — 지시만으론 안 지켜지는 걸 이미 여러 번 겪었다.
#
# ① 끝의 낱말 "요." — "존댓말로 끝내라" 를 글자 그대로 따라서 문장 뒤에
#    "요." 를 한 낱말로 붙인다 ("모르겠어요. 요." 가 실제로 나갔다).
# ② 앞의 호칭 — 호칭은 동백 본체가 붙이므로(ADDRESS_TEMPLATE) 모델이
#    또 붙이면 "홍길동님, 사장님, …" 이 된다.
_TAIL_YO = re.compile(r"([.!?])\s*요\.?\s*$")
_LEAD_ADDRESS = re.compile(r"^\s*(?:[가-힣]{0,3}사장님|회장님|[가-힣]{2,4}님)\s*[,·]?\s*")


def _tidy(text: str) -> str:
    return _LEAD_ADDRESS.sub("", _TAIL_YO.sub(r"\1", text)).strip()


def _is_polite(text: str) -> bool:
    """모든 문장이 존댓말로 끝나는가 — 사장님께 반말이 나가면 안 된다."""
    for sent in re.split(r"[.!?]+", text):
        sent = sent.strip().strip('"』」”')
        if sent and not _POLITE_END.search(sent):
            return False
    return True


# 출력 강제 스키마. enum 이라 "대화"/"업무" 밖의 답이 물리적으로 안 나온다.
_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {"verdict": {"type": "string", "enum": ["대화", "업무"]}},
    "required": ["verdict"],
}
# 즉답도 JSON 에 가둔다 — 영어 서두("Okay, let's...")·마크다운이 낄 자리가 없다.
_REPLY_SCHEMA = {
    "type": "object",
    "properties": {"reply": {"type": "string"}},
    "required": ["reply"],
}


# ⚠ '생각' 칸이 먼저 와야 한다. 순서가 전부다.
#
#   think=False 로 생각을 끄면 추론이 사라지는 게 아니라 갈 곳을 잃는다.
#   칸이 summary 하나뿐이면 거기로 밀려들어, 실측에서 이런 게 나왔다:
#     "동백이 한빛기획 사장님의 한국어 음성 비서로, 소리로 재생되는 답변을
#      두세 문장으로 존댓말로 작성해야 합니다. … 따라서 정답은 …"
#   1,385자를 쓰다 num_predict 에 잘려 JSON 이 깨졌고, summarize 는 매번
#   None 을 돌려 브리핑이 목록 낭독으로 나갔다 (IMPROVE.md 2번의 정체).
#
#   제약 디코딩은 칸을 앞에서부터 채운다. 앞에 자리를 하나 내주면 추론이
#   거기로 흘러가고 summary 는 깨끗해진다. 실측 1,385자 → 2초·60자.
#   maxLength 는 길이를 부탁이 아니라 형식으로 강제한다.
_SUM_SCHEMA = {
    "type": "object",
    "properties": {
        "생각": {"type": "string"},
        "summary": {"type": "string", "maxLength": 140},
    },
    "required": ["생각", "summary"],
}
# ⚠ 지시를 길게 쓰면 소형 모델이 지시문 자체를 보고랍시고 되뇐다 —
#   실제로 "요구사항에 따라 두세 문장으로 정리했으며..." 가 그대로 나왔다.
#   지시는 한 줄, 나머지는 예시로 보여준다.
_SUMMARIZE = """너는 '동백', 사장님의 한국어 음성 비서다. 항목만 근거로 두세 문장 존댓말 보고를 쓴다.
'생각' 칸에 먼저 정리하고, 'summary' 칸에는 소리내어 읽을 보고문만 쓴다.
항목에 없는 내용 금지. 마크다운·이모지·목록·영어 금지.

항목 ({title}):
{bullets}"""

# 모델이 지시문을 되뇌면 요약이 아니다 — 이 말들이 보이면 버린다.
_SUM_ECHO = ("요구사항", "지어내지", "보고문입니다", "정리했으며", "금지했", "존댓말")


def summarize(title: str, bullets: list[str], timeout: float = 25.0) -> str | None:
    """항목 목록 → 두세 문장 구두 보고. 실패하면 None — 부르는 쪽이 목록을
    그대로 쓴다 (fail-open, try_chat 과 같은 계약).

    숫자 계산·집계는 시키지 않는다 — 소형 모델에 숫자를 맡기면 창작한다.
    숫자는 SQL 과 코드가 만들고, 모델은 문장으로 잇기만 한다.
    """
    if not bullets:
        return None
    try:
        out = _generate(
            _SUMMARIZE.format(title=title,
                              bullets="\n".join(f"- {b}" for b in bullets)),
            # 생각 칸이 앞에서 토큰을 쓰므로 넉넉히 준다. 모자라면
            # summary 가 잘려 JSON 이 깨지고 매번 None 이 된다.
            timeout=timeout, num_predict=700, temperature=0.3,
            format=_SUM_SCHEMA,
        )
        s = (json.loads(out).get("summary") or "").strip()
        s = _EMOJI.sub("", s).strip()
        if any(w in s for w in _SUM_ECHO):
            return None               # 지시문 반향 — 목록 낭독이 낫다
        return s or None
    except Exception:
        return None


def try_chat(command: str) -> str | None:
    """가벼운 대화면 로컬 모델이 답한다. 아니면 None → 클로드.

    None 은 '내 일이 아니다' 한 가지 뜻이다. 오류·타임아웃도 같은 답으로
    뭉갠다 — 게이트키퍼가 아프다고 사장님 명령이 멈추면 안 된다.
    """
    try:
        verdict = _generate(
            _CLASSIFY.format(command=command),
            timeout=config.GATEKEEPER_CLASSIFY_TIMEOUT, num_predict=24,
            format=_VERDICT_SCHEMA,
        )
        try:
            verdict = json.loads(verdict).get("verdict", "")
        except ValueError:
            pass                      # 스키마가 깨졌으면 아래 문자열 검사로
        if "대화" not in verdict:
            return None

        ctx = _recent_context()
        # 회상 낌새가 있으면 장기 기억도 얹는다 — "그때 그거 뭐였지" 를
        # 잡담 층에서 풀면 클로드까지 갈 일이 없다.
        try:
            import memory_local
            import router

            if (getattr(config, "MEMORY_ENABLED", False)
                    and router.wants_memory(router.normalize(command))):
                hits = memory_local.recall(command, k=2)
                if hits:
                    ctx += "\n오래된 기억:\n" + "\n".join(hits)
        except Exception:
            pass

        def _once(extra: str = "") -> str | None:
            raw = _generate(
                _CHAT.format(context=ctx, command=command) + extra,
                timeout=config.GATEKEEPER_CHAT_TIMEOUT,
                num_predict=200, temperature=0.6, format=_REPLY_SCHEMA,
            )
            try:
                a = (json.loads(raw).get("reply") or "").strip()
            except ValueError:
                return None           # 형식이 깨진 답을 소리로 읽으면 안 된다
            return _tidy(_EMOJI.sub("", a)) or None

        answer = _once()
        if answer is None or _DEFER in answer:
            return None
        if not _is_polite(answer):
            # 반말이 나왔다 — 한 번만 다시. 그래도 반말이면 클로드에게
            # 넘긴다 (CLAUDE.md 가 존댓말을 강제한다). 반말 두 번보다 낫다.
            answer = _once("\n(방금 답이 반말이었다. 전부 존댓말로 다시.)")
            if answer is None or _DEFER in answer or not _is_polite(answer):
                return None
        return answer
    except Exception:
        return None


# ─────────────────────────────────────────────────────────
# 일정 명령 읽기 — 규칙이 못 읽은 말을 큐웬이 읽는다
# ─────────────────────────────────────────────────────────
# 왜 필요한가. 규칙(정규식+낱말표)으로 자연어를 잡으면 계속 깨진다.
# 2026-08-12 하루에만 '변경' 두 글자 때문에 세 곳을 고쳤다 —
# "법무사 대표 변경의 건" 은 서류 이름인데 옮기기 명령으로 읽혔다.
# 어순·동사꼴·날짜 생략도 각각 따로 패치해야 했다. 새 표현마다 또 깨진다.
#
# ⚠ 그런데 시각은 절대 맡기지 않는다.
#   이 파일 위쪽에 이미 적어둔 원칙이다 — "소형 모델에 숫자를 맡기면
#   창작한다." 11시를 1시로 적어놓고도 태연할 수 있고, 잘못 등록된 일정은
#   무시당하는 것보다 나쁘다. 사장님이 그 시각을 믿고 움직이시기 때문이다.
#
#   그래서 나누어 맡는다:
#     의도·제목 → 큐웬 (문맥 이해가 필요한 것)
#     시각      → korean_time 이 원문에서 파싱 (숫자는 코드가)
#   큐웬이 시각을 지어내도 쓰이지 않는다. 애초에 물어보지 않는다.
# ⚠ 여기엔 '생각' 칸을 두지 않는다 — summarize 와 정반대다.
#   거기는 칸이 자유 문자열 하나라 추론이 넘칠 자리가 있었지만, 여기는
#   의도가 enum 이고 제목에 길이 제한이 있어 애초에 넘칠 자리가 없다.
#   실측(2026-08-12): 생각 칸 있음 7.9초 / 80자 제한 2.2초(제목 오염) /
#   없음 0.6초·정확. 중간에 잘린 생각이 뒤 칸을 망치기까지 했다.
_SCHED_SCHEMA = {
    "type": "object",
    "properties": {
        "의도": {"type": "string", "enum": ["등록", "수정", "삭제", "없음"]},
        "제목": {"type": "string", "maxLength": 60},
    },
    "required": ["의도", "제목"],
}

_SCHED_PROMPT = """일정 명령을 읽는다. 의도와 제목만 뽑는다. 시각은 뽑지 않는다 — 다른 곳에서 처리한다.

의도: 등록(새로 만든다) / 수정(있던 것의 시각을 옮긴다) / 삭제(없앤다) / 없음(일정 명령이 아니다)
제목: 그 일정을 부르는 이름. 날짜·시각·"등록해줘" 같은 말은 빼고 알맹이만.

보기)
"오늘 일정에 법무사 대표 변경의 건으로 서류 전달 11시 등록해줘"
  의도=등록, 제목=법무사 대표 변경의 건 서류 전달
  ('대표 변경의 건' 은 서류 이름이지 옮기라는 말이 아니다)
"본사 미팅 11시로 옮겨줘"  → 의도=수정, 제목=본사 미팅
"내일 치과 예약 취소해"      → 의도=삭제, 제목=치과 예약
"오늘 날씨 어때"            → 의도=없음, 제목=

명령: {command}"""


def parse_schedule(command: str) -> dict | None:
    """일정 명령에서 의도와 제목을 뽑는다. 실패하면 None → 클로드.

    시각은 돌려주지 않는다. 부르는 쪽이 원문에서 직접 파싱해야 한다.
    """
    try:
        out = _generate(
            _SCHED_PROMPT.format(command=command),
            timeout=getattr(config, "GATEKEEPER_CHAT_TIMEOUT", 20.0),
            num_predict=300, temperature=0.0, format=_SCHED_SCHEMA,
        )
        d = json.loads(out)
        intent = (d.get("의도") or "").strip()
        title = (d.get("제목") or "").strip()
        if intent not in ("등록", "수정", "삭제") or not title:
            return None
        return {"intent": intent, "title": title}
    except Exception:
        return None
