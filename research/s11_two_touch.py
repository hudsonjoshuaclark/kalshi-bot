"""Train-only search: sequential YES/NO two-touch positions.

Grid: 2 activation minutes x 4 last-first-entry minutes x 5 ask triggers
= 40 combinations.  Held-out outcomes are never scored here.
"""

import argparse
import os

from microstructure_common import diagnostics, market_null, metrics, two_touch_trades
from volatility_common import chronological_cut, iso_time, load_data, save_json, snapshot


ACTIVATIONS = (1, 3)
LAST_FIRSTS = (3, 5, 7, 9)
TRIGGERS = (0.25, 0.30, 0.35, 0.40, 0.45)
SEARCH_COUNT = len(ACTIVATIONS) * len(LAST_FIRSTS) * len(TRIGGERS)
OUTPUT = "s11_two_touch_train.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="deep")
    args = parser.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    markets, spots = load_data(args.data_dir)
    cut_ts, n_train, n_test, n_all = chronological_cut(markets)
    calendar_days = sorted({
        row["close_ts"] for coin in markets for row in markets[coin]
        if isinstance(row.get("close_ts"), (int, float)) and row["close_ts"] < cut_ts
    })
    # Collapse market timestamps to UTC calendar days without reading results.
    from volatility_common import iso_day
    calendar_days = sorted({iso_day(int(ts)) for ts in calendar_days})
    candidates = []
    for activation in ACTIVATIONS:
        for last_first in LAST_FIRSTS:
            for trigger in TRIGGERS:
                config = {"activation": activation, "last_first": last_first, "trigger": trigger}
                rows = two_touch_trades(markets, spots, cut_ts, "train", config)
                candidates.append({"config": config, "metrics": metrics(rows, calendar_days)})
    eligible = [item for item in candidates if item["metrics"]["n"] >= 200]
    if not eligible:
        raise RuntimeError("no two-touch candidate reached 200 train paths")
    winner = max(eligible, key=lambda item: item["metrics"]["daily_lcb"])
    rows = two_touch_trades(markets, spots, cut_ts, "train", winner["config"])
    winner["train_market_null_p"] = market_null(rows)
    winner["train_diagnostics"] = diagnostics(rows)
    payload = {
        "idea": "sequential two-touch YES/NO lock-in", "data_dir": args.data_dir,
        "snapshot": snapshot(args.data_dir), "cut_ts": cut_ts, "cut_iso": iso_time(cut_ts),
        "all_markets": n_all, "train_markets": n_train, "sealed_test_markets": n_test,
        "search_count": SEARCH_COUNT,
        "execution": "cross displayed ask after completed candle; second leg through minute 13; fee each leg",
        "selection_objective": "max mean daily train P&L minus one daily standard error",
        "winner": winner, "candidates": candidates,
    }
    save_json(OUTPUT, payload)
    m, c = winner["metrics"], winner["config"]
    print("S11 TWO-TOUCH -- TRAIN ONLY")
    print("cut=%s searched=%d train=%d sealed_test=%d" % (payload["cut_iso"], SEARCH_COUNT, n_train, n_test))
    print("winner %r | n=%d paired=%d P&L=%+.2f per-path=%+.4f daily-LCB=%+.4f null-p=%.5f" %
          (c, m["n"], m["paired_count"], m["pnl"], m["pnl_per_trade"], m["daily_lcb"], winner["train_market_null_p"]))
    print("wrote %s; held-out outcomes remain unscored" % OUTPUT)


if __name__ == "__main__":
    main()

