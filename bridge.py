"""Claude CLI 브릿지 — 세션 재사용 + 토큰 회계.

세션 재사용이 토큰 절약의 1순위 레버입니다.
새 세션마다 시스템 프롬프트를 캐시 쓰기(정가 1.25배)로 다시 올리는 대신,
--resume 으로 붙으면 캐시 읽기(정가 0.1배)가 됩니다. 실측 기준 약 7배 차이.
"""

import json
import subprocess
import time
from datetime import datetime, timezone

import config


class ClaudeError(RuntimeError):
    pass


class AuthError(ClaudeError):
    pass


# ─────────────────────────────────────────────────────────
# 세션
# ─────────────────────────────────────────────────────────
# 세션은 '모델별로' 따로 보관한다.
#
# 소넷과 오퍼스를 한 세션에서 번갈아 쓰면 모델이 바뀔 때마다 프롬프트 캐시가
# 무효화된다. 캐시 읽기(0.1배)로 갈 것이 캐시 쓰기(1.25배)가 되니 이 파일의
# 첫 줄에 적힌 7배 차이가 매번 되살아난다. 모델마다 자기 세션을 갖고 있으면
# 각자 캐시가 계속 따뜻하다.
def _read_store() -> dict:
    try:
        data = json.loads(config.SESSION_FILE.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or "session_id" in data:
        # 모델별로 나누기 전의 옛 형식. 어느 모델 것인지 알 수 없으니 버린다.
        return {}
    return data


def _load_session(model: str) -> str | None:
    rec = _read_store().get(model) or {}
    sid = rec.get("session_id")
    # 오래 쉰 세션은 버린다. 프롬프트 캐시가 이미 만료됐을 시간이라
    # 이어붙여도 큰 문맥을 통째로 다시 올릴 뿐이다 (오늘 한 번에 $1 넘게 나갔다).
    if not sid or time.time() - rec.get("updated_at", 0) > config.SESSION_MAX_IDLE_SEC:
        return None
    return sid


def _save_session(model: str, sid: str) -> None:
    store = _read_store()
    store[model] = {"session_id": sid, "updated_at": time.time()}
    config.SESSION_FILE.write_text(json.dumps(store, ensure_ascii=False))


def reset_session(model: str | None = None) -> None:
    """model 을 주면 그 모델 세션만, 없으면 전부 초기화."""
    if model is None:
        config.SESSION_FILE.unlink(missing_ok=True)
        return
    store = _read_store()
    store.pop(model, None)
    config.SESSION_FILE.write_text(json.dumps(store, ensure_ascii=False))


def latest_session() -> str | None:
    """가장 최근에 쓴 세션 ID. 'claude --resume 으로 이어받기' 용."""
    best = None
    for rec in _read_store().values():
        if not isinstance(rec, dict) or not rec.get("session_id"):
            continue
        if best is None or rec.get("updated_at", 0) > best.get("updated_at", 0):
            best = rec
    return best["session_id"] if best else None


# ─────────────────────────────────────────────────────────
# 토큰 회계
# ─────────────────────────────────────────────────────────
def _log_tokens(prompt: str, usage: dict, cost: float | None, elevated: bool,
                model: str | None = None, tier: str = "") -> dict:
    fresh = usage.get("input_tokens", 0) or 0
    cw = usage.get("cache_creation_input_tokens", 0) or 0
    cr = usage.get("cache_read_input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    # 정가 환산: 캐시쓰기 1.25배, 캐시읽기 0.1배
    effective = fresh + cw * 1.25 + cr * 0.10
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "prompt": prompt[:200],
        "elevated": elevated,
        "model": model,
        "tier": tier,
        "input": fresh,
        "cache_write": cw,
        "cache_read": cr,
        "output": out,
        "effective_input": round(effective),
        "cost_usd": cost,
    }
    with config.TOKEN_LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def usage_summary() -> str:
    """오늘 쓴 토큰 요약 — '동백, 토큰 얼마나 썼어' 에 대답용."""
    if not config.TOKEN_LOG.exists():
        return "아직 기록이 없습니다."
    today = datetime.now(timezone.utc).date().isoformat()
    n = 0
    eff = out = 0
    cost = 0.0
    for line in config.TOKEN_LOG.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not r.get("ts", "").startswith(today):
            continue
        n += 1
        eff += r.get("effective_input", 0)
        out += r.get("output", 0)
        cost += r.get("cost_usd") or 0.0
    # 공짜로 막은 건수도 함께 읽어야 '클로드 최소화'가 잘 되고 있는지 들린다.
    # transcript 의 ts 는 로컬 시각이라 오늘 날짜도 로컬 기준으로 잡는다.
    local_n = gk_n = 0
    if config.TRANSCRIPT_LOG.exists():
        local_today = datetime.now().date().isoformat()
        for line in config.TRANSCRIPT_LOG.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not r.get("ts", "").startswith(local_today):
                continue
            if r.get("route") == "local":
                local_n += 1
            elif r.get("route") == "gatekeeper":
                gk_n += 1
    free_bits = []
    if local_n:
        free_bits.append(f"로컬 {local_n}건")
    if gk_n:
        free_bits.append(f"소형 모델 {gk_n}건")
    free = "과 ".join(free_bits)
    if not n:
        if free:
            return f"오늘 클로드 호출은 없습니다. {free}은 토큰 없이 끝냈습니다."
        return "오늘은 아직 호출이 없습니다."
    tail = f" 이 밖에 {free}은 토큰이 들지 않았습니다." if free else ""
    return (
        f"오늘 {n}번 호출했고, 실효 입력 {eff:,} 토큰, 출력 {out:,} 토큰입니다. "
        f"환산 비용은 약 {cost:.2f} 달러입니다.{tail}"
    )


# ─────────────────────────────────────────────────────────
# 호출
# ─────────────────────────────────────────────────────────
def ask(prompt: str, *, elevated: bool = False, dev: bool = False,
        on_text=None) -> tuple[str, dict]:
    """Claude에 물어보고 (답변, 메타) 반환. config.BRIDGE 가 경로를 고른다.

    호출하는 쪽은 어느 브릿지인지 몰라도 된다 — 계약이 같다.
    on_text 는 SDK 경로에서만 의미가 있다 (CLI 는 답을 다 만든 뒤에야 준다).
    """
    if config.BRIDGE == "sdk":
        import bridge_sdk          # 지연 임포트 — bridge_sdk 가 이 모듈을 쓴다

        return bridge_sdk.ask(prompt, elevated=elevated, dev=dev, on_text=on_text)
    return _ask_cli(prompt, elevated=elevated, dev=dev)


def _ask_cli(prompt: str, *, elevated: bool = False, dev: bool = False) -> tuple[str, dict]:
    """Claude에 물어보고 (답변, 메타) 반환.

    등급은 셋이고, 도구·권한·모델은 전부 config.TOOL_POLICY 한 표에서 온다.

    기본           : 평상시 대화·조회 (소넷)
    dev=True       : 코드 작업 — 음성 승인 없이 통과하되 스냅샷이 남는다 (오퍼스)
    elevated=True  : 음성 재확인을 통과한 위험 작업 (오퍼스)

    ⚠ --disallowed-tools 에서 뺀 것과 '승인된 것' 은 다르다. 목록에서 빼도
      편집·셸은 여전히 승인을 요구하는데, --print 헤드리스에는 승인창이 없어
      그대로 거부된다. 그래서 permission_mode 를 반드시 함께 준다.
    """
    tier = "elevated" if elevated else ("dev" if dev else "normal")
    policy = config.TOOL_POLICY[tier]
    model = policy.get("model")

    cmd = [config.CLAUDE_BIN, "--print", "--output-format", "json"]
    if model:
        cmd += ["--model", model]

    # MCP 서버를 동백 전용 목록으로 좁힌다.
    #
    # 전역 설정에는 21개가 등록돼 있고, --print 는 매 호출마다 그걸 전부 띄운
    # 뒤에야 첫 글자를 낸다. 실측 14.1초 중 약 10초가 이 대기였다.
    # 캘린더는 EventKit(calendar_local), 메일은 AppleScript 로 이미 로컬에서
    # 읽고 쓰므로 구글 커넥터가 없어도 기능이 줄지 않는다.
    #
    # 동백에게 새 MCP 를 붙이려면 전역이 아니라 이 파일에 넣어야 한다.
    if config.MCP_CONFIG.exists():
        cmd += ["--mcp-config", str(config.MCP_CONFIG), "--strict-mcp-config"]

    # 내장 도구를 실제로 쓰는 것만 싣는다. 도구 '정의' 가 매 호출의 고정
    # 비용이라, 전부 실으면 기본 문맥이 35,767 → 좁히면 24,753 이다 (실측).
    # 등급마다 다르다 — 조회 등급에는 셸을 아예 싣지 않는다.
    tools = policy.get("tools") or config.CLAUDE_TOOLS
    if tools:
        cmd += ["--tools", *tools]

    for d in config.CLAUDE_EXTRA_DIRS:
        cmd += ["--add-dir", d]

    sid = _load_session(model or "")
    if sid:
        cmd += ["--resume", sid]

    if policy.get("disallowed"):
        cmd += ["--disallowed-tools", *policy["disallowed"]]
    if policy.get("permission_mode"):
        cmd += ["--permission-mode", policy["permission_mode"]]

    # 프롬프트는 인자가 아니라 stdin 으로 넘긴다.
    # --disallowed-tools 가 가변인자(<tools...>)라, 뒤에 프롬프트를 붙이면
    # 그걸 도구 이름으로 삼켜서 "프롬프트가 없다"는 오류가 난다.
    # stdin 으로 주면 인자 파싱·셸 이스케이프 문제가 통째로 사라진다.
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            cwd=config.CLAUDE_WORKDIR,
            capture_output=True,
            text=True,
            timeout=config.CLAUDE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        raise ClaudeError("응답이 너무 오래 걸려서 중단했습니다.")

    stderr = (proc.stderr or "").strip()
    if "authenticate" in stderr.lower() or "oauth" in stderr.lower():
        raise AuthError(
            "클로드 인증이 만료됐습니다. 터미널에서 claude 명령으로 다시 로그인해 주세요."
        )

    if proc.returncode != 0 and not (proc.stdout or "").strip():
        raise ClaudeError(stderr or f"클로드가 코드 {proc.returncode}로 종료했습니다.")

    try:
        data = json.loads(proc.stdout)
    except ValueError:
        # --resume 이 실패했을 수 있음 → 그 모델 세션만 버리고 한 번만 재시도.
        # 전부 지우면 멀쩡한 다른 모델 세션까지 캐시를 잃는다.
        if sid:
            reset_session(model or "")
            return _ask_cli(prompt, elevated=elevated, dev=dev)
        raise ClaudeError("클로드 응답을 해석하지 못했습니다.")

    if data.get("session_id"):
        _save_session(model or "", data["session_id"])

    reply = (data.get("result") or "").strip()
    meta = _log_tokens(prompt, data.get("usage") or {},
                       data.get("total_cost_usd"), elevated, model, tier)
    meta["is_error"] = bool(data.get("is_error"))
    meta["num_turns"] = data.get("num_turns")

    if data.get("is_error") and not reply:
        raise ClaudeError("클로드가 오류를 반환했습니다.")

    return reply, meta


def ask_once(prompt: str, *, model: str, timeout: int = 240) -> str | None:
    """세션·도구 없이 한 번만 묻는다. 순수 텍스트 변환용. 실패하면 None.

    ⚠ 빈 임시 디렉터리에서 돌린다. 동백의 CLAUDE.md 는 '3문장 이내,
      마크다운 금지' 를 지시하는데 그건 음성 답변 규칙이라, JSON 을 뽑을 때
      그대로 적용되면 결과가 망가진다. CLAUDE.md 가 없는 곳에서 부르면
      그 지시가 딸려오지 않는다.

    세션도 붙이지 않는다. 메일 본문으로 대화 세션을 불리면 그다음 음성
    명령까지 그 컨텍스트를 매번 캐시로 실어 나르게 된다.
    """
    import tempfile

    workdir = tempfile.mkdtemp(prefix="dongbaek-once-")
    # 도구·MCP·기본 시스템 프롬프트를 전부 뺀다.
    #
    # 텍스트를 JSON 으로 바꾸는 데는 도구가 하나도 필요 없는데, 그냥 두면
    # 도구 설명과 MCP 서버 목록이 매번 입력으로 실려 간다. 실측으로
    # 실효입력 56,948 → 7,257 (87% 감소), 한 번에 $0.269 → $0.035 였다.
    # 메일 본문은 4천 자뿐인데 나머지가 전부 부가 비용이었다.
    cmd = [config.CLAUDE_BIN, "--print", "--output-format", "json",
           "--model", model,
           "--strict-mcp-config",          # 전역 MCP 설정 무시
           "--tools", "",                  # 도구 전부 끄기
           "--system-prompt", config.ONCE_SYSTEM_PROMPT]
    try:
        proc = subprocess.run(cmd, input=prompt, cwd=workdir,
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)

    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    _log_tokens(prompt[:200], data.get("usage") or {},
                data.get("total_cost_usd"), False, model, "once")
    return (data.get("result") or "").strip() or None


def healthcheck() -> tuple[bool, str]:
    """기동 시 Claude 연결 확인.

    실제 대화를 한 번 거는 대신 `claude auth status` 만 본다.
    이유:
      - 대화를 걸면 세션 크기에 따라 30초 이상 걸린다 (실측 32초).
        기동이 그만큼 늦어지고, 타임아웃에 걸리면 마이크가 있어도 데몬이 죽는다.
      - 확인하려는 건 '인증이 살아있나' 하나뿐이다. 그건 auth status 로 충분하다.
      - 토큰도 쓰지 않는다. 기동할 때마다 수백 원씩 나가던 걸 0으로.
    """
    try:
        p = subprocess.run(
            [config.CLAUDE_BIN, "auth", "status"],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "클로드 응답이 없습니다. 네트워크를 확인해 주세요."
    except OSError as e:
        return False, f"클로드 명령을 실행하지 못했습니다: {e}"

    try:
        st = json.loads(p.stdout or "{}")
    except ValueError:
        st = {}

    if st.get("loggedIn"):
        return True, "준비됨"
    return False, "클로드 인증이 만료됐습니다. 터미널에서 claude 명령으로 다시 로그인해 주세요."
