'use strict';
// Kalshi perpetual-futures client.
//
// Perps live under the `/margin` namespace, NOT `/perpetuals` or `/perps` -
// eight guessed endpoints all 404'd before the docs turned that up. Auth,
// signing and base URL are identical to the binary API, so KalshiApi is reused
// wholesale and only the endpoints differ.
//
// Everything here is denominated in CONTRACTS, and a contract is a fixed
// fraction of a coin (BTC 0.0001, ETH 0.001, ADA 1.0). Fees are charged on
// NOTIONAL, not on margin posted:
//     taker 0.120%   maker 0.020%   (Tier 0, under $100k 30d volume)
// The taker fee is ~30x the observed BTC spread, so it dominates every other
// cost and is the single most important number in the whole design.

const { KalshiApi } = require('../kalshiApi');

const TAKER_FEE = 0.00120;
const MAKER_FEE = 0.00020;

class PerpApi {
  constructor({ keyId, privateKeyPath, env = 'prod', log = () => {} } = {}) {
    this.api = new KalshiApi({ env, keyId, privateKeyPath, log });
    this.log = log;
    this._markets = null;
    this._marketsAt = 0;
  }

  /** All perp markets, cached briefly - contract sizes never change intraday. */
  async markets({ maxAgeMs = 30000 } = {}) {
    if (this._markets && Date.now() - this._marketsAt < maxAgeMs) return this._markets;
    const r = await this.api.request('GET', '/margin/markets', { query: { limit: 100 } });
    const out = new Map();
    for (const m of r.markets || []) {
      const bid = Number(m.bid), ask = Number(m.ask);
      if (!(bid > 0 && ask > 0)) continue;          // unquoted / halted
      out.set(m.ticker, {
        ticker: m.ticker,
        coin: m.ticker.replace(/^KX/, '').replace(/PERP\d*$/, ''),
        bid, ask, mid: (bid + ask) / 2,
        spreadFrac: (ask - bid) / ((ask + bid) / 2),
        contractSize: Number(m.contract_size),
        tickSize: Number(m.tick_size),
        maxLeverage: Number(m.leverage_estimate || 0),
        exchangeIndex: m.exchange_index ?? 0,
        status: m.status,
        volume24hNotional: Number(m.volume_24h_notional_value_dollars || 0),
        openInterest: Number(m.open_interest || 0),
      });
    }
    this._markets = out;
    this._marketsAt = Date.now();
    return out;
  }

  /**
   * Live account truth. The binary bot drained an account because its risk
   * caps read a local table that a field-name bug left empty; nothing here is
   * allowed to depend on local bookkeeping.
   */
  async liveState() {
    try {
      const [bal, pos] = await Promise.all([
        this.api.request('GET', '/portfolio/balance'),
        this.api.request('GET', '/margin/positions'),
      ]);
      const cents = bal.balance ?? bal.balance_cents;
      const rows = pos.positions || [];
      const positions = new Map();
      let grossNotional = 0;
      for (const p of rows) {
        const qty = Number(p.position ?? p.quantity ?? p.contracts ?? 0);
        if (!qty) continue;
        const notional = Math.abs(Number(p.notional_value_dollars ?? p.notional ?? 0));
        grossNotional += notional;
        positions.set(p.ticker, { ticker: p.ticker, contracts: qty, notional });
      }
      return {
        ok: true,
        balance: typeof cents === 'number' ? cents / 100 : null,
        positions,
        grossNotional,
        openCount: positions.size,
      };
    } catch (err) {
      return { ok: false, reason: err.message };
    }
  }

  /**
   * Place a perp order.
   *
   * `side` is 'buy' (long) or 'sell' (short). Size is in CONTRACTS.
   * postOnly rests the order and pays 0.020% instead of 0.120% - a 6x cost
   * difference that decides whether a slow strategy clears its costs.
   */
  async order({ ticker, side, contracts, price, postOnly = false, exchangeIndex = 0,
                clientOrderId = null }) {
    if (side !== 'buy' && side !== 'sell') throw new Error(`bad side "${side}"`);
    if (!(contracts > 0)) throw new Error(`contracts ${contracts} must be > 0`);
    const body = {
      ticker,
      side,
      count: String(contracts),
      type: price ? 'limit' : 'market',
      exchange_index: exchangeIndex,
    };
    if (price) body.price = String(price);
    if (postOnly) body.post_only = true;
    if (clientOrderId) body.client_order_id = clientOrderId;
    return this.api.request('POST', '/margin/orders', { body });
  }

  restingOrders(query = {}) {
    return this.api.request('GET', '/margin/orders', { query: { status: 'resting', ...query } });
  }

  fills(query = {}) {
    return this.api.request('GET', '/margin/fills', { query });
  }

  async cancel(orderId, { exchangeIndex = null, ticker = null } = {}) {
    const query = {};
    if (exchangeIndex !== null) query.exchange_index = exchangeIndex;
    if (ticker) query.market_ticker = ticker;
    return this.api.request('DELETE', `/margin/orders/${encodeURIComponent(orderId)}`, { query });
  }

  /** Cancel everything resting. The panic button. */
  async cancelAll() {
    const list = await this.restingOrders();
    const out = [];
    for (const o of list.orders || []) {
      try {
        await this.cancel(o.order_id, { exchangeIndex: o.exchange_index, ticker: o.ticker });
        out.push({ ok: true, ticker: o.ticker, orderId: o.order_id });
      } catch (err) {
        out.push({ ok: false, ticker: o.ticker, orderId: o.order_id, error: err.message });
      }
    }
    return out;
  }
}

module.exports = { PerpApi, TAKER_FEE, MAKER_FEE };
