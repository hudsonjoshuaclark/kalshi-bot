'use strict';
// Authenticated Kalshi API client - the laptop-only replacement for the phone.
//
// Auth is RSA-PSS request signing. Three headers on every authenticated call:
//
//   KALSHI-ACCESS-KEY        the API key ID
//   KALSHI-ACCESS-TIMESTAMP  current time in MILLISECONDS
//   KALSHI-ACCESS-SIGNATURE  base64(RSA-PSS(SHA256, saltLen=32) over
//                            `${timestampMs}${METHOD}${path}`)
//
// Three details that silently produce 401s if you get them wrong:
//   * the timestamp is milliseconds, not seconds
//   * the signed path INCLUDES `/trade-api/v2` and EXCLUDES the query string
//   * salt length must equal the digest length (32), not the default
//
// The private key is read from a file outside the repo. It is never written to
// config.json, never logged, and never sent anywhere except as a signature.

const crypto = require('node:crypto');
const fs = require('node:fs');

const HOSTS = {
  demo: 'https://external-api.demo.kalshi.co',
  prod: 'https://external-api.kalshi.com',
};
const PREFIX = '/trade-api/v2';

class KalshiApi {
  /**
   * @param {object} o
   * @param {'demo'|'prod'} o.env
   * @param {string} o.keyId               API key ID from Kalshi settings
   * @param {string} o.privateKeyPath      path to the downloaded PEM
   */
  constructor({ env = 'demo', keyId, privateKeyPath, log = () => {} } = {}) {
    if (!HOSTS[env]) throw new Error(`unknown env "${env}" (use demo or prod)`);
    this.env = env;
    this.host = HOSTS[env];
    this.keyId = keyId;
    this.log = log;
    this.privateKeyPath = privateKeyPath;
    this._key = null;
  }

  get key() {
    if (this._key) return this._key;
    if (!this.privateKeyPath) throw new Error('no privateKeyPath configured');
    if (!fs.existsSync(this.privateKeyPath)) {
      throw new Error(`private key not found at ${this.privateKeyPath}`);
    }
    const pem = fs.readFileSync(this.privateKeyPath, 'utf8');
    try {
      this._key = crypto.createPrivateKey(pem);
    } catch (err) {
      throw new Error(`could not read private key: ${err.message}`);
    }
    return this._key;
  }

  /** base64 RSA-PSS signature over `${ts}${METHOD}${path}`. */
  sign(method, path, timestampMs) {
    const msg = `${timestampMs}${method.toUpperCase()}${path}`;
    return crypto.sign('sha256', Buffer.from(msg, 'utf8'), {
      key: this.key,
      padding: crypto.constants.RSA_PKCS1_PSS_PADDING,
      saltLength: 32,                       // must equal the SHA-256 digest length
    }).toString('base64');
  }

  headers(method, path) {
    const ts = Date.now().toString();
    return {
      'KALSHI-ACCESS-KEY': this.keyId,
      'KALSHI-ACCESS-TIMESTAMP': ts,
      'KALSHI-ACCESS-SIGNATURE': this.sign(method, path, ts),
      'accept': 'application/json',
      'accept-encoding': 'gzip',            // brotli breaks Node's decoder here
    };
  }

