"""Does a live-spot fair-value model beat Kalshi's own quote?

Brier score is the scoreboard, not P&L: P&L over 400 markets is dominated by
whether BTC happened to drift up in the sample, whereas Brier measures pure
forecasting skill against the same outcomes the market was pricing.
"""
import json, math, statistics, sys, io, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

FEE_COEF = 0.07
AVG_W = 60.0


def fee(c, px):
    return math.ceil(FEE_COEF * c * px * (1 - px) * 100) / 100


def norm_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def fair_value(price, strike, tau, sigma_per_sqrt_sec, realised_mean=None, track=0.0002):
    """Same maths as src/model.js."""
    if not (price > 0 and strike > 0 and sigma_per_sqrt_sec > 0):
        return None
    sig_abs = price * sigma_per_sqrt_sec
    if tau > AVG_W:
        expected = price
        var = sig_abs ** 2 * (tau - 40)
    elif tau > 0:
        elapsed = AVG_W - tau
        rm = realised_mean if realised_mean else price
        expected = (elapsed * rm + tau * price) / AVG_W
        var = (sig_abs ** 2 * tau ** 3) / (3 * AVG_W ** 2)
    else:
        expected = realised_mean if realised_mean else price
        var = 0.0
    var += (price * track) ** 2
    sd = math.sqrt(var)
    if sd <= 0:
        return 1.0 if expected >= strike else 0.0
    return min(max(norm_cdf((expected - strike) / sd), 0.0005), 0.9995)


spot = {int(k): v for k, v in json.load(open("tape2/spot_BTC-USD.json")).items()}
rows = json.load(open("tape2/KXBTC15M.json"))
print(f"{len(rows)} markets, {len(spot)} spot minutes")

# trailing realised vol per second, from 1-min closes
mins = sorted(spot)
closes = {t: spot[t]["close"] for t in mins}


def sigma_at(ts, lookback=60):
    """sigma per sqrt(second) from the `lookback` minutes before ts."""
    rs = []
    for k in range(lookback):
        t1 = ts - 60 * k
        t0 = t1 - 60
        c1, c0 = closes.get(t1), closes.get(t0)
        if c1 and c0 and c1 > 0 and c0 > 0:
            rs.append(math.log(c1 / c0))
    if len(rs) < 20:
        return None
    var_per_min = sum(r * r for r in rs) / len(rs)
    return math.sqrt(var_per_min / 60.0)


obs = []
missing = 0
for r in rows:
    y = 1 if r["result"] == "yes" else 0
    K = r["strike"]
    close_ts = r["close_ts"]
    if not K:
        continue
    for b in r["bars"]:
        ts, bid, ask = b.get("ts"), b.get("bid"), b.get("ask")
        if ts is None or bid is None or ask is None or ask < bid:
            continue
        tau = close_ts - ts
        if tau <= 0 or tau > 15 * 60:
            continue
        # Kalshi bar ends at ts; the Coinbase minute covering [ts-60, ts) is keyed ts-60.
        p_spot = closes.get(ts - 60)
        if not p_spot:
            missing += 1
            continue
        sig = sigma_at(ts - 60)
        if not sig:
            continue
        mp = fair_value(p_spot, K, tau, sig)
        if mp is None:
            continue
        obs.append({"tau": tau, "left": tau / 60.0, "bid": bid, "ask": ask,
                    "mid": (bid + ask) / 2, "model": mp, "y": y,
                    "spot": p_spot, "K": K, "sig": sig})

print(f"{len(obs)} usable observations ({missing} missing spot)\n")


def brier(xs, key):
    return statistics.mean((o[key] - o["y"]) ** 2 for o in xs)


print(f"{'mins left':>10} {'n':>6} {'Brier mkt':>10} {'Brier model':>12} {'skill':>8} {'corr(mdl,mkt)':>14}")
BUCKETS = [(0, 1), (1, 2), (2, 3), (3, 5), (5, 7), (7, 10), (10, 15.1)]
for lo, hi in BUCKETS:
    g = [o for o in obs if lo <= o["left"] < hi]
    if len(g) < 50:
        continue
    bm, bd = brier(g, "mid"), brier(g, "model")
    skill = (bm - bd) / bm if bm > 0 else 0
    mm = statistics.mean(o["mid"] for o in g)
    md = statistics.mean(o["model"] for o in g)
    sm = statistics.pstdev([o["mid"] for o in g]) or 1e-9
    sd_ = statistics.pstdev([o["model"] for o in g]) or 1e-9
    cov = statistics.mean((o["mid"] - mm) * (o["model"] - md) for o in g)
    print(f"{lo:>4}-{hi:<5} {len(g):>6} {bm:>10.4f} {bd:>12.4f} {skill:>+8.2%} {cov/(sm*sd_):>14.3f}")

allb_m, allb_d = brier(obs, "mid"), brier(obs, "model")
print(f"\nOVERALL  n={len(obs)}  Brier market={allb_m:.4f}  model={allb_d:.4f}"
      f"  skill={(allb_m-allb_d)/allb_m:+.2%}")

# --- trading rule: act when the model disagrees enough to clear friction ---
print("\nTRADING ON DISAGREEMENT (1 contract, crossing the spread, fee included)")
print(f"{'thresh':>7} {'n trades':>9} {'pnl/ct':>9} {'total$':>9} {'hit%':>7} {'t-stat':>7}")
for thresh in [0.02, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20]:
    pnls = []
    for o in obs:
        # buy YES at ask if model says it is cheap
        if o["model"] - o["ask"] > thresh and 0 < o["ask"] < 1:
            pnls.append(o["y"] - o["ask"] - fee(1, o["ask"]))
        # buy NO at (1-bid) if model says YES is dear
        elif (1 - o["model"]) - (1 - o["bid"]) > thresh and 0 < 1 - o["bid"] < 1:
            px = 1 - o["bid"]
            pnls.append((1 - o["y"]) - px - fee(1, px))
    if len(pnls) < 20:
        print(f"{thresh:>7.2f} {len(pnls):>9} {'--':>9}")
        continue
    m = statistics.mean(pnls)
    se = statistics.pstdev(pnls) / math.sqrt(len(pnls))
    hit = statistics.mean(1 if p > 0 else 0 for p in pnls)
    print(f"{thresh:>7.2f} {len(pnls):>9} {m:>+9.4f} {sum(pnls):>+9.2f} {hit:>7.1%} {m/se if se else 0:>+7.2f}")
