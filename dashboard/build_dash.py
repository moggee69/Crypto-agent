import json, re, time, os, sys, csv
import _paths                       # puts swing_bot/ on sys.path, resolves DATA_DIR/DASH_HTML
import strategy as swstrat
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import requests

OUT = _paths.DASH_HTML          # the persistent dashboard source this rewrites
SC = _paths.DATA_DIR            # the bots' live files ({tag}_portfolio.json / _trades.csv / _equity.csv)
WATCH = ["XLM", "HBAR", "XRP", "AVAX", "LINK", "ONDO", "FLR", "HYPE"]   # Agent 002
A3 = ["BTC", "SOL", "ETH", "ZEC", "ADA", "SUI", "LTC", "ICP"]          # Agent 003
ALL = WATCH + A3
ENTRY_DAY = int(datetime(2026, 8, 31, tzinfo=timezone.utc).timestamp()) // 86400 * 86400  # live go-live day
CB = "https://api.exchange.coinbase.com/products/{}-USD/candles"


def cb(sym, gran, days):
    """Paged Coinbase candles -> [[t, o, h, l, c, v]] oldest-first (reordered from
    Coinbase's [t, low, high, open, close, volume])."""
    bars = {}
    cur_end = datetime.now(timezone.utc)
    remaining = days
    step = 295 if gran == 86400 else 12          # 300-candle cap: 295d daily, 12d hourly
    while remaining > 0:
        chunk = min(step, remaining)
        start = cur_end - timedelta(days=chunk)
        for _ in range(3):
            try:
                r = requests.get(CB.format(sym), params={"granularity": gran,
                                 "start": start.isoformat(), "end": cur_end.isoformat()}, timeout=20)
                if r.status_code == 200:
                    for t, lo, hi, o, c, v in r.json():
                        bars[int(t)] = [int(t), o, hi, lo, c, round(v)]
                    break
            except Exception:
                time.sleep(0.5)
        cur_end = start
        remaining -= chunk
        time.sleep(0.05)
    return [bars[t] for t in sorted(bars)]


print("Fetching Coinbase candles for 16 coins...")
daily = {s: cb(s, 86400, 90) for s in ALL}
hourly = {s: cb(s, 3600, 18) for s in ALL}

# ---------- equity / positions / ledger — from the bots' LIVE files (portfolio + CSV logs) ----------
prices = {s: (daily[s][-1][4] if daily[s] else None) for s in ALL}
_ep = lambda s: int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def parse_note(note):
    px = re.search(r"@\s*([0-9.]+)", note); fee = re.search(r"fee\s*([0-9.]+)", note); pn = re.search(r"pnl\s*([-+]?[0-9.]+)", note)
    return (float(px.group(1)) if px else None, float(fee.group(1)) if fee else 0.0, float(pn.group(1)) if pn else None)


def bot_data(tag, botcode, poscode):
    port = json.load(open(os.path.join(SC, f"{tag}_portfolio.json")))
    rows = list(csv.DictReader(open(os.path.join(SC, f"{tag}_equity.csv"))))
    eq = [[_ep(r["timestamp_utc"]), round(float(r["equity"]), 2)] for r in rows]
    # LIVE mark-to-market (holdings x current price + cash) — matches the exchange now,
    # vs the bot's equity log which marks to the last COMPLETED daily close.
    start = float(port.get("start_capital", 500.0))
    live = 0.0
    for prod, c in port.get("coins", {}).items():
        live += c.get("cash", 0.0)
        if c.get("holding"):
            s = prod.replace("-USD", "")
            live += c.get("qty", 0.0) * (prices.get(s) or c.get("buy_price", 0.0))
    cur = round(live, 2)
    pnl = round((live / start - 1) * 100, 2)
    eq.append([int(time.time()), cur])            # live tail so the curve ends "now"
    p = []
    for prod, c in port.get("coins", {}).items():
        if c.get("holding"):
            s = prod.replace("-USD", ""); now = prices.get(s) or c["buy_price"]
            p.append({"bot": poscode, "coin": s, "buy": round(c["buy_price"], 6), "cur": round(now, 6), "pnl": round((now / c["buy_price"] - 1) * 100, 1)})
    lg = []
    for r in csv.reader(open(os.path.join(SC, f"{tag}_trades.csv"))):
        if not r or r[0] == "timestamp_utc":
            continue
        t, a, prod, note = r[0], r[1], r[2], r[4] if len(r) > 4 else ""
        px, fee, pn = parse_note(note)
        lg.append({"t": _ep(t), "bot": botcode, "a": a, "c": prod.replace("-USD", ""), "px": px, "fee": round(fee, 2), "pnl": (round(pn, 2) if pn is not None else None)})
    return ({"eq": eq, "cur": round(cur, 2), "pnl": round(pnl, 2)}, p, lg,
            {"tr": len(lg), "fee": round(port.get("fees_paid", 0), 2)}, port)


