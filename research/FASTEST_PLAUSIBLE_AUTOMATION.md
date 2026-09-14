# Fastest plausible automated Kalshi strategy

## Decision

Run the **Robust RV estimator** in isolated paper automation now at **one contract per signal**. It is the fastest candidate supported by the local evidence: +$7.148 on 200 held-out trades, +$0.396 per elapsed day per contract, raw market-null p=0.02530, four of five coins profitable, and all three held-out weeks profitable.

```powershell
cd C:\Users\hudso\kalshi-bot; npm run paper:robust-rv
```

Audit forward progress with:

```powershell
cd C:\Users\hudso\kalshi-bot; npm run status:robust-rv
```

The engine cannot place real orders: it reads a separate paper config with no API credentials, uses a separate database, and hard-caps every signal at one contract.

## Exact signal

At minute 9 of each BTC/ETH/SOL/XRP/DOGE 15-minute market, compute the 120-minute sample standard deviation of completed Coinbase log returns. Invert the synchronized Kalshi mid into absolute implied volatility using `tau_eff = seconds_left - 40`. Require `abs(Phi^-1(mid)) >= 0.15` and `IV/RV >= 2`. Buy YES at ask if spot is above strike; otherwise buy NO at `1-bid`; hold to settlement.

## How fast it can plausibly earn

| contracts/signal | held-out P&L/day | held-out total | max drawdown | max simultaneous exposure | evidence |
|---:|---:|---:|---:|---:|---|
| 1 | +0.396 | +7.15 | 1.92 | 5.00 | displayed one-contract quote |
| 2 | +0.853 | +15.40 | 3.74 | 9.94 | capacity unverified |
| 5 | +2.249 | +40.58 | 9.17 | 24.78 | capacity unverified |
| 10 | +4.557 | +82.22 | 18.32 | 49.52 | capacity unverified |
| 20 | +9.171 | +165.46 | 36.63 | 99.01 | capacity unverified |

Two contracts would have produced **+$0.853/day** with at most **$9.94** simultaneous historical exposure, about 2% of a $500 bankroll. Do not use that size until forward logs prove the second contract fills at the quoted price. Larger rows are scaling scenarios, not evidence.

## New fast/trendy-market backtests

| strategy | train | test | corrected p | verdict |
|---|---:|---:|---:|---|
| Robust RV | +$4.882 (n=261) | +$7.148 (n=200) | 1.00000 | fastest plausible |
| Outcome streak | -$91.803 (n=3765) | not opened | n/a | REJECTED ON TRAIN; TEST NOT OPENED |
| Volume impulse | -$17.766 (n=2053) | not opened | n/a | REJECTED ON TRAIN; TEST NOT OPENED |
| GPU attention momentum | +$2.690 (n=14) | +$0.740 (n=12) | 1.00000 | POSITIVE BUT UNPROVEN |

The GPU niche is the runner-up: +$2.69 train and +$0.74 held out, with both YES and NO profitable. It is too slow and too concentrated for primary deployment: only 12 held-out events, raw p=0.392, and the five best trades equal 203% of net P&L.

## Honesty boundary

No strategy is globally significant after all **442** searched combinations; Robust RV's corrected p is 1.0 and it historically failed the underlying-up drift block. This is why the automation starts in paper, why an LLM cannot change orders, and why live size remains one contract even after promotion. The 200-trade supervisor gate must pass before any real-money use.

## Automation files

- `../src/strategies/robustRv.js` - pure frozen signal.
- `../config.robust-rv.paper.json` - credential-free one-contract configuration.
- `../bin/robust-rv-paper.js` - isolated launcher.
- `../bin/robust-rv-status.js` - deterministic promotion audit.
- `../AUTOMATION_ROBUST_RV.md` - operating instructions.
- `PROFIT_RATE_SIZING.md` - fee-aware size scenarios.
- `FAST_MARKETS_RESULTS.md` - trendy-market diagnostics.
