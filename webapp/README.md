# Trading Desk PWA (private, self-hosted, live)

An installable web app of the trading console, served privately from the droplet
with automatic HTTPS and a password. It refreshes itself — no republishing.

## How it works
- **build_page.py** runs on the droplet (by cron) → reads all three bots' local
  state files + live Coinbase candles → injects the data into `dashboard/dashboard.html`
  → writes `index.html` here, wrapped as a standalone PWA (manifest, Apple meta,
  a `<head>`, and a "reload every 5 min while visible" script).
- **Caddy** serves this folder over HTTPS at `https://165-227-84-219.sslip.io`
  (a free wildcard-DNS hostname that resolves to the droplet), behind `basic_auth`.
- **cron** re-runs build_page.py every few minutes, so the page stays current; the
  open app reloads on its interval to pick it up.

## Files
- `build_page.py` — the page builder (runs on the droplet)
- `manifest.json`, `icon-180/192/512.png` — PWA install metadata + app icon
- `Caddyfile` — web-server config template (real password hash lives only on the droplet)

## One-time droplet setup
1. `apt install caddy` (official repo), open firewall 80 + 443.
2. `mkdir -p /opt/crypto-agent/webapp` (git pull already provides it).
3. `python3 build_page.py` once to create `index.html`.
4. Put your password hash in `/etc/caddy/Caddyfile` (`caddy hash-password`), reload Caddy.
5. cron: `*/5 * * * * cd /opt/crypto-agent/webapp && /opt/crypto-agent/swing_bot/venv/bin/python build_page.py >/dev/null 2>&1`

## Install on iPhone
Open the URL in Safari → Share → Add to Home Screen. It launches fullscreen as an app.
