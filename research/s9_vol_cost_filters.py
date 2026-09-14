"""Train-only cost-filter search around the best unfiltered IV/RV rule.

This evaluates 6 paid-price bands x 3 maximum spreads = 18 combinations.  The
base rule is selected from all 96 candidates already evaluated by S7 and S8.
No held-out outcome is read or scored.

Run after S7 and S8: python s9_vol_cost_filters.py
"""

import argparse
import os

from volatility_common import (
    build_observations, chronological_cut, config_key, diagnostics, iso_time,
    load_data, load_json, market_null, metrics, save_json, snapshot, trades_for,
)


PRICE_BANDS = (
    (0.00, 1.00),
    (0.05, 0.50),
    (0.15, 0.50),
    (0.50, 0.75),
    (0.50, 0.85),
    (0.50, 0.95),
)
SPREAD_LIMITS = (1.00, 0.01, 0.02)
SEARCH_COUNT = len(PRICE_BANDS) * len(SPREAD_LIMITS)
OUTPUT = "s9_vol_cost_filters_train.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="deep")
    args = parser.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    s7 = load_json("s7_vol_retest_train.json")
    s8 = load_json("s8_vol_estimators_train.json")
    current_snapshot = snapshot(args.data_dir)
    for label, prior in (("S7", s7), ("S8", s8)):
        if prior["snapshot"] != current_snapshot:
            raise RuntimeError("%s was frozen against a different data snapshot" % label)
    cut_ts = s7["cut_ts"]
    if s8["cut_ts"] != cut_ts:
        raise RuntimeError("S7/S8 chronological cuts disagree")

    # This comparison is still train-only.  Every considered base candidate is
    # already included in the 24+72 multiplicity count.
    all_base = s7["candidates"] + s8["candidates"]
    eligible_base = [item for item in all_base if item["metrics"]["n"] >= 200]
    base = max(eligible_base, key=lambda item: item["metrics"]["daily_lcb"])
    base_config = dict(base["config"])

    markets, spots = load_data(args.data_dir)
    check_cut, n_train_all, n_test_all, n_all = chronological_cut(markets)
    if check_cut != cut_ts:
        raise RuntimeError("data cut changed after the train freezes")
    observations = build_observations(
        markets, spots, (base_config["entry"],), (base_config["estimator"],),
        cut_ts=cut_ts, partition="train"
    )
    source = observations[(base_config["entry"], base_config["estimator"])]
    calendar_days = sorted({row["day"] for row in source})
    candidates = []
    for min_paid, max_paid in PRICE_BANDS:
        for max_spread in SPREAD_LIMITS:
            config = dict(base_config)
            config.update({"min_paid": min_paid, "max_paid": max_paid,
                           "max_spread": max_spread})
            rows = trades_for(source, config)
            candidates.append({"config": config, "key": config_key(config),
                               "metrics": metrics(rows, calendar_days=calendar_days)})
    eligible = [item for item in candidates if item["metrics"]["n"] >= 100]
    if not eligible:
        raise RuntimeError("no cost-filter candidate had 100 train markets")
    winner = max(eligible, key=lambda item: item["metrics"]["daily_lcb"])
    winner_rows = trades_for(source, winner["config"])
    winner = dict(winner)
    winner["train_market_null_p"] = market_null(winner_rows)
    winner["train_diagnostics"] = diagnostics(winner_rows)

    payload = {
        "idea": "paid-price and spread filters",
        "data_dir": args.data_dir, "snapshot": current_snapshot,
        "cut_ts": cut_ts, "cut_iso": iso_time(cut_ts),
        "all_markets": n_all, "train_markets_before_data_checks": n_train_all,
        "sealed_test_markets_before_data_checks": n_test_all,
        "prior_search_count": s7["search_count"] + s8["search_count"],
        "search_count": SEARCH_COUNT,
        "global_search_count": s7["search_count"] + s8["search_count"] + SEARCH_COUNT,
        "selection_objective": "max mean daily train P&L minus one daily standard error",
        "base": base, "winner": winner, "candidates": candidates,
    }
    save_json(OUTPUT, payload)

    print("S9 COST FILTERS -- TRAIN ONLY")
    print("split %s; searched %d new filters; global combinations=%d" %
          (payload["cut_iso"], SEARCH_COUNT, payload["global_search_count"]))
    print("base %s" % config_key(base_config))
    c, m = winner["config"], winner["metrics"]
    print("winner paid %.2f..%.2f spread<=%.3f" %
          (c["min_paid"], c["max_paid"], c["max_spread"]))
    print("train n=%d P&L=%+.2f P&L/trade=%+.4f daily-LCB=%+.4f null-p=%.5f" %
          (m["n"], m["pnl"], m["pnl_per_trade"], m["daily_lcb"], winner["train_market_null_p"]))
    print("wrote %s; held-out outcomes remain unscored" % OUTPUT)


if __name__ == "__main__":
    main()
