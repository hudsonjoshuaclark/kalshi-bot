# Trading from the laptop (no phone)

Everything except order entry already ran on this laptop. Kalshi's market data
is public; only placing an order needs credentials, and that is the single job
the phone was doing. An API key removes it.

This is worth doing for three reasons beyond convenience:

1. **It deletes the cost that was killing the strategy.** Phone execution was
   measured at 6-10 seconds and **4-27c of slippage** per order, against an edge
   of 6-7c. API orders land in ~100ms.
2. **It unlocks making instead of taking.** The one real anomaly in the research
   - the favourite-longshot bias, at t = -15 across 8,115 markets - is about the
   size of the bid-ask spread, so it pays whoever *collects* the spread. Tapping
   a phone can only ever pay it.
3. **Real reconciliation.** The bot can read its actual positions, balance and
   fills instead of inferring them from a screenshot.

**None of this creates an edge.** It removes a cost and opens a door.

---

## What is already done

The client, the executor, the CLI and the safety wiring are written and tested.
The signing format is **verified against Kalshi's live demo endpoint**:

    unsigned request  -> token_authentication_failure
    signed, bogus key -> authentication_error      <- a DIFFERENT error

Different error codes prove Kalshi parsed the headers, the millisecond
timestamp, the signed-message layout and the base64 encoding. Only a real key is
missing. (Re-run that check any time: `node research/verify_signing.js`.)

## What only you can do

Generating the key needs your Kalshi login.

1. Sign in to Kalshi -> **Account / Profile -> API Keys** (demo lives at
   `demo.kalshi.co`, production at `kalshi.com` - **they are separate accounts
   with separate keys**).
2. Create a key. Kalshi shows a **Key ID** and downloads a **private key .pem
   exactly once**.
3. Put the .pem somewhere outside this repo, e.g.

       C:\Users\hudso\.kalshi\demo-key.pem

4. Fill in `config.json`:

       "api": {
         "keyId": "<the key ID>",
         "privateKeyPath": "C:\\Users\\hudso\\.kalshi\\demo-key.pem",
         "postOnly": false
       }

5. Check it end to end:

       node bin/bot.js api-verify

   Success prints the environment, your balance and open position count.
   Failure prints a specific reason and a checklist.

**Start on demo.** A demo key will not authenticate against production, which is
a useful accident-preventer rather than an annoyance.

---

## Modes

| mode | orders | money | how |
|---|---|---|---|
| `paper` | simulated | none | assumes a perfect fill at the quoted ask |
| `api` | **real** | **fake** | Kalshi demo exchange |
| `api-live` | **real** | **REAL** | production |
| `live` | **real** | **REAL** | taps the phone |

    node bin/bot.js api          # demo
    node bin/bot.js api-live     # production
    node bin/bot.js run

The environment is derived from the **mode**, not from a separate config field.
There is deliberately no `"env"` setting to leave lying around - that is exactly
how a demo run becomes a real-money run without anyone deciding to.

### Why demo beats paper

`paper` mode assumes it fills at the quoted ask, instantly, in full. That is the
assumption that flattered the backtest. Demo mode places **real orders into a
real book** and reports what actually filled - so it measures execution honestly
while risking nothing.

---

## Taker vs maker

`api.postOnly: false` (default) crosses the spread with an
`immediate_or_cancel` order at our limit, so we never pay worse than the price
the decision was made on. If the book moves, the order is rejected rather than
filled at a worse price.

`api.postOnly: true` rests the order instead. It may not fill - that is the
price of collecting the spread rather than paying it. **This is the mode the
research actually supports**, and it is untestable from a phone.

## Safety, unchanged

Every guardrail applies identically in API mode:

- `maxStakePerTrade`, `maxOpenExposure`, `maxConcurrentPositions`,
  `maxTradesPerHour`, `dailyLossLimit`, quarter Kelly
- `maxTotalLoss` engages the kill switch permanently
- one position per market window
- an **ambiguous** order (a request that may or may not have arrived) engages
  the kill switch in every real-money mode, so nothing gets double-placed.
  A resting maker order is a known state and is not treated as ambiguous.

`node bin/bot.js kill` stops all order placement immediately.

## Never commit the key

The `.pem` is a bearer credential that can trade. Keep it outside the repo,
never paste it into `config.json`, and revoke it in Kalshi's settings if it is
ever exposed. `config.json` stores only a path.
