"""ORB-15 transplanted to Kalshi.

The user's Alpaca bot: 15-minute opening range, RVOL >= 1.5, entry before
11:30 ET, stop at the OR midpoint, NO profit target, hold to the 15:45 flatten.
Low win rate (37-40%), fat right tail, edge carried by rare large-move days.

WHY A NAIVE TRANSLATION FAILS, AND WHAT TO DO INSTEAD

ORB earns its living from convexity: an option keeps paying as the move
extends. A Kalshi binary pays exactly $1 whether the breakout runs 0.1% or 5%,
so buying "will it be up" after a breakout has already been priced in gives you
1.3x on a move that would have paid an option 10x. The tail - the entire source
of ORB's edge - is truncated.

The structural feature that gives it back: **Kalshi's 15-minute contracts reset
their strike to spot at every window open.** However far the trend has already
run, each new window starts at-the-money, priced near 50c. So if breakouts
actually trend, you can buy the breakout direction at ~50c, over and over, and
each one pays 2x. That is a bet on CONDITIONAL drift - P(up | breakout in
progress) > 0.5 - and it is a different hypothesis from anything tested so far,
which all concerned unconditional pricing.

The market prices these contracts as a driftless martingale (the fair-value
model in src/model.js has no drift term, and it correlates 0.976-0.990 with the
market price). So if conditional drift exists, the market is not charging for it.
That is the whole thesis.

METHOD - same discipline as everything else here:
  * train/test split by time, parameters chosen on train only
  * market null: redraw outcomes from the price actually paid
  * per-series and per-block reporting
  * Bonferroni correction over the parameter grid searched
"""
import json, os, math, statistics, sys, io, glob, random, collections, datetime as dt

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
random.seed(20260824)

FEE = 0.07
TRAIN_FRAC = 0.70

SPOT_FILES = {
    "KXBTC15M": "tape2/spot_BTC-USD.json",
    "KXETH15M": "tape2/spot_ETH-USD.json",
    "KXSOL15M": "tape2/spot_SOL-USD.json",
    "KXXRP15M": "tape2/spot_XRP-USD.json",
    "KXDOGE15M": "tape2/spot_DOGE-USD.json",
}


def fee_per_ct(price, contracts=100):
    return math.ceil(FEE * contracts * price * (1 - price) * 100) / 100 / contracts


def pnl(price, win, contracts=100):
    return ((1 - price) if win else -price) - fee_per_ct(price, contracts)


def load_spot(path):
    raw = json.load(open(path))
    bars = raw.get("bars", raw)
    out = {}
    for k, v in bars.items():
        try:
            out[int(k)] = {"o": float(v["open"]), "h": float(v["high"]),
                           "l": float(v["low"]), "c": float(v["close"]),
                           "v": float(v.get("vol", 0.0))}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def load_markets(series):
    p = f"tape2/{series}.json"
    if not os.path.exists(p):
        return []
    return json.load(open(p))


