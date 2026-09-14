# Fast and trendy Kalshi markets: held-out results

The 144 new rules were selected on each series' oldest 70% only. Outcome-streak and volume-impulse families lost on train and their holdouts stayed closed. The GPU winner was frozen before the newest 15 GPU-series events were evaluated. The global correction factor is 442.

| strategy | train | test | corrected p | verdict |
|---|---:|---:|---:|---|
| Outcome streak | -91.803 (-0.0244/trade, n=3765) | not opened | n/a | REJECTED ON TRAIN; TEST NOT OPENED |
| Volume impulse | -17.766 (-0.0087/trade, n=2053) | not opened | n/a | REJECTED ON TRAIN; TEST NOT OPENED |
| GPU attention momentum | +2.690 (+0.1921/trade, n=14) | +0.740 (+0.0617/trade, n=12) | 1.00000 | POSITIVE BUT UNPROVEN |

## GPU attention momentum

Rule: 60% through each GPU weekly event, examine each strike's move over its preceding three observed bars. Among contracts moving at least 3 cents with positive volume/OI activity, select the single most active contract and buy the move direction at the displayed ask; hold to settlement.

Raw market-null p=0.39223; corrected p=1.00000. P&L rate=+0.0529/day over 14.0 elapsed test days. Five best trades contributed +1.500 (202.7% of net).

### GPU series

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| KXA100WS | 1 | +0.040 | +0.0400 |
| KXB200WS | 3 | -0.180 | -0.0600 |
| KXH100WS | 3 | +0.480 | +0.1600 |
| KXH200WS | 3 | -0.060 | -0.0200 |
| KXRTX5090WS | 2 | +0.460 | +0.2300 |

### Settlement weeks

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| 2026-W32 | 4 | +0.410 | +0.1025 |
| 2026-W33 | 4 | +0.570 | +0.1425 |
| 2026-W34 | 4 | -0.240 | -0.0600 |

### Side

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| NO | 7 | +0.340 | +0.0486 |
| YES | 5 | +0.400 | +0.0800 |

## Speed comparison

Robust RV: +0.396 dollars/day and 11.1 trades/day in its held-out span. GPU attention: +0.053 dollars/day and 0.86 events/day in its held-out span.
