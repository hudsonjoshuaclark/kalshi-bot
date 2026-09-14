"""Collect candles across every liquid Kalshi crypto + commodity series.

Selection is by liquidity and history, from universe.json, not by hand-picking -
hand-picking the series to study is itself a form of overfitting.

Candle resolution scales with market duration so a weekly market does not
produce 10,080 one-minute bars: 1min for <=2h markets, 60min for <=4d, else
1440min.
"""
import httpx, sys, io, json, os, time, datetime as dt

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://api.elections.kalshi.com/trade-api/v2"
c = httpx.Client(timeout=60, headers={"Accept-Encoding": "gzip"})
OUT = "broad"
os.makedirs(OUT, exist_ok=True)

MIN_SETTLED = 50
MIN_TOTAL_VOL = 200_000
MAX_MARKETS = 300


def get(path, params=None, tries=5):
    for i in range(tries):
        try:
            r = c.get(BASE + path, params=params or {})
            if r.status_code == 429:
                time.sleep(1.5 * (i + 1)); continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(0.7 * (i + 1))
    return {}


def f(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


universe = json.load(open("universe.json"))
picked = [u for u in universe
          if u["settled"] >= MIN_SETTLED and u["total_vol"] >= MIN_TOTAL_VOL]
print(f"{len(picked)} series pass the liquidity filter "
      f"(>={MIN_SETTLED} settled, >={MIN_TOTAL_VOL:,.0f} volume)\n")

for u in picked:
    st = u["ticker"]
    path = os.path.join(OUT, f"{st}.json")
    if os.path.exists(path):
        print(f"{st}: cached"); continue

    markets, cursor = [], None
    for _ in range(2):
        p = {"series_ticker": st, "status": "settled", "limit": 200}
        if cursor: p["cursor"] = cursor
        b = get("/markets", p)
        markets.extend(b.get("markets", []))
        cursor = b.get("cursor")
        if not cursor: break
    markets = [m for m in markets if m.get("result") in ("yes", "no")][:MAX_MARKETS]
    if not markets:
        print(f"{st}: no settled"); continue

    rows, skipped = [], 0
    for i, m in enumerate(markets):
        try:
            o = int(dt.datetime.fromisoformat(m["open_time"].replace("Z", "+00:00")).timestamp())
            cl = int(dt.datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp())
        except Exception:
            skipped += 1; continue
        dur_h = (cl - o) / 3600.0
        if dur_h <= 0 or dur_h > 24 * 40:
            skipped += 1; continue
        interval = 1 if dur_h <= 2 else (60 if dur_h <= 96 else 1440)
        try:
            cs = get(f"/series/{st}/markets/{m['ticker']}/candlesticks",
                     {"start_ts": o - interval * 60, "end_ts": cl + interval * 60,
                      "period_interval": interval}).get("candlesticks", [])
        except Exception:
            skipped += 1; continue
        bars = []
        for k in cs:
            px, yb, ya = k.get("price", {}), k.get("yes_bid", {}), k.get("yes_ask", {})
            bid, ask = f(yb.get("close_dollars")), f(ya.get("close_dollars"))
            if bid is None or ask is None:
                continue
            bars.append({"ts": k.get("end_period_ts"), "bid": bid, "ask": ask,
                         "close": f(px.get("close_dollars")),
                         "vol": f(k.get("volume_fp"), 0.0),
                         "oi": f(k.get("open_interest_fp"), 0.0)})
        if len(bars) < 3:
            skipped += 1; continue
        rows.append({
            "ticker": m["ticker"], "series": st, "cat": u["cat"], "freq": u["freq"],
            "open_ts": o, "close_ts": cl, "interval": interval,
            "strike": f(m.get("floor_strike")), "cap": f(m.get("cap_strike")),
            "strike_type": m.get("strike_type"),
            "result": m["result"],
            "volume": f(m.get("volume_fp") or m.get("volume"), 0.0),
            "bars": bars,
        })
        if (i + 1) % 100 == 0:
            print(f"   {st} {i+1}/{len(markets)}")
    json.dump(rows, open(path, "w"))
    print(f"{st:<16} {len(rows):>4} markets  ({skipped} skipped)  {u['freq']}")

print("\nDONE")
