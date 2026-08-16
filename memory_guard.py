#!/usr/bin/env python3
"""메모리 지킴이 — 현황을 파악하고, 부족하면 스스로 자리를 만든다.

사장님 지시(2026-08-11): "메모리·프로세스 현황을 파악해서 메모리 관리를
같이할 수 있게" + "부족하면 필요 없는 프로세스는 자동으로 정리".

⚠ '필요 없다고 판단되는 프로세스' 를 코드가 자의로 고르면 언젠가 사장님
작업을 죽인다. 그래서 판단하지 않는다 — 죽여도 안전한 것의 명시 목록만:
  1) ollama 에 상주 중인 모델 내리기 — API 로 부드럽게. 다음 호출 때
     자동으로 다시 올라오므로 잃는 것은 재적재 몇 초뿐이다.
  2) 부모를 잃은(ppid=1) 동백 생태계 고아 — 이미지 생성 잔재(mflux),
     동백 venv 파이썬, 타이머 say. 데몬·텔레그램·자기 자신은 제외.
그 밖의 프로세스는 '보고만' 한다 — 무거운 앱을 알려드리되 죽이는 건
사장님 몫이다. 그게 "같이" 관리하는 것이다.

트리거 셋: 주기 감시(_nudge_loop) · 무거운 작업 직전(웹툰 생성 —
GPU 타임아웃의 직접 원인이 메모리·GPU 경합이었다) · 음성("메모리 정리해줘").
"""

import json
import os
import re
import signal
import subprocess
import urllib.request
from pathlib import Path

import config

# ⚠ 절대 건드리지 않는 것 (사장님 지시: "클로드는 항상 띄워놔야지").
#   클로드 데스크톱 앱은 코드 실행용 리눅스 VM(claudevm.bundle, ~1.7기가)을
#   함께 띄운다. 메모리 상위에 늘 보이지만 이건 상수로 본다 — 정리 대상이
#   아니고, 목록에 넣자는 제안도 하지 않는다.
NEVER_TOUCH = ("Claude.app", "claudevm", "Virtualization", "claude-code")

# 고아일 때 죽여도 되는 것들 — 전부 동백이 스스로 띄운 부류다.
_KILL_PATTERNS = [
    r"/dongbaek/\.venv-image/",          # 이미지 생성 잔재 (mflux)
    r"/dongbaek/\.venv/bin/python",      # 동백 파이썬 고아
    r"/dongbaek/\.venv-mcp/",
    r"^say\b.*지났습니다",                # 타이머 잔재
]
# 고아여도 절대 살려두는 것 — 데몬과 브릿지는 launchd 소속이라 ppid 가 1 이다!
_PROTECT = ("dongbaek.py", "telegram_bridge.py", "self_improve.py",
            "briefing.py", "mail_digest.py", "wakeup.py")

_PAGE = 16384


