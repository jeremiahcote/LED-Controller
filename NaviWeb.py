"""Navi's web interface: a phone-friendly page plus a small JSON API.

Runs on a background thread inside the voice service, so Bluetooth stays owned
by a single process. Requests only queue commands; the main loop runs them the
same way as spoken ones.

API (all require the token, as "Authorization: Bearer <token>" or ?token=):
  GET  /api/state                       strips, shutdown countdown, colour names
  POST /api/lights  {"target": "both|bed|wall", "power": "on|off",
                     "color": "<name>" or [r, g, b]}
  POST /api/pc      {"action": "on|shutdown|cancel"}
"""
import hmac
import json
import os
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = 8765
TOKEN_PATH = os.path.expanduser("~/.config/navi/web_token")
PAGE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "navi_web.html")

TARGETS = {"both": "both", "bed": "led1", "wall": "led2"}
PC_ACTIONS = ("on", "shutdown", "cancel")
MAX_BODY = 4096


def load_or_create_token():
    try:
        with open(TOKEN_PATH, encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token
    except OSError:
        pass
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    token = secrets.token_urlsafe(18)
    fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    return token


def _parse_color(value, colors):
    if isinstance(value, str) and value.lower() in colors:
        return tuple(colors[value.lower()])
    if (isinstance(value, list) and len(value) == 3
            and all(isinstance(c, int) and 0 <= c <= 255 for c in value)):
        return tuple(value)
    return None


def start(submit, get_state, wake_pc, colors, port=PORT):
    """Serve the page and API on a daemon thread.

    submit(command) queues a command tuple for the main loop; get_state()
    returns {"strips": {...}, "shutdown_pending": bool}.
    """
    token = load_or_create_token()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body, content_type="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _authorized(self):
            header = self.headers.get("Authorization", "")
            supplied = header[len("Bearer "):] if header.startswith("Bearer ") else ""
            if not supplied:
                supplied = parse_qs(urlparse(self.path).query).get("token", [""])[0]
            return hmac.compare_digest(supplied.encode(), token.encode())

        def _read_json(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return None
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return None
            return body if isinstance(body, dict) else None

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/":
                with open(PAGE_PATH, "rb") as f:
                    self._send(HTTPStatus.OK, f.read(), "text/html; charset=utf-8")
                return
            if not self._authorized():
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "missing or wrong token"})
                return
            if path == "/api/state":
                self._send(HTTPStatus.OK, {**get_state(), "colors": colors})
                return
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):
            path = urlparse(self.path).path
            if not self._authorized():
                self._send(HTTPStatus.UNAUTHORIZED, {"error": "missing or wrong token"})
                return
            body = self._read_json()
            if body is None:
                self._send(HTTPStatus.BAD_REQUEST, {"error": "expected a JSON object"})
                return

            if path == "/api/lights":
                target = TARGETS.get(str(body.get("target", "both")).lower())
                power = str(body.get("power", "on")).lower()
                if target is None or power not in ("on", "off"):
                    self._send(HTTPStatus.BAD_REQUEST,
                               {"error": "target must be both/bed/wall, power on/off"})
                    return
                if power == "off":
                    rgb = (0, 0, 0)
                elif "color" in body:
                    rgb = _parse_color(body["color"], colors)
                    if rgb is None:
                        self._send(HTTPStatus.BAD_REQUEST, {
                            "error": "color must be one of the names in /api/state "
                                     "or [r, g, b] with 0-255 values"})
                        return
                else:
                    rgb = get_state()["last_color"][target]
                submit(("lights", power, rgb, target))
                self._send(HTTPStatus.ACCEPTED, {"queued": "lights", "target": target,
                                                 "power": power, "rgb": list(rgb)})
                return

            if path == "/api/pc":
                action = str(body.get("action", "")).lower()
                if action not in PC_ACTIONS:
                    self._send(HTTPStatus.BAD_REQUEST, {"error": "action must be on/shutdown/cancel"})
                    return
                if action == "on":
                    wake_pc()
                else:
                    submit((action,))
                self._send(HTTPStatus.ACCEPTED, {"queued": f"pc {action}"})
                return

            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Web interface on port {port}")
    return server
