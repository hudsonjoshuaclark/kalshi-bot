"""Deep price history for the coins Kalshi lists perpetual futures on.

The key structural advantage over everything tested so far: a perpetual future
tracks spot, so a trend/momentum strategy on KXBTCPERP can be backtested
against YEARS of BTC price history. The venue only sets the cost model. That is
the opposite of Kalshi's binary markets, where each contract had its own short
life and the entire research programme was strangled by having 2-3 months of
data when it needed years.

Coinbase serves ~300 candles per request, so history is walked in windows.
Binance is geo-blocked from the US, so Coinbase is the source.
"""
import httpx, sys, io, json, os, time, datetime as dt

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
OUT = "perp_history"
os.makedirs(OUT, exist_ok=True)
c = httpx.Client(timeout=60, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})

# Coins Kalshi offers perps on that also have real Coinbase history.
COINS = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "LINK-USD",
         "LTC-USD", "BCH-USD", "ADA-USD", "DOT-USD", "NEAR-USD", "AAVE-USD",
         "SUI-USD", "HBAR-USD"]

GRANS = {86400: ("1d", dt.datetime(2016, 1, 1, tzinfo=dt.timezone.utc)),
         3600:  ("1h", dt.datetime(2021, 1, 1, tzinfo=dt.timezone.utc))}


def fetch(prod, gran, start, end, tries=4):
    for i in range(tries):
        try:
            r = c.get(f"https://api.exchange.coinbase.com/products/{prod}/candles",
                      params={"granularity": gran, "start": start.isoformat(), "end": end.isoformat()})
            if r.status_code == 429:
                time.sleep(1.2 * (i + 1)); continue
            r.raise_for_status()
            j = r.json()
            return j if isinstance(j, list) else []
        except Exception:
            if i == tries - 1:
                return []
            time.sleep(0.6 * (i + 1))
    return []


now = dt.datetime.now(dt.timezone.utc)
for gran, (label, since) in GRANS.items():
    for prod in COINS:
        path = os.path.join(OUT, f"{prod}_{label}.json")
        if os.path.exists(path):
            print(f"{prod} {label}: cached"); continue
        bars, cur, empty = {}, since, 0
        step = dt.timedelta(seconds=gran * 295)
        while cur < now:
            end = min(cur + step, now)
            rows = fetch(prod, gran, cur, end)
            if rows:
                empty = 0
                for t, lo, hi, op, cl, vol in rows:
                    bars[int(t)] = {"open": op, "high": hi, "low": lo, "close": cl, "vol": vol}
            else:
                empty += 1
                if empty > 40 and not bars:
                    break          # product probably did not exist yet
            cur = end
            time.sleep(0.16)
        if not bars:
            print(f"{prod} {label}: no data"); continue
        json.dump(bars, open(path, "w"))
        ks = sorted(bars)
        print(f"{prod:<10}{label}: {len(bars):>7} bars  "
              f"{dt.datetime.fromtimestamp(ks[0], dt.timezone.utc).date()} -> "
              f"{dt.datetime.fromtimestamp(ks[-1], dt.timezone.utc).date()}")

print("\nDONE")
