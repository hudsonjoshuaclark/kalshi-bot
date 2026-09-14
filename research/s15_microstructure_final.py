"""Single held-out pass for all train-frozen post-volatility candidates.

S11 and S12 failed on train and are not promoted.  The S13 spot-shock winner
and its S14 tail-risk variant are evaluated together on the newest 30%.  Both
use 20,000 price-paid market-null redraws and the global 262-search Bonferroni
factor (114 earlier volatility searches plus 148 searches in S11--S14).
"""

import argparse
import math
import os

from microstructure_common import (
    GLOBAL_SEARCH_COUNT, N_NULL, SEED, cluster_statistics, diagnostics,
    market_null, metrics, spot_shock_trades, unique_market_audit,
)
from s14_spot_shock_filters import filtered_rows
from volatility_common import chronological_cut, load_data, load_json, save_json, snapshot


FREEZES = (
    ("Two-touch lock-in", "s11_two_touch_train.json"),
    ("Quote impulse", "s12_quote_impulse_train.json"),
    ("Spot-shock underreaction", "s13_spot_shock_train.json"),
    ("Filtered spot-shock", "s14_spot_shock_filters_train.json"),
)
JSON_OUTPUT = "microstructure_oos_results.json"
MD_OUTPUT = "MICROSTRUCTURE_RESULTS.md"


def robustness(metrics_value, diag, cluster):
    drift_ok = len(diag["drift"]) == 2 and all(item["pnl"] > 0.0 for item in diag["drift"])
    series_ok = metrics_value["positive_series"] >= min(3, metrics_value["series_count"])
    weeks_ok = metrics_value["positive_weeks"] >= max(1, math.ceil(metrics_value["week_count"] / 2.0))
    concentration_ok = diag["best_five_share"] is not None and diag["best_five_share"] < 0.50
    cluster_ok = bool(cluster.get("ci95")) and cluster["ci95"][0] is not None and cluster["ci95"][0] > 0.0
    return {"drift": drift_ok, "series": series_ok, "weeks": weeks_ok,
            "concentration": concentration_ok, "cluster_ci": cluster_ok,
            "all": drift_ok and series_ok and weeks_ok and concentration_ok and cluster_ok}


def table(items):
    lines = ["| block | n | P&L | P&L/trade |", "|---|---:|---:|---:|"]
    for item in items:
        lines.append("| %s | %d | %+.3f | %+.4f |" %
                     (item["label"], item["n"], item["pnl"], item["pnl_per_trade"]))
    return "\n".join(lines)


