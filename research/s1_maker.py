"""S1: conservative, fee-aware passive market-making simulation.

Orders are one-contract YES orders.  At every quoted bar this simulation cancels
the previous quote and posts a fresh bid one tick below bid and offer one tick
above ask.  The quote is live until the next observed bar only.  A fill requires
the *next bar's reported trade price* (``close``) to be strictly beyond the
quote; a quote being touched is not a fill.  The pessimistic version requires a
trade at least two ticks beyond the quote.  This is not a queue model: it assumes
that a qualifying trade reaches our order (optimistic) or reaches it after two
ticks of adverse movement (pessimistic).  Neither assumption establishes actual
queue position or available size.

Run: python s1_maker.py
"""
import glob
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timezone

SEED = 20260831
N_NULL = 20_000
TICK = 0.01


def fee(price):
    """Kalshi entry fee for exactly one contract, rounded up to cents."""
    return math.ceil(0.07 * price * (1.0 - price) * 100.0 - 1e-12) / 100.0


def iso_week(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%G-W%V")


def load_markets():
    """Load every market file, retaining the richest record for duplicate tickers."""
    paths = (glob.glob("broad/*.json") + glob.glob("ladders/*.json") +
             glob.glob("tape2/KX*15M.json"))
    by_ticker = {}
    for path in paths:
        with open(path, "r", encoding="utf-8") as handle:
            records = json.load(handle)
        for market in records:
            if market.get("result") not in ("yes", "no") or not market.get("bars"):
                continue
            ticker = market.get("ticker")
            old = by_ticker.get(ticker)
            if old is None or len(market["bars"]) > len(old["bars"]):
                by_ticker[ticker] = market
    return list(by_ticker.values())


def usable_quote(bar):
    bid, ask = bar.get("bid"), bar.get("ask")
    return (isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and
            0.0 < bid < ask < 1.0)


def simulate_market(market, pessimistic=False):
    """Return one market-level settlement P&L observation.

    ``cash`` is positive for cash paid and negative for proceeds received.
    Every fill is charged its own entry fee.  The null probability is the mean
    YES execution price across this market's fills: it is the market price we
    actually transacted at, rather than a fitted probability.
    """
    bars = sorted(market["bars"], key=lambda item: item.get("ts", 0))
    qty = 0
    cash = 0.0
    fees = 0.0
    executions = []
    orders = 0
    max_abs_inventory = 0
    for now, later in zip(bars, bars[1:]):
        if not usable_quote(now):
            continue
        trade = later.get("close")
        if not isinstance(trade, (int, float)) or not (0.0 <= trade <= 1.0):
            continue
        buy_price = round(now["bid"] - TICK, 4)
        sell_price = round(now["ask"] + TICK, 4)
        # One bid and one offer are submitted; invalid prices are not submitted.
        if buy_price >= TICK:
            orders += 1
            threshold = buy_price - (2 * TICK if pessimistic else 0.0)
            # Strictly through for the base case.  For pessimistic, the reported
            # print must be at least two full ticks beyond our quote.
            filled = trade <= threshold if pessimistic else trade < threshold
            if filled:
                qty += 1
                cash += buy_price
                fees += fee(buy_price)
                executions.append(buy_price)
                max_abs_inventory = max(max_abs_inventory, abs(qty))
        if sell_price <= 1.0 - TICK:
            orders += 1
            threshold = sell_price + (2 * TICK if pessimistic else 0.0)
            filled = trade >= threshold if pessimistic else trade > threshold
            if filled:
                qty -= 1
                cash -= sell_price
                fees += fee(sell_price)
                executions.append(sell_price)
                max_abs_inventory = max(max_abs_inventory, abs(qty))
    if not orders:
        return None
    outcome = 1 if market["result"] == "yes" else 0
    pnl = outcome * qty - cash - fees
    return {
        "ticker": market["ticker"],
        "series": market.get("series", "UNKNOWN"),
        "ts": market.get("close_ts", market.get("open_ts", 0)),
        "outcome": outcome,
        "p_null": sum(executions) / len(executions) if executions else None,
        "pnl": pnl,
        "qty": qty,
        "fills": len(executions),
        "orders": orders,
        "fees": fees,
        "max_abs_inventory": max_abs_inventory,
    }


def summarize(rows):
    if not rows:
        return {"markets": 0, "fills": 0, "orders": 0, "pnl": 0.0,
                "per_fill": 0.0, "fill_rate": 0.0, "fees": 0.0,
                "inventory": Counter(), "max_inventory": Counter()}
    fills = sum(row["fills"] for row in rows)
    orders = sum(row["orders"] for row in rows)
    return {
        "markets": len(rows), "fills": fills, "orders": orders,
        "pnl": sum(row["pnl"] for row in rows),
        "per_fill": sum(row["pnl"] for row in rows) / fills if fills else 0.0,
        "fill_rate": fills / orders if orders else 0.0,
        "fees": sum(row["fees"] for row in rows),
        "inventory": Counter(row["qty"] for row in rows),
        "max_inventory": Counter(row["max_abs_inventory"] for row in rows),
    }


def market_null(rows):
    """20,000 market-level Bernoulli redraws using our actual execution price."""
    active = [row for row in rows if row["qty"]]
    # Flat-inventory markets have a fixed realised spread P&L.  Retain that
    # constant in both the observed and simulated portfolios rather than
    # dropping them from the market-level null.
    fixed = sum(row["pnl"] for row in rows if not row["qty"])
    observed = fixed + sum(row["pnl"] for row in active)
    rng = random.Random(SEED)
    at_least = 0
    for _ in range(N_NULL):
        simulated = fixed
        for row in active:
            outcome = 1 if rng.random() < row["p_null"] else 0
            # Cash and fees are fixed by the executed trades in this market.
            simulated += row["pnl"] + (outcome - row["outcome"]) * row["qty"]
        if simulated >= observed - 1e-12:
            at_least += 1
    return (at_least + 1) / (N_NULL + 1), len(active)


def breakdown(rows, key):
    groups = defaultdict(list)
    for row in rows:
        groups[key(row)].append(row)
    for name in sorted(groups, key=str):
        stats = summarize(groups[name])
        print("  %-14s markets=%4d fills=%5d pnl=%+8.2f pnl/fill=%+7.4f" %
              (str(name), stats["markets"], stats["fills"], stats["pnl"],
               stats["per_fill"]))


def print_result(label, train, test, combinations):
    train_stats, test_stats = summarize(train), summarize(test)
    raw_p, active_markets = market_null(test)
    corrected = min(1.0, raw_p * combinations)
    print("\n%s" % label)
    print("  train: markets=%d fills=%d/%d (%.2f%%), pnl=%+.2f, pnl/fill=%+.4f, fees=%.2f" %
          (train_stats["markets"], train_stats["fills"], train_stats["orders"],
           100 * train_stats["fill_rate"], train_stats["pnl"],
           train_stats["per_fill"], train_stats["fees"]))
    print("  test : markets=%d fills=%d/%d (%.2f%%), pnl=%+.2f, pnl/fill=%+.4f, fees=%.2f" %
          (test_stats["markets"], test_stats["fills"], test_stats["orders"],
           100 * test_stats["fill_rate"], test_stats["pnl"],
           test_stats["per_fill"], test_stats["fees"]))
    print("  market null: %d non-flat markets; p(null >= ours)=%.5f; Bonferroni x%d => %.5f" %
          (active_markets, raw_p, combinations, corrected))
    print("  final inventory distribution (test): %s" % dict(sorted(test_stats["inventory"].items())))
    print("  max |inventory| distribution (test): %s" % dict(sorted(test_stats["max_inventory"].items())))
    print("  test breakdown by series:")
    breakdown(test, lambda row: row["series"])
    print("  test breakdown by ISO week:")
    breakdown(test, lambda row: iso_week(row["ts"]))
    return train_stats, test_stats, raw_p, corrected


def main():
    markets = load_markets()
    markets.sort(key=lambda row: (row.get("close_ts", row.get("open_ts", 0)), row["ticker"]))
    cut_ts = markets[int(0.70 * len(markets))]["close_ts"]
    train_markets = [row for row in markets if row["close_ts"] < cut_ts]
    test_markets = [row for row in markets if row["close_ts"] >= cut_ts]
    # No tuning parameters were searched: the requested one-tick quote and the
    # separately requested two-tick-through stress case are pre-specified.
    combinations = 1
    print("S1 PASSIVE MARKET MAKING")
    print("Deduplicated markets=%d. Global chronological split at %s: train=%d, test=%d." %
          (len(markets), datetime.fromtimestamp(cut_ts, timezone.utc).isoformat(),
           len(train_markets), len(test_markets)))
    print("Parameter search: %d pre-specified configuration; correction factor=%d." %
          (combinations, combinations))
    for label, pessimistic in (("Base: strict one-tick-through fill", False),
                               ("Pessimistic: at least two ticks through", True)):
        train = [result for result in (simulate_market(row, pessimistic) for row in train_markets)
                 if result is not None]
        test = [result for result in (simulate_market(row, pessimistic) for row in test_markets)
                if result is not None]
        print_result(label, train, test, combinations)


if __name__ == "__main__":
    main()
