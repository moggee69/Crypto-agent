"""Control + login/share API for the Trading Desk PWA (localhost; Caddy proxies /api/*).

    POST /api/login   {password, remember} -> full-access login cookie (desk_auth)
    POST /api/logout                        -> clears all cookies
    GET  /api/enter?v=TOKEN                  -> read-only login (sets desk_view), PUBLIC
    GET  /api/sharelink                      -> {link} for the read-only share link (admin)
    GET  /api/status                         -> {"bots": {...}}
    POST /api/halt|resume/<bot>              -> kill-switch (admin)

Admin (full) actions are enforced by Caddy (require desk_auth). Read-only visitors
(desk_view) can view the page + /api/status only.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

BOTS = {
    "002": "/opt/crypto-agent/swing_bot/HALT",
    "003": "/opt/crypto-agent/agent003/HALT",
    "004": "/opt/crypto-agent/agent004/HALT",
}
HASH_FILE = "/opt/crypto-agent/webapp_pw.hash"          # bcrypt hash of the site password
TOKEN_FILE = "/opt/crypto-agent/remember_token.txt"     # full-access cookie value
VIEW_TOKEN_FILE = "/opt/crypto-agent/view_token.txt"    # read-only cookie value
HOST_URL = "https://165-227-84-219.sslip.io"
PORT = 8899


def halted():
    return {b: os.path.exists(p) for b, p in BOTS.items()}


def _read(path):
    try:
        return open(path).read().strip()
    except OSError:
        return ""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj, cookies=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for c in (cookies or []):
            self.send_header("Set-Cookie", c)
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location, cookies=None):
        self.send_response(302)
        self.send_header("Location", location)
        for c in (cookies or []):
            self.send_header("Set-Cookie", c)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path == "/api/status":
            self._send(200, {"bots": halted()})
        elif path == "/api/sharelink":
            t = _read(VIEW_TOKEN_FILE)
            self._send(200, {"link": (HOST_URL + "/api/enter?v=" + t) if t else ""})
        elif path == "/api/enter":
            tok = (parse_qs(urlparse(self.path).query).get("v") or [""])[0]
            real = _read(VIEW_TOKEN_FILE)
            if real and tok == real:
                self._redirect("/", [
                    "desk_view=%s; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=31536000" % real,
                    "desk_role=view; Path=/; Secure; SameSite=Lax; Max-Age=31536000",
                ])
            else:
                self._redirect("/login.html")
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        p = self.path.rstrip("/")
        if p == "/api/login":
            return self._login()
        if p == "/api/logout":
            clr = lambda n, ho=True: "%s=; Path=/; Secure;%s SameSite=Lax; Max-Age=0" % (n, " HttpOnly;" if ho else "")
            return self._send(200, {"ok": True}, [clr("desk_auth"), clr("desk_view"), clr("desk_role", False)])
        parts = self.path.strip("/").split("/")            # api / halt|resume / <bot>
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
            ok = bool(pw) and bcrypt.checkpw(pw.encode(), _read(HASH_FILE).encode())
        except Exception:
            ok = False
        if not ok:
            return self._send(401, {"ok": False})
        token = _read(TOKEN_FILE)
        maxage = "; Max-Age=31536000" if remember else ""
        cookie = f"desk_auth={token}; Path=/; Secure; HttpOnly; SameSite=Lax{maxage}"
        self._send(200, {"ok": True}, [cookie])

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
