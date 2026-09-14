'use strict';
// The bot loop.
//
// Per tick, per enabled series:
//   1. find the live 15-minute window and its published strike
//   2. read the book and the composite index
//   3. price the contract  (model.fairValue)
//   4. compare against what the book actually charges, net of fee
//   5. gate on data health, then on risk, then execute
//
// It records a forecast for EVERY window whether or not it trades, because the
// scoreboard that matters is Brier score against the market's own price, and
// that record has to accumulate even while the bot is refusing to trade.
// Snapshotting the market price at forecast time is essential: it cannot be
// recovered afterwards.

const { EventEmitter } = require('node:events');
const kalshi = require('./kalshi');
const { fairValue, decisionProbabilities, evaluate, kellyFraction } = require('./model');
const { robustRvSignal } = require('./strategies/robustRv');
const { PriceFeed } = require('./feed');
const { Risk } = require('./risk');
const { EngineLock } = require('./lock');

class Engine extends EventEmitter {
  constructor({ cfg, store, executor }) {
    super();
    this.cfg = cfg;
    this.store = store;
    this.executor = executor;
    const realMoney = ['live', 'api', 'api-live'].includes(cfg.mode);
    this.risk = new Risk(cfg, store, (s, m) => this.log(s, m), { requireLive: realMoney });
    this.realMoney = realMoney;
    this.live = null;            // last live account read from the exchange
    this.enabled = cfg.series.filter((s) => s.enabled);
    this.feed = new PriceFeed({
      symbols: this.enabled.map((s) => s.symbol),
      log: (s, m) => this.log(s, m),
    });
    this.markets = new Map();      // series ticker -> cached live market
    this.lastView = new Map();     // series ticker -> latest model/market view
    this.signalRing = new Map();   // ticker -> most recent decision + why
    this.running = false;
    this.lastEquityAt = 0;
    this.tickCount = 0;
    this.lastError = null;
  }

  // Emit only. Whoever owns the engine decides where lines get printed - doing
  // it here as well as in the owner's handler printed everything twice.
  log(source, message) {
    try { this.store.logEvent('info', source, message); } catch {}
    this.emit('log', { source, message, ts: Date.now() });
  }

  async start({ label = 'cli' } = {}) {
    if (this.running) return;

    this.lock = new EngineLock(this.store, label);
    const got = this.lock.acquire();
    if (!got.ok) {
      this.log('engine', `refusing to start: ${got.reason}`);
      throw new Error(got.reason);
    }

    this.running = true;
    this.log('engine', `starting in ${this.cfg.mode.toUpperCase()} mode, bankroll $${this.cfg.bankroll}`);
    this.log('engine', `series: ${this.enabled.map((s) => s.ticker).join(', ')}`);

    const rd = await this.executor.ready();
    if (!rd.ok) {
      this.log('engine', `executor not ready: ${rd.reason}`);
      if (this.cfg.mode === 'live') {
        this.log('engine', 'refusing to start in live mode without a working executor');
        this.running = false;
        this.lock.release();
        return;
      }
    }

    this.feed.start();
    await Promise.all(this.enabled.map((s) => this.feed.warmup(s.symbol)));
    this.log('feed', 'warmed up realised volatility from 1-minute history');

    this.timer = setInterval(() => {
      this.tick().catch((err) => {
        this.lastError = err.message;
        this.log('engine', `tick error: ${err.message}`);
      });
    }, this.cfg.loop.tickMs);
    this.tick().catch(() => {});
  }

  async stop() {
    this.running = false;
    clearInterval(this.timer);
    this.feed.stop();
    await this.executor.close();
    if (this.lock) this.lock.release();
    this.log('engine', 'stopped');
  }

  async tick() {
    if (!this.running) return;
    this.tickCount++;

    // Refresh the exchange's own view BEFORE deciding anything. In real-money
    // mode a failed read stops trading for this tick rather than falling back
    // to local state - local state is precisely what was wrong last time.
    if (this.realMoney && typeof this.executor.liveState === 'function') {
      this.live = await this.executor.liveState();
      if (!this.live.ok) {
        this.log('engine', `live account read failed: ${this.live.reason} - skipping tick`);
        return;
      }
      await this.reconcile();
      if (this.risk.killed()) return;
    }
    for (const s of this.enabled) {
      try {
        await this.handleSeries(s);
      } catch (err) {
        this.log('engine', `${s.ticker}: ${err.message}`);
      }
    }
    await this.settleClosed();
    await this.scoreForecasts();
    this.maybeSnapshotEquity();
  }

