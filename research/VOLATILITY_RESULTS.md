# Implied-versus-realised volatility: final held-out results

The chronological cut and every rule were frozen before these test outcomes were scored. The study searched 114 parameter combinations in total (24 original + 72 robust-estimator + 18 cost-filter combinations). Each reported p-value uses 20,000 redraws from the price actually paid and is Bonferroni-corrected by 114.

The old ~90x claim was a units error: absolute-price IV had been divided by relative-return RV. Here RV is multiplied by contemporaneous spot, and Coinbase candle key `t-60` is aligned with the Kalshi candle ending at `t`.

| strategy | train | test | corrected p | verdict |
|---|---:|---:|---:|---|
| Corrected original IV/RV | +20.51 (+0.0443/trade, n=463) | -8.53 (-0.0410/trade, n=208) | 1.00000 | FAIL |
| Robust RV estimator | +4.88 (+0.0187/trade, n=261) | +7.15 (+0.0357/trade, n=200) | 1.00000 | FAIL |
| Cost-filtered IV/RV | +20.29 (+0.0528/trade, n=384) | -8.70 (-0.0506/trade, n=172) | 1.00000 | FAIL |

A PASS requires positive held-out net P&L, corrected p < 0.05, and, in both train and test, positive P&L in underlying-up and underlying-down blocks, at least 3 profitable series, at least half the weeks profitable, and less than half of profit from the five best trades.

## Corrected IV/RV relationship

On the held-out data at the train-selected baseline entry, median IV/RV is 1.022 and mean IV/RV is 1.232; IV exceeds RV in 52.0% of usable markets.

| series | n | median IV/RV | mean IV/RV | IV > RV |
|---|---:|---:|---:|---:|
| BTC | 1699 | 1.095 | 1.343 | 59.1% |
| ETH | 1656 | 0.962 | 1.222 | 46.6% |
| SOL | 1666 | 1.005 | 1.170 | 50.6% |
| XRP | 1586 | 1.019 | 1.239 | 51.8% |
| DOGE | 621 | 1.015 | 1.102 | 51.7% |

## Corrected original IV/RV

Rule: `entry=5|estimator=std30|threshold=3.0|regime=iv_high|min_abs_z=0.0|min_paid=0.0|max_paid=1.0|max_spread=1.0`.

Raw market-null p 0.74776; corrected p 1.00000. Five best trades: +2.35 of total -8.53.
Close-time cluster-robust 95% CI on net P&L/trade: -0.1119 to +0.0298 (t=-1.13 across 177 close-time clusters).

### Train series

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| BTC | 134 | +3.44 | +0.0257 |
| DOGE | 30 | +1.20 | +0.0400 |
| ETH | 96 | +6.33 | +0.0659 |
| SOL | 91 | +8.63 | +0.0948 |
| XRP | 112 | +0.91 | +0.0081 |

### Train ISO weeks

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| 2026-W27 | 63 | +0.29 | +0.0046 |
| 2026-W28 | 58 | +4.04 | +0.0697 |
| 2026-W29 | 73 | +2.46 | +0.0337 |
| 2026-W30 | 69 | -0.19 | -0.0028 |
| 2026-W31 | 69 | +3.06 | +0.0443 |
| 2026-W32 | 99 | +9.77 | +0.0987 |
| 2026-W33 | 32 | +1.08 | +0.0337 |

### Held-out series

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| BTC | 65 | +2.05 | +0.0315 |
| DOGE | 9 | -0.89 | -0.0989 |
| ETH | 53 | -1.18 | -0.0223 |
| SOL | 36 | -4.41 | -0.1225 |
| XRP | 45 | -4.10 | -0.0911 |

### Held-out ISO weeks

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| 2026-W33 | 111 | -1.51 | -0.0136 |
| 2026-W34 | 47 | -0.87 | -0.0185 |
| 2026-W35 | 47 | -5.56 | -0.1183 |
| 2026-W36 | 3 | -0.59 | -0.1967 |

### Drift check

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| down_or_flat | 108 | +29.73 | +0.2753 |
| up | 100 | -38.26 | -0.3826 |

### Purchased side

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| NO | 186 | -8.55 | -0.0460 |
| YES | 22 | +0.02 | +0.0009 |

## Robust RV estimator

