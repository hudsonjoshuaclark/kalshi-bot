# kalshi-bot

A fair-value bot for Kalshi's 15-minute crypto and commodity markets, executing
through the iPhone harness, with a desktop tracker.

**Read the Findings section before switching it to live.** The research in
`research/` measured this strategy on 3,200 real market windows and found it
loses money. The live path is built, tested and one switch away — but nothing
here has demonstrated an edge, and the honest expectation is that live trading
converts $19 into less than $19.

---

## What it does

Every 2 seconds, for each enabled series:

1. Find the live 15-minute window and read its published strike.
2. Read the Kalshi order book and a composite crypto index.
3. Price the contract.
4. Compare the model's probability against what the book actually charges,
   net of fees.
5. Gate on data health, then on risk, then execute — on the phone if live.

It records a forecast for **every** window whether or not it trades, so a
calibration record accumulates even while the bot sits on its hands.

### The contract, precisely

    K = mean of the settlement index over the 60s before the window OPENED
        (fixed; Kalshi publishes it as floor_strike once the window opens)
    S = mean of the settlement index over the 60s before the window CLOSES
    YES pays $1 if S >= K

This is a digital on a *60-second average*, not on a point price. That matters
in the final minute: once inside the averaging window, part of `S` is already
realised and no longer random, so variance collapses much faster than a naive
point-price model expects. `src/model.js` handles both regimes.

Settlement sources: **CF Benchmarks** (crypto) and **Pyth** (gold, silver, WTI).

### Fees

Kalshi's fee on these series is quadratic, rounded **up** to the cent:

    fee = ceil(0.07 * contracts * price * (1 - price))

At 50c that is 1.75c/contract — you need a **3.5% edge just to break even**. The
round-up is punishing on small orders: one contract at 95c owes $0.0033 and pays
$0.01. Per-contract fee therefore *falls* with size, which is why `src/risk.js`
re-checks edge at the size actually being sent rather than at a 1-contract probe.

---

## Findings

All of this is reproducible from `research/`; the raw tape is in
`research/tape2/` (9.8 MB, ~100 hours across 8 series).

### 1. These markets are enormous, and efficient

A single 15-minute BTC window trades **2,097,409 contracts** with a ~0.7c
spread. This is not a sleepy market with retail mispricings lying around.

### 2. Crossing the spread loses at every horizon

`research/calibration.py`. Cost per contract, best side, by minutes to close:

| minutes left | BTC | ETH | GOLD |
|---|---|---|---|
| 0–1 | +0.0004 | +0.0000 | −0.0079 |
| 1–2 | −0.0066 | −0.0122 | −0.0124 |
| 3–5 | −0.0115 | −0.0155 | −0.0118 |
| 5–7 | −0.0105 | −0.0050 | −0.0056 |
| 7–10 | −0.0131 | −0.0119 | −0.0051 |

Spread (0.7–1.8c) plus fee (1.75c at the money) is ~2.5c of friction against a
price that is accurate to ~1–2c. **Gold is the cleanest test**: its YES base rate
over 400 windows was 0.5075, so there is no drift to hide behind, and *every*
horizon on *both* sides loses.

### 3. The one positive number is drift, not edge

"Buy YES 10–15 minutes out" showed +$4.78 on BTC, +$21.69 on SOL. Splitting the
sample into 8 chronological blocks (`research/drift_test.py`):

    corr(block YES base rate, block P&L) = +0.984 to +0.989   (BTC/ETH/SOL/XRP)

The mean ask is ~0.50 in every block. P&L is 98–99% explained by which way the
coin drifted — the strategy is a levered long paying a 3.5% fee every 15
minutes. Confirmation: **silver drifted down** (base rate 0.4875) and its only
positive number is on the **NO** side. The fake edge always points whichever way
the asset happened to move.

### 4. The model does not beat the market

`research/model_vs_market.py`, 5,590 BTC observations with live spot:

| minutes left | Brier market | Brier model | skill |
|---|---|---|---|
| 1–2 | 0.0420 | 0.0439 | −4.43% |
| 3–5 | 0.0966 | 0.1012 | −4.83% |
| 7–10 | 0.1698 | 0.1723 | −1.48% |
| 10–15 | 0.2262 | 0.2292 | −1.35% |

Model/market price correlation is **0.976–0.990**: the market already prices
exactly the spot-vs-strike relationship the model computes, slightly better.