  /** Cache the live market per series; refresh when the window rolls over. */
  async liveMarket(seriesTicker) {
    const cached = this.markets.get(seriesTicker);
    const now = Date.now();
    if (cached && cached.closeTime && cached.closeTime > now + 2000) return cached;
    const m = await kalshi.currentMarket(seriesTicker);
    if (m) {
      if (!cached || cached.ticker !== m.ticker) {
        this.log('engine', `${seriesTicker}: new window ${m.ticker} strike ${m.strike} closes ${new Date(m.closeTime).toISOString()}`);
      }
      this.markets.set(seriesTicker, m);
    }
    return m;
  }

  async handleSeries(s) {
    const market = await this.liveMarket(s.ticker);
    if (!market || !market.strike || !market.closeTime) return;

    const now = Date.now();
    const secondsLeft = (market.closeTime - now) / 1000;
    if (secondsLeft <= 0) return;

    const q = this.feed.quote(s.symbol);
    if (!q) return;

    if (this.cfg.strategy === 'robust-rv') {
      return this.handleRobustRv({ s, market, q, secondsLeft });
    }

    const sig = this.cfg.signal;
    const trailing = secondsLeft <= 60 ? this.feed.trailingMean(s.symbol, (60 - secondsLeft) * 1000) : null;

    const fvArgs = {
      price: q.price,
      strike: market.strike,
      secondsLeft,
      sigmaPerSqrtSec: q.sigmaPerSqrtSec,
      trailingMean: trailing,
      trackingErrorFrac: sig.trackingErrorFrac,
    };
    const fv = fairValue(fvArgs);
    if (!fv) return;
    this.lastView.set(s.ticker, {
      modelP: fv.p,
      spot: q.price,
      sigma: q.sigmaPerSqrtSec,
      at: Date.now(),
    });
    // Belief for the record; deliberately pessimistic numbers for the decision.
    const dp = decisionProbabilities({ ...fvArgs, adverseSigmas: sig.adverseSigmas ?? 2 });
    if (!dp) return;

    // One forecast per window, taken at a consistent point so the Brier record
    // is comparable across windows rather than sampled wherever the loop landed.
    //
    // The market price MUST be fetched live here. `market` is cached from when
    // the window opened, and on a 15-minute crypto contract that quote is ~2
    // minutes stale by now - measured at a mean of 12.6c away from the live
    // book, wrong by >3c 86% of the time. Scoring a live model against a stale
    // quote manufactured a +10.5% Brier "skill" that was really -1.2%. The
    // comparison is only honest if both sides are read at the same instant.
    if (secondsLeft <= sig.maxSecondsLeft && secondsLeft > sig.maxSecondsLeft - 30) {
      let mid = null;
      try {
        const fb = await kalshi.orderbook(market.ticker);
        if (fb.bestYesBid != null && fb.bestYesAsk != null) {
          mid = (fb.bestYesBid + fb.bestYesAsk) / 2;
        }
      } catch { /* skip this window's forecast rather than record a stale one */ }
      if (mid != null) {
        this.store.recordForecast({
          ticker: market.ticker, series: s.ticker, secondsLeft,
          modelP: fv.p, marketP: mid, spot: q.price, strike: market.strike,
          sigma: q.sigmaPerSqrtSec, closeTs: Math.floor(market.closeTime / 1000),
        });
      }
    }

    // ---- data-health gates. Refusing to trade on a stale or disagreeing feed
    // matters more than any of the others: a bad index makes the model MORE
    // confident, not less, and that is precisely how it loses money.
    const reject = (reason) => {
      this.noteSignal({ ticker: market.ticker, reason, modelP: fv.p, secondsLeft });
      return null;
    };
    // One position per window. Without this the loop re-buys the same view
    // every tick, turning a single 2c edge into eight stacked bets at 2s apart.
    const already = this.store.get(
      `SELECT COUNT(*) AS n FROM positions WHERE ticker = $t AND mode = $m`,
      { t: market.ticker, m: this.cfg.mode });
    if (already && already.n > 0) return reject('already traded this window');

    if (secondsLeft < sig.minSecondsLeft) return reject('too close to close');
    if (secondsLeft > sig.maxSecondsLeft) return reject('too early in window');
    if (q.ageMs > sig.maxIndexAgeMs) return reject(`index stale (${q.ageMs}ms)`);
    if (q.volSamples < sig.requireVolSamples) return reject('volatility not warmed up');
    if (q.venueSpread != null && q.venueSpread > sig.maxVenueSpread) {
      return reject(`venues disagree (${(q.venueSpread * 100).toFixed(3)}%)`);
    }

    const book = await kalshi.orderbook(market.ticker);
    if (book.bestYesAsk == null && book.bestNoAsk == null) return reject('empty book');
    if (book.bestYesBid != null && book.bestYesAsk != null) {
      const spread = book.bestYesAsk - book.bestYesBid;
      if (spread > sig.maxSpread) return reject(`spread ${spread.toFixed(4)} too wide`);
    }

    // Size first at 1 contract to get a direction, then let risk re-price the
    // fee at the real size - the ceil-to-cent makes per-contract fee depend on
    // the quantity, so edge must be re-checked at the size actually sent.
    const probe = evaluate({
      pYes: dp.yes, pNo: dp.no,
      yesAsk: book.bestYesAsk, noAsk: book.bestNoAsk,
      contracts: 1, feeCoef: kalshi.FEE_COEF,
      minPrice: sig.minPrice ?? 0, maxPrice: sig.maxPrice ?? 1,
    });
    if (!probe) return reject('no side inside the tradable price band');

    const asks = probe.side === 'yes' ? book.yesAsks : book.noAsks;
    const topDepth = asks.length ? asks[0].size : 0;
    if (topDepth < sig.minBookSize) return reject(`thin book (${topDepth})`);

    const decision = this.risk.check({
      side: probe.side,
      price: probe.price,
      prob: probe.prob,
      kelly: probe.kelly,
      bookDepth: topDepth,
      live: this.live,
    });

    const signalId = this.store.insertSignal({
      ts: now, series: s.ticker, ticker: market.ticker, symbol: s.symbol,
      spot: q.price, strike: market.strike, secondsLeft, sigma: q.sigmaPerSqrtSec,
      modelP: fv.p,
      yesBid: book.bestYesBid, yesAsk: book.bestYesAsk,
      noBid: book.bestNoBid, noAsk: book.bestNoAsk,
      mid: book.bestYesBid != null && book.bestYesAsk != null
        ? (book.bestYesBid + book.bestYesAsk) / 2 : null,
      side: probe.side, edge: probe.edge, kelly: probe.kelly,
      contracts: decision.ok ? decision.contracts : 0,
      acted: decision.ok ? 1 : 0,
      reason: decision.ok ? 'trade' : decision.reason,
    });

    this.noteSignal({
      ticker: market.ticker, modelP: fv.p, side: probe.side, edge: probe.edge,
      secondsLeft, reason: decision.ok ? 'trade' : decision.reason,
    });

    if (!decision.ok) return;

    // Re-sweep the book at the real size so the limit price reflects what we
    // would actually pay walking the ladder, not just the top level.
    const swept = kalshi.sweep(asks, decision.contracts);
    if (!swept || !swept.complete) {
      this.store.insertOrder({
        signalId, ticker: market.ticker, series: s.ticker, side: probe.side,
        contracts: decision.contracts, mode: this.cfg.mode, status: 'aborted',
        error: 'book could not fill the full size',
      });
      return;
    }

    await this.execute({ signalId, market, series: s, side: probe.side, decision, swept, fv, book });
  }

