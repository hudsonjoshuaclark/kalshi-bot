"""Train-only attention-flow study for GPU spot-price prediction markets.

The unit of observation is a GPU-series weekly event, not every correlated
strike.  At a fixed fraction through the event, select the single contract with
the largest contemporaneous volume/OI change among those making a sufficiently
large price move; then buy momentum or reversal at the displayed ask.
"""

import json
import math
import os
import statistics
from collections import defaultdict

from microstructure_common import diagnostics, market_null, metrics
from volatility_common import fee, iso_day, iso_time, iso_week, save_json


SERIES = ("KXRTX5090WS", "KXH100WS", "KXH200WS", "KXA100WS", "KXB200WS")
FRACTIONS = (0.35, 0.60, 0.80)
LOOKBACKS = (1, 3)
THRESHOLDS = (0.03, 0.08)
MODES = ("momentum", "reversal")
SEARCH_COUNT = len(FRACTIONS) * len(LOOKBACKS) * len(THRESHOLDS) * len(MODES)
OUTPUT = "s21_gpu_attention_train.json"


def valid_quote(bar):
    if not isinstance(bar, dict):
        return None
    bid, ask = bar.get("bid"), bar.get("ask")
    if not (isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and
            0.0 <= bid < ask <= 1.0):
        return None
    return float(bid), float(ask), (float(bid) + float(ask)) / 2.0


def load_events():
    events = defaultdict(list)
    for series in SERIES:
        with open(os.path.join("broad", series + ".json"), "r", encoding="utf-8") as handle:
            rows = json.load(handle)
        for market in rows:
            ticker = str(market.get("ticker", ""))
            event = ticker.rsplit("-", 1)[0]
            events[(series, event)].append(market)
    clean = {}
    for key, rows in events.items():
        closes = [int(row["close_ts"]) for row in rows if isinstance(row.get("close_ts"), (int, float))]
        opens = [int(row["open_ts"]) for row in rows if isinstance(row.get("open_ts"), (int, float))]
        if closes and opens:
            clean[key] = {"series": key[0], "event": key[1], "markets": rows,
                          "open_ts": min(opens), "close_ts": min(closes)}
    return clean


def chronological_cut(events):
    refs = sorted((item["close_ts"], key) for key, item in events.items())
    cut = refs[int(0.70 * len(refs))][0]
    return cut, sum(ts < cut for ts, _ in refs), sum(ts >= cut for ts, _ in refs)


def event_trade(item, config):
    target = item["open_ts"] + (item["close_ts"] - item["open_ts"]) * config["fraction"]
    candidates = []
    for market in item["markets"]:
        bars = [bar for bar in market.get("bars", [])
                if isinstance(bar.get("ts"), (int, float)) and int(bar["ts"]) < item["close_ts"]
                and valid_quote(bar) is not None]
        bars.sort(key=lambda bar: int(bar["ts"]))
        past = [bar for bar in bars if int(bar["ts"]) <= target]
        lookback = int(config["lookback"])
        if len(past) <= lookback:
            continue
        current, prior = past[-1], past[-1 - lookback]
        q1, q0 = valid_quote(current), valid_quote(prior)
        move = q1[2] - q0[2]
        if abs(move) + 1e-12 < config["threshold"]:
            continue
        volume = float(current.get("vol") or 0.0)
        oi1, oi0 = current.get("oi"), prior.get("oi")
        oi_change = abs(float(oi1) - float(oi0)) if isinstance(oi1, (int, float)) and isinstance(oi0, (int, float)) else 0.0
        activity = max(volume, oi_change)
        if activity <= 0.0:
            continue
        candidates.append((activity, abs(move), str(market.get("ticker")), market, current, move))
    if not candidates:
        return None
    _, _, _, market, bar, move = max(candidates, key=lambda value: (value[0], value[1], value[2]))
    q = valid_quote(bar)
    buy_yes = move > 0.0
    if config["mode"] == "reversal":
        buy_yes = not buy_yes
    paid = q[1] if buy_yes else 1.0 - q[0]
    if not 0.0 < paid <= 0.97:
        return None
    result = market.get("result")
    if result not in ("yes", "no"):
        return None
    outcome = 1 if result == "yes" else 0
    success = outcome if buy_yes else 1 - outcome
    entry_fee = fee(1, paid)
    return {"ticker": market["ticker"], "series": item["series"], "coin": item["series"],
            "event": item["event"], "entry_ts": int(bar["ts"]), "close_ts": item["close_ts"],
            "day": iso_day(item["close_ts"]), "week": iso_week(item["close_ts"]),
            "outcome": outcome, "underlying_return": None,
            "side": "YES" if buy_yes else "NO", "legs": 1, "paired": False,
            "quote_move": move, "prices": [paid], "fees_by_leg": [entry_fee],
            "fee": entry_fee, "cost": paid + entry_fee, "p_paid": paid,
            "success": int(success), "pnl": success - paid - entry_fee,
            "null_fixed_payout": None}


