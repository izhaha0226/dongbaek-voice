#!/usr/bin/env python3
"""메일 감시(mail_watch) 검증. 실제 메일·데몬 없이 로직만.

  ① 등록 — 키워드로 감시를 만든다, 재등록하면 갈아 끼운다
  ② 확인 — 걸리면 알리고 그 감시만 지운다, 안 걸리면 남겨 둔다
  ③ 만료 — TTL 지난 감시는 확인 없이도 사라진다
    python tests/test_mail_watch.py
"""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # 저장소 루트 임포트

from datetime import datetime, timedelta

import config
import mail_local
import mail_watch as W

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"  {'✓' if ok else '✗'} {name}")
    if not ok:
        print(f"    기대={want}\n    실제={got}")
        FAIL.append(name)


config.STATE.mkdir(parents=True, exist_ok=True)
W.STATE_FILE = config.STATE / "_test_mail_watch.json"
W.STATE_FILE.unlink(missing_ok=True)

NOTIFIED = []
W._notify = lambda text: NOTIFIED.append(text)  # type: ignore[assignment]

print("[1] 등록")
w = W.add("한빛건설", "강남 한빛건설")
check("등록됨", w["keyword"], "한빛건설")
w2 = W.add("한빛건설", "다른 문구")
d = W._load()
check("같은 키워드는 갈아 끼운다 (중복 안 됨)", len(d["watches"]), 1)
check("새 note 로 갱신", d["watches"][0]["note"], "다른 문구")

print("\n[2] 확인 — 걸리면 알리고 지운다")
MSGS = [{"from": "한빛건설 관리사무소", "addr": "office@debianc.kr", "subject": "관리비 안내"}]
mail_local.received_brief = lambda hours=3, max_scan=60: MSGS  # type: ignore[assignment]

NOTIFIED.clear()
result = W.run()
check("알림이 나간다", len(NOTIFIED), 1)
check("알림에 note 가 들어간다", "다른 문구" in NOTIFIED[0], True)
check("결과 문자열에도 반영", "다른 문구" in result, True)
d = W._load()
check("걸린 감시는 지운다", d["watches"], [])

print("\n[3] 안 걸리면 남겨 둔다")
W.add("동백동", "동백동 관리소")
mail_local.received_brief = lambda hours=3, max_scan=60: [  # type: ignore[assignment]
    {"from": "다른곳", "addr": "x@x.com", "subject": "안녕하세요"}
]
NOTIFIED.clear()
result2 = W.run()
check("안 걸리면 알리지 않는다", NOTIFIED, [])
d = W._load()
check("안 걸린 감시는 남는다", len(d["watches"]), 1)
check("결과 문구", result2, "아직 안 왔습니다.")

print("\n[4] 만료 — TTL 지난 감시는 확인 전에 사라진다")
W.STATE_FILE.unlink(missing_ok=True)
old = W.add("옛날키워드", ttl_hours=1)
d = W._load()
d["watches"][0]["added"] = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
W._save(d)
result3 = W.run()
check("만료된 감시는 확인 없이 없다고 답한다", result3, "감시 중인 메일이 없습니다.")
d = W._load()
check("만료된 감시는 지워진다", d["watches"], [])

W.STATE_FILE.unlink(missing_ok=True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    raise SystemExit(1)
print("✅ 전부 통과 — 감시 등록·확인·만료가 어긋나지 않는다")
