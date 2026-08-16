#!/usr/bin/env python3
"""개발 모드 · 등급별 정책 검증.

약속:
  ① 포괄 사유('확인이 필요한 요청')로만 걸린 요청은 승인을 묻지 않는다
  ② 코드 작업이면 스냅샷을 강제로 남긴다 — "되돌려" 가 유일한 안전망이므로
  ③ 명시적 위험(배포·삭제·집행…)은 여전히 음성 승인을 받는다
  ④ 등급별 도구·권한·모델이 config.TOOL_POLICY 표와 정확히 일치한다

도구 제한을 열어둔 지금(사장님 지시), ③이 되돌릴 수 없는 작업을 막는
유일한 방어선이다. 여기가 무너지면 승인 없이 배포까지 갈 수 있다.
    python test_dev_mode.py
"""
import config
import bridge
import code_guard
import dongbaek
import router
import speak

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


# CLI 인자를 검사하는 테스트다. bridge.ask 는 config.BRIDGE 를 보고 갈리므로
# 여기서는 CLI 구현을 직접 잡는다 — 브릿지 설정과 무관하게 같은 것을 잰다.
bridge_real_ask = bridge._ask_cli

CALLS = []
SNAPS = []
CONFIRMS = []

# ── 실제 CLI 인자가 어떻게 조립되는지 (실행은 하지 않는다)
# '--disallowed-tools 에서 뺐다'와 '승인됐다'는 다르다. 헤드리스에는 승인창이
# 없어서, 편집 자동 승인 모드를 안 붙이면 조용히 거부된다. 실측으로 확인한
# 계약이라 인자 조립 단계에서 고정해 둔다.
import subprocess

ARGV = []
_real_run = subprocess.run


def _capture_run(cmd, **kw):
    ARGV.append(list(cmd))
    raise subprocess.TimeoutExpired(cmd, 1)   # 실제 호출은 하지 않는다


def argv_for(**kw) -> list[str]:
    ARGV.clear()
    subprocess.run = _capture_run
    try:
        bridge_real_ask(".", **kw)
    except Exception:
        pass
    finally:
        subprocess.run = _real_run
    return ARGV[0] if ARGV else []

# 실제 Claude·git·소리 없이 흐름만 본다
bridge.ask = lambda prompt, elevated=False, dev=False, on_text=None: (  # type: ignore[assignment]
    CALLS.append({"prompt": prompt, "elevated": elevated, "dev": dev}),
    ("고쳤습니다.", {"effective_input": 0, "cache_read": 0,
                  "cache_write": 0, "output": 0, "cost_usd": 0}),
)[1]
code_guard.guard = lambda target, note="": (  # type: ignore[assignment]
    SNAPS.append(target),
    (True, "", {"repo": target, "label": "테스트", "fingerprint": "f0"}),
)[1]
code_guard.tree_fingerprint = lambda repo: "f0"  # type: ignore[assignment]  # 변경 없음
speak.say = lambda *a, **k: None  # type: ignore[assignment]


def run(command, *, confirm_answer=True):
    CALLS.clear()
    SNAPS.clear()
    CONFIRMS.clear()

    def confirm(cmd, hit):
        CONFIRMS.append(hit)
        return confirm_answer

    reply = dongbaek.handle(command, confirm=confirm, source="test")
    return reply


print("[1] 개발 요청 — 승인 없이, dev 권한으로, 스냅샷과 함께")
config.DEV_MODE = True
run("동백 config 파일에 주석 하나 달아줘")
check("승인을 묻지 않음", CONFIRMS, [])
check("Claude 호출 1회", len(CALLS), 1)
check("dev 권한(셸 없음)", CALLS and CALLS[0]["dev"], True)
check("전권 아님", CALLS and CALLS[0]["elevated"], False)
check("스냅샷 강제", len(SNAPS), 1)

print("\n[2] 오인식된 개발 요청도 같은 길 — '폼픽 파일에 주성문 달아봐'")
run("폼픽 파일에 주성문 하나 달아봐봐")
check("승인을 묻지 않음", CONFIRMS, [])
check("dev 권한", CALLS and CALLS[0]["dev"], True)
check("스냅샷 강제", len(SNAPS), 1)

print("\n[3] 명시적 위험은 여전히 승인 게이트")
run("프로덕션 배포해줘", confirm_answer=False)
check("승인을 물음", len(CONFIRMS), 1)
check("거부되면 Claude 호출 없음", len(CALLS), 0)

run("프로덕션 배포해줘", confirm_answer=True)
check("승인 후 전권", CALLS and CALLS[0]["elevated"], True)
check("전권일 때 dev 아님", CALLS and CALLS[0]["dev"], False)