Trading on disagreement gets **worse as disagreement grows** — the signature of
adverse selection, and the opposite of what a real edge looks like:

| threshold | trades | P&L/contract | hit rate |
|---|---|---|---|
| 0.02 | 2866 | −0.0243 | 39.1% |
| 0.05 | 1276 | −0.0164 | 33.6% |
| 0.10 | 335 | −0.0381 | 23.6% |
| 0.15 | 112 | −0.0531 | 15.2% |

### 5. Why a phone makes it worse

Each WebDriverAgent action is 200–450ms and an order is 7–9 actions, so 3–5
seconds pass between deciding and being filled. In a market repricing several
times a second, the price that justified the trade is gone before the tap lands.
The phone is here because it is the only unauthenticated route to order entry —
Kalshi serves market data without a key, but orders need credentials, and the
phone holds the logged-in session. It is not a fast path.

### 6. Portfolio backtest of the bot as configured

`research/backtest.js` — 2,800 settled windows over 138 hours across 7 series
(BTC, ETH, SOL, XRP, DOGE, gold, silver). It **imports the shipped `src/model.js`
and `src/risk.js`** and drives them on simulated time against a temporary
database, so what is measured is the code that would actually place orders:
quarter Kelly, every cap, the ceil-to-the-cent fee, the conservative decision
probabilities, the price band, one-position-per-window, the daily loss limit and
the kill switch.

Commodity index is Pyth — the *actual* settlement source — verified against
Kalshi's published `floor_strike` to **0.0015%** (gold) and **0.0033%** (silver).
WTI is excluded: no Pyth series reproduced its strikes.

    AS SHIPPED
      trades        182  (87W / 95L, hit 47.8%)
      P&L           +$9.56   fees -$7.67   staked $229.44
      final equity  $28.56  from $19.00  (+50.3%)
      max drawdown  $25.20
      forecast      n=2404  Brier model 0.2246 vs market 0.2212  skill -1.56%

**That +50% is luck, and here is the proof.**

*Sensitivity to `minEdge`* — a real signal earns more as the threshold tightens.
This one flips sign at random:

| minEdge | 0.01 | 0.02 | 0.03 | 0.04 | 0.06 | 0.08 | 0.10 | 0.15 |
|---|---|---|---|---|---|---|---|---|
| P&L | −$1.73 | +$4.69 | −$4.90 | −$7.24 | +$9.56 | +$15.09 | +$1.65 | +$10.30 |
| trades | 566 | 552 | 407 | 342 | 182 | 91 | 57 | 23 |

*Significance* (`research/significance.py`). The sharp test is the **market
null**: we know the market's own mid at every entry, so redraw each outcome from
*the market's* probability and see how often chance beats us.

    1. MARKET NULL (20,000 sims)
       our result        +9.56
       null mean        -11.41        <- the EXPECTED result of running this bot
       null 5/50/95    -46.44 / -12.44 / +27.56
       p(null >= ours)   0.173        <- NOT significant

    2. BOOTSTRAP
       95% CI on total P&L   -38.17 .. +62.44   (includes zero)
       36.9% of resamples lose money
       mean per trade +0.0525, se 0.1409, t = +0.37

    3. SPLIT-HALF
       first half   n=91  P&L +26.09
       second half  n=91  P&L -16.52   <- no persistence

t = +0.37. The entire profit came from the first half and reversed in the
second. Four of six series are profitable, which is what coin-flips look like.

*What actually happens to $19.* Replaying the same 182-trade sequence with
outcomes drawn from the market's price, with the kill switch enforced:

    probability the $19 is wiped out : 56.0%
    median final equity              : $0.00
    mean final equity                : $11.39
    share ending below $19           : 73.1%

**The expected outcome of running this bot live is losing about 60% of the
account, with a coin-flip chance of losing all of it.** The backtest's +$9.56 was
a roughly one-sigma good draw from that distribution.

Note the biggest rejection reason is `size below minimum` (18,933 times): at a
$19 bankroll quarter Kelly usually sizes under one contract, so only the
*largest* model-vs-market disagreements ever trade — and finding 4 above showed
those are precisely the worst ones.

### 7. Universe-wide pattern search (2026-08-24)

`research/universe.py` -> `collect_broad.py` -> `patterns.py` -> `strategy.py`
-> `hypotheses.py`. **8,115 settled markets across 33 series**, every liquid
Kalshi crypto and commodity series (>=50 settled markets, >=200k volume), from
15-minute up to weekly.

