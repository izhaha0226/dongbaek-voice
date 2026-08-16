"""말끝 판정(semantic endpointing) — 침묵을 세는 대신 '끝났나'를 묻는다.

지금 동백은 무음 1.4초를 세어 발화가 끝났다고 본다(VAD_END_BLOCKS). 사람은
그렇게 안 듣는다 — 말의 꼴을 보고 끝났는지 안다. 그래서 짧은 명령에도 늘
1.4초가 붙고, 그게 응답 지연의 가장 큰 몫이다(local 경로는 처리 0.00초라
지연이 사실상 대기뿐이다).

Smart Turn v3.2 (pipecat-ai, BSD-2-Clause) 는 그 판정을 8MB ONNX 한 장으로
한다. Whisper Tiny 인코더 + 얕은 분류기라 CPU 로 10ms 안에 끝난다.
한국어는 그 모델이 가장 잘하는 언어다 (공개 벤치 23개 언어 중 1위,
정확도 96.96% · 오탐 2.25% · 미탐 0.79%, 표본 889).

⚠ 새 의존성은 없다. onnxruntime 과 mlx_whisper 는 이미 깔려 있고, 참조
  구현이 쓰는 transformers 는 안 쓴다 — WhisperFeatureExtractor 가 하는 일이
  '오디오 정규화 + 정규 Whisper log-mel' 뿐이라 여기서 직접 한다.
  참조 구현과 같은 오디오에서 확률이 소수점 5자리까지 일치하는 것을 확인했다
  (2026-08-13). 전처리를 고칠 일이 생기면 그 대조부터 다시 할 것.

⚠ 아직 사장님 실제 마이크 음성으로는 검증되지 않았다. 위 벤치는 남의
  데이터셋이고, 합성음(Supertonic)으로 시험하면 미완결 문장도 완결된 억양으로
  읽혀서 판정이 뒤집힌다 — 그건 모델이 아니라 시험 방법의 결함이다.
  그래서 기본값은 섀도 모드다: 판정을 로그로만 남기고 동작은 바꾸지 않는다.
  하루치 실측으로 오탐률을 보고 나서 켠다. 말을 자르는 실수(오탐)는
  기다리는 실수보다 훨씬 나쁘다.
"""

import threading

import numpy as np

import config

_sess = None
_load_lock = threading.Lock()
_load_failed = False

SR = 16000
_WINDOW = SR * 8          # 모델이 보는 창 — 8초 고정 (80 mel × 800 프레임)


def available() -> bool:
    return bool(getattr(config, "TURN_END_ENABLED", False)) and not _load_failed


def _ensure_loaded() -> bool:
    global _sess, _load_failed
    if _sess is not None:
        return True
    if _load_failed:
        return False
    with _load_lock:
        if _sess is not None:
            return True
        try:
            import onnxruntime as ort

            path = config.TURN_END_MODEL
            if not path.exists():
                _load_failed = True
                return False
            # 참조 구현과 같은 세션 설정. 스레드를 늘리면 실시간 청취
            # 루프와 CPU 를 다투므로 늘리지 않는다.
            so = ort.SessionOptions()
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            so.inter_op_num_threads = 1
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            _sess = ort.InferenceSession(str(path), sess_options=so,
                                         providers=["CPUExecutionProvider"])
            return True
        except Exception:
            _load_failed = True
            return False


def preload() -> bool:
    """데몬 기동 때 미리 올린다. 첫 판정이 밀리지 않도록."""
    return _ensure_loaded()


def _features(audio: np.ndarray) -> np.ndarray:
    """참조 구현(WhisperFeatureExtractor + truncate_audio_to_last_n_seconds)과 동일.

    ⚠ 패딩은 '앞쪽'이다. 말은 창의 끝에 붙어야 한다 — 뒤에 채우면 모델이
      보는 마지막 순간이 무음이 되어 늘 '끝났다' 쪽으로 쏠린다.
    """
    import mlx_whisper.audio as A

    a = np.asarray(audio, dtype=np.float32)
    if a.ndim > 1:
        a = a.reshape(-1)
    a = a[-_WINDOW:] if len(a) > _WINDOW else np.pad(a, (_WINDOW - len(a), 0))
    # do_normalize=True 와 같은 것 — 평균 0, 분산 1
    a = (a - a.mean()) / np.sqrt(a.var() + 1e-7)
    mel = np.array(A.log_mel_spectrogram(a, n_mels=80), dtype=np.float32)
    return mel.T[None, :, :800]      # mlx 는 (프레임, mel) 로 준다 — 전치 필요


def probability(audio: np.ndarray) -> float | None:
    """말이 끝났을 확률. 실패하면 None — 호출측은 지금까지의 규칙으로 간다.

    출력은 이미 시그모이드를 통과한 확률이다. 한 번 더 씌우면 값이 전부
    0.5~0.73 으로 뭉쳐 변별력이 사라진다 (실제로 겪었다).
    """
    if not _ensure_loaded():
        return None
    try:
        out = _sess.run(None, {"input_features": _features(audio)})
        return float(out[0][0][0])
    except Exception:
        return None


def looks_ended(audio: np.ndarray) -> bool | None:
    p = probability(audio)
    if p is None:
        return None
    return p >= config.TURN_END_THRESHOLD
