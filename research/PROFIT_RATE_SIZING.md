# Robust RV profit-rate sizing

Every row replays the identical frozen signals; size is not reselected from outcomes. Only one contract is supported by the historical quote. Larger scenarios assume sufficient depth at the recorded top ask and therefore are not yet executable evidence.

| contracts/signal | train P&L/day | test P&L/day | test total | test max DD | max block exposure | % of $500 | capacity |
|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | +0.111 | +0.396 | +7.15 | 1.92 | 5.00 | 1.00% | one-contract displayed quote |
| 2 | +0.257 | +0.853 | +15.40 | 3.74 | 9.94 | 1.99% | UNVERIFIED: assumes every contract fills at recorded top ask |
| 5 | +0.713 | +2.249 | +40.58 | 9.17 | 24.78 | 4.96% | UNVERIFIED: assumes every contract fills at recorded top ask |
| 10 | +1.462 | +4.557 | +82.22 | 18.32 | 49.52 | 9.90% | UNVERIFIED: assumes every contract fills at recorded top ask |
| 20 | +2.947 | +9.171 | +165.46 | 36.63 | 99.01 | 19.80% | UNVERIFIED: assumes every contract fills at recorded top ask |

Recommended now: **one contract per signal in isolated paper mode**. After the promotion gate, use one contract live. Two contracts is the fastest bounded scenario at a $500 bankroll, but only after real depth and fill logs show it remains executable.
