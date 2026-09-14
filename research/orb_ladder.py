"""ORB with a CONVEX payoff, on Kalshi's multi-strike ladders.

This is the faithful translation of ORB-15. The earlier attempt (orb.py) bet on
15-minute at-the-money contracts and failed, but it was also testing the wrong
thing: ORB-15 holds for ~6 hours, not 15 minutes, and it earns its living from
convexity - rare large-move days paying many multiples - which an at-the-money
binary truncates at 1x.

A strike LADDER restores both properties:

  * Hold to the daily/weekly settlement, matching ORB's real holding period.
  * Buy an OUT-OF-THE-MONEY strike, so the contract is cheap (8-30c) and pays
    3-12x if the breakout extends far enough to reach it. Low win rate, fat
    right tail - the same shape as ORB's options.

No external price feed is needed: the ladder implies the underlying. Yes-prices
fall monotonically with strike, so the strike where the ladder crosses 0.50 is
the market's own estimate of spot, and interpolating that crossing through time
reconstructs the intraday path.

Same discipline as everything else: train/test split by time, parameters chosen
on train only, market null, Bonferroni correction over the searched grid.
"""
import json, os, math, statistics, sys, io, glob, random, collections, datetime as dt

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
random.seed(20260824)

FEE = 0.07
TRAIN_FRAC = 0.70


def fee_per_ct(price, contracts=100):
    return math.ceil(FEE * contracts * price * (1 - price) * 100) / 100 / contracts


def pnl(price, win, contracts=100):
    return ((1 - price) if win else -price) - fee_per_ct(price, contracts)


def load_events(pattern="ladders/*.json"):
    """Group markets into events, keeping only simple above/below ladders."""
    events = collections.defaultdict(list)
    for p in sorted(glob.glob(pattern)):
        try:
            rows = json.load(open(p))
        except Exception:
            continue
        for m in rows:
            # Daily/weekly commodity ladders use "greater"; the 15-minute
            # crypto contracts use "greater_or_equal". Both are simple
            # above/below rungs, so both qualify.
            if m.get("strike_type") not in ("greater", "greater_or_equal"):
                continue
            if not m.get("strike"):
                continue
            # collect_broad.py did not record the event ticker; it is the market
            # ticker minus its strike suffix.
            ev = m.get("event") or m["ticker"].rsplit("-", 1)[0]
            events[(m["series"], ev)].append(m)
    # A ladder needs enough rungs to have an out-of-the-money one to buy.
    return {k: v for k, v in events.items() if len(v) >= 8}


def ladder_at(markets, ts):
    """(strike, yes_bid, yes_ask) across the ladder at time `ts`."""
    out = []
    for m in markets:
        best, bd = None, None
        for b in m["bars"]:
            t = b.get("ts")
            if t is None or b.get("bid") is None or b.get("ask") is None:
                continue
            if b["ask"] < b["bid"] or not (0 <= b["bid"] <= 1 and 0 <= b["ask"] <= 1):
                continue
            d = abs(t - ts)
            if bd is None or d < bd:
                best, bd = b, d
        if best is None or bd is None or bd > 3 * 3600:
            continue
        out.append((m["strike"], best["bid"], best["ask"], m))
    out.sort(key=lambda r: r[0])
    return out


def implied_spot(rung):
    """
    Where the ladder crosses 0.50.

    Yes-price falls as strike rises, so the crossing is the market's estimate of
    the underlying. Interpolating between the two rungs that straddle 0.50 gives
    a continuous path from discrete strikes.
    """
    pts = [(s, (b + a) / 2) for (s, b, a, _) in rung]
    pts = [(s, p) for (s, p) in pts if 0 < p < 1]
    if len(pts) < 2:
        return None
    for (s1, p1), (s2, p2) in zip(pts, pts[1:]):
        if (p1 >= 0.5 >= p2) and p1 != p2:
            w = (p1 - 0.5) / (p1 - p2)
            return s1 + w * (s2 - s1)
    return None


def timeline(markets):
    """Sorted bar timestamps common to the event."""
    ts = set()
    for m in markets:
        for b in m["bars"]:
            if b.get("ts") is not None:
                ts.add(b["ts"])
    return sorted(ts)


def build(events, or_bars, max_entry_bars, lo_px, hi_px, min_ext):
    """One ORB trade per event, expressed as an out-of-the-money strike."""
    trades = []
    for (series, ev), markets in events.items():
        close_ts = max(m["close_ts"] for m in markets)
        open_ts = min(m["open_ts"] for m in markets)
        tl = [t for t in timeline(markets) if open_ts <= t <= close_ts]
        if len(tl) < or_bars + 3:
            continue

        # opening range from the implied-spot path
        path = []
        for t in tl:
            sp = implied_spot(ladder_at(markets, t))
            path.append((t, sp))
        opening = [sp for (_, sp) in path[:or_bars] if sp]
        if len(opening) < max(2, or_bars - 1):
            continue
        hi, lo = max(opening), min(opening)
        mid = (hi + lo) / 2
        if hi <= lo:
            continue

        for idx in range(or_bars, min(len(path), or_bars + max_entry_bars)):
            t, sp = path[idx]
            if sp is None:
                continue
            if sp > hi:
                side, ext = "yes", (sp - hi) / hi
            elif sp < lo:
                side, ext = "no", (lo - sp) / lo
            else:
                continue
            if ext < min_ext:
                continue
            # ORB stops out at the OR midpoint; for a hold-to-expiry binary the
            # analog is simply refusing to enter once price is back through it.
            if (side == "yes" and sp < mid) or (side == "no" and sp > mid):
                continue

            rung = ladder_at(markets, t)
            if len(rung) < 6:
                continue
            # Pick the cheapest rung inside the target price band, in the
            # breakout direction: that is the convex bet.
            cands = []
            for (strike, bid, ask, m) in rung:
                if side == "yes":
                    if strike <= sp:      # must be OUT of the money
                        continue
                    px, win = ask, (m["result"] == "yes")
                else:
                    if strike >= sp:
                        continue
                    px, win = 1 - bid, (m["result"] == "no")
                if lo_px <= px <= hi_px:
                    cands.append((px, strike, win, m))
            if not cands:
                continue
            cands.sort()
            px, strike, win, m = cands[0]
            trades.append({"series": series, "event": ev, "open_ts": t,
                           "side": side, "price": px, "win": win,
                           "payoff": 1 / px, "ext": ext})
            break          # one trade per event, like one ORB entry per day
    return trades


