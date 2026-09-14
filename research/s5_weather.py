"""S5 — Kalshi daily temperature markets.

Why this is the best-shaped target left:
  * DAILY markets, so the execution-latency problem that killed everything at
    15-minute horizons simply does not exist
  * settles on official NWS observations
  * each day is a set of MUTUALLY EXCLUSIVE, EXHAUSTIVE buckets ("82-83", "84-85",
    "above 90", ...), so the market publishes a complete probability
    distribution over tomorrow's high temperature. That is far more structure
    than a single binary, and it can be checked two ways a binary cannot:
      - do the bucket probabilities SUM to 1? (the overround)
      - is the DISTRIBUTION the right shape, or too wide / too narrow?

The hypothesis worth testing, and the reason this is not a fishing trip: S7
found Kalshi's implied volatility on crypto running ~90x trailing realised —
the market overprices uncertainty. If that is a house-wide behaviour rather
than a crypto artifact, the same thing should show up here as a distribution
that is too WIDE: central buckets underpriced, tail buckets overpriced.

Discipline, same as everything else here: time-split train/test, ONE
observation per event-day, market null, Bonferroni over the search.
"""
import json, os, glob, math, statistics, sys, io, random, collections, datetime as dt

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
random.seed(20260831)

FEE = 0.07
TRAIN_FRAC = 0.70


def fee_per_ct(price, contracts=100):
    return math.ceil(FEE * contracts * price * (1 - price) * 100) / 100 / contracts


def pnl(price, win, contracts=100):
    return ((1 - price) if win else -price) - fee_per_ct(price, contracts)


def load_events():
    events = collections.defaultdict(list)
    for f in sorted(glob.glob("weather/*.json")):
        for m in json.load(open(f)):
            events[(m["series"], m["event"])].append(m)
    return events


def snapshot(markets, frac):
    """The whole bucket ladder at `frac` through the event's life."""
    o = min(m["open_ts"] for m in markets)
    c = max(m["close_ts"] for m in markets)
    if c <= o:
        return None
    target = o + (c - o) * frac
    rung = []
    for m in markets:
        best, bd = None, None
        for b in m["bars"]:
            ts, bid, ask = b.get("ts"), b.get("bid"), b.get("ask")
            if ts is None or bid is None or ask is None or ask < bid:
                continue
            if not (0 <= bid <= 1 and 0 <= ask <= 1):
                continue
            d = abs(ts - target)
            if bd is None or d < bd:
                best, bd = b, d
        if best is None or bd is None or bd > 6 * 3600:
            continue
        rung.append({
            "ticker": m["ticker"], "type": m["strike_type"],
            "lo": m["strike"], "hi": m["cap"],
            "bid": best["bid"], "ask": best["ask"],
            "mid": (best["bid"] + best["ask"]) / 2,
            "win": 1 if m["result"] == "yes" else 0,
            "open_ts": o,
        })
    # Need a real ladder, and exactly one bucket may win.
    if len(rung) < 4 or sum(r["win"] for r in rung) != 1:
        return None
    return rung


