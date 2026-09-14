'use strict';
// Live index feed.
//
// These markets settle on CF Benchmarks' Real Time Index (BRTI/ERTI/...), which
// is a composite of a fixed set of constituent exchanges. CF's own API is not
// reachable from this machine, but four of the constituents are: Coinbase,
// Kraken, Bitstamp and Gemini. Observed dispersion between them is ~0.02% of
// spot, against a 15-minute sigma of roughly 0.3% - so a composite tracks the
// real index to well inside a tenth of a standard deviation.
//
// Structure:
//   * Coinbase over websocket is the heartbeat - sub-second, free, no rate limit.
//   * The other three are polled on REST every few seconds only to estimate the
//     slowly-varying BASIS between Coinbase and the composite. Basis moves on a
//     timescale of minutes; price moves on a timescale of milliseconds. Treating
//     them separately means we get websocket latency with composite accuracy.
//
// The feed also owns realised volatility, because sigma has to be measured on
// the same series the pricer is quoting against.

const { EventEmitter } = require('node:events');

const PRODUCTS = {
  BTC: { coinbase: 'BTC-USD', kraken: 'XBTUSD', bitstamp: 'btcusd', gemini: 'btcusd' },
  ETH: { coinbase: 'ETH-USD', kraken: 'ETHUSD', bitstamp: 'ethusd', gemini: 'ethusd' },
  SOL: { coinbase: 'SOL-USD', kraken: 'SOLUSD', bitstamp: 'solusd', gemini: 'solusd' },
  XRP: { coinbase: 'XRP-USD', kraken: 'XRPUSD', bitstamp: 'xrpusd', gemini: 'xrpusd' },
  DOGE: { coinbase: 'DOGE-USD', kraken: 'XDGUSD', bitstamp: 'dogeusd', gemini: 'dogeusd' },
};

const CB_WS = 'wss://ws-feed.exchange.coinbase.com';

// Volatility EWMA half-life. Ten minutes is long enough to be stable across a
// 15-minute window and short enough to react to a regime change within one.
const VOL_HALFLIFE_MS = 10 * 60 * 1000;

// Composite samples retained for the sparse volatility estimator.
const SAMPLE_KEEP_MS = 20 * 60 * 1000;

// Candle lookback for the slow volatility estimator, and the measured factor by
// which it still runs hot. Both come from research/vol_check.py over 100 hours
// of BTC: 30min lookback -> 1.077x realised, 60min -> 1.107x, 120min -> 1.137x.
// An overstated sigma drags probabilities toward 0.5 and invents edge, so this
// is corrected rather than left "conservative".
const CANDLE_LOOKBACK_MIN = 30;
const VOL_BIAS = 1.077;
const ROBUST_RV_LOOKBACK_MIN = 120;

class PriceFeed extends EventEmitter {
  constructor({ symbols = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE'], basisIntervalMs = 6000, log = () => {} } = {}) {
    super();
    this.symbols = symbols.filter((s) => PRODUCTS[s]);
    this.basisIntervalMs = basisIntervalMs;
    this.log = log;
    this.ws = null;
    this.stopped = false;
    this.reconnectDelay = 1000;

    /** @type {Map<string, {price:number, ts:number}>} raw Coinbase last trade */
    this.cb = new Map();
    /** @type {Map<string, {basis:number, ts:number, n:number, spread:number}>} */
    this.basis = new Map();
    /** @type {Map<string, {varPerMs:number, last:{p:number,ts:number}|null, samples:number}>} */
    this.vol = new Map();
    /** Rolling 1-second samples of the composite, for the settlement average. */
    this.samples = new Map();

    for (const s of this.symbols) {
      this.vol.set(s, {
        varPerMs: 0, candleVarPerMs: 0, last: null, samples: 0,
        rv120PerSqrtSec: null, rv120Samples: 0, rv120At: 0,
      });
      this.samples.set(s, []);
    }
  }

  start() {
    this.stopped = false;
    this._connect();
    this._basisTimer = setInterval(() => this._pollBasis().catch(() => {}), this.basisIntervalMs);
    this._sampleTimer = setInterval(() => this._sample(), 1000);
    // Re-anchor the slow estimator so a regime change is not permanently
    // averaged against two hours of stale candles.
    this._volTimer = setInterval(() => {
      for (const s of this.symbols) this.warmup(s).catch(() => {});
    }, 5 * 60 * 1000);
    this._pollBasis().catch(() => {});
    return this;
  }

  stop() {
    this.stopped = true;
    clearInterval(this._basisTimer);
    clearInterval(this._sampleTimer);
    clearInterval(this._volTimer);
    if (this.ws) { try { this.ws.close(); } catch {} }
    this.ws = null;
  }

  // -- Coinbase websocket ---------------------------------------------------

  _connect() {
    if (this.stopped) return;
    const products = this.symbols.map((s) => PRODUCTS[s].coinbase);
    let ws;
    try {
      ws = new WebSocket(CB_WS);      // global WebSocket, Node 22+
    } catch (err) {
      this.log('feed', `ws construct failed: ${err.message}`);
      return this._scheduleReconnect();
    }
    this.ws = ws;

    ws.addEventListener('open', () => {
      this.reconnectDelay = 1000;
      ws.send(JSON.stringify({ type: 'subscribe', product_ids: products, channels: ['ticker'] }));
      this.log('feed', `coinbase ws open (${products.join(', ')})`);
      this.emit('open');
    });

    ws.addEventListener('message', (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type !== 'ticker' || !msg.product_id || !msg.price) return;
      const sym = this.symbols.find((s) => PRODUCTS[s].coinbase === msg.product_id);
      if (!sym) return;
      const price = Number(msg.price);
      if (!Number.isFinite(price) || price <= 0) return;
      const ts = msg.time ? Date.parse(msg.time) : Date.now();
      this.cb.set(sym, { price, ts });
      this._updateVol(sym, this.composite(sym), ts);
      this.emit('tick', { symbol: sym, price: this.composite(sym), ts });
    });

    const bounce = () => {
      if (this.ws === ws) this.ws = null;
      this._scheduleReconnect();
    };
    ws.addEventListener('close', bounce);
    ws.addEventListener('error', () => { try { ws.close(); } catch {} });
  }