print("\n[4] 한빛기획 고유 위험도 승인 게이트")
run("네이버 광고 집행해줘", confirm_answer=False)
check("광고 집행은 승인 필요", len(CONFIRMS), 1)
check("거부되면 실행 없음", len(CALLS), 0)

print("\n[5] DEV_MODE 꺼지면 예전처럼 전부 승인")
config.DEV_MODE = False
run("동백 config 파일에 주석 하나 달아줘", confirm_answer=False)
check("승인을 물음", len(CONFIRMS), 1)
check("거부되면 호출 없음", len(CALLS), 0)
config.DEV_MODE = True

print("\n[6] 조회는 게이트 없이 (변경 없음 확인)")
# '최근 커밋 알려줘' 는 이제 로컬(router._is_commit_query)이 먹어서
# Claude 까지 안 간다 — Claude 로 가는 조회 표본은 로그 확인으로.
run("광고플랫폼 로그 확인해줘")
check("승인을 묻지 않음", CONFIRMS, [])
check("조회는 읽기 전용 권한", CALLS and not CALLS[0]["dev"] and not CALLS[0]["elevated"], True)

print("\n[7] 답변 꼬리표 — 바뀐 게 있을 때만 diff, '되돌려' 잔소리는 없다")
code_guard.diff_summary = lambda repo, since_head=None: "config.py 를 고쳤습니다."  # type: ignore[assignment]

# 지문 그대로 = Claude 가 아무것도 안 고침 → 답변만, 군말 없음
code_guard.tree_fingerprint = lambda repo: "f0"  # type: ignore[assignment]
reply = run("동백 config 파일에 주석 하나 달아줘")
check("변경 없으면 diff 안 읽음", reply, "고쳤습니다.")

# 지문 달라짐 = 실제 수정 → diff 는 읽되 '되돌리려면…' 안내는 붙이지 않는다
code_guard.tree_fingerprint = lambda repo: "f1"  # type: ignore[assignment]
reply = run("동백 config 파일에 주석 하나 달아줘")
check("변경 있으면 diff 읽음", "config.py 를 고쳤습니다." in reply, True)
check("'되돌려' 안내는 안 붙임", "되돌리" in reply, False)
code_guard.tree_fingerprint = lambda repo: "f0"  # type: ignore[assignment]

print("\n[7b] 조회 등급으로 고쳐도 재시작이 걸린다")
# 도구를 열면서 조회 등급도 쓰기가 가능해졌는데 스냅샷·재시작은 dev·elevated
# 에서만 걸리고 있었다. 그 사각지대로 config 가 바뀌었고, 재시작이 안 돼
# 텔레그램 브릿지가 옛 config + 새 코드로 AttributeError 를 냈다.
_seq = iter(["before", "after", "after"])
code_guard.tree_fingerprint = lambda repo: next(_seq, "after")
dongbaek._restart_pending = False
reply = run("로그 좀 보여줘")            # 안전 목록 → 게이트 없음 → normal 등급
check("조회 등급으로 처리됐다", CALLS and (CALLS[0]["dev"], CALLS[0]["elevated"]), (False, False))
check("그래도 스냅샷을 남겼다", len(SNAPS), 1)
check("그래도 재시작을 예약했다", dongbaek._restart_pending, True)
dongbaek._restart_pending = False
code_guard.tree_fingerprint = lambda repo: "f0"

print("\n[8] CLI 인자 계약 — '차단 목록에서 뺌' ≠ '승인됨'")
dev_argv = argv_for(dev=True)
norm_argv = argv_for()
elev_argv = argv_for(elevated=True)


def between(argv, flag):
    """flag 뒤에 이어지는 값들(다음 --옵션 전까지)."""
    if flag not in argv:
        return []
    i = argv.index(flag) + 1
    out = []
    while i < len(argv) and not argv[i].startswith("--"):
        out.append(argv[i])
        i += 1
    return out


for tier, argv in (("normal", norm_argv), ("dev", dev_argv), ("elevated", elev_argv)):
    pol = config.TOOL_POLICY[tier]
    # 도구를 열어둔 지금은 목록이 비어 있다. 나중에 다시 좁혀도
    # 이 검사는 표만 따라가므로 그대로 통과한다.
    check(f"{tier}: 차단 목록이 정책과 일치",
          between(argv, "--disallowed-tools"), list(pol["disallowed"]))
    check(f"{tier}: 권한 모드가 정책과 일치",
          between(argv, "--permission-mode"), [pol["permission_mode"]])
    check(f"{tier}: 모델이 정책과 일치", between(argv, "--model"), [pol["model"]])