**Method that makes the result trustworthy**

* *Side-symmetric pooling.* Every observation counted twice - as YES at price p
  and as NO at (1-p). Directional drift helps one side exactly as much as it
  hurts the other, so it cancels by construction. This is the control missing
  from finding 3, and it is why that "+50%" was a mirage.
* *One observation per market.* Bars inside a window are the same bet measured
  repeatedly; treating them as independent inflates every t-statistic.
* *Train/test split by time.* Oldest 70% for all searching; newest 30% touched
  once, at the end.
* *Bonferroni correction* by the number of rules searched.

**The bias is real.** Side-symmetric calibration shows a textbook
favourite-longshot bias: longshots overpriced, favourites underpriced,
symmetric about 0.50, strongest in weekly markets. Buying longshots loses
**-2.8c to -4.5c per contract at t = -10 to -15** - overwhelming significance.

**The bias is not tradable.** Every attempt to harvest it dies out of sample:

| subset | train | test | verdict |
|---|---|---|---|
| all 33 series | +0.79c (t=+2.34) | -0.18c (p=0.37) | fails |
| weekly | +2.05c (t=+5.29) | +0.88c (p=0.18) | fails |
| fifteen-min | +1.23c (t=+1.32) | +0.30c (p=0.38) | fails |
| daily | +1.27c (t=+4.09) | -0.33c (p=0.66) | fails |

Then a pre-registered sweep: **15 rules x 4 entry points = 60 combinations**
(favourite, longshot, momentum, reversal, wide/tight spread, near-50/50), top
five validated on the held-out half. **0/5 survived** Bonferroni correction; all
five came back at or below zero.

Note every subset above posts a gorgeous train statistic - up to t=+5.29 - and
collapses in test. That is selection bias, not signal: the best of 48-60
combinations is inflated by construction, and only the untouched half is honest.
An earlier 15-minute-only run passed at p=0.0095; the same rule on a larger
15-minute sample gives p=0.38. One passing test is not a result.

**Why it is not tradable, in one line:** the favourite-longshot bias is about
the size of the bid-ask spread plus the fee. It lives in the mid-price, and a
taker pays the ask. Whoever *collects* the spread captures it; whoever *pays* it
does not.

That points at the only strategy the data supports - **making, not taking** -
and that needs resting orders, continuous repricing and the authenticated API.
It cannot be done by tapping a phone.

### Conclusion

No edge was found for a taker strategy in these markets, and a phone can only
take. Six independent tests agree — calibration, Brier skill, disagreement
sizing, drift decomposition, the portfolio backtest, and its significance tests.
The one profitable-looking number does not survive a null drawn from the
market's own price.

If you want a real shot, hunt somewhere the book is thin and a considered
forecast can beat it — not the most heavily traded 15-minute contract on the
exchange.

---

## Two bugs the build found, worth knowing

Both were caught by running the thing, and both would have quietly lost money.

**Fake edge from our own uncertainty.** The pricer adds index tracking error to
the variance. That is honest as belief but disastrous as a signal: symmetric
uncertainty pulls probabilities toward 0.5, so on a deep out-of-the-money
contract the market said 0.2% and the model said 6.9% — and the bot read the gap
as a 6.6c edge and bought a worthless lottery ticket. The gap was not edge, it
was us knowing less than the market. `decisionProbabilities()` now drops the
tracking-error variance for decisions and shifts the index *against* the side
being bought, so an edge only counts if it survives our feed being wrong.

**Volatility measured on tick data.** Sigma was estimated from tick-to-tick
websocket returns, which are dominated by bid-ask bounce. Overstated sigma drags
probabilities toward 0.5 and invents disagreement on exactly the contracts
furthest from the money. Now measured on sparse 30-second returns plus a
30-minute candle anchor, and bias-corrected: measured against 100 hours of real
15-minute BTC moves, a 30-minute lookback runs 1.077x hot and a 120-minute one
1.137x (`research/vol_check.py`).

---

## Running it

    npm install                  # once

**Desktop:** the "Kalshi Bot" shortcut. Re-create it with

    powershell -ExecutionPolicy Bypass -File install_desktop_shortcut.ps1
    powershell -ExecutionPolicy Bypass -File install_desktop_shortcut.ps1 -Remove

