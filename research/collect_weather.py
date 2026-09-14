"""Kalshi daily temperature ladders — the market I recommended on day one and
never tested.

Why it is the best remaining candidate:
  * DAILY markets, so execution latency is irrelevant (the phone/API speed
    problem that killed the 15-minute strategy does not exist here)
  * they settle on official NWS observations, a public quantitative source
  * they are strike LADDERS, so the market publishes a full implied
    distribution over tomorrow's high temperature, which can be compared
    against what actually happened
  * temperature is genuinely forecastable, unlike a 15-minute crypto return
"""
import httpx, sys, io, json, os, time, datetime as dt, collections

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://api.elections.kalshi.com/trade-api/v2"
c = httpx.Client(timeout=60, headers={"Accept-Encoding": "gzip"})
OUT = "weather"
os.makedirs(OUT, exist_ok=True)

SERIES = ["KXHIGHNY", "KXHIGHMIA", "KXHIGHCHI", "KXHIGHTPHX", "KXHIGHTDAL",
          "KXHIGHTHOU", "KXHIGHPHIL", "KXHIGHTBOS", "KXHIGHDEN", "KXHIGHTOKC",
          "KXHIGHTDC", "KXHIGHTSATX", "KXLOWTPHX", "KXLOWTAUS"]
MAX_PAGES = 10


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
            print(f"{st}: {e}"); break
        markets.extend(b.get("markets", []))
        cursor = b.get("cursor")
        if not cursor: break
    markets = [m for m in markets if m.get("result") in ("yes", "no")]
    if not markets:
        print(f"{st}: none"); continue

    rows, skipped = [], 0
    for i, m in enumerate(markets):
        try:
            o = int(dt.datetime.fromisoformat(m["open_time"].replace("Z", "+00:00")).timestamp())
            cl = int(dt.datetime.fromisoformat(m["close_time"].replace("Z", "+00:00")).timestamp())
        except Exception:
            skipped += 1; continue
        dur_h = (cl - o) / 3600.0
        if dur_h <= 0 or dur_h > 24 * 10:
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
            "ticker": m["ticker"], "series": st,
            "event": m.get("event_ticker") or m["ticker"].rsplit("-", 1)[0],
            "open_ts": o, "close_ts": cl, "interval": interval,
            # Temperature ladders use floor/cap: the contract is a BUCKET
            # ("high between 78 and 79"), not a simple threshold, so both
            # edges matter and must be kept.
            "strike": f(m.get("floor_strike")), "cap": f(m.get("cap_strike")),
            "strike_type": m.get("strike_type"),
            "subtitle": m.get("yes_sub_title") or m.get("subtitle") or "",
            "result": m["result"], "volume": f(m.get("volume_fp") or m.get("volume"), 0.0),
            "expiration_value": f(m.get("expiration_value")),
            "bars": bars,
        })
        if (i + 1) % 200 == 0:
            print(f"   {st} {i+1}/{len(markets)}")
    json.dump(rows, open(path, "w"))
    ev = len(set(r["event"] for r in rows))
    days = len(set(dt.datetime.fromtimestamp(r["open_ts"], dt.timezone.utc).date() for r in rows))
    types = collections.Counter(r["strike_type"] for r in rows)
    print(f"{st:<14} {len(rows):>5} markets, {ev:>4} events, {days:>3} days, types={dict(types)}")

print("\nDONE")
