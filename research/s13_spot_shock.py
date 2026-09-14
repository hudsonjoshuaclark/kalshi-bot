"""Train-only search: same-minute Coinbase shock underreaction.

Grid: 4 entry minutes x 3 shock-z thresholds x 3 maximum quote responses
= 36.  The shock and Kalshi quote are both read at the completed entry candle.
"""

import argparse
import os

from microstructure_common import diagnostics, market_null, metrics, spot_shock_trades
from volatility_common import chronological_cut, iso_day, iso_time, load_data, save_json, snapshot


ENTRIES = (2, 4, 6, 8)
MIN_Z = (1.5, 2.0, 3.0)
MAX_RESPONSE = (0.03, 0.08, 0.15)
SEARCH_COUNT = len(ENTRIES) * len(MIN_Z) * len(MAX_RESPONSE)
OUTPUT = "s13_spot_shock_train.json"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="deep")
    args = parser.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    markets, spots = load_data(args.data_dir)
    cut_ts, n_train, n_test, n_all = chronological_cut(markets)
    calendar_days = sorted({iso_day(int(row["close_ts"])) for coin in markets for row in markets[coin]
                            if isinstance(row.get("close_ts"), (int, float)) and row["close_ts"] < cut_ts})
    candidates = []
    for entry in ENTRIES:
        for min_z in MIN_Z:
            for max_response in MAX_RESPONSE:
                config = {"entry": entry, "min_z": min_z, "max_response": max_response}
                rows = spot_shock_trades(markets, spots, cut_ts, "train", config)
                candidates.append({"config": config, "metrics": metrics(rows, calendar_days)})
    eligible = [item for item in candidates if item["metrics"]["n"] >= 100]
    if not eligible:
        raise RuntimeError("no spot-shock candidate reached 100 train trades")
    winner = max(eligible, key=lambda item: item["metrics"]["daily_lcb"])
    rows = spot_shock_trades(markets, spots, cut_ts, "train", winner["config"])
    winner["train_market_null_p"] = market_null(rows)
    winner["train_diagnostics"] = diagnostics(rows)
    payload = {
        "idea": "same-minute spot shock underreaction", "data_dir": args.data_dir,
        "snapshot": snapshot(args.data_dir), "cut_ts": cut_ts, "cut_iso": iso_time(cut_ts),
        "all_markets": n_all, "train_markets": n_train, "sealed_test_markets": n_test,
        "search_count": SEARCH_COUNT,
        "execution": "Coinbase candle t-60 and Kalshi candle t are simultaneously complete; cross current ask",
        "selection_objective": "max mean daily train P&L minus one daily standard error",
        "winner": winner, "candidates": candidates,
    }
    save_json(OUTPUT, payload)
    m, c = winner["metrics"], winner["config"]
    print("S13 SPOT SHOCK -- TRAIN ONLY")
    print("cut=%s searched=%d train=%d sealed_test=%d" % (payload["cut_iso"], SEARCH_COUNT, n_train, n_test))
    print("winner %r | n=%d P&L=%+.2f per-trade=%+.4f daily-LCB=%+.4f null-p=%.5f" %
          (c, m["n"], m["pnl"], m["pnl_per_trade"], m["daily_lcb"], winner["train_market_null_p"]))
    print("wrote %s; held-out outcomes remain unscored" % OUTPUT)


if __name__ == "__main__":
    main()
