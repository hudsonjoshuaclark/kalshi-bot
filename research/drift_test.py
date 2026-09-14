"""The only positive backtest number was "buy YES early in the window".
Is that an edge, or just the fact that crypto drifted up during the sample?

If it is drift, then block-by-block P&L should track the block's YES base rate
almost perfectly, and should go NEGATIVE in blocks where the coin fell.
"""
import json, math, statistics, sys, io, os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
FEE = 0.07


def fee(c, px):
    return math.ceil(FEE * c * px * (1 - px) * 100) / 100


for series in ["KXBTC15M", "KXETH15M", "KXSOL15M", "KXXRP15M", "KXDOGE15M"]:
    p = f"tape2/{series}.json"
    if not os.path.exists(p):
        continue
    rows = sorted(json.load(open(p)), key=lambda r: r["open_ts"])
    trades = []          # (open_ts, pnl, outcome)
    for r in rows:
        y = 1 if r["result"] == "yes" else 0
        close = r["close_ts"]
        # one trade per market: buy YES at the ask, 10-15 minutes before close
        cand = [b for b in r["bars"]
                if b.get("ask") and b.get("bid") and 10 * 60 <= (close - (b.get("ts") or 0)) <= 15 * 60]
        if not cand:
            continue
        b = cand[0]
        ask = b["ask"]
        if not (0 < ask < 1):
            continue
        trades.append((r["open_ts"], y - ask - fee(1, ask), y, ask))

    if len(trades) < 40:
        continue
    n = len(trades)
    total = sum(t[1] for t in trades)
    print(f"\n{'='*70}\n{series}: 'buy YES 10-15min out', {n} trades, total {total:+.2f}  "
          f"({total/n:+.4f}/ct)")

    NB = 8
    size = max(1, n // NB)
    print(f"  {'block':>6} {'n':>5} {'YES rate':>9} {'mean ask':>9} {'pnl/ct':>9} {'total':>8}")
    rates, pnls = [], []
    for i in range(0, n, size):
        blk = trades[i:i + size]
        if len(blk) < 15:
            continue
        rate = statistics.mean(t[2] for t in blk)
        ask = statistics.mean(t[3] for t in blk)
        pn = statistics.mean(t[1] for t in blk)
        rates.append(rate); pnls.append(pn)
        print(f"  {i//size+1:>6} {len(blk):>5} {rate:>9.3f} {ask:>9.3f} {pn:>+9.4f} {pn*len(blk):>+8.2f}")

    if len(rates) > 2:
        mr, mp = statistics.mean(rates), statistics.mean(pnls)
        sr = statistics.pstdev(rates) or 1e-9
        sp = statistics.pstdev(pnls) or 1e-9
        cov = statistics.mean((r - mr) * (q - mp) for r, q in zip(rates, pnls))
        neg = sum(1 for q in pnls if q < 0)
        print(f"  corr(block YES rate, block pnl) = {cov/(sr*sp):+.3f}"
              f"   |  {neg}/{len(pnls)} blocks lose money")
