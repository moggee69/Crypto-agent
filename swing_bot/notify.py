"""Optional push notifications via ntfy.sh.

Enabled by setting the NTFY_TOPIC environment variable (kept out of the repo —
it's set in a private file on the server). If unset, notifications are silently
skipped. A notification failure never affects trading.
"""
import os

import requests


def push(title: str, message: str, tags: str = ""):
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return
    try:
        headers = {"Title": title}
        if tags:
            headers["Tags"] = tags
        requests.post(f"https://ntfy.sh/{topic}",
                      data=message.encode("utf-8"), headers=headers, timeout=10)
    except Exception:
        pass
