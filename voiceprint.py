"""화자 인증 — 등록된 목소리만 명령으로 받는다. 전부 로컬, 0 토큰.

막으려는 것: TV·유튜브·방송 음악·옆 사람이 "동백아 …" 라고 말하는 순간
동백이 그걸 사장님 명령으로 실행하는 사고. 받아쓰기(whisper)는 '무슨 말인가'
만 알지 '누가 말했나'를 모른다. 그 빈칸을 여기서 채운다.

방식: ECAPA-TDNN 화자 임베딩(192차원). 발화를 임베딩으로 바꿔
등록된 지문들과 코사인 유사도를 재고, 문턱을 넘는 화자가 없으면 미등록이다.

정책:
  - 등록된 지문이 하나도 없으면 검증하지 않는다 (열림).
    설치 직후에도 동백이 바로 동작해야 하고, --enroll 하는 순간부터 잠긴다.
  - 목소리는 이름을 붙여 여러 명 등록할 수 있고, 같은 이름으로
    다시 등록하면 지문이 누적된다 (마이크 거리·컨디션이 달라질 때 보강).

등록:  dongbaek.py --enroll [이름]     (기본 이름: 사장님)
확인:  dongbaek.py --voices
"""

from __future__ import annotations

import json
from datetime import datetime

import numpy as np

import config

_clf = None
_load_failed = False
_prints: dict[str, np.ndarray] | None = None    # {이름: (N, 192) 정규화 임베딩}

# 학습 지문은 같은 저장소에 이 꼬리표를 붙여 따로 담는다. 등록 지문(코어)과
# 섞어 두면 회전시킬 때 코어까지 밀려 나가고, 학습만으로 신원이 이동한다.
#
# npz 키는 zip 안에서 파일 이름이 된다 — 제어문자를 쓰면 저장은 되는데
# 다시 읽을 때 이름이 뭉개져 학습분이 매번 사라진다(실측). 눈에 보이는
# 글자만 쓴다.
_ADAPT = "#적응"


# 거절된 목소리를 모아 두는 자리. 이름이 없다 — 누구인지가 아니라
# '등록자가 아닌 쪽' 이라는 사실만 쓴다. 키 규칙은 _ADAPT 와 같다
# (제어문자를 쓰면 npz 에서 이름이 뭉개진다).
_IMPOSTER = "#남"

# 마지막 verify 가 '왜' 거절했는지 — 문턱/동률/남과근접. 새벽 잠금 사고
# (2026-08-13)를 세 시간대 로그로도 층을 못 갈라 진단이 늦었다.
# _speaker_ok 가 로그에 붙인다.
LAST = {"reason": ""}


def _base(key: str) -> str:
    """저장소 키 → 사람 이름. 학습 지문 키에서 꼬리표를 떼어낸다."""
    return key[:-len(_ADAPT)] if key.endswith(_ADAPT) else key


def _people(prints: dict) -> dict:
    """사람 지문만 — 임포스터 저장소는 화자가 아니다."""
    return {k: v for k, v in prints.items() if k != _IMPOSTER}


def available() -> bool:
    return not _load_failed


def _ensure_model() -> bool:
    global _clf, _load_failed
    if _clf is not None:
        return True
    if _load_failed:
        return False
    try:
        from speechbrain.inference.speaker import EncoderClassifier

        _clf = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(config.VOICEPRINT_MODEL_DIR),
            run_opts={"device": "cpu"},
        )
        return True
    except Exception:
        _load_failed = True
        return False


def preload() -> bool:
    """데몬 기동 때 백그라운드로 불러 첫 명령이 모델 로딩을 기다리지 않게 한다."""
    return _ensure_model()


def embed(audio: np.ndarray) -> np.ndarray | None:
    """16kHz 모노 float32 → 정규화된 192차원 임베딩. 실패하면 None."""
    if not _ensure_model():
        return None
    try:
        import torch

        wav = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32))[None, :]
        with torch.no_grad():
            e = _clf.encode_batch(wav).squeeze().cpu().numpy().astype(np.float32)
        n = float(np.linalg.norm(e))
        if n <= 0:
            return None
        return e / n
    except Exception:
        return None


# ─────────────────────────────────────────────────────────
# 지문 저장소
# ─────────────────────────────────────────────────────────
def _load_prints(refresh: bool = False) -> dict[str, np.ndarray]:
    global _prints
    if _prints is not None and not refresh:
        return _prints
    _prints = {}
    try:
        if config.VOICEPRINT_FILE.exists():
            with np.load(config.VOICEPRINT_FILE) as data:
                _prints = {k: data[k] for k in data.files}
    except Exception:
        _prints = {}
    return _prints


