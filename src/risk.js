'use strict';
// Risk gate. Every order passes through `check()`, and `check()` is allowed to
// say no for boring reasons. The bankroll here is $19, which sounds like it
// makes risk management irrelevant - it does the opposite. At this size the
// quadratic fee ROUNDS UP TO THE CENT, so a 1-contract order at 50c pays a 2c
// fee: a 4% tax before the trade has an opinion. Sizing has to be aware of that.

const { tradingFee } = require('./kalshi');

class Risk {
  /**
   * `now` is injectable so the backtester can drive this exact class on
   * simulated time. A backtest that reimplements the risk rules is a backtest
   * of something other than the bot.
   */
  constructor(cfg, store, log = () => {}, { now = () => Date.now(), requireLive = false } = {}) {
    this.cfg = cfg;
    this.store = store;
    this.log = log;
    this.now = now;
    // In any real-money mode the bot must NOT trade without a live account
    // read. Trading on local state alone is what emptied the account.
    this.requireLive = requireLive;
  }

  get limits() { return this.cfg.risk; }

  /** Realised P&L since local midnight. */
  todayRealised() {
    const start = new Date(this.now()); start.setHours(0, 0, 0, 0);
    const rows = this.store.all(
      'SELECT pnl FROM positions WHERE settled_at >= $t AND mode = $m',
      { t: start.getTime(), m: this.cfg.mode });
    return rows.reduce((a, r) => a + (r.pnl || 0), 0);
  }

  lifetimeRealised() {
    const rows = this.store.all(
      'SELECT pnl FROM positions WHERE settled_at IS NOT NULL AND mode = $m',
      { m: this.cfg.mode });
    return rows.reduce((a, r) => a + (r.pnl || 0), 0);
  }

  openExposure() {
    return this.store.openPositions(this.cfg.mode)
      .reduce((a, p) => a + p.contracts * p.avg_price + (p.fee || 0), 0);
  }

  /**
   * Orders SENT in the last hour - every status, no exceptions.
   *
   * This used to count only 'filled' and 'pending'. When a bug made the
   * executor label real fills as 'rejected', the rate limit counted zero and
   * the bot sent 354 orders it believed had all failed. A rate limit must
   * measure what was SENT, because that is what spends money; whether we
   * believe it filled is exactly the thing that can be wrong.
   */
  tradesLastHour() {
    const row = this.store.get(
      'SELECT COUNT(*) AS n FROM orders WHERE ts >= $t AND mode = $m',
      { t: this.now() - 3600_000, m: this.cfg.mode });
    return row ? row.n : 0;
  }

  /** Equity at the start of today, remembered so the stop is not self-loosening. */
  dayStartEquity() {
    const start = new Date(this.now()); start.setHours(0, 0, 0, 0);
    const key = `dayStartEquity:${start.toISOString().slice(0, 10)}`;
    let v = this.store.getState(key, null);
    if (v == null) {
      v = this.cfg.bankroll + this.lifetimeRealised() - this.todayRealised();
      this.store.setState(key, v);
    }
    return v;
  }

  /**
   * Trip the drawdown stop: halt trading AND record that the strategy needs
   * re-examining. Deliberately two separate things - the halt is automatic, the
   * strategy change is not. A single bad day is noise, and changing a strategy
   * off noise is how a bot gets worse. Any change must clear the same bar as
   * everything else in research/: held-out test, market null, correction.
   */
  requestReevaluation(detail) {
    if (this.store.getState('reevaluate', null)) return;   // already flagged
    this.store.setState('reevaluate', { ...detail, at: this.now(), handled: false });
    this.kill(`daily drawdown stop hit - down ${(detail.pctDown * 100).toFixed(1)}% ` +
      'today; strategy reevaluation requested');
    this.store.logEvent('error', 'risk',
      `REEVALUATE: down ${(detail.pctDown * 100).toFixed(1)}% from ` +
      `$${detail.dayStartEquity.toFixed(2)}. Run: node bin/bot.js reevaluate`);
  }

  killed() {
    return !!this.store.getState('killSwitch', false);
  }

  kill(reason) {
    this.store.setState('killSwitch', true);
    this.store.setState('killReason', reason);
    this.log('risk', `KILL SWITCH: ${reason}`);
    this.store.logEvent('error', 'risk', `kill switch engaged: ${reason}`);
  }

  revive() {
    this.store.setState('killSwitch', false);
    this.store.setState('killReason', null);
  }

