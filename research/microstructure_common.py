"""Shared timestamp-safe machinery for post-volatility Kalshi studies.

The strategies in s11--s13 are intentionally based on executable displayed
asks.  A Kalshi candle stamped ``t`` is treated as known only at ``t``.  A
Coinbase candle keyed ``t - 60`` closes at that same instant, so comparisons
between the two venues never mix timestamps.
"""

import math
import random
import statistics
from collections import defaultdict

from volatility_common import (
    COINS, N_NULL, SEED, fee, iso_day, iso_week,
)


PRIOR_SEARCH_COUNT = 114
NEW_SEARCH_COUNT = 148
GLOBAL_SEARCH_COUNT = PRIOR_SEARCH_COUNT + NEW_SEARCH_COUNT


def _quote(bar):
    if not isinstance(bar, dict):
        return None
    bid, ask = bar.get("bid"), bar.get("ask")
    if not (isinstance(bid, (int, float)) and isinstance(ask, (int, float))):
        return None
    bid, ask = float(bid), float(ask)
    if not (0.0 <= bid < ask <= 1.0):
        return None
    return bid, ask, (bid + ask) / 2.0


def _market_rows(markets, cut_ts, partition):
    """Yield markets after partitioning, before their outcome is inspected."""
    if partition not in ("train", "test"):
        raise ValueError("partition must be train or test")
    for coin in COINS:
        for market in markets[coin]:
            close_ts = market.get("close_ts")
            open_ts = market.get("open_ts")
            if not isinstance(close_ts, (int, float)) or not isinstance(open_ts, (int, float)):
                continue
            close_ts, open_ts = int(close_ts), int(open_ts)
            if partition == "train" and close_ts >= cut_ts:
                continue
            if partition == "test" and close_ts < cut_ts:
                continue
            # This access deliberately occurs only after the time split.
            result = market.get("result")
            if result not in ("yes", "no"):
                continue
            yield coin, market, open_ts, close_ts, 1 if result == "yes" else 0


def _bars(market):
    answer = {}
    for bar in market.get("bars", []):
        ts = bar.get("ts")
        if isinstance(ts, (int, float)):
            answer[int(ts)] = bar
    return answer


def _underlying_return(spot, entry_ts, close_ts):
    entry = spot.get(entry_ts - 60)
    close = spot.get(close_ts - 60)
    if entry is None or close is None:
        return None
    a, b = entry.get("close"), close.get("close")
    if not (isinstance(a, (int, float)) and isinstance(b, (int, float)) and a > 0.0 and b > 0.0):
        return None
    return math.log(float(b) / float(a))


def _base(coin, market, close_ts, entry_ts, outcome, spot):
    return {
        "ticker": market.get("ticker"),
        "series": market.get("series"),
        "coin": coin,
        "entry_ts": entry_ts,
        "close_ts": close_ts,
        "day": iso_day(close_ts),
        "week": iso_week(close_ts),
        "outcome": outcome,
        "underlying_return": _underlying_return(spot, entry_ts, close_ts),
    }


