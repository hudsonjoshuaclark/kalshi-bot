"""Shared, timestamp-safe machinery for the Kalshi volatility studies.

Only the Python standard library is used.  Kalshi candles are stamped with the
end of their minute.  Coinbase candles are keyed by the start of their minute,
so the Coinbase candle known at a Kalshi timestamp ``t`` is keyed ``t - 60``.

The supplied inversion produces an *absolute-price* volatility (dollars, or
coin-price units, per sqrt(second)).  Realised log-return volatility is
therefore multiplied by current spot before an IV/RV ratio is formed.
"""

import hashlib
import json
import math
import os
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone


COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
SERIES = {coin: "KX%s15M" % coin for coin in COINS}
SPOT_FILES = {coin: "spot_%s-USD.json" % coin for coin in COINS}
N_NULL = 20_000
SEED = 20260831


ESTIMATORS = {
    # kind, lookback returns/minutes, optional EWMA half-life
    "std30": ("std", 30, None),
    "std60": ("std", 60, None),
    "std120": ("std", 120, None),
    "rms60": ("rms", 60, None),
    "ewma30": ("ewma", 120, 30.0),
    "park60": ("parkinson", 60, None),
    "bipower60": ("bipower", 60, None),
}


def fee(contracts, price):
    """Kalshi entry fee in dollars, including the per-order cent ceiling."""
    return math.ceil(0.07 * contracts * price * (1.0 - price) * 100.0 - 1e-12) / 100.0


def norm_ppf(p):
    """Acklam inverse-standard-normal approximation."""
    if not 0.0 < p < 1.0:
        raise ValueError("normal inverse needs 0 < p < 1")
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


