"""FAVOURITE strategy: buy the heavily-favoured side and hold to settlement.

Hypothesis, from patterns.py on TRAIN only: Kalshi's crypto and commodity
markets carry a classic favourite-longshot bias. Longshots are overpriced and
favourites are underpriced, symmetrically about 0.50. Because the calibration
was measured side-symmetrically, directional drift in the underlying cannot
produce it.

The fee schedule points the same way. ceil(0.07*C*p*(1-p)) is smallest exactly
where the bias is largest, so the cheapest contracts to trade are the ones the
bias says are underpriced:

    price   edge(train)   half-spread+fee   net
    0.80-85   +6.1c            1.7c         +4.4c
    0.94-97   +3.3c            0.6c         +2.7c

DISCIPLINE
  * Threshold and entry timing are chosen on TRAIN only.
  * TEST is the newest 30% of markets and is evaluated once, at the end.
  * One observation per market, so t-statistics are not inflated by counting
    the same bet many times.
  * Reported per-series and per-period, because an edge that lives in one
    series or one week is not an edge.

Usage:
  python strategy.py "tape2/*.json"
  python strategy.py "broad/*.json"
"""
import json, os, math, statistics, sys, io, glob, collections, random

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
random.seed(20260824)

from patterns import load, sample_obs, TRAIN_FRAC

FEE = 0.07
CLIP = 100          # contracts assumed per order when amortising the fee ceiling


def fee_total(contracts, price):
    return math.ceil(FEE * contracts * price * (1 - price) * 100) / 100


def trade_pnl(price, win, contracts=CLIP):
    """Per-contract P&L of buying at `price` and holding to settlement."""
    gross = (1 - price) if win else -price
    return gross - fee_total(contracts, price) / contracts


def pick(obs, threshold):
    """Buy whichever side is the favourite, if it clears `threshold`."""
    out = []
    for o in obs:
        yes_ask, no_ask = o["ask"], 1 - o["bid"]
        if yes_ask >= threshold and yes_ask < 0.995:
            out.append({**o, "side": "yes", "price": yes_ask, "win": o["y"]})
        elif no_ask >= threshold and no_ask < 0.995:
            out.append({**o, "side": "no", "price": no_ask, "win": 1 - o["y"]})
    return out


