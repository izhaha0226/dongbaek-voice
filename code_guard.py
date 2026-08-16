"""음성 코드 수정 안전망 — 수정 전 git 스냅샷.

음성 코딩의 진짜 위험은 오인식이 아니라 '되돌릴 수 없음' 이다.
화면으로 코딩하면 diff 를 보고 ⌘Z 를 누르지만, 음성은 둘 다 없다.
잘못 고쳐진 걸 나중에 발견해도 뭐가 바뀌었는지 모른다.

그래서 수정 직전에 현재 상태를 git 에 박아둔다. 무슨 일이 나도
`동백아, 방금 수정 되돌려` 한 마디로 복구된다.

git 저장소가 아니면 수정을 막는다. 안전망 없이 음성으로 코드를 고치는 건
사고가 나기를 기다리는 것과 같다.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import config

TAG_PREFIX = "dongbaek-before"


def _git(repo: str, *args, timeout: int = 30):
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def find_repo(path: str | Path) -> str | None:
    """해당 경로가 속한 git 저장소 루트. 아니면 None."""
    p = Path(path).expanduser()
    if not p.exists():
        return None
    if p.is_file():
        p = p.parent
    r = subprocess.run(
        ["git", "-C", str(p), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, timeout=20,
    )
    return r.stdout.strip() if r.returncode == 0 else None


SNAP_FILE = config.STATE / "last_snapshot.json"


def _save_snap(info: dict) -> None:
    """스냅샷 정보를 파일에 남긴다.

    되돌리기가 다른 프로세스에서 실행될 수 있어서(데몬 재시작, 텔레그램 등)
    메모리에만 두면 못 찾는다.
    """
    try:
        SNAP_FILE.write_text(json.dumps(info, ensure_ascii=False))
    except OSError:
        pass


def _load_snap() -> dict | None:
    try:
        return json.loads(SNAP_FILE.read_text())
    except (OSError, ValueError):
        return None


def tree_fingerprint(repo: str) -> str:
    """작업 트리 상태의 지문. 스냅샷 전후를 비교해 '동백이 실제로 바꿨는가'를 판정한다.

    diff_summary 는 작업 트리 전체를 HEAD 와 비교하므로, 사장님이 원래 수정 중이던
    파일까지 '고쳤습니다' 로 잘못 읽을 수 있다. 지문이 그대로면 아무 말도 하지 않는다.
    status --porcelain 이 untracked 생성·삭제까지 잡아준다.
    """
    import hashlib

    st = _git(repo, "status", "--porcelain").stdout
    df = _git(repo, "diff").stdout
    return hashlib.sha1((st + df).encode("utf-8", "replace")).hexdigest()


def snapshot(repo: str, note: str = "") -> dict:
    """수정 직전 상태를 stash 로 박아둔다.

    commit 이 아니라 stash 를 쓰는 이유:
      - 사장님의 커밋 히스토리를 동백이 어지럽히지 않는다
      - `git stash apply` 한 줄로 복구된다
      - 작업 트리는 그대로 두므로(--keep-index 대신 create/store) 수정이 이어진다
    """
    # ⚠ 테스트가 '진짜 동백 저장소' 에 스냅샷을 남기면 안 된다. 하루 만에
    #   154개가 쌓였다 — 되돌리기 목록이 가짜로 뒤덮이면 정작 필요할 때
    #   못 찾는다. 임시 저장소에서 도는 테스트(test_code_guard)는 그대로
    #   둔다. 그건 스냅샷 기능 자체를 검사하는 것이고 무해하다.
    if os.path.basename(sys.argv[0] or "").startswith("test_"):
        try:
            import config as _cfg

            if Path(repo).resolve() == Path(_cfg.ROOT).resolve():
                return {"repo": repo, "label": "(테스트)", "fingerprint": ""}
        except Exception:
            pass
    st = _git(repo, "status", "--porcelain")
    dirty = bool(st.stdout.strip())

    head = _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    label = f"{TAG_PREFIX} {stamp}" + (f" — {note[:60]}" if note else "")

    fp = tree_fingerprint(repo)

    if not dirty:
        # 깨끗한 상태였다면 stash 할 게 없다.
        # 이때 되돌리기는 'HEAD 로 되돌리기'가 된다 — 지금 생긴 변경은
        # 전부 동백이 만든 것이므로 버려도 안전하다.
        info = {"repo": repo, "head": head, "stash": None, "dirty": False,
                "label": label, "fingerprint": fp}
        _save_snap(info)
        return info

    # create + store: 작업 트리를 건드리지 않고 스냅샷만 만든다
    created = _git(repo, "stash", "create")
    sha = created.stdout.strip()
    info = {"repo": repo, "head": head, "stash": sha or None,
            "dirty": True, "label": label, "fingerprint": fp}
    if sha:
        _git(repo, "stash", "store", "-m", label, sha)
    _save_snap(info)
    return info


def diff_summary(repo: str, since_head: str | None = None) -> str:
    """수정 후 무엇이 바뀌었는지 한 문장으로. 음성으로 읽을 용도."""
    st = _git(repo, "diff", "--stat")
    body = st.stdout.strip()
    if not body:
        return "변경된 파일이 없습니다."
    lines = [l for l in body.splitlines() if "|" in l]
    if not lines:
        return body.splitlines()[-1][:120]

    names = []
    for l in lines[:3]:
        f = l.split("|")[0].strip()
        names.append(Path(f).name)
    tail = f" 외 {len(lines) - 3}개" if len(lines) > 3 else ""
    last = body.splitlines()[-1].strip()
    return f"{', '.join(names)}{tail} 를 고쳤습니다. {last}"


def restore(repo: str | None = None) -> str:
    """가장 최근 동백 스냅샷으로 되돌린다.

    두 경우를 모두 처리해야 한다:
      ① 스냅샷 시점에 이미 수정 중이었음 → stash 를 되살린다
      ② 스냅샷 시점에 깨끗했음          → HEAD 로 되돌린다
    ②를 빠뜨려서 "되돌릴 스냅샷이 없습니다" 만 뱉고 수정이 남아 있던 적이 있다.
    """
    snap = _load_snap()
    if snap and not repo:
        repo = snap.get("repo")
    if not repo:
        return "되돌릴 대상을 찾지 못했습니다."

    # ① 저장된 stash 가 있으면 그걸 되살린다
    lst = _git(repo, "stash", "list")
    target = None
    for line in lst.stdout.splitlines():
        if TAG_PREFIX in line:
            target = line.split(":")[0]
            break

    if target is not None:
        _git(repo, "checkout", "--", ".")
        r = _git(repo, "stash", "apply", target)
        if r.returncode != 0:
            return f"되돌리기에 실패했습니다: {(r.stderr or '')[:100]}"
        _git(repo, "stash", "drop", target)
        SNAP_FILE.unlink(missing_ok=True)
        return "방금 수정 전 상태로 되돌렸습니다."

    # ② stash 가 없다 = 스냅샷 때 깨끗했다는 뜻.
    #    지금 남은 변경은 전부 동백이 만든 것이므로 HEAD 로 되돌린다.
    if snap and snap.get("dirty") is False:
        st = _git(repo, "status", "--porcelain")
        if not st.stdout.strip():
            return "되돌릴 변경사항이 없습니다."
        _git(repo, "checkout", "--", ".")
        _git(repo, "clean", "-fd")          # 동백이 새로 만든 파일도 치운다
        SNAP_FILE.unlink(missing_ok=True)
        return "방금 수정 전 상태로 되돌렸습니다."

    return "되돌릴 동백 스냅샷이 없습니다."


def guard(target_path: str, note: str = "") -> tuple[bool, str, dict | None]:
    """수정해도 되는지 판단하고, 되면 스냅샷을 남긴다.

    반환: (허용?, 음성으로 읽을 메시지, 스냅샷정보)
    """
    repo = find_repo(target_path)
    if repo is None:
        return (False,
                "그 위치는 깃 저장소가 아니라서 음성으로 고칠 수 없습니다. "
                "되돌릴 방법이 없기 때문입니다.", None)

    if config.CODE_ALLOWED_REPOS:
        allowed = any(Path(repo).match(p) or str(repo).startswith(str(Path(p).expanduser()))
                      for p in config.CODE_ALLOWED_REPOS)
        if not allowed:
            return (False,
                    f"{Path(repo).name} 는 음성 수정이 허용된 저장소가 아닙니다.", None)

    snap = snapshot(repo, note)
    return (True, "", snap)
