"""Agent SDK 브릿지 — 답이 나오는 대로 말한다.

bridge.py 와 똑같은 일을 하되 `claude --print --output-format json` 대신
claude_agent_sdk 를 쓴다. 차이는 하나다: 답을 다 만들 때까지 기다리지 않는다.

    CLI : 명령 → (전부 생성될 때까지 대기) → 답 도착 → 그제서야 TTS 시작
    SDK : 명령 → 첫 문장 도착 → TTS 시작 → 나머지는 말하는 동안 생성

tts.py 는 이미 문장 단위로 쪼개 첫 문장부터 재생하도록 만들어져 있다
(생성이 재생보다 6~7배 빨라서 뒤가 안 밀린다). 그 앞단이 통째로 막혀 있어서
이득을 못 보고 있었을 뿐이다.

상주 연결 (config.BRIDGE_RESIDENT):
  query() 는 호출마다 `claude` 를 새로 띄운다 — 프로세스·MCP 서버·세션
  협상을 매번 다시 한다. 상주 모드는 등급별 ClaudeSDKClient 를 붙들고 있어
  두 번째 호출부터 그 비용이 사라진다. 음성 대기 시간이 중요한 normal 만
  상주한다. 실패한 호출은 일회성 경로로 조용히 폴백한다 — 단, 이미 말하기
  시작한 답변이 끊긴 경우는 다시 묻지 않고 오류를 알린다. 같은 답을 두 번
  소리내는 것이 오류 한 번보다 나쁘다.

세션 파일(state/session.json)과 토큰 회계는 bridge.py 것을 그대로 쓴다.
브릿지를 바꿔도 대화가 끊기지 않고, 비용 기록도 한 곳에 모인다.
"""

import asyncio
import sys
import threading
import time

import config

# 세션 저장·토큰 회계·예외는 브릿지가 달라도 같아야 한다. 복사하면 반드시
# 갈라지므로 bridge.py 것을 그대로 쓴다.
import bridge
from bridge import AuthError, ClaudeError


def _options(policy: dict, model: str | None, sid: str | None, *, stream: bool):
    """config.TOOL_POLICY 한 줄을 SDK 옵션으로 옮긴다.

    CLI 인자와 1:1 로 대응한다. 대응이 깨지면 두 브릿지가 다르게 동작하므로
    여기가 test_bridge_parity.py 가 검사하는 지점이다.

        --model             → model
        --tools             → tools            (어떤 도구를 '싣는가')
        --disallowed-tools  → disallowed_tools
        --permission-mode   → permission_mode
        --add-dir           → add_dirs
        --mcp-config        → mcp_servers
        --strict-mcp-config → strict_mcp_config
        --resume            → resume

    ⚠ tools 와 allowed_tools 는 다른 것이다. tools 는 '무엇을 싣는가'(매 호출의
      고정 비용을 가르는 그 목록), allowed_tools 는 '무엇을 안 물어보고
      실행하는가'다. 둘을 헷갈리면 기본 문맥이 35,767 로 되돌아간다.
    """
    from claude_agent_sdk import ClaudeAgentOptions

    opts: dict = {
        # ⚠ SDK 의 system_prompt 기본값은 None — 시스템 프롬프트가 아예 안 붙는다.
        #   CLI 의 --print 는 claude_code 프리셋을 쓰므로, 명시하지 않으면
        #   CLAUDE.md 의 '3문장 이내, 마크다운 금지' 가 통째로 빠지고
        #   TTS 가 마크다운을 소리내어 읽게 된다.
        "system_prompt": {"type": "preset", "preset": "claude_code"},
        # setting_sources 는 기본값(None = 전부)이 CLI 와 같다.
        # project 가 포함돼야 CLAUDE.md 가 로드되는데, 기본값에 이미 들어 있다.
        "cwd": config.CLAUDE_WORKDIR,
        "add_dirs": list(config.CLAUDE_EXTRA_DIRS),
        "include_partial_messages": stream,
    }

    if model:
        opts["model"] = model

    tools = policy.get("tools") or config.CLAUDE_TOOLS
    if tools:
        opts["tools"] = list(tools)

    if policy.get("disallowed"):
        opts["disallowed_tools"] = list(policy["disallowed"])
    if policy.get("permission_mode"):
        opts["permission_mode"] = policy["permission_mode"]

    # 전역에 등록된 MCP 를 전부 띄우면 첫 글자까지 10초가 더 걸린다 (실측).
    # 동백 전용 목록만 쓴다.
    if config.MCP_CONFIG.exists():
        opts["mcp_servers"] = str(config.MCP_CONFIG)
        opts["strict_mcp_config"] = True

    if sid:
        opts["resume"] = sid

    return ClaudeAgentOptions(**opts)


