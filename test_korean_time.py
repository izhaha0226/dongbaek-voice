#!/usr/bin/env python3
"""한국어 날짜·시각 파서 검증.

일정을 엉뚱한 날짜에 잡으면 사고다. 확신 없으면 None 을 돌려주는지가 핵심.
    python test_korean_time.py
"""
from datetime import datetime

import korean_time as kt

FAIL = []
# 기준: 2026-08-09(일) 오후 2시로 고정해야 결과가 재현된다
NOW = datetime(2026, 8, 9, 14, 0)


def check(label, got, want):
    ok = got == want
    if not ok:
        FAIL.append(f"{label}\n      기대={want}\n      실제={got}")
    print(f"  {'✓' if ok else '✗'} {label}")


def d(*a):
    return datetime(*a)


print("\n[1] 날짜 (기준: 2026-08-09 일요일)")
for text, want in [
    ("오늘 일정", d(2026, 8, 9)),
    ("내일 3시", d(2026, 8, 10)),
    ("모레 회의", d(2026, 8, 11)),
    ("8월 12일", d(2026, 8, 12)),
    ("8/14 미팅", d(2026, 8, 14)),
    ("화요일에", d(2026, 8, 11)),          # 다가오는 화요일
    ("다음주 화요일", d(2026, 8, 18)),
    ("일요일", d(2026, 8, 16)),            # 오늘이 일요일 → 다음 일요일
    ("그냥 미팅 잡아줘", None),            # 날짜 없음 → None
]:
    check(f"{text!r}", kt.parse_date(text, NOW), want)

print("\n[2] 시각")
for text, want in [
    ("오후 3시", (15, 0)),
    ("오전 9시", (9, 0)),
    ("3시", (15, 0)),                      # 업무 맥락 → 오후
    ("오후 2시 30분", (14, 30)),
    ("2시반", (14, 30)),
    ("세시", (15, 0)),
    ("열두시", (12, 0)),
    ("14:30", (14, 30)),
    ("저녁 7시", (19, 0)),
    ("아침 8시", (8, 0)),
    ("오전 12시", (0, 0)),
    ("시간 없는 문장", None),
]:
    check(f"{text!r}", kt.parse_time(text), want)

print("\n[3] 날짜+시각 결합 — 하나라도 없으면 None")
for text, want in [
    ("내일 오후 3시에 미팅", d(2026, 8, 10, 15, 0)),
    ("8월 12일 오전 10시 회의", d(2026, 8, 12, 10, 0)),
    ("다음주 목요일 2시", d(2026, 8, 20, 14, 0)),
    ("내일 미팅", None),                   # 시각 없음
    ("3시에 미팅", None),                  # 날짜 없음
    ("언제 한번 보자", None),
]:
    check(f"{text!r}", kt.parse_datetime(text, NOW), want)

print("\n[4] 제목 추출")
for text, want in [
    ("내일 오후 3시에 배움창작소 미팅 잡아줘", "배움창작소 미팅"),
    ("8월 12일 2시 [강남 한빛건설] 등록해줘", "강남 한빛건설"),
    ('내일 3시 "중간보고회" 일정 추가', "중간보고회"),
    ("내일 오후 4시에 일정 하나 만들어줘", "일정"),
]:
    check(f"{text!r}", kt.extract_title(text), want)

print("\n[5] 소요 시간")
for text, want in [
    ("2시간짜리 회의", 2.0),
    ("내일 3시 미팅", 1.0),                # 기본 1시간
    ("30시간 회의", 12.0),                 # 상한
]:
    check(f"{text!r}", kt.parse_duration_hours(text), want)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    for f in FAIL:
        print("  " + f)
    raise SystemExit(1)
print("✅ 전부 통과")
