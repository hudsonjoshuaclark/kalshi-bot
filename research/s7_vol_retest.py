"""Train-only reconstruction of the original 24-rule IV/RV candidate.

This is the exact family (3 entry minutes x 4 ratio thresholds x 2 regimes)
but with timestamp alignment and units corrected.  It never scores a held-out
outcome.  Run it before ``s10_vol_final.py``.

Run: python s7_vol_retest.py
"""

import argparse
import os

from volatility_common import (
    build_observations, chronological_cut, config_key, diagnostics, iso_time,
    load_data, market_null, metrics, relation, save_json, snapshot, trades_for,
)


ENTRIES = (1, 3, 5)
THRESHOLDS = (1.25, 1.50, 2.00, 3.00)
REGIMES = ("iv_high", "iv_low")
ESTIMATOR = "std30"
SEARCH_COUNT = len(ENTRIES) * len(THRESHOLDS) * len(REGIMES)
OUTPUT = "s7_vol_retest_train.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="deep")
    args = parser.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    markets, spots = load_data(args.data_dir)
    cut_ts, n_train_all, n_test_all, n_all = chronological_cut(markets)
    observations = build_observations(
        markets, spots, ENTRIES, (ESTIMATOR,), cut_ts=cut_ts, partition="train"
    )
    calendar_days = sorted({row["day"] for rows in observations.values() for row in rows})
    candidates = []
    for entry in ENTRIES:
        for threshold in THRESHOLDS:
            for regime in REGIMES:
                config = {
                    "entry": entry, "estimator": ESTIMATOR, "threshold": threshold,
                    "regime": regime, "min_abs_z": 0.0,
                    "min_paid": 0.0, "max_paid": 1.0, "max_spread": 1.0,
                }
                rows = trades_for(observations[(entry, ESTIMATOR)], config)
                result = {"config": config, "key": config_key(config),
                          "metrics": metrics(rows, calendar_days=calendar_days)}
                candidates.append(result)
    eligible = [item for item in candidates if item["metrics"]["n"] >= 100]
    if not eligible:
        raise RuntimeError("no original-family candidate had 100 train markets")
    # Reproduce the original selection objective, while using only deep/train.
    winner = max(eligible, key=lambda item: item["metrics"]["pnl_per_trade"])
    winner_rows = trades_for(
        observations[(winner["config"]["entry"], ESTIMATOR)], winner["config"]
    )
    winner = dict(winner)
    winner["train_market_null_p"] = market_null(winner_rows)
    winner["train_diagnostics"] = diagnostics(winner_rows)

    payload = {
        "idea": "corrected original IV/RV family",
        "data_dir": args.data_dir, "snapshot": snapshot(args.data_dir),
        "cut_ts": cut_ts, "cut_iso": iso_time(cut_ts),
        "all_markets": n_all, "train_markets_before_data_checks": n_train_all,
        "sealed_test_markets_before_data_checks": n_test_all,
        "search_count": SEARCH_COUNT, "selection_objective": "max train P&L per trade",
        "units_note": "IV and RV are both absolute-price volatility per sqrt(second)",
        "timestamp_note": "Kalshi end t uses Coinbase candle keyed t-60",
        "relations": {str(entry): relation(observations[(entry, ESTIMATOR)]) for entry in ENTRIES},
        "winner": winner, "candidates": candidates,
    }
    save_json(OUTPUT, payload)

    print("S7 CORRECTED ORIGINAL IV/RV FAMILY -- TRAIN ONLY")
    print("split %s; raw markets train=%d test(sealed)=%d; searched=%d" %
          (payload["cut_iso"], n_train_all, n_test_all, SEARCH_COUNT))
    for entry in ENTRIES:
        item = payload["relations"][str(entry)]
        print("entry %2dm relation n=%5d median IV/RV=%7.3f mean=%9.3f IV>RV=%5.1f%%" %
              (entry, item["n"], item["median_iv_rv"], item["mean_iv_rv"], item["iv_above_rv_pct"]))
    c, m = winner["config"], winner["metrics"]
    print("winner entry=%d estimator=%s threshold=%.2f regime=%s" %
          (c["entry"], c["estimator"], c["threshold"], c["regime"]))
    print("train n=%d P&L=%+.2f P&L/trade=%+.4f daily-LCB=%+.4f null-p=%.5f" %
          (m["n"], m["pnl"], m["pnl_per_trade"], m["daily_lcb"], winner["train_market_null_p"]))
    print("wrote %s; held-out outcomes remain unscored" % OUTPUT)


if __name__ == "__main__":
    main()
