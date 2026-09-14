# Cost-aware microstructure search: final results

The search used the oldest 70% for all selection and opened the newest 30% once. It searched 148 new combinations; the reported correction factor is 262 after adding the 114 earlier volatility combinations. Every entry crosses the contemporaneous displayed ask, every leg pays the entry fee, and each market contributes at most one observation.

| strategy | train | test | corrected p | verdict |
|---|---:|---:|---:|---|
| Two-touch lock-in | -184.29 (-0.0332/trade, n=5550) | not opened | n/a | REJECTED ON TRAIN; TEST NOT OPENED |
| Quote impulse | -6.16 (-0.0056/trade, n=1092) | not opened | n/a | REJECTED ON TRAIN; TEST NOT OPENED |
| Spot-shock underreaction | +2.62 (+0.0198/trade, n=132) | +0.52 (+0.0101/trade, n=51) | 1.00000 | POSITIVE BUT UNPROVEN |
| Filtered spot-shock | +0.61 (+0.0082/trade, n=74) | -0.06 (-0.0030/trade, n=21) | 1.00000 | FAIL |

## Spot-shock underreaction

At minute 8, compute the last completed Coinbase 1-minute return divided by the sample standard deviation of the preceding 30 returns. If |z| >= 3.0 and Kalshi's signed mid response from the preceding minute is <= 0.15, buy YES after a positive shock or NO after a negative shock at the displayed ask; hold to settlement.

Raw market-null p=0.36598; Bonferroni-corrected p=1.00000. Close-time cluster 95% CI per trade: -0.0292 to +0.0493. Five best trades contributed +1.321 (256.5% of net).

### Series

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| BTC | 16 | +0.809 | +0.0506 |
| DOGE | 1 | +0.001 | +0.0010 |
| ETH | 11 | -0.081 | -0.0074 |
| SOL | 13 | -0.213 | -0.0164 |
| XRP | 10 | -0.001 | -0.0001 |

### ISO weeks

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| 2026-W33 | 11 | +0.633 | +0.0575 |
| 2026-W34 | 14 | +0.481 | +0.0344 |
| 2026-W35 | 24 | -0.395 | -0.0165 |
| 2026-W36 | 2 | -0.204 | -0.1020 |

### Underlying drift

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| down_or_flat | 26 | -0.059 | -0.0023 |
| up | 25 | +0.574 | +0.0230 |

### Purchased side

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| NO | 24 | -0.699 | -0.0291 |
| YES | 27 | +1.214 | +0.0450 |

## Filtered spot-shock

At minute 8, compute the last completed Coinbase 1-minute return divided by the sample standard deviation of the preceding 30 returns. If |z| >= 3.5 and Kalshi's signed mid response from the preceding minute is <= 0.15, buy the shock direction at the displayed ask only if price <= 1.00; hold to settlement.

Raw market-null p=0.69487; Bonferroni-corrected p=1.00000. Close-time cluster 95% CI per trade: -0.0306 to +0.0245. Five best trades contributed +0.185.

### Series

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| BTC | 8 | +0.006 | +0.0008 |
| DOGE | 1 | +0.001 | +0.0010 |
| ETH | 2 | +0.053 | +0.0265 |
| SOL | 6 | +0.011 | +0.0018 |
| XRP | 4 | -0.135 | -0.0337 |

### ISO weeks

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| 2026-W33 | 3 | +0.046 | +0.0153 |
| 2026-W34 | 4 | +0.081 | +0.0203 |
| 2026-W35 | 14 | -0.191 | -0.0136 |

### Underlying drift

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| down_or_flat | 8 | -0.330 | -0.0412 |
| up | 13 | +0.266 | +0.0205 |

### Purchased side

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| NO | 10 | -0.036 | -0.0036 |
| YES | 11 | -0.028 | -0.0025 |

## What to trade

**Spot-shock underreaction — BEST AVAILABLE; EXPERIMENTAL, NOT MULTIPLICITY-SAFE.**

At minute 8, compute the last completed Coinbase 1-minute return divided by the sample standard deviation of the preceding 30 returns. If |z| >= 3.0 and Kalshi's signed mid response from the preceding minute is <= 0.15, buy YES after a positive shock or NO after a negative shock at the displayed ask; hold to settlement.

Size: **1 contract per qualifying market**, only with at least $500 before live use bankroll. maximum 1 contract per market and 5 contracts across a simultaneous five-coin block; never average down. do not increase size until 200 additional timestamped forward trades remain net profitable.