sw, pos_sw, ledg_sw, act_sw, SWPORT = bot_data("sw", "SW", "Swing")
sl, pos_sl, ledg_sl, act_sl, SLPORT = bot_data("sl", "SL", "Stop-loss")
a4, pos_a4, ledg_a4, act_a4, A4PORT = bot_data("a4", "A4", "Top8")
pos = pos_sl + pos_sw + pos_a4
ledger = ledg_sl + ledg_sw + ledg_a4
ledger.sort(key=lambda x: x["t"])
tr = [{"t": l["t"], "bot": l["bot"], "a": l["a"], "p": l["c"], "pnl": l["pnl"]} for l in ledger][-10:]
act = {"sl": act_sl, "sw": act_sw, "a4": act_a4}

# ---------- benchmark: $500 equal-weight hold of ALL 16, from the mid-July baseline ----------
per = 500 / len(ALL)
dclose = {s: {row[0] // 86400 * 86400: row[4] for row in daily[s]} for s in ALL}


def start_price(s):
    if ENTRY_DAY in dclose[s]:
        return dclose[s][ENTRY_DAY]
    later = [d for d in sorted(dclose[s]) if d >= ENTRY_DAY]
    return dclose[s][later[0]] if later else None


start_px = {s: start_price(s) for s in ALL}
days_sorted = sorted({d for s in ALL for d in dclose[s] if d >= ENTRY_DAY})
bench = []
for d in days_sorted:
    vals = [per * dclose[s][d] / start_px[s] for s in ALL if d in dclose[s] and start_px[s]]
    if len(vals) == len(ALL):
        bench.append([d, round(sum(vals), 2)])

# ---------- sparklines + signal radar (per bot) ----------
SWSTATE = SWPORT          # live portfolio (holdings + armed flags)
A3STATE = SLPORT
A4STATE = A4PORT


def build_radar(coins, state):
    sp, pr = {}, []
    for s in coins:
        cl = [row[4] for row in daily[s]]
        sp[s] = [round(v, 6) for v in cl[-30:]]
        rss = swstrat.rsi(cl)
        macd, sig = swstrat.macd_lines(cl)
        rsi_now = rss[-1] if (rss and rss[-1] is not None) else 50
        cst = state.get("coins", {}).get(s + "-USD", {})
        held, armed = cst.get("holding", False), cst.get("armed", False)
        if held and armed:
            st = "armed"
        elif held:
            st = "riding"
        elif swstrat.macd_bull_cross(macd, sig) and rsi_now < 40:
            st = "primed"
        else:
            st = "watching"
        peak = cst.get("peak_price", 0) or 0
        target = round(peak * 0.9, 6) if (held and peak) else None   # 10% fade sell trigger
        pr.append({"c": s, "px": round(cl[-1], 6), "rsi": round(rsi_now),
                   "held": held, "armed": armed, "st": st,
                   "peak": round(peak, 6) if peak else 0, "target": target})
    return sp, pr


spark, prox = build_radar(WATCH, SWSTATE)      # Agent 002
spark3, prox3 = build_radar(A3, A3STATE)       # Agent 003
spark4, prox4 = build_radar(["ZEC", "SOL", "LINK", "ETH", "XRP", "ADA", "HYPE", "BTC"], A4STATE)  # Agent 004

# ---------- candles straight from Coinbase (always current), all 16 coins ----------
candles = {}
for s in ALL:
    h4 = {}
    for t, o, h, l, c, v in hourly[s]:
        b = t // 14400 * 14400
        if b not in h4:
            h4[b] = [b, o, h, l, c, v]
        else:
            x = h4[b]; x[2] = max(x[2], h); x[3] = min(x[3], l); x[4] = c; x[5] += v
    candles[s] = {"D": daily[s][-60:], "H4": [h4[b] for b in sorted(h4)], "H1": hourly[s][-192:]}

DATA = {"sl": sl, "sw": sw, "a4": a4, "pos": pos, "tr": tr, "prox": prox, "prox3": prox3, "prox4": prox4,
        "spark": spark, "spark3": spark3, "spark4": spark4, "bench": bench, "act": act,
        "ledger": ledger, "candles": candles}

# ---------- inject ---------- (OUT is the persistent clean source)
h = open(OUT, encoding="utf-8").read()
i = h.find("const DATA=")
depth = 0; instr = False; esc = False; start = None; end = None
for j in range(i, len(h)):
    c = h[j]
    if start is None:
        if c == "{":
            start = j; depth = 1
        continue
    if esc:
        esc = False; continue
    if c == "\\":
        esc = True; continue
    if c == '"':
        instr = not instr; continue
    if instr:
        continue
    if c == "{":
        depth += 1
    elif c == "}":
        depth -= 1
        if depth == 0:
            end = j + 1; break
h = h[:start] + json.dumps(DATA) + h[end:]
stamp = datetime.now(ZoneInfo("Europe/London")).strftime("%d %b %Y %H:%M %Z")  # BST/GMT
h = re.sub(r"(\$\('updated'\)\.textContent=)[^;]+;", r"\1'updated " + stamp + "';", h, count=1)
open(OUT, "w", encoding="utf-8").write(h)
print("done. size", round(len(h) / 1024, 1), "KB")
print(f"sl(003) {sl['cur']} {sl['pnl']}% | sw(002) {sw['cur']} {sw['pnl']}% | bench pts {len(bench)} | candles {len(candles)} coins")
print("prox 002:", [(p['c'], p['rsi'], p['st']) for p in prox[:3]])
print("prox3 003:", [(p['c'], p['rsi'], p['st']) for p in prox3[:3]])