  async request(method, endpoint, { query = null, body = null, timeout = 15000 } = {}) {
    const path = PREFIX + endpoint;                    // signed: no query string
    const url = new URL(this.host + path);
    if (query) {
      for (const [k, v] of Object.entries(query)) {
        if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
      }
    }
    const h = this.headers(method, path);
    if (body) h['content-type'] = 'application/json';

    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeout);
    try {
      const r = await fetch(url, {
        method, headers: h, signal: ctl.signal,
        body: body ? JSON.stringify(body) : undefined,
      });
      const text = await r.text();
      let json = null;
      try { json = text ? JSON.parse(text) : null; } catch { /* keep raw */ }
      if (!r.ok) {
        const code = json && json.error ? json.error.code : r.status;
        const msg = json && json.error ? json.error.message : text.slice(0, 200);
        const e = new Error(`kalshi ${r.status} ${code}: ${msg}`);
        e.status = r.status; e.code = code; e.body = json;
        throw e;
      }
      return json;
    } finally { clearTimeout(timer); }
  }

  // -- reads ----------------------------------------------------------------
  balance() { return this.request('GET', '/portfolio/balance'); }
  positions(q = {}) { return this.request('GET', '/portfolio/positions', { query: q }); }
  fills(q = {}) { return this.request('GET', '/portfolio/fills', { query: q }); }
  orders(q = {}) { return this.request('GET', '/portfolio/orders', { query: q }); }

  // -- orders ---------------------------------------------------------------
  /**
   * Place an order.
   *
   * Kalshi's v2 order endpoint expresses everything as the YES leg:
   *   buying YES  -> side "bid",  price = the YES price you pay
   *   buying NO   -> side "ask",  price = 1 - (the NO price you pay)
   * i.e. buying NO at 30c is offering YES at 70c. Getting this inverted would
   * silently take the opposite side of every trade, so it is done in one place
   * and unit-tested rather than inline at the call sites.
   *
   * `postOnly: true` makes it a MAKER order - it rests rather than crossing.
   * That is the whole point of moving off the phone: the one real edge in the
   * research (the favourite-longshot bias) is the size of the spread, so it
   * pays whoever collects the spread rather than pays it.
   */
  createOrder({ ticker, side, contracts, price, exchangeIndex = 0,
                timeInForce = 'immediate_or_cancel',
                postOnly = false, clientOrderId = null, expirationTime = null }) {
    if (side !== 'yes' && side !== 'no') throw new Error(`bad side "${side}"`);
    if (!(price > 0 && price < 1)) throw new Error(`price ${price} must be between 0 and 1`);
    if (!(contracts >= 1)) throw new Error(`contracts ${contracts} must be >= 1`);

    const yesPrice = side === 'yes' ? price : 1 - price;
    const body = {
      ticker,
      side: side === 'yes' ? 'bid' : 'ask',
      count: Number(contracts).toFixed(2),
      price: yesPrice.toFixed(4),
      time_in_force: timeInForce,
      self_trade_prevention_type: 'taker_at_cross',
      // Required. Omit it, or send the wrong one, and Kalshi answers
      // `404 user_not_found` - which means "no funds on THAT exchange",
      // not "bad credentials".
      exchange_index: exchangeIndex,
    };
    if (postOnly) body.post_only = true;
    if (clientOrderId) body.client_order_id = clientOrderId;
    if (expirationTime) body.expiration_time = expirationTime;
    return this.request('POST', '/portfolio/events/orders', { body });
  }

  /**
   * Cancel a resting order.
   *
   * `DELETE /portfolio/orders/{id}` is the deprecated v1 route and answers
   * `410 deprecated_v1_order_endpoint` - which a smoke test discovered the hard
   * way, leaving a live order on the book. The v2 route mirrors create:
   * `DELETE /portfolio/events/orders/{id}`.
   */
  cancelOrder(orderId, { exchangeIndex = null, ticker = null } = {}) {
    // The routing query params are the whole trick. `DELETE
    // /portfolio/events/orders/{id}` on its own answers `404 not_found` even
    // for a live order, because it does not know which exchange shard to look
    // on. Passing exchange_index (or market_ticker) routes it and it cancels.
    // Query params are excluded from the signature, so adding them is safe.
    const query = {};
    if (exchangeIndex !== null) query.exchange_index = exchangeIndex;
    if (ticker) query.market_ticker = ticker;
    return this.request('DELETE', `/portfolio/events/orders/${encodeURIComponent(orderId)}`, { query });
  }

  /** Cancel every resting order. The panic button. */
  async cancelAllResting() {
    const list = await this.orders({ status: 'resting' });
    const rows = list.orders || [];
    const out = [];
    for (const o of rows) {
      try {
        await this.cancelOrder(o.order_id, { exchangeIndex: o.exchange_index, ticker: o.ticker });
        out.push({ ok: true, orderId: o.order_id, ticker: o.ticker });
      } catch (err) {
        out.push({ ok: false, orderId: o.order_id, ticker: o.ticker, error: err.message });
      }
    }
    return out;
  }

  /** Cheap end-to-end auth check. Returns {ok, balance} or {ok:false, reason}. */
  async verify() {
    try {
      const b = await this.balance();
      const cents = b && (b.balance ?? b.balance_cents);
      return { ok: true, env: this.env, raw: b,
               balance: typeof cents === 'number' ? cents / 100 : null };
    } catch (err) {
      return { ok: false, env: this.env, reason: err.message, code: err.code };
    }
  }
}

module.exports = { KalshiApi, HOSTS, PREFIX };