def trades_for(events, cut_ts, partition, config):
    rows = []
    for item in events.values():
        if partition == "train" and item["close_ts"] >= cut_ts:
            continue
        if partition == "test" and item["close_ts"] < cut_ts:
            continue
        # Target outcome is accessed by event_trade only after partitioning.
        row = event_trade(item, config)
        if row is not None:
            rows.append(row)
    return sorted(rows, key=lambda row: (row["close_ts"], row["ticker"]))


def enrich(rows, settlement_days, elapsed_days):
    value = metrics(rows, calendar_days=settlement_days)
    value["elapsed_calendar_days"] = elapsed_days
    value["pnl_per_elapsed_day"] = value["pnl"] / elapsed_days if elapsed_days else None
    value["events_per_elapsed_day"] = value["n"] / elapsed_days if elapsed_days else None
    return value


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    events = load_events()
    cut_ts, n_train, n_test = chronological_cut(events)
    train_closes = [item["close_ts"] for item in events.values() if item["close_ts"] < cut_ts]
    settlement_days = sorted({iso_day(ts) for ts in train_closes})
    elapsed = max(1.0, (max(train_closes) - min(train_closes)) / 86400.0) if train_closes else 0.0
    candidates = []
    for fraction in FRACTIONS:
        for lookback in LOOKBACKS:
            for threshold in THRESHOLDS:
                for mode in MODES:
                    config = {"fraction": fraction, "lookback": lookback,
                              "threshold": threshold, "mode": mode}
                    rows = trades_for(events, cut_ts, "train", config)
                    candidates.append({"config": config,
                                       "metrics": enrich(rows, settlement_days, elapsed),
                                       "diagnostics": diagnostics(rows)})
    eligible = [item for item in candidates if item["metrics"]["n"] >= 10]
    if not eligible:
        raise RuntimeError("no GPU attention rule reached 10 train events")
    winner = max(eligible, key=lambda item: item["metrics"]["daily_lcb"])
    rows = trades_for(events, cut_ts, "train", winner["config"])
    winner["train_market_null_p"] = market_null(rows)
    payload = {"idea": "GPU attention-flow price continuation/reversal",
               "series": list(SERIES), "cut_ts": cut_ts, "cut_iso": iso_time(cut_ts),
               "events_train": n_train, "sealed_events_test": n_test,
               "search_count": SEARCH_COUNT,
               "unit_of_observation": "one selected contract per GPU-series weekly event",
               "winner": winner, "candidates": candidates}
    save_json(OUTPUT, payload)
    m = winner["metrics"]
    print("S21 GPU ATTENTION -- TRAIN ONLY")
    print("cut=%s events train=%d sealed-test=%d searched=%d" %
          (payload["cut_iso"], n_train, n_test, SEARCH_COUNT))
    print("winner=%r n=%d pnl=%+.3f per=%+.4f per-day=%+.4f null-p=%.5f" %
          (winner["config"], m["n"], m["pnl"], m["pnl_per_trade"],
           m["pnl_per_elapsed_day"], winner["train_market_null_p"]))
    print("wrote %s; newest GPU events remain unscored" % OUTPUT)


if __name__ == "__main__":
    main()
