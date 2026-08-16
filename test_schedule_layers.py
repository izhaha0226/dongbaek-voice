#!/usr/bin/env python3
"""일정 명령 3층 — 규칙 → 큐웬 → 클로드.

사장님 제안(2026-08-12): "일정 등록하고 수정, 삭제하는건 로컬보다 큐웬이
해야 하지 않을까 싶은데?"

맞는 제안이었다. 규칙(정규식+낱말표)은 새 표현마다 깨진다. 그날 하루에만
'변경' 두 글자 때문에 세 곳을 고쳤다 — "법무사 대표 변경의 건" 은 서류
이름인데 옮기기 명령으로 읽혔다.

다만 시각은 큐웬에게 맡기지 않는다. 여기서 재는 것의 절반이 그것이다.

    python test_schedule_layers.py
"""
import config
import gatekeeper
import router

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


print("\n큐웬이 의도와 제목을 읽는가")
for said, intent, title_has in [
    ("오늘 일정에 법무사 대표 변경의 건으로 어 서류 전달 11시 등록해줘",
     "등록", "법무사"),
    ("본사 미팅 11시 반으로 옮겨줘", "수정", "본사"),
    ("내일 치과 예약 취소해", "삭제", "치과"),
]:
    r = gatekeeper.parse_schedule(said)
    check(f"{said[:20]!r}… → {intent}", (r or {}).get("intent"), intent)
    check(f"    제목에 {title_has!r} 가 있다",
          title_has in (r or {}).get("title", ""), True)

check("일정 명령이 아니면 None", gatekeeper.parse_schedule("오늘 날씨 어때"), None)

print("\n⚠ 큐웬에게 시각을 묻지 않는다 (숫자를 맡기면 창작한다)")
r = gatekeeper.parse_schedule("내일 오후 3시 회의 등록해줘")
check("돌려주는 칸은 의도와 제목뿐", sorted((r or {}).keys()), ["intent", "title"])
check("스키마에 시각 칸이 없다",
      any("시" in k or "time" in k.lower() for k in gatekeeper._SCHED_SCHEMA["properties"]),
      False)
check("제목에 시각이 섞여 들어오지 않는다",
      any(w in (r or {}).get("title", "") for w in ("3시", "오후", "내일")), False)

print("\n층이 순서대로 도는가")
check("큐웬 층이 켜져 있다", config.GATEKEEPER_SCHEDULE, True)
check("규칙이 못 읽으면 큐웬이 받는다",
      router.handle_local("내일 3시 김부장 만나기로 한 거 넣어줘") is not None, True)
# 정리
import calendar_local
calendar_local.delete_matching("김부장 만나기")

print("\n잡담에는 큐웬을 부르지 않는다 (0원이어도 0.6초가 붙는다)")
for said in ["고마워", "지금 몇 시야", "오늘 기분 어때"]:
    check(f"{said!r} → 일정 얘기 아님",
          router._looks_scheduleish(said, router.normalize(said)), False)
for said in ["내일 3시 회의 등록해줘", "치과 예약 취소해"]:
    check(f"{said!r} → 일정 얘기",
          router._looks_scheduleish(said, router.normalize(said)), True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 규칙 → 큐웬 → 클로드, 시각은 코드가 뽑습니다")
