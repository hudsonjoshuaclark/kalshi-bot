"""One-time held-out evaluation of all train-frozen volatility ideas.

Do not run until S7, S8, and S9 have been inspected and accepted as frozen.
The script evaluates their three winners in one pass over the newest 30%, uses
20,000 market-null redraws per winner, applies the global 114-combination
Bonferroni correction, and writes the requested summary plus diagnostics.

Run once: python s10_vol_final.py
"""

import argparse
import math
import os

from volatility_common import (
    N_NULL, SEED, breakdown, build_observations, chronological_cut, config_key,
    diagnostics, load_data, load_json, market_null, metrics, relation, save_json,
    snapshot, trades_for,
)


FREEZES = (
    ("Corrected original IV/RV", "s7_vol_retest_train.json"),
    ("Robust RV estimator", "s8_vol_estimators_train.json"),
    ("Cost-filtered IV/RV", "s9_vol_cost_filters_train.json"),
)
JSON_OUTPUT = "volatility_oos_results.json"
MD_OUTPUT = "VOLATILITY_RESULTS.md"


def fmt_money(value):
    return "%+.2f" % value


def fmt_rate(value):
    return "%+.4f" % value


def table_breakdown(items):
    lines = ["| block | n | P&L | P&L/trade |", "|---|---:|---:|---:|"]
    for item in items:
        lines.append("| %s | %d | %s | %s |" %
                     (item["label"], item["n"], fmt_money(item["pnl"]),
                      fmt_rate(item["pnl_per_trade"])))
    return "\n".join(lines)


def statistical_details(rows):
    wins = sum(row["success"] for row in rows)
    expected = sum(row["p_paid"] for row in rows)
    variance = sum(row["p_paid"] * (1.0 - row["p_paid"]) for row in rows)
    z = (wins - expected) / math.sqrt(variance) if variance > 0.0 else float("nan")
    # Coins closing in the same 15-minute block are correlated.  The mandated
    # market null redraws contracts independently, so also report an
    # outcome-based cluster-robust interval with close_ts as the cluster.
    mean_pnl = sum(row["pnl"] for row in rows) / len(rows)
    clusters = {}
    for row in rows:
        clusters.setdefault(row["close_ts"], []).append(row["pnl"])
    cluster_sums = [sum(value - mean_pnl for value in values)
                    for values in clusters.values()]
    group_count = len(cluster_sums)
    if group_count > 1:
        variance_mean = (group_count / (group_count - 1.0)) * sum(
            value * value for value in cluster_sums
        ) / (len(rows) * len(rows))
        cluster_se = math.sqrt(variance_mean)
        cluster_t = mean_pnl / cluster_se if cluster_se > 0.0 else float("nan")
        cluster_ci = [mean_pnl - 1.96 * cluster_se, mean_pnl + 1.96 * cluster_se]
    else:
        cluster_se = float("nan")
        cluster_t = float("nan")
        cluster_ci = [float("nan"), float("nan")]
    return {"observed_wins": wins, "market_expected_wins": expected,
            "market_null_z": z, "close_time_clusters": group_count,
            "cluster_robust_se_per_trade": cluster_se,
            "cluster_robust_t_net_pnl": cluster_t,
            "cluster_robust_95pct_ci_per_trade": cluster_ci}


