"""Pattern study across the whole liquid Kalshi crypto + commodity universe.

METHOD - the parts that stop this being a curve fit:

* SIDE-SYMMETRIC POOLING. Every observation is counted twice: once as YES at
  price p with outcome y, once as NO at price (1-p) with outcome (1-y). Any
  directional drift in the underlying helps one side exactly as much as it hurts
  the other, so it cancels by construction. This is the control the earlier
  per-series tables lacked - that is why they showed a "+50% edge" that was
  really just crypto going up during the sample.

* ONE OBSERVATION PER MARKET. Bars inside a window are nearly the same bet
  measured repeatedly; treating them as independent inflates every t-statistic.
  Sampling one bar per market keeps the observations independent so the standard
  errors mean something.

* TRAIN / TEST SPLIT BY TIME. The oldest 70% of markets is the only data used
  to look for patterns or choose parameters. The newest 30% is touched once, at
  the end, to validate. Anything that only works in train is discarded.

Fees are Kalshi's: ceil(0.07 * C * p * (1-p)), rounded UP to the cent. They fall
steeply as price approaches 1 (1.75c at 0.50, 0.63c at 0.90, 0.20c at 0.97),
which matters: if favourites are underpriced, the cheap side to trade is also
the cheap side to pay for.
"""
import json, os, math, statistics, sys, io, glob, collections

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

FEE = 0.07
SAMPLE_FRAC = 0.5          # where in the window to sample; scanned later on train only
TRAIN_FRAC = 0.70


def fee_per_contract(price, contracts=100):
    """Per-contract fee at a realistic clip size (the ceil matters less at size)."""
    return math.ceil(FEE * contracts * price * (1 - price) * 100) / 100 / contracts


def load(pattern="broad/*.json"):
    rows = []
    for p in sorted(glob.glob(pattern)):
        if os.path.basename(p).startswith(("spot_", "pyth_", "universe")):
            continue
        try:
            data = json.load(open(p))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for m in data:
            # tape2/ predates the cat/freq fields; fill them in so both
            # collections can be pooled.
            m.setdefault("series", os.path.basename(p)[:-5])
            m.setdefault("cat", "Commodities" if any(k in m["series"] for k in
                         ("GOLD", "SILVER", "WTI", "BRENT", "GAS", "COPPER")) else "Crypto")
            m.setdefault("freq", "fifteen_min")
            rows.append(m)
    return rows


def sample_obs(markets, frac=SAMPLE_FRAC):
    """One (price, outcome) per market, at `frac` through the window."""
    out = []
    for m in markets:
        o, c = m["open_ts"], m["close_ts"]
        if c <= o:
            continue
        target = o + (c - o) * frac
        best, bestd = None, None
        for b in m["bars"]:
            ts, bid, ask = b.get("ts"), b.get("bid"), b.get("ask")
            if ts is None or bid is None or ask is None or ask < bid:
                continue
            if not (0 < bid <= 1 and 0 < ask <= 1):
                continue
            d = abs(ts - target)
            if bestd is None or d < bestd:
                best, bestd = b, d
        if not best:
            continue
        mid = (best["bid"] + best["ask"]) / 2
        if not (0.005 < mid < 0.995):
            continue
        out.append({
            "series": m["series"], "cat": m["cat"], "freq": m["freq"],
            "open_ts": o, "close_ts": c,
            "mid": mid, "bid": best["bid"], "ask": best["ask"],
            "spread": best["ask"] - best["bid"],
            "y": 1 if m["result"] == "yes" else 0,
            "vol": m.get("volume", 0.0),
        })
    return out


def symmetric(obs):
    """Each observation as both sides, so directional drift cancels."""
    out = []
    for o in obs:
        out.append({**o, "side": "yes", "price": o["mid"], "win": o["y"],
                    "ask_px": o["ask"]})
        out.append({**o, "side": "no", "price": 1 - o["mid"], "win": 1 - o["y"],
                    "ask_px": 1 - o["bid"]})
    return out


def calib_table(rows, edges, label):
    print(f"\n{label}")
    print(f"  {'bucket':>13} {'n':>7} {'mean px':>9} {'realised':>9} {'diff':>9} {'se':>7} {'t':>7}")
    res = []
    for lo, hi in zip(edges, edges[1:]):
        g = [r for r in rows if lo <= r["price"] < hi]
        if len(g) < 100:
            continue
        mp = statistics.mean(r["price"] for r in g)
        rz = statistics.mean(r["win"] for r in g)
        # Independent units are MARKETS; the two symmetric rows from one market
        # are perfectly anti-correlated, so halve n for the standard error.
        n_eff = len(g) / 2
        se = math.sqrt(max(rz * (1 - rz), 1e-9) / n_eff)
        t = (rz - mp) / se if se > 0 else 0
        print(f"  {lo:.2f}-{hi:<8.2f} {len(g):>7} {mp:>9.4f} {rz:>9.4f} {rz-mp:>+9.4f} {se:>7.4f} {t:>+7.2f}")
        res.append({"lo": lo, "hi": hi, "n": len(g), "price": mp, "real": rz, "diff": rz - mp, "t": t})
    return res


def main():
    markets = load(sys.argv[1] if len(sys.argv) > 1 else "broad/*.json")
    if not markets:
        print("no data in broad/ yet"); return
    markets.sort(key=lambda m: m["open_ts"])
    print(f"{len(markets)} settled markets across "
          f"{len(set(m['series'] for m in markets))} series")

    split = markets[int(len(markets) * TRAIN_FRAC)]["open_ts"]
    train = [m for m in markets if m["open_ts"] < split]
    test = [m for m in markets if m["open_ts"] >= split]
    print(f"train {len(train)} markets, test {len(test)} markets (split at "
          f"{__import__('datetime').datetime.utcfromtimestamp(split)})")

    tr = symmetric(sample_obs(train))
    print(f"\ntrain observations: {len(tr)//2} markets -> {len(tr)} side-symmetric rows")

    EDGES = [0, .03, .06, .10, .15, .20, .30, .40, .50, .60, .70, .80, .85, .90, .94, .97, 1.0]
    calib_table(tr, EDGES, "TRAIN - CALIBRATION, SIDE-SYMMETRIC (diff>0 = underpriced)")

    # Same table per category, to see whether any effect is broad or one market.
    for cat in sorted(set(r["cat"] for r in tr)):
        sub = [r for r in tr if r["cat"] == cat]
        if len(sub) > 800:
            calib_table(sub, [0, .10, .30, .50, .70, .85, .94, 1.0], f"TRAIN - {cat}")

    for fq in sorted(set(str(r["freq"]) for r in tr)):
        sub = [r for r in tr if str(r["freq"]) == fq]
        if len(sub) > 800:
            calib_table(sub, [0, .10, .30, .50, .70, .85, .94, 1.0], f"TRAIN - frequency: {fq}")

    print("\nSPREAD AND FEE BY PRICE BUCKET (what a taker actually pays)")
    print(f"  {'bucket':>13} {'n':>7} {'spread':>8} {'fee/ct':>8} {'total':>8}")
    for lo, hi in zip(EDGES, EDGES[1:]):
        g = [r for r in tr if lo <= r["price"] < hi]
        if len(g) < 100:
            continue
        sp = statistics.mean(r["spread"] for r in g)
        fe = statistics.mean(fee_per_contract(r["price"]) for r in g)
        print(f"  {lo:.2f}-{hi:<8.2f} {len(g):>7} {sp:>8.4f} {fe:>8.4f} {sp/2+fe:>8.4f}")


if __name__ == "__main__":
    main()