def _save_prints(prints: dict[str, np.ndarray]) -> None:
    global _prints
    config.VOICEPRINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(config.VOICEPRINT_FILE, **prints)
    _prints = prints
    _backup_enrolled(prints)


def _backup_enrolled(prints: dict[str, np.ndarray]) -> None:
    """등록 지문은 영구 보관한다 (사장님 지시 2026-08-16).

    등록 지문에는 상한이 없다 — 학습 지문(#적응)과 남 지문(#남)만 넘칠 때
    솎아낸다. 그러나 '지워지지 않는다' 와 '영구히 남는다' 는 다른 말이다.
    파일은 state/ 아래 한 장뿐이고, state/ 는 gitignore 라 버전 관리 밖이다.
    2026-08-13 새벽에 자가개선 롤백의 git clean 이 같은 자리의 dongbaek.db 를
    쓸어간 전례가 있다. 그때 잃은 건 다시 쌓이지만, 목소리는 사장님이 다시
    등록해 주셔야 돌아온다.

    그래서 등록 지문만 따로 뜬다. 학습 지문은 뜨지 않는다 — 그건 다시 배우면
    되고, 섞어 두면 어느 것이 '사람이 등록한 원본' 인지 흐려진다.
    바뀔 때만 쓴다(하루 67번 도는 학습마다 쓰면 디스크만 닳는다).
    """
    try:
        keep = {k: v for k, v in prints.items()
                if not k.endswith(_ADAPT) and k != _IMPOSTER}
        if not keep:
            return                      # 빈 걸로 좋은 백업을 덮지 않는다
        sig = {k: len(v) for k, v in sorted(keep.items())}
        d = config.STATE / "voiceprints-enrolled"
        d.mkdir(parents=True, exist_ok=True)
        stamp = d / "signature.json"
        try:
            if json.loads(stamp.read_text()) == sig:
                return                  # 등록 지문은 그대로다
        except (OSError, ValueError):
            pass
        np.savez(d / "latest.npz", **keep)
        np.savez(d / f"{datetime.now():%Y%m%d}.npz", **keep)   # 날짜별 한 장
        stamp.write_text(json.dumps(sig, ensure_ascii=False))
    except Exception:
        pass                            # 백업 실패로 등록이 막히면 본말전도다


def enrolled() -> dict[str, int]:
    """{이름: 등록 지문 수}. 비어 있으면 검증이 꺼져 있다는 뜻이다.

    학습 지문은 세지 않는다 — '몇 문장 읽어서 등록했나' 와 '그 뒤 몇 번
    배웠나' 는 다른 질문이고, 잠금 여부를 정하는 건 앞의 것이다.
    """
    return {k: len(v) for k, v in _load_prints().items()
            if not k.endswith(_ADAPT) and k != _IMPOSTER}


def learned() -> dict[str, int]:
    """{이름: 학습으로 쌓인 지문 수}."""
    return {k[:-len(_ADAPT)]: len(v) for k, v in _load_prints().items()
            if k.endswith(_ADAPT)}


def strangers() -> int:
    """모아 둔 '남' 지문 수 — 많을수록 헷갈릴 일이 준다."""
    imp = _load_prints().get(_IMPOSTER)
    return 0 if imp is None else len(imp)


def remember_stranger(audio: np.ndarray, score: float) -> bool:
    """거절된 목소리를 '남' 으로 기억한다 — 다음부터 더 확실히 가른다.

    ⚠ 애매하게 거절된 것은 절대 담지 않는다. 사장님이 감기 든 날 0.43 을
      받아 거절됐는데 그게 '남' 으로 박히면 그 뒤로 감기 목소리가 영구히
      막힌다. 실측(voice_scores.jsonl)상 본인 통과는 0.48~0.80, 거절은
      0.28~0.45 로 경계가 좁다 — 확실히 낮은 것만 담는다.
    """
    if not getattr(config, "VOICE_IMPOSTER_ENABLED", False):
        return False
    if score > getattr(config, "VOICE_IMPOSTER_MAX", 0.35):
        return False               # 본인일 수도 있는 구간 — 손대지 않는다
    e = embed(audio)
    if e is None:
        return False
    prints = dict(_load_prints(refresh=True))
    if not _people(prints):
        return False               # 등록자가 없으면 '남' 도 정의되지 않는다
    cur = prints.get(_IMPOSTER)
    stack = e[None, :] if cur is None else np.vstack([cur, e[None, :]])
    cap = max(1, int(getattr(config, "VOICE_IMPOSTER_MAX_N", 40)))
    prints[_IMPOSTER] = stack[-cap:]
    _save_prints(prints)
    return True


