"""Coinbase public market-data websocket feed (no API key required).

Subscribes to the `ticker` channel on Coinbase's public Exchange feed and calls
`on_price(product_id, price, size)` on every price update (size = last trade
size, used for volume logging). It auto-reconnects on drop
or staleness so the bot can run unattended for weeks. Only public market data is
used here — order execution lives in portfolio.py and uses the authenticated
Advanced Trade REST API instead.
"""
import json
import threading
import time

import websocket  # pip install websocket-client


class TickerFeed:
    def __init__(self, ws_url, products, on_price, channel="ticker",
                 staleness_timeout_s=90):
        self.ws_url = ws_url
        self.products = products
        self.on_price = on_price
        self.channel = channel
        self.staleness_timeout_s = staleness_timeout_s
        self.last_msg_at = time.monotonic()
        self._stop = False
        self._ws = None

    # ---------- websocket callbacks ----------
    def _on_open(self, ws):
        ws.send(json.dumps({
            "type": "subscribe",
            "product_ids": self.products,
            "channels": [self.channel],
        }))
        print(f"[feed] connected - subscribed to {len(self.products)} products")

    def set_products(self, new_products):
        """Swap the subscribed product set at runtime (for a dynamic universe).
        Diffs against the current set and sends subscribe/unsubscribe frames on
        the live socket. If the socket isn't open, the new set is stored and the
        next (re)connect subscribes to it via _on_open."""
        new = list(dict.fromkeys(new_products))          # de-dupe, preserve order
        old = set(self.products)
        added = [p for p in new if p not in old]
        removed = [p for p in old if p not in new]
        self.products = new
        ws = self._ws
        if ws and (added or removed):
            try:
                if added:
                    ws.send(json.dumps({"type": "subscribe",
                                        "product_ids": added, "channels": [self.channel]}))
                if removed:
                    ws.send(json.dumps({"type": "unsubscribe",
                                        "product_ids": removed, "channels": [self.channel]}))
            except Exception:
                pass                                     # next reconnect resubscribes to self.products
        return added, removed

    def _on_message(self, ws, message):
        self.last_msg_at = time.monotonic()
        msg = json.loads(message)
        if msg.get("type") != "ticker":
            return
        product = msg.get("product_id")
        price = msg.get("price")
        size = msg.get("last_size")
        if product and price is not None:
            self.on_price(product, float(price), float(size) if size else 0.0)

    def _on_error(self, ws, error):
        print(f"[feed] error: {error}")

    def _on_close(self, ws, status, msg):
        print(f"[feed] disconnected ({status})")

    # ---------- staleness watchdog ----------
    def _watchdog(self):
        """Force a reconnect if no message has arrived for too long. A silent,
        half-open socket is the classic way an unattended feed quietly dies."""
        while not self._stop:
            time.sleep(5)
            if self._ws and time.monotonic() - self.last_msg_at > self.staleness_timeout_s:
                print("[feed] stale - forcing reconnect")
                try:
                    self._ws.close()
                except Exception:
                    pass

    # ---------- run loop ----------
    def run(self):
        threading.Thread(target=self._watchdog, daemon=True).start()
        while not self._stop:
            self._ws = websocket.WebSocketApp(
                self.ws_url,
                on_open=self._on_open,
                on_message=self._on_message,
                on_error=self._on_error,
                on_close=self._on_close,
            )
            self.last_msg_at = time.monotonic()
            self._ws.run_forever(ping_interval=20, ping_timeout=10)
            if self._stop:
                break
            print("[feed] reconnecting in 5s...")
            time.sleep(5)

    def stop(self):
        self._stop = True
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
