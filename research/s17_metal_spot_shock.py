"""Out-of-domain validation of the frozen S13 spot-shock rule on gold/silver.

No metal parameter is selected.  The exact crypto winner (entry=8, |z|>=3,
signed quote response<=0.15) is applied to Pyth XAU/XAG minute bars.  Markets
are still split oldest 70% / newest 30% to expose time stability.
"""

import json
import math
import os
import statistics

from microstructure_common import diagnostics, market_null, metrics
from volatility_common import fee, iso_day, iso_time, iso_week, load_json, save_json


ASSETS = {"GOLD": "KXGOLD15M", "SILVER": "KXSILVER15M"}
GLOBAL_SEARCH_COUNT = 298
OUTPUT = "metal_spot_shock_results.json"


def quote(bar):
    if not isinstance(bar, dict):
        return None
    bid, ask = bar.get("bid"), bar.get("ask")
    if not (isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and
            0.0 <= bid < ask <= 1.0):
        return None
    return float(bid), float(ask), (float(bid) + float(ask)) / 2.0


def trailing_std(spot, previous_key, lookback=30):
    values = []
    for offset in range(lookback, -1, -1):
        row = spot.get(previous_key - 60 * offset)
        if row is None or not isinstance(row.get("close"), (int, float)) or row["close"] <= 0.0:
            return None
        values.append(float(row["close"]))
    returns = [math.log(values[index] / values[index - 1]) for index in range(1, len(values))]
    value = statistics.stdev(returns)
    return value if value > 0.0 and math.isfinite(value) else None


def load_data(data_dir):
    markets, spots = {}, {}
    for asset, series in ASSETS.items():
        with open(os.path.join(data_dir, series + ".json"), "r", encoding="utf-8") as handle:
            markets[asset] = json.load(handle)
        with open(os.path.join(data_dir, "pyth_" + series + ".json"), "r", encoding="utf-8") as handle:
            raw = json.load(handle)["bars"]
        spots[asset] = {int(key): value for key, value in raw.items()}
    return markets, spots


def cut(markets):
    refs = sorted(int(row["close_ts"]) for rows in markets.values() for row in rows)
    value = refs[int(0.70 * len(refs))]
    return value, sum(ts < value for ts in refs), sum(ts >= value for ts in refs)


def trades(markets, spots, cut_ts, partition):
    rows = []
    for asset, series in ASSETS.items():
        spot = spots[asset]
        for market in markets[asset]:
            close_ts = int(market["close_ts"])
            if partition == "train" and close_ts >= cut_ts:
                continue
            if partition == "test" and close_ts < cut_ts:
                continue
            # Outcome access follows the time partition.
            if market.get("result") not in ("yes", "no"):
                continue
            outcome = 1 if market["result"] == "yes" else 0
            open_ts = int(market["open_ts"])
            entry_ts = open_ts + 8 * 60
            current_key, previous_key = entry_ts - 60, entry_ts - 120
            current, previous = spot.get(current_key), spot.get(previous_key)
            if current is None or previous is None:
                continue
            trailing = trailing_std(spot, previous_key)
            if trailing is None:
                continue
            shock = math.log(float(current["close"]) / float(previous["close"]))
            z = shock / trailing
            if abs(z) < 3.0 or shock == 0.0:
                continue
            bars = {int(bar["ts"]): bar for bar in market.get("bars", [])
                    if isinstance(bar.get("ts"), (int, float))}
            prior_q, current_q = quote(bars.get(entry_ts - 60)), quote(bars.get(entry_ts))
            if prior_q is None or current_q is None:
                continue
            direction = 1.0 if shock > 0.0 else -1.0
            signed_response = direction * (current_q[2] - prior_q[2])
            if signed_response > 0.15:
                continue
            buy_yes = shock > 0.0
            paid = current_q[1] if buy_yes else 1.0 - current_q[0]
            if not 0.0 < paid < 1.0:
                continue
            success = outcome if buy_yes else 1 - outcome
            entry_fee = fee(1, paid)
            close_spot = spot.get(close_ts - 60)
            underlying_return = None
            if close_spot is not None and float(close_spot["close"]) > 0.0:
                underlying_return = math.log(float(close_spot["close"]) / float(current["close"]))
            rows.append({
                "ticker": market["ticker"], "series": series, "coin": asset,
                "entry_ts": entry_ts, "close_ts": close_ts, "day": iso_day(close_ts),
                "week": iso_week(close_ts), "outcome": outcome,
                "underlying_return": underlying_return,
                "side": "YES" if buy_yes else "NO", "legs": 1, "paired": False,
                "shock": shock, "shock_z": z, "signed_quote_response": signed_response,
                "prices": [paid], "fees_by_leg": [entry_fee], "fee": entry_fee,
                "cost": paid + entry_fee, "p_paid": paid, "success": success,
                "pnl": success - paid - entry_fee, "null_fixed_payout": None,
            })
    return sorted(rows, key=lambda row: (row["close_ts"], row["ticker"]))


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    frozen = load_json("s13_spot_shock_train.json")["winner"]["config"]
    if frozen != {"entry": 8, "max_response": 0.15, "min_z": 3.0}:
        raise RuntimeError("S13 winner changed; metal validation no longer matches frozen rule")
    markets, spots = load_data("tape2")
    cut_ts, n_train, n_test = cut(markets)
    train_rows = trades(markets, spots, cut_ts, "train")
    test_rows = trades(markets, spots, cut_ts, "test")
    train_metrics, test_metrics = metrics(train_rows), metrics(test_rows)
    raw_p = market_null(test_rows, seed=SEED + 1700) if test_rows else None
    corrected = min(1.0, raw_p * GLOBAL_SEARCH_COUNT) if raw_p is not None else None
    payload = {"idea": "frozen crypto spot-shock transported to Pyth metals",
               "config": frozen, "cut_ts": cut_ts, "cut_iso": iso_time(cut_ts),
               "market_records_train": n_train, "market_records_test": n_test,
               "train": train_metrics, "train_diagnostics": diagnostics(train_rows),
               "test": test_metrics, "test_diagnostics": diagnostics(test_rows),
               "raw_market_null_p": raw_p, "bonferroni_combinations": GLOBAL_SEARCH_COUNT,
               "corrected_market_null_p": corrected,
               "verdict": ("PASS" if test_rows and test_metrics["pnl"] > 0.0 and corrected < 0.05
                           else "POSITIVE BUT UNPROVEN" if test_rows and test_metrics["pnl"] > 0.0
                           else "FAIL")}
    save_json(OUTPUT, payload)
    print("S17 FROZEN SPOT SHOCK ON METALS")
    print("cut=%s records train=%d test=%d" % (payload["cut_iso"], n_train, n_test))
    print("usable train n=%d P&L=%+.3f per=%s" %
          (train_metrics["n"], train_metrics["pnl"], train_metrics["pnl_per_trade"]))
    print("usable test n=%d P&L=%+.3f per=%s raw-p=%s corrected=%s verdict=%s" %
          (test_metrics["n"], test_metrics["pnl"], test_metrics["pnl_per_trade"],
           raw_p, corrected, payload["verdict"]))
    print("wrote %s" % OUTPUT)


if __name__ == "__main__":
    from volatility_common import SEED
    main()