  /**
   * Size a position, then approve or refuse it.
   * Returns { ok, contracts, cost, fee, reason }.
   */
  check({ side, price, prob, kelly, bookDepth, live = null }) {
    if (this.killed()) {
      return { ok: false, reason: `kill switch: ${this.store.getState('killReason', 'unknown')}` };
    }

    const lim = this.limits;

    // ---- exchange truth beats local bookkeeping -----------------------
    // Every cap below used to read the local positions table. A field-name bug
    // left that table empty while the real account drained to zero, so the
    // $5 exposure cap, the position count, the per-window rule and the 25%
    // drawdown stop all measured nothing and all passed. When a live read is
    // available it wins, and a FAILED live read is a stop, not a shrug.
    if (this.requireLive && !(live && live.ok)) {
      return { ok: false, reason: `no live account read (${live ? live.reason : 'not supplied'}) - refusing to trade blind` };
    }

    if (live && live.ok) {
      const floor = lim.minBalance ?? 0;
      const bal = live.shardBalance != null ? live.shardBalance : live.balance;
      if (bal != null && bal <= floor) {
        this.kill(`live balance $${bal.toFixed(2)} at or below floor $${floor.toFixed(2)}`);
        return { ok: false, reason: `balance floor reached ($${bal.toFixed(2)})` };
      }
      if (live.exposure != null && live.exposure >= lim.maxOpenExposure) {
        return { ok: false, reason: `live exposure $${live.exposure.toFixed(2)} >= cap $${lim.maxOpenExposure}` };
      }
      if (live.openCount != null && live.openCount >= lim.maxConcurrentPositions) {
        return { ok: false, reason: `live open positions ${live.openCount} >= cap ${lim.maxConcurrentPositions}` };
      }
    }
    const lifetime = this.lifetimeRealised();
    if (lifetime <= -Math.abs(this.cfg.maxTotalLoss)) {
      this.kill(`lifetime loss ${lifetime.toFixed(2)} hit maxTotalLoss`);
      return { ok: false, reason: 'max total loss reached' };
    }

    // --- daily drawdown stop -------------------------------------------
    // Measured against the equity this day STARTED at, not the original
    // bankroll: after a good run, "down 25%" has to mean 25% of what you had
    // this morning, or the stop silently loosens as equity grows.
    const today = this.todayRealised();
    const dayStart = this.dayStartEquity();
    const pct = lim.drawdownStopPct;
    if (pct && dayStart > 0 && today <= -pct * dayStart) {
      this.requestReevaluation({
        dayStartEquity: dayStart,
        lossToday: today,
        pctDown: -today / dayStart,
      });
      return {
        ok: false,
        reason: `daily drawdown stop: down ${(-today / dayStart * 100).toFixed(1)}% ` +
          `($${(-today).toFixed(2)} of $${dayStart.toFixed(2)})`,
      };
    }
    if (today <= -Math.abs(lim.dailyLossLimit)) {
      return { ok: false, reason: `daily loss limit (${today.toFixed(2)})` };
    }

    const openN = this.store.openPositions(this.cfg.mode).length;
    if (openN >= lim.maxConcurrentPositions) {
      return { ok: false, reason: `already ${openN} open positions` };
    }

    if (this.tradesLastHour() >= lim.maxTradesPerHour) {
      return { ok: false, reason: 'hourly trade cap' };
    }

    // Remaining capital: starting bankroll plus whatever has been realised,
    // minus what is already tied up in open positions.
    // Prefer live figures for both equity and exposure.
    const equity = (live && live.ok && live.balance != null)
      ? live.balance + (live.exposure || 0)
      : this.cfg.bankroll + lifetime;
    const exposure = (live && live.ok && live.exposure != null)
      ? live.exposure : this.openExposure();
    const free = (live && live.ok && live.shardBalance != null)
      ? Math.max(0, live.shardBalance)
      : Math.max(0, equity - exposure);
    if (free < 0.05) return { ok: false, reason: 'no free capital' };

    const headroom = Math.max(0, lim.maxOpenExposure - exposure);
    const budget = Math.min(lim.maxStakePerTrade, free, headroom);
    if (budget < price) {
      return { ok: false, reason: `budget ${budget.toFixed(2)} < one contract at ${price}` };
    }

    // Fractional Kelly on equity, then clipped by every hard cap.
    const kellyStake = Math.max(0, kelly) * (lim.kellyFraction || 0.25) * equity;
    const stake = Math.min(kellyStake, budget);
    let contracts = Math.floor(stake / price);

    contracts = Math.min(contracts, lim.maxContracts);
    if (bookDepth != null) contracts = Math.min(contracts, Math.floor(bookDepth));

    if (contracts < (lim.minContracts || 1)) {
      return { ok: false, reason: `size ${contracts} below minimum` };
    }

    // Re-price the fee at the ACTUAL contract count. The ceil-to-cent means the
    // per-contract fee falls sharply with size, so a trade that is unprofitable
    // at 1 contract can be profitable at 8 - and vice versa if a cap cut the
    // size down. Verify edge survives at the size we are really sending.
    const fee = tradingFee(contracts, price);
    const costPer = price + fee / contracts;
    const netEdge = prob - costPer;
    if (netEdge < this.cfg.signal.minEdge) {
      return {
        ok: false,
        reason: `edge ${netEdge.toFixed(4)} < minEdge ${this.cfg.signal.minEdge} at ${contracts} contracts (fee ${fee.toFixed(2)})`,
      };
    }

    const cost = contracts * price + fee;
    if (cost > free) return { ok: false, reason: 'cost exceeds free capital' };

    return { ok: true, contracts, cost, fee, costPer, netEdge, side, price };
  }
}

module.exports = { Risk };
