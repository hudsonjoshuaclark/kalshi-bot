"""DEPRECATED historical S2 volatility backtest -- do not trade this output.

This file is retained to document the prior result.  It has two fatal defects:
Coinbase candle key ``t`` was paired with a Kalshi candle ending at ``t`` even
though that Coinbase candle covers the following minute, and absolute-price IV
was divided by relative log-return RV.  Those errors created the reported
~90x gap.  Use s7_vol_retest.py through s10_vol_final.py instead.

The normal-CDF inversion and realised-volatility estimator use only the local
Kalshi and Coinbase files.  One bar (one possible trade) is used per market.
The oldest 70% of close times selects entry minute, gap threshold, and whether
to trade high- or low-implied-volatility observations; the newer 30% is held
out until that selection is complete.

Run: python s2_vol.py
"""
import glob
import os
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone

SEED = 20260831
N_NULL = 20_000
COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
ENTRY_MINUTES = (1, 3, 5)
RATIO_THRESHOLDS = (1.25, 1.50, 2.00, 3.00)
REGIMES = ("iv_high", "iv_low")
COMBINATIONS = len(ENTRY_MINUTES) * len(RATIO_THRESHOLDS) * len(REGIMES)


def fee(price):
    return math.ceil(0.07 * price * (1.0 - price) * 100.0 - 1e-12) / 100.0


