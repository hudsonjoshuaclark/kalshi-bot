"""1-minute Pyth history for the commodity 15M settlement indices, plus a check
that the series we pull actually reproduces Kalshi's published strikes.

Kalshi settles KXGOLD15M / KXSILVER15M / KXWTI15M on Pyth index feeds. Pyth's
TradingView shim does not expose the exact `*.Index.*` symbols Kalshi names, so
we take the closest public feed and then VERIFY it against floor_strike rather
than assuming. A backtest priced off the wrong index is worse than no backtest.
"""
import httpx, json, os, sys, io, time, math, statistics, datetime as dt

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
OUT = "tape2"
c = httpx.Client(timeout=45, headers={"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})

CANDIDATES = {
    "KXGOLD15M": ["Metal.XAU/USD"],
    "KXSILVER15M": ["Metal.XAG/USD"],
    "KXWTI15M": ["Commodities.WTIV6/USD", "Commodities.USOILSPOT"],
}


def history(symbol, frm, to):
    bars = {}
    step = 6 * 3600
    cur = frm
    while cur < to:
        end = min(cur + step, to)
        for attempt in range(4):
            try:
                r = c.get("https://benchmarks.pyth.network/v1/shims/tradingview/history",
                          params={"symbol": symbol, "resolution": "1", "from": cur, "to": end})
                j = r.json()
                if j.get("s") == "ok":
                    for t, o, h, l, cl in zip(j["t"], j["o"], j["h"], j["l"], j["c"]):
                        bars[int(t)] = {"open": o, "high": h, "low": l, "close": cl}
                break
            except Exception:
                if attempt == 3:
                    pass
                time.sleep(0.5 * (attempt + 1))
        cur = end
        time.sleep(0.15)
    return bars


for series, syms in CANDIDATES.items():
    path = os.path.join(OUT, f"{series}.json")
    if not os.path.exists(path):
        print(f"{series}: no Kalshi tape, skip")
        continue
    rows = json.load(open(path))
    frm = min(r["open_ts"] for r in rows) - 600
    to = max(r["close_ts"] for r in rows) + 600

    best = None
    for sym in syms:
        bars = history(sym, frm, to)
        if not bars:
            print(f"  {sym}: no bars")
            continue
        # The strike is the mean index over the 60s before the window opened, so
        # the close of the minute ending at open_ts is the tightest single-bar
        # proxy. Score on relative error against Kalshi's own number.
        errs = []
        for r in rows:
            k = r.get("strike")
            if not k:
                continue
            px = bars.get(r["open_ts"] - 60, {}).get("close")
            if px:
                errs.append(abs(px - k) / k)
        if not errs:
            print(f"  {sym}: no overlap")
            continue
        med = statistics.median(errs)
        print(f"  {sym:26s} n={len(errs):4d} median |err| = {med*100:.4f}%  p90={sorted(errs)[int(len(errs)*0.9)]*100:.4f}%")
        if best is None or med < best[1]:
            best = (sym, med, bars)

    if not best:
        print(f"{series}: NO USABLE INDEX")
        continue
    sym, med, bars = best
    out = os.path.join(OUT, f"pyth_{series}.json")
    json.dump({"symbol": sym, "median_strike_err": med, "bars": {str(k): v for k, v in bars.items()}},
              open(out, "w"))
    verdict = "USABLE" if med < 0.0005 else ("MARGINAL" if med < 0.002 else "TOO FAR OFF - do not backtest on this")
    print(f"{series}: chose {sym}, median strike error {med*100:.4f}%  -> {verdict}\n")
