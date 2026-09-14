"""Consolidated fastest-plausible strategy and automation report."""

import json
import os


GLOBAL_SEARCHES = 442
JSON_OUTPUT = "fastest_strategy.json"
MD_OUTPUT = "FASTEST_PLAUSIBLE_AUTOMATION.md"


def load(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    vol = load("volatility_oos_results.json")
    fast = load("fast_markets_oos_results.json")
    sizing = load("profit_rate_sizing.json")
    if vol["global_search_count"] != 114 or fast["global_searches"] != GLOBAL_SEARCHES:
        raise RuntimeError("upstream multiplicity count changed")
    robust = next(item for item in vol["results"] if item["strategy"] == "Robust RV estimator")
    gpu = next(item for item in fast["results"] if item["strategy"] == "GPU attention momentum")
    one = next(item for item in sizing["results"]["test"] if item["contracts"] == 1)
    two = next(item for item in sizing["results"]["test"] if item["contracts"] == 2)
    recommendation = {
        "strategy": "Robust RV estimator", "automation": "isolated paper engine",
        "start_command": "cd C:\\Users\\hudso\\kalshi-bot; npm run paper:robust-rv",
        "status_command": "cd C:\\Users\\hudso\\kalshi-bot; npm run status:robust-rv",
        "size_now": "1 paper contract per signal", "live_size_after_gate": "1 contract per signal",
        "fast_scenario_after_depth_validation": "2 contracts per signal at $500 bankroll",
        "one_contract_oos_pnl_per_day": one["pnl_per_day"],
        "two_contract_oos_scenario_pnl_per_day": two["pnl_per_day"],
        "two_contract_max_block_exposure": two["max_simultaneous_exposure"],
        "evidence_status": "plausible but not globally significant",
        "raw_market_null_p": robust["raw_market_null_p"],
        "global_corrected_p": min(1.0, robust["raw_market_null_p"] * GLOBAL_SEARCHES),
        "promotion_gate": sizing["recommended_deployment"]["promotion_gate"],
    }
    rows = [
        {"strategy": "Robust RV estimator", "train": robust["train"], "test": robust["test"],
         "corrected_p": min(1.0, robust["raw_market_null_p"] * GLOBAL_SEARCHES),
         "verdict": "FASTEST PLAUSIBLE; PAPER AUTOMATED"},
        *fast["results"],
    ]
    output = {"scope": {"web_used": False, "reason": "project constraint",
                         "local_universe": "33 broad series, 5 deep crypto series, 5 GPU series, 14 ladders, 14 weather cities"},
              "prior_searches": 298, "new_searches": 144,
              "global_searches": GLOBAL_SEARCHES, "rows": rows,
              "recommendation": recommendation,
              "runner_up": {"strategy": "GPU attention momentum",
                            "train_pnl": gpu["train"]["pnl"], "test_pnl": gpu["test"]["pnl"],
                            "test_pnl_per_day": gpu["test"]["pnl_per_elapsed_day"],
                            "raw_p": gpu["raw_market_null_p"],
                            "why_not_primary": "12 held-out events, raw p 0.392, top-five concentration 203%"}}
    with open(JSON_OUTPUT, "w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2, sort_keys=True)

    lines = ["# Fastest plausible automated Kalshi strategy", "",
             "## Decision", "",
             "Run the **Robust RV estimator** in isolated paper automation now at **one contract per signal**. "
             "It is the fastest candidate supported by the local evidence: +$7.148 on 200 held-out trades, "
             "+$0.396 per elapsed day per contract, raw market-null p=0.02530, four of five coins profitable, "
             "and all three held-out weeks profitable.", "",
             "```powershell", recommendation["start_command"], "```", "",
             "Audit forward progress with:", "", "```powershell", recommendation["status_command"], "```", "",
             "The engine cannot place real orders: it reads a separate paper config with no API credentials, "
             "uses a separate database, and hard-caps every signal at one contract.", "",
             "## Exact signal", "",
             "At minute 9 of each BTC/ETH/SOL/XRP/DOGE 15-minute market, compute the 120-minute sample "
             "standard deviation of completed Coinbase log returns. Invert the synchronized Kalshi mid into "
             "absolute implied volatility using `tau_eff = seconds_left - 40`. Require "
             "`abs(Phi^-1(mid)) >= 0.15` and `IV/RV >= 2`. Buy YES at ask if spot is above strike; otherwise "
             "buy NO at `1-bid`; hold to settlement.", "",
             "## How fast it can plausibly earn", "",
             "| contracts/signal | held-out P&L/day | held-out total | max drawdown | max simultaneous exposure | evidence |",
             "|---:|---:|---:|---:|---:|---|"]
    for item in sizing["results"]["test"]:
        lines.append("| %d | %+.3f | %+.2f | %.2f | %.2f | %s |" %
                     (item["contracts"], item["pnl_per_day"], item["pnl"],
                      item["max_realized_drawdown"], item["max_simultaneous_exposure"],
                      "displayed one-contract quote" if item["contracts"] == 1 else "capacity unverified"))
    lines.extend(["", "Two contracts would have produced **+$0.853/day** with at most **$9.94** simultaneous "
                  "historical exposure, about 2% of a $500 bankroll. Do not use that size until forward logs prove "
                  "the second contract fills at the quoted price. Larger rows are scaling scenarios, not evidence.", "",
                  "## New fast/trendy-market backtests", "",
                  "| strategy | train | test | corrected p | verdict |", "|---|---:|---:|---:|---|"])
    # Robust row.
    lines.append("| Robust RV | +$%.3f (n=%d) | +$%.3f (n=%d) | %.5f | fastest plausible |" %
                 (robust["train"]["pnl"], robust["train"]["n"], robust["test"]["pnl"],
                  robust["test"]["n"], recommendation["global_corrected_p"]))
    for item in fast["results"]:
        train = "+$%.3f (n=%d)" % (item["train"]["pnl"], item["train"]["n"]) if item["train"]["pnl"] >= 0 else "-$%.3f (n=%d)" % (-item["train"]["pnl"], item["train"]["n"])
        if item["test"] is None:
            test, p = "not opened", "n/a"
        else:
            test = "%s$%.3f (n=%d)" % ("+" if item["test"]["pnl"] >= 0 else "-", abs(item["test"]["pnl"]), item["test"]["n"])
            p = "%.5f" % item["corrected_market_null_p"]
        lines.append("| %s | %s | %s | %s | %s |" %
                     (item["strategy"], train, test, p, item["verdict"]))
    lines.extend(["", "The GPU niche is the runner-up: +$2.69 train and +$0.74 held out, with both YES and "
                  "NO profitable. It is too slow and too concentrated for primary deployment: only 12 held-out "
                  "events, raw p=0.392, and the five best trades equal 203% of net P&L.", "",
                  "## Honesty boundary", "",
                  "No strategy is globally significant after all **442** searched combinations; Robust RV's "
                  "corrected p is 1.0 and it historically failed the underlying-up drift block. This is why the "
                  "automation starts in paper, why an LLM cannot change orders, and why live size remains one "
                  "contract even after promotion. The 200-trade supervisor gate must pass before any real-money use.", "",
                  "## Automation files", "",
                  "- `../src/strategies/robustRv.js` - pure frozen signal.",
                  "- `../config.robust-rv.paper.json` - credential-free one-contract configuration.",
                  "- `../bin/robust-rv-paper.js` - isolated launcher.",
                  "- `../bin/robust-rv-status.js` - deterministic promotion audit.",
                  "- `../AUTOMATION_ROBUST_RV.md` - operating instructions.",
                  "- `PROFIT_RATE_SIZING.md` - fee-aware size scenarios.",
                  "- `FAST_MARKETS_RESULTS.md` - trendy-market diagnostics.", ""])
    with open(MD_OUTPUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    print("FASTEST PLAUSIBLE AUTOMATION")
    print("Robust RV paper x1: %+.3f/day held-out; raw-p %.5f; global corrected %.1f" %
          (one["pnl_per_day"], robust["raw_market_null_p"], recommendation["global_corrected_p"]))
    print("GPU runner-up: %+.3f/day, n=%d test, raw-p %.3f" %
          (gpu["test"]["pnl_per_elapsed_day"], gpu["test"]["n"], gpu["raw_market_null_p"]))
    print("wrote %s and %s" % (MD_OUTPUT, JSON_OUTPUT))


if __name__ == "__main__":
    main()
