"""Train-only search for volume-confirmed short-horizon quote moves."""

import os

from fast_common import (
    calendar, fast_diagnostics, load_fast_data, market_null, profit_rate,
    series_cuts, snapshot, train_robustness, volume_impulse_trades,
)
from volatility_common import save_json


ENTRIES = (5, 8, 11)
LOOKBACKS = (1, 3)
THRESHOLDS = (0.05, 0.12)
MODES = ("momentum", "reversal")
VOLUME_RATIOS = (1.0, 2.0)
SEARCH_COUNT = len(ENTRIES) * len(LOOKBACKS) * len(THRESHOLDS) * len(MODES) * len(VOLUME_RATIOS)
OUTPUT = "s20_volume_impulse_train.json"


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    markets, spots = load_fast_data()
    cuts, counts = series_cuts(markets)
    days = calendar(markets, cuts, "train")
    candidates = []
    for entry in ENTRIES:
        for lookback in LOOKBACKS:
            for threshold in THRESHOLDS:
                for mode in MODES:
                    for volume_ratio in VOLUME_RATIOS:
                        config = {"entry": entry, "lookback": lookback, "threshold": threshold,
                                  "mode": mode, "volume_ratio": volume_ratio}
                        rows = volume_impulse_trades(markets, spots, cuts, "train", config)
                        m, d = profit_rate(rows, days), fast_diagnostics(rows)
                        candidates.append({"config": config, "metrics": m, "diagnostics": d,
                                           "robustness": train_robustness(m, d)})
    eligible = [item for item in candidates if item["metrics"]["n"] >= 200]
    if not eligible:
        raise RuntimeError("no volume-impulse rule reached 200 train trades")
    winner = max(eligible, key=lambda item: item["metrics"]["daily_lcb"])
    rows = volume_impulse_trades(markets, spots, cuts, "train", winner["config"])
    winner["train_market_null_p"] = market_null(rows)
    files = [os.path.join("deep" if name in ("BTC","ETH","SOL","XRP","DOGE") else "broad",
                          "KX%s15M.json" % name) for name in markets]
    payload = {"idea": "volume-confirmed quote impulse", "series": list(markets),
               "cuts": cuts, "counts": counts, "search_count": SEARCH_COUNT,
               "selection": "max daily train P&L lower confidence bound; minimum 200 trades",
               "snapshot": snapshot(files), "winner": winner, "candidates": candidates}
    save_json(OUTPUT, payload)
    m = winner["metrics"]
    print("S20 VOLUME IMPULSE -- TRAIN ONLY")
    print("searched=%d winner=%r" % (SEARCH_COUNT, winner["config"]))
    print("n=%d pnl=%+.2f per=%+.4f per-day=%+.3f drawdown=%.2f null-p=%.5f" %
          (m["n"], m["pnl"], m["pnl_per_trade"], m["pnl_per_calendar_day"],
           m["max_realized_drawdown"], winner["train_market_null_p"]))
    print("wrote %s; per-series newest 30%% remains unscored" % OUTPUT)


if __name__ == "__main__":
    main()
