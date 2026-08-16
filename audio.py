"""마이크 캡처 + 에너지 기반 VAD + whisper STT. 전부 로컬, 토큰 0."""

import queue
import re
import sys
import time

import numpy as np

import config
from dblog import log as _log

try:
    import sounddevice as sd
except OSError as e:  # PortAudio 미설치
    print(f"[동백] 오디오 백엔드 로드 실패: {e}\n  → brew install portaudio", file=sys.stderr)
    raise


# ─────────────────────────────────────────────────────────
# 장치 선택
# ─────────────────────────────────────────────────────────
def input_devices() -> list[tuple[int, str, bool]]:
    """(index, name, is_virtual) 목록."""
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] < 1:
            continue
        name = d["name"]
        virtual = any(h in name.lower() for h in config.VIRTUAL_DEVICE_HINTS)
        out.append((i, name, virtual))
    return out


def pick_device() -> int:
    devs = input_devices()
    if not devs:
        raise RuntimeError(
            "입력 장치가 하나도 없습니다. USB 마이크를 연결하세요."
        )

    if config.INPUT_DEVICE is not None:
        want = str(config.INPUT_DEVICE).lower()
        for i, name, _ in devs:
            if want in name.lower():
                return i
        raise RuntimeError(
            f"INPUT_DEVICE='{config.INPUT_DEVICE}' 와 맞는 장치 없음. "
            f"가용: {[n for _, n, _ in devs]}"
        )

    real = [d for d in devs if not d[2]]
    if not real:
        names = ", ".join(n for _, n, _ in devs)
        raise RuntimeError(
            f"물리 마이크가 없습니다. 감지된 건 가상 장치뿐: {names}\n"
            "  → USB 마이크를 연결하거나, 아이폰 연속성 카메라를 켜세요."
        )
    return real[0][0]


def describe_devices() -> str:
    lines = []
    for i, name, virtual in input_devices():
        lines.append(f"  [{i}] {name}{'  (가상 — 사용 불가)' if virtual else ''}")
    return "\n".join(lines) or "  (없음)"


