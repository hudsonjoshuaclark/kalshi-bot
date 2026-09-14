"""Train-only search for sequential cross-strike ladder lock-ins.

For threshold contracts K1 < K2, YES(K1) + NO(K2) pays at least $1 in every
settlement state.  Unlike the already-dead static arbitrage scan, this strategy
allows the two displayed asks to be crossed at different observed timestamps.
Every abandoned first leg remains in the backtest.

Grid: 2 activation fractions x 2 last-first fractions x 3 strike-rank gaps x
3 touch prices = 36 combinations.
"""

import argparse
import glob
import json
import math
import os
import random
import statistics
from collections import defaultdict

from volatility_common import N_NULL, SEED, fee, iso_day, iso_time, iso_week, save_json


ACTIVATIONS = (0.10, 0.35)
LAST_FIRSTS = (0.60, 0.80)
RANK_GAPS = (1, 3, 5)
TRIGGERS = (0.25, 0.35, 0.45)
MIN_PAID = 0.05
SEARCH_COUNT = len(ACTIVATIONS) * len(LAST_FIRSTS) * len(RANK_GAPS) * len(TRIGGERS)
OUTPUT = "s16_dynamic_ladder_train.json"


def valid_quote(bar):
    bid, ask = bar.get("bid"), bar.get("ask")
    return (isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and
            math.isfinite(bid) and math.isfinite(ask) and 0.0 <= bid < ask <= 1.0)


