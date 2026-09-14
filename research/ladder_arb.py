#!/usr/bin/env python3
"""Scan locally saved Kalshi strike ladders for executable static arbitrage.

This program deliberately does not fill quotes forward: every pair used below is
quoted in an actual bar with the same timestamp.  It also treats a zero bid as
a valid quote, but rejects malformed/crossed/out-of-bounds books.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any, DefaultDict, Iterable


ROOT = Path(__file__).resolve().parent
INPUT_DIRECTORIES = (ROOT / "ladders", ROOT / "broad")
OUTPUT_FILE = ROOT / "ladder_arb_violations.json"
ONE = Decimal("1")
CENT = Decimal("0.01")
FEE_RATE = Decimal("0.07")
THRESHOLD_STRIKE_TYPES = {"greater", "greater_or_equal"}


# Payoff algebra, for K1 < K2 and settlement S:
#   YES(K1) = 1[S > K1],  NO(K2) = 1[S <= K2].
#   YES(K1) + NO(K2) =
#       1  if S <= K1,
#       2  if K1 < S <= K2,
#       1  if S > K2.
# Thus this two-leg position has a guaranteed minimum payout of $1 per pair.
# (The superficially reversed pair YES(K2) + NO(K1) has minimum payout $0 and
# is intentionally never reported.)


def decimal_price(value: Any) -> Decimal:
    """Parse a JSON numeric price without inheriting binary-float error."""
    return Decimal(str(value))


def fee_for_entry(price: Decimal, contracts: int = 1) -> Decimal:
    """Kalshi entry fee in dollars, rounded *up* independently for each leg."""
    cents = FEE_RATE * Decimal(contracts) * price * (ONE - price) * Decimal(100)
    return cents.to_integral_value(rounding=ROUND_CEILING) / Decimal(100)


def event_key(market: dict[str, Any]) -> str:
    event = market.get("event")
    if event:
        return str(event)
    ticker = str(market["ticker"])
    return ticker.rsplit("-", 1)[0]


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def quote_is_valid(bar: dict[str, Any]) -> bool:
    """A tradable YES book must be ordered and lie inside the $0--$1 bounds."""
    bid, ask = bar.get("bid"), bar.get("ask")
    return (
        finite_number(bid)
        and finite_number(ask)
        and 0.0 <= bid <= ask <= 1.0
    )


def depth_from_bar(bar: dict[str, Any], side: str) -> Any:
    """Return displayed size, if the source happened to retain it.

    The supplied data normally contains only price, volume, and sometimes OI;
    volume/OI are not treated as executable top-of-book depth.
    """
    names = (
        ("ask_size", "ask_depth", "yes_ask_size", "yes_ask_depth", "ask_qty")
        if side == "ask"
        else ("bid_size", "bid_depth", "yes_bid_size", "yes_bid_depth", "bid_qty")
    )
    for name in names:
        if name in bar:
            return bar[name]
    return None


def serialized_amount(value: Decimal) -> str:
    """Render an amount exactly (quotes can be finer than a cent).

    Fees are integral cents by construction, but the saved books include
    mill-priced quotes.  Rounding a $0.001 edge to cents would conceal both
    its size and, in edge cases, the fact that it remains strictly positive.
    """
    rendered = format(value, "f")
    whole, dot, fraction = rendered.partition(".")
    if not dot:
        return whole + ".00"
    fraction = fraction.rstrip("0")
    return whole + ("." + fraction if fraction else ".00")


def all_input_files() -> Iterable[Path]:
    for directory in INPUT_DIRECTORIES:
        yield from sorted(directory.glob("*.json"))


def load_events() -> tuple[dict[str, list[dict[str, Any]]], int, int]:
    """Load each local JSON array, grouping contracts by the required event key."""
    events: DefaultDict[str, list[dict[str, Any]]] = defaultdict(list)
    records = 0
    non_threshold_contracts_skipped = 0
    # Ticker deduplication prevents an accidental duplicated download from
    # creating duplicate strikes within an otherwise identical event.
    seen_tickers: set[str] = set()
    for path in all_input_files():
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, list):
            raise ValueError(f"{path} is not a JSON array")
        for market in payload:
            records += 1
            ticker = str(market.get("ticker", ""))
            if not ticker:
                raise ValueError(f"market without ticker in {path}")
            if ticker in seen_tickers:
                continue
            seen_tickers.add(ticker)
            # Some broad files also contain "between" and "less" contracts
            # under the same event key.  Their YES payoff is not 1[S > K], so
            # including them would make the ladder algebra invalid.
            if market.get("strike_type") not in THRESHOLD_STRIKE_TYPES:
                non_threshold_contracts_skipped += 1
                continue
            if not finite_number(market.get("strike")):
                continue
            events[event_key(market)].append(market)
    return dict(events), records, non_threshold_contracts_skipped


def make_record(
    event: str,
    ts: Any,
    lower: tuple[float, dict[str, Any], dict[str, Any]],
    higher: tuple[float, dict[str, Any], dict[str, Any]],
) -> dict[str, Any]:
    """Create one filled, post-fee profitable YES(K1)+NO(K2) opportunity."""
    lower_strike, lower_market, lower_bar = lower
    higher_strike, higher_market, higher_bar = higher
    yes_k1_ask = decimal_price(lower_bar["ask"])
    yes_k2_bid = decimal_price(higher_bar["bid"])
    no_k2_ask = ONE - yes_k2_bid
    yes_fee = fee_for_entry(yes_k1_ask)
    no_fee = fee_for_entry(no_k2_ask)
    raw_cost = yes_k1_ask + no_k2_ask
    total_cost = raw_cost + yes_fee + no_fee
    return {
        "event": event,
        "timestamp": ts,
        "action": "buy YES at lower strike; buy NO at higher strike",
        "lower": {
            "ticker": lower_market["ticker"],
            "strike": lower_strike,
            "yes_bid": lower_bar["bid"],
            "yes_ask_paid": lower_bar["ask"],
            "yes_ask_depth": depth_from_bar(lower_bar, "ask"),
            "bar_volume_not_depth": lower_bar.get("vol"),
            "bar_open_interest_not_depth": lower_bar.get("oi"),
        },
        "higher": {
            "ticker": higher_market["ticker"],
            "strike": higher_strike,
            "yes_bid_used_to_buy_no": higher_bar["bid"],
            "yes_ask": higher_bar["ask"],
            "yes_bid_depth": depth_from_bar(higher_bar, "bid"),
            "bar_volume_not_depth": higher_bar.get("vol"),
            "bar_open_interest_not_depth": higher_bar.get("oi"),
        },
        "yes_k1_entry_price": serialized_amount(yes_k1_ask),
        "no_k2_entry_price": serialized_amount(no_k2_ask),
        "yes_k1_entry_fee": serialized_amount(yes_fee),
        "no_k2_entry_fee": serialized_amount(no_fee),
        "raw_cost_before_fees": serialized_amount(raw_cost),
        "total_cost_including_fees": serialized_amount(total_cost),
        "guaranteed_minimum_payoff": "1.00",
        "net_profit_per_contract": serialized_amount(ONE - total_cost),
    }


def percentile(sorted_values: list[Decimal], fraction: Decimal) -> Decimal:
    """Nearest-rank percentile, appropriate for discrete per-contract cents."""
    if not sorted_values:
        return Decimal(0)
    index = int((Decimal(len(sorted_values) - 1) * fraction).to_integral_value())
    return sorted_values[index]


def profit_distribution(records: list[dict[str, Any]]) -> str:
    if not records:
        return "no post-fee profitable opportunities"
    profits = sorted(decimal_price(record["net_profit_per_contract"]) for record in records)
    summary = [
        ("min", profits[0]),
        ("p25", percentile(profits, Decimal("0.25"))),
        ("p50", percentile(profits, Decimal("0.50"))),
        ("p75", percentile(profits, Decimal("0.75"))),
        ("p90", percentile(profits, Decimal("0.90"))),
        ("p95", percentile(profits, Decimal("0.95"))),
        ("max", profits[-1]),
    ]
    return ", ".join(f"{label}=${serialized_amount(value)}" for label, value in summary)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_FILE,
        help=f"where to write every post-fee violation (default: {OUTPUT_FILE.name})",
    )
    args = parser.parse_args()

    events, input_records, non_threshold_contracts_skipped = load_events()
    violations: list[dict[str, Any]] = []
    events_with_violation: set[str] = set()
    eligible_timestamps = 0
    malformed_or_out_of_bounds_quotes = 0
    pre_fee_profitable_not_post_fee = 0
    pre_fee_profitable_total = 0
    timestamps_with_candidates = 0
    duplicate_strike_pairs_skipped = 0

    for event, markets in events.items():
        # Each element is (strike, market, bar).  No preceding/following bar is
        # ever used, so comparisons are exactly simultaneous.
        at_timestamp: DefaultDict[Any, list[tuple[float, dict[str, Any], dict[str, Any]]]] = defaultdict(list)
        for market in markets:
            strike = float(market["strike"])
            for bar in market.get("bars", []):
                if quote_is_valid(bar):
                    at_timestamp[bar.get("ts")].append((strike, market, bar))
                elif "bid" in bar or "ask" in bar:
                    malformed_or_out_of_bounds_quotes += 1

        for ts, ladder in at_timestamp.items():
            if len(ladder) < 4:
                continue
            eligible_timestamps += 1
            ladder.sort(key=lambda row: row[0])

            # Search every K1 < K2 pair.  Looking only at adjacent strikes can
            # miss an executable inversion over an intermediate wide spread.
            found_pre_fee_candidate_here = False
            for lower_index, lower in enumerate(ladder[:-1]):
                lower_ask = decimal_price(lower[2]["ask"])
                for higher in ladder[lower_index + 1 :]:
                    if lower[0] == higher[0]:
                        duplicate_strike_pairs_skipped += 1
                        continue
                    higher_bid = decimal_price(higher[2]["bid"])
                    # Equivalently: ask(YES K1) + ask(NO K2) < $1.
                    if lower_ask >= higher_bid:
                        continue
                    found_pre_fee_candidate_here = True
                    pre_fee_profitable_total += 1
                    no_k2_ask = ONE - higher_bid
                    raw_cost = lower_ask + no_k2_ask
                    total_cost = raw_cost + fee_for_entry(lower_ask) + fee_for_entry(no_k2_ask)
                    if total_cost >= ONE:
                        pre_fee_profitable_not_post_fee += 1
                        continue
                    record = make_record(event, ts, lower, higher)
                    violations.append(record)
                    events_with_violation.add(event)
            if found_pre_fee_candidate_here:
                timestamps_with_candidates += 1

    violations.sort(key=lambda row: decimal_price(row["net_profit_per_contract"]), reverse=True)
    output = {
        "method": {
            "same_timestamp_only": True,
            "minimum_quoted_strikes": 4,
            "contracts_per_pair": 1,
            "fee": "ceil(0.07 * contracts * price * (1-price) * 100) / 100 per leg",
            "payoff": "YES(K1)+NO(K2), K1<K2: minimum payout is $1.00",
        },
        "summary": {
            "input_market_records": input_records,
            "non_threshold_contracts_skipped": non_threshold_contracts_skipped,
            "events_loaded": len(events),
            "eligible_event_timestamps": eligible_timestamps,
            "events_with_post_fee_violation": len(events_with_violation),
            "post_fee_violations": len(violations),
            "pre_fee_profitable_pairs": pre_fee_profitable_total,
            "profitable_before_fees_but_not_after": pre_fee_profitable_not_post_fee,
            "malformed_or_out_of_bounds_quotes_rejected": malformed_or_out_of_bounds_quotes,
            "duplicate_strike_pairs_skipped": duplicate_strike_pairs_skipped,
        },
        "violations": violations,
    }
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(output, handle, indent=2)
        handle.write("\n")

    print(f"Loaded {input_records:,} market records into {len(events):,} events.")
    print(f"Skipped non-threshold contracts (not YES=1[settle > K]): {non_threshold_contracts_skipped:,}")
    print(f"Eligible same-timestamp ladders (>=4 valid bid/ask strikes): {eligible_timestamps:,}")
    print(f"Events with any post-fee violation: {len(events_with_violation):,}")
    print(f"Post-fee executable violations: {len(violations):,}")
    print(f"Profitable before fees but not after: {pre_fee_profitable_not_post_fee:,}")
    print(f"Rejected malformed/out-of-bounds books: {malformed_or_out_of_bounds_quotes:,}")
    print(f"Net profit per contract distribution: {profit_distribution(violations)}")
    print(f"All post-fee violations written to: {args.output.resolve()}")
    print("Top 20 by net profit per contract:")
    if not violations:
        print("  (none)")
    for rank, record in enumerate(violations[:20], start=1):
        print(
            f"  {rank:2d}. ${record['net_profit_per_contract']} | {record['event']} | "
            f"ts={record['timestamp']} | YES {record['lower']['ticker']} "
            f"(K={record['lower']['strike']}, ask={record['yes_k1_entry_price']}) + "
            f"NO {record['higher']['ticker']} "
            f"(K={record['higher']['strike']}, ask={record['no_k2_entry_price']}) | "
            f"cost=${record['total_cost_including_fees']}"
        )


if __name__ == "__main__":
    main()
