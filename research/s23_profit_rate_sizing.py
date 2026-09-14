"""Profit-rate and drawdown scaling for the frozen Robust RV rule.

This is not another parameter search: every size replays the identical frozen
trade set.  Historical bars contain price but not top-of-book depth, so results
above one contract are explicitly capacity-unverified scenarios.
"""

import math
import os
from collections import defaultdict

from volatility_common import (
    build_observations, chronological_cut, fee, load_data, load_json, save_json, trades_for,
)


SIZES = (1, 2, 5, 10, 20)
OUTPUT = "profit_rate_sizing.json"
MD_OUTPUT = "PROFIT_RATE_SIZING.md"


def scaled(rows, contracts):
    answer = []
    for row in rows:
        entry_fee = fee(contracts, row["p_paid"])
        pnl = contracts * (row["success"] - row["p_paid"]) - entry_fee
        answer.append({**row, "contracts": contracts, "scaled_fee": entry_fee,
                       "scaled_cost": contracts * row["p_paid"] + entry_fee,
                       "scaled_pnl": pnl})
    return answer


def summary(rows, partition_first, partition_last, bankroll=500.0):
    elapsed_days = max(1.0, (partition_last - partition_first) / 86400.0)
    pnl = sum(row["scaled_pnl"] for row in rows)
    cumulative = peak = drawdown = 0.0
    blocks = defaultdict(list)
    for row in sorted(rows, key=lambda item: (item["close_ts"], item["ticker"])):
        cumulative += row["scaled_pnl"]
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
        blocks[row["close_ts"]].append(row)
    exposures = [sum(row["scaled_cost"] for row in block) for block in blocks.values()]
    block_pnls = [sum(row["scaled_pnl"] for row in block) for block in blocks.values()]
    return {"n": len(rows), "contracts": rows[0]["contracts"] if rows else 0,
            "pnl": pnl, "pnl_per_trade": pnl / len(rows),
            "elapsed_days": elapsed_days, "pnl_per_day": pnl / elapsed_days,
            "fees": sum(row["scaled_fee"] for row in rows),
            "max_realized_drawdown": drawdown,
            "max_simultaneous_exposure": max(exposures) if exposures else 0.0,
            "max_block_exposure_pct_500_bankroll": max(exposures) / bankroll if exposures else 0.0,
            "worst_simultaneous_block_pnl": min(block_pnls) if block_pnls else 0.0,
            "best_simultaneous_block_pnl": max(block_pnls) if block_pnls else 0.0,
            "capacity_status": "one-contract displayed quote" if rows and rows[0]["contracts"] == 1
                               else "UNVERIFIED: assumes every contract fills at recorded top ask"}


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    frozen = load_json("s8_vol_estimators_train.json")["winner"]["config"]
    markets, spots = load_data("deep")
    cut_ts, _, _, _ = chronological_cut(markets)
    observations_train = build_observations(markets, spots, (frozen["entry"],),
                                            (frozen["estimator"],), cut_ts=cut_ts, partition="train")
    observations_test = build_observations(markets, spots, (frozen["entry"],),
                                           (frozen["estimator"],), cut_ts=cut_ts, partition="test")
    train_rows = trades_for(observations_train[(frozen["entry"], frozen["estimator"])], frozen)
    test_rows = trades_for(observations_test[(frozen["entry"], frozen["estimator"])], frozen)
    if len(train_rows) != 261 or len(test_rows) != 200:
        raise RuntimeError("frozen trade count changed")
    partitions = {
        "train": (train_rows, min(row["close_ts"] for row in train_rows),
                  max(row["close_ts"] for row in train_rows)),
        "test": (test_rows, min(row["close_ts"] for row in test_rows),
                 max(row["close_ts"] for row in test_rows)),
    }
    results = {}
    for label, (rows, first, last) in partitions.items():
        results[label] = [summary(scaled(rows, size), first, last) for size in SIZES]
    payload = {"strategy": "Robust RV estimator", "config": frozen,
               "sizes_are_not_parameter_searches": True,
               "historical_depth_available": False, "results": results,
               "recommended_deployment": {
                   "paper": "1 contract per signal immediately",
                   "live_after_promotion": "1 contract per signal",
                   "fast_but_capacity_unverified": "2 contracts per signal at $500 bankroll only after live depth/fill logging",
                   "promotion_gate": "200 new forward trades net positive, both drift regimes nonnegative, no reconciliation errors",
               }}
    save_json(OUTPUT, payload)

    lines = ["# Robust RV profit-rate sizing", "",
             "Every row replays the identical frozen signals; size is not reselected from outcomes. "
             "Only one contract is supported by the historical quote. Larger scenarios assume sufficient depth at "
             "the recorded top ask and therefore are not yet executable evidence.", "",
             "| contracts/signal | train P&L/day | test P&L/day | test total | test max DD | max block exposure | % of $500 | capacity |",
             "|---:|---:|---:|---:|---:|---:|---:|---|"]
    for train, test in zip(results["train"], results["test"]):
        lines.append("| %d | %+.3f | %+.3f | %+.2f | %.2f | %.2f | %.2f%% | %s |" %
                     (test["contracts"], train["pnl_per_day"], test["pnl_per_day"], test["pnl"],
                      test["max_realized_drawdown"], test["max_simultaneous_exposure"],
                      100 * test["max_block_exposure_pct_500_bankroll"], test["capacity_status"]))
    lines.extend(["", "Recommended now: **one contract per signal in isolated paper mode**. "
                  "After the promotion gate, use one contract live. Two contracts is the fastest bounded scenario "
                  "at a $500 bankroll, but only after real depth and fill logs show it remains executable.", ""])
    with open(MD_OUTPUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print("ROBUST RV PROFIT-RATE SIZING")
    for item in results["test"]:
        print("x%-2d test=%+.2f pnl/day=%+.3f maxDD=%.2f maxBlock=%.2f capacity=%s" %
              (item["contracts"], item["pnl"], item["pnl_per_day"],
               item["max_realized_drawdown"], item["max_simultaneous_exposure"],
               item["capacity_status"]))
    print("wrote %s and %s" % (OUTPUT, MD_OUTPUT))


if __name__ == "__main__":
    main()