# 목록이 비었을 때 값 없는 --disallowed-tools 를 흘리면 그다음 인자를
# 도구 이름으로 삼켜 버린다. 빈 목록이면 플래그 자체가 없어야 한다.
check("빈 차단 목록이면 플래그를 안 붙임",
      any("--disallowed-tools" in a for a in (norm_argv, dev_argv, elev_argv)
          if not config.TOOL_POLICY["normal"]["disallowed"]), False)

print("\n[7c] 음성으로 부르는 이름에서 저장소를 찾는가")
# 폴더명은 영문인데 음성은 항상 한글로 온다. 이게 안 되면 "광고플랫폼
# 고쳐줘" 가 동백 자기 폴더를 대상으로 삼고, Claude 는 실제로 광고플랫폼를
# 고치는데 스냅샷은 엉뚱한 곳에 찍혀 '되돌려' 가 안 통한다.
from pathlib import Path as _Path  # noqa: E402

for said, want in [
    ("광고플랫폼 최근 커밋 확인해줘", "my-ads"),
    ("광고플랫폼 애즈에 로그 추가해줘", "my-ads"),
    ("광고플랫폼 오에스 구조 봐줘", "adsplatform-os"),   # 긴 별칭이 먼저
    ("애즈플랫폼 코드 고쳐줘", "ads-platform"),
    ("피제이 사이트 헤더 수정해줘", "pj-site"),
    ("my-ads 구조 봐줘", "my-ads"),      # 영문으로 말해도
    ("그냥 아무 말", _Path(config.ROOT).name),            # 못 찾으면 동백 자신
]:
    if want != _Path(config.ROOT).name and not (_Path.home() / "projects" / want).is_dir():
        continue                        # 그 프로젝트가 없는 환경이면 건너뛴다
    check(f"{said[:22]!r} → {want}",
          _Path(dongbaek._guess_target(said)).name, want)

print("\n[8b] 비용 방어 — 도구를 좁히고 MCP 를 전용으로 묶는다")
# 도구 '정의' 가 매 호출의 고정 비용이다. 전부 실으면 기본 문맥이 35,767,
# 실제 쓰는 것만 남기면 24,753 (실측 -31%). 세션 캐시가 만료될 때마다
# 이걸 통째로 다시 올리므로 차이가 그대로 돈이다.
check("조회 등급은 조회용 목록을 쓴다",
      between(norm_argv, "--tools"), list(config.TOOLS_QUERY))
check("개발 등급은 전체 목록을 쓴다",
      between(dev_argv, "--tools"), list(config.CLAUDE_TOOLS))
check("웹 검색은 어디서나 남긴다 (날씨 같은 질문)",
      "WebSearch" in config.TOOLS_QUERY and "WebSearch" in config.CLAUDE_TOOLS, True)
check("전역 MCP 대신 동백 전용만",
      "--strict-mcp-config" in norm_argv, True)

# 조회 등급에 셸이 없다는 게 지금 유일하게 남은 '도구로 그은 경계' 다.
# 되돌릴 수 없는 일(배포·푸시·DB·광고 집행)은 거의 전부 셸을 거치는데,
# 실사용의 61%가 조회 등급으로 오므로 사고 표면이 그만큼 줄어든다.
# Bash 정의만 4,040 토큰이라 비용도 같이 준다 (실측 24,765 → 20,725).
check("조회 등급에는 셸이 없다", "Bash" in config.TOOLS_QUERY, False)
check("개발·전권에는 셸이 있다",
      "Bash" in between(dev_argv, "--tools")
      and "Bash" in between(elev_argv, "--tools"), True)
# 쓰기는 남긴다 — 사장님 지시로 모든 등급에서 수정이 되어야 한다
check("조회 등급도 파일 수정은 된다",
      {"Write", "Edit"} <= set(config.TOOLS_QUERY), True)

print("\n[8c] 세션 수명 — 캐시가 만료될 만큼 쉬면 새로 시작한다")
import time as _time  # noqa: E402
# 오래 쉰 뒤 옛 세션에 이어붙이면 커진 문맥을 통째로 다시 올린다.
# 오늘 그렇게 한 번에 $1 넘게 나갔다. 프롬프트 캐시 수명이 1시간이라
# 그보다 오래 쉬었으면 어차피 다시 올릴 판이고, 그럴 바엔 25k 가 낫다.
check("세션 수명이 캐시 수명(1시간) 이하", config.SESSION_MAX_IDLE_SEC <= 3600, True)

_now = _time.time()
bridge._read_store = lambda: {  # type: ignore[assignment]
    config.MODEL_CHAT: {"session_id": "fresh", "updated_at": _now - 60},
    config.MODEL_DEV: {"session_id": "stale",
                       "updated_at": _now - config.SESSION_MAX_IDLE_SEC - 60},
}
check("최근 세션은 이어받는다", bridge._load_session(config.MODEL_CHAT), "fresh")
check("오래 쉰 세션은 버린다", bridge._load_session(config.MODEL_DEV), None)

