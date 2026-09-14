"""Deep history for MULTI-STRIKE Kalshi ladders (daily/weekly commodities + crypto).

collect_broad.py capped at 300 markets/series, which for a ladder with ~37
strikes per event is only 8 days of history - useless for testing a daily
strategy. This pages far deeper and keeps the event structure, because the
strike ladder is the point: it is what lets a breakout be expressed as a cheap
out-of-the-money contract with a convex payoff, which is how ORB actually earns
its living.

No external spot feed is needed. The ladder itself implies the underlying: the
strike whose price crosses 0.50 is the market's estimate of spot, so the
implied-spot path can be reconstructed from Kalshi data alone.
"""
import httpx, sys, io, json, os, time, datetime as dt, collections

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://api.elections.kalshi.com/trade-api/v2"
c = httpx.Client(timeout=60, headers={"Accept-Encoding": "gzip"})
OUT = "ladders"
os.makedirs(OUT, exist_ok=True)

SERIES = ["KXGOLDD", "KXSILVERD", "KXWTI", "KXNATGASD", "KXBRENTD", "KXAAAGASD",
          "KXGOLDW", "KXSILVERW", "KXWTIW", "KXBRENTW", "KXCOPPERW", "KXNATGASW",
          "KXBTCD", "KXETHD"]
MAX_PAGES = 14          # 200/page
MAX_MARKETS = 2600


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


for st in SERIES:
    path = os.path.join(OUT, f"{st}.json")
    if os.path.exists(path):
        print(f"{st}: cached"); continue

    markets, cursor = [], None
    for _ in range(MAX_PAGES):
        p = {"series_ticker": st, "status": "settled", "limit": 200}
        if cursor: p["cursor"] = cursor
        try:
            b = get("/markets", p)
        except Exception as e:
            print(f"{st}: page error {e}"); break
        markets.extend(b.get("markets", []))
        cursor = b.get("cursor")
        if not cursor or len(markets) >= MAX_MARKETS:
            break
    markets = [m for m in markets if m.get("result") in ("yes", "no")]
    if not markets:
        print(f"{st}: none"); continue

    events = collections.Counter(m.get("event_ticker") for m in markets)
    print(f"{st}: {len(markets)} markets across {len(events)} events")

    rows, skipped = [], 0
    for i, m in enumerate(markets):
        try:
            o = int(dt.datetime.fromisoformat(m["open_time"].replace("Z", "+00:00")).timestamp())
            cl = int(dt.datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp())
        except Exception:
            skipped += 1; continue
        dur_h = (cl - o) / 3600.0
        if dur_h <= 0 or dur_h > 24 * 20:
            skipped += 1; continue
        interval = 60 if dur_h <= 96 else 1440
        try:
            cs = get(f"/series/{st}/markets/{m['ticker']}/candlesticks",
                     {"start_ts": o - interval * 60, "end_ts": cl + interval * 60,
                      "period_interval": interval}).get("candlesticks", [])
        except Exception:
            skipped += 1; continue
        bars = []
        for k in cs:
            yb, ya = k.get("yes_bid", {}), k.get("yes_ask", {})
            bid, ask = f(yb.get("close_dollars")), f(ya.get("close_dollars"))
            if bid is None or ask is None:
                continue
            bars.append({"ts": k.get("end_period_ts"), "bid": bid, "ask": ask,
                         "vol": f(k.get("volume_fp"), 0.0)})
        if len(bars) < 2:
            skipped += 1; continue
        rows.append({
            "ticker": m["ticker"], "series": st, "event": m.get("event_ticker"),
            "open_ts": o, "close_ts": cl, "interval": interval,
            "strike": f(m.get("floor_strike")), "cap": f(m.get("cap_strike")),
            "strike_type": m.get("strike_type"),
            "result": m["result"], "volume": f(m.get("volume_fp") or m.get("volume"), 0.0),
            "bars": bars,
        })
        if (i + 1) % 300 == 0:
            print(f"   {st} {i+1}/{len(markets)}")
    json.dump(rows, open(path, "w"))
    days = len(set(dt.datetime.fromtimestamp(r["open_ts"], dt.timezone.utc).date() for r in rows))
    print(f"{st:<14} wrote {len(rows):>5} markets, {len(set(r['event'] for r in rows)):>4} events, "
          f"{days:>3} distinct days ({skipped} skipped)")

print("\nDONE")
