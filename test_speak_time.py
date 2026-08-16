#!/usr/bin/env python3
"""시각 낭독 — "08:34" 를 '공팔 삼사' 로 읽던 것 (사장님 교정 2026-08-13).

원인은 기호 제거가 콜론을 지워 숫자 두 덩이만 남긴 것. 지우기 전에
한국어 시각으로 풀어야 한다.
"""
import speak

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}" + ("" if ok else f"  기대={want!r} 실제={got!r}"))
    if not ok:
        FAIL.append(name)


print("[1] 시각은 시각으로")
for src, want in [
    ("08:34 에 부르셨습니다", "8시 34분 에 부르셨습니다"),
    ("통화 시작 08:30 끝 08:52", "통화 시작 8시 30분 끝 8시 52분"),
    ("14:00 미팅", "14시 미팅"),                    # 정각은 '분' 을 안 붙인다
    ("09:05:07 기록", "9시 5분 7초 기록"),
    ("00:00 자정", "0시 자정"),
]:
    check(f"{src!r}", speak.clean(src), want)

print("[2] 시각이 아닌 콜론은 점수·비율 — '3대 1' (사장님 교정)")
check("점수", speak.clean("점수는 3:1 이었다"), "점수는 3대 1 이었다")
check("비율", speak.clean("비율이 2:1 입니다"), "비율이 2대 1 입니다")
check("범위 밖(25:99)도 비율로", speak.clean("25:99"), "25대 99")
check("이미 한글이면 그대로", speak.clean("오전 10시 30분 미팅"), "오전 10시 30분 미팅")

print("[3] 다른 낭독 규칙과 충돌하지 않는다")
check("소수점 쩜", speak.clean("5.0기가 남음"), "5쩜0기가바이트 남음")
check("단위 풀어읽기", speak.clean("32GB 씁니다"), "32기가바이트 씁니다")

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 전부 통과 — 시각은 시각으로 읽는다")
