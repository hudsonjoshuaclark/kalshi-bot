"""Did the overnight paper run actually show skill, or is it another mirage?

Two things get tested separately, because they answer different questions:

  1. BRIER SKILL - paired against the market's own snapshotted price on the same
     resolved windows. This is a forecasting test and it is the one that matters:
     it cannot be produced by the underlying drifting, because a drift that helps
     our forecast helps the market's forecast identically.

  2. P&L - tested against the market null (outcomes redrawn from the price paid).
     Over 34 trades this is nearly powerless, and it is also the number most
     easily produced by direction rather than skill, so it gets checked for
     side-imbalance too.
"""
import sqlite3, math, statistics, sys, io, os, collections, random

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
random.seed(20260826)

db = sqlite3.connect("../data/bot.db")
db.row_factory = sqlite3.Row

# ---- 1. Brier, paired -----------------------------------------------------
rows = db.execute(
    "SELECT model_p, market_p, result, series, ticker FROM forecasts "
    "WHERE result IN ('yes','no')").fetchall()
print(f"{len(rows)} resolved forecasts\n")

d, bm, bk = [], [], []
for r in rows:
    y = 1 if r["result"] == "yes" else 0
    m = (r["model_p"] - y) ** 2
    k = (r["market_p"] - y) ** 2
    bm.append(m); bk.append(k); d.append(k - m)      # >0 means model beat market

mean_d = statistics.mean(d)
se_d = statistics.pstdev(d) / math.sqrt(len(d))
t = mean_d / se_d if se_d else 0
print("1. BRIER SKILL (paired, same outcomes)")
print(f"   model  {statistics.mean(bm):.4f}")
print(f"   market {statistics.mean(bk):.4f}")
print(f"   skill  {(statistics.mean(bk)-statistics.mean(bm))/statistics.mean(bk)*100:+.2f}%")
print(f"   paired diff {mean_d:+.5f}  se {se_d:.5f}  t = {t:+.2f}")
print("   -> " + ("SIGNIFICANT (t>2): the model forecast better than the price"
                  if t > 2 else "not significant at t>2"))

# Is it broad, or one series carrying it?
print("\n   by series:")
by = collections.defaultdict(list)
for r, dd in zip(rows, d):
    by[r["series"]].append(dd)
for k in sorted(by):
    v = by[k]
    se = statistics.pstdev(v) / math.sqrt(len(v)) if len(v) > 1 else float("inf")
    print(f"     {k:<12} n={len(v):>4}  diff={statistics.mean(v):+.5f}  "
          f"t={statistics.mean(v)/se if se else 0:+.2f}")

# ---- 2. P&L ---------------------------------------------------------------
pos = db.execute(
    "SELECT side, contracts, avg_price, fee, result, pnl, market_p, series "
    "FROM positions WHERE settled_at IS NOT NULL").fetchall()
print(f"\n2. P&L over {len(pos)} paper trades")
tot = sum(p["pnl"] for p in pos)
sides = collections.Counter(p["side"] for p in pos)
print(f"   total {tot:+.2f}   sides: {dict(sides)}")

pnls = [p["pnl"] for p in pos]
se = statistics.pstdev(pnls) / math.sqrt(len(pnls))
print(f"   mean/trade {statistics.mean(pnls):+.4f}  se {se:.4f}  t={statistics.mean(pnls)/se:+.2f}")

# market null: redraw each outcome from the market's own probability for our side
ITERS = 20000
null = []
for _ in range(ITERS):
    s = 0.0
    for p in pos:
        mp = p["market_p"]
        if mp is None:
            mp = p["avg_price"]
        win = random.random() < min(max(mp, 0.0), 1.0)
        s += (p["contracts"] * (1 - p["avg_price"]) if win
              else -p["contracts"] * p["avg_price"]) - (p["fee"] or 0)
    null.append(s)
null.sort()
pv = sum(1 for x in null if x >= tot) / ITERS
print(f"   market null: mean {statistics.mean(null):+.2f}, p(null>=ours) = {pv:.4f}")
print("   -> " + ("beats the market null" if pv < 0.05 else "NOT distinguishable from the market null"))

# Drift check: did one side carry everything?
print("\n   P&L by side (a one-sided result is direction, not skill):")
for s in ("yes", "no"):
    g = [p["pnl"] for p in pos if p["side"] == s]
    if g:
        print(f"     {s:<4} n={len(g):>3}  total {sum(g):+.2f}  mean {statistics.mean(g):+.4f}")
