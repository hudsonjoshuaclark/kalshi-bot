"""Independent post-run verifier for the frozen volatility study artifacts.

Run after s10_vol_final.py.  A nonzero exit means the result is not publishable.
"""

import math
import os

from volatility_common import load_json


FREEZES = (
    "s7_vol_retest_train.json",
    "s8_vol_estimators_train.json",
    "s9_vol_cost_filters_train.json",
)
RESULTS = "volatility_oos_results.json"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def finite_tree(value, path="root"):
    if isinstance(value, float):
        require(math.isfinite(value), "%s contains non-finite float" % path)
    elif isinstance(value, dict):
        for key, item in value.items():
            finite_tree(item, "%s.%s" % (path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            finite_tree(item, "%s[%d]" % (path, index))


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    freezes = [load_json(path) for path in FREEZES]
    result = load_json(RESULTS)
    require([item["search_count"] for item in freezes] == [24, 72, 18],
            "freeze search counts are not 24+72+18")
    require(sum(item["search_count"] for item in freezes) == 114,
            "global search count is not 114")
    require(all(item["snapshot"] == freezes[0]["snapshot"] for item in freezes),
            "freeze data snapshots disagree")
    require(all(item["cut_ts"] == freezes[0]["cut_ts"] for item in freezes),
            "freeze time cuts disagree")
    require(result["snapshot"] == freezes[0]["snapshot"],
            "test result uses a different data snapshot")
    require(result["cut_ts"] == freezes[0]["cut_ts"],
            "test result uses a different time cut")
    require(result["global_search_count"] == 114,
            "result does not use global Bonferroni factor 114")
    require(result["market_null_simulations_per_strategy"] == 20_000,
            "result does not use 20,000 null redraws")
    require(len(result["results"]) == 3, "expected exactly three frozen winners")

    for item, freeze in zip(result["results"], freezes):
        require(item["config"] == freeze["winner"]["config"],
                "%s test config differs from train freeze" % item["strategy"])
        require(item["train"] == freeze["winner"]["metrics"],
                "%s train metrics differ from freeze" % item["strategy"])
        require(item["train"]["last_close_ts"] < result["cut_ts"],
                "%s has train trades at/after cut" % item["strategy"])
        require(item["test"]["first_close_ts"] >= result["cut_ts"],
                "%s has test trades before cut" % item["strategy"])
        require(item["market_null_simulations"] == 20_000,
                "%s null redraw count is wrong" % item["strategy"])
        expected_p = min(1.0, item["raw_market_null_p"] * 114)
        require(abs(item["corrected_market_null_p"] - expected_p) < 1e-12,
                "%s Bonferroni correction is wrong" % item["strategy"])
        diagnostics = item["diagnostics"]
        require(all(key in diagnostics for key in
                    ("series", "weeks", "sides", "drift", "best_five_pnl", "best_five_share")),
                "%s robustness diagnostics are incomplete" % item["strategy"])
        require(sum(group["n"] for group in diagnostics["series"]) == item["test"]["n"],
                "%s series counts do not reconcile" % item["strategy"])
        require(sum(group["n"] for group in diagnostics["weeks"]) == item["test"]["n"],
                "%s week counts do not reconcile" % item["strategy"])
        require(sum(group["n"] for group in diagnostics["sides"]) == item["test"]["n"],
                "%s side counts do not reconcile" % item["strategy"])

    pass_names = [item["strategy"] for item in result["results"]
                  if item["verdict"]["label"] == "PASS"]
    if pass_names:
        require(result["recommendation"]["strategy"] in pass_names,
                "recommendation is not a robust PASS")
    else:
        require(result["recommendation"]["strategy"] == "none" and
                result["recommendation"]["size"] == "0 contracts",
                "failed study recommends a nonzero trade")
    finite_tree(result)
    print("VERIFIED: time split, freezes, 114x correction, 20,000-redraw nulls, "
          "diagnostic reconciliation, and recommendation all pass")


if __name__ == "__main__":
    main()

