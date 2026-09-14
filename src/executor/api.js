'use strict';
// API executor - places real orders from the laptop, no phone involved.
//
// Why this exists: the phone was only ever the unauthenticated route to order
// entry, and it cost 6-10 seconds and 4-27c of slippage per order against an
// edge of 6-7c. Execution was eating the strategy. This lands orders in ~100ms.
//
// It also unlocks the only strategy the research actually supported. The one
// real anomaly found across 8,115 markets - the favourite-longshot bias, at
// t = -15 - is about the size of the bid-ask spread, so it pays whoever
// COLLECTS the spread. Tapping a phone can only ever pay it. `postOnly` here
// rests an order instead of crossing, which is how you get on the other side.
//
// Same interface as the paper and phone executors: ready(), place(), close().

const { KalshiApi } = require('../kalshiApi');
const { tradingFee } = require('../kalshi');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class ApiExecutor {
  constructor(cfg, { log = () => {} } = {}) {
    this.mode = cfg.mode;
    this.full = cfg;
    this.cfg = cfg.api || {};
    this.log = log;
    // Environment is derived from the MODE, never from a separate config flag.
    // A stray "env": "prod" sitting in a file is exactly how a demo run turns
    // into a real-money run without anyone deciding to.
    this.env = cfg.mode === 'api-live' ? 'prod' : 'demo';
    this.api = new KalshiApi({
      env: this.env,
      keyId: this.cfg.keyId,
      privateKeyPath: this.cfg.privateKeyPath,
      log,
    });
    this.postOnly = !!this.cfg.postOnly;
  }

  async ready() {
    if (!this.cfg.keyId) return { ok: false, reason: 'api.keyId is not set in config.json' };
    if (!this.cfg.privateKeyPath) return { ok: false, reason: 'api.privateKeyPath is not set' };
    const v = await this.api.verify();
    if (!v.ok) return { ok: false, reason: v.reason };
    this.log('api', `authenticated on ${v.env}` +
      (v.balance != null ? `, balance $${v.balance.toFixed(2)}` : ''));
    return { ok: true, balance: v.balance };
  }

  /** Live cash, so the bot sizes against the real account rather than config. */
  async balance() {
    try {
      const v = await this.api.verify();
      return v.ok ? v.balance : null;
    } catch { return null; }
  }

  async place({ ticker, side, contracts, limitPrice, exchangeIndex = 0 }) {
    const started = Date.now();
    const clientOrderId = `kbot-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    try {
      // Taker by default (immediate_or_cancel at our limit, so we never pay
      // worse than the price the decision was made on). Maker mode rests the
      // order instead; it may not fill, which is the trade-off for collecting
      // the spread rather than paying it.
      const res = await this.api.createOrder({
        ticker, side, contracts, exchangeIndex,
        price: limitPrice,
        timeInForce: this.postOnly ? 'good_till_canceled' : 'immediate_or_cancel',
        postOnly: this.postOnly,
        clientOrderId,
      });

      const order = (res && (res.order || res)) || {};
      const status = String(order.status || '').toLowerCase();

      // THE FIELD NAME THAT COST THE ACCOUNT.
      // Kalshi returns `fill_count_fp` (a STRING). The names guessed here
      // originally - filled_count / count_filled - do not exist, so this was
      // always 0, every genuinely-filled order was reported as "rejected", the
      // engine never booked a position, and every risk cap that reads the
      // positions table therefore measured an empty table while real money
      // drained. One wrong field name silently disabled the whole risk system.
      // Read every known spelling, and treat "no recognised field" as UNKNOWN
      // rather than as zero.
      const fillFields = ['fill_count_fp', 'fill_count', 'filled_count',
                          'count_filled', 'filled_quantity'];
      const present = fillFields.find((k) => order[k] !== undefined);
      const filled = present !== undefined ? Number(order[present]) : null;

      // A resting maker order is a legitimate non-fill, not a failure.
      if (this.postOnly && !filled) {
        return {
          status: 'pending', orderId: order.order_id || null,
          error: 'maker order resting, not yet filled',
          latencyMs: Date.now() - started, evidence: clientOrderId,
        };
      }

      // Never conclude "not filled" from a response we do not understand.
      // Ask the exchange what actually happened, keyed on our client_order_id.
      let confirmed = filled;
      if (confirmed === null || confirmed === 0) {
        const seen = await this.confirmFill(clientOrderId, ticker);
        if (seen === null) {
          // We genuinely cannot tell. That is ambiguous, not rejected - the
          // engine turns 'pending' into a kill switch, which is the correct
          // response to not knowing whether real money just moved.
          return {
            status: 'pending',
            error: `cannot confirm fill for ${clientOrderId} - reconcile before trading again`,
            latencyMs: Date.now() - started, evidence: clientOrderId,
          };
        }
        confirmed = seen;
      }

      if (!confirmed || status === 'canceled') {
        return {
          status: 'rejected',
          error: `no fill (status ${status || 'unknown'}) - the book moved away from ${limitPrice.toFixed(3)}`,
          latencyMs: Date.now() - started, evidence: clientOrderId,
        };
      }
      const filledCount = confirmed;

      const avg = Number(order.average_fill_price ?? order.avg_fill_price
                         ?? order.avg_fill_price_dollars ?? limitPrice);
      const fillPrice = avg > 1 ? avg / 100 : avg;     // API has shipped cents and dollars
      return {
        status: 'filled',
        fillPrice,
        fee: tradingFee(filledCount, fillPrice),
        notional: filledCount * fillPrice,
        contractsFilled: filledCount,
        orderId: order.order_id || null,
        evidence: clientOrderId,
        latencyMs: Date.now() - started,
      };
    } catch (err) {
      // A network failure AFTER the request left is ambiguous: the order may
      // exist. Say so rather than reporting a clean failure - the engine turns
      // a 'pending' into a kill-switch so nothing gets double-placed.
      const ambiguous = /abort|timeout|network|fetch failed|ECONN/i.test(err.message);
      this.log('api', `order ${ambiguous ? 'AMBIGUOUS' : 'failed'}: ${err.message}`);
      return {
        status: ambiguous ? 'pending' : 'failed',
        error: err.message,
        evidence: clientOrderId,
        latencyMs: Date.now() - started,
      };
    }
  }

  /**
   * Ask the exchange whether our order filled, keyed on client_order_id.
   * Returns contracts filled, or null if we still cannot tell - and null must
   * never be treated as zero.
   */
  async confirmFill(clientOrderId, ticker) {
    for (let attempt = 0; attempt < 3; attempt++) {
      await sleep(300 * (attempt + 1));
      try {
        const list = await this.api.orders({ ticker, limit: 50 });
        const mine = (list.orders || []).find((o) => o.client_order_id === clientOrderId);
        if (mine) {
          const v = mine.fill_count_fp ?? mine.fill_count ?? mine.filled_count;
          if (v !== undefined) return Number(v);
        }
        const fills = await this.api.fills({ ticker, limit: 100 });
        const rows = (fills.fills || []).filter((f) => f.order_id && mine
          && f.order_id === mine.order_id);
        if (rows.length) {
          return rows.reduce((a, f) => a + Number(f.count_fp ?? f.count ?? 0), 0);
        }
        if (mine) return 0;          // order exists and shows no fills
      } catch { /* retry */ }
    }
    return null;                      // genuinely unknown
  }

  /**
   * Live account truth: cash and open exposure straight from the exchange.
   * The risk layer uses THIS, not the local database, because the local
   * database is exactly what was wrong when the account drained.
   */
  async liveState() {
    try {
      const b = await this.api.balance();
      const cents = b.balance ?? b.balance_cents;
      const shard = (b.balance_breakdown || [])
        .find((e) => e.exchange_index === (this.full.api.exchangeIndex ?? 2));
      const pos = await this.api.positions({ settlement_status: 'unsettled' });
      const rows = (pos.market_positions || pos.positions || []);
      let exposure = 0;
      for (const p of rows) {
        const e = Number(p.market_exposure ?? p.market_exposure_dollars ?? 0);
        exposure += e > 1 ? e / 100 : e;
      }
      return {
        ok: true,
        balance: typeof cents === 'number' ? cents / 100 : null,
        shardBalance: shard ? Number(shard.balance) : null,
        exposure,
        openCount: rows.filter((p) => Number(p.position ?? p.position_fp ?? 0) !== 0).length,
      };
    } catch (err) {
      return { ok: false, reason: err.message };
    }
  }

  /** True positions from the exchange - what the phone path could never do. */
  async reconcile() {
    try {
      const p = await this.api.positions({ settlement_status: 'unsettled' });
      return (p && (p.market_positions || p.positions)) || [];
    } catch (err) {
      this.log('api', `reconcile failed: ${err.message}`);
      return null;
    }
  }

  async close() {}
}

module.exports = { ApiExecutor };