def load_events(data_dir):
    events = defaultdict(list)
    seen = set()
    for path in sorted(glob.glob(os.path.join(data_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        for market in payload:
            ticker = str(market.get("ticker", ""))
            if not ticker or ticker in seen or market.get("strike_type") != "greater":
                continue
            if not isinstance(market.get("strike"), (int, float)):
                continue
            event = str(market.get("event") or ticker.rsplit("-", 1)[0])
            seen.add(ticker)
            events[event].append(market)
    clean = {}
    for event, markets in events.items():
        markets.sort(key=lambda row: (float(row["strike"]), str(row["ticker"])))
        closes = [int(row["close_ts"]) for row in markets if isinstance(row.get("close_ts"), (int, float))]
        opens = [int(row["open_ts"]) for row in markets if isinstance(row.get("open_ts"), (int, float))]
        if len(markets) >= 6 and closes and opens:
            clean[event] = {"event": event, "markets": markets, "open_ts": min(opens),
                            "close_ts": min(closes), "series": str(markets[0].get("series"))}
    return clean


def chronological_cut(events, fraction=0.70):
    refs = sorted((item["close_ts"], event) for event, item in events.items())
    cut = refs[int(fraction * len(refs))][0]
    train = sum(ts < cut for ts, _ in refs)
    return cut, train, len(refs) - train, len(refs)


def _partition(events, cut_ts, partition):
    for event in sorted(events, key=lambda key: (events[key]["close_ts"], key)):
        item = events[event]
        if partition == "train" and item["close_ts"] >= cut_ts:
            continue
        if partition == "test" and item["close_ts"] < cut_ts:
            continue
        yield item


def event_trade(item, config):
    """Return at most one sequential position path for an event."""
    markets = item["markets"]
    open_ts, close_ts = item["open_ts"], item["close_ts"]
    duration = close_ts - open_ts
    if duration <= 0:
        return None
    activation_ts = open_ts + duration * float(config["activation"])
    last_first_ts = open_ts + duration * float(config["last_first"])
    gap, trigger = int(config["rank_gap"]), float(config["trigger"])
    at_time = defaultdict(list)
    for index, market in enumerate(markets):
        for bar in market.get("bars", []):
            ts = bar.get("ts")
            if isinstance(ts, (int, float)) and int(ts) < close_ts and valid_quote(bar):
                at_time[int(ts)].append((index, bar))
    last = {}
    first = None
    first_ts = None
    for ts in sorted(at_time):
        candidates = []
        if activation_ts <= ts <= last_first_ts:
            for index, bar in at_time[ts]:
                previous = last.get(index)
                if previous is None:
                    continue
                bid, ask = float(bar["bid"]), float(bar["ask"])
                prev_bid, prev_ask = previous
                no_ask, prev_no_ask = 1.0 - bid, 1.0 - prev_bid
                if index + gap < len(markets) and prev_ask > trigger and MIN_PAID <= ask <= trigger:
                    candidates.append((ask, "YES", index))
                if index - gap >= 0 and prev_no_ask > trigger and MIN_PAID <= no_ask <= trigger:
                    candidates.append((no_ask, "NO", index))
        # Update only after detecting a crossing relative to the preceding actual bar.
        for index, bar in at_time[ts]:
            last[index] = (float(bar["bid"]), float(bar["ask"]))
        if candidates:
            # Closest-to-threshold touch is the least tail-like and deterministic.
            paid, side, index = max(candidates, key=lambda value: (value[0], value[1], -value[2]))
            first = (side, index, paid)
            first_ts = ts
            break
    if first is None:
        return None

    first_side, first_index, first_paid = first
    target_index = first_index + gap if first_side == "YES" else first_index - gap
    target_side = "NO" if first_side == "YES" else "YES"
    second = None
    for ts in sorted(value for value in at_time if value > first_ts):
        for index, bar in at_time[ts]:
            if index != target_index:
                continue
            paid = float(bar["ask"]) if target_side == "YES" else 1.0 - float(bar["bid"])
            if MIN_PAID <= paid <= trigger:
                second = (target_side, target_index, paid, ts)
                break
        if second is not None:
            break

    # Outcome access happens after the event has already passed the time split.
    first_result = markets[first_index].get("result")
    if first_result not in ("yes", "no"):
        return None
    first_yes = 1 if first_result == "yes" else 0
    first_success = first_yes if first_side == "YES" else 1 - first_yes
    first_fee = fee(1, first_paid)
    legs = [{"side": first_side, "ticker": markets[first_index]["ticker"],
             "strike": float(markets[first_index]["strike"]), "ts": first_ts,
             "p_paid": first_paid, "fee": first_fee, "success": first_success}]
    if second is not None:
        second_side, second_index, second_paid, second_ts = second
        second_result = markets[second_index].get("result")
        if second_result not in ("yes", "no"):
            return None
        second_yes = 1 if second_result == "yes" else 0
        second_success = second_yes if second_side == "YES" else 1 - second_yes
        second_fee = fee(1, second_paid)
        legs.append({"side": second_side, "ticker": markets[second_index]["ticker"],
                     "strike": float(markets[second_index]["strike"]), "ts": second_ts,
                     "p_paid": second_paid, "fee": second_fee, "success": second_success})
        lower = legs[0] if legs[0]["side"] == "YES" else legs[1]
        higher = legs[0] if legs[0]["side"] == "NO" else legs[1]
        if not lower["strike"] < higher["strike"] or sum(leg["success"] for leg in legs) < 1:
            raise RuntimeError("invalid guaranteed ladder pair in %s" % item["event"])
    payout = sum(leg["success"] for leg in legs)
    cost = sum(leg["p_paid"] + leg["fee"] for leg in legs)
    return {
        "event": item["event"], "ticker": item["event"], "series": item["series"],
        "close_ts": close_ts, "day": iso_day(close_ts), "week": iso_week(close_ts),
        "side": first_side + "_FIRST", "paired": len(legs) == 2, "legs": legs,
        "fees": sum(leg["fee"] for leg in legs), "cost": cost,
        "payout": payout, "pnl": payout - cost,
        "guaranteed_min_pnl": 1.0 - cost if len(legs) == 2 else None,
    }


def trades_for(events, cut_ts, partition, config):
    rows = []
    for item in _partition(events, cut_ts, partition):
        row = event_trade(item, config)
        if row is not None:
            rows.append(row)
    return rows


def _group(rows, key):
    grouped = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return grouped


def breakdown(rows, key):
    answer = []
    for label, group in sorted(_group(rows, key).items(), key=lambda item: str(item[0])):
        pnl = sum(row["pnl"] for row in group)
        answer.append({"label": str(label), "n": len(group), "pnl": pnl,
                       "pnl_per_event": pnl / len(group)})
    return answer


def diagnostics(rows):
    total = sum(row["pnl"] for row in rows)
    best = sum(sorted((row["pnl"] for row in rows), reverse=True)[:5])
    return {"series": breakdown(rows, lambda row: row["series"]),
            "weeks": breakdown(rows, lambda row: row["week"]),
            "first_side": breakdown(rows, lambda row: row["side"]),
            "completion": breakdown(rows, lambda row: "paired" if row["paired"] else "single"),
            "best_five_pnl": best, "best_five_share": best / total if total > 0.0 else None}


def metrics(rows, calendar_weeks=None):
    if not rows:
        return {"n": 0, "pnl": 0.0, "pnl_per_event": None, "paired_count": 0,
                "paired_pct": None, "fees": 0.0, "weekly_lcb": None,
                "positive_series": 0, "series_count": 0, "positive_weeks": 0,
                "week_count": 0, "first_close_ts": None, "last_close_ts": None}
    pnl = sum(row["pnl"] for row in rows)
    paired = sum(row["paired"] for row in rows)
    by_series = _group(rows, lambda row: row["series"])
    by_week = _group(rows, lambda row: row["week"])
    weekly_lcb = None
    if calendar_weeks and len(calendar_weeks) > 1:
        values = [sum(row["pnl"] for row in by_week.get(week, ())) for week in calendar_weeks]
        weekly_lcb = statistics.mean(values) - statistics.stdev(values) / math.sqrt(len(values))
    return {"n": len(rows), "pnl": pnl, "pnl_per_event": pnl / len(rows),
            "paired_count": paired, "paired_pct": paired / len(rows),
            "fees": sum(row["fees"] for row in rows), "weekly_lcb": weekly_lcb,
            "positive_series": sum(sum(row["pnl"] for row in group) > 0.0 for group in by_series.values()),
            "series_count": len(by_series),
            "positive_weeks": sum(sum(row["pnl"] for row in group) > 0.0 for group in by_week.values()),
            "week_count": len(by_week), "first_close_ts": min(row["close_ts"] for row in rows),
            "last_close_ts": max(row["close_ts"] for row in rows)}


def market_null(rows, simulations=N_NULL, seed=SEED):
    if not rows:
        return float("nan")
    observed = sum(row["pnl"] for row in rows)
    legs = [leg for row in rows for leg in row["legs"]]
    constant_cost = sum(leg["p_paid"] + leg["fee"] for leg in legs)
    rng = random.Random(seed)
    at_least = 0
    for _ in range(simulations):
        simulated = sum(rng.random() < leg["p_paid"] for leg in legs) - constant_cost
        if simulated >= observed - 1e-12:
            at_least += 1
    return (at_least + 1) / (simulations + 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="ladders")
    args = parser.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    events = load_events(args.data_dir)
    cut_ts, n_train, n_test, n_all = chronological_cut(events)
    calendar_weeks = sorted({iso_week(item["close_ts"]) for item in events.values()
                             if item["close_ts"] < cut_ts})
    candidates = []
    for activation in ACTIVATIONS:
        for last_first in LAST_FIRSTS:
            for rank_gap in RANK_GAPS:
                for trigger in TRIGGERS:
                    config = {"activation": activation, "last_first": last_first,
                              "rank_gap": rank_gap, "trigger": trigger, "min_paid": MIN_PAID}
                    rows = trades_for(events, cut_ts, "train", config)
                    candidates.append({"config": config, "metrics": metrics(rows, calendar_weeks),
                                       "diagnostics": diagnostics(rows)})
    eligible = [item for item in candidates if item["metrics"]["n"] >= 20]
    if not eligible:
        raise RuntimeError("no dynamic-ladder candidate reached 20 train event paths")
    robust = []
    for item in eligible:
        m, d = item["metrics"], item["diagnostics"]
        concentration = d["best_five_share"] is not None and d["best_five_share"] < 0.50
        series = m["positive_series"] >= min(3, m["series_count"])
        weeks = m["positive_weeks"] >= max(1, math.ceil(m["week_count"] / 2.0))
        completion = m["paired_pct"] >= 0.50
        item["train_robustness"] = {"concentration": concentration, "series": series,
                                     "weeks": weeks, "completion": completion,
                                     "all": concentration and series and weeks and completion}
        if item["train_robustness"]["all"]:
            robust.append(item)
    pool = robust if robust else eligible
    winner = max(pool, key=lambda item: item["metrics"]["weekly_lcb"])
    winner_rows = trades_for(events, cut_ts, "train", winner["config"])
    winner["train_market_null_p"] = market_null(winner_rows)
    payload = {"idea": "sequential cross-strike guaranteed-minimum ladder pair",
               "data_dir": args.data_dir, "cut_ts": cut_ts, "cut_iso": iso_time(cut_ts),
               "all_events": n_all, "train_events": n_train, "sealed_test_events": n_test,
               "search_count": SEARCH_COUNT, "prior_global_search_count": 262,
               "global_search_count_after_this_stage": 262 + SEARCH_COUNT,
               "selection_objective": "train robustness gates, then max weekly P&L lower bound",
               "robust_candidates": len(robust), "winner": winner, "candidates": candidates}
    save_json(OUTPUT, payload)
    m = winner["metrics"]
    print("S16 DYNAMIC LADDER -- TRAIN ONLY")
    print("cut=%s events train=%d sealed_test=%d searched=%d global=%d" %
          (payload["cut_iso"], n_train, n_test, SEARCH_COUNT, payload["global_search_count_after_this_stage"]))
    print("robust candidates=%d winner=%r" % (len(robust), winner["config"]))
    print("n=%d paired=%d (%.1f%%) P&L=%+.2f per-event=%+.4f weekly-LCB=%+.4f null-p=%.5f top5=%s" %
          (m["n"], m["paired_count"], 100 * m["paired_pct"], m["pnl"], m["pnl_per_event"],
           m["weekly_lcb"], winner["train_market_null_p"], winner["diagnostics"]["best_five_share"]))
    print("wrote %s; held-out event outcomes remain unscored" % OUTPUT)


if __name__ == "__main__":
    main()
