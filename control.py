"""제어 서버 — 푸시투토크 트리거 + 아이폰 텍스트 직송.

보안 원칙:
  - 기본은 127.0.0.1 바인딩. 이 맥 밖에서는 접속 자체가 안 된다.
  - 모든 요청에 토큰 필수. 토큰이 틀리면 404 (엔드포인트 존재조차 숨김).
  - 위험 명령은 이 경로로도 우회할 수 없다. 반드시 confirm 을 한 번 더 받는다.
"""

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import config


def get_token() -> str:
    if config.TOKEN_FILE.exists():
        tok = config.TOKEN_FILE.read_text().strip()
        if tok:
            return tok
    tok = secrets.token_urlsafe(24)
    config.TOKEN_FILE.write_text(tok)
    config.TOKEN_FILE.chmod(0o600)
    return tok


class _Handler(BaseHTTPRequestHandler):
    hooks: dict = {}
    token: str = ""

    # 접속 로그로 콘솔이 더러워지는 걸 막는다
    def log_message(self, fmt, *args):
        pass

    # ── 공통 ──────────────────────────────────────────────
    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self, params: dict) -> bool:
        given = (params.get("token") or [""])[0]
        header = self.headers.get("X-Dongbaek-Token", "")
        # 타이밍 공격 방지
        return secrets.compare_digest(given or header, self.token)

    def _read_body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if n <= 0 or n > 64_000:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw)
        except ValueError:
            return {"text": raw.decode("utf-8", "replace")}

    # ── 라우팅 ────────────────────────────────────────────
    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def _route(self):
        u = urlparse(self.path)
        params = parse_qs(u.query)
        if not self._authed(params):
            self._send(404, {"error": "not found"})
            return

        path = u.path.rstrip("/") or "/"
        body = self._read_body() if self.command == "POST" else {}

        if path == "/status":
            self._send(200, self.hooks["status"]())
            return

        if path == "/ptt":
            self.hooks["ptt"]()
            self._send(200, {"ok": True, "listening_sec": config.PTT_WINDOW_SEC})
            return

        if path == "/say":
            text = body.get("text") or (params.get("text") or [""])[0]
            if not text:
                self._send(400, {"error": "text 없음"})
                return
            self.hooks["say"](text)
            self._send(200, {"ok": True})
            return

        if path == "/command":
            text = body.get("text") or (params.get("text") or [""])[0]
            confirm = body.get("confirm") or (params.get("confirm") or [""])[0]
            if not text:
                self._send(400, {"error": "text 없음"})
                return
            result = self.hooks["command"](text, confirm)
            self._send(result.pop("_code", 200), result)
            return

        self._send(404, {"error": "not found"})


def start(hooks: dict) -> tuple[str, int, str]:
    """제어 서버를 백그라운드로 띄우고 (host, port, token) 반환."""
    token = get_token()
    _Handler.hooks = hooks
    _Handler.token = token
    srv = ThreadingHTTPServer((config.CONTROL_HOST, config.CONTROL_PORT), _Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return config.CONTROL_HOST, config.CONTROL_PORT, token