# ─────────────────────────────────────────────────────────
# VAD 캡처
# ─────────────────────────────────────────────────────────
def rescan() -> None:
    """PortAudio는 장치 목록을 초기화 시점에 캐싱한다.
    핫플러그(KVM 전환, USB 재연결)를 인식하려면 강제로 다시 열어야 함."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        pass


def wait_for_device(name: str | None, *, poll: float = 2.0, announce=None) -> int:
    """장치가 (돌아)올 때까지 대기. KVM이 다른 PC를 향해 있을 때 여기서 버팁니다."""
    warned = False
    while True:
        rescan()
        try:
            if name:
                for i, dev_name, virtual in input_devices():
                    if not virtual and name.lower() in dev_name.lower():
                        return i
            else:
                return pick_device()
        except RuntimeError:
            pass
        if not warned:
            if announce:
                announce()
            warned = True
        time.sleep(poll)


class Listener:
    """마이크를 열고 발화 단위(numpy float32)로 잘라서 내보냅니다.

    장치가 사라져도 죽지 않고, 돌아오면 스스로 다시 붙습니다.
    인덱스는 재열거마다 바뀌므로 '이름'을 기준으로 재탐색합니다.
    """

    def __init__(self, device: int | None = None):
        idx = pick_device() if device is None else device
        self.device = idx
        self.device_name = self._name_of(idx)
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: sd.InputStream | None = None
        self._floor = config.VAD_ABS_FLOOR
        self._floor_hist: list[float] = []
        self._dead = False
        self.on_disconnect = None
        self.on_reconnect = None
        self.gate = None   # () -> bool, True면 동백가 말하는 중
        self.on_barge_in = None   # () -> None, 사장님이 말을 시작해 끊어야 할 때
        self.on_gate_report = None  # (peak, threshold) -> None, 끊기 문턱 조정용 실측

    @staticmethod
    def _name_of(idx: int) -> str:
        try:
            return sd.query_devices()[idx]["name"]
        except Exception:
            return ""

    def _open(self) -> None:
        self._stream = sd.InputStream(
            device=self.device,
            channels=1,
            samplerate=config.SAMPLE_RATE,
            blocksize=config.BLOCK,
            dtype="float32",
            callback=self._cb,
        )
        self._stream.start()
        self._dead = False

    def _close(self) -> None:
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stream = None

    def _reconnect(self) -> None:
        """장치가 돌아올 때까지 기다렸다가 다시 붙는다."""
        self._close()
        if self.on_disconnect:
            self.on_disconnect(self.device_name)
        self.device = wait_for_device(self.device_name)
        self.device_name = self._name_of(self.device)
        with self._q.mutex:
            self._q.queue.clear()
        self._floor_hist.clear()
        self._open()
        if self.on_reconnect:
            self.on_reconnect(self.device_name)

    def __enter__(self):
        self._open()
        return self

    def __exit__(self, *exc):
        self._close()

    def _cb(self, indata, frames, time_info, status):
        if status and getattr(status, "input_error", False):
            self._dead = True
            return
        self._q.put(indata[:, 0].copy())

    def _update_floor(self, rms: float) -> None:
        self._floor_hist.append(rms)
        if len(self._floor_hist) > 60:          # 최근 ~2초
            self._floor_hist.pop(0)
        quiet = sorted(self._floor_hist)[: max(1, len(self._floor_hist) // 2)]
        self._floor = max(config.VAD_ABS_FLOOR, float(np.mean(quiet)))

    def _drain(self) -> None:
        with self._q.mutex:
            self._q.queue.clear()

    def next_utterance(self, timeout: float | None = None) -> np.ndarray | None:
        """발화 하나를 잡아서 반환. timeout 초과 시 None.

        제너레이터가 아니라 명령형인 이유: 위험 명령 재확인 때
        같은 스트림에서 '한 번 더' 받아야 하는데, 제너레이터 재진입은 꼬입니다.
        """
        preroll: list[np.ndarray] = []
        buf: list[np.ndarray] = []
        speaking = False
        hot = cold = barge = loud_run = 0
        ask_p: float | None = None      # 말끝 판정 (turn_end) — 발화마다 한 번
        ask_at = 0
        ask_resumed = False
        ask_loud_run = 0
        started = time.monotonic()
        gate_since: float | None = None
        barged = False          # 이번 발화에서 이미 동백을 끊었나
        gate_peak = 0.0         # 동백이 말하는 동안 들린 가장 큰 소리
        # '못 끊은 이유' 를 피크만으로는 못 가른다 — 0.1079(문턱 4.9배)가
        # "길이가 모자람" 으로 남았는데, 한 블록짜리 소음이었는지 계수기
        # 진동 사각이었는지 피크로는 구분이 안 된다 (2026-08-12 23:22).
        # 계수기가 어디까지 올라갔는지를 같이 남긴다.
        gate_streak = 0.0       # barge 계수기가 이번 게이트에서 오른 최고치
        gate_loud_streak = 0    # 큰 소리(문턱 2배) 최장 연속 블록
        max_blocks = int(config.MAX_UTTERANCE_SEC * config.SAMPLE_RATE / config.BLOCK)

        while True:
            if timeout is not None and not speaking:
                if time.monotonic() - started > timeout:
                    return None

            # 동백가 말하는 중.
            #
            # 이 시간은 타임아웃에서 뺀다. 사장님이 답할 수 있는 건 동백가 말을
            # 끝낸 뒤부터인데, 말하는 동안에도 시계가 흐르면 실제로 기다려주는
            # 시간이 그만큼 줄어든다. 되묻는 문장이 길수록 심해져서,
            # 답할 틈도 없이 "응답이 없어서 취소했습니다" 가 나오던 원인이었다.
            # barged: 이미 끊고 사장님 말을 받는 중이면 게이트를 보지 않는다.
            #
            # 안 그러면 이런 일이 난다 — stop() 을 불러도 재생 스레드가
            # 정리되기까지 잠깐은 is_speaking() 이 True 로 남는다. 그 사이
            # 여기로 되돌아오면 사장님 말이 buf 가 아니라 10블록짜리 preroll
            # 에만 쌓이고, 다시 끊길 때마다 그 0.32초로 buf 를 덮어써서
            # 앞말이 통째로 날아간다. 로그에 '끊고 들어옴' 이 세 번 연속
            # 찍힌 게 그 증거였다. 한 번 끊었으면 그 발화가 끝날 때까지
            # 계속 받아야 문맥이 온전히 남는다.
            if self.gate and self.gate() and not barged:
                now = time.monotonic()
                if gate_since is None:
                    gate_since = now
                # 말하는 시간만큼 타임아웃 시계를 멈춘다 — 다만 무한정은 아니다.
                #
                # 재생이 어떤 이유로 안 끝나면(워커가 죽거나 장치가 멈추면)
                # is_speaking() 이 계속 True 로 남는다. 그때도 시계를 멈춰두면
                # next_utterance 가 영원히 안 돌아오고 동백은 귀를 잃는다.
                # 상한을 넘기면 시계를 다시 흐르게 두어 빠져나갈 수 있게 한다.
                if now - gate_since < config.GATE_MAX_HOLD_SEC:
                    started = now
                if not config.BARGE_IN_ENABLED:
                    self._drain()
                    preroll, buf, speaking, hot, cold, barge = [], [], False, 0, 0, 0
                    loud_run = 0
                    time.sleep(0.05)
                    continue

                # 끊고 들어오기 — 사장님이 말을 시작하면 동백이 입을 다문다.
                #
                # 여기서 듣는 소리에는 동백 자기 목소리도 섞여 있다.
                # 입력·출력이 같은 PowerConf 라 하드웨어 에코 제거가 걸리지만
                # 잔향은 남으므로, 평상시보다 문턱을 높이고(BARGE_IN_MULT)
                # 더 오래 이어져야(BARGE_IN_BLOCKS) 사람 말로 인정한다.
                # 그래도 속을 수 있어서, 받아쓴 뒤 '방금 자기가 한 말' 과
                # 비슷하면 버리는 2차 방어를 dongbaek 쪽에 두었다.
                try:
                    block = self._q.get(timeout=0.25)
                except queue.Empty:
                    continue
                rms = float(np.sqrt(np.mean(block**2)) + 1e-12)
                # 시작 부분이 잘리지 않게 계속 모아둔다. 끊은 뒤 이 앞부분을
                # 그대로 발화로 이어받는다 — 안 그러면 첫 음절이 날아간다.
                preroll.append(block)
                if len(preroll) > config.VAD_PREROLL_BLOCKS:
                    preroll.pop(0)
                # 실제로 얼마나 큰 소리가 들어왔는지 남긴다.
                # 문턱을 추측으로 조정하다 두 번 어긋났다 — 음악에 끊기거나
                # 사장님 말에 안 끊기거나. 다음엔 이 숫자를 보고 정한다.
                gate_peak = max(gate_peak, rms)
                _loud = max(self._floor * config.BARGE_IN_MULT,
                            config.BARGE_IN_ABS_FLOOR)
                if rms > _loud:
                    barge += 1
                    # 큰 소리는 길이를 안 본다 — 아래 ⓑ 참고.
                    # 원거리 시간대엔 '큰 소리' 기준도 내려간다 (config).
                    if rms >= _loud * config.barge_in_loud_mult():
                        loud_run += 1
                    else:
                        loud_run = 0
                else:
                    loud_run = 0
                    # ⚠ 0 으로 되돌리면 안 된다. 사람 말은 음절 사이마다
                    #   소리가 잠깐씩 죽는데, 그 틈에서 처음부터 다시 세면
                    #   연속 블록이 영영 안 채워진다.
                    #
                    #   2026-08-12 새벽, 사장님이 끼어드신 10번이 전부
                    #   "넘었는데 길이가 모자람" 으로 흘렀다. 소리가 문턱의
                    #   두 배(0.076 vs 0.030)였는데도 못 끊었다. 사장님
                    #   지적: "내가 얘기하면 인식하는 순간 지 할 말을 끊어
                    #   버리고 내 얘기를 들어야지."
                    #
                    #   한 칸씩만 내린다. 짧은 숨은 견디고, 진짜 조용해지면
                    #   금세 0 으로 돌아간다.
                    #
                    #   ⓐ 2026-08-12 밤: 한 칸도 컸다. +1 −1 +1 −1 로 진동하면
                    #      3에 영영 안 닿아서, 문턱의 3배(0.0943)로 말씀하신
                    #      것까지 "길이가 모자람" 으로 흘렀다. 0.5 로 낮춘다.
                    barge = max(0.0, barge - getattr(config, "BARGE_IN_DECAY", 0.5))
                gate_streak = max(gate_streak, barge)
                gate_loud_streak = max(gate_loud_streak, loud_run)
                # ⓑ 큰 소리는 길이를 기다리지 않는다. 문턱의 두 배가 두 블록
                #   (~64ms) 이어졌으면 사람이 말을 시작한 것이다 — 문 닫는
                #   소리 같은 순간 충격은 한 블록으로 끝나 여기 안 걸린다.
                if (barge >= config.barge_in_blocks()
                        or loud_run >= getattr(config, "BARGE_IN_LOUD_BLOCKS", 2)):
                    if self.on_barge_in:
                        self.on_barge_in()
                    barged = True
                    buf, speaking, hot, cold, barge = list(preroll), True, 0, 0, 0
                    loud_run = 0
                    preroll = []
                continue

            # 게이트가 막 열렸다 = 동백이 말을 마쳤다. 그동안 들린 최댓값을
            # 문턱과 함께 남긴다. '못 끊었다' 는 사실만으론 원인을 못 찾는다.
            if gate_since is not None and gate_peak > 0 and self.on_gate_report:
                self.on_gate_report(gate_peak,
                                    max(self._floor * config.BARGE_IN_MULT,
                                        config.BARGE_IN_ABS_FLOOR),
                                    gate_streak, gate_loud_streak)
            barge = 0
            gate_since = None
            gate_peak = 0.0
            gate_streak = 0.0
            gate_loud_streak = 0

            # 장치 소실 감지 (KVM 전환 / USB 분리)
            if self._dead or (self._stream is not None and not self._stream.active):
                self._reconnect()
                preroll, buf, speaking, hot, cold = [], [], False, 0, 0
                started = time.monotonic()
                continue

            try:
                block = self._q.get(timeout=0.25)
            except queue.Empty:
                continue

            rms = float(np.sqrt(np.mean(block**2)) + 1e-12)
            # 새벽 원거리 모드면 더 작은 소리에도 귀를 연다 (config 참조).
            threshold = max(self._floor * config.vad_trigger_mult(),
                            config.vad_abs_floor())
            loud = rms > threshold

            if not speaking:
                self._update_floor(rms)
                preroll.append(block)
                if len(preroll) > config.VAD_PREROLL_BLOCKS:
                    preroll.pop(0)
                hot = hot + 1 if loud else 0
                if hot >= config.VAD_START_BLOCKS:
                    speaking = True
                    buf, preroll, cold = list(preroll), [], 0
                    ask_p = None          # 이번 발화의 말끝 판정 (한 번만 묻는다)
                    ask_at = 0            # 물어본 시점의 블록 수
                    ask_resumed = False   # 물어본 뒤에 말이 이어졌나
                    ask_loud_run = 0      # 물어본 뒤 연속으로 시끄러운 블록 수
            else:
                buf.append(block)
                if ask_p is not None:
                    # 끝났다고 본 뒤에 말이 이어졌으면 그 판정은 오탐이었다 —
                    # 실전 모드였으면 사장님 말을 잘랐다는 뜻이다.
                    #
                    # ⚠ 한 블록 시끄러운 것으로 '이어졌다' 고 세면 안 된다.
                    #   VAD 자신도 발화 시작을 VAD_START_BLOCKS 연속으로 본다.
                    #   기침·문소리 한 번을 오탐으로 세면 섀도 수치가 부풀고,
                    #   그 부푼 숫자로 '켜면 안 되겠다' 를 판단하게 된다.
                    #   계측기가 판단을 그르치는 쪽으로 틀리면 안 된다.
                    ask_loud_run = ask_loud_run + 1 if loud else 0
                    if ask_loud_run >= config.VAD_START_BLOCKS:
                        ask_resumed = True
                cold = 0 if loud else cold + 1

                # 짧은 무음에서 한 번 물어본다. 섀도 모드에선 기록만 하고
                # 아래 규칙(무음 세기)은 그대로 둔다 — 정확도를 실측으로
                # 확인하기 전에는 사장님 말을 자를 위험을 지지 않는다.
                if (ask_p is None and cold == config.TURN_END_ASK_BLOCKS
                        and len(buf) > cold):
                    ask_p = _ask_turn_end(np.concatenate(buf))
                    ask_at = len(buf)
                # 짧게 끊어 말했으면 일찍 끝낸다. "동백아" 한마디에 1.4초를
                # 기다리면 못 알아들은 줄 알게 된다. 길게 말하는 중이면
                # 생각하느라 쉬는 것이므로 넉넉히 기다린다.
                spoken = len(buf) - cold
                need = (config.VAD_END_BLOCKS
                        if spoken > config.VAD_SHORT_SPEECH_BLOCKS
                        else config.VAD_END_BLOCKS_SHORT)
                if cold >= need or len(buf) >= max_blocks:
                    audio = np.concatenate(buf)
                    # 물어본 뒤에 들어온 소리만 따로 넘긴다 — 그게 사장님
                    # 목소리인지 옆 소리인지 가려야 판정을 채점할 수 있다.
                    tail = (np.concatenate(buf[ask_at:])
                            if ask_p is not None and len(buf) > ask_at else None)
                    _log_turn_end(ask_p, ask_at, ask_resumed, len(buf), need, tail)
                    if len(audio) / config.SAMPLE_RATE >= config.MIN_UTTERANCE_SEC:
                        return dawn_boost(audio)
                    buf, speaking, hot, cold = [], False, 0, 0
                    started = time.monotonic()

    def utterances(self, timeout: float | None = None):
        while (audio := self.next_utterance(timeout)) is not None:
            yield audio


def _ask_turn_end(buf: np.ndarray) -> float | None:
    """말이 끝난 꼴인가를 모델에게 묻는다 (10ms). 실패하면 None.

    청취 루프 안에서 바로 돈다 — 한 블록이 ~32ms 라 10ms 는 묻힌다.
    스레드로 빼면 판정과 발화의 짝을 다시 맞춰야 해서 되레 복잡해진다.
    """
    if not getattr(config, "TURN_END_ENABLED", False):
        return None
    try:
        import turn_end

        return turn_end.probability(buf)
    except Exception:
        return None      # 말끝 판정 때문에 귀가 멎으면 안 된다


def _resumed_by_owner(tail) -> bool | None:
    """이어진 소리가 등록된 사람의 목소리인가. 모르면 None.

    ⚠ 이게 없으면 섀도 수치가 못 쓰게 된다. 사장님 자리엔 옆 통화·아파트
      방송·TV 가 상시 있어서, '소리가 이어졌다' 를 그대로 '사장님이 말을
      이었다' 로 세면 오탐이 부풀어 오른다 (774건 실측 오탐률 36% 중
      얼마가 주변 소리인지 가릴 수 없었다).
      화자 확인은 18.5ms 라 발화당 한 번은 싸다.
    """
    if tail is None or not getattr(config, "VOICE_VERIFY_ENABLED", False):
        return None
    if len(tail) < int(config.SAMPLE_RATE * 0.4):
        return None                    # 너무 짧으면 지문이 안 잡힌다
    try:
        import voiceprint

        if not voiceprint.enrolled():
            return None                # 등록된 사람이 없으면 가릴 수 없다
        name, _ = voiceprint.verify(tail)
        return name is not None
    except Exception:
        return None


def _log_turn_end(p, ask_at, resumed, total_blocks, need, tail=None) -> None:
    """섀도 기록 — 실전으로 켜기 전에 오탐률을 여기서 읽는다.

    판정이 맞았는지는 '물어본 뒤에 **사장님이** 말을 이었는가' 로 가른다:
      · 끝이라 했는데 이어짐  → 오탐. 실전이었으면 사장님 말을 잘랐다.
      · 끝이라 했고 안 이어짐 → 맞음. 남은 대기만큼이 벌 수 있던 시간이다.

    ⚠ '소리가 났다' 와 '사장님이 말을 이었다' 는 다르다. 앞의 것으로 세면
      옆 통화·방송이 전부 오탐으로 들어간다. 화자 확인으로 가른다.
    """
    if p is None or not getattr(config, "TURN_END_SHADOW", True):
        return
    owner = _resumed_by_owner(tail) if resumed else None
    who = ""
    if resumed and owner is False:
        resumed = False                # 옆 소리였다 — 판정을 탓할 일이 아니다
        who = " (이어진 건 남 목소리)"
    elif resumed and owner is True:
        who = " (사장님 목소리 확인)"
    said_end = p >= config.TURN_END_THRESHOLD
    if said_end and resumed:
        verdict = "오탐(말을 잘랐을 것)"
    elif said_end:
        saved = max(0, (total_blocks - ask_at)) * 0.032
        verdict = f"맞음 — {saved:.1f}초 벌 수 있었음"
    elif resumed:
        verdict = "맞음(계속으로 봄, 실제로 이어짐)"
    else:
        verdict = "미탐(계속으로 봤지만 끝남)"
    _log(f"말끝판정: p={p:.3f} → {'끝' if said_end else '계속'} · {verdict}{who}")


def dawn_boost(audio: np.ndarray) -> np.ndarray:
    """먼 데서 온 작은 발화를 증폭한다 — whisper 와 화자 지문을 위해.

    ⚠ 시간대에 묶지 않는다. 2026-08-13 08:29, 새벽 모드가 8시에 꺼진
      직후 침대에서 부르신 "동백아" 가 '크기가' 로 적혀 버려졌다.
      멀리서 부르는 건 시계를 안 보고 일어난다. 증폭은 이미 VAD 가
      '사람 말' 로 판정한 소리에만 걸리고, 큰 소리는 손대지 않으므로
      (아래 rms >= target 조기 반환) 낮에 켜도 잃을 게 없다.

    시간대(새벽)에 묶이는 것은 '문턱 완화' 쪽이다 — 그건 오인식 위험이
    있어 방문객·TV 가 드문 시간대로 제한한다 (config.dawn_far_active).
    """
    if not getattr(config, "FAR_GAIN_ENABLED", True):
        return audio
    rms = float(np.sqrt(np.mean(audio**2)) + 1e-12)
    target = getattr(config, "DAWN_GAIN_TARGET_RMS", 0.030)
    if rms <= 0 or rms >= target:
        return audio
    gain = min(getattr(config, "DAWN_GAIN_MAX", 6.0), target / rms)
    if _on_gain:
        _on_gain(rms, gain)
    return np.clip(audio * gain, -1.0, 1.0)


# 증폭이 걸린 사실을 데몬이 로그에 남긴다 — '작아서 놓쳤나' 를 숫자로 본다.
_on_gain = None


def set_gain_reporter(fn) -> None:
    global _on_gain
    _on_gain = fn


# ─────────────────────────────────────────────────────────
# STT
# ─────────────────────────────────────────────────────────
_model_warm = False
_TERM_FIXES = [(re.compile(p), r) for p, r in config.TERM_FIXES]


def preload() -> bool:
    """whisper 모델을 미리 올린다. 데몬 기동 때 백그라운드로 부를 것.

    안 하면 기동 후 첫 "동백아" 가 모델 로딩까지 떠안는다.
    실측: 첫 호출 2.82초 / 그다음부터 0.42초 — 2.4초가 전부 로딩이었다.
    로그에는 '모델 준비 중' 이라고 찍히는데 정작 준비하는 코드가 없었다.
    """
    global _model_warm
    if _model_warm:
        return True
    try:
        transcribe(np.zeros(int(0.4 * config.SAMPLE_RATE), dtype=np.float32))
        return True
    except Exception:
        return False


def transcribe(audio: np.ndarray) -> str:
    global _model_warm
    import mlx_whisper

    if not _model_warm:
        _model_warm = True
    result = mlx_whisper.transcribe(
        audio,
        path_or_hf_repo=config.WHISPER_MODEL,
        language=config.LANGUAGE,
        fp16=True,
        condition_on_previous_text=False,
        # 자주 쓰는 고유명사를 미리 알려주면 그쪽으로 인식이 쏠린다.
        # '주석'→'주성문', 'config'→'폼픽' 같은 오인식을 줄인다.
        initial_prompt=config.WHISPER_PROMPT,
    )
    text = (result.get("text") or "").strip()
    if not text:
        return ""

    # ── 환각 차단 ①: 프롬프트 되받아쓰기
    #
    # whisper 는 애매한 소리를 들으면 initial_prompt 를 그대로 적기도 한다.
    # 실제로 '일정, 캘린더, 메일, 진행, 승인, 취소, 되돌려.' 가 명령으로
    # 실행돼 Claude 호출료가 나갔다.
    #
    # 부분 문자열 비교만으로는 부족했다 — '일정'을 '네온'으로 적은 변형이
    # 그대로 통과했다. 낱말이 얼마나 겹치는지로 본다.
    if _looks_like_prompt(text):
        return ""

    # ── 환각 차단 ②: 같은 말 반복
    #
    # 잡음이나 무음을 들으면 한 낱말을 수백 번 반복해 적는다.
    # ('많이 많이 많이 …' × 200, 'vents vents vents …' × 200 실측)
    # 승인 대기 중에 이런 게 들어오면 사장님 대답으로 오인된다.
    if _is_repetition(text):
        return ""

    # ── 고유명사 교정
    # initial_prompt 는 '그쪽으로 쏠리게' 할 뿐 보장이 아니다. 실제로
    # '한빛리조트' 가 한 세션에서 '용탐 밸리'·'용전 밸리'·'영팁밸리' 로 갈렸다.
    text = fix_terms(text)

    # ── 억양으로 물음표 복원 (사장님 지시 2026-08-13)
    # whisper 가 ? 를 빼먹으면 "잘 들려?" 가 평서문이 되어 헛답이 나간다.
    # 말끝 피치가 올라갔으면 물음표를 붙인다 — 붙이기만 하고 지우지 않는다.
    if (getattr(config, "PROSODY_ENABLED", False)
            and text and not text.rstrip().endswith(("?", "!"))):
        try:
            import prosody

            if prosody.rising(audio,
                              ratio=getattr(config, "PROSODY_RISE_RATIO", 1.12)):
                text = text.rstrip().rstrip(".…,") + "?"
                _log("억양 올라감 — 물음표 보강")
        except Exception:
            pass                    # 운율 분석 실패로 받아쓰기가 죽으면 본말전도
    return text


def fix_terms(text: str) -> str:
    """자주 틀리는 고유명사를 되돌린다."""
    for pattern, right in _TERM_FIXES:
        text = pattern.sub(right, text)
    return text


def _squash(s: str) -> str:
    """구두점·공백 차이를 무시하고 비교하기 위한 표준형."""
    return re.sub(r"[^\w가-힣]+", "", s).lower()


def _looks_like_prompt(text: str) -> bool:
    if len(text) < 6:
        return False
    squashed = _squash(text)
    prompt = _squash(config.WHISPER_PROMPT)
    if squashed in prompt:
        return True
    # 프롬프트 낱말이 대부분을 차지하면 되받아쓴 것으로 본다
    words = [w for w in re.split(r"[,\s]+", text) if len(w) >= 2]
    if len(words) < 4:
        return False
    known = {w for w in re.split(r"[,.\s]+", config.WHISPER_PROMPT) if len(w) >= 2}
    hit = sum(1 for w in words if w.strip(".,") in known)
    return hit / len(words) >= 0.6


def _is_repetition(text: str) -> bool:
    """같은 말만 되풀이하는 받아쓰기인가.

    두 갈래로 나온다. 실측:
      낱말 반복  — '많이 많이 많이 …' × 200, 'vents vents …' × 180
      글자 반복  — '시시시시시시…' (띄어쓰기가 아예 없다)
    낱말 기준만 두었다가 뒤엣것을 놓쳤다. 한 낱말로 세어져 통과했다.
    """
    words = text.split()
    # 낱말 수에 비해 종류가 턱없이 적으면 반복이다
    if len(words) >= 8 and len(set(words)) <= max(2, len(words) // 12):
        return True
    # 띄어쓰기 없이 같은 글자만 이어지는 경우.
    # 사람이 12자를 말하면서 글자 종류가 두 개뿐일 수는 없다.
    squashed = re.sub(r"\s+", "", text)
    return len(squashed) >= 12 and len(set(squashed)) <= 2


def transcribe_file(path: str) -> str:
    """마이크 없이 테스트할 때 사용."""
    import mlx_whisper

    result = mlx_whisper.transcribe(
        path,
        path_or_hf_repo=config.WHISPER_MODEL,
        language=config.LANGUAGE,
    )
    return (result.get("text") or "").strip()
