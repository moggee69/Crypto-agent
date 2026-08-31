"""Control + login API for the Trading Desk PWA (localhost only; Caddy proxies /api/*).

    POST /api/login        {password, remember} -> verifies the password (bcrypt) and,
                           on success, sets the desk_auth login cookie (long-lived if
                           remember, session-only otherwise). This backs the custom
                           login page, replacing HTTP basic-auth.
    GET  /api/status       -> {"bots": {"002": <halted?>, ...}}   (behind the cookie)
    POST /api/halt/<bot>   -> create the HALT kill-switch file
    POST /api/resume/<bot> -> remove it

Only the three known bots are addressable — no arbitrary paths.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

BOTS = {
    "002": "/opt/crypto-agent/swing_bot/HALT",
    "003": "/opt/crypto-agent/agent003/HALT",
    "004": "/opt/crypto-agent/agent004/HALT",
}
HASH_FILE = "/opt/crypto-agent/webapp_pw.hash"        # bcrypt hash of the site password
TOKEN_FILE = "/opt/crypto-agent/remember_token.txt"   # the desk_auth cookie value
PORT = 8899


def halted():
    return {b: os.path.exists(p) for b, p in BOTS.items()}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj, cookie=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/api/status":
            self._send(200, {"bots": halted()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") == "/api/login":
            return self._login()
        parts = self.path.strip("/").split("/")           # api / halt|resume / <bot>
        if len(parts) == 3 and parts[0] == "api" and parts[1] in ("halt", "resume") and parts[2] in BOTS:
            path = BOTS[parts[2]]
            if parts[1] == "halt":
                open(path, "w").close()
            else:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            self._send(200, {"bot": parts[2], "halted": os.path.exists(path)})
        else:
            self._send(400, {"error": "bad request"})

    def _login(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send(400, {"ok": False})
        pw = str(data.get("password", ""))
        remember = bool(data.get("remember"))
        try:
            import bcrypt
            stored = open(HASH_FILE, "rb").read().strip()
            ok = bool(pw) and bcrypt.checkpw(pw.encode(), stored)
        except Exception:
            ok = False
        if not ok:
            return self._send(401, {"ok": False})
        token = open(TOKEN_FILE).read().strip()
        maxage = "; Max-Age=31536000" if remember else ""    # else a session cookie
        cookie = f"desk_auth={token}; Path=/; Secure; HttpOnly; SameSite=Lax{maxage}"
        self._send(200, {"ok": True}, cookie=cookie)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