def _sysctl(key: str) -> str:
    try:
        out = subprocess.run(["sysctl", "-n", key], capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _free_pct() -> float | None:
    """macOS 가 직접 계산해 주는 여유 비율. vm_stat 을 손으로 더하는 것보다 믿을 만하다."""
    try:
        out = subprocess.run(["memory_pressure"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"free percentage:\s*(\d+)", out)
    return float(m.group(1)) if m else None


def snapshot() -> dict:
    """지금 메모리 사정 — 전부 읽기 전용."""
    vm = {}
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True,
                             timeout=5).stdout
        for line in out.splitlines():
            m = re.match(r"Pages ([a-z ]+):\s+(\d+)", line.strip())
            if m:
                vm[m.group(1)] = int(m.group(2))
    except (OSError, subprocess.TimeoutExpired):
        pass
    gb = _PAGE / (1024 ** 3)
    total_gb = int(_sysctl("hw.memsize") or 0) / (1024 ** 3)
    # ⚠ free + speculative 만 세면 안 된다. macOS 는 남는 램을 파일 캐시로
    # 꽉 채워 두고 필요하면 즉시 회수한다 — 그 캐시를 '쓰는 중' 으로 세면
    # 48기가 머신이 영영 '메모리 부족' 으로 보인다. 실제로 시스템 여유가
    # 84% 인 상태에서 "부족" 판정이 나 qwen3:4b 를 내렸다 (사장님 지적:
    # "아니 지금 맥미니가 48기간데 그게 안된다고???").
    pct = _free_pct()
    if pct is not None:
        free_gb = total_gb * pct / 100.0
    else:
        # memory_pressure 가 없을 때의 근사. inactive 는 회수 가능한 캐시다.
        free_gb = (vm.get("free", 0) + vm.get("speculative", 0)
                   + vm.get("inactive", 0)) * gb
    inactive_gb = vm.get("inactive", 0) * gb
    swap_used_gb = 0.0
    m = re.search(r"used = ([\d.]+)M", _sysctl("vm.swapusage"))
    if m:
        swap_used_gb = float(m.group(1)) / 1024
    try:
        pressure = int(_sysctl("kern.memorystatus_vm_pressure_level") or 1)
    except ValueError:
        pressure = 1
    return {"total_gb": total_gb, "free_gb": free_gb,
            "inactive_gb": inactive_gb, "swap_used_gb": swap_used_gb,
            "pressure": pressure}


def _top_procs(n: int = 3) -> list[tuple[float, str]]:
    """무거운 프로세스 상위 — (GB, 이름). 보고용, 절대 죽이지 않는다."""
    try:
        out = subprocess.run(["ps", "-axo", "rss=,comm="], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows = []
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            rows.append((int(parts[0]), parts[1]))
    rows.sort(reverse=True)
    return [(r / 1024 / 1024, Path(c).name if "/" in c else c)
            for r, c in rows[:n]]


def _ollama_loaded() -> list[dict]:
    try:
        with urllib.request.urlopen(f"{config.GATEKEEPER_URL}/api/ps",
                                    timeout=5) as r:
            return json.loads(r.read().decode()).get("models") or []
    except Exception:
        return []


def _unload(model: str) -> bool:
    """모델을 메모리에서 내린다. 생성형이 아니면 embed 쪽으로 한 번 더."""
    for path, payload in (("/api/generate", {"model": model, "keep_alive": 0}),
                          ("/api/embed", {"model": model, "input": "x",
                                          "keep_alive": 0})):
        try:
            req = urllib.request.Request(
                config.GATEKEEPER_URL + path,
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15).read()
            return True
        except Exception:
            continue
    return False


def _ps_lines() -> list[str]:
    try:
        return subprocess.run(["ps", "-axo", "pid=,ppid=,command="],
                              capture_output=True, text=True,
                              timeout=5).stdout.splitlines()
    except (OSError, subprocess.TimeoutExpired):
        return []


def _orphans(lines: list[str] | None = None) -> list[tuple[int, str]]:
    """죽여도 되는 고아 — 허용 패턴 + ppid 1 + 보호 목록 아님 + 내가 아님."""
    out = []
    me = os.getpid()
    for line in (lines if lines is not None else _ps_lines()):
        parts = line.strip().split(None, 2)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        pid, ppid, cmd = int(parts[0]), parts[1], parts[2]
        if ppid != "1" or pid == me:
            continue
        if any(p in cmd for p in _PROTECT) or any(p in cmd for p in NEVER_TOUCH):
            continue
        if any(re.search(pat, cmd) for pat in _KILL_PATTERNS):
            out.append((pid, cmd[:80]))
    return out


def reclaim(reason: str = "") -> list[str]:
    """자리를 만든다. 무엇을 했는지 한국어 조치 목록으로 돌려준다."""
    acts: list[str] = []
    for m in _ollama_loaded():
        name = m.get("name", "")
        size_gb = (m.get("size") or 0) / (1024 ** 3)
        if name and _unload(name):
            acts.append(f"{name} 모델 내림({size_gb:.1f}기가)")
    for pid, cmd in _orphans():
        try:
            os.kill(pid, signal.SIGTERM)
            acts.append(f"고아 프로세스 정리(pid {pid})")
        except OSError:
            pass
    return acts


def ensure_headroom() -> list[str]:
    """무거운 작업(이미지 생성) 직전 — 모델부터 내려 GPU·램을 비운다."""
    return reclaim("사전 확보")


def _svc(name: str) -> dict | None:
    return (getattr(config, "HEAVY_SERVICES", {}) or {}).get(name)


def service_running(name: str) -> bool:
    svc = _svc(name)
    if not svc:
        return False
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 3 and p[2] == svc["label"]:
            return p[0] != "-"
    return False


def service_switch(name: str, on: bool) -> str:
    """무거운 서비스를 켜고 끈다 — 목록(HEAVY_SERVICES)에 있는 것만.

    ⚠ stop 이 아니라 bootout 이다. KeepAlive 가 걸려 있으면 stop 은 곧바로
      되살아나 껐다고 착각하게 된다. 켤 때는 plist 로 bootstrap 한다.
    """
    svc = _svc(name)
    if not svc:
        return f"{name}은 제가 켜고 끌 수 있는 목록에 없습니다."
    uid = os.getuid()
    target = f"gui/{uid}/{svc['label']}"
    try:
        if on:
            subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", svc["plist"]],
                           capture_output=True, timeout=15)
        else:
            subprocess.run(["launchctl", "bootout", target],
                           capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return f"{name} 전환에 실패했습니다."
    import time as _t

    _t.sleep(1.5)
    now = service_running(name)
    if on:
        return (f"{name} 켰습니다." if now
                else f"{name}을 켜지 못했습니다. 설정 파일을 확인해 주세요.")
    if now:
        return f"{name}이 아직 돌고 있습니다. 다시 시도해 보겠습니다."
    s = snapshot()
    return (f"{name} 껐습니다. 메모리 여유는 {s['free_gb']:.1f}기가입니다. "
            "다시 쓰시려면 오픈클로 켜줘라고 말씀해 주세요.")


def needs_reclaim() -> bool:
    """자리를 만들어야 하는가.

    ⚠ 경고(2)만으로는 안 내린다. macOS 는 압축기·스왑이 한 번 붐비면
      여유가 넉넉해진 뒤에도 경고 깃발을 오래 붙들고 있다. 그래서 '지금
      빡빡하다' 가 아니라 '한때 빡빡했다' 를 뜻할 때가 많다.

      실측 2026-08-12 08:02 — 여유 22.1기가(46%), 문턱 4기가, 가장 큰
      프로세스가 0.8기가인데 압박 2 하나로 큐웬(5.1기가)을 내렸다.
      그날 큐웬 처리 건수가 0이었고 잡담이 전부 클로드로 가서 $6.47 이
      나갔다. 사장님 지적: "메모리 보호로 큐웬을 내렸는데 부족 맞아?"

      위험(4)은 다르다. 커널이 곧 프로세스를 죽이겠다는 신호라 그 자체로
      회수한다 — 그때는 늦기 전에 자리를 비우는 쪽이 옳다.
    """
    s = snapshot()
    if s["pressure"] >= 4:
        return True
    return s["free_gb"] < getattr(config, "MEMGUARD_MIN_FREE_GB", 4.0)


def status_speak() -> str:
    """현황 한 문단 — '같이 관리' 의 절반은 정확한 보고다."""
    s = snapshot()
    level = {1: "보통", 2: "경고", 4: "위험"}.get(s["pressure"], "보통")
    line = (f"메모리 {s['total_gb']:.0f}기가 중 여유 {s['free_gb']:.1f}기가, "
            f"스왑 {s['swap_used_gb']:.1f}기가 사용, 압박 단계 {level}입니다.")
    loaded = _ollama_loaded()
    if loaded:
        line += " 상주 모델은 " + ", ".join(
            f"{m['name']}({(m.get('size') or 0)/(1024**3):.1f}기가)"
            for m in loaded) + "."
    tops = _top_procs()
    if tops:
        line += " 무거운 프로세스는 " + ", ".join(
            f"{name} {gb:.1f}기가" for gb, name in tops) + " 순입니다."
    return line
