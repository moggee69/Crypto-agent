"""Build the self-hosted PWA page from LIVE bot data — runs ON the droplet (by
cron), so it reads each bot's local state files directly (no SSH) plus live
Coinbase candles, injects the data into the existing dashboard template, and wraps
it as a standalone installable web app (adds a proper <head> with the PWA manifest,
Apple meta, and a periodic auto-refresh).

Single source of truth: the render code + CSS come from dashboard/dashboard.html,
so the hosted app never diverges from the artifact dashboard.
"""
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

ROOT = "/opt/crypto-agent"
sys.path.insert(0, os.path.join(ROOT, "swing_bot"))
import strategy as swstrat  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(ROOT, "dashboard", "dashboard.html")
OUT = os.path.join(HERE, "index.html")

WATCH = ["XLM", "HBAR", "XRP", "AVAX", "LINK", "ONDO", "FLR", "HYPE"]   # 002
A3 = ["BTC", "SOL", "ETH", "ZEC", "ADA", "SUI", "LTC", "ICP"]          # 003 (LIVE)
A4C = ["ZEC", "SOL", "LINK", "ETH", "XRP", "ADA", "HYPE", "BTC"]       # 004
ALL = WATCH + A3
ENTRY_DAY = int(datetime(2026, 8, 31, tzinfo=timezone.utc).timestamp()) // 86400 * 86400  # live go-live day
CB = "https://api.exchange.coinbase.com/products/{}-USD/candles"

# per-bot: (local dir, ledger code, positions code)
BOTS = {"sw": (os.path.join(ROOT, "swing_bot"), "SW", "Swing"),
        "sl": (os.path.join(ROOT, "agent003"), "SL", "Stop-loss"),
        "a4": (os.path.join(ROOT, "agent004"), "A4", "Top8")}


def cb(sym, gran, days):
    bars = {}
    cur_end = datetime.now(timezone.utc)
    rem = days
    step = 295 if gran == 86400 else 12
    while rem > 0:
        chunk = min(step, rem)
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
        rem -= chunk
        time.sleep(0.05)
    return [bars[t] for t in sorted(bars)]


print("fetching candles...")
daily = {s: cb(s, 86400, 90) for s in ALL}
hourly = {s: cb(s, 3600, 18) for s in ALL}
prices = {s: (daily[s][-1][4] if daily[s] else None) for s in ALL}
_ep = lambda s: int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def parse_note(note):
    px = re.search(r"@\s*([0-9.]+)", note); fee = re.search(r"fee\s*([0-9.]+)", note); pn = re.search(r"pnl\s*([-+]?[0-9.]+)", note)
    return (float(px.group(1)) if px else None, float(fee.group(1)) if fee else 0.0, float(pn.group(1)) if pn else None)


def bot_data(botdir, botcode, poscode):
    port = json.load(open(os.path.join(botdir, "swing_portfolio.json")))
    eqf = os.path.join(botdir, "swing_equity.csv")
    rows = list(csv.DictReader(open(eqf))) if os.path.exists(eqf) else []
    eq = [[_ep(r["timestamp_utc"]), round(float(r["equity"]), 2)] for r in rows]
    # LIVE mark-to-market from holdings + current prices — matches the exchange NOW.
    # (The bot's own equity log marks to the last COMPLETED daily close, so it lags.)
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
            p.append({"bot": poscode, "coin": s, "buy": round(c["buy_price"], 6),
                      "cur": round(now, 6), "pnl": round((now / c["buy_price"] - 1) * 100, 1)})
    lg = []
    tf = os.path.join(botdir, "swing_trades.csv")
    if os.path.exists(tf):
        for r in csv.reader(open(tf)):
            if not r or r[0] == "timestamp_utc":
                continue
            t, a, prod, note = r[0], r[1], r[2], r[4] if len(r) > 4 else ""
            px, fee, pn = parse_note(note)
            lg.append({"t": _ep(t), "bot": botcode, "a": a, "c": prod.replace("-USD", ""),
                       "px": px, "fee": round(fee, 2), "pnl": (round(pn, 2) if pn is not None else None)})
    act = {"tr": len(lg), "fee": round(port.get("fees_paid", 0), 2)}
    return ({"eq": eq, "cur": round(cur, 2), "pnl": round(pnl, 2)}, p, lg, act, port)


