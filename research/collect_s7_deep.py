"""Deep history for the S7 volatility test — the only strategy still standing.

S7's weakness is not its logic, it is its sample: one week of held-out data,
which is why a raw p=0.0074 dies under a x24 correction. Kalshi has ~9 weeks of
settled 15-minute history reachable (6,000 markets/series back to 2026-06-29),
so the test set can be roughly 6-9x larger.

Coinbase serves at most ~300 candles per request, so 1-minute spot has to be
walked in 5-hour windows.
"""
import httpx, sys, io, json, os, time, datetime as dt

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://api.elections.kalshi.com/trade-api/v2"
k = httpx.Client(timeout=60, headers={"Accept-Encoding": "gzip"})
cb = httpx.Client(timeout=60, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
OUT = "deep"
os.makedirs(OUT, exist_ok=True)

SERIES = {"KXBTC15M": "BTC-USD", "KXETH15M": "ETH-USD", "KXSOL15M": "SOL-USD",
          "KXXRP15M": "XRP-USD", "KXDOGE15M": "DOGE-USD"}
MAX_PAGES = 30


def get(cl, url, params, tries=5):
    for i in range(tries):
        try:
            r = cl.get(url, params=params)
            if r.status_code == 429:
                time.sleep(1.5 * (i + 1)); continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(0.7 * (i + 1))
    return None


def f(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ---- Kalshi markets + candles ---------------------------------------------
for st in SERIES:
    path = os.path.join(OUT, f"{st}.json")
    if os.path.exists(path):
        print(f"{st}: cached"); continue
    markets, cursor = [], None
    for _ in range(MAX_PAGES):
        p = {"series_ticker": st, "status": "settled", "limit": 200}
        if cursor: p["cursor"] = cursor
        b = get(k, BASE + "/markets", p)
        if not b: break
        markets.extend(b.get("markets", []))
        cursor = b.get("cursor")
        if not cursor: break
    markets = [m for m in markets if m.get("result") in ("yes", "no") and m.get("floor_strike")]
    print(f"{st}: {len(markets)} settled markets, fetching candles...")

    rows = []
    for i, m in enumerate(markets):
        try:
            o = int(dt.datetime.fromisoformat(m["open_time"].replace("Z", "+00:00")).timestamp())
            c = int(dt.datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp())
        except Exception:
            continue
        if not (0 < c - o <= 3600):
            continue
        cs = get(k, f"{BASE}/series/{st}/markets/{m['ticker']}/candlesticks",
                 {"start_ts": o - 60, "end_ts": c + 60, "period_interval": 1})
        if not cs:
            continue
        bars = []
        for b in cs.get("candlesticks", []):
            yb, ya = b.get("yes_bid", {}), b.get("yes_ask", {})
            bid, ask = f(yb.get("close_dollars")), f(ya.get("close_dollars"))
            if bid is None or ask is None:
                continue
            bars.append({"ts": b.get("end_period_ts"), "bid": bid, "ask": ask,
                         "vol": f(b.get("volume_fp"), 0.0)})
        if len(bars) < 3:
            continue
        rows.append({"ticker": m["ticker"], "series": st, "open_ts": o, "close_ts": c,
                     "strike": f(m.get("floor_strike")), "result": m["result"],
                     "volume": f(m.get("volume_fp") or m.get("volume"), 0.0), "bars": bars})
        if (i + 1) % 500 == 0:
            print(f"   {st} {i+1}/{len(markets)}")
    json.dump(rows, open(path, "w"))
    days = len(set(dt.datetime.fromtimestamp(r["open_ts"], dt.timezone.utc).date() for r in rows))
    print(f"{st}: wrote {len(rows)} markets over {days} days")

# ---- Coinbase 1-minute spot over the same span ----------------------------
allrows = []
for st in SERIES:
    p = os.path.join(OUT, f"{st}.json")
    if os.path.exists(p):
        allrows.extend(json.load(open(p)))
if not allrows:
    print("no markets; stopping"); raise SystemExit
lo = min(r["open_ts"] for r in allrows) - 3600
hi = max(r["close_ts"] for r in allrows) + 3600
print(f"\nspot span {dt.datetime.fromtimestamp(lo, dt.timezone.utc)} -> "
      f"{dt.datetime.fromtimestamp(hi, dt.timezone.utc)} ({(hi-lo)/86400:.1f} days)")

for st, prod in SERIES.items():
    sp = os.path.join(OUT, f"spot_{prod}.json")
    if os.path.exists(sp):
        print(f"  {prod}: cached"); continue
    bars, cur, chunks = {}, lo, 0
    while cur < hi:                      # 300 candles max => 5-hour windows
        end = min(cur + 300 * 60, hi)
        try:
            j = get(cb, f"https://api.exchange.coinbase.com/products/{prod}/candles",
                    {"granularity": 60,
                     "start": dt.datetime.fromtimestamp(cur, dt.timezone.utc).isoformat(),
                     "end": dt.datetime.fromtimestamp(end, dt.timezone.utc).isoformat()})
            for row in (j or []):
                bars[int(row[0])] = {"low": row[1], "high": row[2], "open": row[3],
                                     "close": row[4], "vol": row[5]}
        except Exception:
            pass
        cur = end; chunks += 1
        if chunks % 100 == 0:
            print(f"    {prod} {len(bars)} minutes so far")
        time.sleep(0.18)
    json.dump(bars, open(sp, "w"))
    print(f"  {prod}: {len(bars)} spot minutes")

print("\nDONE")
