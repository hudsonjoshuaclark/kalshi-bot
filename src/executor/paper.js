'use strict';
// Paper executor. Fills at the price we would actually have swept to, and
// charges the real Kalshi fee - including the round-up-to-the-cent, which is
// the single biggest cost at a $19 bankroll and the thing a naive paper trader
// always forgets.
//
// It does NOT model queue position or partial fills, because it only ever
// crosses the spread: it lifts resting size that the order book said was there
// milliseconds earlier. The honest error term is adverse selection - the size
// you lift is disproportionately the size that wanted to be lifted - and no
// paper fill model captures that. Treat paper P&L as an optimistic bound.

const { tradingFee } = require('../kalshi');

class PaperExecutor {
  constructor({ log = () => {} } = {}) {
    this.mode = 'paper';
    this.log = log;
  }

  async ready() { return { ok: true }; }

  async place({ ticker, side, contracts, limitPrice }) {
    const started = Date.now();
    const fee = tradingFee(contracts, limitPrice);
    this.log('paper', `FILL ${side} ${contracts} ${ticker} @ ${limitPrice.toFixed(4)} fee ${fee.toFixed(2)}`);
    return {
      status: 'filled',
      fillPrice: limitPrice,
      fee,
      notional: contracts * limitPrice,
      evidence: 'paper',
      latencyMs: Date.now() - started,
    };
  }

  async close() {}
}

module.exports = { PaperExecutor };
