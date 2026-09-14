"""One held-out pass for the frozen high-turnover/trendy-market studies."""

import math
import os

from microstructure_common import diagnostics, market_null, metrics
from s21_gpu_attention import chronological_cut, load_events, trades_for
from volatility_common import N_NULL, SEED, load_json, save_json


FREEZES = (("Outcome streak", "s19_outcome_streak_train.json"),
           ("Volume impulse", "s20_volume_impulse_train.json"),
           ("GPU attention momentum", "s21_gpu_attention_train.json"))
GLOBAL_SEARCHES = 442
JSON_OUTPUT = "fast_markets_oos_results.json"
MD_OUTPUT = "FAST_MARKETS_RESULTS.md"


def table(items):
    lines = ["| block | n | P&L | P&L/trade |", "|---|---:|---:|---:|"]
    for item in items:
        lines.append("| %s | %d | %+.3f | %+.4f |" %
                     (item["label"], item["n"], item["pnl"], item["pnl_per_trade"]))
    return "\n".join(lines)


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    freezes = [(label, load_json(path)) for label, path in FREEZES]
    new_count = sum(payload["search_count"] for _, payload in freezes)
    if new_count != 144 or GLOBAL_SEARCHES != 298 + new_count:
        raise RuntimeError("global search count mismatch")

    results = []
    for label, payload in freezes[:2]:
        results.append({"strategy": label, "config": payload["winner"]["config"],
                        "train": payload["winner"]["metrics"], "test": None,
                        "raw_market_null_p": None, "corrected_market_null_p": None,
                        "verdict": "REJECTED ON TRAIN; TEST NOT OPENED"})

    label, gpu = freezes[2]
    events = load_events()
    cut_ts, n_train_events, n_test_events = chronological_cut(events)
    if cut_ts != gpu["cut_ts"]:
        raise RuntimeError("GPU event cut changed")
    config = gpu["winner"]["config"]
    rows = trades_for(events, cut_ts, "test", config)
    if not rows:
        raise RuntimeError("GPU rule made no test trades")
    closes = [item["close_ts"] for item in events.values() if item["close_ts"] >= cut_ts]
    settlement_days = sorted({__import__("volatility_common").iso_day(ts) for ts in closes})
    elapsed = max(1.0, (max(closes) - min(closes)) / 86400.0)
    m = metrics(rows, calendar_days=settlement_days)
    m["elapsed_calendar_days"] = elapsed
    m["pnl_per_elapsed_day"] = m["pnl"] / elapsed
    m["events_per_elapsed_day"] = m["n"] / elapsed
    diag = diagnostics(rows)
    raw_p = market_null(rows, simulations=N_NULL, seed=SEED + 2200)
    corrected = min(1.0, raw_p * GLOBAL_SEARCHES)
    series_ok = m["positive_series"] >= min(3, m["series_count"])
    weeks_ok = m["positive_weeks"] >= max(1, math.ceil(m["week_count"] / 2.0))
    concentration_ok = diag["best_five_share"] is not None and diag["best_five_share"] < 0.50
    passed = m["pnl"] > 0.0 and corrected < 0.05 and series_ok and weeks_ok and concentration_ok
    verdict = "PASS" if passed else "POSITIVE BUT UNPROVEN" if m["pnl"] > 0.0 else "FAIL"
    results.append({"strategy": label, "config": config, "train": gpu["winner"]["metrics"],
                    "train_diagnostics": gpu["winner"]["diagnostics"], "test": m,
                    "diagnostics": diag, "raw_market_null_p": raw_p,
                    "market_null_simulations": N_NULL,
                    "bonferroni_combinations": GLOBAL_SEARCHES,
                    "corrected_market_null_p": corrected,
                    "robustness": {"series": series_ok, "weeks": weeks_ok,
                                   "concentration": concentration_ok},
                    "verdict": verdict})

    robust = next(item for item in load_json("volatility_oos_results.json")["results"]
                  if item["strategy"] == "Robust RV estimator")
    vol_days = max(1.0, (robust["test"]["last_close_ts"] - robust["test"]["first_close_ts"]) / 86400.0)
    comparison = {"robust_rv_test_pnl_per_day": robust["test"]["pnl"] / vol_days,
                  "robust_rv_test_trades_per_day": robust["test"]["n"] / vol_days,
                  "gpu_test_pnl_per_day": m["pnl_per_elapsed_day"],
                  "gpu_test_events_per_day": m["events_per_elapsed_day"]}
    output = {"prior_searches": 298, "new_searches": new_count,
              "global_searches": GLOBAL_SEARCHES, "gpu_events_train": n_train_events,
              "gpu_events_test": n_test_events, "results": results,
              "profit_rate_comparison": comparison}
    save_json(JSON_OUTPUT, output)

    lines = ["# Fast and trendy Kalshi markets: held-out results", "",
             "The 144 new rules were selected on each series' oldest 70% only. Outcome-streak and volume-impulse "
             "families lost on train and their holdouts stayed closed. The GPU winner was frozen before the newest "
             "15 GPU-series events were evaluated. The global correction factor is 442.", "",
             "| strategy | train | test | corrected p | verdict |", "|---|---:|---:|---:|---|"]
    for item in results:
        train = ("%+.3f (%+.4f/trade, n=%d)" %
                 (item["train"]["pnl"], item["train"]["pnl_per_trade"], item["train"]["n"]))
        if item["test"] is None:
            test, p = "not opened", "n/a"
        else:
            test = ("%+.3f (%+.4f/trade, n=%d)" %
                    (item["test"]["pnl"], item["test"]["pnl_per_trade"], item["test"]["n"]))
            p = "%.5f" % item["corrected_market_null_p"]
        lines.append("| %s | %s | %s | %s | %s |" %
                     (item["strategy"], train, test, p, item["verdict"]))
    lines.extend(["", "## GPU attention momentum", "",
                  "Rule: 60% through each GPU weekly event, examine each strike's move over its preceding three "
                  "observed bars. Among contracts moving at least 3 cents with positive volume/OI activity, select "
                  "the single most active contract and buy the move direction at the displayed ask; hold to settlement.", "",
                  "Raw market-null p=%.5f; corrected p=%.5f. P&L rate=%+.4f/day over %.1f elapsed test days. "
                  "Five best trades contributed %+.3f%s." %
                  (raw_p, corrected, m["pnl_per_elapsed_day"], elapsed, diag["best_five_pnl"],
                   (" (%.1f%% of net)" % (100 * diag["best_five_share"]) if diag["best_five_share"] is not None else "")),
                  "", "### GPU series", "", table(diag["series"]),
                  "", "### Settlement weeks", "", table(diag["weeks"]),
                  "", "### Side", "", table(diag["sides"]),
                  "", "## Speed comparison", "",
                  "Robust RV: %+.3f dollars/day and %.1f trades/day in its held-out span. "
                  "GPU attention: %+.3f dollars/day and %.2f events/day in its held-out span." %
                  (comparison["robust_rv_test_pnl_per_day"], comparison["robust_rv_test_trades_per_day"],
                   comparison["gpu_test_pnl_per_day"], comparison["gpu_test_events_per_day"]), ""])
    with open(MD_OUTPUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print("S22 FAST/TRENDY FINAL")
    for item in results:
        print(item["strategy"], "train", item["train"]["pnl"],
              "test", None if item["test"] is None else item["test"]["pnl"], item["verdict"])
    print("GPU raw-p=%.5f corrected=%.5f pnl/day=%+.4f" % (raw_p, corrected, m["pnl_per_elapsed_day"]))
    print("wrote %s and %s" % (JSON_OUTPUT, MD_OUTPUT))


if __name__ == "__main__":
    main()