def iso_week(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%G-W%V")


def norm_ppf(p):
    """Acklam's inverse standard-normal CDF approximation (stdlib-only)."""
    if not 0.0 < p < 1.0:
        raise ValueError("normal inverse needs a probability strictly inside (0,1)")
    a = (-39.6968302866538, 220.946098424521, -275.928510446969,
         138.357751867269, -30.6647980661472, 2.50662827745924)
    b = (-54.4760987982241, 161.585836858041, -155.698979859887,
         66.8013118877197, -13.2806815528857)
    c = (-0.00778489400243029, -0.322396458041136, -2.40075827716184,
         -2.54973253934373, 4.37466414146497, 2.93816398269878)
    d = (0.00778469570904146, 0.32246712907004, 2.445134137143,
         3.75440866190742)
    if p < 0.02425:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > 1.0 - 0.02425:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                 ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


# Data directory is switchable so the same test can run against the small
# original sample (tape2/) or the ~9-week deep pull (deep/). The whole point of
# re-running S7 is more HELD-OUT data, so the loader must not be hardcoded.
DATA_DIR = os.environ.get("S7_DATA", "tape2")


def load_spot(coin):
    with open("%s/spot_%s-USD.json" % (DATA_DIR, coin), "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return {int(ts): row["close"] for ts, row in raw.items()
            if isinstance(row.get("close"), (int, float)) and row["close"] > 0.0}


def load_markets(coin):
    with open("%s/KX%s15M.json" % (DATA_DIR, coin), "r", encoding="utf-8") as handle:
        return json.load(handle)


def observed_market(market, coin, spots, entry_minute):
    """Build a single ex-ante observation.  Missing one-minute data is dropped."""
    entry_ts = market["open_ts"] + 60 * entry_minute
    bar = next((item for item in market.get("bars", []) if item.get("ts") == entry_ts), None)
    if bar is None:
        return None
    bid, ask = bar.get("bid"), bar.get("ask")
    if not (isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and
            0.0 < bid < ask < 1.0):
        return None
    closes = [spots.get(entry_ts - 60 * minute) for minute in range(30, -1, -1)]
    if any(value is None or value <= 0.0 for value in closes):
        return None
    returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]
    mean_return = sum(returns) / len(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / (len(returns) - 1)
    # Both values are volatilities per sqrt(second), so their ratio is unit-free.
    realised = math.sqrt(variance) / math.sqrt(60.0)
    tau_eff = market["close_ts"] - entry_ts - 40
    spot = closes[-1]
    strike = market.get("strike")
    mid = (bid + ask) / 2.0
    if not (realised > 0.0 and tau_eff > 0 and isinstance(strike, (int, float))):
        return None
    z = norm_ppf(mid)
    displacement = spot - strike
    # Sigma must be positive under the supplied P(yes) formula.  Contradictory
    # spot/price observations are not forced through an invalid inversion.
    if abs(z) < 1e-5 or displacement * z <= 0.0:
        return None
    implied = abs(displacement / z) / math.sqrt(tau_eff)
    if not math.isfinite(implied) or implied <= 0.0:
        return None
    close_spot = spots.get(market["close_ts"])
    underlying_return = (math.log(close_spot / spot) if close_spot and close_spot > 0 else None)
    return {
        "ticker": market["ticker"], "coin": coin, "ts": entry_ts,
        "close_ts": market["close_ts"], "week": iso_week(market["close_ts"]),
        "outcome": 1 if market["result"] == "yes" else 0,
        "bid": bid, "ask": ask, "spot": spot, "strike": strike,
        "iv": implied, "rv": realised, "ratio": implied / realised,
        "spot_above_strike": displacement > 0.0,
        "underlying_return": underlying_return,
    }


def make_trade(obs, regime):
    """High IV buys the spot-implied favourite; low IV buys the opposite side."""
    favourite_yes = obs["spot_above_strike"]
    buy_yes = favourite_yes if regime == "iv_high" else not favourite_yes
    paid = obs["ask"] if buy_yes else 1.0 - obs["bid"]
    success = obs["outcome"] if buy_yes else 1 - obs["outcome"]
    trade = dict(obs)
    trade.update({"side": "YES" if buy_yes else "NO", "p_paid": paid,
                  "pnl": success - paid - fee(paid), "fee": fee(paid)})
    return trade


def candidate_trades(observations, threshold, regime):
    result = []
    for obs in observations:
        if regime == "iv_high" and obs["ratio"] >= threshold:
            result.append(make_trade(obs, regime))
        elif regime == "iv_low" and obs["ratio"] <= 1.0 / threshold:
            result.append(make_trade(obs, regime))
    return result


def pnl_per_trade(rows):
    return sum(row["pnl"] for row in rows) / len(rows) if rows else float("-inf")


def market_null(rows):
    observed = sum(row["pnl"] for row in rows)
    rng = random.Random(SEED)
    at_least = 0
    for _ in range(N_NULL):
        simulated = 0.0
        for row in rows:
            simulated += (1.0 if rng.random() < row["p_paid"] else 0.0) - row["p_paid"] - row["fee"]
        if simulated >= observed - 1e-12:
            at_least += 1
    return (at_least + 1) / (N_NULL + 1)


def correlation(xs, ys):
    if len(xs) < 2:
        return float("nan")
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(vx * vy) if vx and vy else float("nan")


def median(values):
    values = sorted(values)
    if not values:
        return float("nan")
    half = len(values) // 2
    return values[half] if len(values) % 2 else (values[half - 1] + values[half]) / 2.0


def relation_report(label, rows):
    ratios = [row["ratio"] for row in rows]
    log_iv = [math.log(row["iv"]) for row in rows]
    log_rv = [math.log(row["rv"]) for row in rows]
    print("%s: n=%d median IV/RV=%.3f, mean IV/RV=%.3f, IV>RV=%.1f%%, corr(log IV, log RV)=%.3f" %
          (label, len(rows), median(ratios), sum(ratios) / len(ratios),
           100.0 * sum(value > 1.0 for value in ratios) / len(ratios),
           correlation(log_iv, log_rv)))


def print_breakdown(rows, key, title):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    print("  %s:" % title)
    for name in sorted(groups, key=str):
        group = groups[name]
        pnl = sum(row["pnl"] for row in group)
        print("    %-12s n=%4d pnl=%+7.2f pnl/trade=%+7.4f" %
              (str(name), len(group), pnl, pnl / len(group)))


def main():
    raise SystemExit(
        "DEPRECATED: s2_vol.py contains a one-minute look-ahead and an IV/RV "
        "units mismatch. Run s7_vol_retest.py, s8_vol_estimators.py, "
        "s9_vol_cost_filters.py, then s10_vol_final.py."
    )
    by_minute = {minute: [] for minute in ENTRY_MINUTES}
    for coin in COINS:
        spots = load_spot(coin)
        for market in load_markets(coin):
            for minute in ENTRY_MINUTES:
                obs = observed_market(market, coin, spots, minute)
                if obs is not None:
                    by_minute[minute].append(obs)
    split_reference = sorted(by_minute[1], key=lambda row: (row["close_ts"], row["ticker"]))
    cut_ts = split_reference[int(0.70 * len(split_reference))]["close_ts"]
    train = {minute: [row for row in rows if row["close_ts"] < cut_ts]
             for minute, rows in by_minute.items()}
    test = {minute: [row for row in rows if row["close_ts"] >= cut_ts]
            for minute, rows in by_minute.items()}
    print("S2 REALISED VERSUS IMPLIED VOLATILITY")
    print("Chronological split at %s.  One entry observation per market." %
          datetime.fromtimestamp(cut_ts, timezone.utc).isoformat())
    print("Train-only search: entry minute %s x IV/RV threshold %s x regime %s = %d combinations." %
          (ENTRY_MINUTES, RATIO_THRESHOLDS, REGIMES, COMBINATIONS))
    for minute in ENTRY_MINUTES:
        relation_report("Train relation, minute %d" % minute, train[minute])
    # Only the train set is used to select a single specification.
    candidates = []
    for minute in ENTRY_MINUTES:
        for threshold in RATIO_THRESHOLDS:
            for regime in REGIMES:
                rows = candidate_trades(train[minute], threshold, regime)
                if len(rows) >= 20:
                    candidates.append((pnl_per_trade(rows), minute, threshold, regime, rows))
    if not candidates:
        raise RuntimeError("no train candidate had 20 independent market observations")
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, minute, threshold, regime, train_trades = candidates[0]
    # Test data is first used here, after the selected parameters are frozen.
    test_trades = candidate_trades(test[minute], threshold, regime)
    if not test_trades:
        raise RuntimeError("selected train rule made no held-out trades")
    raw_p = market_null(test_trades)
    corrected_p = min(1.0, raw_p * COMBINATIONS)
    print("\nSelected on train: entry minute=%d, regime=%s, IV/RV threshold=%.2f." %
          (minute, regime, threshold))
    relation_report("Held-out relation at selected minute", test[minute])
    for label, rows in (("train", train_trades), ("test", test_trades)):
        total = sum(row["pnl"] for row in rows)
        fees = sum(row["fee"] for row in rows)
        print("%s trades=%d pnl=%+.2f pnl/trade=%+.4f fees=%.2f" %
              (label, len(rows), total, total / len(rows), fees))
    print("Held-out market null p(null >= ours)=%.5f; Bonferroni x%d => %.5f." %
          (raw_p, COMBINATIONS, corrected_p))
    print_breakdown(test_trades, lambda row: row["coin"], "held-out series")
    print_breakdown(test_trades, lambda row: row["week"], "held-out ISO week")
    print_breakdown(test_trades, lambda row: row["side"], "held-out purchased side")
    with_return = [row for row in test_trades if row["underlying_return"] is not None]
    if with_return:
        print_breakdown(with_return,
                        lambda row: "underlying up" if row["underlying_return"] > 0 else "underlying down/flat",
                        "drift check: held-out underlying move to settlement")
        avg_return = sum(row["underlying_return"] for row in with_return) / len(with_return)
        print("  mean underlying log return on held-out trades: %+.5f (%d/%d have a closing spot)" %
              (avg_return, len(with_return), len(test_trades)))
    concentration = sorted((row["pnl"] for row in test_trades), reverse=True)[:5]
    print("  concentration check: five best trades contributed %+.2f of total held-out P&L %+.2f." %
          (sum(concentration), sum(row["pnl"] for row in test_trades)))
    print("Verdict rule: only call this promising if held-out P&L is positive and corrected p < 0.05.")


if __name__ == "__main__":
    main()
