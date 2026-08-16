#!/usr/bin/env python3
"""층별 시간·정확도 계측 — 동백이 스스로 재고 자라는 고리.

사장님 지시(2026-08-12): "동백 에이전트가 계속 사용하면서 시간체크,
정확도 체크 등을 고려해서 진화해나가도록 로직과 개발을 해줘."

지금까지는 사람이 로그를 뒤져 손으로 쟀다. 규칙이 못 읽는 표현도 사람이
하나씩 찾아 메웠다 — 그날 하루에만 '변경' 두 글자로 세 곳을 고쳤다.

    python test_perf.py
"""
import json
import time

import config
import perf
import router

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


print("\n시간을 재는가")
_real_log, _real_guard = perf.LOG, perf._is_test_process
perf.LOG = config.STATE / "perf_test.jsonl"
perf._is_test_process = lambda: False   # 계측 자체를 검사하려면 잠깐 꺼야 한다
try:
    perf.LOG.unlink(missing_ok=True)
    with perf.track("qwen", "시험 명령", note="일정 명령") as t:
        time.sleep(0.05)
        t.ok = True
    rows = [json.loads(l) for l in perf.LOG.read_text().splitlines() if l.strip()]
    check("한 건이 남았다", len(rows), 1)
    check("층이 남았다", rows[0]["route"], "qwen")
    check("성공 여부가 남았다", rows[0]["ok"], True)
    check("시간이 실제로 쟀다 (0.05초 이상)", rows[0]["sec"] >= 0.05, True)

    print("\n예외로 빠져나가도 시간은 남는가 (느려서 터진 게 제일 중요하다)")
    try:
        with perf.track("claude", "터지는 명령") as t2:
            raise RuntimeError("일부러")
    except RuntimeError:
        pass
    rows = [json.loads(l) for l in perf.LOG.read_text().splitlines() if l.strip()]
    check("실패도 기록된다", len(rows), 2)
    check("실패로 표시된다", rows[1]["ok"], False)

    print("\n요약이 층별로 나오는가")
    s = perf.summary(days=1)
    check("두 층이 잡힌다", sorted(s["routes"].keys()), ["claude", "qwen"])
    check("건수가 맞다", s["total"], 2)

    print("\n규칙 구멍을 모으는가 (큐웬이 대신 읽은 표현)")
    gaps = perf.rule_gaps(days=1, min_count=1)
    check("큐웬이 읽은 건이 후보로 잡힌다", len(gaps) >= 1, True)
    check("실패한 건은 후보가 아니다",
          all("터지는" not in g["example"] for g in gaps), True)

    print("\n음성 보고가 소리로 읽을 만한가")
    line = perf.speak_report(1)
    check("마크다운·기호가 없다", any(c in line for c in "*#`|"), False)
    check("한 문단이다", "\n" in line, False)
finally:
    perf.LOG.unlink(missing_ok=True)
    perf.LOG, perf._is_test_process = _real_log, _real_guard

print("\n음성으로 물어볼 수 있는가")
for said in ["속도 어때", "느려?", "얼마나 걸려", "반응속도 어때"]:
    check(f"{said!r} → 빠르기 질문",
          router._is_perf_query(router.normalize(said)), True)
for said in ["오늘 일정 알려줘", "고마워"]:
    check(f"{said!r} → 빠르기 질문 아님",
          router._is_perf_query(router.normalize(said)), False)
check("승인 없이 답한다", router.is_safe_query("속도 어때"), True)

print("\n⚠ 테스트가 실사용 계측을 오염시키지 않는가")
# 처음 붙였을 때 66건 중 대부분이 테스트분이었다. 테스트는 가짜 브릿지를
# 꽂아 0초에 끝나므로 '클로드 중앙 0.00초' 같은 거짓 숫자가 나왔다.
# 이 파일 자체가 test_ 로 시작하므로, 여기서 record 를 불러도 안 남아야 한다.
_probe = config.STATE / "perf_guard_probe.jsonl"
_keep = perf.LOG
perf.LOG = _probe
try:
    _probe.unlink(missing_ok=True)
    perf.record("claude", 0.001, True, "테스트에서 부른 것")
    check("테스트에서 부르면 아무것도 안 남는다", _probe.exists(), False)
finally:
    _probe.unlink(missing_ok=True)
    perf.LOG = _keep


print("\n야간 자가정비가 이 숫자를 증거로 읽는가")
import self_improve
check("perf 를 증거원으로 쓴다", "perf" in open("self_improve.py").read(), True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 스스로 재고, 그 숫자가 야간 정비의 증거가 됩니다")
