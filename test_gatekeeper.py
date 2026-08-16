#!/usr/bin/env python3
"""게이트키퍼 검증 — '분류가 틀려도 안전한 방향으로만 틀리는가'.

ollama 없이 돈다. gatekeeper._generate 를 몽키패치해 분류·즉답을 조작하고,
dongbaek.handle 흐름에서 게이트키퍼가 '언제 나서면 안 되는지'를 본다.
    python test_gatekeeper.py
"""
import json
import sys

import bridge
import code_guard
import config
import dongbaek
import gatekeeper

FAIL = []


def check(name, got, want):
    ok = got == want
    if not ok:
        FAIL.append(name)
    print(f"  {'✓' if ok else '✗'} {name}"
          + ("" if ok else f"  기대={want!r} 실제={got!r}"))


# ⚠ 배포에서는 게이트키퍼가 꺼져 있다 (2026-08-14 — 6.8% 처리하자고
#   옆 통화에 지어낸 답을 내던 것이 계산에 안 맞았다). 그래도 코드는 남는다:
#   다시 켤 수 있어야 하고, 켤 때 이 검사가 살아 있어야 한다.
#   그래서 여기서만 켠다 — 이 파일은 '배포 스위치' 가 아니라 '동작' 을 본다.
config.GATEKEEPER_ENABLED = True

# 실제 클로드·git·소리 없이 흐름만 본다
ASKED = []
bridge.ask = lambda prompt, elevated=False, dev=False, on_text=None: (  # type: ignore[assignment]
    ASKED.append(prompt),
    ("클로드 답변.", {"effective_input": 0, "cache_read": 0, "cache_write": 0,
                  "output": 0, "cost_usd": 0}),
)[1]
code_guard.guard = lambda target, note="": (True, "", {"repo": target, "label": "t", "fingerprint": "f0"})  # type: ignore[assignment]
code_guard.tree_fingerprint = lambda repo: "f0"  # type: ignore[assignment]

GEN = {"calls": [], "verdict": "업무", "chat": "잡담 답변입니다.", "boom": False}


def fake_generate(prompt, *, timeout, num_predict, temperature=0.0, format=None):
    GEN["calls"].append(prompt)
    if GEN["boom"]:
        raise RuntimeError("ollama 죽음")
    if "한 단어만" in prompt:
        return json.dumps({"verdict": GEN["verdict"]}, ensure_ascii=False)
    chat = GEN["chat"]
    if isinstance(chat, list):        # 재시도 검사용 — 순서대로 하나씩
        chat = chat.pop(0) if chat else ""
    return json.dumps({"reply": chat}, ensure_ascii=False)


gatekeeper._generate = fake_generate  # type: ignore[assignment]


def run(cmd, approved=True):
    ASKED.clear()
    GEN["calls"].clear()
    return dongbaek.handle(cmd, confirm=lambda c, h: approved)


print("\n[1] '대화' 판정이면 로컬 즉답 — 클로드 0회")
GEN["verdict"] = "대화"
reply = run("고마워 오늘도 수고했어")
check("즉답을 그대로 반환", reply, "잡담 답변입니다.")
check("클로드를 부르지 않음", ASKED, [])

print("\n[2] '업무' 판정이면 클로드로")
GEN["verdict"] = "업무"
reply = run("레일웨이 로그 확인해줘")
check("클로드 답변 반환", reply, "클로드 답변.")
check("클로드 1회", len(ASKED), 1)

print("\n[3] 게이트키퍼가 죽어도 명령은 멈추지 않는다 (fail-open)")
GEN["boom"] = True
reply = run("레일웨이 로그 확인해줘")
check("클로드로 조용히 넘어감", reply, "클로드 답변.")
GEN["boom"] = False

print("\n[4] ⚠ 위험 명령은 게이트키퍼를 아예 안 거친다")
GEN["verdict"] = "대화"          # 분류가 미쳐도
reply = run("프로덕션 배포해줘", approved=True)
check("게이트키퍼 호출 0회", GEN["calls"], [])
check("승인 후 클로드로", len(ASKED), 1)

