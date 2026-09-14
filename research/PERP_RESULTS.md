# Kalshi perpetuals — trend/breakout results (run 2026-09-03)

`perp_search.py` was written 2026-09-01 but never run to a recorded result. This is that
result, plus the buy-and-hold benchmark it was missing.

**Question asked:** how profitable would an ORB-15-style bot be on Kalshi perpetuals?

**Answer:** it loses to doing nothing. The breakout family — the actual ORB analogue — was
the weakest group tested, and the best strategy overall underperformed buy-and-hold by 6
percentage points a year out of sample.

---

## 1. An intraday ORB port is dead before you test it

ORB-15's premise is the *opening range*: the first 15 minutes after 09:30 ET, when overnight
information reprices on a volume surge. Perpetuals trade 24/7. There is no open, so there is
no opening range. The nearest honest analogue is a Donchian breakout on a slow horizon,
which is what `sig_breakout` implements.

A *fast* ORB-style rule fails on arithmetic alone. Taker fee is 0.120% per side, so 0.24%
per round trip:

| round trips/day | annual fee drag |
|---|---:|
| 1 | ~60% |
| 2 | ~120% |
| 4 | ~240% |

Against an asset with roughly 60% annualised volatility, no intraday signal survives that.
Any perpetuals strategy here has to be slow, which makes it a different strategy from ORB-15
rather than a port of it.

## 2. What was tested

14 coins, daily bars, 2016→2026. Chronological split at **2023-06-20** — everything before
is train, everything after is held out and touched once. 14 strategies searched, portfolio
equal-weighted, taker fees charged on every position change, funding charged on every held
bar.

**Train (in-sample), the breakout family specifically:**

| strategy | CAGR | Sharpe | maxDD |
|---|---:|---:|---:|
| breakout20 | 2.8% | 0.35 | 12.8% |
| breakout55 | 3.0% | 0.39 | 14.5% |
| breakout100 | **−1.6%** | −0.26 | 20.6% |

The breakouts were among the worst of the 14 even *in-sample*, and none was selected. The
search picked **ma10/50** (train Sharpe 0.92, maxDD 7.8%).

## 3. Held out — the number that matters

| | CAGR | Sharpe | maxDD | coins profitable |
|---|---:|---:|---:|---:|
| ma10/50 trend portfolio | **+8.2%** | 0.41 | **43.0%** | **5 of 14** |
| **equal-weight buy-and-hold** | **+14.3%** | — | — | **10 of 14** |

Train Sharpe 0.92 → test Sharpe 0.41. Train maxDD 7.8% → test maxDD 43.0%. Per-coin
held-out drawdowns ran 61–93%.

`perp_search.py` states its own verdict rule: *"a real edge needs positive held-out Sharpe, a
majority of coins profitable, and a drawdown you could actually sit through."* It fails two
of three.

**And it is value-destroying, not merely weak.** Over the identical window, doing nothing
returned +14.3%. The strategy turned that into +8.2% by trading 25–38 times per coin and
paying fees and funding to get whipsawed. The 6-point gap is the cost of the activity.

## 4. Is the reduced drawdown worth it?

Trend's 43% portfolio drawdown does beat buy-and-hold's per-coin 53–93%. But you do not need
a trend system to reduce drawdown — you can just hold less. Roughly 60% BTC / 40% cash over
the same window gives ~+22% CAGR at ~32% drawdown, which beats the trend portfolio on
**both** axes without trading at all.

## 5. Caveats, all pointing the same way

- Modelled on Coinbase spot daily bars. Kalshi perpetuals are far thinner, so real spread and
  slippage would be **worse** than modelled, not better.
- Fees modelled at taker 0.120% / funding 0.0001 per 8h. Verify against Kalshi's live
  schedule before trusting the magnitude.
- The held-out window (2023-06→2026-09) was a crypto bull period. A long-biased trend system
  had the wind behind it and still lost to holding.

## 6. Conclusion

Do not build this. The binary-market research in this folder searched 298 combinations to a
corrected p of 1.0; the perpetuals search adds a 15th negative result on a different
instrument. The framework was correctly built and correctly disciplined — it did its job,
which was to say no cheaply.

If perpetuals get revisited, the only structurally interesting angle is the one
`perp_search.py`'s docstring already names: a portfolio of weakly-correlated coins is the
managed-futures playbook and is the one thing binaries could never do. But that thesis just
failed its own held-out test, and it needs a *new* mechanism rather than a re-tuned one.