def _delta_text(event: dict) -> str:
    """스트림 이벤트에서 사람이 읽을 글자만 꺼낸다. 없으면 빈 문자열.

    thinking 델타는 걸러낸다 — 소리내어 읽을 내용이 아니다.
    """
    if event.get("type") != "content_block_delta":
        return ""
    delta = event.get("delta") or {}
    if delta.get("type") != "text_delta":
        return ""
    return delta.get("text") or ""


async def _collect(stream, on_text):
    """메시지 흐름에서 (답변, ResultMessage) 를 만든다. 일회성·상주가 공유한다.

    답변은 스트림 델타가 아니라 완성된 AssistantMessage 에서 만든다.
    델타는 '지금 말을 시작하기 위한' 미리보기고, 최종본이 정본이다.
    (델타는 부하가 걸리면 유실될 수 있다 — 둘을 섞으면 답이 잘린다)
    """
    from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock

    parts: list[str] = []
    result = None
    auth_failed = False

    async for msg in stream:
        if isinstance(msg, StreamEvent):
            if on_text:
                chunk = _delta_text(msg.event)
                if chunk:
                    on_text(chunk)
        elif isinstance(msg, AssistantMessage):
            if msg.error == "authentication_failed":
                auth_failed = True
            for block in msg.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
        elif isinstance(msg, ResultMessage):
            result = msg

    if auth_failed:
        raise AuthError(
            "클로드 인증이 만료됐습니다. 터미널에서 claude 명령으로 다시 로그인해 주세요."
        )
    return "".join(parts).strip(), result


async def _query(prompt: str, options):
    from claude_agent_sdk import query

    async for msg in query(prompt=prompt, options=options):
        yield msg


async def _run(prompt: str, options, on_text):
    """일회성 한 판 — 호출마다 claude 를 새로 띄운다."""
    return await _collect(_query(prompt, options), on_text)


def _finish(prompt: str, reply: str, result, *, elevated: bool,
            model: str | None, tier: str) -> tuple[str, dict]:
    """호출 결과를 세션·회계에 반영하고 (답변, 메타) 로 마감한다. 두 경로 공용."""
    if result is None:
        raise ClaudeError("클로드가 결과를 돌려주지 않았습니다.")

    if result.session_id:
        bridge._save_session(model or "", result.session_id)

    meta = bridge._log_tokens(prompt, result.usage or {}, result.total_cost_usd,
                              elevated, model, tier)
    meta["is_error"] = bool(result.is_error)
    meta["num_turns"] = result.num_turns

    if result.is_error and not reply:
        raise ClaudeError("클로드가 오류를 반환했습니다.")

    return reply, meta


def _ask_cold(prompt: str, *, policy: dict, model: str | None, tier: str,
              elevated: bool, on_text, _retried: bool = False) -> tuple[str, dict]:
    """일회성 경로 — 상주가 꺼져 있거나 폴백일 때."""
    sid = bridge._load_session(model or "")
    options = _options(policy, model, sid, stream=on_text is not None)
    try:
        reply, result = asyncio.run(
            asyncio.wait_for(_run(prompt, options, on_text),
                             timeout=config.CLAUDE_TIMEOUT_SEC)
        )
    except asyncio.TimeoutError:
        raise ClaudeError("응답이 너무 오래 걸려서 중단했습니다.")
    except AuthError:
        raise
    except Exception as e:
        # --resume 이 실패했을 수 있다 → 그 모델 세션만 버리고 한 번만 재시도.
        # 전부 지우면 멀쩡한 다른 모델 세션까지 캐시를 잃는다.
        if sid and not _retried:
            bridge.reset_session(model or "")
            return _ask_cold(prompt, policy=policy, model=model, tier=tier,
                             elevated=elevated, on_text=on_text, _retried=True)
        raise ClaudeError(f"{type(e).__name__}: {e}")

    return _finish(prompt, reply, result, elevated=elevated, model=model, tier=tier)


# ─────────────────────────────────────────────────────────
# 상주 연결
# ─────────────────────────────────────────────────────────
# 비동기 클라이언트를 붙들고 있으려면 살아 있는 이벤트 루프가 필요하다.
# asyncio.run() 은 끝나면서 루프를 닫아 클라이언트가 같이 죽는다 — 그래서
# 전용 스레드에 루프 하나를 상주시키고 모든 상주 호출을 거기로 보낸다.
_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


class _Resident:
    __slots__ = ("client", "last_used")

    def __init__(self, client):
        self.client = client
        self.last_used = time.time()


_residents: dict[str, _Resident] = {}


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is not None:
            return _loop
        loop = asyncio.new_event_loop()

        def _spin():
            asyncio.set_event_loop(loop)
            loop.run_forever()

        threading.Thread(target=_spin, name="bridge-resident", daemon=True).start()
        _loop = loop
        return loop


def _eligible(tier: str) -> bool:
    return (bool(getattr(config, "BRIDGE_RESIDENT", False))
            and tier in getattr(config, "BRIDGE_RESIDENT_TIERS", ()))


