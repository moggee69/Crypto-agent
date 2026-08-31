"""Tiny control API for the Trading Desk PWA — lets the app freeze/resume a bot
(create/remove its HALT kill-switch file) from the phone.

Bound to localhost only; Caddy reverse-proxies /api/* to it and enforces the same
password/cookie as the rest of the site, so this service does no auth of its own.
Only the three known bots are addressable — no arbitrary paths.

    GET  /api/status        -> {"bots": {"002": <halted?>, "003": ..., "004": ...}}
    POST /api/halt/<bot>    -> create the HALT file  (bot stops placing orders)
    POST /api/resume/<bot>  -> remove the HALT file  (bot resumes)
"""
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

BOTS = {
    "002": "/opt/crypto-agent/swing_bot/HALT",
    "003": "/opt/crypto-agent/agent003/HALT",
    "004": "/opt/crypto-agent/agent004/HALT",
}
PORT = 8899


def halted():
    return {b: os.path.exists(p) for b, p in BOTS.items()}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/api/status":
            self._send(200, {"bots": halted()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        parts = self.path.strip("/").split("/")     # api / halt|resume / <bot>
        if len(parts) == 3 and parts[0] == "api" and parts[1] in ("halt", "resume") and parts[2] in BOTS:
            path = BOTS[parts[2]]
            if parts[1] == "halt":
                open(path, "w").close()               # touch -> freeze
            else:
                try:
                    os.remove(path)                   # resume
                except FileNotFoundError:
                    pass
            self._send(200, {"bot": parts[2], "halted": os.path.exists(path)})
        else:
            self._send(400, {"error": "bad request"})

    def log_message(self, *a):
        pass   # quiet


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