def forgive(name: str, audio: np.ndarray) -> bool:
    """'방금 그건 나였어' — 잘못 거절된 발화를 본인 지문으로 올린다.

    동시에 그 발화와 닮은 '남' 지문을 지운다. 오인의 원인이 거기 있기
    때문이다 — 정정만 하고 원인을 두면 내일 또 같은 자리에서 막힌다.
    """
    e = embed(audio)
    if e is None:
        return False
    prints = dict(_load_prints(refresh=True))
    if name not in prints:
        return False
    prints[name] = np.vstack([prints[name], e[None, :]])
    imp = prints.get(_IMPOSTER)
    if imp is not None and len(imp):
        keep = imp[(imp @ e) < getattr(config, "VOICE_FORGIVE_SIM", 0.55)]
        if len(keep):
            prints[_IMPOSTER] = keep
        else:
            del prints[_IMPOSTER]
    _save_prints(prints)
    return True


def enroll_sample(name: str, audio: np.ndarray) -> bool:
    """발화 하나를 이름 밑에 지문으로 추가한다."""
    e = embed(audio)
    if e is None:
        return False
    prints = dict(_load_prints(refresh=True))
    if name in prints:
        prints[name] = np.vstack([prints[name], e[None, :]])
    else:
        prints[name] = e[None, :]
    _save_prints(prints)
    return True


def rename(old: str, new: str) -> bool:
    """지문을 그대로 둔 채 이름만 바꾼다.

    지우고 다시 등록하면 5문장을 또 읽어야 한다. 받아쓰기가 이름을 잘못
    적는 일이 흔해서('김철수'→'홍해장') 고칠 일이 자주 생긴다.
    같은 이름이 이미 있으면 지문을 합친다.
    """
    prints = dict(_load_prints(refresh=True))
    if old not in prints or not new:
        return False
    for suffix in ("", _ADAPT):     # 학습 지문도 함께 따라간다
        src, dst = old + suffix, new + suffix
        if src not in prints:
            continue
        if dst in prints:
            prints[dst] = np.vstack([prints[dst], prints[src]])
        else:
            prints[dst] = prints[src]
        del prints[src]
    _save_prints(prints)
    return True


def forget(name: str) -> bool:
    prints = dict(_load_prints(refresh=True))
    if name not in prints:
        return False
    del prints[name]
    prints.pop(name + _ADAPT, None)
    _save_prints(prints)
    return True


def adapt(name: str, audio: np.ndarray, score: float) -> bool:
    """확실하게 통과한 발화를 학습 지문으로 되먹인다.

    등록 때 읽은 다섯 문장은 그날 그 마이크 거리의 목소리다. 감기·아침
    목소리·새 마이크에서 본인이 막히는 걸 이걸로 줄인다.

    두 가지를 지킨다. 문턱 바로 위의 애매한 통과는 배우지 않는다 —
    남을 조금씩 본인으로 받아들이는 드리프트의 입구다. 그리고 학습 지문은
    개수를 묶어 오래된 것부터 버리되 등록 지문은 건드리지 않는다 —
    학습만으로 신원이 옮겨 가면 안 된다.
    """
    if not getattr(config, "VOICE_ADAPT_ENABLED", False):
        return False
    if not name or name.startswith("("):        # "(검증불가)" 는 사람이 아니다
        return False
    if score < getattr(config, "VOICE_ADAPT_MIN", 0.60):
        return False
    e = embed(audio)
    if e is None:
        return False
    prints = dict(_load_prints(refresh=True))
    if name not in prints:                      # 등록된 적 없는 이름은 안 만든다
        return False
    # 남 지문과도 비슷한 발화는 배우지 않는다. 애매한 것을 먹을수록 내
    # 지문이 남 쪽으로 번지고, 그게 바로 '헷갈리는' 상태다.
    imp = prints.get(_IMPOSTER)
    if imp is not None and len(imp):
        if float(np.max(imp @ e)) >= score - getattr(config, "VOICE_IMPOSTER_MARGIN", 0.08):
            return False
    key = name + _ADAPT
    cur = prints.get(key)
    stack = e[None, :] if cur is None else np.vstack([cur, e[None, :]])
    cap = max(1, int(getattr(config, "VOICE_ADAPT_MAX", 10)))
    prints[key] = _thin(stack, cap)
    _save_prints(prints)
    return True


