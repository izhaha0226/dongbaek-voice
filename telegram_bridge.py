#!/usr/bin/env python3
"""텔레그램 브릿지 — 밖에서 음성으로 동백을 부른다.

  텔레그램 음성메시지 → whisper 받아쓰기 → 동백 라우터 → 텍스트+음성 답장

왜 텔레그램인가:
  아이폰 단축어는 '아이폰이 받아쓴 텍스트'를 보낸다. 텔레그램은 음성 파일
  자체를 보낼 수 있어서, 맥에서 마이크로 말하는 것과 완전히 같은 경로를 탄다.
  LTE·해외 로밍 어디서든 되고, 포트를 인터넷에 열지 않아도 된다.

⚠ 반드시 동백 전용 봇을 쓸 것.
  헤리(@herry_dev_bot)와 같은 봇을 쓰면 안 된다. 텔레그램 getUpdates 는
  한 번 읽으면 그 메시지가 사라져서, 두 프로그램이 서로 메시지를 뺏어간다.
  먼저 읽는 쪽이 가져가고 다른 쪽은 영영 못 받는다.

설정:
  ~/dongbaek/state/telegram.json
    {"bot_token": "...", "allowed_chat_ids": [123456789]}

실행:
  .venv/bin/python telegram_bridge.py
"""

import json
import queue
import subprocess
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import config
import dblog

CONF_FILE = config.STATE / "telegram.json"
OFFSET_FILE = config.STATE / "telegram_offset.txt"
API = "https://api.telegram.org"
POLL_TIMEOUT = 50          # long polling. 텔레그램 권장 최대치.
MAX_VOICE_SEC = 120        # 이보다 긴 음성은 거부 (whisper 시간·비용 방어)


def log(msg: str) -> None:
    dblog.log(msg, tag="텔레그램")


# ─────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────
def load_conf() -> dict | None:
    if not CONF_FILE.exists():
        return None
    try:
        c = json.loads(CONF_FILE.read_text())
    except ValueError:
        return None
    if not c.get("bot_token"):
        return None
    # allowed_chat_ids 가 비어 있으면 아무나 명령할 수 있다. 그건 막는다.
    if not c.get("allowed_chat_ids"):
        log("⚠ allowed_chat_ids 가 비어 있습니다. 보안상 아무도 허용하지 않습니다.")
        c["allowed_chat_ids"] = []
    return c


# ─────────────────────────────────────────────────────────
# 텔레그램 API
# ─────────────────────────────────────────────────────────
def api(token: str, method: str, params: dict | None = None, timeout: int = 60):
    url = f"{API}/bot{token}/{method}"
    data = urllib.parse.urlencode(params or {}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"ok": False, "description": f"HTTP {e.code}"}
    except Exception as e:
        return {"ok": False, "description": f"{type(e).__name__}: {e}"}


def send_text(token: str, chat_id: int, text: str) -> int | None:
    """보낸 메시지의 id 를 돌려준다 — 나중에 그 자리에서 고쳐 쓰기 위해서다.

    음성 문답 미러가 이걸 쓴다: 명령을 알아들은 즉시 한 줄 띄우고,
    답이 나오면 같은 메시지를 고쳐 답변을 채운다. 새 메시지를 또 보내면
    채팅이 두 배로 시끄러워진다.

    ⚠ 테스트가 돌 때는 보내지 않는다. **여기가 폰으로 나가는 유일한 문이다** —
      briefing._to_telegram, wakeup, 음성 미러가 전부 이 함수를 거친다.
      부르는 쪽마다 가드를 다는 방식으로는 언젠가 한 곳이 빠지고, 실제로
      빠졌다: 2026-08-15 06:17~06:47, call_notes.save() 에 붙인 공유 링크
      전송이 test_call_notes 를 타고 나가 "📞 통화 정리" 가 폰에 반복해서
      떴다. 위키 경로가 임시폴더(/var/folders/.../tmpXXXX/)라 사장님이
      이상함을 알아채셨다. 스위트를 돌린 횟수만큼 나갔다.
      한 곳을 막으면 앞으로 누가 무슨 알림을 새로 붙여도 새지 않는다.
    """
    if os.path.basename(sys.argv[0] or "").startswith("test_"):
        return None
    # 텔레그램 메시지 상한은 4096자
    res = api(token, "sendMessage", {"chat_id": chat_id, "text": text[:4000]})
    try:
        return int(res["result"]["message_id"])
    except (KeyError, TypeError, ValueError):
        return None


