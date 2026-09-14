"""Is the backtest's profit an edge, or is it luck?

Three tests, hardest first:

  1. MARKET NULL. For each trade we know the price we paid and the market's own
     mid at entry. Under the null that the market price is correct, our win
     probability IS the market's implied probability. Re-draw every outcome from
     that and recompute P&L, thousands of times. If the real result sits inside
     that distribution, our selection added nothing to the price we crossed.
     This is the test that matters: it asks whether we beat the market, not
     whether we made money while the market drifted.

  2. BOOTSTRAP. Resample the realised trade P&Ls to get a confidence interval on
     the total. Answers "how repeatable is this number".

  3. SPLIT-HALF. Does the first half of the sample predict the second half?
"""
import json, math, random, statistics, sys, io, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
random.seed(20260823)

import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))   # run from anywhere
trades = json.load(open("backtest_trades.json"))
n = len(trades)
actual = sum(t["pnl"] for t in trades)
print(f"{n} trades, realised P&L {actual:+.2f}\n")

usable = [t for t in trades if t.get("marketP") is not None]
print(f"{len(usable)} trades carry a market probability snapshot")

# ---- 1. market null ---------------------------------------------------------
ITERS = 20000
sims = []
for _ in range(ITERS):
    tot = 0.0
    for t in usable:
        p = min(max(t["marketP"], 0.0), 1.0)      # market's implied P(our side wins)
        win = random.random() < p
        tot += (t["contracts"] * (1 - t["price"]) if win
                else -t["contracts"] * t["price"]) - t["fee"]
    sims.append(tot)
sims.sort()
actual_usable = sum(t["pnl"] for t in usable)
better = sum(1 for s in sims if s >= actual_usable)
pval = better / ITERS
mean = statistics.mean(sims)
print(f"\n1. MARKET NULL  ({ITERS} simulations, outcomes drawn from the market's own price)")
print(f"   our result            {actual_usable:+.2f}")
print(f"   null mean             {mean:+.2f}   (negative because we pay the spread and the fee)")
print(f"   null 5th / 50th / 95th  {sims[int(.05*ITERS)]:+.2f} / {sims[int(.5*ITERS)]:+.2f} / {sims[int(.95*ITERS)]:+.2f}")
print(f"   p(null >= ours)       {pval:.3f}")
print("   -> " + ("SIGNIFICANT at 5%: our selection beat the market's own probabilities"
                  if pval < 0.05 else
                  "NOT significant: a coin weighted by the market price does this well "
                  f"{pval*100:.0f}% of the time"))

# ---- 2. bootstrap -----------------------------------------------------------
pnls = [t["pnl"] for t in trades]
boot = []
for _ in range(ITERS):
    boot.append(sum(random.choice(pnls) for _ in range(n)))
boot.sort()
lo, hi = boot[int(.025 * ITERS)], boot[int(.975 * ITERS)]
print(f"\n2. BOOTSTRAP  (resampling the {n} realised trade P&Ls)")
print(f"   95% CI on total P&L   {lo:+.2f} .. {hi:+.2f}")
print(f"   share of resamples losing money  {sum(1 for b in boot if b < 0)/ITERS:.1%}")
print("   -> " + ("CI excludes zero" if lo > 0 else "CI includes zero: the total is not distinguishable from noise"))

mean_pc = statistics.mean(pnls)
se = statistics.pstdev(pnls) / math.sqrt(n)
print(f"   mean per trade {mean_pc:+.4f}  se {se:.4f}  t={mean_pc/se if se else 0:+.2f}")

# ---- 3. split-half ----------------------------------------------------------
ts = sorted(trades, key=lambda t: t["t"])
half = len(ts) // 2
a, b = ts[:half], ts[half:]
print(f"\n3. SPLIT-HALF  (does the first half predict the second?)")
for name, g in (("first half", a), ("second half", b)):
    tot = sum(t["pnl"] for t in g)
    wins = sum(1 for t in g if t["won"])
    print(f"   {name:12s} n={len(g):4d}  P&L {tot:+8.2f}  hit {wins/len(g)*100:.1f}%")

# ---- per-series sign consistency -------------------------------------------
print("\n4. SIGN CONSISTENCY BY SERIES  (a real edge should not flip sign)")
by = collections.defaultdict(list)
for t in trades:
    by[t["series"]].append(t["pnl"])
pos = 0
for s, v in sorted(by.items()):
    tot = sum(v)
    pos += 1 if tot > 0 else 0
    print(f"   {s:14s} n={len(v):4d}  P&L {tot:+8.2f}")
print(f"   {pos}/{len(by)} series profitable")
