"""Supertonic 신경망 TTS — 문장 단위 파이프라이닝.

macOS `say` 는 스트리밍이라 즉시 시작하지만 기계음이다.
Supertonic 은 자연스럽지만 wav 를 다 만들어야 재생된다.

그래서 문장 단위로 쪼갠다. 첫 문장만 만들어 재생을 시작하고
나머지는 재생 중에 만든다. 생성이 재생보다 6~7배 빠르므로 뒤는 밀리지 않고,
첫 소리까지의 시간이 답변 길이와 무관하게 ~0.5초로 고정된다.

실패하면 조용히 `say` 로 내려간다. 목소리가 아예 안 나오는 것보다 낫다.
"""

import queue
import re
import threading
import time

import numpy as np

import config

_tts = None
_style = None
_load_lock = threading.Lock()
_load_failed = False

# 문장을 '종결부호까지 포함해서' 통째로 집는다.
# split() 으로 자르면 구분자인 마침표가 사라져서, 짧은 문장을 병합할 때
# "확인했습니다 오류 세 건입니다" 처럼 쉼표도 마침표도 없는 문장이 되어
# TTS 가 쉬지 않고 읽어버린다.
_SENT = re.compile(r"[^.!?。\n]*[.!?。]|[^.!?。\n]+")
_MIN_CHUNK = 12
_MAX_CHUNK = 90


def available() -> bool:
    return config.TTS_ENGINE == "supertonic" and not _load_failed


def preload() -> bool:
    """모델을 미리 올려둔다. 데몬 시작 때 부르면 첫 응답이 안 밀린다."""
    return _ensure_loaded()


def _ensure_loaded() -> bool:
    global _tts, _style, _load_failed
    if _tts is not None:
        return True
    if _load_failed:
        return False
    with _load_lock:
        if _tts is not None:
            return True
        try:
            from supertonic import TTS

            t = TTS(auto_download=False)
            _style = t.get_voice_style(voice_name=config.TTS_SUPERTONIC_VOICE)
            _tts = t
            return True
        except Exception:
            _load_failed = True
            return False


def split_sentences(text: str) -> list[str]:
    raw = [m.group().strip() for m in _SENT.finditer(text) if m.group().strip()]
    if not raw:
        return []
    out: list[str] = []
    for s in raw:
        # 너무 긴 문장은 쉼표에서 한 번 더 자른다
        while len(s) > _MAX_CHUNK:
            cut = s.rfind(",", 0, _MAX_CHUNK)
            if cut < _MIN_CHUNK:
                cut = _MAX_CHUNK
            out.append(s[: cut + 1].strip())
            s = s[cut + 1 :].strip()
        if out and len(s) < _MIN_CHUNK:
            out[-1] = f"{out[-1]} {s}"   # 짧은 꼬리는 앞에 붙임
        elif s:
            out.append(s)
    return out


def _synth(text: str) -> "np.ndarray | None":
    if not _ensure_loaded():
        return None
    try:
        wav, _ = _tts.synthesize(
            text=text,
            lang=config.LANGUAGE,
            voice_style=_style,
            total_steps=config.TTS_SUPERTONIC_STEPS,
            speed=config.TTS_SUPERTONIC_SPEED,
        )
        # 모델은 (1, N) float32 로 준다. sounddevice 는 1-D 를 원한다.
        return np.asarray(wav, dtype=np.float32).reshape(-1)
    except Exception:
        return None


def output_device() -> int | None:
    """sounddevice 출력 장치 인덱스.

    afplay 는 출력 장치를 못 고른다. 기본 출력이 스피커 없는 LG 모니터라
    그대로 두면 무음이 되므로 직접 골라야 한다.
    """
    try:
        import sounddevice as sd

        devs = [
            (i, d["name"])
            for i, d in enumerate(sd.query_devices())
            if d["max_output_channels"] > 0
        ]
    except Exception:
        return None

    for want in config.TTS_DEVICE_PREFERENCE:
        for i, name in devs:
            if want.lower() in name.lower():
                return i
    for i, name in devs:
        if not any(h in name.lower() for h in config.VIRTUAL_DEVICE_HINTS):
            return i
    return None


# ─────────────────────────────────────────────────────────
# 상주 출력 스트림 — 발화마다 장치를 열닫지 않는다 (2026-08-13).
#
# sd.play 는 전역 스트림을 매번 새로 여는데, 직전 발화가 장치를 놓기 전에
# 다음이 열면 err=-50 으로 튕겼다. 0.15초 재시도를 넣어도 하루 폴백이
# 발화의 14% (55건 중 8건) — 목소리가 문장마다 Yuna 로 바뀌었다.
# 스트림을 한 번 열어 두고 write 만 하면 여닫는 경합 자체가 없다.
# ─────────────────────────────────────────────────────────
_out = {"stream": None, "device": None}
_out_lock = threading.Lock()


