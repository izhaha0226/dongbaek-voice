#!/usr/bin/env python3
"""야간 자가 정비 — 동백이 스스로 자기 코드를 고친다 (매일 04:30, launchd).

사장님 지시(2026-08-11): "동백 에이전트는 계속 스스로 클로드 코드가
개선·개발·코드 수정할 수 있도록 해줘."

원칙 — 자율은 감사(監査) 위에서만:
  1. 증거가 있을 때만 부른다. 새 오류 로그·실패 기록·IMPROVE.md 백로그가
     전부 비어 있으면 클로드를 부르지 않는다 ($0로 끝).
  2. 시작 전 작업 트리가 깨끗해야 한다 — 진행 중인 사람 작업을 밟지 않는다.
  3. 한 번에 하나, 작은 diff. 러너가 diff 줄 수를 재고 상한을 넘으면 되돌린다.
  4. 테스트는 러너가 '독립적으로' 전부 다시 돌린다 — 클로드의 "통과했습니다"
     를 믿지 않는다. 하나라도 깨지면 git 롤백.
  5. 안전 완화는 테스트가 막는다 — 화자 문턱·게이트 회귀 테스트가 이미
     박혀 있어(예: test_voiceprint 의 0.45 하한) 완화하는 순간 4번에서 걸린다.
  6. 결과는 텔레그램 [자가개선] 보고 + git 커밋. 끄기: SELF_IMPROVE_ENABLED=False.

지금 한 번(증거·프롬프트만 보기): .venv/bin/python self_improve.py --dry-run
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import config
import dbstore

ROOT = Path(config.CLAUDE_WORKDIR)
IMPROVE_MD = ROOT / "IMPROVE.md"
STAMP_FILE = config.STATE / "self_improve.json"
# 밤의 결과 노트 — 아침 브리핑(briefing.py)이 이걸 사장님께 읽어드린다.
NOTE_FILE = config.STATE / "self_improve_note.json"

# 오류 로그에서 걸러낼 무해한 소음 — 모델 로딩 진행바 따위.
_NOISE = ("Fetching", "resource_tracker", "warnings.warn", "it/s]", "%|")


# ─────────────────────────────────────────────────────────
# 증거 수집 — 전부 로컬
# ─────────────────────────────────────────────────────────
def _load_stamps() -> dict:
    try:
        return json.loads(STAMP_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save_stamps(stamps: dict) -> None:
    try:
        STAMP_FILE.write_text(json.dumps(stamps, ensure_ascii=False))
    except OSError:
        pass


def _tail_new(path: Path, stamps: dict) -> list[str]:
    """지난 실행 이후 새로 쌓인 줄만. 오프셋은 stamps 에 남긴다."""
    key = f"off:{path.name}"
    try:
        size = path.stat().st_size
    except OSError:
        return []
    if key not in stamps:
        # 첫 실행 — 여기서부터가 기준이다. 과거 이력 전체를 증거로 삼으면
        # 옛 버전의 해묵은 Traceback 까지 밤마다 고치려 든다 (실제로 나왔다).
        stamps[key] = size
        return []
    start = int(stamps[key])
    if start > size:                     # 로그가 잘렸다(회전) — 처음부터
        start = 0
    stamps[key] = size
    if size <= start:
        return []
    try:
        with path.open("rb") as f:
            f.seek(start)
            text = f.read().decode("utf-8", "ignore")
    except OSError:
        return []
    return [l for l in text.splitlines()
            if l.strip() and not any(n in l for n in _NOISE)]


def _backlog_items(path: Path) -> list[str]:
    """IMPROVE.md 의 미완(- [ ]) 항목. 들여쓴 설명 줄은 항목에 붙인다."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    items: list[str] = []
    for line in lines:
        if line.lstrip().startswith("- [ ]"):
            items.append(line.strip()[5:].strip())
        elif items and (line.startswith("      ") or line.startswith("\t")):
            items[-1] += " " + line.strip()
    return items


def _recent_errors(hours: int = 26) -> list[str]:
    """transcript 의 route=error — 실제로 사장님 명령이 실패한 순간들."""
    since = datetime.now().strftime("%Y-%m-%dT00:00")
    out = []
    for r in dbstore.rows(limit=500):
        if r.get("route") == "error" and r.get("ts", "") >= since:
            out.append(f"명령 실패: {r.get('command','')[:50]} — {r.get('error','')[:80]}")
    return out[-5:]