def two_touch_trades(markets, spots, cut_ts, partition, config):
    """Buy a cheap side, then buy the opposite side if it later becomes cheap.

    The first leg may trigger from ``activation`` through ``last_first``.  Once
    entered, the opposite displayed ask remains watched through minute 13.
    Minute 14 is excluded because it is the settlement timestamp.
    """
    activation = int(config["activation"])
    last_first = int(config["last_first"])
    trigger = float(config["trigger"])
    trades = []
    for coin, market, open_ts, close_ts, outcome in _market_rows(markets, cut_ts, partition):
        bars = _bars(market)
        first = None
        for minute in range(activation, last_first + 1):
            q = _quote(bars.get(open_ts + 60 * minute))
            if q is None:
                continue
            bid, ask, _ = q
            yes_ask, no_ask = ask, 1.0 - bid
            if yes_ask <= trigger + 1e-12:
                first = (minute, "YES", yes_ask)
                break
            if no_ask <= trigger + 1e-12:
                first = (minute, "NO", no_ask)
                break
        if first is None:
            continue
        first_minute, first_side, first_paid = first
        opposite = "NO" if first_side == "YES" else "YES"
        second = None
        for minute in range(first_minute + 1, 14):
            q = _quote(bars.get(open_ts + 60 * minute))
            if q is None:
                continue
            bid, ask, _ = q
            paid = ask if opposite == "YES" else 1.0 - bid
            if paid <= trigger + 1e-12:
                second = (minute, opposite, paid)
                break
        if not 0.0 < first_paid < 1.0:
            continue
        entry_ts = open_ts + 60 * first_minute
        row = _base(coin, market, close_ts, entry_ts, outcome, spots[coin])
        first_fee = fee(1, first_paid)
        if second is None:
            success = outcome if first_side == "YES" else 1 - outcome
            cost = first_paid + first_fee
            row.update({
                "side": first_side, "legs": 1, "paired": False,
                "first_minute": first_minute, "second_minute": None,
                "prices": [first_paid], "fees_by_leg": [first_fee],
                "fee": first_fee, "cost": cost, "p_paid": first_paid,
                "success": int(success), "pnl": success - cost,
                "null_fixed_payout": None,
            })
        else:
            second_minute, second_side, second_paid = second
            if not 0.0 < second_paid < 1.0:
                continue
            second_fee = fee(1, second_paid)
            cost = first_paid + first_fee + second_paid + second_fee
            row.update({
                "side": "BOTH", "legs": 2, "paired": True,
                "first_side": first_side, "first_minute": first_minute,
                "second_side": second_side, "second_minute": second_minute,
                "prices": [first_paid, second_paid],
                "fees_by_leg": [first_fee, second_fee],
                "fee": first_fee + second_fee, "cost": cost,
                "p_paid": None, "success": None, "pnl": 1.0 - cost,
                "null_fixed_payout": 1.0,
            })
        trades.append(row)
    return sorted(trades, key=lambda row: (row["close_ts"], row["ticker"]))


def quote_impulse_trades(markets, spots, cut_ts, partition, config):
    """Trade continuation or reversal after an early move in Kalshi's own mid."""
    entry = int(config["entry"])
    threshold = float(config["threshold"])
    mode = config["mode"]
    trades = []
    for coin, market, open_ts, close_ts, outcome in _market_rows(markets, cut_ts, partition):
        bars = _bars(market)
        q0 = _quote(bars.get(open_ts + 60))
        q1 = _quote(bars.get(open_ts + 60 * entry))
        if q0 is None or q1 is None:
            continue
        change = q1[2] - q0[2]
        if abs(change) + 1e-12 < threshold:
            continue
        buy_yes = change > 0.0
        if mode == "reversal":
            buy_yes = not buy_yes
        elif mode != "momentum":
            raise ValueError("unknown quote impulse mode %s" % mode)
        paid = q1[1] if buy_yes else 1.0 - q1[0]
        if not 0.0 < paid < 1.0:
            continue
        success = outcome if buy_yes else 1 - outcome
        entry_ts = open_ts + 60 * entry
        row = _base(coin, market, close_ts, entry_ts, outcome, spots[coin])
        entry_fee = fee(1, paid)
        row.update({
            "side": "YES" if buy_yes else "NO", "legs": 1,
            "paired": False, "quote_change": change, "prices": [paid],
            "fees_by_leg": [entry_fee], "fee": entry_fee,
            "cost": paid + entry_fee, "p_paid": paid,
            "success": int(success), "pnl": success - paid - entry_fee,
            "null_fixed_payout": None,
        })
        trades.append(row)
    return sorted(trades, key=lambda row: (row["close_ts"], row["ticker"]))