def _get_out(device):
    import sounddevice as sd

    with _out_lock:
        st = _out["stream"]
        if st is not None and _out["device"] == device:
            try:
                if st.active:
                    return st
                st.start()
                return st
            except Exception:
                pass
        if st is not None:
            try:
                st.close()
            except Exception:
                pass
        st = sd.OutputStream(samplerate=config.TTS_SUPERTONIC_SR, channels=1,
                             dtype="float32", device=device)
        st.start()
        _out.update(stream=st, device=device)
        return st


def abort_out() -> None:
    """재생 중인 소리를 즉시 끊는다 (버퍼 파기). 스트림은 다시 살려 둔다."""
    with _out_lock:
        st = _out["stream"]
        if st is None:
            return
        try:
            st.abort()
            st.start()
        except Exception:
            try:
                st.close()
            except Exception:
                pass
            _out["stream"] = None


def play_array(arr: np.ndarray, *, stop_event=None, interrupt=None,
               device=None) -> bool:
    """상주 스트림으로 배열 하나를 재생. 소리가 나가기 시작했으면 True.

    조각 단위로 쓰며 중단 신호를 살핀다 — write 는 블로킹이라 통째로
    넣으면 stop 이 문장 끝까지 못 끊는다.
    """
    st = _get_out(device if device is not None else output_device())
    data = np.ascontiguousarray(arr, dtype=np.float32)
    started = False
    CHUNK = 4096
    i = 0
    while i < len(data):
        if (stop_event is not None and stop_event.is_set()) or (
                interrupt is not None and interrupt.is_set()):
            break
        st.write(data[i:i + CHUNK])
        started = True
        i += CHUNK
    return started


class Playback:
    """생성 스레드와 재생 스레드를 분리해 첫 소리를 앞당긴다.

    생성이 재생보다 6~7배 빠르므로, 첫 문장이 나가는 동안 나머지가 준비된다.
    """

    def __init__(self, device: int | None):
        self._device = device
        self._q: queue.Queue = queue.Queue()
        self._stopped = threading.Event()
        self._done = threading.Event()
        # 소리가 한 번이라도 스피커로 나갔는가. 재생이 멎어 끊어야 할 때,
        # 다시 말해도 되는지(안 나갔다) 겹치는지(나갔다) 를 가른다.
        self._started = threading.Event()
        # 재생 스레드가 장치 오류로 죽었는가 — 호출측 진단용.
        self._failed = False

    def start(self, chunks: list[str]) -> None:
        def produce():
            for c in chunks:
                if self._stopped.is_set():
                    break
                arr = _synth(c)
                if arr is not None and arr.size:
                    self._q.put(arr)
            self._q.put(None)

        def consume():
            while not self._stopped.is_set():
                arr = self._q.get()
                if arr is None:
                    break
                try:
                    # 상주 스트림에 write — 발화마다 여닫던 경합이 없다.
                    # started 는 실제 소리가 나가기 시작했을 때만 켠다
                    # (무음이 "나갔다" 로 남으면 호출측이 폴백을 생략한다).
                    if play_array(arr, stop_event=self._stopped,
                                  device=self._device):
                        self._started.set()
                except Exception as e:
                    # 장치가 뽑혔거나 스트림이 진짜 죽었다 — 새로 열어
                    # 한 번만 다시 써 본다. 무슨 오류인지는 반드시 남긴다:
                    # 지금까지 원인 추적이 늦은 건 예외가 조용히 삼켜져서다.
                    try:
                        from dblog import log as _tlog

                        _tlog(f"재생 오류 {type(e).__name__}: {str(e)[:80]}"
                              " — 스트림 재개방 시도")
                    except Exception:
                        pass
                    try:
                        abort_out()
                        time.sleep(0.15)
                        if play_array(arr, stop_event=self._stopped,
                                      device=self._device):
                            self._started.set()
                    except Exception:
                        self._failed = True
                        break
            self._done.set()

        for fn in (produce, consume):
            threading.Thread(target=fn, daemon=True).start()

    def stop(self) -> None:
        # ⚠ 스트림을 abort 하지 않는다. abort→start→(다음 열기) 가 결국
        #   재개방이 되는데, 입력 스트림이 항상 열려 있는 PowerConf 에선
        #   출력 재개방 자체가 경합이다 — 상주화 뒤에도 barge 직후 두 번
        #   튕긴 실측(11:37:47·11:38:02)이 그 증거다. 먹이 공급만 끊으면
        #   조각(≤170ms)+내부 버퍼(~100ms) 꼬리만 남고 스트림은 계속 산다.
        self._stopped.set()
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break
        self._done.set()

    def wait(self) -> None:
        self._done.wait()

    def is_active(self) -> bool:
        return not self._done.is_set()

    def started(self) -> bool:
        """소리가 한 번이라도 나갔는가."""
        return self._started.is_set()

    def failed(self) -> bool:
        """재생 스레드가 장치 오류로 죽었는가."""
        return self._failed


def speak(text: str, *, block: bool) -> "Playback | None":
    """성공하면 Playback, 실패하면 None (호출측이 say 로 폴백)."""
    chunks = split_sentences(text)
    if not chunks or not _ensure_loaded():
        return None
    pb = Playback(output_device())
    pb.start(chunks)
    if block:
        pb.wait()
    return pb