def _thin(stack: np.ndarray, cap: int) -> np.ndarray:
    """상한을 넘으면 **가장 겹치는** 하나를 버린다 — 오래된 것이 아니라.

    ⚠ 오래된 것부터 버리면 기억이 '최근 몇 시간' 이 된다. 실측 2026-08-16:
      학습 대상(0.60 이상 통과)이 하루 67건인데 상한이 10개였다. 남는 건
      최근 3.5시간치뿐이라, 아침 목소리·먼 목소리·감기 목소리가 그날 오후
      잡담에 밀려 사라졌다. 그러면 같은 조건에서 매번 다시 거절당한다.
      실제로 문턱 근처(0.35~0.45) 거절이 하루 39건에서 85건으로 늘었다.

    지문의 값어치는 개수가 아니라 **조건의 다양성** 이다. verify 가 최대값을
    쓰기 때문에, 서로 닮은 지문 열 개보다 다른 조건 열 개가 훨씬 낫다.
    그래서 넘칠 때는 가장 닮은 짝을 찾아 그중 '나머지와 더 겹치는' 쪽을
    버린다. 남은 자리는 아직 없는 조건이 채운다.
    """
    while len(stack) > cap:
        sim = stack @ stack.T
        np.fill_diagonal(sim, -1.0)
        i, j = np.unravel_index(int(np.argmax(sim)), sim.shape)
        # 둘 중 나머지 전체와 더 겹치는 쪽이 덜 아깝다
        stack = np.delete(stack, i if sim[i].sum() >= sim[j].sum() else j, axis=0)
    return stack


# ─────────────────────────────────────────────────────────
# 판정
# ─────────────────────────────────────────────────────────
def verify(audio: np.ndarray) -> tuple[str | None, float]:
    """(화자 이름, 유사도). 문턱을 못 넘으면 (None, 최고 유사도).

    화자별 점수는 지문들과의 코사인 '최대값' 이다. 평균을 쓰면 마이크
    거리가 달라진 날 전체가 끌려 내려간다. 지문 다섯 개 중 하나라도
    지금 조건과 같으면 본인이 맞다.

    문턱에 더해 1·2위 격차(VOICE_VERIFY_MARGIN)도 본다. 낯선 목소리는
    등록된 지문 여럿에 어중간하게 '고르게' 걸린다 — 누구와도 확실히 닮지
    않았다는 신호고, 그건 남이다. "여자 목소리면 다 김철수" 이 이 빈틈이었다.
    """
    prints = _load_prints()
    if not prints:
        return (None, 0.0)
    e = embed(audio)
    if e is None:
        # 모델이 죽었을 때 전부 잠그면 동백 자체가 벽돌이 된다.
        # 검증 불가는 '통과' 로 두되 available() 로 상태를 알린다.
        return ("(검증불가)", 1.0)

    # 사람 단위로 묶어서 점수를 낸다. 학습 지문을 별개 화자로 세면 본인의
    # 학습 지문이 자기 '2위' 가 되어 격차 검사에 스스로 걸린다.
    scores: dict[str, float] = {}
    for key, embs in _people(prints).items():
        name = _base(key)
        s = float(np.max(embs @ e))
        if s > scores.get(name, -1.0):
            scores[name] = s

    best_name, best, second = None, -1.0, -1.0
    for name, s in scores.items():
        if s > best:
            best_name, best, second = name, s, best
        elif s > second:
            second = s
    # 새벽 원거리 모드면 문턱이 내려간다 (0.45→0.35) — 먼 방·작은 목소리는
    # 지문 유사도가 깎인다. 승인 게이트·도구 권한은 이 완화와 무관하다.
    if best < config.voice_verify_threshold():
        LAST["reason"] = "문턱미달"
        return (None, best)
    if second > -1.0 and best - second < getattr(config, "VOICE_VERIFY_MARGIN", 0.0):
        LAST["reason"] = f"동률({second:.2f})"
        return (None, best)

    # 남들과 견준다. '나와 닮은 정도' 가 '남과 닮은 정도' 보다 확실히 커야
    # 통과다. 절대 문턱은 마이크·감기·거리에 흔들리지만 같은 발화를 같은
    # 조건에서 재는 상대 비교는 흔들리지 않는다 — 학습으로 내 지문이
    # 넓어져도 남 지문은 함께 넓어지지 않으므로, 계속 배우면서도 안 헷갈린다.
    #
    # ⚠ 이 층은 VOICE_IMPOSTER_ENABLED 로 꺼져야 한다 — 전에는 플래그가
    #   '담기' 만 막고 '판정' 은 계속 돌아서, 코호트가 오염되면 끌 방법이
    #   없었다. 실사례 2026-08-13 05:48 — 사장님 새벽 목소리가 코호트에
    #   쌓여 0.55 (절대문턱 0.45 를 한참 넘는 점수) 까지 거절됐다.
    if getattr(config, "VOICE_IMPOSTER_ENABLED", False):
        imp = prints.get(_IMPOSTER)
        if imp is not None and len(imp):
            imp_best = float(np.max(imp @ e))
            if best - imp_best < getattr(config, "VOICE_IMPOSTER_MARGIN", 0.08):
                LAST["reason"] = f"남과근접({imp_best:.2f})"
                return (None, best)
    return (best_name, best)