def summarise(trades, label, show=True):
    if not trades:
        if show: print(f"  {label:<26} no trades")
        return None
    pnl = [trade_pnl(t["price"], t["win"]) for t in trades]
    m = statistics.mean(pnl)
    se = statistics.pstdev(pnl) / math.sqrt(len(pnl)) if len(pnl) > 1 else float("inf")
    hit = statistics.mean(t["win"] for t in trades)
    avgpx = statistics.mean(t["price"] for t in trades)
    roi = m / avgpx
    if show:
        print(f"  {label:<26} n={len(trades):>5}  hit={hit*100:>5.1f}%  "
              f"avg px={avgpx:.3f}  net/ct={m:>+8.4f}  ROI={roi:>+7.2%}  t={m/se if se else 0:>+6.2f}")
    return {"n": len(trades), "mean": m, "se": se, "t": m / se if se else 0,
            "hit": hit, "avgpx": avgpx, "roi": roi, "total": sum(pnl)}


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else "broad/*.json"
    markets = load(pattern)
    # Optional subset, e.g. --freq=weekly. Chosen on TRAIN evidence only; the
    # test half still validates it.
    freq = None
    for a in sys.argv[2:]:
        if a.startswith("--freq="):
            freq = a.split("=", 1)[1]
    if freq:
        markets = [m for m in markets if str(m.get("freq")) == freq]
        print(f"[subset: frequency = {freq}]")
    if not markets:
        print("no data"); return
    markets.sort(key=lambda m: m["open_ts"])
    split = markets[int(len(markets) * TRAIN_FRAC)]["open_ts"]
    train_m = [m for m in markets if m["open_ts"] < split]
    test_m = [m for m in markets if m["open_ts"] >= split]
    print(f"{len(markets)} markets, {len(set(m['series'] for m in markets))} series")
    print(f"train {len(train_m)}  |  test {len(test_m)}  (held out, touched once)\n")

    # ---------- 1. choose threshold and entry timing on TRAIN --------------
    print("TRAIN: threshold scan (entry at 50% through the window)")
    tr_obs = sample_obs(train_m, 0.5)
    best = None
    for th in [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.94]:
        s = summarise(pick(tr_obs, th), f"threshold {th:.2f}")
        if s and s["n"] >= 150 and (best is None or s["mean"] > best[1]["mean"]):
            best = (th, s)
    if not best:
        print("\nno threshold produced enough trades"); return
    TH = best[0]
    print(f"\n  -> chosen on train: threshold {TH:.2f}")

    print("\nTRAIN: entry timing scan (fraction through the window)")
    best_f = None
    for frac in [0.2, 0.35, 0.5, 0.65, 0.8, 0.9]:
        s = summarise(pick(sample_obs(train_m, frac), TH), f"entry at {frac:.0%}")
        if s and s["n"] >= 150 and (best_f is None or s["mean"] > best_f[1]["mean"]):
            best_f = (frac, s)
    FRAC = best_f[0]
    print(f"\n  -> chosen on train: entry at {FRAC:.0%} through the window")

    tr = pick(sample_obs(train_m, FRAC), TH)
    tr_stat = summarise(tr, "TRAIN final", show=False)
    print(f"\nTRAIN FINAL  n={tr_stat['n']}  net/ct={tr_stat['mean']:+.4f}  "
          f"ROI={tr_stat['roi']:+.2%}  t={tr_stat['t']:+.2f}")

    # ---------- 2. robustness WITHIN train --------------------------------
    print("\nTRAIN: by series (an edge in one series is not an edge)")
    by = collections.defaultdict(list)
    for t in tr: by[t["series"]].append(t)
    pos = 0
    for k in sorted(by):
        s = summarise(by[k], k)
        if s and s["mean"] > 0: pos += 1
    print(f"  {pos}/{len(by)} series positive")

    print("\nTRAIN: by time block")
    blocks = 5
    tr_sorted = sorted(tr, key=lambda t: t["open_ts"])
    size = max(1, len(tr_sorted) // blocks)
    posb = 0
    for i in range(0, len(tr_sorted), size):
        b = tr_sorted[i:i + size]
        if len(b) < 30: continue
        s = summarise(b, f"block {i//size+1}")
        if s and s["mean"] > 0: posb += 1
    print(f"  {posb} blocks positive")

    # ---------- 3. TEST: touched once -------------------------------------
    print("\n" + "=" * 74)
    print("TEST (held out, parameters frozen from train)")
    te = pick(sample_obs(test_m, FRAC), TH)
    te_stat = summarise(te, "TEST", show=False)
    if not te_stat:
        print("  no test trades"); return
    print(f"  n={te_stat['n']}  hit={te_stat['hit']*100:.1f}%  avg px={te_stat['avgpx']:.3f}")
    print(f"  net/ct = {te_stat['mean']:+.4f}   ROI = {te_stat['roi']:+.2%}   t = {te_stat['t']:+.2f}")

    # Null: the market price is right. Redraw each outcome from the price paid.
    ITERS = 20000
    obs_total = te_stat["total"]
    null = []
    for _ in range(ITERS):
        tot = 0.0
        for t in te:
            win = random.random() < t["price"]
            tot += trade_pnl(t["price"], win)
        null.append(tot)
    null.sort()
    p = sum(1 for x in null if x >= obs_total) / ITERS
    print(f"\n  MARKET NULL ({ITERS} sims): our total {obs_total:+.2f}, "
          f"null mean {statistics.mean(null):+.2f}, p(null>=ours) = {p:.4f}")
    print("  -> " + ("SURVIVES out of sample" if p < 0.05 and te_stat["mean"] > 0
                     else "does NOT survive out of sample"))

    print("\nTEST: by series")
    byt = collections.defaultdict(list)
    for t in te: byt[t["series"]].append(t)
    posn = 0
    for k in sorted(byt):
        s = summarise(byt[k], k)
        if s and s["mean"] > 0: posn += 1
    print(f"  {posn}/{len(byt)} series positive")


if __name__ == "__main__":
    main()