  /** Frozen robust IV/RV strategy from the nine-week chronological study. */
  async handleRobustRv({ s, market, q, secondsLeft }) {
    const sig = this.cfg.signal;
    const reject = (reason, extra = {}) => {
      this.noteSignal({ ticker: market.ticker, reason, secondsLeft, ...extra });
      return null;
    };
    const target = sig.targetSecondsLeft ?? 360;
    const window = sig.entryWindowSeconds ?? 10;
    // Enter only after minute 9 has completed, never before it.
    if (secondsLeft > target || secondsLeft <= target - window) {
      return reject(secondsLeft > target ? 'waiting for frozen minute-9 entry' : 'minute-9 entry window passed');
    }
    const already = this.store.get(
      `SELECT COUNT(*) AS n FROM positions WHERE ticker = $t AND mode = $m`,
      { t: market.ticker, m: this.cfg.mode });
    if (already && already.n > 0) return reject('already traded this window');
    if (q.ageMs > sig.maxIndexAgeMs) return reject(`index stale (${q.ageMs}ms)`);
    if (q.venueSpread != null && q.venueSpread > sig.maxVenueSpread) {
      return reject(`venues disagree (${(q.venueSpread * 100).toFixed(3)}%)`);
    }
    if (!(q.rv120Samples >= (sig.requireRv120Samples ?? 120)) || !(q.rv120PerSqrtSec > 0)) {
      return reject(`120-minute RV not ready (${q.rv120Samples || 0} samples)`);
    }
    if (q.rv120AgeMs == null || q.rv120AgeMs > (sig.maxRvAgeMs ?? 360000)) {
      return reject('120-minute RV is stale');
    }

    const book = await kalshi.orderbook(market.ticker);
    if (book.bestYesBid == null || book.bestYesAsk == null) return reject('two-sided book unavailable');
    const spread = book.bestYesAsk - book.bestYesBid;
    if (spread > (sig.maxSpread ?? 1)) return reject(`spread ${spread.toFixed(4)} too wide`);
    const rv = robustRvSignal({
      spot: q.price, strike: market.strike, secondsLeft,
      rvRelativePerSqrtSec: q.rv120PerSqrtSec,
      yesBid: book.bestYesBid, yesAsk: book.bestYesAsk,
      threshold: sig.ivRvThreshold ?? 2,
      minAbsZ: sig.minAbsZ ?? 0.15,
    });
    if (!rv.ok) return reject(rv.reason, { ratio: rv.ratio });

    const asks = rv.side === 'yes' ? book.yesAsks : book.noAsks;
    const topDepth = asks.length ? asks[0].size : 0;
    if (topDepth < (sig.minBookSize ?? 1)) return reject(`thin book (${topDepth})`, { ratio: rv.ratio });

    // The rule selects a side but does not claim a per-trade probability.  Use
    // its conservative TRAIN net edge solely for sizing; the separate config
    // hard-caps size at one contract, so this cannot leverage a noisy estimate.
    const oneFee = kalshi.tradingFee(1, rv.paid);
    const costPer = rv.paid + oneFee;
    const assumedNetEdge = sig.assumedNetEdge ?? 0.0187;
    const sideProb = Math.min(0.999, costPer + assumedNetEdge);
    const decision = this.risk.check({
      side: rv.side, price: rv.paid, prob: sideProb,
      kelly: kellyFraction(sideProb, costPer), bookDepth: topDepth, live: this.live,
    });
    const yesProb = rv.side === 'yes' ? sideProb : 1 - sideProb;
    const now = Date.now();
    const signalId = this.store.insertSignal({
      ts: now, series: s.ticker, ticker: market.ticker, symbol: s.symbol,
      spot: q.price, strike: market.strike, secondsLeft, sigma: q.rv120PerSqrtSec,
      modelP: yesProb, yesBid: book.bestYesBid, yesAsk: book.bestYesAsk,
      noBid: book.bestNoBid, noAsk: book.bestNoAsk,
      mid: rv.mid, side: rv.side, edge: assumedNetEdge,
      kelly: kellyFraction(sideProb, costPer), contracts: decision.ok ? decision.contracts : 0,
      acted: decision.ok ? 1 : 0,
      reason: decision.ok ? `trade IV/RV=${rv.ratio.toFixed(3)}` : decision.reason,
    });
    this.lastView.set(s.ticker, { modelP: yesProb, spot: q.price,
      sigma: q.rv120PerSqrtSec, ratio: rv.ratio, at: now });
    this.noteSignal({ ticker: market.ticker, side: rv.side, edge: assumedNetEdge,
      ratio: rv.ratio, secondsLeft, reason: decision.ok ? 'trade' : decision.reason });
    if (!decision.ok) return;

    const swept = kalshi.sweep(asks, decision.contracts);
    if (!swept || !swept.complete) return reject('book could not fill one contract', { ratio: rv.ratio });
    await this.execute({ signalId, market, series: s, side: rv.side, decision, swept,
      fv: { p: yesProb }, book });
  }

