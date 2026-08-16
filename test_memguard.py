#!/usr/bin/env python3
"""메모리 지킴이 검증 — '판단하지 않고 목록대로만 죽이는가'.

실제 kill·ollama 없이 선별 논리만 본다. 데몬을 죽이면 그날로 끝이라,
보호 목록이 지켜지는지가 전부다.
    python test_memguard.py
"""
import os
import sys

import memory_guard as mg

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


print("\n[1] 고아 선별 — 허용 패턴·ppid 1·보호 목록")
me = os.getpid()
lines = [
    f"  101 1 ~/dongbaek/.venv-image/bin/python mflux-generate",
    f"  102 1 ~/dongbaek/.venv/bin/python dongbaek.py --daemon",   # 보호!
    f"  103 1 ~/dongbaek/.venv/bin/python telegram_bridge.py",     # 보호!
    f"  104 500 ~/dongbaek/.venv-image/bin/python mflux",          # 부모 살아있음
    f"  105 1 /Applications/Safari.app/Contents/MacOS/Safari",                 # 목록 밖
    f"  106 1 say -v Yuna 5분 지났습니다",
    f"  {me} 1 ~/dongbaek/.venv/bin/python 나자신",
]
lines.append("  201 1 /Applications/Claude.app/Contents/MacOS/Claude")   # 절대 금지
lines.append("  202 1 .../com.apple.Virtualization.VirtualMachine")        # 클로드 VM
got = mg._orphans(lines)
check("잡히는 건 2개 (mflux 고아, say 잔재)", [p for p, _ in got], [101, 106])
check("⚠ 클로드와 그 VM 은 절대 안 건드린다 (사장님 지시)",
      all(p not in (201, 202) for p, _ in got), True)
check("데몬·텔레그램 보호", all("dongbaek.py" not in c and "telegram" not in c for _, c in got), True)

print("\n[2] 자동 정리 문턱 — 위험(4) 이거나 여유 4기가 미만")
# ⚠ 2026-08-12 정정: 예전엔 '압박 2 이상' 도 정리 사유였다. 그런데 macOS 는
#   압축기가 한 번 붐비면 여유가 넉넉해진 뒤에도 경고를 오래 붙들고 있어서,
#   여유 22기가에 큐웬(5.1기가)을 내리는 일이 실제로 벌어졌다.
#   경고는 여유와 '함께' 볼 때만 사유가 된다.
mg.snapshot = lambda: {"pressure": 1, "free_gb": 10.0, "total_gb": 48,
                       "inactive_gb": 5, "swap_used_gb": 1}
check("여유로우면 가만히", mg.needs_reclaim(), False)
mg.snapshot = lambda: {"pressure": 2, "free_gb": 10.0, "total_gb": 48,
                       "inactive_gb": 5, "swap_used_gb": 1}
check("경고인데 여유가 넉넉하면 가만히", mg.needs_reclaim(), False)
mg.snapshot = lambda: {"pressure": 4, "free_gb": 10.0, "total_gb": 48,
                       "inactive_gb": 5, "swap_used_gb": 1}
check("위험이면 여유와 무관하게 정리", mg.needs_reclaim(), True)
mg.snapshot = lambda: {"pressure": 1, "free_gb": 2.0, "total_gb": 48,
                       "inactive_gb": 5, "swap_used_gb": 30}
check("여유 4기가 미만이면 정리", mg.needs_reclaim(), True)

print("\n[3] 정리 조치 목록 — 모델 내림 + 고아만")
mg._ollama_loaded = lambda: [{"name": "qwen3:4b", "size": 5_500_000_000}]
mg._unload = lambda name: True
mg._orphans = lambda lines=None: [(101, "mflux")]
killed = []
mg.os.kill = lambda pid, sig: killed.append(pid)
acts = mg.reclaim()
check("조치 2건", len(acts), 2)
check("모델 내림 보고", "qwen3:4b" in acts[0] and "5.1기가" in acts[0], True)
check("고아만 kill", killed, [101])

print("\n[4] 음성 라우팅 — 상태는 조회, 정리는 허용목록 실행")
import router  # noqa: E402