def main():
    events = load_events()
    print(f"{len(events)} event-days across {len(set(k[0] for k in events))} cities\n")

    # ---- 1. does the implied distribution sum to 1? -----------------------
    print("1. OVERROUND — do the mutually exclusive buckets sum to 1?")
    for frac in (0.25, 0.5, 0.75):
        mids, asks, bids = [], [], []
        for markets in events.values():
            r = snapshot(markets, frac)
            if not r:
                continue
            mids.append(sum(x["mid"] for x in r))
            asks.append(sum(x["ask"] for x in r))
            bids.append(sum(x["bid"] for x in r))
        if not mids:
            continue
        print(f"   at {frac:.0%} through: n={len(mids):>4}  "
              f"sum(bid)={statistics.median(bids):.3f}  "
              f"sum(mid)={statistics.median(mids):.3f}  "
              f"sum(ask)={statistics.median(asks):.3f}")
    print("   (sum(ask) > 1 > sum(bid) means no riskless arb; the gap is the house's)\n")

    # ---- 2. is the distribution the right SHAPE? --------------------------
    # Rank buckets by price and ask whether each rank wins as often as it is
    # priced. If the market overprices uncertainty, the top-ranked bucket wins
    # MORE than its price implies, and the tails win less.
    print("2. SHAPE — does each price rank win as often as it is priced?")
    ev_sorted = sorted(events.items(), key=lambda kv: min(m["open_ts"] for m in kv[1]))
    cut = ev_sorted[int(len(ev_sorted) * TRAIN_FRAC)][1]
    split_ts = min(m["open_ts"] for m in cut)
    train = [kv for kv in ev_sorted if min(m["open_ts"] for m in kv[1]) < split_ts]
    test = [kv for kv in ev_sorted if min(m["open_ts"] for m in kv[1]) >= split_ts]
    print(f"   train {len(train)} event-days | test {len(test)} (held out)\n")

    def rank_table(subset, frac, label):
        by_rank = collections.defaultdict(lambda: {"n": 0, "px": 0.0, "win": 0})
        for _, markets in subset:
            r = snapshot(markets, frac)
            if not r:
                continue
            r.sort(key=lambda x: -x["mid"])
            for i, x in enumerate(r[:6]):
                b = by_rank[i]
                b["n"] += 1; b["px"] += x["mid"]; b["win"] += x["win"]
        print(f"   {label}")
        print(f"     {'rank':>5}{'n':>7}{'mean px':>10}{'won':>9}{'diff':>10}{'t':>8}")
        for i in sorted(by_rank):
            b = by_rank[i]
            if b["n"] < 40:
                continue
            px, wr = b["px"] / b["n"], b["win"] / b["n"]
            se = math.sqrt(max(wr * (1 - wr), 1e-9) / b["n"])
            print(f"     {i+1:>5}{b['n']:>7}{px:>10.4f}{wr:>9.4f}{wr-px:>+10.4f}{(wr-px)/se if se else 0:>+8.2f}")
        return by_rank

    rank_table(train, 0.5, "TRAIN (diff > 0 means that rank is UNDERPRICED)")

    # ---- 3. trade the favourite bucket ------------------------------------
    print("\n3. STRATEGY — buy the top-ranked bucket at the ask, hold to settlement")
    GRID = [(frac, k) for frac in (0.25, 0.5, 0.75, 0.9) for k in (1, 2)]
    print(f"   searching {len(GRID)} combinations on TRAIN")
    print(f"     {'entry':>7}{'topK':>6}{'n':>7}{'hit':>8}{'px':>8}{'net/ct':>10}{'t':>8}")
    results = []
    for frac, k in GRID:
        tr = []
        for _, markets in train:
            r = snapshot(markets, frac)
            if not r:
                continue
            r.sort(key=lambda x: -x["mid"])
            for x in r[:k]:
                if 0.02 < x["ask"] < 0.98:
                    tr.append({"price": x["ask"], "win": x["win"]})
        if len(tr) < 60:
            continue
        v = [pnl(t["price"], t["win"]) for t in tr]
        m = statistics.mean(v); se = statistics.pstdev(v) / math.sqrt(len(v))
        print(f"     {frac:>7.0%}{k:>6}{len(tr):>7}"
              f"{statistics.mean(t['win'] for t in tr):>8.1%}"
              f"{statistics.mean(t['price'] for t in tr):>8.3f}{m:>+10.4f}{m/se:>+8.2f}")
        results.append(((frac, k), m / se if se else 0, m))
    if not results:
        print("   no combination produced enough trades"); return
    results.sort(key=lambda r: -r[1])
    (best_frac, best_k) = results[0][0]
    print(f"\n   -> chosen on train: entry {best_frac:.0%}, top {best_k}")

    # ---- 4. held out, once ------------------------------------------------
    te = []
    for _, markets in test:
        r = snapshot(markets, best_frac)
        if not r:
            continue
        r.sort(key=lambda x: -x["mid"])
        for x in r[:best_k]:
            if 0.02 < x["ask"] < 0.98:
                te.append({"price": x["ask"], "win": x["win"]})
    print("\n" + "=" * 70)
    print(f"TEST (held out). Bonferroni threshold for {len(GRID)} searches: p < {0.05/len(GRID):.5f}")
    if len(te) < 40:
        print(f"  only {len(te)} test trades"); return
    v = [pnl(t["price"], t["win"]) for t in te]
    m = statistics.mean(v); se = statistics.pstdev(v) / math.sqrt(len(v))
    hit = statistics.mean(t["win"] for t in te)
    px = statistics.mean(t["price"] for t in te)
    print(f"  n={len(te)}  hit={hit:.1%}  avg px={px:.3f}  net/ct={m:+.4f}  "
          f"ROI={m/px:+.2%}  t={m/se:+.2f}")
    ITERS = 20000
    obs = sum(v); null = []
    for _ in range(ITERS):
        null.append(sum(pnl(t["price"], random.random() < t["price"]) for t in te))
    p = sum(1 for x in null if x >= obs) / ITERS
    print(f"  market null p={p:.4f}   corrected p={min(1.0, p*len(GRID)):.4f}")
    ok = (p * len(GRID) < 0.05) and m > 0
    print("  -> " + ("SURVIVES" if ok else
                     ("positive but not significant after correction" if m > 0 else "fails")))
    rank_table(test, best_frac, "\nTEST shape check")


if __name__ == "__main__":
    main()