print("\n[9] 모델 배정 — 대화는 소넷, 개발은 오퍼스")
check("조회는 소넷", between(norm_argv, "--model"), [config.MODEL_CHAT])
check("개발은 오퍼스", between(dev_argv, "--model"), [config.MODEL_DEV])
check("전권은 오퍼스", between(elev_argv, "--model"), [config.MODEL_DEV])

print("\n[10] 세션은 모델별로 따로 — 번갈아 써도 캐시가 안 깨진다")
# 모델이 바뀌면 프롬프트 캐시가 무효화된다. 모델마다 자기 세션을 들고 있어야
# 각자 캐시가 따뜻하게 유지된다. 두 등급이 같은 모델이면 세션도 하나를
# 공유하는 게 맞다 (같은 캐시니까).
import time as _time

bridge._read_store = lambda: {  # type: ignore[assignment]
    m: {"session_id": f"{m}-sid", "updated_at": _time.time()}
    for m in {config.MODEL_CHAT, config.MODEL_DEV}
}
check("조회는 자기 모델의 세션을 쓴다",
      between(argv_for(), "--resume"), [f"{config.MODEL_CHAT}-sid"])
check("개발은 자기 모델의 세션을 쓴다",
      between(argv_for(dev=True), "--resume"), [f"{config.MODEL_DEV}-sid"])

# 모델이 서로 다를 때 정말로 갈리는지 (지금은 둘 다 소넷이라 강제로 갈라 본다)
_saved = config.MODEL_DEV
config.MODEL_DEV = "claude-opus-5"
config.TOOL_POLICY["dev"]["model"] = config.MODEL_DEV
bridge._read_store = lambda: {  # type: ignore[assignment]
    config.MODEL_CHAT: {"session_id": "sonnet-sid", "updated_at": _time.time()},
    config.MODEL_DEV: {"session_id": "opus-sid", "updated_at": _time.time() - 10},
}
check("모델이 다르면 세션도 갈린다",
      (between(argv_for(), "--resume"), between(argv_for(dev=True), "--resume")),
      (["sonnet-sid"], ["opus-sid"]))
check("가장 최근 세션을 찾아준다", bridge.latest_session(), "sonnet-sid")
config.MODEL_DEV = _saved
config.TOOL_POLICY["dev"]["model"] = _saved

print("\n[11] 자기 코드 수정 → 재시작, 단 문법이 멀쩡할 때만")
import shutil
import tempfile

dongbaek._restart_pending = False
check("정상 코드면 문법 통과", dongbaek._self_syntax_error(), None)

msg = dongbaek._arm_self_restart()
check("동백 코드가 바뀌면 재시작 예약", dongbaek._restart_pending, True)
check("  → 답변에 알림", "다시 시작" in msg, True)
dongbaek._restart_pending = False

# 재시작 판단은 '동백 자신이 바뀌었나' 로만 한다. 도구를 열면서 조회
# 등급으로도 코드를 고칠 수 있게 됐는데, 등급 이름으로 판단하면 그 경로가
# 사각지대가 된다 (텔레그램이 옛 config 로 죽은 원인).
for tier, pol in config.TOOL_POLICY.items():
    blocked = set(pol.get("disallowed") or ())
    can_write = not {"Write", "Edit"} <= blocked
    check(f"{tier}: 쓸 수 있으면 스냅샷·재시작 대상", can_write, True)

# 깨진 코드로 재시작하면 크래시 루프에 빠지고, 그러면 "되돌려" 라고 말할
# 상대조차 없어진다. 이 게이트는 한 번 조용히 무력화된 적이 있다
# (py_compile 이 cfile 을 못 써서 던진 OSError 를 except 가 삼켰다).
_scratch = config.ROOT / "_syntax_probe.py"
try:
    _scratch.write_text("this is not valid python !!!\n", encoding="utf-8")
    check("깨진 .py 를 잡아낸다", dongbaek._self_syntax_error(), _scratch.name)
    msg = dongbaek._arm_self_restart()
    check("  → 재시작을 막는다", dongbaek._restart_pending, False)
    check("  → 이유를 말한다", "문법 오류" in msg, True)
finally:
    _scratch.unlink(missing_ok=True)
check("정리 후 다시 정상", dongbaek._self_syntax_error(), None)
dongbaek._restart_pending = False

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    raise SystemExit(1)
print("✅ 전부 통과 — 등급별 도구·권한·모델이 정책 표와 일치")