  async execute({ signalId, market, series, side, decision, swept, fv, book }) {
    this.log('engine',
      `ORDER ${side} ${decision.contracts} ${market.ticker} @ ${swept.avgPrice.toFixed(4)} ` +
      `(model ${fv.p.toFixed(3)}, edge ${decision.netEdge.toFixed(4)})`);

    const orderId = this.store.insertOrder({
      signalId, ticker: market.ticker, series: series.ticker, side,
      contracts: decision.contracts, limitPrice: swept.avgPrice,
      mode: this.cfg.mode, status: 'pending',
    });

    let res;
    try {
      res = await this.executor.place({
        ticker: market.ticker, side, contracts: decision.contracts,
        limitPrice: swept.avgPrice, series: series.ticker,
        // The app's search results are identified by strike, not ticker.
        strike: market.strike,
        exchangeIndex: market.exchangeIndex,
      });
    } catch (err) {
      res = { status: 'failed', error: err.message };
    }

    this.store.updateOrder(orderId, {
      status: res.status,
      fillPrice: res.fillPrice ?? null,
      fee: res.fee ?? null,
      notional: res.notional ?? null,
      error: res.error ?? null,
      evidence: res.evidence ?? null,
      latencyMs: res.latencyMs ?? null,
    });

    if (res.status !== 'filled') {
      this.log('engine', `order ${orderId} ${res.status}: ${res.error || ''}`);
      // An executor that failed mid-flow may or may not have placed an order.
      // Stop rather than risk stacking duplicates on an unknown state. This
      // applies to every real-money path, not just the phone: an API request
      // that times out after leaving the machine is exactly as ambiguous as a
      // tap that did not visibly confirm.
      //
      // A resting maker order is NOT ambiguous - it is a known, intended state.
      const realMoney = ['live', 'api', 'api-live'].includes(this.cfg.mode);
      const restingMaker = this.cfg.api && this.cfg.api.postOnly
        && /resting/i.test(res.error || '');
      if (realMoney && res.status === 'pending' && !restingMaker) {
        this.risk.kill(`unconfirmed order ${orderId} (${this.cfg.mode}) - ` +
          'reconcile the account before restarting');
      }
      return;
    }

    const fee = res.fee ?? kalshi.tradingFee(decision.contracts, res.fillPrice);
    const mid = book.bestYesBid != null && book.bestYesAsk != null
      ? (book.bestYesBid + book.bestYesAsk) / 2 : null;
    this.store.openPosition({
      ticker: market.ticker, series: series.ticker, side,
      contracts: decision.contracts, avgPrice: res.fillPrice, fee,
      openedAt: Date.now(), closeTs: Math.floor(market.closeTime / 1000),
      modelP: side === 'yes' ? fv.p : 1 - fv.p,
      marketP: mid == null ? null : (side === 'yes' ? mid : 1 - mid),
      mode: this.cfg.mode,
    });
  }