def _trailing_std(spot, previous_key, lookback=30):
    keys = [previous_key - 60 * offset for offset in range(lookback, -1, -1)]
    closes = []
    for key in keys:
        candle = spot.get(key)
        if candle is None or not isinstance(candle.get("close"), (int, float)) or candle["close"] <= 0.0:
            return None
        closes.append(float(candle["close"]))
    returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]
    if len(returns) < 2:
        return None
    value = statistics.stdev(returns)
    return value if value > 0.0 and math.isfinite(value) else None


def spot_shock_trades(markets, spots, cut_ts, partition, config):
    """Buy the spot-shock direction only when Kalshi has barely responded."""
    entry = int(config["entry"])
    min_z = float(config["min_z"])
    max_response = float(config["max_response"])
    trades = []
    for coin, market, open_ts, close_ts, outcome in _market_rows(markets, cut_ts, partition):
        entry_ts = open_ts + 60 * entry
        current_key, previous_key = entry_ts - 60, entry_ts - 120
        current = spots[coin].get(current_key)
        previous = spots[coin].get(previous_key)
        if current is None or previous is None:
            continue
        a, b = previous.get("close"), current.get("close")
        if not (isinstance(a, (int, float)) and isinstance(b, (int, float)) and a > 0.0 and b > 0.0):
            continue
        trailing = _trailing_std(spots[coin], previous_key, lookback=30)
        if trailing is None:
            continue
        shock = math.log(float(b) / float(a))
        z = shock / trailing
        if abs(z) + 1e-12 < min_z or shock == 0.0:
            continue
        bars = _bars(market)
        prior_quote = _quote(bars.get(entry_ts - 60))
        current_quote = _quote(bars.get(entry_ts))
        if prior_quote is None or current_quote is None:
            continue
        direction = 1.0 if shock > 0.0 else -1.0
        signed_response = direction * (current_quote[2] - prior_quote[2])
        if signed_response > max_response + 1e-12:
            continue
        buy_yes = shock > 0.0
        paid = current_quote[1] if buy_yes else 1.0 - current_quote[0]
        if not 0.0 < paid < 1.0:
            continue
        success = outcome if buy_yes else 1 - outcome
        row = _base(coin, market, close_ts, entry_ts, outcome, spots[coin])
        entry_fee = fee(1, paid)
        row.update({
            "side": "YES" if buy_yes else "NO", "legs": 1,
            "paired": False, "shock": shock, "shock_z": z,
            "signed_quote_response": signed_response, "prices": [paid],
            "fees_by_leg": [entry_fee], "fee": entry_fee,
            "cost": paid + entry_fee, "p_paid": paid,
            "success": int(success), "pnl": success - paid - entry_fee,
            "null_fixed_payout": None,
        })
        trades.append(row)
    return sorted(trades, key=lambda row: (row["close_ts"], row["ticker"]))


