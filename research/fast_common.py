"""Shared machinery for high-turnover and high-attention Kalshi studies."""

import glob
import json
import math
import os
import statistics
from collections import defaultdict

from microstructure_common import diagnostics, market_null, metrics
from volatility_common import fee, iso_day, iso_week


PRIMARY = ("BTC", "ETH", "SOL", "XRP", "DOGE")
TRENDY = ("BNB", "HYPE", "NEAR", "ZEC")
ALL_FAST = PRIMARY + TRENDY
SERIES = {name: "KX%s15M" % name for name in ALL_FAST}
PRIOR_GLOBAL_SEARCHES = 298
NEW_FAST_SEARCHES = 144
GLOBAL_SEARCHES = PRIOR_GLOBAL_SEARCHES + NEW_FAST_SEARCHES


def valid_quote(bar):
    if not isinstance(bar, dict):
        return None
    bid, ask = bar.get("bid"), bar.get("ask")
    if not (isinstance(bid, (int, float)) and isinstance(ask, (int, float))):
        return None
    bid, ask = float(bid), float(ask)
    if not (0.0 <= bid < ask <= 1.0):
        return None
    return bid, ask, (bid + ask) / 2.0


def load_fast_data(deep_dir="deep", broad_dir="broad"):
    markets, spots = {}, {}
    for name in ALL_FAST:
        folder = deep_dir if name in PRIMARY else broad_dir
        with open(os.path.join(folder, SERIES[name] + ".json"), "r", encoding="utf-8") as handle:
            markets[name] = json.load(handle)
        spots[name] = {}
        if name in PRIMARY:
            with open(os.path.join(deep_dir, "spot_%s-USD.json" % name), "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            spots[name] = {int(key): value for key, value in raw.items()}
    return markets, spots


def series_cuts(markets, fraction=0.70):
    cuts = {}
    counts = {}
    for name, rows in markets.items():
        refs = sorted(int(row["close_ts"]) for row in rows
                      if isinstance(row.get("close_ts"), (int, float)))
        cut = refs[int(fraction * len(refs))]
        cuts[name] = cut
        counts[name] = {"all": len(refs), "train": sum(ts < cut for ts in refs),
                        "test": sum(ts >= cut for ts in refs)}
    return cuts, counts


def calendar(markets, cuts, partition):
    timestamps = []
    for name, rows in markets.items():
        for row in rows:
            ts = row.get("close_ts")
            if not isinstance(ts, (int, float)):
                continue
            ts = int(ts)
            if partition == "train" and ts < cuts[name]:
                timestamps.append(ts)
            if partition == "test" and ts >= cuts[name]:
                timestamps.append(ts)
    return sorted({iso_day(ts) for ts in timestamps})


def _in_partition(name, close_ts, cuts, partition):
    return close_ts < cuts[name] if partition == "train" else close_ts >= cuts[name]


def _bars(market):
    return {int(bar["ts"]): bar for bar in market.get("bars", [])
            if isinstance(bar.get("ts"), (int, float))}


def _underlying_return(spot, entry_ts, close_ts):
    a, b = spot.get(entry_ts - 60), spot.get(close_ts - 60)
    if a is None or b is None:
        return None
    x, y = a.get("close"), b.get("close")
    if not (isinstance(x, (int, float)) and isinstance(y, (int, float)) and x > 0.0 and y > 0.0):
        return None
    return math.log(float(y) / float(x))


def _trade(name, market, spot, entry_ts, close_ts, outcome, buy_yes, paid, features):
    if not 0.0 < paid < 1.0:
        return None
    success = outcome if buy_yes else 1 - outcome
    entry_fee = fee(1, paid)
    row = {
        "ticker": market.get("ticker"), "series": market.get("series", SERIES[name]),
        "coin": name, "entry_ts": entry_ts, "close_ts": close_ts,
        "day": iso_day(close_ts), "week": iso_week(close_ts), "outcome": outcome,
        "underlying_return": _underlying_return(spot, entry_ts, close_ts),
        "side": "YES" if buy_yes else "NO", "legs": 1, "paired": False,
        "prices": [paid], "fees_by_leg": [entry_fee], "fee": entry_fee,
        "cost": paid + entry_fee, "p_paid": paid, "success": int(success),
        "pnl": success - paid - entry_fee, "null_fixed_payout": None,
    }
    row.update(features)
    return row


def streak_trades(markets, spots, cuts, partition, config):
    entry = int(config["entry"])
    length = int(config["streak"])
    mode = config["mode"]
    max_paid = float(config["max_paid"])
    answer = []
    for name in ALL_FAST:
        ordered = sorted(markets[name], key=lambda row: (int(row.get("close_ts", 0)), str(row.get("ticker", ""))))
        for index in range(length, len(ordered)):
            market = ordered[index]
            open_ts, close_ts = market.get("open_ts"), market.get("close_ts")
            if not isinstance(open_ts, (int, float)) or not isinstance(close_ts, (int, float)):
                continue
            open_ts, close_ts = int(open_ts), int(close_ts)
            if not _in_partition(name, close_ts, cuts, partition):
                continue
            prior = ordered[index - length:index]
            prior_results = [row.get("result") for row in prior]
            if any(value not in ("yes", "no") for value in prior_results):
                continue
            if any(int(row.get("close_ts", 0)) > open_ts for row in prior):
                continue
            if len(set(prior_results)) != 1:
                continue
            outcome_text = market.get("result")
            if outcome_text not in ("yes", "no"):
                continue
            outcome = 1 if outcome_text == "yes" else 0
            entry_ts = open_ts + 60 * entry
            q = valid_quote(_bars(market).get(entry_ts))
            if q is None:
                continue
            buy_yes = prior_results[-1] == "yes"
            if mode == "reversal":
                buy_yes = not buy_yes
            elif mode != "continuation":
                raise ValueError("unknown streak mode")
            paid = q[1] if buy_yes else 1.0 - q[0]
            if paid > max_paid + 1e-12:
                continue
            row = _trade(name, market, spots[name], entry_ts, close_ts, outcome, buy_yes, paid,
                         {"prior_streak_side": prior_results[-1], "streak_length": length})
            if row is not None:
                answer.append(row)
    return sorted(answer, key=lambda row: (row["close_ts"], row["ticker"]))


def volume_impulse_trades(markets, spots, cuts, partition, config):
    entry = int(config["entry"])
    lookback = int(config["lookback"])
    threshold = float(config["threshold"])
    mode = config["mode"]
    volume_ratio = float(config["volume_ratio"])
    answer = []
    for name in ALL_FAST:
        for market in markets[name]:
            open_ts, close_ts = market.get("open_ts"), market.get("close_ts")
            if not isinstance(open_ts, (int, float)) or not isinstance(close_ts, (int, float)):
                continue
            open_ts, close_ts = int(open_ts), int(close_ts)
            if not _in_partition(name, close_ts, cuts, partition):
                continue
            outcome_text = market.get("result")
            if outcome_text not in ("yes", "no"):
                continue
            outcome = 1 if outcome_text == "yes" else 0
            entry_ts = open_ts + 60 * entry
            bars = _bars(market)
            current = bars.get(entry_ts)
            old = bars.get(entry_ts - 60 * lookback)
            q1, q0 = valid_quote(current), valid_quote(old)
            if q1 is None or q0 is None:
                continue
            move = q1[2] - q0[2]
            if abs(move) + 1e-12 < threshold:
                continue
            current_vol = current.get("vol")
            prior_vols = []
            for offset in range(1, 4):
                row = bars.get(entry_ts - 60 * offset)
                value = row.get("vol") if row else None
                if isinstance(value, (int, float)) and value >= 0.0:
                    prior_vols.append(float(value))
            if not isinstance(current_vol, (int, float)) or len(prior_vols) != 3:
                continue
            baseline = statistics.median(prior_vols)
            if baseline <= 0.0 or float(current_vol) < volume_ratio * baseline:
                continue
            buy_yes = move > 0.0
            if mode == "reversal":
                buy_yes = not buy_yes
            elif mode != "momentum":
                raise ValueError("unknown volume impulse mode")
            paid = q1[1] if buy_yes else 1.0 - q1[0]
            if paid > 0.97:
                continue
            row = _trade(name, market, spots[name], entry_ts, close_ts, outcome, buy_yes, paid,
                         {"quote_move": move, "volume_ratio_observed": float(current_vol) / baseline})
            if row is not None:
                answer.append(row)
    return sorted(answer, key=lambda row: (row["close_ts"], row["ticker"]))


def profit_rate(rows, evaluation_days):
    base = metrics(rows, calendar_days=evaluation_days)
    day_count = len(evaluation_days)
    base["calendar_days"] = day_count
    base["pnl_per_calendar_day"] = base["pnl"] / day_count if day_count else None
    base["trades_per_calendar_day"] = base["n"] / day_count if day_count else None
    cumulative = peak = drawdown = 0.0
    for row in sorted(rows, key=lambda item: (item["close_ts"], item["ticker"])):
        cumulative += row["pnl"]
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    base["max_realized_drawdown"] = drawdown
    close_counts = defaultdict(int)
    for row in rows:
        close_counts[row["close_ts"]] += 1
    base["max_simultaneous_block"] = max(close_counts.values()) if close_counts else 0
    return base


def train_robustness(metrics_value, diag):
    primary_drift = [item for item in diag["drift"]]
    drift_ok = len(primary_drift) == 2 and all(item["pnl"] > 0.0 for item in primary_drift)
    return {
        "series": metrics_value["positive_series"] >= min(5, metrics_value["series_count"]),
        "weeks": metrics_value["positive_weeks"] >= max(1, math.ceil(metrics_value["week_count"] / 2.0)),
        "concentration": diag["best_five_share"] is not None and diag["best_five_share"] < 0.50,
        "primary_drift": drift_ok,
    }


def fast_diagnostics(rows):
    return diagnostics(rows)


def snapshot(paths):
    return [[path.replace("\\", "/"), os.path.getsize(path), os.stat(path).st_mtime_ns]
            for path in sorted(paths)]