  /**
   * Compare the exchange against our own books and STOP on disagreement.
   *
   * The account drained because the bot believed it held nothing while the
   * exchange held real positions. Any gap between those two views is now a
   * halt, not a warning: if the two disagree, the exchange is right and our
   * risk arithmetic is meaningless until a human looks.
   */
  async reconcile() {
    if (!this.live || !this.live.ok) return;
    const ours = this.store.openPositions(this.cfg.mode).length;
    const theirs = this.live.openCount ?? 0;
    if (theirs !== ours) {
      this.risk.kill(
        `reconciliation mismatch: exchange shows ${theirs} open position(s), ` +
        `our book shows ${ours}. Halting - the exchange is right.`);
      this.log('engine', `RECONCILE MISMATCH exchange=${theirs} local=${ours}`);
      return;
    }
    const bal = this.live.shardBalance ?? this.live.balance;
    const floor = this.cfg.risk.minBalance ?? 0;
    if (bal != null && bal <= floor) {
      this.risk.kill(`live balance $${bal.toFixed(2)} at or below floor $${floor.toFixed(2)}`);
    }
  }

  /** Settle any open position whose market has closed and resolved. */
  async settleClosed() {
    const open = this.store.openPositions(this.cfg.mode)
      .filter((p) => p.close_ts && p.close_ts * 1000 < Date.now() - 20000);
    for (const p of open) {
      let result = null;
      try {
        const body = await kalshi.req('/markets', { tickers: p.ticker, limit: 1 });
        const m = (body.markets || [])[0];
        result = m && (m.result === 'yes' || m.result === 'no') ? m.result : null;
      } catch { continue; }
      if (!result) continue;

      // A winning contract pays $1; a loser pays nothing. The entry fee is
      // already spent either way. Settlement itself is free on Kalshi.
      const won = result === p.side;
      const gross = won ? p.contracts * (1 - p.avg_price) : -p.contracts * p.avg_price;
      const pnl = gross - (p.fee || 0);
      this.store.settlePosition(p.id, result, pnl);
      this.log('engine',
        `SETTLED ${p.ticker} ${p.side} x${p.contracts} -> ${result} ${won ? 'WIN' : 'LOSS'} ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}`);
    }
  }