def _group(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return grouped


def training_score(rows, calendar_days):
    if not rows or len(calendar_days) < 2:
        return float("-inf")
    grouped = _group(rows, lambda row: row["day"])
    daily = [sum(row["pnl"] for row in grouped.get(day, ())) for day in calendar_days]
    return statistics.mean(daily) - statistics.stdev(daily) / math.sqrt(len(daily))


def metrics(rows, calendar_days=None):
    if not rows:
        return {
            "n": 0, "pnl": 0.0, "pnl_per_trade": None, "fees": 0.0,
            "legs": 0, "paired_count": 0, "paired_pct": None,
            "single_win_rate": None, "mean_cost": None, "daily_lcb": None,
            "first_close_ts": None, "last_close_ts": None,
            "positive_series": 0, "series_count": 0,
            "positive_weeks": 0, "week_count": 0,
        }
    pnl = sum(row["pnl"] for row in rows)
    singles = [row for row in rows if not row["paired"]]
    paired = sum(row["paired"] for row in rows)
    by_series = _group(rows, lambda row: row["coin"])
    by_week = _group(rows, lambda row: row["week"])
    return {
        "n": len(rows), "pnl": pnl, "pnl_per_trade": pnl / len(rows),
        "fees": sum(row["fee"] for row in rows),
        "legs": sum(row["legs"] for row in rows),
        "paired_count": paired, "paired_pct": paired / len(rows),
        "single_win_rate": (sum(row["success"] for row in singles) / len(singles) if singles else None),
        "mean_cost": sum(row["cost"] for row in rows) / len(rows),
        "daily_lcb": (training_score(rows, calendar_days) if calendar_days else None),
        "first_close_ts": min(row["close_ts"] for row in rows),
        "last_close_ts": max(row["close_ts"] for row in rows),
        "positive_series": sum(sum(row["pnl"] for row in group) > 0.0 for group in by_series.values()),
        "series_count": len(by_series),
        "positive_weeks": sum(sum(row["pnl"] for row in group) > 0.0 for group in by_week.values()),
        "week_count": len(by_week),
    }


def market_null(rows, simulations=N_NULL, seed=SEED):
    """Redraw each unpaired outcome from its actual paid price.

    A paired YES+NO path has a deterministic $1 payout under every state, so it
    remains fixed in each null draw.  This is the correct state-contingent null
    for a sequentially completed two-leg position.
    """
    if not rows:
        return float("nan")
    observed = sum(row["pnl"] for row in rows)
    fixed_pnl = sum(row["null_fixed_payout"] - row["cost"] for row in rows
                    if row["null_fixed_payout"] is not None)
    singles = [row for row in rows if row["null_fixed_payout"] is None]
    single_cost = sum(row["cost"] for row in singles)
    probabilities = [row["p_paid"] for row in singles]
    rng = random.Random(seed)
    at_least = 0
    for _ in range(simulations):
        wins = sum(rng.random() < probability for probability in probabilities)
        simulated = fixed_pnl + wins - single_cost
        if simulated >= observed - 1e-12:
            at_least += 1
    return (at_least + 1) / (simulations + 1)


def breakdown(rows, key):
    answer = []
    for label, group in sorted(_group(rows, key).items(), key=lambda item: str(item[0])):
        pnl = sum(row["pnl"] for row in group)
        answer.append({"label": str(label), "n": len(group), "pnl": pnl,
                       "pnl_per_trade": pnl / len(group)})
    return answer


def diagnostics(rows):
    total = sum(row["pnl"] for row in rows)
    best_five = sum(sorted((row["pnl"] for row in rows), reverse=True)[:5])
    with_return = [row for row in rows if row["underlying_return"] is not None]
    return {
        "series": breakdown(rows, lambda row: row["coin"]),
        "weeks": breakdown(rows, lambda row: row["week"]),
        "sides": breakdown(rows, lambda row: row["side"]),
        "drift": breakdown(with_return, lambda row: "up" if row["underlying_return"] > 0.0 else "down_or_flat"),
        "paired": breakdown(rows, lambda row: "paired" if row["paired"] else "single"),
        "best_five_pnl": best_five,
        "best_five_share": best_five / total if total > 0.0 else None,
    }


def cluster_statistics(rows):
    if not rows:
        return {}
    mean_pnl = sum(row["pnl"] for row in rows) / len(rows)
    clusters = _group(rows, lambda row: row["close_ts"])
    sums = [sum(row["pnl"] - mean_pnl for row in group) for group in clusters.values()]
    count = len(sums)
    if count <= 1:
        return {"close_time_clusters": count, "se": None, "t": None, "ci95": [None, None]}
    variance_mean = (count / (count - 1.0)) * sum(value * value for value in sums) / (len(rows) ** 2)
    se = math.sqrt(variance_mean)
    return {
        "close_time_clusters": count, "se": se,
        "t": mean_pnl / se if se > 0.0 else None,
        "ci95": [mean_pnl - 1.96 * se, mean_pnl + 1.96 * se],
    }


def unique_market_audit(rows):
    keys = [(row["series"], row["ticker"]) for row in rows]
    return len(keys) == len(set(keys))