The window starts and stops the bot itself. Closing the window does **not** kill
a bot you started from the CLI — a viewer should not close positions.

**Headless:**

    node bin/bot.js run          # loop + control API on 127.0.0.1:8770
    node bin/bot.js status       # equity, positions, forecast record
    node bin/bot.js kill         # stop all order placement, immediately
    node bin/bot.js revive

The bot runs in real Node (24) and the tracker is Electron; they talk over HTTP
because Electron 33 bundles Node 20, which has no `node:sqlite`. An engine lock
in the database stops the CLI and the window trading the same bankroll twice.

### The number to watch

Not P&L — **forecast skill vs market**, on the tracker and in `status`. It is the
Brier score of the model against the market's own snapshotted price on the same
resolved windows. Over a few hundred 15-minute windows P&L is dominated by
whether the coin drifted; Brier isolates whether the model actually knows
something. If that number is not positive, any profit is luck.

---

## Going live

Live mode taps real orders into the Kalshi app on the phone.

**Prerequisites**

1. The phone plugged in, unlocked, harness running:
   `cd ../phone-harness && node bin/phone.js serve` (`doctor` diagnoses).
2. The Kalshi app installed and logged in. Set `phone.bundleId` in
   `config.json` — check with `node ../phone-harness/bin/phone.js apps`.
3. **Calibrate the UI selectors first.** The order flow is written against
   accessibility labels that move when Kalshi ships an update:

       node bin/bot.js phone-dryrun --series=KXBTC15M --side=yes --contracts=1

   This walks the entire flow and stops immediately before the final confirm,
   leaving numbered screenshots in `data/shots/`. Every selector is matched
   loosely and every step fails loudly rather than guessing — a missed selector
   aborts the order instead of tapping something else. **This has not been run
   against the real app**, because the phone was unplugged during the build.
   Expect to fix selectors on the first pass.

Then `node bin/bot.js live`, or the tracker's *Go live* button (which asks
first, and tells you what the backtest found).

### Guardrails

From `config.json` — all enforced in `src/risk.js` before every order:

| setting | value | meaning |
|---|---|---|
| `bankroll` | $19 | starting equity |
| `maxTotalLoss` | $19 | engages the kill switch permanently |
| `risk.maxStakePerTrade` | $2 | per position |
| `risk.maxOpenExposure` | $6 | across all open positions |
| `risk.maxConcurrentPositions` | 3 | |
| `risk.maxTradesPerHour` | 8 | |
| `risk.dailyLossLimit` | $5 | pauses for the day |
| `risk.kellyFraction` | 0.25 | quarter Kelly |
| `signal.minEdge` | 0.06 | net of fees, at the real order size |
| `signal.minPrice` / `maxPrice` | 0.05 / 0.95 | no tail lottery tickets |

Also: one position per market window (without it the loop re-buys the same view
every tick), and a live order that fails *after* possibly reaching the exchange
engages the kill switch rather than risking a duplicate — reconcile on the phone
before restarting.

`signal.minEdge` at 0.06 does not stop the bot trading in paper — the model
still produces 6–10c disagreements. That is deliberate: paper trading builds the
live forecast record that either confirms or overturns the backtest. Watch the
Brier skill number before you touch the live switch.

---

## Layout

    bin/bot.js              CLI
    config.json             all tunables
    src/kalshi.js           public market data; book conversion; the fee formula
    src/feed.js             composite index + realised volatility
    src/model.js            the pricer and the conservative decision probabilities
    src/engine.js           the loop
    src/risk.js             sizing and every hard cap
    src/store.js            SQLite; forecasts, orders, positions, equity
    src/executor/paper.js   logs fills, charges real fees
    src/executor/phone.js   drives the Kalshi iOS app
    src/server.js           control API on 127.0.0.1:8770
    tracker/                Electron window
    research/               everything in Findings, plus the raw tape
    research/diagnose.js    live pipeline check — run this when the model
                            disagrees loudly with the market; it is usually us

## Known gaps

- **The phone order flow is unverified against the real app.** See above.
- **No position reconciliation.** Without API credentials the bot cannot read
  its own fills; the confirmation screen *is* the fill record. If the app and
  the database disagree, the database is wrong.
- The crypto index is a 4-venue proxy for CF Benchmarks, not the real thing.
  Commodities could use Pyth directly (it is public) — not yet wired up.
- Commodity series are weekend-closed and were not live during the build, so
  only their historical tape has been analysed.