def edit_text(token: str, chat_id: int, message_id: int, text: str) -> bool:
    """이미 보낸 메시지를 고쳐 쓴다. 실패해도 무해 — 원문은 남는다."""
    if os.path.basename(sys.argv[0] or "").startswith("test_"):
        return False
    res = api(token, "editMessageText",
              {"chat_id": chat_id, "message_id": message_id, "text": text[:4000]})
    return bool(res.get("ok"))


def send_voice(token: str, chat_id: int, text: str) -> bool:
    """답변을 Supertonic 으로 합성해 음성메시지로 보낸다. 실패해도 무해."""
    if os.path.basename(sys.argv[0] or "").startswith("test_"):
        return False
    try:
        import numpy as np
        import soundfile as sf

        import tts

        chunks = tts.split_sentences(text)
        if not chunks:
            return False
        parts = [a for a in (tts._synth(c) for c in chunks[:6]) if a is not None]
        if not parts:
            return False
        wav = np.concatenate(parts)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            out = f.name
        raw = out.replace(".ogg", ".wav")
        sf.write(raw, wav, config.TTS_SUPERTONIC_SR)
        # 텔레그램 음성메시지는 opus/ogg 를 요구한다
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", raw, "-c:a", "libopus", "-b:a", "32k", out],
            capture_output=True, timeout=90,
        )
        Path(raw).unlink(missing_ok=True)
        if r.returncode != 0 or not Path(out).exists():
            Path(out).unlink(missing_ok=True)
            return False

        ok = _upload_voice(token, chat_id, out)
        Path(out).unlink(missing_ok=True)
        return ok
    except Exception:
        return False


def _upload_voice(token: str, chat_id: int, path: str) -> bool:
    """multipart 업로드. 표준 라이브러리만 쓴다."""
    boundary = "----dongbaek" + str(int(time.time() * 1000))
    body = bytearray()

    def field(name, value):
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(f"{value}\r\n".encode())

    field("chat_id", chat_id)
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="voice"; filename="v.ogg"\r\n')
    body.extend(b"Content-Type: audio/ogg\r\n\r\n")
    body.extend(Path(path).read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        f"{API}/bot{token}/sendVoice", data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r).get("ok", False)
    except Exception:
        return False


def send_photo(token: str, chat_id: int, path: str, caption: str = "") -> bool:
    """사진 전송 — 뉴스 웹툰 등. _upload_voice 와 같은 multipart, 표준 라이브러리만."""
    if os.path.basename(sys.argv[0] or "").startswith("test_"):
        return False
    boundary = "----dongbaek" + str(int(time.time() * 1000))
    body = bytearray()

    def field(name, value):
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        body.extend(f"{value}\r\n".encode())

    field("chat_id", chat_id)
    if caption:
        field("caption", caption[:1000])
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="photo"; filename="p.png"\r\n')
    body.extend(b"Content-Type: image/png\r\n\r\n")
    body.extend(Path(path).read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())

    req = urllib.request.Request(
        f"{API}/bot{token}/sendPhoto", data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return json.load(r).get("ok", False)
    except Exception:
        return False


def download_voice(token: str, file_id: str) -> str | None:
    info = api(token, "getFile", {"file_id": file_id})
    if not info.get("ok"):
        return None
    path = info["result"].get("file_path")
    if not path:
        return None
    try:
        with urllib.request.urlopen(f"{API}/file/bot{token}/{path}", timeout=120) as r:
            data = r.read()
    except Exception:
        return None
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(data)
        return f.name


# ─────────────────────────────────────────────────────────
# 음성 → 텍스트
# ─────────────────────────────────────────────────────────
def transcribe(ogg_path: str) -> str | None:
    """ogg → wav 변환 후 whisper. 맥에서 로컬 처리라 토큰 0."""
    wav = ogg_path.replace(".ogg", ".wav")
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", ogg_path, "-ar", str(config.SAMPLE_RATE),
             "-ac", "1", wav],
            capture_output=True, timeout=120,
        )
        if r.returncode != 0:
            return None
        import audio as audio_mod

        return audio_mod.transcribe_file(wav)
    except Exception:
        return None
    finally:
        Path(wav).unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────