Rule: `entry=9|estimator=std120|threshold=2.0|regime=iv_high|min_abs_z=0.15|min_paid=0.0|max_paid=1.0|max_spread=1.0`.

Raw market-null p 0.02530; corrected p 1.00000. Five best trades: +2.04 of total +7.15 (28.5%).
Close-time cluster-robust 95% CI on net P&L/trade: -0.0086 to +0.0801 (t=+1.58 across 152 close-time clusters).

### Train series

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| BTC | 79 | +2.22 | +0.0281 |
| DOGE | 1 | +0.39 | +0.3900 |
| ETH | 55 | +1.33 | +0.0242 |
| SOL | 55 | -0.62 | -0.0113 |
| XRP | 71 | +1.56 | +0.0220 |

### Train ISO weeks

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| 2026-W27 | 26 | +2.09 | +0.0804 |
| 2026-W28 | 47 | +2.23 | +0.0475 |
| 2026-W29 | 37 | -0.75 | -0.0204 |
| 2026-W30 | 41 | +0.71 | +0.0173 |
| 2026-W31 | 43 | +0.89 | +0.0208 |
| 2026-W32 | 44 | +1.49 | +0.0340 |
| 2026-W33 | 23 | -1.79 | -0.0777 |

### Held-out series

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| BTC | 58 | +1.58 | +0.0273 |
| DOGE | 10 | -0.52 | -0.0520 |
| ETH | 56 | +1.62 | +0.0289 |
| SOL | 38 | +2.17 | +0.0572 |
| XRP | 38 | +2.29 | +0.0604 |

### Held-out ISO weeks

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| 2026-W33 | 83 | +3.07 | +0.0370 |
| 2026-W34 | 68 | +3.00 | +0.0441 |
| 2026-W35 | 49 | +1.07 | +0.0219 |

### Drift check

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| down_or_flat | 101 | +15.55 | +0.1540 |
| up | 99 | -8.41 | -0.0849 |

### Purchased side

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| NO | 143 | +5.24 | +0.0366 |
| YES | 57 | +1.91 | +0.0335 |

## Cost-filtered IV/RV

Rule: `entry=5|estimator=std30|threshold=3.0|regime=iv_high|min_abs_z=0.0|min_paid=0.0|max_paid=1.0|max_spread=0.02`.

Raw market-null p 0.81311; corrected p 1.00000. Five best trades: +2.35 of total -8.70.
Close-time cluster-robust 95% CI on net P&L/trade: -0.1272 to +0.0260 (t=-1.29 across 154 close-time clusters).

### Train series

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| BTC | 134 | +3.44 | +0.0257 |
| DOGE | 17 | +3.58 | +0.2106 |
| ETH | 84 | +7.05 | +0.0839 |
| SOL | 73 | +8.91 | +0.1221 |
| XRP | 76 | -2.69 | -0.0354 |

### Train ISO weeks

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| 2026-W27 | 56 | -0.63 | -0.0113 |
| 2026-W28 | 46 | +3.74 | +0.0813 |
| 2026-W29 | 60 | +3.81 | +0.0635 |
| 2026-W30 | 59 | +0.41 | +0.0069 |
| 2026-W31 | 59 | +4.75 | +0.0805 |
| 2026-W32 | 81 | +6.98 | +0.0862 |
| 2026-W33 | 23 | +1.23 | +0.0535 |

### Held-out series

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| BTC | 65 | +2.05 | +0.0315 |
| DOGE | 6 | -0.23 | -0.0383 |
| ETH | 48 | -1.31 | -0.0273 |
| SOL | 25 | -5.90 | -0.2360 |
| XRP | 28 | -3.31 | -0.1182 |

### Held-out ISO weeks

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| 2026-W33 | 85 | -2.46 | -0.0289 |
| 2026-W34 | 39 | -0.18 | -0.0046 |
| 2026-W35 | 45 | -5.47 | -0.1216 |
| 2026-W36 | 3 | -0.59 | -0.1967 |

### Drift check

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| down_or_flat | 89 | +23.70 | +0.2663 |
| up | 83 | -32.40 | -0.3904 |

### Purchased side

| block | n | P&L | P&L/trade |
|---|---:|---:|---:|
| NO | 152 | -8.84 | -0.0582 |
| YES | 20 | +0.14 | +0.0070 |

## Recommendation

Trade **none of these strategies**; size is **0 contracts**. A new untouched sample is required before reconsidering them.
