"""Train-only search for robust RV estimators and better entry timing.

The grid is deliberately compact: 4 entry minutes x 6 prespecified estimators
x 3 high-IV thresholds = 72 combinations.  A fixed |z| >= 0.15 rejects the
numerically unstable neighborhood around a 50-cent mid.  Selection maximises a
one-standard-error lower bound on daily train P&L.

Run: python s8_vol_estimators.py
"""

import argparse
import os

from volatility_common import (
    build_observations, chronological_cut, config_key, diagnostics, iso_time,
    load_data, market_null, metrics, save_json, snapshot, trades_for,
)


ENTRIES = (3, 5, 7, 9)
ESTIMATORS = ("std60", "std120", "rms60", "ewma30", "park60", "bipower60")
THRESHOLDS = (1.25, 1.50, 2.00)
SEARCH_COUNT = len(ENTRIES) * len(ESTIMATORS) * len(THRESHOLDS)
OUTPUT = "s8_vol_estimators_train.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="deep")
    args = parser.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    markets, spots = load_data(args.data_dir)
    cut_ts, n_train_all, n_test_all, n_all = chronological_cut(markets)
    observations = build_observations(
        markets, spots, ENTRIES, ESTIMATORS, cut_ts=cut_ts, partition="train"
    )
    calendar_days = sorted({row["day"] for rows in observations.values() for row in rows})
    candidates = []
    for entry in ENTRIES:
        for estimator in ESTIMATORS:
            for threshold in THRESHOLDS:
                config = {
                    "entry": entry, "estimator": estimator, "threshold": threshold,
                    "regime": "iv_high", "min_abs_z": 0.15,
                    "min_paid": 0.0, "max_paid": 1.0, "max_spread": 1.0,
                }
                rows = trades_for(observations[(entry, estimator)], config)
                candidates.append({"config": config, "key": config_key(config),
                                   "metrics": metrics(rows, calendar_days=calendar_days)})
    eligible = [item for item in candidates if item["metrics"]["n"] >= 200]
    if not eligible:
        raise RuntimeError("no robust-estimator candidate had 200 train markets")
    winner = max(eligible, key=lambda item: item["metrics"]["daily_lcb"])
    c = winner["config"]
    winner_rows = trades_for(observations[(c["entry"], c["estimator"])], c)
    winner = dict(winner)
    winner["train_market_null_p"] = market_null(winner_rows)
    winner["train_diagnostics"] = diagnostics(winner_rows)

    payload = {
        "idea": "robust RV estimators and later entries",
        "data_dir": args.data_dir, "snapshot": snapshot(args.data_dir),
        "cut_ts": cut_ts, "cut_iso": iso_time(cut_ts),
        "all_markets": n_all, "train_markets_before_data_checks": n_train_all,
        "sealed_test_markets_before_data_checks": n_test_all,
        "search_count": SEARCH_COUNT,
        "selection_objective": "max mean daily train P&L minus one daily standard error",
        "fixed_inversion_filter": "abs(Phi^-1(mid)) >= 0.15",
        "winner": winner, "candidates": candidates,
    }
    save_json(OUTPUT, payload)

    print("S8 ROBUST VOL ESTIMATORS -- TRAIN ONLY")
    print("split %s; raw markets train=%d test(sealed)=%d; searched=%d" %
          (payload["cut_iso"], n_train_all, n_test_all, SEARCH_COUNT))
    c, m = winner["config"], winner["metrics"]
    print("winner entry=%d estimator=%s threshold=%.2f regime=%s |z|>=%.2f" %
          (c["entry"], c["estimator"], c["threshold"], c["regime"], c["min_abs_z"]))
    print("train n=%d P&L=%+.2f P&L/trade=%+.4f daily-LCB=%+.4f null-p=%.5f" %
          (m["n"], m["pnl"], m["pnl_per_trade"], m["daily_lcb"], winner["train_market_null_p"]))
    print("train sign consistency: %d/%d series, %d/%d weeks positive" %
          (m["positive_series"], m["series_count"], m["positive_weeks"], m["week_count"]))
    print("wrote %s; held-out outcomes remain unscored" % OUTPUT)


if __name__ == "__main__":
    main()