def _observations() -> list[str]:
    """아이디어 모드의 재료 — 최근 7일 사용 데이터. 가설은 관찰에서 나온다."""
    obs: list[str] = []
    since = (datetime.now().date() - timedelta(days=7)).isoformat()
    routes: dict = {}
    repeats: dict = {}
    for r in dbstore.rows(limit=2000):
        if r.get("ts", "") < since:
            continue
        routes[r.get("route")] = routes.get(r.get("route"), 0) + 1
        if r.get("route") == "claude":
            c = (r.get("command") or "").strip()[:40]
            if len(c) >= 6:
                repeats[c] = repeats.get(c, 0) + 1
    if routes:
        obs.append("최근 7일 처리 분포: "
                   + ", ".join(f"{k} {v}건" for k, v in sorted(routes.items())))
    top = [(c, n) for c, n in sorted(repeats.items(), key=lambda x: -x[1]) if n >= 2][:5]
    if top:
        obs.append("클로드로 반복해서 간 명령(로컬화 후보): "
                   + ", ".join(f"{c!r} {n}회" for c, n in top))
    try:
        n_mis = sum(1 for l in (config.STATE / "misheard.jsonl").read_text().splitlines()
                    if l.strip() and json.loads(l).get("ts", "") >= since)
        if n_mis:
            obs.append(f"오인식 기록 {n_mis}건 (state/misheard.jsonl)")
    except (OSError, ValueError):
        pass
    try:
        rej = sum(1 for l in (config.STATE / "voice_scores.jsonl").read_text().splitlines()
                  if l.strip() and json.loads(l).get("name") is None
                  and json.loads(l).get("ts", "") >= since)
        if rej:
            obs.append(f"화자 인증 거부 {rej}건 (state/voice_scores.jsonl — 본인 거부인지 확인 가치)")
    except (OSError, ValueError):
        pass
    # 알아들었나 못 알아들었나 — 가장 직접적인 증거다.
    #
    # 2026-08-12 새벽에 동백이 30분 넘게 사장님 말을 씹었는데, 그 사실이
    # 어떤 증거 항목에도 안 잡혔다. 처리 분포도 오인식 기록도 화자 거부도
    # "씹혔다" 를 말해주지 않는다. 사람이 "쌩까네" 라고 해줘야 알았다.
    # 그래서 그날 이후로는 이걸 먼저 본다.
    try:
        import score

        s = score.summary(days=7)
        if s["attempts"]:
            worst = sorted(
                ((k, n) for k, n in s["kinds"].items() if score.POINTS[k][0] < 0),
                key=lambda kv: score.POINTS[kv[0]][0] * kv[1])
            line = (f"대화 채점 7일: {s['attempts']}번 중 {s['success']}번 성공"
                    f" (성공률 {s['rate']}%, 점수 {s['score']}점)")
            if worst:
                line += " — 실패 유형: " + ", ".join(
                    f"{score.POINTS[k][1]} {n}건" for k, n in worst[:3])
            obs.append(line)
    except Exception:
        pass
    # 층별 시간과 '규칙 구멍' — 어디를 고치면 빨라지는지 숫자로 짚는다.
    #
    # 규칙 구멍이 특히 값지다. 큐웬이 0.6초에 읽어낸 표현을 규칙으로
    # 내리면 0.03초가 된다. 오늘까지는 사람이 손으로 찾아 메웠고,
    # 2026-08-12 하루에만 '변경' 두 글자로 세 곳을 고쳤다.
    try:
        import perf

        ps = perf.summary(days=7)
        if ps["total"]:
            line = "층별 시간 7일: " + ", ".join(
                f"{r} {d['count']}건 중앙 {d['p50']}초"
                + ("(느림)" if d["slow"] else "")
                for r, d in ps["routes"].items())
            obs.append(line)
        gaps = perf.rule_gaps(days=7, min_count=2)
        if gaps:
            obs.append("규칙 구멍(큐웬이 대신 읽음, 규칙으로 내리면 20배 빨라짐): "
                       + ", ".join(f"{g['example'][:30]!r} {g['count']}회"
                                   for g in gaps[:3]))
    except Exception:
        pass
    return obs


HISTORY_FILE = config.STATE / "self_improve_history.jsonl"


def _history_add(mode: str, result: str, files: list[str] | None = None,
                 detail: str = "") -> None:
    """이번 회차가 무엇을 시도했고 어떻게 끝났는지 남긴다.

    ⚠ 이게 없어서 자가정비가 '자가시도' 에 머물렀다. 롤백이 일곱 번 있었는데
      일곱 번 다 처음처럼 시작했다 — 어젯밤 "이 방법은 테스트에서 깨졌다" 를
      오늘 밤 모른다. 시행착오는 있는데 착오가 쌓이지 않았다.
      (동백은 다른 데서는 다 배운다 — 오인식 교정표, 화자 지문 적응,
       대화 채점. 정작 자기 코드를 고치는 층만 기억이 없었다.)
    """
    if os.path.basename(sys.argv[0] or "").startswith("test_"):
        return
    try:
        rec = {"ts": datetime.now().isoformat(timespec="seconds"),
               "mode": mode, "result": result,
               "files": (files or [])[:6], "detail": detail[:200]}
        with HISTORY_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _history_lines(limit: int = 12) -> list[str]:
    """지난 시도들을 프롬프트에 넣을 꼴로. 최신이 뒤로 간다."""
    try:
        raw = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in raw[-limit:]:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        when = str(r.get("ts", ""))[5:10]
        files = ", ".join(r.get("files") or []) or "-"
        detail = f" — {r['detail']}" if r.get("detail") else ""
        out.append(f"{when} [{r.get('result','?')}] {files}{detail}")
    return out


