"""Is the Kalshi 15-min market calibrated? And is any side systematically cheap?"""
import json, sys, io, os, math, statistics, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

FEE_COEF = 0.07


def fee(contracts, px):
    return math.ceil(FEE_COEF * contracts * px * (1 - px) * 100) / 100


def load(series):
    p = os.path.join("tape2", f"{series}.json")
    if not os.path.exists(p):
        return []
    return json.load(open(p))


def analyse(series):
    rows = load(series)
    if not rows:
        print(f"{series}: no data"); return
    obs = []          # (mins_left, bid, ask, mid, outcome)
    for r in rows:
        y = 1 if r["result"] == "yes" else 0
        close = r["close_ts"]
        for b in r["bars"]:
            ts, bid, ask = b.get("ts"), b.get("bid"), b.get("ask")
            if ts is None or bid is None or ask is None:
                continue
            if not (0 < bid <= 1 and 0 < ask <= 1) or ask < bid:
                continue
            left = (close - ts) / 60.0
            if left < 0 or left > 15:
                continue
            obs.append((left, bid, ask, (bid + ask) / 2, y, b.get("vol") or 0.0))

    n = len(obs)
    if not n:
        print(f"{series}: no usable bars"); return
    print(f"\n{'='*72}\n{series}: {len(rows)} markets, {n} minute-bars")
    yes_rate = sum(o[4] for o in obs) / n
    print(f"  base rate YES over markets: {sum(1 for r in rows if r['result']=='yes')/len(rows):.4f}")
    print(f"  mean mid={statistics.mean(o[3] for o in obs):.4f}  mean outcome={yes_rate:.4f}"
          f"  mean spread={statistics.mean(o[2]-o[1] for o in obs):.4f}")

    # --- calibration -------------------------------------------------------
    print("\n  CALIBRATION (mid price vs realised YES rate)")
    print(f"  {'bucket':>12} {'n':>7} {'mean mid':>9} {'realised':>9} {'diff':>8} {'z':>7}")
    edges = [0, .02, .05, .10, .20, .30, .40, .45, .50, .55, .60, .70, .80, .90, .95, .98, 1.01]
    for lo, hi in zip(edges, edges[1:]):
        g = [o for o in obs if lo <= o[3] < hi]
        if len(g) < 30:
            continue
        m = statistics.mean(o[3] for o in g)
        rz = statistics.mean(o[4] for o in g)
        se = math.sqrt(max(rz * (1 - rz), 1e-9) / len(g))
        z = (rz - m) / se if se > 0 else 0
        print(f"  {lo:.2f}-{hi:<7.2f} {len(g):>7} {m:>9.4f} {rz:>9.4f} {rz-m:>+8.4f} {z:>+7.2f}")

    # --- can you make money crossing the spread? --------------------------
    print("\n  CROSSING THE SPREAD, 1 contract, by minutes left")
    print(f"  {'mins left':>10} {'n':>7} {'YES pnl/ct':>11} {'NO pnl/ct':>11} {'best':>10}")
    for lo, hi in [(0,1),(1,2),(2,3),(3,5),(5,7),(7,10),(10,15.1)]:
        g = [o for o in obs if lo <= o[0] < hi]
        if len(g) < 50:
            continue
        # buy YES at ask; buy NO at 1-bid
        ypnl = statistics.mean((o[4] - o[2] - fee(1, o[2])) for o in g)
        npnl = statistics.mean(((1 - o[4]) - (1 - o[1]) - fee(1, 1 - o[1])) for o in g)
        best = max(ypnl, npnl)
        print(f"  {lo:>4}-{hi:<5} {len(g):>7} {ypnl:>+11.4f} {npnl:>+11.4f} {best:>+10.4f}")

    # --- is the mid biased? (fee-free, the pure forecasting question) -------
    print("\n  MID-PRICE BIAS by minutes left  (realised - mid; >0 means YES underpriced)")
    for lo, hi in [(0,1),(1,2),(2,3),(3,5),(5,7),(7,10),(10,15.1)]:
        g = [o for o in obs if lo <= o[0] < hi]
        if len(g) < 50:
            continue
        d = [o[4] - o[3] for o in g]
        m = statistics.mean(d)
        se = statistics.pstdev(d) / math.sqrt(len(d))
        print(f"  {lo:>4}-{hi:<5} n={len(g):>6} bias={m:>+8.4f} se={se:.4f}  t={m/se if se else 0:>+6.2f}")


for s in ["KXSILVER15M","KXWTI15M"]:
    analyse(s)
