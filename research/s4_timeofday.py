"""S4: time-of-day, weekday, and commodity-session calibration test.

This uses exactly one ex-ante quote per settled market: the first valid two-sided
quote.  Calibration is outcome minus that midpoint forecast.  A deliberately
simple trading screen may buy YES or NO for one train-selected calendar group;
it pays the appropriate displayed ask (YES) or 1-bid (NO), plus the specified
entry fee.  The newer 30% of market close times is held out.

Commodity session timing is a transparent local convention, not a claim about
each contract's exact venue: Sunday--Thursday 22:00 UTC is used as the next
Globex-style session boundary, with buckets 0--1h, 1--4h, 4--8h, and 8h+.
Crypto markets are excluded from that particular session test.

Run: python s4_timeofday.py
"""
import glob
import json
import math
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone

SEED = 20260831
N_NULL = 20_000
THRESHOLDS = (0.03, 0.05, 0.07, 0.10)
SESSION_BUCKETS = ("0-1h", "1-4h", "4-8h", "8h+")
CRYPTO_PREFIXES = ("KXBTC", "KXETH", "KXSOL", "KXXRP", "KXDOGE",
                   "KXBNB", "KXHYPE", "KXNEAR", "KXZEC")
# 24 hourly + 7 weekday + 4 commodity-session groups, each with four
# thresholds and two possible purchased sides.  This deliberately treats the
# train-estimated sign as a searched direction when correcting the p-value.
COMBINATIONS = (24 + 7 + len(SESSION_BUCKETS)) * len(THRESHOLDS) * 2


def fee(price):
    return math.ceil(0.07 * price * (1.0 - price) * 100.0 - 1e-12) / 100.0


