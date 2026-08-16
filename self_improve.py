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
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import config

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
    try:
        for line in config.TRANSCRIPT_LOG.read_text().splitlines()[-500:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("route") == "error" and r.get("ts", "") >= since:
                out.append(f"명령 실패: {r.get('command','')[:50]} — {r.get('error','')[:80]}")
    except OSError:
        pass
    return out[-5:]


def _observations() -> list[str]:
    """아이디어 모드의 재료 — 최근 7일 사용 데이터. 가설은 관찰에서 나온다."""
    obs: list[str] = []
    since = (datetime.now().date() - timedelta(days=7)).isoformat()
    routes: dict = {}
    repeats: dict = {}
    try:
        for line in config.TRANSCRIPT_LOG.read_text().splitlines()[-2000:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("ts", "") < since:
                continue
            routes[r.get("route")] = routes.get(r.get("route"), 0) + 1
            if r.get("route") == "claude":
                c = (r.get("command") or "").strip()[:40]
                if len(c) >= 6:
                    repeats[c] = repeats.get(c, 0) + 1
    except OSError:
        pass
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
"""


def run_claude(prompt: str) -> tuple[bool, str]:
    """클로드 코드 한 판 — dev 정책과 같은 모양, 세션 없이 새로."""
    pol = config.TOOL_POLICY["dev"]
    cmd = [config.CLAUDE_BIN, "-p", prompt,
           "--model", getattr(config, "SELF_IMPROVE_MODEL", None) or pol.get("model") or "",
           "--permission-mode", pol.get("permission_mode", "bypassPermissions"),
           "--output-format", "text"]
    if config.MCP_CONFIG.exists():
        cmd += ["--mcp-config", str(config.MCP_CONFIG), "--strict-mcp-config"]
    try:
        out = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True,
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


def run_suite(repo: Path) -> tuple[bool, str]:
    """전체 테스트 독립 재검증 — 클로드의 말을 믿지 않는다."""
    py = repo / ".venv" / "bin" / "python"
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

    # 증거(오류·백로그)가 있으면 수리 모드, 없으면 관찰 기반 아이디어 모드.
    if evidence:
        mode = "fix"
        prompt = _PROMPT_FIX.format(mode=mode, evidence="\n\n".join(evidence))
    else:
        obs = _observations()
        if not obs:
            _save_stamps(stamps)
            print("증거도 관찰도 없음 — 오늘은 쉰다 (클로드 호출 없음)")
            return 0
        mode = "idea"
        prompt = _PROMPT_IDEA.format(mode=mode, evidence="\n".join(f"- {o}" for o in obs))

    if dry:
        print(prompt)
        return 0

    if not _tree_clean(ROOT):
        _report("작업 트리가 깨끗하지 않아 건너뜀 — 사람 작업을 밟지 않는다.")
        _save_stamps(stamps)
        return 0

    base = _git(ROOT, "rev-parse", "HEAD")
    ok, reply = run_claude(prompt)
    _save_stamps(stamps)               # 실패해도 같은 오류로 매일 재도전하진 않는다

    if not ok:
        _rollback(ROOT, base)
        _write_note({"mode": mode, "ok": False, "changed": False,
                     "result": f"실행 실패({reply[:120]}) — 되돌림"})
        _report(f"실패({reply[:120]}) — 되돌렸습니다.")
        return 1

    if reply.startswith("변경 없음"):
        if not _tree_clean(ROOT) or _git(ROOT, "rev-parse", "HEAD") != base:
            _rollback(ROOT, base)      # 말과 행동이 다르면 행동을 지운다
        _write_note({"mode": mode, "ok": True, "changed": False,
                     "result": reply[:200]})
        _report(f"변경 없음. {reply[:200]}")
        return 0

    changed = _changed_lines(ROOT, base)
    if changed > getattr(config, "SELF_IMPROVE_MAX_DIFF", 400):
        _rollback(ROOT, base)
        _write_note({"mode": mode, "ok": False, "changed": False,
                     "result": f"diff {changed}줄 상한 초과 — 되돌림"})
        _report(f"diff {changed}줄 — 상한 초과라 통째로 되돌렸습니다. "
                "야간 자가 정비는 작게만 고친다.")
        return 1

    passed, failed_at = run_suite(ROOT)
    if not passed:
        _rollback(ROOT, base)
        _write_note({"mode": mode, "ok": False, "changed": False,
                     "result": f"테스트 실패({failed_at}) — 되돌림"})
        _report(f"테스트 실패({failed_at}) — 되돌렸습니다.")
        return 1

    if not _tree_clean(ROOT):          # 커밋을 안 했으면 러너가 마무리한다
        _git(ROOT, "add", "-A")
        _git(ROOT, "commit", "-m", "[자가개선] 야간 정비 (러너 마무리 커밋)")

    log_line = _git(ROOT, "log", "--oneline", f"{base}..HEAD")
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