  async scoreForecasts() {
    const pending = this.store.unscoredForecasts(Date.now() - 20000);
    for (const f of pending.slice(0, 10)) {
      try {
        const body = await kalshi.req('/markets', { tickers: f.ticker, limit: 1 });
        const m = (body.markets || [])[0];
        if (m && (m.result === 'yes' || m.result === 'no')) this.store.scoreForecast(f.id, m.result);
      } catch { /* try again next tick */ }
    }
  }

  maybeSnapshotEquity() {
    const now = Date.now();
    if (now - this.lastEquityAt < this.cfg.loop.equitySnapshotMs) return;
    this.lastEquityAt = now;
    const realised = this.risk.lifetimeRealised();
    const exposure = this.risk.openExposure();
    const cash = this.cfg.bankroll + realised - exposure;
    this.store.recordEquity({
      ts: now, cash, exposure, realised,
      total: this.cfg.bankroll + realised, mode: this.cfg.mode,
    });
  }

  /**
   * Remember why each window was passed over. The rejection reason is the most
   * useful thing on the tracker: a bot that never trades looks broken, and this
   * is what distinguishes "no edge today" from "the feed is down".
   */
  noteSignal(sig) {
    this.signalRing.set(sig.ticker, { ...sig, at: Date.now() });
    if (this.signalRing.size > 40) {
      const oldest = [...this.signalRing.entries()].sort((a, b) => a[1].at - b[1].at)[0];
      if (oldest) this.signalRing.delete(oldest[0]);
    }
    this.emit('signal', sig);
  }

  /** Everything the tracker needs, in one read. */
  snapshot() {
    const summary = this.store.summary(this.cfg.mode);
    const quotes = {};
    for (const s of this.enabled) quotes[s.symbol] = this.feed.quote(s.symbol);
    const markets = {};
    for (const [k, m] of this.markets) {
      if (!m) continue;
      const v = this.lastView.get(k) || {};
      markets[k] = {
        ticker: m.ticker, strike: m.strike, closeTime: m.closeTime,
        secondsLeft: m.closeTime ? Math.round((m.closeTime - Date.now()) / 1000) : null,
        yesBid: m.yesBid, yesAsk: m.yesAsk,
        modelP: v.modelP ?? null,
      };
    }
    return {
      mode: this.cfg.mode,
      running: this.running,
      tickCount: this.tickCount,
      killed: this.risk.killed(),
      killReason: this.store.getState('killReason', null),
      bankroll: this.cfg.bankroll,
      equity: this.cfg.bankroll + this.risk.lifetimeRealised(),
      realised: this.risk.lifetimeRealised(),
      today: this.risk.todayRealised(),
      exposure: this.risk.openExposure(),
      summary,
      forecastScore: this.store.forecastScore(),
      quotes,
      markets,
      recentSignals: [...this.signalRing.values()].sort((a, b) => b.at - a.at).slice(0, 15),
      lastError: this.lastError,
    };
  }
}

module.exports = { Engine };