def iso_week(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%G-W%V")


def load_markets():
    paths = (glob.glob("broad/*.json") + glob.glob("ladders/*.json") +
             glob.glob("tape2/KX*15M.json"))
    by_ticker = {}
    for path in paths:
        with open(path, "r", encoding="utf-8") as handle:
            records = json.load(handle)
        for market in records:
            if market.get("result") not in ("yes", "no") or not market.get("bars"):
                continue
            old = by_ticker.get(market.get("ticker"))
            if old is None or len(market["bars"]) > len(old["bars"]):
                by_ticker[market.get("ticker")] = market
    return list(by_ticker.values())


def session_bucket(ts):
    """Elapsed time from the most recent Sun--Thu 22:00 UTC session boundary."""
    now = datetime.fromtimestamp(ts, timezone.utc)
    for days_back in range(8):
        candidate_date = (now - timedelta(days=days_back)).date()
        # datetime.weekday: Monday=0 ... Sunday=6; sessions start Sun--Thu.
        if candidate_date.weekday() not in (6, 0, 1, 2, 3):
            continue
        start = datetime(candidate_date.year, candidate_date.month, candidate_date.day,
                         22, tzinfo=timezone.utc)
        if start <= now:
            hours = (now - start).total_seconds() / 3600.0
            if hours < 1.0:
                return "0-1h"
            if hours < 4.0:
                return "1-4h"
            if hours < 8.0:
                return "4-8h"
            return "8h+"
    return None


def first_observation(market):
    for bar in sorted(market["bars"], key=lambda row: row.get("ts", 0)):
        bid, ask = bar.get("bid"), bar.get("ask")
        if (isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and
                0.0 < bid < ask < 1.0):
            ts = bar["ts"]
            category = str(market.get("cat", market.get("category", ""))).lower()
            # Broad records carry ``cat``.  Some otherwise-identical ladder/tape
            # records do not, so infer their non-crypto commodity membership
            # from the local series code rather than silently excluding them.
            series = market.get("series", "")
            commodity = ("commodit" in category or
                         (not category and not series.startswith(CRYPTO_PREFIXES)))
            return {
                "ticker": market["ticker"], "series": market.get("series", "UNKNOWN"),
                "ts": ts, "close_ts": market["close_ts"], "week": iso_week(market["close_ts"]),
                "hour": datetime.fromtimestamp(ts, timezone.utc).hour,
                "weekday": datetime.fromtimestamp(ts, timezone.utc).weekday(),
                "session": session_bucket(ts) if commodity else None,
                "commodity": commodity,
                "bid": bid, "ask": ask, "mid": (bid + ask) / 2.0,
                "outcome": 1 if market["result"] == "yes" else 0,
            }
    return None


def calibration_rows(rows, key, title):
    groups = defaultdict(list)
    for row in rows:
        value = key(row)
        if value is not None:
            groups[value].append(row)
    print("\n%s (held-out; calibration error = outcome - first-quote midpoint)" % title)
    for name in sorted(groups, key=str):
        group = groups[name]
        error = sum(row["outcome"] - row["mid"] for row in group) / len(group)
        brier = sum((row["outcome"] - row["mid"]) ** 2 for row in group) / len(group)
        print("  %-8s n=%5d mean error=%+7.4f brier=%.5f" % (str(name), len(group), error, brier))


def feature_value(row, feature):
    return row[feature]


def train_groups(rows, feature):
    groups = defaultdict(list)
    for row in rows:
        value = feature_value(row, feature)
        if value is not None:
            groups[value].append(row)
    return groups


def make_trade(row, buy_yes):
    paid = row["ask"] if buy_yes else 1.0 - row["bid"]
    success = row["outcome"] if buy_yes else 1 - row["outcome"]
    trade = dict(row)
    trade.update({"side": "YES" if buy_yes else "NO", "p_paid": paid,
                  "fee": fee(paid), "pnl": success - paid - fee(paid)})
    return trade


def market_null(rows):
    observed = sum(row["pnl"] for row in rows)
    rng = random.Random(SEED)
    at_least = 0
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
    for value in sorted(groups, key=str):
        group = groups[value]
        pnl = sum(row["pnl"] for row in group)
        print("    %-14s n=%4d pnl=%+8.2f pnl/trade=%+7.4f" %
              (str(value), len(group), pnl, pnl / len(group)))


def main():
    observations = [row for row in (first_observation(market) for market in load_markets())
                    if row is not None]
    observations.sort(key=lambda row: (row["close_ts"], row["ticker"]))
    cut_ts = observations[int(0.70 * len(observations))]["close_ts"]
    train = [row for row in observations if row["close_ts"] < cut_ts]
    test = [row for row in observations if row["close_ts"] >= cut_ts]
    print("S4 TIME-OF-DAY / SESSION EFFECTS")
    print("Deduplicated, first-quote observations=%d. Chronological split at %s: train=%d test=%d." %
          (len(observations), datetime.fromtimestamp(cut_ts, timezone.utc).isoformat(),
           len(train), len(test)))
    print("Train-only search: one group among 24 hours, 7 weekdays, 4 commodity session buckets; "
          "four error thresholds and two directions = %d Bonferroni combinations." % COMBINATIONS)
    candidates = []
    # A candidate is exactly one calendar group.  Its direction is set from the
    # train calibration sign, and it must clear the train error threshold.
    for feature in ("hour", "weekday", "session"):
        for value, group in train_groups(train, feature).items():
            if len(group) < 30:
                continue
            error = sum(row["outcome"] - row["mid"] for row in group) / len(group)
            buy_yes = error > 0.0
            for threshold in THRESHOLDS:
                if abs(error) < threshold:
                    continue
                trades = [make_trade(row, buy_yes) for row in group]
                mean_pnl = sum(row["pnl"] for row in trades) / len(trades)
                candidates.append((mean_pnl, feature, value, threshold, buy_yes, error, trades))
    if not candidates:
        raise RuntimeError("no calendar group had >=30 train markets and the minimum calibration error")
    candidates.sort(key=lambda item: item[0], reverse=True)
    _, feature, value, threshold, buy_yes, train_error, train_trades = candidates[0]
    test_trades = [make_trade(row, buy_yes) for row in test if row[feature] == value]
    if not test_trades:
        raise RuntimeError("selected calendar group has no held-out markets")
    # The held-out set is first examined here, after the calendar feature,
    # group, threshold, and direction have all been frozen from train.
    calibration_rows(test, lambda row: row["hour"], "UTC hour")
    calibration_rows(test, lambda row: row["weekday"], "UTC weekday (Mon=0)")
    calibration_rows([row for row in test if row["commodity"]], lambda row: row["session"],
                     "Commodity proximity to 22:00 UTC session boundary")
    total_train = sum(row["pnl"] for row in train_trades)
    total_test = sum(row["pnl"] for row in test_trades)
    raw_p = market_null(test_trades)
    corrected_p = min(1.0, raw_p * COMBINATIONS)
    print("\nSelected on train: %s=%s, mean calibration error=%+.4f, |error| threshold=%.2f, buy %s." %
          (feature, value, train_error, threshold, "YES" if buy_yes else "NO"))
    print("train trades=%d pnl=%+.2f pnl/trade=%+.4f fees=%.2f" %
          (len(train_trades), total_train, total_train / len(train_trades),
           sum(row["fee"] for row in train_trades)))
    print("test  trades=%d pnl=%+.2f pnl/trade=%+.4f fees=%.2f" %
          (len(test_trades), total_test, total_test / len(test_trades),
           sum(row["fee"] for row in test_trades)))
    print("Held-out market null p(null >= ours)=%.5f; Bonferroni x%d => %.5f." %
          (raw_p, COMBINATIONS, corrected_p))
    print_breakdown(test_trades, lambda row: row["series"], "held-out series")
    print_breakdown(test_trades, lambda row: row["week"], "held-out ISO week")
    print_breakdown(test_trades, lambda row: row["side"], "held-out purchased side")
    top_five = sum(sorted((row["pnl"] for row in test_trades), reverse=True)[:5])
    print("  concentration check: five best held-out trades=%+.2f versus total=%+.2f." %
          (top_five, total_test))
    print("Verdict rule: positive held-out P&L and corrected p < 0.05 are both required.")


if __name__ == "__main__":
    main()