def robustness_verdict(train_metrics, train_diag, test_metrics, diag, corrected_p):
    statistical = test_metrics["pnl"] > 0.0 and corrected_p < 0.05
    train_drift_positive = bool(train_diag["drift"]) and all(
        item["pnl"] > 0.0 for item in train_diag["drift"]
    )
    drift_positive = bool(diag["drift"]) and all(item["pnl"] > 0.0 for item in diag["drift"])
    train_series_ok = train_metrics["positive_series"] >= min(3, train_metrics["series_count"])
    train_weeks_needed = max(1, math.ceil(train_metrics["week_count"] / 2.0))
    train_weeks_ok = train_metrics["positive_weeks"] >= train_weeks_needed
    train_concentration_ok = (train_diag["best_five_share"] is not None and
                              train_diag["best_five_share"] < 0.50)
    series_ok = test_metrics["positive_series"] >= min(3, test_metrics["series_count"])
    weeks_needed = max(1, math.ceil(test_metrics["week_count"] / 2.0))
    weeks_ok = test_metrics["positive_weeks"] >= weeks_needed
    concentration_ok = diag["best_five_share"] is not None and diag["best_five_share"] < 0.50
    robust = (statistical and train_drift_positive and drift_positive and
              train_series_ok and series_ok and train_weeks_ok and weeks_ok and
              train_concentration_ok and concentration_ok)
    if robust:
        label = "PASS"
    elif statistical:
        label = "FRAGILE"
    else:
        label = "FAIL"
    return {"label": label, "statistical": statistical,
            "train_drift_positive": train_drift_positive,
            "test_drift_positive": drift_positive,
            "train_series_ok": train_series_ok, "test_series_ok": series_ok,
            "train_weeks_ok": train_weeks_ok, "test_weeks_ok": weeks_ok,
            "train_concentration_ok": train_concentration_ok,
            "test_concentration_ok": concentration_ok}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="deep")
    args = parser.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    frozen = []
    current_snapshot = snapshot(args.data_dir)
    for label, path in FREEZES:
        payload = load_json(path)
        if payload["snapshot"] != current_snapshot:
            raise RuntimeError("%s does not match the frozen data snapshot" % path)
        frozen.append((label, payload))
    cut_ts = frozen[0][1]["cut_ts"]
    if any(payload["cut_ts"] != cut_ts for _, payload in frozen):
        raise RuntimeError("frozen chronological cuts disagree")
    global_search_count = frozen[-1][1]["global_search_count"]
    expected_count = sum(payload["search_count"] for _, payload in frozen)
    if global_search_count != expected_count:
        raise RuntimeError("multiplicity audit failed: %d != %d" %
                           (global_search_count, expected_count))

    configs = [(label, payload["winner"]["config"], payload["winner"]["metrics"],
                payload["winner"]["train_diagnostics"])
               for label, payload in frozen]
    markets, spots = load_data(args.data_dir)
    check_cut, n_train_all, n_test_all, n_all = chronological_cut(markets)
    if check_cut != cut_ts:
        raise RuntimeError("data changed after strategies were frozen")
    entries = tuple(sorted(set(config["entry"] for _, config, _, _ in configs)))
    estimators = tuple(sorted(set(config["estimator"] for _, config, _, _ in configs)))
    observations = build_observations(
        markets, spots, entries, estimators, cut_ts=cut_ts, partition="test"
    )

    results = []
    test_trade_sets = {}
    for index, (label, config, train_metrics, train_diag) in enumerate(configs):
        source = observations[(config["entry"], config["estimator"])]
        rows = trades_for(source, config)
        if not rows:
            raise RuntimeError("%s made no held-out trades" % label)
        test_trade_sets[label] = rows
        test_metrics = metrics(rows)
        source_relation = relation(source)
        relation_by_coin = {coin: relation([row for row in source if row["coin"] == coin])
                            for coin in ("BTC", "ETH", "SOL", "XRP", "DOGE")}
        raw_p = market_null(rows, simulations=N_NULL, seed=SEED + 1009 * index)
        corrected_p = min(1.0, raw_p * global_search_count)
        diag = diagnostics(rows)
        verdict = robustness_verdict(train_metrics, train_diag, test_metrics, diag, corrected_p)
        results.append({
            "strategy": label, "config": config, "config_key": config_key(config),
            "train": train_metrics, "train_diagnostics": train_diag, "test": test_metrics,
            "market_null_simulations": N_NULL, "raw_market_null_p": raw_p,
            "bonferroni_combinations": global_search_count,
            "corrected_market_null_p": corrected_p,
            "statistics": statistical_details(rows),
            "test_iv_rv_relation": source_relation,
            "test_iv_rv_relation_by_coin": relation_by_coin,
            "diagnostics": diag, "verdict": verdict,
        })

    completion_audit = {
        "search_count_matches_freezes": global_search_count == 114,
        "all_train_trades_before_cut": all(item["train"]["last_close_ts"] < cut_ts
                                            for item in results),
        "all_test_trades_at_or_after_cut": all(item["test"]["first_close_ts"] >= cut_ts
                                                for item in results),
        "exactly_three_train_frozen_winners_tested_once": len(results) == 3,
        "twenty_thousand_null_redraws_each": all(item["market_null_simulations"] == 20_000
                                                  for item in results),
        "bonferroni_factor_applied": all(
            abs(item["corrected_market_null_p"] -
                min(1.0, item["raw_market_null_p"] * global_search_count)) < 1e-12
            for item in results
        ),
    }
    if not all(completion_audit.values()):
        raise RuntimeError("final methodology audit failed: %r" % completion_audit)

    passes = [item for item in results if item["verdict"]["label"] == "PASS"]
    if passes:
        # All p-values are already globally corrected, so choosing among genuine
        # survivors here does not evade the multiplicity penalty.
        recommended = max(passes, key=lambda item: item["test"]["pnl"])
        recommendation = {
            "strategy": recommended["strategy"],
            "size": "1 contract per signal initially",
            "risk_cap": "do not risk more than 0.5% of bankroll per market or 2.5% across a simultaneous five-coin block",
            "scale_condition": "collect at least 200 timestamped live trades before increasing size; no depth data supports a larger backtested order",
        }
    else:
        recommended = None
        recommendation = {
            "strategy": "none", "size": "0 contracts",
            "risk_cap": "not applicable",
            "scale_condition": "require a new untouched sample before reconsidering these families",
        }

    output = {
        "data_dir": args.data_dir, "snapshot": current_snapshot,
        "cut_ts": cut_ts, "all_markets": n_all,
        "train_markets_before_data_checks": n_train_all,
        "test_markets_before_data_checks": n_test_all,
        "global_search_count": global_search_count,
        "market_null_simulations_per_strategy": N_NULL,
        "completion_audit": completion_audit,
        "results": results, "recommendation": recommendation,
    }
    save_json(JSON_OUTPUT, output)

    lines = [
        "# Implied-versus-realised volatility: final held-out results",
        "",
        "The chronological cut and every rule were frozen before these test outcomes were scored. "
        "The study searched %d parameter combinations in total (24 original + 72 robust-estimator + 18 cost-filter combinations). "
        "Each reported p-value uses 20,000 redraws from the price actually paid and is Bonferroni-corrected by %d."
        % (global_search_count, global_search_count),
        "",
        "The old ~90x claim was a units error: absolute-price IV had been divided by relative-return RV. "
        "Here RV is multiplied by contemporaneous spot, and Coinbase candle key `t-60` is aligned with the Kalshi candle ending at `t`.",
        "",
        "| strategy | train | test | corrected p | verdict |",
        "|---|---:|---:|---:|---|",
    ]
    for item in results:
        lines.append("| %s | %s (%s/trade, n=%d) | %s (%s/trade, n=%d) | %.5f | %s |" %
                     (item["strategy"], fmt_money(item["train"]["pnl"]),
                      fmt_rate(item["train"]["pnl_per_trade"]), item["train"]["n"],
                      fmt_money(item["test"]["pnl"]), fmt_rate(item["test"]["pnl_per_trade"]),
                      item["test"]["n"], item["corrected_market_null_p"],
                      item["verdict"]["label"]))
    lines.extend(["", "A PASS requires positive held-out net P&L, corrected p < 0.05, and, in both train and test, positive P&L in underlying-up and underlying-down blocks, at least 3 profitable series, at least half the weeks profitable, and less than half of profit from the five best trades."])

    baseline_relation = results[0]["test_iv_rv_relation"]
    lines.extend([
        "", "## Corrected IV/RV relationship", "",
        "On the held-out data at the train-selected baseline entry, median IV/RV is %.3f and mean IV/RV is %.3f; IV exceeds RV in %.1f%% of usable markets."
        % (baseline_relation["median_iv_rv"], baseline_relation["mean_iv_rv"],
           baseline_relation["iv_above_rv_pct"]),
        "",
        "| series | n | median IV/RV | mean IV/RV | IV > RV |",
        "|---|---:|---:|---:|---:|",
    ])
    for coin, item in results[0]["test_iv_rv_relation_by_coin"].items():
        lines.append("| %s | %d | %.3f | %.3f | %.1f%% |" %
                     (coin, item["n"], item["median_iv_rv"], item["mean_iv_rv"],
                      item["iv_above_rv_pct"]))

    for item in results:
        diag = item["diagnostics"]
        lines.extend([
            "", "## %s" % item["strategy"], "",
            "Rule: `%s`." % item["config_key"], "",
            "Raw market-null p %.5f; corrected p %.5f. Five best trades: %s of total %s%s."
            % (item["raw_market_null_p"], item["corrected_market_null_p"],
               fmt_money(diag["best_five_pnl"]), fmt_money(item["test"]["pnl"]),
               (" (%.1f%%)" % (100.0 * diag["best_five_share"]) if diag["best_five_share"] is not None else "")),
            "Close-time cluster-robust 95%% CI on net P&L/trade: %+.4f to %+.4f (t=%+.2f across %d close-time clusters)."
            % (item["statistics"]["cluster_robust_95pct_ci_per_trade"][0],
               item["statistics"]["cluster_robust_95pct_ci_per_trade"][1],
               item["statistics"]["cluster_robust_t_net_pnl"],
               item["statistics"]["close_time_clusters"]),
            "", "### Train series", "", table_breakdown(item["train_diagnostics"]["series"]),
            "", "### Train ISO weeks", "", table_breakdown(item["train_diagnostics"]["weeks"]),
            "", "### Held-out series", "", table_breakdown(diag["series"]),
            "", "### Held-out ISO weeks", "", table_breakdown(diag["weeks"]),
            "", "### Drift check", "", table_breakdown(diag["drift"]),
            "", "### Purchased side", "", table_breakdown(diag["sides"]),
        ])
    lines.extend([
        "", "## Recommendation", "",
        ("Trade **%s** at **%s**. %s. %s."
         % (recommendation["strategy"], recommendation["size"],
            recommendation["risk_cap"], recommendation["scale_condition"])
         if recommended is not None else
         "Trade **none of these strategies**; size is **0 contracts**. A new untouched sample is required before reconsidering them."),
        "",
    ])
    with open(MD_OUTPUT, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))

    print("FINAL HELD-OUT VOLATILITY STUDY")
    print("global Bonferroni factor: %d; market-null redraws: %d per strategy" %
          (global_search_count, N_NULL))
    print("%-28s %24s %24s %12s %10s" %
          ("strategy", "train", "test", "corrected p", "verdict"))
    for item in results:
        train_text = ("%+.2f / %+.4f / n=%d" %
                      (item["train"]["pnl"], item["train"]["pnl_per_trade"], item["train"]["n"]))
        test_text = ("%+.2f / %+.4f / n=%d" %
                     (item["test"]["pnl"], item["test"]["pnl_per_trade"], item["test"]["n"]))
        print("%-28s %24s %24s %12.5f %10s" %
              (item["strategy"], train_text, test_text,
               item["corrected_market_null_p"], item["verdict"]["label"]))
    print("recommendation: %s at %s" % (recommendation["strategy"], recommendation["size"]))
    print("wrote %s and %s" % (JSON_OUTPUT, MD_OUTPUT))


if __name__ == "__main__":
    main()