def _write_note(note: dict) -> None:
    """결과 노트. 아침 브리핑이 이걸 읽어드리므로 진짜 실행일 때만 쓴다.

    ⚠ _report 에는 이 가드가 있는데 여기엔 없었다. 그래서 테스트를 돌리면
      "검증에 실패해 되돌렸습니다" 라는 **일어나지 않은 일**이 노트에 박히고,
      그날 남은 브리핑이 그걸 사장님께 읽어드린다 (2026-08-13 22:03 실사례 —
      마침 그날 브리핑이 다 끝난 뒤라 실피해는 없었다).
      거짓말 금지는 답변만이 아니라 데몬이 남기는 기록에도 걸린다.
    """
    if os.path.basename(sys.argv[0] or "").startswith("test_"):
        return
    note["date"] = datetime.now().date().isoformat()
    try:
        NOTE_FILE.write_text(json.dumps(note, ensure_ascii=False, indent=1))
    except OSError:
        pass


def collect_evidence(stamps: dict) -> list[str]:
    ev: list[str] = []
    for name in ("daemon.err.log", "telegram.err.log", "briefing.err.log",
                 "mail_digest.err.log"):
        new = _tail_new(config.STATE / name, stamps)
        if new:
            ev.append(f"[{name} 새 오류 {len(new)}줄]\n" + "\n".join(new[-12:]))
    errs = _recent_errors()
    if errs:
        ev.append("[명령 실패 기록]\n" + "\n".join(errs))
    backlog = _backlog_items(IMPROVE_MD)
    if backlog:
        ev.append("[IMPROVE.md 백로그]\n" + "\n".join(f"- {b}" for b in backlog[:5]))
    return ev


# ─────────────────────────────────────────────────────────
# 실행과 검증
# ─────────────────────────────────────────────────────────
_RULES = """규칙:
- 한 번에 하나. 작은 diff. 새 의존성 금지. 사장님 개인 설정 값 변경 금지.
- 안전(위험 게이트·화자 인증·권한·전화 모드)은 강화만 허용, 완화 금지.
- 코드 스타일은 주변과 같게 — 이 저장소는 '왜'를 적는 한국어 주석을 쓴다.
- 고친 뒤 관련 테스트를 돌려 확인하고, 필요하면 회귀 테스트를 보태라.
- IMPROVE.md 항목을 끝냈으면 - [x] 로 바꾸고 날짜를 적어라.
- git 커밋 한 번 ([자가개선] 접두, 왜 고쳤는지 본문에). push 는 하지 마라.
- 데몬 재시작은 하지 마라 — 러너가 검증 후에 한다.
- 마치기 전에 state/self_improve_note.json 을 써라 — 아침 브리핑이 이걸
  사장님께 읽어드린다:
  {{"mode":"{mode}","hypothesis":"한 문장 가설","scenarios":"비교한 대안 요약",
   "chosen":"고른 안과 이유","result":"무엇이 어떻게 좋아졌나"}}
- 고칠 게 없거나 확신이 없으면 아무것도 바꾸지 말고 "변경 없음:" 으로
  시작하는 한 줄만 답하라. 애매한 개선보다 무변경이 낫다."""

_PROMPT_FIX = """너는 동백(이 폴더의 한국어 음성 비서)의 야간 정비 담당이다.
아래 증거 가운데 가장 가치 있는 것 '하나만' 골라 작게 고쳐라.

""" + _RULES + """

증거:
{evidence}

{history}
"""

# 사장님 지시(2026-08-11): "좋은 아이디어가 있을 때도 가설을 수립해서
# 최적의 시나리오를 적용해 개발하고, 아침 브리핑에서 보고하라."
_PROMPT_IDEA = """너는 동백(이 폴더의 한국어 음성 비서)의 야간 정비 담당이다.
오늘 밤은 고칠 오류가 없다. 아래 '관찰'을 보고, 사장님의 실제 사용을
낫게 할 좋은 아이디어가 있으면 가설을 세워 진행하라:

  1) 가설 — 무엇을 바꾸면 무엇이 얼마나 나아지는가 (관찰 근거 명시)
  2) 시나리오 — 대안 2~3개를 비교하고 최적안을 이유와 함께 고른다
  3) 구현 — 작게. 검증 가능한 것만.

억지 개선 금지 — 확신 있는 아이디어가 없으면 "변경 없음:" 이 정답이다.

""" + _RULES + """

관찰 (최근 7일):
{evidence}

{history}
"""


