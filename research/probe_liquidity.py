"""Ground truth: do the 15-min markets actually TRADE? Use the trades tape."""
import httpx, sys, io, datetime as dt, collections, statistics

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = "https://api.elections.kalshi.com/trade-api/v2"
c = httpx.Client(timeout=40, headers={"Accept-Encoding": "gzip"})

now = dt.datetime.now(dt.timezone.utc)
print(f"UTC now {now.isoformat()}  weekday={now.strftime('%A')}")
et = now - dt.timedelta(hours=4)
print(f"ET approx {et.strftime('%Y-%m-%d %H:%M %A')}\n")

# Trades tape for the last 24h, per series
since = int((now - dt.timedelta(hours=24)).timestamp())
for st in ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M",
           "KXGOLD15M", "KXSILVER15M", "KXWTI15M", "KXINX15M", "KXNDQ15M"]:
    # find recent markets in this series (any status)
    tickers = []
    for status in ["settled", "closed", "open", "unopened"]:
        try:
            r = c.get(f"{BASE}/markets", params={"series_ticker": st, "status": status, "limit": 200})
            for m in r.json().get("markets", []):
                tickers.append((m.get("close_time") or "", m["ticker"], status, m.get("volume")))
        except Exception as e:
            pass
    if not tickers:
        print(f"{st:12s} NO MARKETS AT ALL")
        continue
    tickers.sort(reverse=True)
    statuses = collections.Counter(t[2] for t in tickers)
    vols = [t[3] for t in tickers if t[3] is not None]
    print(f"{st:12s} {len(tickers)} markets  statuses={dict(statuses)}")
    if vols:
        vols_s = sorted(vols)
        print(f"             volume: n={len(vols)} median={statistics.median(vols_s):.0f} mean={statistics.mean(vols_s):.0f} max={max(vols_s)}")
    # sample the 3 most recent non-open markets for trade prints
    shown = 0
    for close_t, tk, status, vol in tickers:
        if status == "unopened" or shown >= 3:
            continue
        try:
            tr = c.get(f"{BASE}/markets/trades", params={"ticker": tk, "limit": 1000}).json().get("trades", [])
        except Exception as e:
            tr = []
        if tr:
            px = [t.get("yes_price") for t in tr if t.get("yes_price") is not None]
            cnt = sum(t.get("count", 0) for t in tr)
            print(f"             {tk} [{status}] close={close_t[:16]} prints={len(tr)} contracts={cnt}"
                  f" px_range={min(px)}-{max(px)}" if px else f"             {tk} prints={len(tr)}")
        else:
            print(f"             {tk} [{status}] close={close_t[:16]} NO PRINTS  (vol field={vol})")
        shown += 1
    print()
