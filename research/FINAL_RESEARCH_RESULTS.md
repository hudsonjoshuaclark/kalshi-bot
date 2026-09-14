# Final Kalshi strategy recommendation

## What to trade

Trade **Robust RV estimator** at **1 contract per qualifying market**. Keep at least **$500** bankroll, allow at most five simultaneous one-contract signals across the five coins, and never average down.

For each BTC, ETH, SOL, XRP, or DOGE 15-minute market, wait until minute 9 after open. Use the Kalshi bid/ask candle ending at that instant and the Coinbase candle keyed one minute earlier (which closes at the same instant). Let mid=(bid+ask)/2, z=Phi^-1(mid), and tau_eff=seconds to settlement minus 40. Compute implied absolute volatility as abs(spot-strike)/(abs(z)*sqrt(tau_eff)). Compute realised volatility as current spot times the sample standard deviation of the preceding 120 one-minute log returns, converted from per-minute to per-sqrt-second. Trade only when abs(z)>=0.15 and implied/realised>=2.0. If spot is above strike, buy YES at the displayed ask; otherwise buy NO at 1-bid. Hold to settlement.

This is the strongest candidate, not a claim of proven alpha. It earned **+$7.148 on 200 held-out trades (3.574 cents/trade)** with raw market-null p=0.02530, four of five coins profitable, all three held-out weeks profitable, and 28.5% of P&L from the best five trades. After all 298 searched combinations, corrected p=1.0. It also failed the drift check: underlying-down P&L was +$15.555 while underlying-up P&L was -$8.407. That is why size stays at one contract.

Do not increase size until 200 additional forward trades remain net profitable **and** both underlying-up and underlying-down blocks are nonnegative.

## Consolidated results

| strategy | train | test | corrected p | verdict |
|---|---:|---:|---:|---|
| Corrected original IV/RV | +20.510 (+0.0443/trade, n=463) | -8.530 (-0.0410/trade, n=208) | 1.00000 | FAIL |
| Robust RV estimator | +4.882 (+0.0187/trade, n=261) | +7.148 (+0.0357/trade, n=200) | 1.00000 | BEST AVAILABLE - TRADE SMALL |
| Cost-filtered IV/RV | +20.290 (+0.0528/trade, n=384) | -8.700 (-0.0506/trade, n=172) | 1.00000 | FAIL |
| Two-touch lock-in | -184.295 (-0.0332/trade, n=5550) | not opened | n/a | REJECTED ON TRAIN; TEST NOT OPENED |
| Quote impulse | -6.164 (-0.0056/trade, n=1092) | not opened | n/a | REJECTED ON TRAIN; TEST NOT OPENED |
| Spot-shock underreaction | +2.618 (+0.0198/trade, n=132) | +0.515 (+0.0101/trade, n=51) | 1.00000 | RUNNER-UP; UNPROVEN |
| Filtered spot-shock | +0.606 (+0.0082/trade, n=74) | -0.064 (-0.0030/trade, n=21) | 1.00000 | FAIL |
| Dynamic ladder lock-in | -2.280 (-0.0104/trade, n=219) | not opened | n/a | REJECTED ON TRAIN; TEST NOT OPENED |
| Frozen spot-shock on metals | +0.080 (+0.0400/trade, n=2) | no usable signals | n/a | INCONCLUSIVE; NO TEST SIGNALS |

## Why this rule, not the positive spot-shock runner-up?

The spot-shock rule made only +$0.515 on 51 held-out trades, raw p=0.366, with 256.5% of net profit coming from its five best trades; its NO side lost money and only BTC contributed meaningful profit. Robust RV has the larger sample, stronger market-null result, higher profit per trade, broader series/week support, and much lower concentration.

## Files

- `VOLATILITY_RESULTS.md`: corrected volatility study and full diagnostics.
- `MICROSTRUCTURE_RESULTS.md`: two-touch, quote-impulse, and spot-shock studies.
- `s16_dynamic_ladder_train.json`: dynamic ladder train rejection.
- `metal_spot_shock_results.json`: frozen metal transportability check.
- `final_recommendation.json`: machine-readable recommendation and audit totals.
