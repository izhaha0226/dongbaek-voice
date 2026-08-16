#!/usr/bin/env python3
"""TTS 파이프라이닝 검증 — 소리는 내지 않고 타이밍만 측정.

핵심 확인: 답변이 길어져도 '첫 소리까지의 시간'이 일정하게 유지되는가.
    python test_tts.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

import time

import sounddevice as sd

import config
import tts

FAIL = []
PLAYED = []
T0 = 0.0

# 실제 재생을 가로챈다. 소리는 안 나고 재생 시간만큼 흉내만 낸다.
_real_sleep = time.sleep


def fake_play(arr, sr, device=None):
    PLAYED.append((time.time() - T0, len(arr) / sr))


def fake_wait():
    if PLAYED:
        _real_sleep(min(PLAYED[-1][1], 0.15))   # 재생을 짧게 압축


# 2026-08-13 스트림 상주화 이후 재생은 sd.play 가 아니라 tts.play_array 다.
# 같은 흉내(시간 기록 + 압축 재생)를 새 경로에 건다.
def fake_play_array(arr, *, stop_event=None, interrupt=None, device=None):
    PLAYED.append((time.time() - T0, len(arr) / config.TTS_SUPERTONIC_SR))
    _real_sleep(min(len(arr) / config.TTS_SUPERTONIC_SR, 0.15))
    return True


tts.play_array = fake_play_array

sd.play = fake_play
sd.wait = fake_wait


def check(label, got, want):
    ok = got == want
    if not ok:
        FAIL.append(f"{label}: 기대={want!r} 실제={got!r}")
    print(f"  {'✓' if ok else '✗'} {label}")


print("\n[1] 문장 분할")
cases = [
    ("네, 배포 끝났습니다.", 1),
    ("오늘 일정은 두 건입니다. 오후 두 시 미팅과 네 시 리뷰입니다.", 2),
    # 앞 두 문장이 짧아 하나로 병합된다 (조각 2개)
    ("확인했습니다. 오류 세 건입니다. 전부 타임아웃이었습니다.", 2),
]
for text, want_n in cases:
    got = tts.split_sentences(text)
    check(f"{text[:20]!r}… → {want_n}조각", len(got), want_n)

print("\n[2] 병합해도 종결부호가 살아남는가 (억양 유지)")
merged = tts.split_sentences("확인했습니다. 오류 세 건입니다. 전부 타임아웃이었습니다.")
check("병합된 조각에 마침표 보존", "확인했습니다." in merged[0], True)
check("원문 글자 수 보존", sum(len(c.replace(" ", "")) for c in merged),
      len("확인했습니다.오류세건입니다.전부타임아웃이었습니다."))

print("\n[2b] 짧은 꼬리는 앞 문장에 붙는가")
chunks = tts.split_sentences("레일웨이 로그를 확인했습니다. 정상.")
check("짧은 '정상.' 이 흡수됨", len(chunks), 1)
check("꼬리 마침표 보존", chunks[0].endswith("정상."), True)

print("\n[3] 출력 장치 선택")
dev = tts.output_device()
info = sd.query_devices()[dev]["name"] if dev is not None else "(기본)"
virtual = any(h in info.lower() for h in config.VIRTUAL_DEVICE_HINTS)
check(f"가상 장치를 피함 → {info!r}", virtual, False)

print("\n[4] 첫 소리까지의 시간 — 답변이 길어져도 일정해야 함")
# 재기 전에 모델을 올려둔다.
#
# 안 하면 첫 측정에 모델 로딩(약 4초)이 섞여 들어가 '길이와 무관하게
# 일정한가' 라는 질문 자체가 성립하지 않는다. 실사용에서는 데몬이 기동할 때
# speak.precache 로 이미 올려두므로, 예열 상태가 오히려 현실에 맞다.
tts.preload()
samples = [
    ("1문장", "배포가 정상적으로 끝났습니다."),
    ("3문장", "확인했습니다. 오류는 세 건이고 전부 타임아웃입니다. 재시도로 해결됐습니다."),
    ("6문장", "매출 데이터를 정리했습니다. 이번 주 전환율은 12퍼센트 올랐습니다. "
              "구매형 키워드 비중이 늘었습니다. 정보성 키워드는 유입만 많습니다. "
              "전환이 거의 없습니다. 예산을 옮기는 걸 권합니다."),
]
rows = []
for label, text in samples:
    PLAYED.clear()
    T0 = time.time()
    pb = tts.speak(text, block=True)
    if pb is None:
        FAIL.append(f"{label}: Supertonic 합성 실패")
        print(f"  ✗ {label}: 합성 실패")
        continue
    total = time.time() - T0
    first = PLAYED[0][0] if PLAYED else float("inf")
    rows.append((label, first, total, len(PLAYED)))
    print(f"  {label:<6} 조각 {len(PLAYED)}개 · 첫 소리 {first:.2f}초 · 전체 {total:.2f}초")
    # 재생 조각 수 = 분할 결과. 조각을 뭉쳐 내보내면 첫 소리가 그만큼 늦는데,
    # 시간으로는 못 잡는다 — 뭉치면 조각당 몫도 같이 커져 비율이 가려진다.
    check(f"{label} 조각 {len(PLAYED)}개 = 분할 결과", len(PLAYED),
          len(tts.split_sentences(text)))

# 예전에는 초 단위로 못을 박았다 — "편차 0.6초 미만", "최악의 첫 소리 1.0초
# 미만". 둘 다 시계에 기댄 단정이라 기계가 바쁘면 파이프라이닝이 멀쩡한데도
# 빨개졌다 (2026-08-14 실측: 단독 0.82~0.86초, 스위트 안 1.09초. 같은 문장을
# 네 번 합성해도 0.66~1.04초로 흔들려, 편차 0.6초는 잡음만으로도 넘는다).
# test_speak_overlap 에서 걷어낸 것과 같은 병이다.
#
# 대신 이 판에서 잰 값끼리 비교한다: 첫 소리는 '조각 하나' 몫이어야지
# '답변 전체' 몫이면 안 된다. 조각당 합성비(전체÷조각수)는 기계가 느려지면
# 첫 소리와 같이 느려지므로 부하가 걸려도 비율은 그대로다.
if len(rows) == len(samples):
    first_times = [r[1] for r in rows]
    print(f"  (참고) 첫 소리 편차 {max(first_times) - min(first_times):.2f}초 ·"
          f" 최악 {max(first_times):.2f}초 — 절대값은 단정하지 않는다")
    for label, first, total, n in rows:
        if n < 2:   # 조각이 하나뿐이면 '첫 소리=전체' 라 볼 게 없다
            continue
        budget = total / n * 1.6 + 0.2
        check(f"{label} 첫 소리 {first:.2f}초 ≤ 조각 하나 몫 {budget:.2f}초",
              first <= budget, True)

print("\n[5] 폴백 — Supertonic 이 죽어도 say 로 살아남는가")
orig = tts._ensure_loaded
tts._ensure_loaded = lambda: False
try:
    check("합성 실패 시 None 반환", tts.speak("테스트", block=True), None)
finally:
    tts._ensure_loaded = orig

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    for f in FAIL:
        print("  " + f)
    raise SystemExit(1)
print("✅ 전부 통과")