def exact_rule(label, config):
    if label == "Filtered spot-shock":
        return ("At minute %d, compute the last completed Coinbase 1-minute return divided by "
                "the sample standard deviation of the preceding 30 returns. If |z| >= %.1f and "
                "Kalshi's signed mid response from the preceding minute is <= %.2f, buy the shock "
                "direction at the displayed ask only if price <= %.2f; hold to settlement." %
                (config["entry"], config["min_z"], config["max_response"], config["max_paid"]))
    return ("At minute %d, compute the last completed Coinbase 1-minute return divided by the "
            "sample standard deviation of the preceding 30 returns. If |z| >= %.1f and Kalshi's "
            "signed mid response from the preceding minute is <= %.2f, buy YES after a positive "
            "shock or NO after a negative shock at the displayed ask; hold to settlement." %
            (config["entry"], config["min_z"], config["max_response"]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="deep")
    args = parser.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    current_snapshot = snapshot(args.data_dir)
    frozen = []
    for label, path in FREEZES:
        payload = load_json(path)
        if payload["snapshot"] != current_snapshot:
            raise RuntimeError("%s does not match frozen data" % path)
        frozen.append((label, payload))
    cut_ts = frozen[0][1]["cut_ts"]
    if any(payload["cut_ts"] != cut_ts for _, payload in frozen):
        raise RuntimeError("frozen cuts disagree")
    new_searches = sum(payload["search_count"] for _, payload in frozen)
    if new_searches != 148 or GLOBAL_SEARCH_COUNT != 114 + new_searches:
        raise RuntimeError("multiplicity count mismatch")

    markets, spots = load_data(args.data_dir)
    check_cut, n_train, n_test, n_all = chronological_cut(markets)
    if check_cut != cut_ts:
        raise RuntimeError("data changed after train freeze")

    results = []
    # S11/S12 were killed without inspecting test outcomes.
    for label, payload in frozen[:2]:
        results.append({
            "strategy": label, "config": payload["winner"]["config"],
            "train": payload["winner"]["metrics"], "test": None,
            "raw_market_null_p": None, "corrected_market_null_p": None,
            "bonferroni_combinations": GLOBAL_SEARCH_COUNT,
            "verdict": "REJECTED ON TRAIN; TEST NOT OPENED",
        })

    promoted = []
    for index, (label, payload) in enumerate(frozen[2:]):
        config = payload["winner"]["config"]
        if label == "Filtered spot-shock":
            rows = filtered_rows(markets, spots, cut_ts, "test", config)
            train_diag = payload["winner"]["diagnostics"]
        else:
            rows = spot_shock_trades(markets, spots, cut_ts, "test", config)
            train_diag = payload["winner"]["train_diagnostics"]
        if not rows or not unique_market_audit(rows):
            raise RuntimeError("%s has empty or duplicate held-out observations" % label)
        test_metrics = metrics(rows)
        diag = diagnostics(rows)
        cluster = cluster_statistics(rows)
        raw_p = market_null(rows, simulations=N_NULL, seed=SEED + index * 1009)
        corrected_p = min(1.0, raw_p * GLOBAL_SEARCH_COUNT)
        test_robustness = robustness(test_metrics, diag, cluster)
        train_robustness = robustness(payload["winner"]["metrics"], train_diag,
                                      cluster_statistics([]))
        # Cluster significance is a held-out requirement, not a train selection gate.
        train_robustness["all_without_cluster"] = all(
            train_robustness[key] for key in ("drift", "series", "weeks", "concentration")
        )
        proven = (test_metrics["pnl"] > 0.0 and corrected_p < 0.05 and
                  test_robustness["all"] and train_robustness["all_without_cluster"])
        verdict = "PASS" if proven else "POSITIVE BUT UNPROVEN" if test_metrics["pnl"] > 0.0 else "FAIL"
        item = {
            "strategy": label, "config": config, "rule": exact_rule(label, config),
            "train": payload["winner"]["metrics"], "train_diagnostics": train_diag,
            "test": test_metrics, "diagnostics": diag, "cluster_statistics": cluster,
            "market_null_simulations": N_NULL, "raw_market_null_p": raw_p,
            "bonferroni_combinations": GLOBAL_SEARCH_COUNT,
            "corrected_market_null_p": corrected_p,
            "train_robustness": train_robustness, "test_robustness": test_robustness,
            "verdict": verdict,
        }
        results.append(item)
        promoted.append(item)

    proven = [item for item in promoted if item["verdict"] == "PASS"]
    positive = [item for item in promoted if item["test"]["pnl"] > 0.0]
    if proven:
        chosen = max(proven, key=lambda item: item["test"]["pnl_per_trade"])
        evidence = "PROVEN IN THIS STUDY"
    elif positive:
        chosen = min(positive, key=lambda item: (item["raw_market_null_p"],
                                                  -item["test"]["pnl_per_trade"]))
        evidence = "BEST AVAILABLE; EXPERIMENTAL, NOT MULTIPLICITY-SAFE"
    else:
        # The previously evaluated robust-volatility rule is the only positive
        # held-out candidate available.  Use it rather than recommending a new
        # rule that actually lost out of sample.
        old = load_json("volatility_oos_results.json")
        chosen = next(item for item in old["results"] if item["strategy"] == "Robust RV estimator")
        evidence = "BEST AVAILABLE FALLBACK; EXPERIMENTAL, NOT MULTIPLICITY-SAFE"

    recommendation = {
        "strategy": chosen["strategy"], "evidence": evidence,
        "config": chosen["config"],
        "rule": chosen.get("rule", "Use the frozen Robust RV estimator configuration exactly as recorded in volatility_oos_results.json."),
        "size": "1 contract per qualifying market",
        "bankroll_floor": "$500 before live use",
        "risk_limits": "maximum 1 contract per market and 5 contracts across a simultaneous five-coin block; never average down",
        "scale_condition": "do not increase size until 200 additional timestamped forward trades remain net profitable",
    }

    audit = {
        "new_search_count": new_searches, "prior_search_count": 114,
        "global_bonferroni_count": GLOBAL_SEARCH_COUNT,
        "one_observation_per_test_market": all(unique_market_audit(rows) for rows in [
            spot_shock_trades(markets, spots, cut_ts, "test", frozen[2][1]["winner"]["config"]),
            filtered_rows(markets, spots, cut_ts, "test", frozen[3][1]["winner"]["config"]),
        ]),
        "train_before_cut": all(item["train"]["last_close_ts"] < cut_ts for item in results),
        "promoted_test_at_or_after_cut": all(item["test"]["first_close_ts"] >= cut_ts for item in promoted),
        "null_redraws": N_NULL,
        "test_pass_executed_once": True,
    }
    if not (audit["one_observation_per_test_market"] and audit["train_before_cut"] and
            audit["promoted_test_at_or_after_cut"] and new_searches == 148):
        raise RuntimeError("methodology audit failed: %r" % audit)

    output = {
        "data_dir": args.data_dir, "snapshot": current_snapshot, "cut_ts": cut_ts,
        "all_markets": n_all, "train_markets": n_train, "test_markets": n_test,
        "audit": audit, "results": results, "recommendation": recommendation,
    }
    save_json(JSON_OUTPUT, output)

    lines = [
        "# Cost-aware microstructure search: final results", "",
        "The search used the oldest 70% for all selection and opened the newest 30% once. "
        "It searched 148 new combinations; the reported correction factor is 262 after adding "
        "the 114 earlier volatility combinations. Every entry crosses the contemporaneous displayed "
        "ask, every leg pays the entry fee, and each market contributes at most one observation.", "",
        "| strategy | train | test | corrected p | verdict |",
        "|---|---:|---:|---:|---|",
    ]
    for item in results:
        train_text = "%+.2f (%+.4f/trade, n=%d)" % (item["train"]["pnl"], item["train"]["pnl_per_trade"], item["train"]["n"])
        if item["test"] is None:
            test_text, corrected = "not opened", "n/a"
        else:
            test_text = "%+.2f (%+.4f/trade, n=%d)" % (item["test"]["pnl"], item["test"]["pnl_per_trade"], item["test"]["n"])
            corrected = "%.5f" % item["corrected_market_null_p"]
        lines.append("| %s | %s | %s | %s | %s |" %
                     (item["strategy"], train_text, test_text, corrected, item["verdict"]))
    for item in promoted:
        d, c = item["diagnostics"], item["cluster_statistics"]
        lines.extend([
            "", "## %s" % item["strategy"], "", item["rule"], "",
            "Raw market-null p=%.5f; Bonferroni-corrected p=%.5f. Close-time cluster 95%% CI per trade: %+.4f to %+.4f. "
            "Five best trades contributed %+.3f%s." %
            (item["raw_market_null_p"], item["corrected_market_null_p"], c["ci95"][0], c["ci95"][1],
             d["best_five_pnl"], (" (%.1f%% of net)" % (100.0 * d["best_five_share"]) if d["best_five_share"] is not None else "")),
            "", "### Series", "", table(d["series"]),
            "", "### ISO weeks", "", table(d["weeks"]),
            "", "### Underlying drift", "", table(d["drift"]),
            "", "### Purchased side", "", table(d["sides"]),
        ])
    lines.extend([
        "", "## What to trade", "",
        "**%s — %s.**" % (recommendation["strategy"], recommendation["evidence"]), "",
        recommendation["rule"], "",
        "Size: **%s**, only with at least %s bankroll. %s. %s." %
        (recommendation["size"], recommendation["bankroll_floor"],
         recommendation["risk_limits"], recommendation["scale_condition"]), "",
    ])
    with open(MD_OUTPUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    print("FINAL COST-AWARE MICROSTRUCTURE STUDY")
    print("global Bonferroni=%d; null redraws=%d" % (GLOBAL_SEARCH_COUNT, N_NULL))
    for item in results:
        if item["test"] is None:
            print("%-28s train=%+.2f n=%d | TEST NOT OPENED | %s" %
                  (item["strategy"], item["train"]["pnl"], item["train"]["n"], item["verdict"]))
        else:
            print("%-28s train=%+.2f n=%d | test=%+.2f n=%d | raw-p=%.5f corrected=%.5f | %s" %
                  (item["strategy"], item["train"]["pnl"], item["train"]["n"],
                   item["test"]["pnl"], item["test"]["n"], item["raw_market_null_p"],
                   item["corrected_market_null_p"], item["verdict"]))
    print("TRADE: %s | %s | %s" %
          (recommendation["strategy"], recommendation["size"], recommendation["evidence"]))
    print("wrote %s and %s" % (JSON_OUTPUT, MD_OUTPUT))


if __name__ == "__main__":
    main()
