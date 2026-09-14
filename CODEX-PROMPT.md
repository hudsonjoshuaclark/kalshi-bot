# Prompt for Codex (or any research agent)

Copy everything below the line. It is self-contained.

---

You are doing quantitative research to find a profitable trading strategy on
**Kalshi**, a US prediction-market exchange where every contract is a binary
that settles at $1 or $0.

**The goal:** find a strategy with a real, demonstrable edge — one that survives
out-of-sample testing — and make it as profitable as possible. Keep iterating
until you either find one or can prove none of the candidates works. A rigorous
negative is a valid result; a fake positive is worse than nothing, because it
gets traded with real money.

## Constraints

- **You have NO web access.** Do not attempt network calls. Everything you need
  is already on disk.
- Python 3, standard library only (no pandas/numpy). Run scripts with `python x.py`.
- Working directory: `C:\Users\hudso\kalshi-bot\research`

## The data on disk

| folder | contents |
|---|---|
| `deep/` | ~9 weeks of 1-minute candles for 15-min crypto markets (BTC/ETH/SOL/XRP/DOGE) **plus** matching 1-minute Coinbase spot in `spot_*.json` |
| `tape2/` | 8 series x 400 settled 15-min markets + 1-min spot + Pyth gold/silver |
| `broad/` | 33 series, 8,115 settled markets (15-min to weekly, crypto + commodities) |
| `ladders/` | 14 series, 16,152 markets grouped into strike ladders |
| `weather/` | 14 city temperature ladders, 5,712 markets, 952 event-days |

Market record shape:
```json
{"ticker","series","event","open_ts","close_ts","strike","cap","strike_type",
 "result":"yes"|"no","volume","bars":[{"ts","bid","ask","vol","oi"}]}
```
`bid`/`ask` are the **YES price in dollars** (0–1). Spot files are
`{unix_minute: {"open","high","low","close","vol"}}`.

**Fee** (charged per leg on ENTRY, not on settlement):
```
fee_dollars = ceil(0.07 * contracts * price * (1 - price) * 100) / 100
```
It is smallest near 0 and 1, largest at 0.50 (1.75c/contract).

## What has already been tested and is DEAD — do not redo these

| strategy | how it died |
|---|---|
| Spot-vs-strike fair-value model | Brier score WORSE than the market at every horizon |
| Buying underpriced favourites | 0 of 5 rules survived Bonferroni correction |
| Opening-range breakout, at-the-money | 58% hit in train → 47.7% in test |
| Static ladder arbitrage | 0 executable violations after fees (257 before fees) |
| Passive market making | −$0.11 to −$0.16 per fill, **every** series, both weeks |
| Cross-coin disagreement | the other coins forecast worse than the coin's own price |
| Time-of-day effects | +$10 on 59 train trades → −$11 on 152 test trades |
| Weather / temperature ladders | market well calibrated; ~8% overround absorbs everything |

**The pattern across all of them: Kalshi is inefficient GROSS and efficient NET.**
Real anomalies exist — the favourite-longshot bias is significant at t = −15 —
but each one is almost exactly the size of the bid-ask spread plus the fee. Any
new idea must explain how it beats transaction costs, not just how it finds a
mispricing.

## The one live candidate: implied vs realised volatility

Kalshi's 15-minute crypto contracts imply a volatility. Invert the pricing:
```
P(yes) = Phi( (spot - strike) / (sigma * sqrt(tau_eff)) ),  tau_eff = tau - 40 seconds
```
Compare that implied sigma against realised volatility computed from the
1-minute spot files. Finding so far: **implied runs ~90x trailing realised** —
the market massively overprices uncertainty on these contracts.

Trading the gap gave +$0.0349/trade out-of-sample, raw p = 0.0074 — but it
failed a x24 Bonferroni correction (p = 0.178) and had only ONE week of held-out
data. `deep/` now holds ~9 weeks. **Re-test it properly.** Then try to improve
it: better volatility estimator, better entry timing, better sizing, better
filters. Make it as profitable as it honestly can be.

## Methodology — non-negotiable, this is where every previous idea died

1. **Split by TIME.** Oldest 70% train, newest 30% test. Choose every parameter
   on train only. Touch the test set ONCE, at the end.
2. **One observation per market.** Bars inside a window are the same bet
   measured repeatedly; treating them as independent inflates every t-statistic.
3. **Market null.** Redraw each outcome from the price actually paid, 20,000
   times, and report p(null >= ours). Beating a coin weighted by the market's
   own price is the bar — not beating zero.
4. **Bonferroni-correct** by the number of parameter combinations searched, and
   state how many you searched.
5. **Report per-series and per-time-block.** An edge in one series or one week
   is not an edge.
6. **Drift check.** If the strategy is directional, show it works when the
   underlying rose AND when it fell. Otherwise you have measured the market
   going up, not skill.
7. **Concentration check.** Report how much of the P&L comes from the five best
   trades.

## Traps already paid for in this project

- A backtest showed **+50%** and turned out to be luck: p = 0.17 against the
  market null, t = +0.37, and the entire profit reversed in the second half.
- A live model was scored against a **stale** market quote (cached ~2 minutes
  earlier, off by 12.6c on average). That manufactured a +10.5% "skill" that was
  really −1.2%. **Both sides of any comparison must be read at the same instant.**
- A strategy that looked significant at p = 0.0095 on 8 series gave p = 0.38 on a
  larger sample of 12. **One passing test is not a result.**
- Every subset tested posted a beautiful TRAIN statistic (up to t = +5.29) and
  collapsed out of sample. That is selection bias: the best of 48–60 searched
  combinations is inflated by construction.

## How to work

Be adversarial toward your own results. When something looks profitable, your
first job is to try to kill it: is it drift? does it survive out-of-sample? is
it concentrated in a handful of trades? was the fee applied on entry? are both
legs quoted at the same timestamp?

Iterate. If an idea dies, say so plainly and move to the next. If an idea
survives, push on it — tune it on TRAIN only and re-validate — until you have
the most profitable honest version of it.

Deliver: one script per idea, a summary table of `strategy | train | test |
corrected p | verdict`, and a clear final recommendation of which single
strategy to trade and at what size.