  _scheduleReconnect() {
    if (this.stopped) return;
    const delay = this.reconnectDelay;
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
    this.log('feed', `coinbase ws reconnect in ${delay}ms`);
    setTimeout(() => this._connect(), delay);
  }

  // -- Composite basis ------------------------------------------------------

  async _pollBasis() {
    for (const sym of this.symbols) {
      const p = PRODUCTS[sym];
      const quotes = [];
      const cbq = this.cb.get(sym);
      if (cbq) quotes.push(cbq.price);

      const results = await Promise.allSettled([
        this._kraken(p.kraken),
        this._bitstamp(p.bitstamp),
        this._gemini(p.gemini),
      ]);
      for (const r of results) {
        if (r.status === 'fulfilled' && Number.isFinite(r.value) && r.value > 0) quotes.push(r.value);
      }
      if (quotes.length < 2 || !cbq) continue;

      // Median is the right centre here: one venue printing a stale or wide
      // quote should not drag the index the way a mean would.
      const composite = median(quotes);
      const spread = (Math.max(...quotes) - Math.min(...quotes)) / composite;
      const raw = composite - cbq.price;

      // Smooth the basis hard. It is a slow variable and a noisy estimate of it
      // would inject fake volatility straight into the pricer.
      const prev = this.basis.get(sym);
      const basis = prev ? prev.basis * 0.7 + raw * 0.3 : raw;
      this.basis.set(sym, { basis, ts: Date.now(), n: quotes.length, spread });
    }
  }

