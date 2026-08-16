"""문장 임베딩 — ollama 없이, 이 프로세스 안에서.

왜 옮겼나. 장기 기억 회상 하나 때문에 ollama 서버(별도 프로세스, 상주
모델)를 통째로 띄워두고 있었다. 사장님 판단(2026-08-14): "임베딩 하나
때문에 큐웬을 남겨둘 이유가 없다. 올라마가 아예 관여를 안 하게 하자."

multilingual-e5-small 을 ONNX 로 돌린다 (Xenova 배포, int8 113MB).
말끝 판정(turn_end)에서 쓴 방식 그대로다 — onnxruntime 은 이미 있고,
새로 더한 것은 tokenizer.json 을 읽을 `tokenizers` 하나뿐이다.

⚠ e5 계열은 접두어를 요구한다. 찾는 말에는 "query: ", 저장하는 글에는
  "passage: " 를 붙여야 한다. 안 붙이면 조용히 품질만 떨어진다 —
  에러가 안 나서 알아채기 어렵다. embed_query/embed_passage 로 갈라 둔 건
  그래서다. 부르는 쪽이 접두어를 기억하게 두면 언젠가 한 곳이 빠진다.

⚠ 차원이 1024(qwen3-embedding) → 384(e5-small) 로 바뀐다. 이미 쌓인
  벡터는 못 쓴다. tools/reembed_memory.py 로 다시 만든다 — 원문이
  memory.db 에 남아 있어 가능하다.
"""

import threading

import numpy as np

import config

_sess = None
_tok = None
_lock = threading.Lock()
_failed = False

MAX_TOKENS = 512          # e5-small 의 학습 길이. 넘으면 잘라도 뜻은 대개 앞에 있다


def available() -> bool:
    return not _failed and config.EMBED_MODEL.exists()


def _ensure() -> bool:
    global _sess, _tok, _failed
    if _sess is not None:
        return True
    if _failed:
        return False
    with _lock:
        if _sess is not None:
            return True
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            if not (config.EMBED_MODEL.exists() and config.EMBED_TOKENIZER.exists()):
                _failed = True
                return False
            so = ort.SessionOptions()
            so.inter_op_num_threads = 1      # 청취 루프와 CPU 를 다투지 않게
            _sess = ort.InferenceSession(str(config.EMBED_MODEL), sess_options=so,
                                         providers=["CPUExecutionProvider"])
            _tok = Tokenizer.from_file(str(config.EMBED_TOKENIZER))
            return True
        except Exception:
            _failed = True
            return False


def preload() -> bool:
    return _ensure()


def _embed(text: str) -> np.ndarray | None:
    """평균 풀링 + L2 정규화. 실패하면 None — 회상이 죽어도 동백은 돈다."""
    if not _ensure() or not (text or "").strip():
        return None
    try:
        enc = _tok.encode(text)
        ids = enc.ids[:MAX_TOKENS]
        mask = [1] * len(ids)
        a = np.array([ids], dtype=np.int64)
        m = np.array([mask], dtype=np.int64)
        out = _sess.run(None, {
            "input_ids": a,
            "attention_mask": m,
            "token_type_ids": np.zeros_like(a),
        })[0]                                  # (1, seq, 384)
        # ⚠ 평균 풀링이다. CLS 토큰만 쓰면 e5 는 품질이 크게 떨어진다.
        #   패딩이 없어도 마스크를 곱해 둔다 — 나중에 배치로 바꿔도 안 깨진다.
        w = m[..., None].astype(np.float32)
        vec = (out * w).sum(axis=1) / np.clip(w.sum(axis=1), 1e-9, None)
        v = vec[0].astype(np.float32)
        n = float(np.linalg.norm(v))
        return v / n if n > 0 else None
    except Exception:
        return None


def embed_query(text: str) -> np.ndarray | None:
    """찾는 말. e5 는 여기에 'query: ' 를 요구한다."""
    return _embed(f"query: {text}")


def embed_passage(text: str) -> np.ndarray | None:
    """저장할 글. e5 는 여기에 'passage: ' 를 요구한다."""
    return _embed(f"passage: {text}")