# 메시지 처리
# ─────────────────────────────────────────────────────────
def handle_command(text: str, chat_id: int, pending: dict) -> str:
    """동백 라우터로 넘긴다. 위험 게이트는 원격에서도 그대로 적용된다."""
    import dongbaek
    import router

    # 앞선 위험 명령에 대한 승인 답장인가
    if chat_id in pending:
        cmd, asked_at = pending[chat_id]
        if time.time() - asked_at > 180:
            del pending[chat_id]
        elif router.is_confirmation(text):
            del pending[chat_id]
            return dongbaek.handle(cmd, confirm=lambda c, h: True,
                                   source="telegram", heard=text)
        else:
            del pending[chat_id]
            return "취소했습니다."

    # 호출어는 있어도 되고 없어도 된다. 텔레그램은 이미 동백에게 보낸 것이므로.
    stripped = router.match_wake(text)
    command = stripped if stripped else text

    # 대기 중인 질문이 없는데 "진행" 만 왔다면 명령이 아니다.
    # 그대로 두면 "진행. 진행할까요?" 하고 되묻게 된다.
    if router.is_bare_response(command):
        return "지금은 확인을 기다리는 작업이 없습니다."

    hit = router.danger_hit(command)
    # 포괄 사유('확인이 필요한 요청')로만 걸린 건 명시적 위험이 아니다.
    # 음성 경로는 DEV_MODE 에서 이미 면제하는데(dongbaek.handle) 텔레그램만
    # 빠져 있었다. 그래서 명령도 아닌 되물음에까지 승인을 요구했다 — 실제로
    # "아니 지금 맥미니가 48기간데 그게 안된다고???" 가 게이트에 걸려
    # "'진행해' 라고 답장해 주세요" 가 나갔다 (사장님 지적, 두 번째).
    # 명시적 위험(배포·삭제·집행…)은 그대로 승인을 받는다.
    if config.DEV_MODE and hit == router.SAFE_ONLY_REASON:
        hit = None
    if hit:
        pending[chat_id] = (command, time.time())
        return (f"⚠ {command}\n\n"
                f"'{hit}' 가 포함된 되돌릴 수 없는 작업입니다. 진행할까요?\n"
                f"'진행해' 라고 답장해 주세요. (3분 내)")

    return dongbaek.handle(command, confirm=lambda c, h: False,
                           source="telegram", heard=text)


def process(token: str, msg: dict, allowed: list, pending: dict) -> None:
    chat_id = (msg.get("chat") or {}).get("id")
    if chat_id not in allowed:
        log(f"거부: 허용되지 않은 chat_id {chat_id}")
        return

    text = None
    if msg.get("voice"):
        v = msg["voice"]
        if v.get("duration", 0) > MAX_VOICE_SEC:
            send_text(token, chat_id, f"음성이 너무 깁니다 ({MAX_VOICE_SEC}초 이하로).")
            return
        send_text(token, chat_id, "🎧 듣는 중…")
        ogg = download_voice(token, v["file_id"])
        if not ogg:
            send_text(token, chat_id, "음성 파일을 받지 못했습니다.")
            return
        text = transcribe(ogg)
        Path(ogg).unlink(missing_ok=True)
        if not text:
            send_text(token, chat_id, "받아쓰기에 실패했습니다.")
            return
        send_text(token, chat_id, f"📝 {text}")
    elif msg.get("text"):
        text = msg["text"].strip()
    else:
        return

    if not text:
        return
    log(f"명령: {text!r}")
    _WORK.put((token, chat_id, text, pending))


