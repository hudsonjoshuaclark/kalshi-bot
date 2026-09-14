# Robust RV automation

## Start safely

```powershell
cd C:\Users\hudso\kalshi-bot
npm run paper:robust-rv
```

This launches `config.robust-rv.paper.json` against a separate
`data/robust-rv-paper.db`. It cannot use the credentials in `config.json`, its
mode is fixed to `paper`, and its maximum size is one contract.

Check the forward promotion gate with:

```powershell
npm run status:robust-rv
```

## Frozen signal

At six minutes to settlement (minute 9 after open), the engine:

1. Reads a fresh, two-sided Kalshi book and synchronized composite spot.
2. Uses only completed Coinbase candles and computes the sample standard
   deviation of the preceding 120 one-minute log returns.
3. Inverts the Kalshi mid into absolute-price implied volatility with
   `tau_eff = seconds_left - 40`.
4. Requires `abs(Phi^-1(mid)) >= 0.15` and `IV/RV >= 2.0`.
5. Buys YES when spot is above strike, otherwise NO, at the displayed ask.
6. Places at most one position per market and holds it to settlement.

The LLM is not in this loop. Claude or Codex may run tests, review logs, and
summarize the promotion audit. They must never choose a side, relax a risk cap,
or switch the bot to real-money mode.

## Promotion gate

The supervisor requires at least 200 new settled paper trades, positive total
and both chronological halves, positive YES and NO sides, at least four of five
profitable series, and less than half of P&L from the best five trades. It still
prints `livePromotionApproved: false`; a separate underlying-up/down audit is
required because the historical strategy failed that drift check.

No script automatically promotes to live or increases size. That is
intentional: the held-out edge was nominally significant before search
correction, but not after the full correction.
