"""Consolidate all already-computed studies without reopening any holdout."""

import json
import os


GLOBAL_SEARCH_COUNT = 298  # 114 volatility + 148 microstructure + 36 dynamic ladder
MD_OUTPUT = "FINAL_RESEARCH_RESULTS.md"
JSON_OUTPUT = "final_recommendation.json"


def load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def cell(metrics, per_key):
    if metrics is None:
        return "not opened"
    if metrics["n"] == 0:
        return "no usable signals"
    return "%+.3f (%+.4f/trade, n=%d)" % (metrics["pnl"], metrics[per_key], metrics["n"])


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    vol = load("volatility_oos_results.json")
    micro = load("microstructure_oos_results.json")
    ladder = load("s16_dynamic_ladder_train.json")
    metal = load("metal_spot_shock_results.json")

    if vol["global_search_count"] != 114 or micro["audit"]["new_search_count"] != 148:
        raise RuntimeError("upstream search counts changed")
    if ladder["search_count"] != 36 or GLOBAL_SEARCH_COUNT != 114 + 148 + 36:
        raise RuntimeError("global multiplicity audit failed")

    rows = []
    for item in vol["results"]:
        rows.append({"strategy": item["strategy"], "train": item["train"],
                     "test": item["test"], "raw_p": item["raw_market_null_p"],
                     "corrected_p": min(1.0, item["raw_market_null_p"] * GLOBAL_SEARCH_COUNT),
                     "verdict": "BEST AVAILABLE - TRADE SMALL" if item["strategy"] == "Robust RV estimator" else "FAIL"})
    for item in micro["results"]:
        rows.append({"strategy": item["strategy"], "train": item["train"],
                     "test": item["test"], "raw_p": item["raw_market_null_p"],
                     "corrected_p": (min(1.0, item["raw_market_null_p"] * GLOBAL_SEARCH_COUNT)
                                     if item["raw_market_null_p"] is not None else None),
                     "verdict": ("RUNNER-UP; UNPROVEN" if item["strategy"] == "Spot-shock underreaction"
                                 else item["verdict"])})
    lw = ladder["winner"]
    rows.append({"strategy": "Dynamic ladder lock-in", "train": lw["metrics"], "test": None,
                 "raw_p": None, "corrected_p": None, "verdict": "REJECTED ON TRAIN; TEST NOT OPENED",
                 "per_key": "pnl_per_event"})
    rows.append({"strategy": "Frozen spot-shock on metals", "train": metal["train"],
                 "test": metal["test"], "raw_p": metal["raw_market_null_p"],
                 "corrected_p": None, "verdict": "INCONCLUSIVE; NO TEST SIGNALS"})

    robust = next(item for item in vol["results"] if item["strategy"] == "Robust RV estimator")
    config = robust["config"]
    rule = (
        "For each BTC, ETH, SOL, XRP, or DOGE 15-minute market, wait until minute 9 after open. "
        "Use the Kalshi bid/ask candle ending at that instant and the Coinbase candle keyed one minute "
        "earlier (which closes at the same instant). Let mid=(bid+ask)/2, z=Phi^-1(mid), and "
        "tau_eff=seconds to settlement minus 40. Compute implied absolute volatility as "
        "abs(spot-strike)/(abs(z)*sqrt(tau_eff)). Compute realised volatility as current spot times "
        "the sample standard deviation of the preceding 120 one-minute log returns, converted from "
        "per-minute to per-sqrt-second. Trade only when abs(z)>=0.15 and implied/realised>=2.0. "
        "If spot is above strike, buy YES at the displayed ask; otherwise buy NO at 1-bid. Hold to settlement."
    )
    recommendation = {
        "strategy": "Robust RV estimator", "status": "EXPERIMENTAL - positive out of sample, not multiplicity-safe",
        "config": config, "rule": rule, "size": "1 contract per qualifying market",
        "bankroll_floor": 500,
        "concurrency": "maximum 5 contracts across one simultaneous five-coin 15-minute block",
        "do_not_scale_until": "200 new forward trades are net profitable and both underlying-up and underlying-down blocks are nonnegative",
        "evidence": {"train_pnl": robust["train"]["pnl"], "train_n": robust["train"]["n"],
                     "test_pnl": robust["test"]["pnl"], "test_n": robust["test"]["n"],
                     "test_pnl_per_trade": robust["test"]["pnl_per_trade"],
                     "raw_market_null_p": robust["raw_market_null_p"],
                     "global_corrected_p": min(1.0, robust["raw_market_null_p"] * GLOBAL_SEARCH_COUNT),
                     "positive_test_series": robust["test"]["positive_series"],
                     "test_series": robust["test"]["series_count"],
                     "positive_test_weeks": robust["test"]["positive_weeks"],
                     "test_weeks": robust["test"]["week_count"],
                     "top_five_share": robust["diagnostics"]["best_five_share"],
                     "underlying_down_pnl": robust["diagnostics"]["drift"][0]["pnl"],
                     "underlying_up_pnl": robust["diagnostics"]["drift"][1]["pnl"]}}

    output = {"global_search_count": GLOBAL_SEARCH_COUNT, "rows": rows,
              "recommendation": recommendation,
              "holdout_policy": "aggregation only; no holdout data was rescored or used to tune a parameter"}
    with open(JSON_OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)

    lines = [
        "# Final Kalshi strategy recommendation", "",
        "## What to trade", "",
        "Trade **Robust RV estimator** at **1 contract per qualifying market**. Keep at least **$500** bankroll, "
        "allow at most five simultaneous one-contract signals across the five coins, and never average down.", "",
        rule, "",
        "This is the strongest candidate, not a claim of proven alpha. It earned **+$7.148 on 200 held-out trades "
        "(3.574 cents/trade)** with raw market-null p=0.02530, four of five coins profitable, all three held-out "
        "weeks profitable, and 28.5% of P&L from the best five trades. After all 298 searched combinations, "
        "corrected p=1.0. It also failed the drift check: underlying-down P&L was +$15.555 while "
        "underlying-up P&L was -$8.407. That is why size stays at one contract.", "",
        "Do not increase size until 200 additional forward trades remain net profitable **and** both underlying-up "
        "and underlying-down blocks are nonnegative.", "",
        "## Consolidated results", "",
        "| strategy | train | test | corrected p | verdict |", "|---|---:|---:|---:|---|",
    ]
    for item in rows:
        per_key = item.get("per_key", "pnl_per_trade")
        train_cell = cell(item["train"], per_key)
        test_cell = cell(item["test"], per_key) if item["test"] is not None else "not opened"
        corrected = "n/a" if item["corrected_p"] is None else "%.5f" % item["corrected_p"]
        lines.append("| %s | %s | %s | %s | %s |" %
                     (item["strategy"], train_cell, test_cell, corrected, item["verdict"]))
    lines.extend([
        "", "## Why this rule, not the positive spot-shock runner-up?", "",
        "The spot-shock rule made only +$0.515 on 51 held-out trades, raw p=0.366, with 256.5% of net profit "
        "coming from its five best trades; its NO side lost money and only BTC contributed meaningful profit. "
        "Robust RV has the larger sample, stronger market-null result, higher profit per trade, broader series/week "
        "support, and much lower concentration.", "",
        "## Files", "",
        "- `VOLATILITY_RESULTS.md`: corrected volatility study and full diagnostics.",
        "- `MICROSTRUCTURE_RESULTS.md`: two-touch, quote-impulse, and spot-shock studies.",
        "- `s16_dynamic_ladder_train.json`: dynamic ladder train rejection.",
        "- `metal_spot_shock_results.json`: frozen metal transportability check.",
        "- `final_recommendation.json`: machine-readable recommendation and audit totals.", "",
    ])
    with open(MD_OUTPUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print("FINAL RECOMMENDATION")
    print("TRADE Robust RV estimator | 1 contract/signal | bankroll >= $500")
    print("test +$%.3f on %d; raw-p=%.5f; corrected-p=%.1f; searches=%d" %
          (robust["test"]["pnl"], robust["test"]["n"], robust["raw_market_null_p"],
           recommendation["evidence"]["global_corrected_p"], GLOBAL_SEARCH_COUNT))
    print("wrote %s and %s" % (MD_OUTPUT, JSON_OUTPUT))


if __name__ == "__main__":
    main()

