"""A pre-registered sweep of simple tradable rules, scored honestly.

Everything so far died out of sample, so the point here is to look BROADLY but
to keep the accounting straight:

  * Every rule is defined up front, in this file, before seeing its result.
  * All ranking happens on TRAIN. TEST is evaluated once, at the end, for the
    handful of rules that looked best.
  * The p-value reported for the winners is Bonferroni-corrected by the number
    of rules searched. Searching 40 rules and reporting the best one's raw
    p-value is how noise gets published.

Each rule buys one side of a market and holds to settlement, paying the ask and
the real Kalshi fee.
"""
import json, os, math, statistics, sys, io, random, collections

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
random.seed(20260824)

from patterns import load, TRAIN_FRAC
from strategy import trade_pnl

FRACS = [0.25, 0.5, 0.75, 0.9]


def obs_at(markets, frac):
    """One snapshot per market, plus a little context about the path so far."""
    out = []
    for m in markets:
        o, c = m["open_ts"], m["close_ts"]
        if c <= o:
            continue
        target = o + (c - o) * frac
        bars = [b for b in m["bars"]
                if b.get("ts") is not None and b.get("bid") is not None
                and b.get("ask") is not None and b["ask"] >= b["bid"]
                and 0 < b["bid"] <= 1 and 0 < b["ask"] <= 1]
        if len(bars) < 3:
            continue
        bars.sort(key=lambda b: b["ts"])
        past = [b for b in bars if b["ts"] <= target]
        if not past:
            continue
        cur = past[-1]
        first = bars[0]
        mid = (cur["bid"] + cur["ask"]) / 2
        if not (0.005 < mid < 0.995):
            continue
        out.append({
            "series": m["series"], "cat": m["cat"], "freq": str(m["freq"]),
            "open_ts": o, "close_ts": c,
            "mid": mid, "bid": cur["bid"], "ask": cur["ask"],
            "spread": cur["ask"] - cur["bid"],
            "move": mid - (first["bid"] + first["ask"]) / 2,   # path so far
            "vol": cur.get("vol") or 0.0,
            "oi": cur.get("oi") or 0.0,
            "y": 1 if m["result"] == "yes" else 0,
            "hour": (o // 3600) % 24,
        })
    return out


def buy(o, side):
    if side == "yes":
        return {"price": o["ask"], "win": o["y"], **o}
    return {"price": 1 - o["bid"], "win": 1 - o["y"], **o}


# ---- rule definitions -----------------------------------------------------
# Each returns a trade dict or None. Named so results are readable.

def make_rules():
    R = {}

    for th in (0.70, 0.80, 0.90):
        def fav(o, th=th):
            if o["ask"] >= th and o["ask"] < 0.995: return buy(o, "yes")
            if 1 - o["bid"] >= th and 1 - o["bid"] < 0.995: return buy(o, "no")
            return None
        R[f"favourite>={th}"] = fav

    for th in (0.10, 0.20, 0.30):
        def dog(o, th=th):
            if o["ask"] <= th: return buy(o, "yes")
            if 1 - o["bid"] <= th: return buy(o, "no")
            return None
        R[f"longshot<={th}"] = dog

    for mv in (0.05, 0.10, 0.20):
        def mom(o, mv=mv):
            if o["move"] >= mv and o["ask"] < 0.97: return buy(o, "yes")
            if o["move"] <= -mv and 1 - o["bid"] < 0.97: return buy(o, "no")
            return None
        R[f"momentum>={mv}"] = mom

        def rev(o, mv=mv):
            if o["move"] >= mv and 1 - o["bid"] < 0.97: return buy(o, "no")
            if o["move"] <= -mv and o["ask"] < 0.97: return buy(o, "yes")
            return None
        R[f"reversal>={mv}"] = rev

    def wide(o):
        if o["spread"] < 0.04: return None
        return buy(o, "yes") if o["ask"] >= 0.70 else (buy(o, "no") if 1 - o["bid"] >= 0.70 else None)
    R["wide-spread favourite"] = wide

    def tight(o):
        if o["spread"] > 0.01: return None
        return buy(o, "yes") if o["ask"] >= 0.70 else (buy(o, "no") if 1 - o["bid"] >= 0.70 else None)
    R["tight-spread favourite"] = tight

    def midprice(o):
        if 0.40 <= o["mid"] <= 0.60:
            return buy(o, "yes" if o["ask"] < 1 - o["bid"] else "no")
        return None
    R["cheaper side near 50/50"] = midprice

    return R


def score(trades):
    if len(trades) < 60:
        return None
    pnl = [trade_pnl(t["price"], t["win"]) for t in trades]
    m = statistics.mean(pnl)
    se = statistics.pstdev(pnl) / math.sqrt(len(pnl))
    return {"n": len(trades), "mean": m, "t": m / se if se else 0,
            "total": sum(pnl), "hit": statistics.mean(t["win"] for t in trades),
            "avgpx": statistics.mean(t["price"] for t in trades)}


def null_p(trades, iters=20000):
    """Outcomes redrawn from the price paid: is this better than the market?"""
    obs = sum(trade_pnl(t["price"], t["win"]) for t in trades)
    hits = 0
    for _ in range(iters):
        tot = 0.0
        for t in trades:
            tot += trade_pnl(t["price"], random.random() < t["price"])
        if tot >= obs:
            hits += 1
    return hits / iters


def main():
    markets = load(sys.argv[1] if len(sys.argv) > 1 else "broad/*.json")
    markets.sort(key=lambda m: m["open_ts"])
    split = markets[int(len(markets) * TRAIN_FRAC)]["open_ts"]
    train_m = [m for m in markets if m["open_ts"] < split]
    test_m = [m for m in markets if m["open_ts"] >= split]
    rules = make_rules()
    n_search = len(rules) * len(FRACS)
    print(f"{len(markets)} markets | {len(rules)} rules x {len(FRACS)} entry points "
          f"= {n_search} combinations searched on TRAIN\n")

    tr_obs = {f: obs_at(train_m, f) for f in FRACS}
    results = []
    for name, fn in rules.items():
        for f in FRACS:
            tr = [t for t in (fn(o) for o in tr_obs[f]) if t]
            s = score(tr)
            if s:
                results.append((name, f, s))
    results.sort(key=lambda r: -r[2]["t"])

    print("TRAIN: top 12 by t-statistic")
    print(f"  {'rule':<26}{'entry':>7}{'n':>7}{'hit':>7}{'net/ct':>10}{'t':>7}")
    for name, f, s in results[:12]:
        print(f"  {name:<26}{f:>7.0%}{s['n']:>7}{s['hit']*100:>6.1f}%{s['mean']:>+10.4f}{s['t']:>+7.2f}")

    print("\nTRAIN: worst 4 (a strong NEGATIVE is also a tradable edge, inverted)")
    for name, f, s in results[-4:]:
        print(f"  {name:<26}{f:>7.0%}{s['n']:>7}{s['hit']*100:>6.1f}%{s['mean']:>+10.4f}{s['t']:>+7.2f}")

    # ---- validate the top few, once ------------------------------------
    print("\n" + "=" * 76)
    print(f"TEST (held out). Bonferroni threshold for {n_search} searches: "
          f"p < {0.05/n_search:.5f}")
    te_obs = {f: obs_at(test_m, f) for f in FRACS}
    survived = 0
    for name, f, s in results[:5]:
        te = [t for t in (rules[name](o) for o in te_obs[f]) if t]
        st = score(te)
        if not st:
            print(f"  {name:<26}{f:>7.0%}  too few test trades")
            continue
        p = null_p(te)
        ok = p < 0.05 / n_search and st["mean"] > 0
        survived += ok
        print(f"  {name:<26}{f:>7.0%} n={st['n']:>5} net/ct={st['mean']:>+8.4f} "
              f"t={st['t']:>+5.2f}  p={p:.4f}  {'SURVIVES' if ok else 'no'}")
    print(f"\n  {survived}/5 survived correction.")


if __name__ == "__main__":
    main()