sw, pos_sw, ledg_sw, act_sw, SWP = bot_data(*BOTS["sw"])
sl, pos_sl, ledg_sl, act_sl, SLP = bot_data(*BOTS["sl"])
a4, pos_a4, ledg_a4, act_a4, A4P = bot_data(*BOTS["a4"])
pos = pos_sl + pos_sw + pos_a4
ledger = ledg_sl + ledg_sw + ledg_a4
ledger.sort(key=lambda x: x["t"])
tr = [{"t": l["t"], "bot": l["bot"], "a": l["a"], "p": l["c"], "pnl": l["pnl"]} for l in ledger][-10:]
act = {"sw": act_sw, "sl": act_sl, "a4": act_a4}

dclose = {s: {row[0] // 86400 * 86400: row[4] for row in daily[s]} for s in ALL}
# Buy & hold = the two live bots' ACTUAL opening purchases, held forever. bench_basis.json
# is a frozen snapshot of 002+003's real seed holdings (same coins, same fill prices, same
# quantities, same leftover cash), so the benchmark is IDENTICAL to 002+003 combined until
# the bots actually trade — then it stays put while they move. Regenerate only to re-baseline.
_basis = json.load(open(os.path.join(ROOT, "bench_basis.json")))
BH = _basis["holdings"]                                       # {coin: {qty, cash}}
BH_CASH = sum(h.get("cash", 0.0) for h in BH.values())
days_sorted = sorted({d for s in BH if s in dclose for d in dclose[s] if d >= ENTRY_DAY})
bench = []
for d in days_sorted:
    vals = [BH[s]["qty"] * dclose[s][d] for s in BH if s in dclose and d in dclose[s]]
    if len(vals) == len(BH):
        bench.append([d, round(sum(vals) + BH_CASH, 2)])


def build_radar(coins, state):
    sp, pr = {}, []
    for s in coins:
        cl = [row[4] for row in daily[s]]
        sp[s] = [round(v, 6) for v in cl[-30:]]
        rss = swstrat.rsi(cl); macd, sig = swstrat.macd_lines(cl)
        rsi_now = rss[-1] if (rss and rss[-1] is not None) else 50
        cst = state.get("coins", {}).get(s + "-USD", {})
        held, armed = cst.get("holding", False), cst.get("armed", False)
        st = "armed" if (held and armed) else "riding" if held else \
            ("primed" if (swstrat.macd_bull_cross(macd, sig) and rsi_now < 40) else "watching")
        peak = cst.get("peak_price", 0) or 0
        target = round(peak * 0.9, 6) if (held and peak) else None   # 10% fade sell trigger
        pr.append({"c": s, "px": round(cl[-1], 6), "rsi": round(rsi_now),
                   "held": held, "armed": armed, "st": st,
                   "peak": round(peak, 6) if peak else 0, "target": target})
    return sp, pr


spark, prox = build_radar(WATCH, SWP)
spark3, prox3 = build_radar(A3, SLP)
spark4, prox4 = build_radar(A4C, A4P)

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


def build_logging():
    """Per-coin summary of the minute-bar price database (stoploss_bot/minute_data/):
    row count, first/last logged minute, latest close, and one close-per-day for charts."""
    mdir = os.path.join(ROOT, "stoploss_bot", "minute_data")
    out = []
    for s in ALL:
        path = os.path.join(mdir, f"{s}-USD.csv")
        rows = 0; first = None; last_ts = None; last_close = None; day = {}
        if os.path.exists(path):
            with open(path) as f:
                r = csv.reader(f); next(r, None)
                for row in r:
                    if len(row) < 5:
                        continue
                    try:
                        ts = int(datetime.fromisoformat(row[0]).timestamp())
                        close = float(row[4])
                    except (ValueError, IndexError):
                        continue
                    rows += 1
                    if first is None:
                        first = ts
                    last_ts = ts; last_close = close
                    day[ts // 86400 * 86400] = round(close, 6)
        out.append({"c": s, "rows": rows, "first": first, "last": last_ts,
                    "px": last_close, "daily": [[d, day[d]] for d in sorted(day)]})
    return out


DATA = {"sl": sl, "sw": sw, "a4": a4, "pos": pos, "tr": tr, "prox": prox, "prox3": prox3,
        "prox4": prox4, "spark": spark, "spark3": spark3, "spark4": spark4, "bench": bench,
        "act": act, "ledger": ledger, "candles": candles, "logging": build_logging()}

# ---- inject DATA into the dashboard template (brace-matched swap) ----
h = open(TEMPLATE, encoding="utf-8").read()
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
h = re.sub(r"(\$\('updated'\)\.textContent=)[^;]+;", r"\1'updated " + stamp + " · live';", h, count=1)

# ---- wrap the template (title+style ... markup ... script) as a standalone PWA ----
sp = h.find("</style>") + len("</style>")
head_part, body_part = h[:sp], h[sp:]
HEAD = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
    '<meta name="apple-mobile-web-app-capable" content="yes">'
    '<meta name="mobile-web-app-capable" content="yes">'
    '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
    '<meta name="apple-mobile-web-app-title" content="Trading Desk">'
    '<meta name="theme-color" content="#000000">'
    '<link rel="manifest" href="manifest.json">'
    '<link rel="apple-touch-icon" href="icon-180.png">'
    '<style>html{background:#000}body{padding-top:env(safe-area-inset-top)}</style>'
)
RELOAD = '<script>setTimeout(function(){if(!document.hidden)location.reload()},300000);</script>'
EXTRAS = r'''
<div id="ptr" style="position:fixed;top:0;left:0;right:0;height:0;display:flex;align-items:center;justify-content:center;color:#9ea9ff;font:12px/1 ui-monospace,monospace;overflow:hidden;transition:height .12s;z-index:9999;background:rgba(0,0,0,.55);pointer-events:none"></div>
<script>
(function(){
  var ptr=document.getElementById("ptr"), sy=0, pulling=false, TH=70;
  function top(){ return (document.scrollingElement||document.documentElement).scrollTop; }
  addEventListener("touchstart", function(e){ if(top()<=0){ sy=e.touches[0].clientY; pulling=true; } }, {passive:true});
  addEventListener("touchmove", function(e){ if(!pulling) return; var d=e.touches[0].clientY-sy;
    if(d>0){ var h=Math.min(d*0.5,84); ptr.style.height=h+"px"; ptr.textContent=h>=TH?"↑ release to refresh":"↓ pull to refresh"; }
    else { ptr.style.height="0"; } }, {passive:true});
  addEventListener("touchend", function(){ if(!pulling) return; pulling=false;
    var h=parseFloat(ptr.style.height)||0;
    if(h>=TH){ ptr.textContent="refreshing…"; location.reload(); } else { ptr.style.height="0"; } }, {passive:true});
})();
</script>'''
CONTROLS = r'''
<div id="ctrlWrap" style="position:fixed;top:calc(8px + env(safe-area-inset-top));left:10px;z-index:9998;font:11px/1.2 ui-monospace,monospace">
  <button id="ctrlToggle" style="background:rgba(20,28,52,.9);border:1px solid #2a355c;border-radius:20px;padding:6px 13px;color:#9ea9ff;font:inherit;cursor:pointer;-webkit-backdrop-filter:blur(4px);backdrop-filter:blur(4px)">controls</button>
  <div id="ctrlPanel" style="display:none;margin-top:8px;background:rgba(20,28,52,.95);border:1px solid #2a355c;border-radius:12px;padding:10px 12px;min-width:186px;-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px);color:#9ea9ff"></div>
</div>
<script>
(function(){
  var names={"002":"Utility 002","003":"Blue-chip 003","004":"All-stars 004"};
  var toggle=document.getElementById("ctrlToggle"), panel=document.getElementById("ctrlPanel");
  if(document.cookie.indexOf("desk_role=view")>=0){   // read-only visitor: no controls
    document.getElementById("ctrlWrap").innerHTML='<div style="background:rgba(20,28,52,.9);border:1px solid #2a355c;border-radius:20px;padding:6px 13px;color:#9ea9ff;font:11px/1.2 ui-monospace,monospace">\u{1F441} read only</div>';
    return;
  }
  function refresh(){
    fetch("/api/status",{cache:"no-store"}).then(function(r){return r.json();}).then(function(d){
      panel.innerHTML=Object.keys(names).map(function(b){
        var halted=!!(d.bots&&d.bots[b]);
        return '<div style="display:flex;justify-content:space-between;align-items:center;gap:14px;padding:5px 0">'
          +'<span'+(halted?' style="color:#f4726a"':'')+'>'+names[b]+(halted?" · frozen":"")+'</span>'
          +'<button data-b="'+b+'" data-h="'+halted+'" style="border:1px solid '+(halted?"#4ade80":"#f4726a")+';background:transparent;color:'+(halted?"#4ade80":"#f4726a")+';border-radius:12px;padding:3px 11px;font:inherit;cursor:pointer">'+(halted?"resume":"freeze")+'</button></div>';
      }).join("")+'<div style="border-top:1px solid #2a355c;margin-top:9px;padding-top:9px;display:flex;flex-direction:column;gap:6px">'
        +'<button id="sharebtn" style="width:100%;border:1px solid #2a355c;background:transparent;color:#4ade80;border-radius:12px;padding:6px;font:inherit;cursor:pointer">share (read-only link)</button>'
        +'<button id="signout" style="width:100%;border:1px solid #2a355c;background:transparent;color:#9ea9ff;border-radius:12px;padding:6px;font:inherit;cursor:pointer">sign out</button></div>';
      panel.querySelectorAll("button[data-b]").forEach(function(btn){
        btn.onclick=function(){
          var b=btn.dataset.b, halted=btn.dataset.h==="true", act=halted?"resume":"halt";
          var code=prompt((halted?"Resume ":"Freeze ")+names[b]+" — enter passcode:");
          if(code===null) return;                         // cancelled
          if(code!=="6791"){ alert("Wrong passcode."); return; }
          btn.textContent="…";
          fetch("/api/"+act+"/"+b,{method:"POST"}).then(function(r){return r.json();}).then(function(){refresh();})
            .catch(function(){alert("Control failed - try again.");refresh();});
        };
      });
      var so=document.getElementById("signout");
      if(so) so.onclick=function(){ fetch("/api/logout",{method:"POST"}).then(function(){location.href="/login.html";}).catch(function(){location.href="/login.html";}); };
      var sb=document.getElementById("sharebtn");
      if(sb) sb.onclick=function(){ sb.textContent="…";
        fetch("/api/sharelink").then(function(r){return r.json();}).then(function(d){
          if(d&&d.link){
            var ok=function(){ sb.textContent="link copied ✓"; setTimeout(function(){sb.textContent="share (read-only link)";},2500); };
            if(navigator.clipboard&&navigator.clipboard.writeText) navigator.clipboard.writeText(d.link).then(ok,function(){prompt("Copy this read-only link:",d.link);sb.textContent="share (read-only link)";});
            else { prompt("Copy this read-only link:",d.link); sb.textContent="share (read-only link)"; }
          } else { sb.textContent="share unavailable"; }
        }).catch(function(){ sb.textContent="share failed"; }); };
    }).catch(function(){panel.innerHTML='<div style="color:#f4726a">control API offline</div>';});
  }
  toggle.onclick=function(){ var open=panel.style.display!=="none"; panel.style.display=open?"none":"block"; if(!open) refresh(); };
})();
</script>'''
doc = HEAD + head_part + "</head><body>" + body_part + EXTRAS + CONTROLS + RELOAD + "</body></html>"

tmp = OUT + ".tmp"
open(tmp, "w", encoding="utf-8").write(doc)
os.replace(tmp, OUT)
print(f"wrote {OUT} ({len(doc)//1024} KB) · sl {sl['cur']} sw {sw['cur']} a4 {a4['cur']} @ {stamp}")