def run_claude(prompt: str, cwd: Path | None = None) -> tuple[bool, str]:
    """클로드 코드 한 판 — dev 정책과 같은 모양, 세션 없이 새로.

    cwd 를 주면 거기서 돈다. 사람이 작업 중일 때 별도 워크트리에서
    일하기 위한 것이다 (_workspace 참조).
    """
    pol = config.TOOL_POLICY["dev"]
    cmd = [config.CLAUDE_BIN, "-p", prompt,
           "--model", getattr(config, "SELF_IMPROVE_MODEL", None) or pol.get("model") or "",
           "--permission-mode", pol.get("permission_mode", "bypassPermissions"),
           "--output-format", "text"]
    if config.MCP_CONFIG.exists():
        cmd += ["--mcp-config", str(config.MCP_CONFIG), "--strict-mcp-config"]
    try:
        out = subprocess.run(
            cmd, cwd=str(cwd or ROOT), capture_output=True, text=True,
            timeout=getattr(config, "SELF_IMPROVE_TIMEOUT_SEC", 900),
        )
    except subprocess.TimeoutExpired:
        return False, "시간 초과"
    except OSError as e:
        return False, f"실행 실패: {e}"
    if out.returncode != 0:
        return False, (out.stderr or out.stdout or "알 수 없는 오류")[-400:]
    return True, out.stdout.strip()


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args],
                         capture_output=True, text=True, timeout=30)
    return out.stdout.strip()


def _tree_clean(repo: Path) -> bool:
    return _git(repo, "status", "--porcelain") == ""


def _changed_since(repo: Path, base: str) -> list[str]:
    """base 이후 바뀐 파일들 (추적·미추적 모두).

    `git add -A` 대신 이걸 쓴다. 시작할 때 트리가 깨끗했으므로 여기 잡히는
    것은 이번 회차가 만든 것이다 — 사람이 중간에 끼어들지 않았다면.
    그 '끼어듦' 은 _human_busy 가 막는다.
    """
    # ⚠ 고정 위치(line[3:])로 자르면 안 된다. _git 이 출력을 strip 하므로
    #   첫 줄만 앞 공백이 사라져 ' M config.py' → 'M config.py' 가 되고,
    #   3글자를 떼면 'onfig.py' 라는 없는 파일이 나온다. 실제로 그렇게 나왔다.
    #   상태 코드는 1~2글자라 폭이 일정하지 않다 — 패턴으로 잡는다.
    out = _git(repo, "status", "--porcelain")
    files = []
    for line in (out or "").splitlines():
        m = re.match(r"^\s*\S{1,2}\s+(.*)$", line)
        if not m:
            continue
        name = m.group(1).strip().strip('"')
        if " -> " in name:                 # 이름이 바뀐 것은 새 이름만
            name = name.split(" -> ", 1)[1]
        if name:
            files.append(name)
    return files


def _human_busy(repo: Path, within_sec: float) -> str:
    """방금 사람이 만진 파일이 있으면 그 이름. 없으면 빈 문자열.

    ⚠ 트리가 깨끗한지만 보는 것으로는 모자랐다. 사람이 파일을 고치는 동안엔
      '편집과 편집 사이' 에 잠깐씩 깨끗해 보이고, 러너가 하필 그 틈에 들어와
      남의 작업을 자기 커밋에 담아 갔다 — 2026-08-14 하루에 다섯 번, 한 번은
      반쪽 상태로 커밋됐다.
      그래서 상태가 아니라 **시각**을 본다. 방금 손댄 파일이 있으면 비켜간다.
    """
    import time

    now = time.time()
    for p in repo.glob("*.py"):
        try:
            if now - p.stat().st_mtime < within_sec:
                return p.name
        except OSError:
            continue
    for p in (repo / "tests").glob("*.py"):
        try:
            if now - p.stat().st_mtime < within_sec:
                return f"tests/{p.name}"
        except OSError:
            continue
    return ""


def _rebase_if_moved(repo: Path, base: str) -> str:
    """러너가 도는 사이 다른 커밋(사람·다른 러너)이 생겼으면 기준을 지금
    HEAD 로 옮긴다.

    ⚠ 이게 없어서 2026-08-13 낮에 두 번 물렸다: 러너가 도는 사이 사람이
      커밋한 미팅 모드 443줄을 '자기 diff' 로 세어 상한 초과 판정을 내고,
      reset --hard 로 남의 커밋 두 개를 로컬에서 지워버렸다 (원격에 있어
      복구했지만, push 전이었다면 통째로 사라졌다).
    러너 자신의 작업은 이 시점엔 아직 미커밋이므로, 기준을 HEAD 로 옮기면
    '자기 것만' 재고 '자기 것만' 되돌리게 된다.
    """
    head = _git(repo, "rev-parse", "HEAD")
    if head != base:
        _report(f"러너 도중 다른 커밋 감지 ({base[:7]}→{head[:7]}) — "
                f"내 작업만 재고 되돌립니다.")
        return head
    return base


