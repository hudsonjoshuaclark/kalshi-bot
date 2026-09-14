"""Train-only search for cross-window outcome continuation/reversal."""

import os

from fast_common import (
    calendar, fast_diagnostics, load_fast_data, market_null, profit_rate,
    series_cuts, snapshot, streak_trades, train_robustness,
)
from volatility_common import save_json


ENTRIES = (1, 3, 5, 7)
STREAKS = (1, 2, 3)
MODES = ("continuation", "reversal")
MAX_PAID = (0.75, 0.90, 0.99)
SEARCH_COUNT = len(ENTRIES) * len(STREAKS) * len(MODES) * len(MAX_PAID)
OUTPUT = "s19_outcome_streak_train.json"


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    markets, spots = load_fast_data()
    cuts, counts = series_cuts(markets)
    days = calendar(markets, cuts, "train")
    candidates = []
    for entry in ENTRIES:
        for streak in STREAKS:
            for mode in MODES:
                for max_paid in MAX_PAID:
                    config = {"entry": entry, "streak": streak, "mode": mode, "max_paid": max_paid}
                    rows = streak_trades(markets, spots, cuts, "train", config)
                    m, d = profit_rate(rows, days), fast_diagnostics(rows)
                    candidates.append({"config": config, "metrics": m, "diagnostics": d,
                                       "robustness": train_robustness(m, d)})
    eligible = [item for item in candidates if item["metrics"]["n"] >= 300]
    if not eligible:
        raise RuntimeError("no streak rule reached 300 train trades")
    winner = max(eligible, key=lambda item: item["metrics"]["daily_lcb"])
    rows = streak_trades(markets, spots, cuts, "train", winner["config"])
    winner["train_market_null_p"] = market_null(rows)
    files = [os.path.join("deep" if name in ("BTC","ETH","SOL","XRP","DOGE") else "broad",
                          "KX%s15M.json" % name) for name in markets]
    payload = {"idea": "cross-window outcome streak", "series": list(markets),
               "cuts": cuts, "counts": counts, "search_count": SEARCH_COUNT,
               "selection": "max daily train P&L lower confidence bound; minimum 300 trades",
               "snapshot": snapshot(files), "winner": winner, "candidates": candidates}
    save_json(OUTPUT, payload)
    m = winner["metrics"]
    print("S19 OUTCOME STREAK -- TRAIN ONLY")
    print("searched=%d winner=%r" % (SEARCH_COUNT, winner["config"]))
    print("n=%d pnl=%+.2f per=%+.4f per-day=%+.3f drawdown=%.2f null-p=%.5f" %
          (m["n"], m["pnl"], m["pnl_per_trade"], m["pnl_per_calendar_day"],
           m["max_realized_drawdown"], winner["train_market_null_p"]))
    print("wrote %s; per-series newest 30%% remains unscored" % OUTPUT)


if __name__ == "__main__":
    main()