print("\n[5] 끄면 예전 그대로 전부 클로드")
config.GATEKEEPER_ENABLED = False
GEN["verdict"] = "대화"
reply = run("고마워 오늘도 수고했어")
check("게이트키퍼 호출 0회", GEN["calls"], [])
check("클로드로 감", len(ASKED), 1)
config.GATEKEEPER_ENABLED = True

print("\n[6] 즉답 모델이 '[업무]' 로 물러나면 클로드로 — 이중 안전망")
GEN["verdict"] = "대화"
GEN["chat"] = "[업무]"
reply = run("다음 분기 매출 어떻게 될 것 같아")
check("클로드 답변 반환", reply, "클로드 답변.")
GEN["chat"] = "잡담 답변입니다."

print("\n[7] 정규식 로컬이 게이트키퍼보다 먼저다 — 시각은 모델도 안 부른다")
reply = run("지금 몇 시야")
check("게이트키퍼 호출 0회", GEN["calls"], [])
check("클로드도 0회", ASKED, [])
check("시각 답변", "분입니다" in reply, True)

print("\n[7b] ⚠ 반말 방지 — 사장님께 반말이 나가면 안 된다 (실사례)")
GEN["verdict"] = "대화"
GEN["chat"] = ["시계 확인할 도구가 없어", "죄송합니다, 제가 시각을 놓쳤습니다."]
reply = run("고마워")
check("반말 → 재시도로 존댓말", reply, "죄송합니다, 제가 시각을 놓쳤습니다.")
GEN["chat"] = ["반말이야", "또 반말이야"]
reply = run("고마워")
check("두 번 다 반말 → 클로드로", reply, "클로드 답변.")
GEN["chat"] = "잡담 답변입니다."

print("\n[7c] 회상 낌새면 클로드 프롬프트에 기억을 붙인다")
import memory_local  # noqa: E402

_real_recall = memory_local.recall
memory_local.recall = lambda q, k=3, min_sim=None: ["[8월 1일] 사장님: 한빛 CPC 낮추자 / 동백: 네"]
config.MEMORY_ENABLED = True
GEN["verdict"] = "업무"
run("지난번에 얘기한 한빛 건 어떻게 됐어", approved=True)
check("기억이 프롬프트 앞에", bool(ASKED) and ASKED[0].startswith("(관련 기억:"), True)
run("레일웨이 로그 확인해줘")
check("회상 아니면 원문 그대로", ASKED[0] if ASKED else "", "레일웨이 로그 확인해줘")
memory_local.recall = _real_recall

print("\n[8] <think> 블록은 걷어낸다")
check("think 제거",
      gatekeeper._THINK.sub("", "<think>추론…</think>네, 알겠습니다."),
      "네, 알겠습니다.")

print("\n[9] 이모지는 소리로 안 읽는다 — 실측에서 실제로 샜다")
check("이모지 제거",
      gatekeeper._EMOJI.sub("", "좋아요 😊 함께해요 🎉").strip(),
      "좋아요  함께해요")

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건")
    sys.exit(1)
print("✅ 전부 통과")


# ── 요약 스키마 ─────────────────────────────────────────
# 2026-08-12: summarize 가 매번 None 을 돌려 브리핑이 목록 낭독으로 나갔다.
# think=False 로 끈 추론이 갈 곳을 잃고 summary 칸으로 밀려들어(실측 1,385자)
# num_predict 에 잘려 JSON 이 깨진 것이었다. '생각' 칸을 앞에 두어 해결했다.
print("\n[요약 스키마] 생각 칸이 앞에 있어야 summary 가 깨끗하다")
import gatekeeper as _g
_props = list(_g._SUM_SCHEMA["properties"].keys())
check("생각 칸이 있다", "생각" in _props, True)
check("생각 칸이 summary 보다 앞이다 (제약 디코딩은 앞부터 채운다)",
      _props.index("생각") < _props.index("summary"), True)
check("summary 에 길이 제한이 있다",
      "maxLength" in _g._SUM_SCHEMA["properties"]["summary"], True)
check("생각 칸도 필수 (안 그러면 건너뛰고 summary 로 샌다)",
      "생각" in _g._SUM_SCHEMA["required"], True)