def _changed_lines(repo: Path, base: str) -> int:
    """base 이후의 변경 줄 수 — 커밋됐든 아직이든 전부 센다."""
    base = _rebase_if_moved(repo, base)
    stat = _git(repo, "diff", "--shortstat", base)
    # "3 files changed, 120 insertions(+), 40 deletions(-)" → 120 + 40
    nums = [int(t) for t in stat.replace(",", " ").split() if t.isdigit()]
    return sum(nums[1:]) if len(nums) > 1 else 0


def _rollback(repo: Path, base: str) -> None:
    """base 로 되돌린다 — 추적 파일은 완전히, 새 파일은 지우지 않고 격리한다.

    ⚠ 원래 git clean -fd 였는데, 사람 작업을 지웠다 (2026-08-13 새벽
      실사례: 러너가 도는 사이 사람이 만들던 dbstore.py 가 테스트 실패
      롤백에 쓸려나가 재작성해야 했다). '시작할 때 트리가 깨끗했다' 는
      그때 얘기고, 되돌리는 시점의 새 파일이 클로드 것인지 사람 것인지는
      구분할 방법이 없다 — 그러니 지우지 않는다.

    지우는 대신 .git/improve-quarantine/<시각>/ 으로 옮긴다:
      · 사람 파일이었으면 → 거기서 꺼내면 된다 (삭제는 복구가 안 된다)
      · 클로드 잔해였으면 → 치워졌으니 트리가 깨끗하다
      · 트리가 깨끗해야 다음 밤 러너가 "사람 작업" 오인으로 안 멈춘다
    """
    import shutil
    from datetime import datetime

    # 러너가 도는 사이 생긴 남의 커밋은 절대 되감지 않는다 (위 참조).
    base = _rebase_if_moved(repo, base)
    _git(repo, "reset", "--hard", base)
    # -z: 경로를 따옴표로 안 감싼다 — 한글 파일명이 "\\354..." 로 깨지면
    # 격리를 건너뛰게 된다.
    entries = _git(repo, "status", "--porcelain", "-z").split("\0")
    news = [e[3:] for e in entries if e.startswith("?? ")]
    if not news:
        return
    qdir = repo / ".git" / "improve-quarantine" / datetime.now().strftime("%Y%m%d-%H%M%S")
    qdir.mkdir(parents=True, exist_ok=True)
    moved = []
    for rel in news:
        src = repo / rel
        try:
            dst = qdir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved.append(rel)
        except OSError:
            pass                       # 못 옮긴 건 그냥 둔다 — 지우지는 않는다
    if moved:
        _report(f"롤백 중 새 파일 {len(moved)}개 격리 (사람 것일 수 있음): "
                f"{', '.join(moved[:5])} → {qdir}")


def _workspace_open() -> tuple[Path, str, str]:
    """일할 자리를 스스로 고른다 — (작업경로, 브랜치 또는 "", 사유).

    사장님 지시 (2026-08-15): "트리가 지저분하다고 자가정비가 작동을 안 한다.
    스스로 신규 트리를 만들어서 하든 트리를 정리하고 하든 선제 작업을 스스로
    결정하게 해줘."

    그동안은 트리가 더러우면 그냥 물러났다. 사람이 하루 종일 코드를 만지는
    날에는 자가정비가 통째로 쉬어버린다 — 실측 37회 중 8회가 그 이유였다.

    ⚠ 사람 작업을 치우지 않는다. 스태시도 커밋도 하지 않는다 — 남의 작업에
      손대는 순간 '정비' 가 아니라 사고다. 대신 **자기 자리를 새로 만든다**:
      git worktree 로 HEAD 를 떼어낸 별도 폴더에서 일한다. 사람은 자기
      트리에서 계속 일하고, 둘은 서로를 못 본다.
    """
    if _tree_clean(ROOT):
        return ROOT, "", "트리가 깨끗해 제자리에서"

    branch = f"improve/{datetime.now():%Y%m%d-%H%M}"
    path = ROOT / ".git" / "improve-worktree"
    try:
        if path.exists():                  # 지난 회차가 남긴 것 — 치우고 시작
            _git(ROOT, "worktree", "remove", "--force", str(path))
        _git(ROOT, "worktree", "add", "-b", branch, str(path), "HEAD")
    except Exception:
        return ROOT, "", "워크트리를 못 만듦"
    if not (path / "self_improve.py").exists():
        return ROOT, "", "워크트리가 비어 있음"
    # ⚠ .venv 는 git 이 추적하지 않아 새 체크아웃에 안 따라온다. 그런데 검증은
    #   `.venv/bin/python` 을 그대로 부른다(run_suite, test_import_shadow) —
    #   없으면 워크트리 회차가 통째로 터진다. 2026-08-16 13:30 실측: 검증도
    #   되돌리기도 보고도 못 하고 끝났고, 고친 코드는 워크트리에 남았다.
    #   가상환경은 체크아웃이 아니라 기계에 딸린 것이라 본 트리 것을 이어 붙인다.
    #   (worktree remove 는 심링크만 끊고 본체는 안 건드리는 것을 확인했다.)
    try:
        if not (path / ".venv").exists():
            (path / ".venv").symlink_to(ROOT / ".venv")
    except OSError:
        pass                               # 못 걸어도 run_suite 가 본 트리 것을 쓴다
    return path, branch, f"사람이 작업 중이라 별도 워크트리({branch})에서"