for q, status, clean in [
    ("메모리 상태 어때", True, False),
    ("메모리 현황 알려줘", True, False),
    ("메모리 정리해줘", False, True),
    ("메모리 부족한 것 같은데 비워줘", False, True),
    ("램 얼마나 남았어", True, False),
    ("메모리에 남겨줘", False, False),          # 기억 요청과 혼동 금지
]:
    t = router.normalize(q)
    check(f"{q!r} → 상태 {status}", router._is_mem_status(t), status)
    check(f"{q!r} → 정리 {clean}", router._is_mem_clean(t), clean)

print("\n[5] 오픈클로 껐다 켜기 — 목록에 있는 것만")
calls = []
mg.subprocess.run = lambda cmd, **k: (
    calls.append(" ".join(str(c) for c in cmd)),
    type("R", (), {"stdout": "", "returncode": 0})())[1]
mg.service_running = lambda name: False        # 껐다고 가정
out = mg.service_switch("오픈클로", False)
check("bootout 을 쓴다 (stop 은 KeepAlive 에 되살아난다)",
      any("bootout" in c for c in calls), True)
check("껐다고 보고", "껐습니다" in out, True)
check("다시 켜는 법 안내", "켜줘" in out, True)

calls.clear()
mg.service_running = lambda name: True
out = mg.service_switch("오픈클로", True)
check("bootstrap 으로 켠다", any("bootstrap" in c for c in calls), True)
check("켰다고 보고", "켰습니다" in out, True)

calls.clear()
out = mg.service_switch("모르는서비스", False)
check("목록에 없으면 손대지 않는다", calls, [])
check("그렇다고 알린다", "목록에 없습니다" in out, True)

print("\n[6] 음성 라우팅 — 오픈클로")
for q, want in [
    ("오픈클로 꺼줘", "off"), ("오픈클로 종료해", "off"),
    ("오픈클로 켜줘", "on"), ("오픈클로 실행해", "on"),
    ("오픈클로 상태 어때", "status"),
]:
    t = router.normalize(q)
    hit = any(k in t for k in ("오픈클로", "오픈클라우", "오픈크로", "openclaw"))
    off = any(k in t for k in ("꺼", "끄", "종료", "중지", "내려", "정지"))
    on = any(k in t for k in ("켜", "시작", "올려", "실행", "가동"))
    got = "off" if (hit and off) else ("on" if (hit and on) else "status")
    check(f"{q!r} → {want}", got, want)
check("'오픈클로 꺼줘' → 게이트 없음", router.danger_hit("오픈클로 꺼줘"), None)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과 — 목록대로만, 보호부터")


# ── 회수 판정 ───────────────────────────────────────────
# 2026-08-12 08:02: 여유 22.1기가(46%)인데 압박 2 하나로 큐웬(5.1기가)을
# 내렸다. macOS 는 압축기가 한 번 붐비면 경고를 오래 붙들고 있어서,
# 압박 2는 '지금 빡빡하다' 가 아니라 '한때 빡빡했다' 일 때가 많다.
print("\n[회수 판정] 경고만으로 모델을 내리지 않는다")
import memory_guard as _mg

_real = _mg.snapshot
def _fake(total=48.0, free=22.0, pressure=1, swap=1.7):
    return lambda: {"total_gb": total, "free_gb": free, "inactive_gb": 5.0,
                    "swap_used_gb": swap, "pressure": pressure}

try:
    _mg.snapshot = _fake(free=22.0, pressure=2)
    check("여유 22기가 + 경고 → 안 내린다", _mg.needs_reclaim(), False)
    _mg.snapshot = _fake(free=22.0, pressure=4)
    check("여유 22기가 + 위험 → 내린다 (커널이 곧 죽인다는 뜻)",
          _mg.needs_reclaim(), True)
    _mg.snapshot = _fake(free=2.0, pressure=2)
    check("여유 2기가 + 경고 → 내린다", _mg.needs_reclaim(), True)
    _mg.snapshot = _fake(free=2.0, pressure=1)
    check("여유 2기가 + 정상 → 내린다 (문턱 아래면 압박과 무관)",
          _mg.needs_reclaim(), True)
    _mg.snapshot = _fake(free=22.0, pressure=1)
    check("여유 22기가 + 정상 → 안 내린다", _mg.needs_reclaim(), False)
finally:
    _mg.snapshot = _real
