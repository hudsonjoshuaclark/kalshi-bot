"""Walk-forward validation + volatility-scaled sizing for the perp trend bot.

Two questions the single train/test split cannot answer:

1. IS SHARPE 0.41 STABLE, OR REGIME LUCK? Trend following has decade-long good
   and bad regimes. One 3-year window cannot tell them apart. Walk-forward
   re-selects the rule on each training window and trades it on the NEXT
   window, repeatedly - which is what actually running this would feel like.

2. CAN THE 43% DRAWDOWN BE FIXED? The standard answer is volatility targeting:
   size each position inversely to its own recent volatility, so a quiet coin
   gets more capital than a wild one and the portfolio's risk stays roughly
   constant. This is the single biggest realistic improvement available and it
   is not a new signal - it is the same signal, sized sanely.

Everything is still charged Kalshi's real costs, and no window is ever selected
on data it is then scored against.
"""
import json, os, math, statistics, sys, io, glob, collections, datetime as dt

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from perp_backtest import (load, TAKER, MAKER, FUNDING_8H,
                           sig_timeseries_momentum, sig_ma_cross,
                           sig_breakout, sig_meanrev)

GRAN = "1d"
FEE = MAKER if "--maker" in sys.argv else TAKER
FEE_LABEL = "maker 0.020%" if FEE == MAKER else "taker 0.120%"
VOL_TARGET = 0.40 / math.sqrt(365)     # 40% annualised, expressed per day
VOL_LOOKBACK = 30
MAX_LEV = 3.0                          # well inside Kalshi's ~6x cap


def grid():
    g = []
    for lb in (10, 20, 30, 50, 100):
        g.append((f"tsmom{lb}", lambda b, lb=lb: sig_timeseries_momentum(b, lb)))
    for f_, s_ in ((5, 20), (10, 50), (20, 100), (50, 200)):
        g.append((f"ma{f_}/{s_}", lambda b, f=f_, s=s_: sig_ma_cross(b, f, s)))
    for lb in (20, 55, 100):
        g.append((f"breakout{lb}", lambda b, lb=lb: sig_breakout(b, lb)))
    for lb, z in ((20, 1.5), (50, 2.0)):
        g.append((f"meanrev{lb}z{z}", lambda b, lb=lb, z=z: sig_meanrev(b, lb, z)))
    return g


def daily_returns(bars, sig, fee, vol_scaled):
    """Per-bar strategy returns, entering at the NEXT bar's open."""
    out, pos = [], 0.0
    closes = [b["close"] for _, b in bars]
    for i in range(len(sig) - 1):
        _, want = sig[i]
        size = float(want)
        if vol_scaled and i >= VOL_LOOKBACK:
            w = closes[i - VOL_LOOKBACK:i + 1]
            rets = [math.log(b / a) for a, b in zip(w, w[1:]) if a > 0 and b > 0]
            sd = statistics.pstdev(rets) if len(rets) > 5 else None
            if sd and sd > 0:
                size = max(-MAX_LEV, min(MAX_LEV, want * (VOL_TARGET / sd)))
            else:
                size = 0.0
        cost = fee * abs(size - pos)
        pos = size
        nxt = bars[i + 1][1]
        r = (nxt["close"] - nxt["open"]) / nxt["open"] if nxt["open"] else 0.0
        fund = abs(pos) * FUNDING_8H * 3      # three 8h periods per day
        out.append(pos * r - cost - fund)
    return out


def portfolio_returns(data, fn, fee, lo_ts, hi_ts, vol_scaled):
    """Equal-weight across coins on a shared calendar window."""
    per_coin = {}
    for coin, bars in data.items():
        use = [x for x in bars if lo_ts <= x[0] < hi_ts]
        if len(use) < 120:
            continue
        rets = daily_returns(use, fn(use), fee, vol_scaled)
        if rets:
            per_coin[coin] = dict(zip([t for t, _ in use[1:]], rets))
    if not per_coin:
        return [], {}
    days = sorted(set().union(*[set(v) for v in per_coin.values()]))
    combined = []
    for d in days:
        vals = [v[d] for v in per_coin.values() if d in v]
        if vals:
            combined.append(statistics.mean(vals))
    return combined, per_coin


def summarise(rets, years):
    if not rets:
        return None
    eq, curve = 1.0, []
    for r in rets:
        eq *= (1 + r); curve.append(eq)
    sd = statistics.pstdev(rets)
    sharpe = (statistics.mean(rets) / sd * math.sqrt(len(rets) / max(years, 1e-9))) if sd > 0 else 0
    peak, maxdd = 1.0, 0.0
    for x in curve:
        peak = max(peak, x); maxdd = max(maxdd, (peak - x) / peak)
    return {"final": eq, "cagr": eq ** (1 / max(years, 1e-9)) - 1,
            "sharpe": sharpe, "maxdd": maxdd, "n": len(rets)}


def walk_forward(data, fee, vol_scaled, train_days=730, test_days=365):
    lo = min(b[0][0] for b in data.values())
    hi = max(b[-1][0] for b in data.values())
    D = 86400
    folds, all_rets = [], []
    start = lo
    while start + (train_days + test_days) * D <= hi:
        tr_lo, tr_hi = start, start + train_days * D
        te_lo, te_hi = tr_hi, tr_hi + test_days * D
        best, best_sh = None, -9e9
        for name, fn in grid():
            r, _ = portfolio_returns(data, fn, fee, tr_lo, tr_hi, vol_scaled)
            s = summarise(r, train_days / 365.25)
            if s and s["sharpe"] > best_sh:
                best, best_sh = (name, fn), s["sharpe"]
        if not best:
            start += test_days * D; continue
        r, _ = portfolio_returns(data, best[1], fee, te_lo, te_hi, vol_scaled)
        s = summarise(r, test_days / 365.25)
        if s:
            folds.append({"from": te_lo, "to": te_hi, "rule": best[0],
                          "train_sharpe": best_sh, **s})
            all_rets.extend(r)
        start += test_days * D
    return folds, all_rets


def main():
    data = load(GRAN)
    print(f"{len(data)} coins, daily bars, fee = {FEE_LABEL}\n")
    for vol_scaled in (False, True):
        label = "VOLATILITY-SCALED" if vol_scaled else "FIXED SIZE (+/-1)"
        folds, rets = walk_forward(data, FEE, vol_scaled)
        if not folds:
            print(f"{label}: not enough history"); continue
        print(f"{label}")
        print(f"  {'test window':<26}{'rule chosen':<14}{'trainSh':>8}{'CAGR':>9}{'Sharpe':>8}{'maxDD':>8}")
        for f in folds:
            a = dt.datetime.fromtimestamp(f["from"], dt.timezone.utc).date()
            b = dt.datetime.fromtimestamp(f["to"], dt.timezone.utc).date()
            print(f"  {str(a)+' -> '+str(b):<26}{f['rule']:<14}{f['train_sharpe']:>8.2f}"
                  f"{f['cagr']:>8.1%}{f['sharpe']:>8.2f}{f['maxdd']:>8.1%}")
        yrs = sum(1 for _ in folds) * 1.0
        agg = summarise(rets, yrs)
        pos = sum(1 for f in folds if f["cagr"] > 0)
        print(f"  {'STITCHED OUT-OF-SAMPLE':<26}{'':<14}{'':>8}"
              f"{agg['cagr']:>8.1%}{agg['sharpe']:>8.2f}{agg['maxdd']:>8.1%}")
        print(f"  {pos}/{len(folds)} folds profitable\n")


if __name__ == "__main__":
    main()
