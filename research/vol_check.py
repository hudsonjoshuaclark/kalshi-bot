"""Is the 1-minute-candle volatility estimator right, or inflated?

Compare, over the same 100 hours of BTC history:
  A) what a 120-minute lookback of 1-minute returns PREDICTS for a 15-min move
  B) what 15-minute moves ACTUALLY were
  C) what Kalshi's own price IMPLIED at the time
"""
import json, math, statistics, sys, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

spot = {int(k): v["close"] for k, v in json.load(open("tape2/spot_BTC-USD.json")).items()}
mins = sorted(spot)
print(f"{len(mins)} spot minutes\n")


def est_sigma_per_min(ts, lookback):
    rs = []
    for k in range(lookback):
        t1, t0 = ts - 60 * k, ts - 60 * (k + 1)
        c1, c0 = spot.get(t1), spot.get(t0)
        if c1 and c0 and c1 > 0 and c0 > 0:
            rs.append(math.log(c1 / c0))
    if len(rs) < max(20, lookback // 3):
        return None
    return math.sqrt(sum(r * r for r in rs) / len(rs))


# --- A vs B: predicted 15-min sigma vs realised 15-min move -----------------
for lookback in [30, 60, 120]:
    preds, reals = [], []
    for i in range(lookback + 5, len(mins) - 15, 5):
        ts = mins[i]
        s = est_sigma_per_min(ts, lookback)
        if not s:
            continue
        p0, p1 = spot.get(ts), spot.get(ts + 15 * 60)
        if not p0 or not p1:
            continue
        preds.append(s * math.sqrt(15))
        reals.append(abs(math.log(p1 / p0)))
    if len(preds) < 50:
        continue
    # For a normal, E|r| = sigma * sqrt(2/pi). Compare implied sigmas.
    realised_sigma = statistics.mean(reals) / math.sqrt(2 / math.pi)
    pred_sigma = statistics.mean(preds)
    print(f"lookback {lookback:>3}min  n={len(preds):>4}  "
          f"predicted 15m sigma={pred_sigma*100:.4f}%   "
          f"realised 15m sigma={realised_sigma*100:.4f}%   "
          f"ratio={pred_sigma/realised_sigma:.3f}")

# --- C: what did Kalshi's price imply? --------------------------------------
print()
rows = json.load(open("tape2/KXBTC15M.json"))


def inv_norm(p):
    # Beasley-Springer-Moro is overkill; bisection on erf is fine offline.
    lo, hi = -8.0, 8.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if 0.5 * (1 + math.erf(mid / math.sqrt(2))) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


implied, modelled = [], []
for r in rows:
    K, close = r["strike"], r["close_ts"]
    if not K:
        continue
    for b in r["bars"]:
        ts, bid, ask = b.get("ts"), b.get("bid"), b.get("ask")
        if not ts or bid is None or ask is None:
            continue
        tau = close - ts
        if not (240 <= tau <= 600):
            continue
        mid = (bid + ask) / 2
        if not (0.05 < mid < 0.95):
            continue
        p_spot = spot.get(ts - 60)
        if not p_spot:
            continue
        dist = p_spot - K
        if abs(dist) < 1e-9:
            continue
        z = inv_norm(mid)
        if abs(z) < 0.15:
            continue
        sd = abs(dist / z)
        tau_eff = max(tau - 40, 1)
        imp = sd / (p_spot * math.sqrt(tau_eff))          # per sqrt(sec)
        s_min = est_sigma_per_min(ts - 60, 120)
        if not s_min:
            continue
        mdl = s_min / math.sqrt(60)                        # per sqrt(sec)
        if 0 < imp < 1:
            implied.append(imp)
            modelled.append(mdl)

if implied:
    mi = statistics.median(implied)
    mm = statistics.median(modelled)
    print(f"n={len(implied)}  median market-implied sigma={mi*1e5:.3f}e-5/sqrt(s)   "
          f"median model sigma={mm*1e5:.3f}e-5/sqrt(s)   model/market={mm/mi:.3f}")
    print(f"  as a 10-minute move: market {mi*math.sqrt(600)*100:.4f}%   model {mm*math.sqrt(600)*100:.4f}%")