  async _json(url, timeoutMs = 4000) {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const r = await fetch(url, { signal: ctl.signal, headers: { 'accept-encoding': 'gzip' } });
      if (!r.ok) throw new Error(String(r.status));
      return await r.json();
    } finally { clearTimeout(t); }
  }

  async _kraken(pair) {
    const j = await this._json(`https://api.kraken.com/0/public/Ticker?pair=${pair}`);
    const key = Object.keys(j.result || {})[0];
    if (!key) return null;
    const t = j.result[key];
    return (Number(t.a[0]) + Number(t.b[0])) / 2;
  }

  async _bitstamp(pair) {
    const j = await this._json(`https://www.bitstamp.net/api/v2/ticker/${pair}/`);
    return (Number(j.bid) + Number(j.ask)) / 2;
  }

  async _gemini(pair) {
    const j = await this._json(`https://api.gemini.com/v1/pubticker/${pair}`);
    return (Number(j.bid) + Number(j.ask)) / 2;
  }

  // -- Public reads ---------------------------------------------------------

  /** Best current estimate of the settlement index for `symbol`. */
  composite(symbol) {
    const cbq = this.cb.get(symbol);
    if (!cbq) return null;
    const b = this.basis.get(symbol);
    return cbq.price + (b ? b.basis : 0);
  }

  /**
   * Volatility used for pricing, per sqrt(second).
   *
   * Two independent estimators, both deliberately low-frequency:
   *   * sparse 30s returns off the live composite - current, reacts within
   *     minutes, and largely free of bid-ask bounce
   *   * 1-minute candles over the last two hours - stable, slow, refreshed
   *     periodically, and the only thing available at boot
   *
   * The smaller of the two is used. Overstating sigma manufactures fake edge
   * (it drags probabilities toward 0.5, so every out-of-the-money contract
   * looks mispriced), whereas understating it merely makes the bot decline
   * trades. Given which error costs money, take the low one.
   */
  sigmaFor(symbol) {
    const sparseVar = this.sparseVariancePerMs(symbol, 30000);
    const v = this.vol.get(symbol);
    const candleVar = v && v.candleVarPerMs > 0 ? v.candleVarPerMs : null;
    const cands = [sparseVar, candleVar].filter((x) => x && x > 0);
    if (!cands.length) return null;
    return Math.sqrt(Math.min(...cands) * 1000);
  }

  quote(symbol) {
    const price = this.composite(symbol);
    if (price === null) return null;
    const cbq = this.cb.get(symbol);
    const b = this.basis.get(symbol);
    const v = this.vol.get(symbol);
    const arr = this.samples.get(symbol) || [];
    return {
      symbol,
      price,
      ts: cbq.ts,
      ageMs: Date.now() - cbq.ts,
      venues: b ? b.n : 1,
      venueSpread: b ? b.spread : null,
      sigmaPerSqrtSec: this.sigmaFor(symbol),
      sigmaSparse: (() => { const s = this.sparseVariancePerMs(symbol, 30000); return s ? Math.sqrt(s * 1000) : null; })(),
      sigmaCandle: v && v.candleVarPerMs > 0 ? Math.sqrt(v.candleVarPerMs * 1000) : null,
      rv120PerSqrtSec: v ? v.rv120PerSqrtSec : null,
      rv120Samples: v ? v.rv120Samples : 0,
      rv120AgeMs: v && v.rv120At ? Date.now() - v.rv120At : null,
      volSamples: arr.length,
    };
  }

  /**
   * Mean composite price over the last `windowMs`. Settlement is the average of
   * the final 60 seconds, so once we are inside that window the realised part
   * of the average is knowable rather than merely forecastable.
   */
  trailingMean(symbol, windowMs) {
    const arr = this.samples.get(symbol) || [];
    const cutoff = Date.now() - windowMs;
    const use = arr.filter((s) => s.ts >= cutoff);
    if (!use.length) return null;
    return { mean: use.reduce((a, s) => a + s.p, 0) / use.length, n: use.length };
  }

  _sample() {
    const now = Date.now();
    for (const sym of this.symbols) {
      const p = this.composite(sym);
      if (p === null) continue;
      const arr = this.samples.get(sym);
      arr.push({ p, ts: now });
      // Twenty minutes, because this buffer is also the volatility estimator's
      // input and sparse sampling needs a long enough window to have samples.
      const cutoff = now - SAMPLE_KEEP_MS;
      while (arr.length && arr[0].ts < cutoff) arr.shift();
    }
  }

  /**
   * Realised volatility from SPARSELY sampled returns.
   *
   * Tick-to-tick returns are the obvious thing to use and they are badly wrong.
   * Consecutive trade prints bounce between bid and ask, so a large part of a
   * high-frequency return is microstructure noise rather than volatility. That
   * noise contributes a roughly fixed variance per observation, so measuring at
   * 30-second spacing instead of 300-millisecond spacing shrinks its share by
   * two orders of magnitude while leaving the true diffusion untouched.
   *
   * This matters more than it sounds: overstated sigma pulls every probability
   * toward 0.5, which invents disagreement with the market on exactly the
   * contracts that are furthest from the money - and the bot reads invented
   * disagreement as edge. An inflated sigma is a money-losing bug, not a
   * cosmetic one.
   *
   * Returns overlap, which leaves the estimate unbiased but correlated. That is
   * the right trade here: we want a stable number, not an independent one.
   */
  sparseVariancePerMs(symbol, lagMs = 30000) {
    const arr = this.samples.get(symbol) || [];
    if (arr.length < 60) return null;
    const byTs = arr;
    let sum = 0, n = 0;
    // Samples land ~1s apart, so index arithmetic is a good enough lookup and
    // avoids a binary search per observation.
    const lagIdx = Math.round(lagMs / 1000);
    for (let i = lagIdx; i < byTs.length; i++) {
      const a = byTs[i - lagIdx], b = byTs[i];
      const dt = b.ts - a.ts;
      if (dt < lagMs * 0.6 || dt > lagMs * 1.6) continue;
      if (!(a.p > 0 && b.p > 0)) continue;
      const r = Math.log(b.p / a.p);
      sum += (r * r) / dt;
      n++;
    }
    if (n < 20) return null;
    return sum / n;
  }

  /**
   * EWMA of variance per millisecond, from log returns of the composite.
   *
   * Scaling by dt matters: websocket ticks arrive irregularly, and treating an
   * 8 ms gap and an 800 ms gap as the same observation would badly misestimate
   * sigma. Very short gaps are skipped because at that scale you are measuring
   * bid-ask bounce, not volatility.
   */
  _updateVol(symbol, price, ts) {
    if (price === null || !Number.isFinite(price)) return;
    const v = this.vol.get(symbol);
    if (!v) return;
    const last = v.last;
    v.last = { p: price, ts };
    if (!last) return;
    const dt = ts - last.ts;
    if (dt < 250 || dt > 120000) return;
    const r = Math.log(price / last.p);
    const instVarPerMs = (r * r) / dt;
    const lambda = Math.pow(0.5, dt / VOL_HALFLIFE_MS);
    v.varPerMs = v.varPerMs === 0 ? instVarPerMs : lambda * v.varPerMs + (1 - lambda) * instVarPerMs;
    v.samples += 1;
  }

  /** Seed sigma from recent 1-minute history so the bot is not blind at boot. */
  async warmup(symbol) {
    const prod = PRODUCTS[symbol] && PRODUCTS[symbol].coinbase;
    if (!prod) return null;
    try {
      const j = await this._json(
        `https://api.exchange.coinbase.com/products/${prod}/candles?granularity=60`, 8000);
      if (!Array.isArray(j) || j.length < 20) return null;
      // Coinbase includes the currently forming candle.  It was a fatal source
      // of look-ahead in an earlier backtest, so retain only candles whose full
      // minute has ended before this read.
      const now = Date.now();
      const complete = j.filter((row) => Number(row[0]) * 1000 + 60000 <= now);
      if (complete.length < 20) return null;
      // [time, low, high, open, close, volume], newest first.
      //
      // Thirty minutes, not two hours. Measured against 100 hours of realised
      // 15-minute BTC moves, a 30-minute lookback overstates sigma by 7.7%
      // while a 120-minute lookback overstates it by 13.7% - the longer window
      // keeps averaging in volatility that has already passed.
      const closes = complete.slice(0, CANDLE_LOOKBACK_MIN + 1).map((r) => Number(r[4])).reverse();
      let sum = 0, n = 0;
      for (let i = 1; i < closes.length; i++) {
        if (!(closes[i] > 0 && closes[i - 1] > 0)) continue;
        const r = Math.log(closes[i] / closes[i - 1]);
        sum += r * r; n++;
      }
      if (!n) return null;
      // Empirical bias correction, from research/vol_check: this estimator
      // predicts a 15-minute sigma 7.7% larger than the one that actually
      // realises. Sigma enters the pricer through the variance, so the
      // correction is applied to the variance as the square.
      const varPerMin = (sum / n) / (VOL_BIAS ** 2);
      const varPerMs = varPerMin / 60000;
      const v = this.vol.get(symbol);
      if (v) { v.candleVarPerMs = varPerMs; v.candleSamples = n; v.candleAt = Date.now(); }

      // Exact estimator selected by the robust IV/RV study: sample standard
      // deviation of 120 completed one-minute log returns, per sqrt(second).
      if (v && complete.length >= ROBUST_RV_LOOKBACK_MIN + 1) {
        const c120 = complete.slice(0, ROBUST_RV_LOOKBACK_MIN + 1)
          .map((row) => Number(row[4])).reverse();
        const returns = [];
        for (let i = 1; i < c120.length; i++) {
          if (c120[i] > 0 && c120[i - 1] > 0) returns.push(Math.log(c120[i] / c120[i - 1]));
        }
        if (returns.length === ROBUST_RV_LOOKBACK_MIN) {
          const mean = returns.reduce((sum, value) => sum + value, 0) / returns.length;
          const variancePerMinute = returns.reduce((sum, value) => sum + (value - mean) ** 2, 0) /
            (returns.length - 1);
          v.rv120PerSqrtSec = Math.sqrt(variancePerMinute / 60);
          v.rv120Samples = returns.length;
          v.rv120At = now;
        }
      }
      return Math.sqrt(varPerMs * 1000);
    } catch { return null; }
  }

  ready(symbol) {
    const q = this.quote(symbol);
    return !!(q && q.sigmaPerSqrtSec && q.ageMs < 15000);
  }
}

function median(xs) {
  const a = [...xs].sort((x, y) => x - y);
  const m = a.length >> 1;
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}

module.exports = { PriceFeed, PRODUCTS };
