"""Train-only second-stage tail-risk filters for the S13 spot-shock winner.

S13 selected entry minute 8.  With that timing frozen, this script searches
4 shock thresholds x 3 response caps x 4 maximum entry prices = 48 additional
combinations.  All 48 count toward the final multiplicity correction.
"""

import argparse
import os

from microstructure_common import diagnostics, market_null, metrics, spot_shock_trades
from volatility_common import (
    chronological_cut, iso_day, iso_time, load_data, load_json, save_json, snapshot,
)


ENTRY = 8
MIN_Z = (2.5, 3.0, 3.5, 4.0)
MAX_RESPONSE = (0.08, 0.12, 0.15)
MAX_PAID = (0.75, 0.85, 0.95, 1.00)
SEARCH_COUNT = len(MIN_Z) * len(MAX_RESPONSE) * len(MAX_PAID)
OUTPUT = "s14_spot_shock_filters_train.json"


def filtered_rows(markets, spots, cut_ts, partition, config):
    base = spot_shock_trades(markets, spots, cut_ts, partition, {
        "entry": ENTRY, "min_z": config["min_z"],
        "max_response": config["max_response"],
    })
    return [row for row in base if row["p_paid"] <= config["max_paid"] + 1e-12]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="deep")
    args = parser.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    stage_one = load_json("s13_spot_shock_train.json")
    if stage_one["winner"]["config"]["entry"] != ENTRY:
        raise RuntimeError("S13 entry winner changed; second-stage grid is no longer frozen")
    markets, spots = load_data(args.data_dir)
    current_snapshot = snapshot(args.data_dir)
    if current_snapshot != stage_one["snapshot"]:
        raise RuntimeError("data changed after S13 was frozen")
    cut_ts, n_train, n_test, n_all = chronological_cut(markets)
    if cut_ts != stage_one["cut_ts"]:
        raise RuntimeError("chronological split changed after S13")
    calendar_days = sorted({iso_day(int(row["close_ts"])) for coin in markets for row in markets[coin]
                            if isinstance(row.get("close_ts"), (int, float)) and row["close_ts"] < cut_ts})
    candidates = []
    for min_z in MIN_Z:
        for max_response in MAX_RESPONSE:
            for max_paid in MAX_PAID:
                config = {"entry": ENTRY, "min_z": min_z,
                          "max_response": max_response, "max_paid": max_paid}
                rows = filtered_rows(markets, spots, cut_ts, "train", config)
                candidates.append({"config": config, "metrics": metrics(rows, calendar_days),
                                   "diagnostics": diagnostics(rows)})
    eligible = [item for item in candidates if item["metrics"]["n"] >= 50]
    if not eligible:
        raise RuntimeError("no filtered spot-shock candidate reached 50 train trades")
    # Prefer candidates that meet every train robustness gate.  If none does,
    # retain the best conservative daily result but mark it as fragile.
    robust = []
    for item in eligible:
        m, d = item["metrics"], item["diagnostics"]
        drift_ok = len(d["drift"]) == 2 and all(block["pnl"] > 0.0 for block in d["drift"])
        concentration_ok = d["best_five_share"] is not None and d["best_five_share"] < 0.50
        weeks_ok = m["positive_weeks"] >= max(1, (m["week_count"] + 1) // 2)
        series_ok = m["positive_series"] >= min(3, m["series_count"])
        item["train_robustness_gate"] = {
            "drift": drift_ok, "concentration": concentration_ok,
            "weeks": weeks_ok, "series": series_ok,
            "all": drift_ok and concentration_ok and weeks_ok and series_ok,
        }
        if item["train_robustness_gate"]["all"]:
            robust.append(item)
    pool = robust if robust else eligible
    winner = max(pool, key=lambda item: item["metrics"]["daily_lcb"])
    rows = filtered_rows(markets, spots, cut_ts, "train", winner["config"])
    winner["train_market_null_p"] = market_null(rows)
    payload = {
        "idea": "tail-risk-filtered same-minute spot shock", "data_dir": args.data_dir,
        "snapshot": current_snapshot, "cut_ts": cut_ts, "cut_iso": iso_time(cut_ts),
        "all_markets": n_all, "train_markets": n_train, "sealed_test_markets": n_test,
        "search_count": SEARCH_COUNT, "parent_search_count": stage_one["search_count"],
        "prior_global_search_count": 114,
        "global_search_count_after_this_stage": 114 + 40 + 24 + 36 + SEARCH_COUNT,
        "selection_objective": "train robustness gates, then max mean daily P&L minus one daily standard error",
        "robust_candidates": len(robust), "winner": winner, "candidates": candidates,
    }
    save_json(OUTPUT, payload)
    m, c = winner["metrics"], winner["config"]
    print("S14 SPOT SHOCK FILTERS -- TRAIN ONLY")
    print("cut=%s searched=%d total-global=%d train=%d sealed_test=%d" %
          (payload["cut_iso"], SEARCH_COUNT, payload["global_search_count_after_this_stage"], n_train, n_test))
    print("robust candidates=%d; winner %r" % (len(robust), c))
    print("n=%d P&L=%+.2f per-trade=%+.4f daily-LCB=%+.4f null-p=%.5f top5-share=%s" %
          (m["n"], m["pnl"], m["pnl_per_trade"], m["daily_lcb"],
           winner["train_market_null_p"], winner["diagnostics"]["best_five_share"]))
    print("wrote %s; held-out outcomes remain unscored" % OUTPUT)


if __name__ == "__main__":
    main()