# 처리는 따로 도는 스레드가 맡는다.
#
# 예전에는 폴링 루프가 직접 처리했다. 무거운 명령 하나가 3분을 쓰면 그동안
# 다음 메시지를 아예 받지 못해, 밖에서 보면 봇이 죽은 것처럼 보인다.
# 하나씩 순서대로 처리하는 건 그대로다 — 같은 Claude 세션을 쓰므로
# 나란히 부르면 세션 기록이 엉킨다.
_WORK: "queue.Queue" = queue.Queue()


def _run_jobs() -> None:
    while True:
        token, chat_id, text, pending = _WORK.get()
        try:
            reply = handle_command(text, chat_id, pending)
        except Exception as e:
            # 종류만 말하면("AttributeError") 사장님은 아무것도 알 수 없다.
            # 밖에 계실 땐 로그를 볼 수도 없으니 내용까지 보낸다.
            reply = f"처리 중 오류가 났습니다. {type(e).__name__}: {e}"
            log(f"오류: {type(e).__name__}: {e}")

        if not reply:
            reply = "(응답 없음)"
        try:
            send_text(token, chat_id, reply)
            send_voice(token, chat_id, reply)
        except Exception as e:
            log(f"답장 실패: {type(e).__name__}: {e}")

        # 동백이 자기 코드를 고쳤다면 답장을 다 보낸 뒤에 갈아탄다.
        try:
            import dongbaek

            dongbaek.restart_if_pending()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────
def main() -> int:
    conf = load_conf()
    if conf is None:
        print(f"설정이 없습니다: {CONF_FILE}")
        print('  {"bot_token": "...", "allowed_chat_ids": [123456789]}')
        return 2

    token = conf["bot_token"]
    allowed = [int(c) for c in conf["allowed_chat_ids"]]

    me = api(token, "getMe")
    if not me.get("ok"):
        print("봇 인증 실패:", me.get("description"))
        return 3
    uname = me["result"].get("username")
    log(f"봇 @{uname} 연결됨. 허용 chat_id: {allowed}")

    # 다른 프로그램이 같은 봇을 폴링 중이면 서로 메시지를 뺏는다.
    wh = api(token, "getWebhookInfo")
    if wh.get("ok") and wh["result"].get("url"):
        log("⚠ 이 봇에 웹훅이 걸려 있습니다. 폴링과 충돌합니다.")
        return 4

    offset = 0
    if OFFSET_FILE.exists():
        try:
            offset = int(OFFSET_FILE.read_text().strip())
        except ValueError:
            pass

    pending: dict = {}
    # 처리기를 띄운다. 이 스레드가 명령을 도맡으므로 아래 폴링은
    # Claude 응답을 기다리지 않고 계속 메시지를 받는다.
    threading.Thread(target=_run_jobs, daemon=True).start()
    log("대기 중. 텔레그램에서 음성이나 텍스트를 보내세요.")
    while True:
        r = api(token, "getUpdates",
                {"offset": offset, "timeout": POLL_TIMEOUT},
                timeout=POLL_TIMEOUT + 15)
        if not r.get("ok"):
            log(f"폴링 오류: {r.get('description')}")
            time.sleep(5)
            continue
        for u in r.get("result", []):
            offset = u["update_id"] + 1
            OFFSET_FILE.write_text(str(offset))
            msg = u.get("message") or u.get("edited_message")
            if msg:
                process(token, msg, allowed, pending)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[텔레그램] 종료")
        sys.exit(0)