def score(trades, min_n=25):
    if len(trades) < min_n:
        return None
    v = [pnl(t["price"], t["win"]) for t in trades]
    m = statistics.mean(v)
    se = statistics.pstdev(v) / math.sqrt(len(v))
    return {"n": len(v), "mean": m, "t": m / se if se else 0, "total": sum(v),
            "hit": statistics.mean(t["win"] for t in trades),
            "avgpx": statistics.mean(t["price"] for t in trades),
            "payoff": statistics.mean(t["payoff"] for t in trades)}


def null_p(trades, iters=20000):
    obs = sum(pnl(t["price"], t["win"]) for t in trades)
    hits = 0
    for _ in range(iters):
        tot = 0.0
        for t in trades:
            tot += pnl(t["price"], random.random() < t["price"])
        if tot >= obs:
            hits += 1
    return hits / iters


def split(trades):
    trades = sorted(trades, key=lambda t: t["open_ts"])
    if not trades:
        return [], []
    cut = trades[int(len(trades) * TRAIN_FRAC)]["open_ts"]
    return [t for t in trades if t["open_ts"] < cut], [t for t in trades if t["open_ts"] >= cut]


def main():
    events = load_events(sys.argv[1] if len(sys.argv) > 1 else "ladders/*.json")
    if not events:
        print("no ladder data yet (run collect_ladders.py)"); return
    by_series = collections.Counter(s for (s, _) in events)
    print(f"{len(events)} events with a usable ladder, across {len(by_series)} series")
    for s, n in by_series.most_common():
        print(f"   {s:<14} {n:>4} events")

    GRID = []
    for or_bars in (2, 3, 4):
        for max_entry in (3, 6, 12):
            for band in ((0.05, 0.20), (0.10, 0.35), (0.20, 0.50)):
                for min_ext in (0.0, 0.001):
                    GRID.append((or_bars, max_entry, band, min_ext))

    print(f"\nTRAIN: searching {len(GRID)} parameter sets\n")
    print(f"  {'OR':>4}{'entry':>7}{'band':>14}{'ext':>7}{'n':>6}{'hit':>7}"
          f"{'avg px':>8}{'payoff':>8}{'net/ct':>10}{'t':>7}")
    results = []
    for (ob, me, band, mx) in GRID:
        allt = build(events, ob, me, band[0], band[1], mx)
        tr, te = split(allt)
        s = score(tr)
        if s:
            results.append(((ob, me, band, mx), s, allt))
    if not results:
        print("  no parameter set produced enough trades"); return
    results.sort(key=lambda r: -r[1]["t"])
    for (ob, me, band, mx), s, _ in results[:10]:
        print(f"  {ob:>4}{me:>7}{str(band):>14}{mx:>7}{s['n']:>6}{s['hit']*100:>6.1f}%"
              f"{s['avgpx']:>8.3f}{s['payoff']:>7.1f}x{s['mean']:>+10.4f}{s['t']:>+7.2f}")

    params, s_tr, allt = results[0]
    tr, te = split(allt)
    print(f"\n  -> chosen on train: OR={params[0]} bars, entry<= {params[1]} bars, "
          f"price band {params[2]}, min extension {params[3]}")
    print(f"     TRAIN n={s_tr['n']} hit={s_tr['hit']*100:.1f}% "
          f"avg px={s_tr['avgpx']:.3f} ({s_tr['payoff']:.1f}x) net/ct={s_tr['mean']:+.4f} t={s_tr['t']:+.2f}")

    print("\n" + "=" * 76)
    print(f"TEST (held out). Bonferroni threshold for {len(GRID)} searches: p < {0.05/len(GRID):.5f}")
    st = score(te)
    if not st:
        print(f"  only {len(te)} test trades - not enough to conclude"); return
    p = null_p(te)
    print(f"  n={st['n']}  hit={st['hit']*100:.1f}%  avg px={st['avgpx']:.3f} "
          f"({st['payoff']:.1f}x)")
    print(f"  net/ct={st['mean']:+.4f}  ROI={st['mean']/st['avgpx']:+.2%}  t={st['t']:+.2f}")
    print(f"  market null p={p:.4f}")
    ok = p < 0.05 / len(GRID) and st["mean"] > 0
    print("  -> " + ("SURVIVES" if ok else
                     ("positive but not significant after correction" if st["mean"] > 0 else "fails")))


if __name__ == "__main__":
    main()