async def _drop(tier: str) -> None:
    res = _residents.pop(tier, None)
    if res is None:
        return
    try:
        await asyncio.wait_for(res.client.disconnect(), 5)
    except Exception:
        pass                # 이미 죽은 연결이다 — 버리는 게 목적이고, 버렸다


async def _ensure_client(tier: str) -> _Resident:
    """tier 의 상주 클라이언트를 (필요하면 연결해서) 돌려준다."""
    from claude_agent_sdk import ClaudeSDKClient

    res = _residents.get(tier)
    if res and time.time() - res.last_used > config.SESSION_MAX_IDLE_SEC:
        # 프롬프트 캐시도 세션 재사용 기준도 식었다. 붙들고 있어 봐야 다음
        # 호출이 큰 cache_write 를 무는 건 같다 — 세션 정책과 똑같이 새로 간다.
        await _drop(tier)
        res = None
    if res is None:
        policy = config.TOOL_POLICY[tier]
        model = policy.get("model")
        sid = bridge._load_session(model or "")
        # 스트리밍 여부는 연결 때 정해진다. 항상 켜 두고, 필요 없는 호출은
        # 델타를 그냥 버린다 — 로컬 stdio 라 공짜에 가깝다.
        options = _options(policy, model, sid, stream=True)
        client = ClaudeSDKClient(options)
        await asyncio.wait_for(client.connect(), 30)
        res = _Resident(client)
        _residents[tier] = res
    return res


async def _resident_call(tier: str, prompt: str, on_text):
    res = await _ensure_client(tier)
    try:
        await res.client.query(prompt)
        reply, result = await asyncio.wait_for(
            _collect(res.client.receive_response(), on_text),
            config.CLAUDE_TIMEOUT_SEC,
        )
    except BaseException:
        # 어떤 실패든 이 클라이언트는 더 못 믿는다. 다음 호출이 새로 연결한다.
        await _drop(tier)
        raise
    res.last_used = time.time()
    return reply, result


def _ask_resident(tier: str, prompt: str, on_text):
    """상주 경로 시도. 폴백해야 하면 None — 그 판단은 여기서 끝낸다.

    이미 말하기 시작한 답이 끊긴 경우만은 폴백하지 않는다. 다시 물으면
    같은 답을 두 번 소리내게 된다 — 그건 오류 한 번보다 나쁘다.
    """
    delivered = {"n": 0}

    def _tap(chunk):
        delivered["n"] += 1
        on_text(chunk)

    fut = asyncio.run_coroutine_threadsafe(
        _resident_call(tier, prompt, _tap if on_text else None), _ensure_loop()
    )
    try:
        return fut.result(timeout=config.CLAUDE_TIMEOUT_SEC + 20)
    except AuthError:
        raise                               # 인증 문제는 폴백으로 못 푼다
    except Exception as e:
        if delivered["n"]:
            raise ClaudeError("응답 중 연결이 끊겨서 중단했습니다.")
        print(f"[브릿지] 상주 연결 실패 → 일회성 경로로: {type(e).__name__}: {e}",
              file=sys.stderr)
        return None


def warm_up() -> None:
    """normal 클라이언트를 미리 연결해 둔다 — 첫 명령부터 스폰 없이 받는다.

    연결만 하고 아무것도 묻지 않으므로 토큰은 들지 않는다.
    실패는 조용히 넘어간다 — 어차피 첫 호출이 알아서(lazy) 연결한다.
    """
    if not _eligible("normal"):
        return
    try:
        fut = asyncio.run_coroutine_threadsafe(_ensure_client("normal"), _ensure_loop())
        fut.result(timeout=40)
    except Exception:
        pass


def ask(prompt: str, *, elevated: bool = False, dev: bool = False,
        on_text=None) -> tuple[str, dict]:
    """Claude에 물어보고 (답변, 메타) 반환. bridge.ask 와 같은 계약.

    on_text 를 주면 글자가 도착하는 대로 그 함수를 부른다. 안 주면 CLI 와
    똑같이 다 모아서 한 번에 돌려준다 — 호출하는 쪽을 안 고쳐도 된다.

    등급은 셋이고, 도구·권한·모델은 전부 config.TOOL_POLICY 한 표에서 온다.
    """
    tier = "elevated" if elevated else ("dev" if dev else "normal")
    policy = config.TOOL_POLICY[tier]
    model = policy.get("model")

    if _eligible(tier):
        out = _ask_resident(tier, prompt, on_text)
        if out is not None:
            reply, result = out
            return _finish(prompt, reply, result,
                           elevated=elevated, model=model, tier=tier)

    return _ask_cold(prompt, policy=policy, model=model, tier=tier,
                     elevated=elevated, on_text=on_text)


# healthcheck 는 여기 없다 — bridge.healthcheck 를 그대로 쓴다.
# 그건 `claude auth status` 만 보고 토큰을 쓰지 않는다. 브릿지가 무엇이든
# 확인하려는 건 '인증이 살아있나' 하나뿐이라 갈라놓을 이유가 없다.
