"""Kalshi candles for settled 15-min COMMODITY markets (closed for the weekend,
so this is last week's tape)."""
import httpx, sys, io, json, time, os, datetime as dt

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
BASE = "https://api.elections.kalshi.com/trade-api/v2"
c = httpx.Client(timeout=45, headers={"Accept-Encoding": "gzip"})
OUT = "tape2"
os.makedirs(OUT, exist_ok=True)


def get(url, params, tries=5):
    for i in range(tries):
        try:
            r = c.get(url, params=params)
            if r.status_code == 429:
                time.sleep(1.2 * (i + 1)); continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(0.6 * (i + 1))
    return {}


def d(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


for st in ["KXGOLD15M", "KXSILVER15M", "KXWTI15M"]:
    path = os.path.join(OUT, f"{st}.json")
    if os.path.exists(path):
        print(f"{st}: cached"); continue
    markets, cursor = [], None
    for _ in range(2):
        p = {"series_ticker": st, "status": "settled", "limit": 200}
        if cursor: p["cursor"] = cursor
        b = get(f"{BASE}/markets", p)
        markets.extend(b.get("markets", []))
        cursor = b.get("cursor")
        if not cursor: break
    markets = [m for m in markets if m.get("result") in ("yes", "no")]
    print(f"{st}: {len(markets)} settled")
    rows = []
    for i, m in enumerate(markets):
        tk = m["ticker"]
        try:
            s = int(dt.datetime.fromisoformat(m["open_time"].replace("Z", "+00:00")).timestamp())
            e = int(dt.datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp())
        except Exception:
            continue
        try:
            cs = get(f"{BASE}/series/{st}/markets/{tk}/candlesticks",
                     {"start_ts": s - 60, "end_ts": e + 60, "period_interval": 1}).get("candlesticks", [])
        except Exception:
            continue
        bars = []
        for k in cs:
            px, yb, ya = k.get("price", {}), k.get("yes_bid", {}), k.get("yes_ask", {})
            bars.append({"ts": k.get("end_period_ts"),
                         "close": d(px.get("close_dollars")), "mean": d(px.get("mean_dollars")),
                         "bid": d(yb.get("close_dollars")), "ask": d(ya.get("close_dollars")),
                         "vol": d(k.get("volume_fp"), 0.0), "oi": d(k.get("open_interest_fp"), 0.0)})
        rows.append({"ticker": tk, "series": st, "open_ts": s, "close_ts": e,
                     "strike": d(m.get("floor_strike")),
                     "expiration_value": d(m.get("expiration_value")),
                     "result": m["result"],
                     "volume": d(m.get("volume_fp") or m.get("volume"), 0.0),
                     "bars": bars})
        if (i + 1) % 50 == 0:
            print(f"   {st} {i+1}/{len(markets)}")
    json.dump(rows, open(path, "w"))
    print(f"{st}: wrote {len(rows)}")
print("DONE")