def _workspace_land(branch: str, work: Path) -> tuple[bool, str]:
    """워크트리에서 만든 커밋을 본 트리로 가져온다. (성공, 사유).

    ⚠ --ff-only 다. 사람 작업 위에 병합 커밋을 얹지 않는다 — 충돌이 나면
      가져오지 않고 브랜치를 남긴 채 알린다. 남의 작업과 겹치는 변경을
      밤에 몰래 섞는 것보다, 아침에 사람이 보고 정하는 편이 낫다.
    """
    ahead = _git(ROOT, "rev-list", "--count", f"HEAD..{branch}")
    if not (ahead or "").strip().isdigit() or int(ahead) == 0:
        return False, "워크트리에 새 커밋이 없음"
    out = _git(ROOT, "merge", "--ff-only", branch)
    landed = _git(ROOT, "rev-list", "--count", f"HEAD..{branch}") == "0"
    if not landed:
        return False, f"본 트리와 겹쳐 가져오지 못함 — 브랜치 {branch} 에 남겨둠 ({(out or '')[:60]})"
    try:
        _git(ROOT, "worktree", "remove", "--force", str(work))
        _git(ROOT, "branch", "-d", branch)
    except Exception:
        pass
    return True, ""


def run_suite(repo: Path) -> tuple[bool, str]:
    """전체 테스트 독립 재검증 — 클로드의 말을 믿지 않는다."""
    py = repo / ".venv" / "bin" / "python"
    # ⚠ 별도 워크트리에는 .venv 가 없다. git 이 추적하지 않는 폴더라 새 체크아웃에
    #   안 따라온다 — 그래서 사람이 작업 중인 밤(워크트리 회차)마다 여기서
    #   FileNotFoundError 로 통째로 터졌다. 2026-08-16 13:30 실측: 검증도
    #   되돌리기도 보고도 못 하고 끝났고, 고친 코드는 워크트리에 남았다.
    #   가상환경은 체크아웃이 아니라 기계에 딸린 것이므로 본 트리 것을 빌린다.
    #   테스트는 cwd=repo 로 도니 import 는 그대로 그 워크트리 코드를 본다.
    if not py.exists():
        py = ROOT / ".venv" / "bin" / "python"
    if not py.exists():                   # 가상환경 없이 도는 판(임시 저장소 시험)
        py = Path(sys.executable)
    # 2026-08-13 정리로 테스트가 tests/ 로 옮겨졌다. 옛 위치도 함께 봐서
    # (임시 저장소를 쓰는 자기 시험 등) 어느 쪽이든 다 돈다.
    #
    # ⚠ 이 목록에는 test_self_improve.py 도 들어 있다. 그 테스트는 main() 을
    #   진짜로 부르므로, 표식이 없으면 여기서 다시 run_suite 가 돌아 재귀한다
    #   (180초 타임아웃에 걸려 "시간 초과" 로 끝난다 — 2026-08-13 실측 229초).
    #   지금까지 안 터진 건 실제 야간 실행에서는 클로드가 파일을 고친 뒤라
    #   작업 트리가 더러워 안쪽 main() 이 조기 반환했기 때문이다. 우연이었다.
    #   목록에서 빼면 자가 정비의 안전장치(롤백·diff 상한) 검증이 통째로
    #   빠지므로, 빼지 않고 표식으로 막는다.
    env = {**os.environ, "DONGBAEK_IN_SUITE": "1"}
    for t in sorted(list(repo.glob("tests/test_*.py")) + list(repo.glob("test_*.py"))):
        try:
            r = subprocess.run([str(py), str(t)], capture_output=True,
                               text=True, timeout=180, cwd=str(repo), env=env)
        except subprocess.TimeoutExpired:
            return False, f"{t.name} 시간 초과"
        if r.returncode != 0:
            return False, t.name
    return True, ""


def _report(text: str) -> None:
    """결과 보고. 화면에는 늘 찍고, 폰으로는 진짜 실행일 때만.

    ⚠ 이 가드가 없어서 test_self_improve 를 돌릴 때마다 사장님 폰으로
      "자가개선 건너뜀" 이 갔다 (2026-08-11 저녁, 일곱 번). 테스트는
      _observations() 가 실제 기록을 읽어 아이디어 모드로 진입하고,
      그때 워킹트리가 더러우면 여기까지 온다.
      record()·_mirror_to_telegram 과 같은 가드를 여기에도 둔다.
    """
    print(text)
    if os.path.basename(sys.argv[0] or "").startswith("test_"):
        return
    try:
        import briefing

        briefing._to_telegram("🔧 자가개선", text)
    except Exception:
        pass