def iso_time(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def iso_week(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%G-W%V")


def iso_day(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def median(values):
    return statistics.median(values) if values else float("nan")


def snapshot(data_dir):
    """Small reproducibility fingerprint without hashing multi-GB contents."""
    rows = []
    for coin in COINS:
        for name in (SERIES[coin] + ".json", SPOT_FILES[coin]):
            path = os.path.join(data_dir, name)
            stat = os.stat(path)
            # Lists survive a JSON save/load round trip unchanged; tuples do not.
            rows.append([name, stat.st_size, stat.st_mtime_ns])
    encoded = json.dumps(rows, separators=(",", ":")).encode("utf-8")
    return {"files": rows, "sha256_metadata": hashlib.sha256(encoded).hexdigest()}


def require_complete_data(data_dir):
    missing = []
    for coin in COINS:
        for name in (SERIES[coin] + ".json", SPOT_FILES[coin]):
            path = os.path.join(data_dir, name)
            if not os.path.isfile(path) or os.path.getsize(path) == 0:
                missing.append(path)
    if missing:
        raise RuntimeError("deep dataset is incomplete; missing: %s" % ", ".join(missing))


def load_data(data_dir="deep"):
    require_complete_data(data_dir)
    markets = {}
    spots = {}
    for coin in COINS:
        with open(os.path.join(data_dir, SERIES[coin] + ".json"), "r", encoding="utf-8") as handle:
            markets[coin] = json.load(handle)
        with open(os.path.join(data_dir, SPOT_FILES[coin]), "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        clean = {}
        for key, value in raw.items():
            try:
                ts = int(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(value, dict):
                continue
            row = {}
            for field in ("open", "high", "low", "close", "vol"):
                item = value.get(field)
                if isinstance(item, (int, float)) and math.isfinite(item):
                    row[field] = float(item)
            if all(field in row and row[field] > 0.0 for field in ("open", "high", "low", "close")):
                clean[ts] = row
        spots[coin] = clean
    return markets, spots


def chronological_cut(markets, fraction=0.70):
    refs = []
    for coin, rows in markets.items():
        for market in rows:
            close_ts = market.get("close_ts")
            if isinstance(close_ts, (int, float)):
                refs.append((int(close_ts), str(market.get("ticker", "")), coin))
    if not refs:
        raise RuntimeError("no timestamped markets")
    refs.sort()
    cut = refs[int(fraction * len(refs))][0]
    n_train = sum(close_ts < cut for close_ts, _, _ in refs)
    return cut, n_train, len(refs) - n_train, len(refs)


def _returns_and_ranges(spot, spot_key, max_lookback):
    """Return histories ending at the last candle complete at entry."""
    keys = [spot_key - 60 * offset for offset in range(max_lookback, -1, -1)]
    candles = [spot.get(key) for key in keys]
    if any(row is None for row in candles):
        return None, None
    closes = [row["close"] for row in candles]
    returns = []
    for index in range(1, len(closes)):
        if closes[index - 1] <= 0.0 or closes[index] <= 0.0:
            return None, None
        returns.append(math.log(closes[index] / closes[index - 1]))
    # There are max_lookback+1 candles.  The last max_lookback ranges end at
    # entry and line up with the max_lookback close-to-close returns.
    ranges = [math.log(row["high"] / row["low"]) for row in candles[1:]]
    return returns, ranges


def relative_volatility(returns, ranges, estimator):
    kind, lookback, half_life = ESTIMATORS[estimator]
    if len(returns) < lookback or len(ranges) < lookback:
        return None
    rs = returns[-lookback:]
    hs = ranges[-lookback:]
    if kind == "std":
        if lookback < 2:
            return None
        mean = sum(rs) / lookback
        variance_per_minute = sum((value - mean) ** 2 for value in rs) / (lookback - 1)
    elif kind == "rms":
        variance_per_minute = sum(value * value for value in rs) / lookback
    elif kind == "ewma":
        decay = math.exp(-math.log(2.0) / half_life)
        weights = [decay ** (lookback - 1 - index) for index in range(lookback)]
        denom = sum(weights)
        variance_per_minute = sum(weight * value * value for weight, value in zip(weights, rs)) / denom
    elif kind == "parkinson":
        variance_per_minute = sum(value * value for value in hs) / (4.0 * math.log(2.0) * lookback)
    elif kind == "bipower":
        if lookback < 2:
            return None
        variance_per_minute = (math.pi / 2.0) * sum(
            abs(rs[index]) * abs(rs[index - 1]) for index in range(1, lookback)
        ) / (lookback - 1)
    else:
        raise ValueError("unknown estimator kind %s" % kind)
    if not math.isfinite(variance_per_minute) or variance_per_minute <= 0.0:
        return None
    return math.sqrt(variance_per_minute / 60.0)


def build_observations(markets, spots, entries, estimators, cut_ts=None, partition=None):
    """Build at most one observation per market for each entry/estimator pair.

    ``partition`` may be ``train`` or ``test``.  Filtering happens before the
    outcome is copied, which makes the training scripts mechanically unable to
    score held-out outcomes.
    """
    entries = tuple(sorted(set(entries)))
    estimators = tuple(dict.fromkeys(estimators))
    max_lookback = max(ESTIMATORS[name][1] for name in estimators)
    result = {(entry, estimator): [] for entry in entries for estimator in estimators}
    for coin in COINS:
        spot = spots[coin]
        for market in markets[coin]:
            close_ts = market.get("close_ts")
            open_ts = market.get("open_ts")
            strike = market.get("strike")
            if not all(isinstance(value, (int, float)) for value in (close_ts, open_ts, strike)):
                continue
            close_ts = int(close_ts)
            open_ts = int(open_ts)
            if partition == "train" and not close_ts < cut_ts:
                continue
            if partition == "test" and not close_ts >= cut_ts:
                continue
            outcome = 1 if market.get("result") == "yes" else 0 if market.get("result") == "no" else None
            if outcome is None:
                continue
            bars = {int(bar["ts"]): bar for bar in market.get("bars", [])
                    if isinstance(bar.get("ts"), (int, float))}
            for entry in entries:
                entry_ts = open_ts + 60 * entry
                tau_eff = close_ts - entry_ts - 40
                bar = bars.get(entry_ts)
                if bar is None or tau_eff <= 0:
                    continue
                bid, ask = bar.get("bid"), bar.get("ask")
                if not (isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and
                        0.0 <= bid < ask <= 1.0):
                    continue
                mid = (float(bid) + float(ask)) / 2.0
                if not 0.0 < mid < 1.0:
                    continue
                # Coinbase key is candle start; this candle closes at entry_ts.
                spot_key = entry_ts - 60
                current = spot.get(spot_key)
                if current is None:
                    continue
                spot_price = current["close"]
                displacement = spot_price - float(strike)
                z = norm_ppf(mid)
                if abs(z) < 1e-8 or displacement * z <= 0.0:
                    continue
                implied_abs = abs(displacement / z) / math.sqrt(tau_eff)
                if not math.isfinite(implied_abs) or implied_abs <= 0.0:
                    continue
                returns, ranges = _returns_and_ranges(spot, spot_key, max_lookback)
                if returns is None:
                    continue
                close_candle = spot.get(close_ts - 60)
                underlying_return = None
                if close_candle is not None and close_candle["close"] > 0.0:
                    underlying_return = math.log(close_candle["close"] / spot_price)
                base = {
                    "ticker": market.get("ticker"), "series": market.get("series", SERIES[coin]),
                    "coin": coin, "entry": entry, "ts": entry_ts, "close_ts": close_ts,
                    "day": iso_day(close_ts), "week": iso_week(close_ts), "outcome": outcome,
                    "bid": float(bid), "ask": float(ask), "mid": mid,
                    "spread": float(ask) - float(bid), "spot": spot_price,
                    "strike": float(strike), "z": z, "abs_z": abs(z),
                    "iv_abs": implied_abs, "spot_above_strike": displacement > 0.0,
                    "underlying_return": underlying_return,
                }
                for estimator in estimators:
                    rel_rv = relative_volatility(returns, ranges, estimator)
                    if rel_rv is None:
                        continue
                    # Critical units fix: price-return sigma -> absolute-price sigma.
                    realised_abs = spot_price * rel_rv
                    row = dict(base)
                    row.update({"estimator": estimator, "rv_rel": rel_rv,
                                "rv_abs": realised_abs, "ratio": implied_abs / realised_abs})
                    result[(entry, estimator)].append(row)
    for rows in result.values():
        rows.sort(key=lambda row: (row["close_ts"], row["ticker"]))
    return result


def trades_for(rows, config):
    threshold = float(config["threshold"])
    regime = config["regime"]
    min_abs_z = float(config.get("min_abs_z", 0.0))
    min_paid = float(config.get("min_paid", 0.0))
    max_paid = float(config.get("max_paid", 1.0))
    max_spread = float(config.get("max_spread", 1.0))
    trades = []
    seen_markets = set()
    for obs in rows:
        if obs["abs_z"] < min_abs_z or obs["spread"] > max_spread + 1e-12:
            continue
        if regime == "iv_high":
            if obs["ratio"] < threshold:
                continue
            buy_yes = obs["spot_above_strike"]
        elif regime == "iv_low":
            if obs["ratio"] > 1.0 / threshold:
                continue
            buy_yes = not obs["spot_above_strike"]
        else:
            raise ValueError("unknown regime %s" % regime)
        paid = obs["ask"] if buy_yes else 1.0 - obs["bid"]
        if not (0.0 < paid < 1.0 and min_paid <= paid <= max_paid):
            continue
        success = obs["outcome"] if buy_yes else 1 - obs["outcome"]
        item = dict(obs)
        item.update({
            "side": "YES" if buy_yes else "NO", "p_paid": paid,
            "success": int(success), "fee": fee(1, paid),
            "pnl": success - paid - fee(1, paid),
        })
        market_key = (item["series"], item["ticker"])
        if market_key in seen_markets:
            raise RuntimeError("candidate produced more than one observation for market %s" %
                               (market_key,))
        seen_markets.add(market_key)
        trades.append(item)
    return trades


def _group_pnl(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return grouped


def training_score(rows, calendar_days=None):
    """Conservative daily-P&L lower bound, used only by improved families."""
    if not rows:
        return float("-inf")
    grouped = _group_pnl(rows, lambda row: row["day"])
    if calendar_days is None:
        daily = [sum(row["pnl"] for row in group) for group in grouped.values()]
    else:
        # A filter that elects not to trade on a day earns exactly zero, rather
        # than removing that day from its risk-adjusted selection statistic.
        daily = [sum(row["pnl"] for row in grouped.get(day, ())) for day in calendar_days]
    if len(daily) < 2:
        return float("-inf")
    mean = statistics.mean(daily)
    se = statistics.stdev(daily) / math.sqrt(len(daily))
    return mean - se


def metrics(rows, calendar_days=None):
    if not rows:
        return {
            "n": 0, "pnl": 0.0, "pnl_per_trade": None,
            "first_close_ts": None, "last_close_ts": None,
            "fees": 0.0, "win_rate": None, "mean_paid": None,
            "median_ratio": None, "daily_lcb": None,
            "positive_series": 0, "series_count": 0,
            "positive_weeks": 0, "week_count": 0,
        }
    pnl = sum(row["pnl"] for row in rows)
    by_series = _group_pnl(rows, lambda row: row["coin"])
    by_week = _group_pnl(rows, lambda row: row["week"])
    return {
        "n": len(rows), "pnl": pnl, "pnl_per_trade": pnl / len(rows),
        "first_close_ts": min(row["close_ts"] for row in rows),
        "last_close_ts": max(row["close_ts"] for row in rows),
        "fees": sum(row["fee"] for row in rows),
        "win_rate": sum(row["success"] for row in rows) / len(rows),
        "mean_paid": sum(row["p_paid"] for row in rows) / len(rows),
        "median_ratio": median([row["ratio"] for row in rows]),
        "daily_lcb": training_score(rows, calendar_days=calendar_days),
        "positive_series": sum(sum(item["pnl"] for item in group) > 0.0 for group in by_series.values()),
        "series_count": len(by_series),
        "positive_weeks": sum(sum(item["pnl"] for item in group) > 0.0 for group in by_week.values()),
        "week_count": len(by_week),
    }


def market_null(rows, simulations=N_NULL, seed=SEED):
    """Required market null: redraw each outcome from the actual paid price."""
    if not rows:
        return float("nan")
    observed = sum(row["pnl"] for row in rows)
    constant_cost = sum(row["p_paid"] + row["fee"] for row in rows)
    probabilities = [row["p_paid"] for row in rows]
    rng = random.Random(seed)
    at_least = 0
    for _ in range(simulations):
        wins = 0
        for probability in probabilities:
            wins += rng.random() < probability
        simulated = wins - constant_cost
        if simulated >= observed - 1e-12:
            at_least += 1
    return (at_least + 1) / (simulations + 1)


def breakdown(rows, key):
    groups = _group_pnl(rows, key)
    answer = []
    for label in sorted(groups, key=str):
        group = groups[label]
        pnl = sum(row["pnl"] for row in group)
        answer.append({"label": str(label), "n": len(group), "pnl": pnl,
                       "pnl_per_trade": pnl / len(group)})
    return answer


def diagnostics(rows):
    total = sum(row["pnl"] for row in rows)
    best_five = sorted((row["pnl"] for row in rows), reverse=True)[:5]
    with_return = [row for row in rows if row["underlying_return"] is not None]
    return {
        "series": breakdown(rows, lambda row: row["coin"]),
        "weeks": breakdown(rows, lambda row: row["week"]),
        "sides": breakdown(rows, lambda row: row["side"]),
        "drift": breakdown(with_return, lambda row: "up" if row["underlying_return"] > 0.0 else "down_or_flat"),
        "best_five_pnl": sum(best_five),
        "best_five_share": (sum(best_five) / total if total > 0.0 else None),
    }


def relation(rows):
    if not rows:
        return {}
    ratios = [row["ratio"] for row in rows]
    return {"n": len(rows), "median_iv_rv": median(ratios),
            "mean_iv_rv": sum(ratios) / len(ratios),
            "iv_above_rv_pct": 100.0 * sum(value > 1.0 for value in ratios) / len(ratios)}


def config_key(config):
    fields = ("entry", "estimator", "threshold", "regime", "min_abs_z",
              "min_paid", "max_paid", "max_spread")
    return "|".join("%s=%s" % (field, config.get(field)) for field in fields)


def save_json(path, value):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)


def load_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
