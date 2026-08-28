"""Portable paths for the dashboard tooling.

Import this FIRST in every dashboard script. It puts the repo's swing_bot/ on
sys.path and resolves the data + html locations relative to this repo, so the
scripts run on any machine straight from a `git clone` — no edits needed.

- DASH_HTML : the dashboard source that build_dash.py reads and rewrites
              (<repo>/dashboard/dashboard.html).
- DATA_DIR  : where the bots' {tag}_portfolio.json / _trades.csv / _equity.csv
              and the baseline bundles live. Defaults to <repo>/dashboard/data/.
              Override with the DASH_DATA environment variable, e.g. point it at
              a folder where you've pulled the droplet's live files.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))     # <repo>/dashboard
REPO = os.path.dirname(HERE)                           # <repo>
SWING_BOT = os.path.join(REPO, "swing_bot")
if SWING_BOT not in sys.path:
    sys.path.insert(0, SWING_BOT)                      # so `import strategy` etc. resolve

DATA_DIR = os.environ.get("DASH_DATA") or os.path.join(HERE, "data")
os.makedirs(DATA_DIR, exist_ok=True)

DASH_HTML = os.path.join(HERE, "dashboard.html")