def main() -> int:
    if not getattr(config, "SELF_IMPROVE_ENABLED", False):
        print("SELF_IMPROVE_ENABLED=False — 건너뜀")
        return 0

    # run_suite 가 띄운 테스트 안이다. 여기서 또 정비를 돌리면 그 안에서
    # run_suite 가 다시 돌아 재귀한다 (run_suite 의 주석 참고).
    if os.environ.get("DONGBAEK_IN_SUITE"):
        print("스위트 안에서 불렸다 — 재귀 방지로 건너뜀")
        return 0

    stamps = _load_stamps()
    evidence = collect_evidence(stamps)
    dry = "--dry-run" in sys.argv

    # 받아쓰기 교정표를 먼저 늘린다. 클로드를 부르지 않는 순수 로컬 집계라
    # 아래 수리 모드가 쉬는 날에도 귀는 조금씩 좋아진다.
    if not dry:
        try:
            import term_learn
            rules, weak = term_learn.mine()
            if rules:
                n = term_learn.apply(rules, weak)
                if n:
                    print(f"교정표 학습: {n}개 추가 — "
                          + ", ".join(f"{r['wrong']}→{r['right']}" for r in rules[:5]))
        except Exception as e:      # 교정표 때문에 야간 정비가 죽으면 안 된다
            print(f"교정표 학습 건너뜀: {e}")

    # ⚠ 지난 시도를 프롬프트에 함께 넘긴다. 이게 없으면 어젯밤 롤백된 방법을
    #   오늘 밤 또 시도한다 — 실제로 일곱 번 롤백하고 일곱 번 다 잊었다.
    #   롤백을 버리지 않고 지식으로 바꾸는 유일한 자리다.
    _hl = _history_lines()
    history = ("[지난 시도 — 같은 것을 또 하지 마라. 되돌려진 것은 이유가 있다]\n"
               + "\n".join(_hl)) if _hl else ""

    # 증거(오류·백로그)가 있으면 수리 모드, 없으면 관찰 기반 아이디어 모드.
    if evidence:
        mode = "fix"
        prompt = _PROMPT_FIX.format(mode=mode, history=history,
                                    evidence="\n\n".join(evidence))
    else:
        obs = _observations()
        if not obs:
            _save_stamps(stamps)
            print("증거도 관찰도 없음 — 오늘은 쉰다 (클로드 호출 없음)")
            return 0
        mode = "idea"
        prompt = _PROMPT_IDEA.format(mode=mode, history=history,
                                     evidence="\n".join(f"- {o}" for o in obs))

    if dry:
        print(prompt)
        return 0

    # 일할 자리를 스스로 고른다. 트리가 더러우면 물러나는 게 아니라
    # 자기 워크트리를 새로 판다 (사장님 지시 2026-08-15).
    #
    # ⚠ 사람이 방금 만진 파일이 있어도 마찬가지다 — 예전엔 여기서 물러났는데,
    #   그러면 사람이 코드를 만지는 날은 자가정비가 통째로 쉰다 (실측 37회 중
    #   8회가 그 이유). 물러나는 대신 자리를 옮긴다.
    work, branch, why = _workspace_open()
    _report(f"자가 정비 시작 — {why}.")

    base = _git(work, "rev-parse", "HEAD")
    ok, reply = run_claude(prompt, cwd=work)
    _save_stamps(stamps)               # 실패해도 같은 오류로 매일 재도전하진 않는다
    # 이번 회차가 만진 파일 — 결말마다 이력에 함께 남긴다. 어느 파일에서
    # 무엇이 깨졌는지가 다음 회차의 가장 값진 정보다.
    _touched = _changed_since(work, base)

    if not ok:
        _rollback(work, base)
        _write_note({"mode": mode, "ok": False, "changed": False,
                     "result": f"실행 실패({reply[:120]}) — 되돌림"})
        _history_add(mode, "실행실패", _touched, reply[:120])
        _report(f"실패({reply[:120]}) — 되돌렸습니다.")
        return 1

    if reply.startswith("변경 없음"):
        if not _tree_clean(work) or _git(work, "rev-parse", "HEAD") != base:
            _rollback(work, base)      # 말과 행동이 다르면 행동을 지운다
        _write_note({"mode": mode, "ok": True, "changed": False,
                     "result": reply[:200]})
        _history_add(mode, "변경없음", [], reply[:120])
        _report(f"변경 없음. {reply[:200]}")
        return 0

    changed = _changed_lines(work, base)
    if changed > getattr(config, "SELF_IMPROVE_MAX_DIFF", 400):
        _rollback(work, base)
        _write_note({"mode": mode, "ok": False, "changed": False,
                     "result": f"diff {changed}줄 상한 초과 — 되돌림"})
        _history_add(mode, "diff상한초과", _touched, f"{changed}줄")
        _report(f"diff {changed}줄 — 상한 초과라 통째로 되돌렸습니다. "
                "야간 자가 정비는 작게만 고친다.")
        return 1

    # ⚠ 자기 자를 고치는 것은 개선이 아니다. 검증이 테스트뿐인데 그 테스트를
    #   스스로 고치면 검증이 무의미해진다 — 실제로 자가개선 10건 중 2건이
    #   테스트 수정이었고, 둘 다 "시계를 믿던 것" 이었다. 부하가 걸리면
    #   시간 검사가 빨개지고, 그걸 '고칠 거리' 로 보고 자를 손댄 것이다.
    #   사람이 보고 판단할 일이라 되돌리고 알린다.
    touched_tests = [f for f in _changed_since(work, base) if f.startswith("tests/")]
    if touched_tests:
        _rollback(work, base)
        _write_note({"mode": mode, "ok": False, "changed": False,
                     "result": f"테스트를 고치려 해서 되돌림: {', '.join(touched_tests[:3])}"})
        _history_add(mode, "테스트를고치려함", touched_tests)
        _report(f"테스트({', '.join(touched_tests[:3])})를 고치려 해서 되돌렸습니다. "
                "검증 도구는 사람이 봐야 합니다.")
        return 1

    passed, failed_at = run_suite(work)
    if not passed:
        _rollback(work, base)
        _write_note({"mode": mode, "ok": False, "changed": False,
                     "result": f"테스트 실패({failed_at}) — 되돌림"})
        _history_add(mode, "테스트실패", _touched, failed_at)
        _report(f"테스트 실패({failed_at}) — 되돌렸습니다.")
        return 1

    if not _tree_clean(work):          # 커밋을 안 했으면 러너가 마무리한다
        # ⚠ `git add -A` 를 쓰면 안 된다. 사람이 편집 중이던 미커밋 파일까지
        #   통째로 담아, 커밋 메시지와 내용물이 어긋난 커밋이 된다.
        #   2026-08-14 하루에만 다섯 번 그랬다 — 사람의 말투 작업·말끝 판정·
        #   메일 도구가 "[자가개선] 야간 정비" 라는 이름으로 들어갔고,
        #   한 번은 편집 도중에 걸려 **반쪽 상태로** 커밋됐다.
        #   담을 것은 이번 회차가 만진 파일뿐이다.
        mine = _changed_since(work, base)
        if mine:
            _git(work, "add", "--", *mine)
            _git(work, "commit", "-m", "[자가개선] 야간 정비 (러너 마무리 커밋)")
        else:
            _report("바뀐 파일을 특정하지 못해 마무리 커밋을 건너뜁니다.")

    # 통과했다. 무엇이 통했는지도 남긴다 — 실패만 쌓으면 "하지 마라" 만
    # 늘어나고, 통한 방향은 다음 회차가 알 길이 없다.
    _history_add(mode, "성공", _touched, (reply or "")[:120])

    log_line = _git(work, "log", "--oneline", f"{base}..HEAD")

    # 별도 워크트리에서 일했으면 본 트리로 가져온다. 못 가져오면(사람 작업과
    # 겹치면) 브랜치를 남긴 채 알린다 — 밤에 몰래 섞는 것보다 아침에 사람이
    # 보고 정하는 편이 낫다. 데몬은 본 트리에서 도므로, 가져오기 전에는
    # 재시작해봐야 옛 코드다.
    if branch:
        landed, why_land = _workspace_land(branch, work)
        if not landed:
            _write_note({"mode": mode, "ok": True, "changed": False,
                         "result": f"고쳤지만 본 트리로 못 가져옴 — {why_land}"})
            _history_add(mode, "가져오기보류", _touched, why_land)
            _report(f"고쳐서 검증까지 끝냈는데 {why_land}. "
                    "아침에 확인하고 합쳐 주세요.")
            return 0

    subprocess.run(["./restart.sh"], cwd=str(ROOT), capture_output=True,
                   text=True, timeout=120)

    # 클로드가 남긴 가설 노트에 러너의 검증 사실을 얹는다.
    # 노트를 안 남겼어도 브리핑은 나가야 하므로 러너가 최소본을 만든다.
    try:
        note = json.loads(NOTE_FILE.read_text())
    except (OSError, ValueError):
        note = {"mode": mode, "result": reply[:300]}
    note.update({"ok": True, "changed": True, "commit": log_line.splitlines()[0] if log_line else ""})
    _write_note(note)

    _report(f"완료 — 테스트 전부 통과, 재시작됨.\n{log_line}\n\n{reply[:400]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
