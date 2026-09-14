"""S3: cross-series disagreement among synchronous 15-minute crypto markets.

For every complete five-coin window, the own-market forecast is its executable
midpoint and the cross-market forecast is the midpoint average of the other four
coins.  The trading rule buys YES only in the one coin furthest below that
cross-sectional consensus.  A 70/30 chronological split selects the entry bar
and disagreement threshold using train data only.

Run: python s3_crossmarket.py
"""
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timezone

SEED = 20260831
N_NULL = 20_000
COINS = ("BTC", "ETH", "SOL", "XRP", "DOGE")
ENTRY_MINUTES = (1, 3, 5)
GAP_THRESHOLDS = (0.03, 0.05, 0.07, 0.10)
COMBINATIONS = len(ENTRY_MINUTES) * len(GAP_THRESHOLDS)


def fee(price):
    return math.ceil(0.07 * price * (1.0 - price) * 100.0 - 1e-12) / 100.0


def iso_week(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%G-W%V")


def load_markets(coin):
    with open("tape2/KX%s15M.json" % coin, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_spot(coin):
    with open("tape2/spot_%s-USD.json" % coin, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return {int(ts): row["close"] for ts, row in raw.items()
            if isinstance(row.get("close"), (int, float)) and row["close"] > 0.0}


def make_observation(market, coin, entry_minute, spots):
    ts = market["open_ts"] + entry_minute * 60
    bar = next((row for row in market.get("bars", []) if row.get("ts") == ts), None)
    if bar is None:
        return None
    bid, ask = bar.get("bid"), bar.get("ask")
    if not (isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and
            0.0 < bid < ask < 1.0):
        return None
    start_spot, end_spot = spots.get(ts), spots.get(market["close_ts"])
    return {
        "coin": coin, "ticker": market["ticker"], "open_ts": market["open_ts"],
        "close_ts": market["close_ts"], "week": iso_week(market["close_ts"]),
        "mid": (bid + ask) / 2.0, "ask": ask,
        "outcome": 1 if market["result"] == "yes" else 0,
        "underlying_return": (math.log(end_spot / start_spot)
                              if start_spot and end_spot and start_spot > 0.0 and end_spot > 0.0
                              else None),
    }


def synchronous_windows(entry_minute, sources):
    per_coin = {}
    for coin in COINS:
        per_coin[coin] = {}
        for market in sources[coin]["markets"]:
            row = make_observation(market, coin, entry_minute, sources[coin]["spots"])
            if row is not None:
                per_coin[coin][row["open_ts"]] = row
    windows = []
    for open_ts in set.intersection(*(set(per_coin[coin]) for coin in COINS)):
        rows = [per_coin[coin][open_ts] for coin in COINS]
        # This check prevents a superficially matching open time from combining
        # different settlement windows.
        if len({row["close_ts"] for row in rows}) == 1:
            windows.append(rows)
    return sorted(windows, key=lambda rows: rows[0]["close_ts"])


def candidate_trades(windows, threshold):
    trades = []
    for rows in windows:
        chosen = None
        chosen_gap = float("-inf")
        for row in rows:
            consensus = (sum(other["mid"] for other in rows) - row["mid"]) / 4.0
            gap = consensus - row["mid"]
            if gap > chosen_gap:
                chosen, chosen_gap = row, gap
        if chosen_gap >= threshold:
            trade = dict(chosen)
            trade.update({"consensus": chosen_gap + chosen["mid"], "gap": chosen_gap,
                          "p_paid": chosen["ask"], "fee": fee(chosen["ask"]),
                          "pnl": chosen["outcome"] - chosen["ask"] - fee(chosen["ask"])})
            trades.append(trade)
    return trades


def brier_report(label, windows):
    own, consensus = [], []
    for rows in windows:
        for row in rows:
            other_mean = (sum(other["mid"] for other in rows) - row["mid"]) / 4.0
            own.append((row["outcome"] - row["mid"]) ** 2)
            consensus.append((row["outcome"] - other_mean) ** 2)
    own_brier = sum(own) / len(own)
    other_brier = sum(consensus) / len(consensus)
    print("%s: %d market observations, own Brier=%.5f, other-four Brier=%.5f, improvement=%+.5f" %
          (label, len(own), own_brier, other_brier, own_brier - other_brier))


def pnl_per_trade(rows):
    return sum(row["pnl"] for row in rows) / len(rows) if rows else float("-inf")


def market_null(rows):
    observed = sum(row["pnl"] for row in rows)
    at_least = 0
    rng = random.Random(SEED)
    for _ in range(N_NULL):
        simulated = 0.0
        for row in rows:
            simulated += (1.0 if rng.random() < row["p_paid"] else 0.0) - row["p_paid"] - row["fee"]
        if simulated >= observed - 1e-12:
            at_least += 1
    return (at_least + 1) / (N_NULL + 1)


def print_breakdown(rows, key, title):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    print("  %s:" % title)
    for group_name in sorted(groups, key=str):
        group = groups[group_name]
        pnl = sum(row["pnl"] for row in group)
        print("    %-14s n=%3d pnl=%+7.2f pnl/trade=%+7.4f" %
              (str(group_name), len(group), pnl, pnl / len(group)))


def main():
    sources = {coin: {"markets": load_markets(coin), "spots": load_spot(coin)} for coin in COINS}
    all_windows = {minute: synchronous_windows(minute, sources) for minute in ENTRY_MINUTES}
    reference = all_windows[1]
    cut_ts = reference[int(0.70 * len(reference))][0]["close_ts"]
    train = {minute: [rows for rows in all_windows[minute] if rows[0]["close_ts"] < cut_ts]
             for minute in ENTRY_MINUTES}
    test = {minute: [rows for rows in all_windows[minute] if rows[0]["close_ts"] >= cut_ts]
            for minute in ENTRY_MINUTES}
    print("S3 CROSS-SERIES DISAGREEMENT")
    print("Complete five-coin windows only. Chronological split at %s." %
          datetime.fromtimestamp(cut_ts, timezone.utc).isoformat())
    print("Train-only search: entry minute %s x outlier threshold %s = %d combinations." %
          (ENTRY_MINUTES, GAP_THRESHOLDS, COMBINATIONS))
    for minute in ENTRY_MINUTES:
        brier_report("Train predictive comparison, minute %d" % minute, train[minute])
    candidates = []
    for minute in ENTRY_MINUTES:
        for threshold in GAP_THRESHOLDS:
            rows = candidate_trades(train[minute], threshold)
            if len(rows) >= 20:
                candidates.append((pnl_per_trade(rows), minute, threshold, rows))
    if not candidates:
        raise RuntimeError("no train candidate had 20 complete-window trades")
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, minute, threshold, train_trades = candidates[0]
    # The held-out data is first used after all search choices are fixed.
    test_trades = candidate_trades(test[minute], threshold)
    if not test_trades:
        raise RuntimeError("selected train rule made no held-out trades")
    print("\nSelected on train: entry minute=%d, buy outlier when other-four consensus minus own mid >= %.2f." %
          (minute, threshold))
    brier_report("Held-out predictive comparison at selected minute", test[minute])
    for label, rows in (("train", train_trades), ("test", test_trades)):
        total = sum(row["pnl"] for row in rows)
        print("%s trades=%d pnl=%+.2f pnl/trade=%+.4f fees=%.2f mean gap=%.4f" %
              (label, len(rows), total, total / len(rows), sum(row["fee"] for row in rows),
               sum(row["gap"] for row in rows) / len(rows)))
    raw_p = market_null(test_trades)
    print("Held-out market null p(null >= ours)=%.5f; Bonferroni x%d => %.5f." %
          (raw_p, COMBINATIONS, min(1.0, raw_p * COMBINATIONS)))
    print_breakdown(test_trades, lambda row: row["coin"], "held-out chosen coin")
    print_breakdown(test_trades, lambda row: row["week"], "held-out ISO week")
    with_return = [row for row in test_trades if row["underlying_return"] is not None]
    if with_return:
        print_breakdown(with_return,
                        lambda row: "underlying up" if row["underlying_return"] > 0 else "underlying down/flat",
                        "drift check: selected coin move to settlement")
        print("  mean selected-coin log return: %+.5f (%d/%d closing spot observations)." %
              (sum(row["underlying_return"] for row in with_return) / len(with_return),
               len(with_return), len(test_trades)))
    top_five = sum(sorted((row["pnl"] for row in test_trades), reverse=True)[:5])
    print("  concentration check: five best held-out trades=%+.2f versus total=%+.2f." %
          (top_five, sum(row["pnl"] for row in test_trades)))
    print("Verdict rule: positive held-out P&L and corrected p < 0.05 are both required.")


if __name__ == "__main__":
    main()