def opening_range(spot, anchor_ts, minutes):
    """High/low/volume of the `minutes` after the session anchor."""
    hi, lo, vol, n = None, None, 0.0, 0
    for t in range(anchor_ts, anchor_ts + minutes * 60, 60):
        b = spot.get(t)
        if not b:
            continue
        hi = b["h"] if hi is None else max(hi, b["h"])
        lo = b["l"] if lo is None else min(lo, b["l"])
        vol += b["v"]; n += 1
    if n < max(3, minutes // 3):
        return None
    return {"hi": hi, "lo": lo, "mid": (hi + lo) / 2, "vol": vol, "n": n}


def rvol(spot, anchor_ts, minutes, lookback_days=5):
    """Opening-range volume vs the same clock window on prior days."""
    cur = opening_range(spot, anchor_ts, minutes)
    if not cur:
        return None
    prior = []
    for d in range(1, lookback_days + 1):
        o = opening_range(spot, anchor_ts - d * 86400, minutes)
        if o and o["vol"] > 0:
            prior.append(o["vol"])
    if len(prior) < 2:
        return None
    base = statistics.median(prior)
    return cur["vol"] / base if base > 0 else None


def entry_price(m, side, delay_s):
    """Kalshi ask `delay_s` after the window opens, for the chosen side."""
    target = m["open_ts"] + delay_s
    best, bd = None, None
    for b in m["bars"]:
        ts, bid, ask = b.get("ts"), b.get("bid"), b.get("ask")
        if ts is None or bid is None or ask is None or ask < bid:
            continue
        if not (0 < bid <= 1 and 0 < ask <= 1):
            continue
        d = abs(ts - target)
        if bd is None or d < bd:
            best, bd = b, d
    if not best or bd is None or bd > 240:
        return None
    return best["ask"] if side == "yes" else 1 - best["bid"]


def build_trades(anchor_hour_utc, or_min, max_hours, rvol_min, delay_s,
                 require_mid=True):
    """One ORB signal per 15-minute window, across all crypto series."""
    trades = []
    for series, spath in SPOT_FILES.items():
        if not os.path.exists(spath):
            continue
        spot = load_spot(spath)
        markets = load_markets(series)
        if not spot or not markets:
            continue
        for m in markets:
            if m.get("result") not in ("yes", "no") or not m.get("strike"):
                continue
            o = m["open_ts"]
            day = o - (o % 86400)
            anchor = day + anchor_hour_utc * 3600
            if o < anchor:
                anchor -= 86400
            hours_since = (o - anchor) / 3600.0
            # ORB-15 enters only in the first couple of hours after the open.
            if hours_since < or_min / 60.0 or hours_since > max_hours:
                continue
            orange = opening_range(spot, anchor, or_min)
            if not orange:
                continue
            px = spot.get(o - 60)          # spot as the window opens
            if not px:
                continue
            p = px["c"]

            if p > orange["hi"]:
                side = "yes"
            elif p < orange["lo"]:
                side = "no"
            else:
                continue                    # inside the range: no signal

            # ORB-15 stops out at the OR midpoint; the analog for a
            # hold-to-expiry binary is simply not to enter once price has
            # fallen back through the midpoint.
            if require_mid:
                if side == "yes" and p < orange["mid"]:
                    continue
                if side == "no" and p > orange["mid"]:
                    continue

            if rvol_min:
                rv = rvol(spot, anchor, or_min)
                if rv is None or rv < rvol_min:
                    continue

            price = entry_price(m, side, delay_s)
            if price is None or not (0.02 < price < 0.98):
                continue
            win = (m["result"] == "yes") if side == "yes" else (m["result"] == "no")
            trades.append({
                "series": series, "open_ts": o, "side": side, "price": price,
                "win": win, "hours_since": hours_since,
                "ext": abs(p - (orange["hi"] if side == "yes" else orange["lo"])) / p,
            })
    return trades


def score(trades):
    if len(trades) < 40:
        return None
    v = [pnl(t["price"], t["win"]) for t in trades]
    m = statistics.mean(v)
    se = statistics.pstdev(v) / math.sqrt(len(v))
    return {"n": len(v), "mean": m, "t": m / se if se else 0, "total": sum(v),
            "hit": statistics.mean(t["win"] for t in trades),
            "avgpx": statistics.mean(t["price"] for t in trades)}


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
    trades.sort(key=lambda t: t["open_ts"])
    if not trades:
        return [], []
    cut = trades[int(len(trades) * TRAIN_FRAC)]["open_ts"]
    return [t for t in trades if t["open_ts"] < cut], [t for t in trades if t["open_ts"] >= cut]


def main():
    print("ORB-15 -> Kalshi 15-minute windows (crypto, 5 series)\n")

    # First: does the entry even happen near 50c? If Kalshi already prices the
    # breakout, the convexity this whole idea depends on is not there.
    probe = build_trades(0, 15, 6, None, 30)
    if probe:
        s = score(probe)
        print(f"sanity: {s['n']} raw breakout signals, average entry price "
              f"{s['avgpx']:.3f} (near 0.50 means the strike reset really does "
              f"re-arm the bet)\n")

    GRID = []
    for anchor in (0, 13, 14):                 # 00:00 UTC, 09:00/10:00 ET
        for or_min in (15, 30):
            for max_h in (2, 4, 8):
                for rv in (None, 1.5):
                    GRID.append((anchor, or_min, max_h, rv))
    print(f"TRAIN: searching {len(GRID)} parameter sets "
          f"(anchor x OR length x entry cutoff x RVOL)\n")

    results = []
    for (anchor, or_min, max_h, rv) in GRID:
        tr_all = build_trades(anchor, or_min, max_h, rv, 30)
        tr, te = split(tr_all)
        s = score(tr)
        if s:
            results.append(((anchor, or_min, max_h, rv), s, tr_all))
    if not results:
        print("no parameter set produced enough trades"); return
    results.sort(key=lambda r: -r[1]["t"])

    print(f"  {'anchor':>7}{'OR':>5}{'cutoff':>8}{'RVOL':>7}{'n':>7}{'hit':>7}{'net/ct':>10}{'t':>7}")
    for (a, om, mh, rv), s, _ in results[:10]:
        print(f"  {a:>5}:00{om:>5}{mh:>7}h{str(rv or '-'):>7}{s['n']:>7}"
              f"{s['hit']*100:>6.1f}%{s['mean']:>+10.4f}{s['t']:>+7.2f}")

    best_params, best_s, best_all = results[0]
    tr, te = split(best_all)
    print(f"\n  -> chosen on train: anchor {best_params[0]}:00 UTC, "
          f"OR {best_params[1]}min, cutoff {best_params[2]}h, RVOL {best_params[3] or 'off'}")
    print(f"     TRAIN n={best_s['n']} hit={best_s['hit']*100:.1f}% "
          f"net/ct={best_s['mean']:+.4f} t={best_s['t']:+.2f}")

    print("\n" + "=" * 74)
    print(f"TEST (held out). Bonferroni threshold for {len(GRID)} searches: "
          f"p < {0.05/len(GRID):.5f}")
    st = score(te)
    if not st:
        print("  too few test trades"); return
    p = null_p(te)
    print(f"  n={st['n']}  hit={st['hit']*100:.1f}%  avg px={st['avgpx']:.3f}")
    print(f"  net/ct={st['mean']:+.4f}  ROI={st['mean']/st['avgpx']:+.2%}  t={st['t']:+.2f}")
    print(f"  market null p={p:.4f}")
    print("  -> " + ("SURVIVES" if p < 0.05 / len(GRID) and st["mean"] > 0
                     else ("positive but not significant after correction"
                           if st["mean"] > 0 else "fails")))

    print("\nTEST by series")
    by = collections.defaultdict(list)
    for t in te: by[t["series"]].append(t)
    for k in sorted(by):
        s = score(by[k])
        if s:
            print(f"  {k:<14} n={s['n']:>5} hit={s['hit']*100:>5.1f}% net/ct={s['mean']:>+8.4f} t={s['t']:>+5.2f}")


if __name__ == "__main__":
    main()
