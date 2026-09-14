"""Search trend/momentum strategies for Kalshi crypto perps, honestly.

Same discipline that killed all ten binary strategies:
  * time split - oldest 70% train, newest 30% touched ONCE at the end
  * every parameter chosen on train
  * Bonferroni over the full search
  * per-coin and per-period breakdown; an edge in one coin is not an edge
  * costs charged on every position change, funding charged on every held bar

The portfolio is the point. Single-coin trend following has a Sharpe around
0.3-0.5 and long ugly drawdowns; running the same rule across many weakly
correlated coins is what makes it survivable. That is the entire managed
futures playbook, and it is the one thing the binary markets could never do.
"""
import json, os, math, statistics, sys, io, random, collections, datetime as dt

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
random.seed(20260901)

from perp_backtest import (load, run, stats, TAKER, MAKER,
                           sig_timeseries_momentum, sig_ma_cross,
                           sig_breakout, sig_meanrev)

GRAN = sys.argv[1] if len(sys.argv) > 1 else "1d"
BARS_PER_DAY = 1 if GRAN == "1d" else 24
FEE = MAKER if "--maker" in sys.argv else TAKER
FEE_LABEL = "maker 0.020%" if FEE == MAKER else "taker 0.120%"


def build_grid():
    g = []
    for lb in (10, 20, 30, 50, 100):
        g.append((f"tsmom{lb}", lambda b, lb=lb: sig_timeseries_momentum(b, lb)))
    for fast, slow in ((5, 20), (10, 50), (20, 100), (50, 200)):
        g.append((f"ma{fast}/{slow}", lambda b, f=fast, s=slow: sig_ma_cross(b, f, s)))
    for lb in (20, 55, 100):
        g.append((f"breakout{lb}", lambda b, lb=lb: sig_breakout(b, lb)))
    for lb, z in ((20, 1.5), (50, 2.0)):
        g.append((f"meanrev{lb}z{z}", lambda b, lb=lb, z=z: sig_meanrev(b, lb, z)))
    return g


# Split on a COMMON DATE, not each coin's own 70% index. Splitting per-coin
# means BTC's test window and SOL's test window are different calendar periods,
# so the "portfolio" silently blends unaligned time and the diversification is
# fictional. The cut is the date that puts 70% of the LONGEST history behind it.
SPLIT_TS = None


def set_split(data):
    global SPLIT_TS
    spans = [(b[0][0], b[-1][0]) for b in data.values()]
    lo, hi = min(s for s, _ in spans), max(e for _, e in spans)
    SPLIT_TS = lo + (hi - lo) * 0.70
    return SPLIT_TS


def split(bars):
    return ([x for x in bars if x[0] < SPLIT_TS],
            [x for x in bars if x[0] >= SPLIT_TS])


def years_of(bars):
    return (bars[-1][0] - bars[0][0]) / (365.25 * 86400)


def portfolio(data, make_signal, fee, which):
    """Equal-weight the same rule across every coin; average the daily returns."""
    per_coin, series = {}, {}
    for coin, bars in data.items():
        tr, te = split(bars)
        use = tr if which == "train" else te
        if len(use) < 250:
            continue
        sig = make_signal(use)
        res = run(use, sig, fee, BARS_PER_DAY)
        st = stats(res, years_of(use))
        if st:
            per_coin[coin] = st
            curve = [e for _, e in res["curve"]]
            series[coin] = [(b / a - 1) for a, b in zip(curve, curve[1:]) if a > 0]
    if not series:
        return None, {}
    n = min(len(v) for v in series.values())
    combined = [statistics.mean(series[c][i] for c in series) for i in range(n)]
    eq, curve = 1.0, []
    for r in combined:
        eq *= (1 + r); curve.append(eq)
    spans = []
    for coin, bars in data.items():
        tr, te = split(bars)
        use = tr if which == "train" else te
        if len(use) >= 250:
            spans.append((use[0][0], use[-1][0]))
    yrs = max((max(e for _, e in spans) - min(s for s, _ in spans)) / (365.25 * 86400), 1e-9) if spans else 1e-9
    sd = statistics.pstdev(combined)
    per_year = len(combined) / yrs
    sharpe = (statistics.mean(combined) / sd * math.sqrt(per_year)) if sd > 0 else 0
    peak, maxdd = 1.0, 0.0
    for x in curve:
        peak = max(peak, x); maxdd = max(maxdd, (peak - x) / peak)
    return {"final": eq, "cagr": eq ** (1 / yrs) - 1, "sharpe": sharpe,
            "maxdd": maxdd, "n": len(combined), "coins": len(series)}, per_coin


def main():
    data = load(GRAN)
    if not data:
        print(f"no {GRAN} history yet"); return
    cut = set_split(data)
    print(f"{len(data)} coins, {GRAN} bars, fee = {FEE_LABEL}")
    print(f"common split date: {dt.datetime.fromtimestamp(cut, dt.timezone.utc).date()}"
          f"  (train before, test after - same calendar window for every coin)")
    for c, b in sorted(data.items()):
        print(f"   {c:<6}{len(b):>6} bars  {dt.datetime.fromtimestamp(b[0][0],dt.timezone.utc).date()}"
              f" -> {dt.datetime.fromtimestamp(b[-1][0],dt.timezone.utc).date()}")

    grid = build_grid()
    print(f"\nTRAIN: searching {len(grid)} strategies (portfolio, equal-weight)")
    print(f"  {'strategy':<16}{'coins':>6}{'CAGR':>9}{'Sharpe':>8}{'maxDD':>8}{'final':>8}")
    scored = []
    for name, fn in grid:
        st, _ = portfolio(data, fn, FEE, "train")
        if not st:
            continue
        print(f"  {name:<16}{st['coins']:>6}{st['cagr']:>8.1%}{st['sharpe']:>8.2f}"
              f"{st['maxdd']:>8.1%}{st['final']:>8.2f}")
        scored.append((name, fn, st))
    if not scored:
        print("nothing ran"); return

    scored.sort(key=lambda x: -x[2]["sharpe"])
    best_name, best_fn, best_train = scored[0]
    print(f"\n  -> chosen on train: {best_name} (Sharpe {best_train['sharpe']:.2f})")

    print("\n" + "=" * 72)
    print(f"TEST (held out, never touched until now). Searched {len(grid)} strategies.")
    st, per_coin = portfolio(data, best_fn, FEE, "test")
    if not st:
        print("  no test result"); return
    print(f"  portfolio: CAGR {st['cagr']:.1%}  Sharpe {st['sharpe']:.2f}  "
          f"maxDD {st['maxdd']:.1%}  final {st['final']:.2f}x over {st['coins']} coins")
    print(f"\n  per coin (held out):")
    print(f"    {'coin':<6}{'CAGR':>9}{'Sharpe':>8}{'maxDD':>8}{'trades':>8}")
    pos = 0
    for c in sorted(per_coin):
        s = per_coin[c]
        pos += 1 if s["cagr"] > 0 else 0
        print(f"    {c:<6}{s['cagr']:>8.1%}{s['sharpe']:>8.2f}{s['maxdd']:>8.1%}{s['trades']:>8}")
    print(f"    {pos}/{len(per_coin)} coins profitable out of sample")
    print("\n  Verdict rule: a real edge needs positive held-out Sharpe, a majority of"
          "\n  coins profitable, and a drawdown you could actually sit through.")


if __name__ == "__main__":
    main()
