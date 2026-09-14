'use strict';
// Portfolio backtest of the bot AS CONFIGURED, on real Kalshi tape.
//
// This is deliberately not a fresh implementation of the strategy. It imports
// the shipped `src/model.js` and the shipped `src/risk.js` and drives them
// against a temporary database on simulated time, so what gets measured is the
// actual code that would place orders - Kelly sizing, every cap, the
// ceil-to-the-cent fee, the conservative decision probabilities, the price
// band, one-position-per-window, the daily loss limit and the kill switch.
//
// Data
//   Kalshi per-minute candles (bid/ask/close) for 400 settled windows/series.
//   Crypto index: Coinbase 1-minute closes (a CF Benchmarks constituent).
//   Commodity index: Pyth, the actual settlement source - verified against
//   Kalshi's published floor_strike to 0.0015% (gold) and 0.0033% (silver).
//
// Where this is OPTIMISTIC, and it matters:
//   * Fills are assumed at the minute's closing quote, in full, with no depth
//     limit and no queue. Real size would walk the book.
//   * Zero latency. The live path taps a phone for 3-5 seconds per order, by
//     which time the quote that justified the trade has moved.
//   * Decisions are evaluated once a minute, not every 2s.
//   * Sigma uses the 30-minute candle estimator only; live also computes a
//     sparse 30s estimate and takes the MIN, so live sigma runs a little lower.
//
// Every one of those biases the result in the strategy's favour. Treat the
// output as an upper bound on what live trading would have done.

const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

const { Store } = require('../src/store');
const { Risk } = require('../src/risk');
const { fairValue, decisionProbabilities, evaluate } = require('../src/model');
const { tradingFee, sweep, FEE_COEF } = require('../src/kalshi');

const TAPE = path.join(__dirname, 'tape2');

// Must match src/feed.js, or the backtest prices a different model than ships.
const CANDLE_LOOKBACK_MIN = 30;
const VOL_BIAS = 1.077;

const CRYPTO = {
  KXBTC15M: 'spot_BTC-USD.json',
  KXETH15M: 'spot_ETH-USD.json',
  KXSOL15M: 'spot_SOL-USD.json',
  KXXRP15M: 'spot_XRP-USD.json',
  KXDOGE15M: 'spot_DOGE-USD.json',
};
const COMMODITY = {
  KXGOLD15M: 'pyth_KXGOLD15M.json',
  KXSILVER15M: 'pyth_KXSILVER15M.json',
};

function loadIndex(file) {
  const p = path.join(TAPE, file);
  if (!fs.existsSync(p)) return null;
  const raw = JSON.parse(fs.readFileSync(p, 'utf8'));
  const bars = raw.bars || raw;                 // pyth wraps, coinbase does not
  const out = new Map();
  for (const [k, v] of Object.entries(bars)) {
    const close = Number(v.close);
    if (Number.isFinite(close) && close > 0) out.set(Number(k), close);
  }
  return out;
}

function loadSeries(series) {
  const p = path.join(TAPE, `${series}.json`);
  if (!fs.existsSync(p)) return [];
  return JSON.parse(fs.readFileSync(p, 'utf8'));
}

/** Sigma per sqrt(second) from 1-minute closes, matching src/feed.js warmup. */
function sigmaAt(index, ts, lookback = CANDLE_LOOKBACK_MIN) {
  let sum = 0, n = 0;
  for (let k = 0; k < lookback; k++) {
    const t1 = ts - 60 * k, t0 = t1 - 60;
    const c1 = index.get(t1), c0 = index.get(t0);
    if (c1 && c0) { const r = Math.log(c1 / c0); sum += r * r; n++; }
  }
  if (n < 20) return null;
  const varPerMin = (sum / n) / (VOL_BIAS ** 2);
  return Math.sqrt(varPerMin / 60);
}

function buildTimeline(seriesList) {
  const events = [];
  for (const { series, index, rows } of seriesList) {
    for (const r of rows) {
      if (!r.strike || !(r.result === 'yes' || r.result === 'no')) continue;
      for (const b of r.bars || []) {
        if (b.ts == null || b.bid == null || b.ask == null) continue;
        if (b.ask < b.bid) continue;
        const secondsLeft = r.close_ts - b.ts;
        if (secondsLeft <= 0 || secondsLeft > 15 * 60) continue;
        events.push({ t: b.ts, type: 'bar', series, index, market: r, bar: b, secondsLeft });
      }
      events.push({ t: r.close_ts + 1, type: 'settle', series, market: r });
    }
  }
  events.sort((a, b) => a.t - b.t || (a.type === 'settle' ? -1 : 1));
  return events;
}

function run(cfg, seriesList, { verbose = false } = {}) {
  const dbFile = path.join(os.tmpdir(), `kbot-backtest-${process.pid}-${Math.random().toString(36).slice(2)}.db`);
  const store = new Store(dbFile);
  let clock = 0;
  const risk = new Risk(cfg, store, () => {}, { now: () => clock });

  const events = buildTimeline(seriesList);
  const openByTicker = new Map();
  const equityCurve = [];
  const trades = [];
  const rejects = new Map();
  let forecastN = 0, brierModel = 0, brierMarket = 0;
  const seenForecast = new Set();

  for (const ev of events) {
    clock = ev.t * 1000;

    if (ev.type === 'settle') {
      const posId = openByTicker.get(ev.market.ticker);
      if (posId == null) continue;
      const pos = store.get('SELECT * FROM positions WHERE id = $id', { id: posId });
      openByTicker.delete(ev.market.ticker);
      if (!pos) continue;
      const won = ev.market.result === pos.side;
      const gross = won ? pos.contracts * (1 - pos.avg_price) : -pos.contracts * pos.avg_price;
      const pnl = gross - (pos.fee || 0);
      store.settlePosition(pos.id, ev.market.result, pnl, clock);
      trades.push({
        t: ev.t, ticker: pos.ticker, series: pos.series, side: pos.side,
        contracts: pos.contracts, price: pos.avg_price, fee: pos.fee,
        // Snapshotted at entry: needed to test our selection against the
        // market's own probability, which is the only null worth testing.
        modelP: pos.model_p, marketP: pos.market_p,
        result: ev.market.result, won, pnl,
      });
      equityCurve.push({ t: ev.t, equity: cfg.bankroll + risk.lifetimeRealised() });
      continue;
    }

    const { market, bar, index, secondsLeft, series } = ev;
    const spot = index.get(bar.ts - 60);       // minute [ts-60, ts) closes at ts
    if (!spot) continue;
    const sigma = sigmaAt(index, bar.ts - 60);
    if (!sigma) continue;

    const sig = cfg.signal;
    const fvArgs = {
      price: spot, strike: market.strike, secondsLeft,
      sigmaPerSqrtSec: sigma, trailingMean: null,
      trackingErrorFrac: sig.trackingErrorFrac,
    };
    const fv = fairValue(fvArgs);
    if (!fv) continue;

    // Forecast record, taken at the same point the engine takes it.
    if (!seenForecast.has(market.ticker)
        && secondsLeft <= sig.maxSecondsLeft && secondsLeft > sig.maxSecondsLeft - 60) {
      seenForecast.add(market.ticker);
      const mid = (bar.bid + bar.ask) / 2;
      const y = market.result === 'yes' ? 1 : 0;
      brierModel += (fv.p - y) ** 2;
      brierMarket += (mid - y) ** 2;
      forecastN++;
    }

    const note = (why) => rejects.set(why, (rejects.get(why) || 0) + 1);

    if (openByTicker.has(market.ticker)
        || store.get('SELECT COUNT(*) AS n FROM positions WHERE ticker=$t AND mode=$m',
          { t: market.ticker, m: cfg.mode }).n > 0) { note('already traded this window'); continue; }
    if (secondsLeft < sig.minSecondsLeft) { note('too close to close'); continue; }
    if (secondsLeft > sig.maxSecondsLeft) { note('too early in window'); continue; }
    if (bar.ask - bar.bid > sig.maxSpread) { note('spread too wide'); continue; }

    const dp = decisionProbabilities({ ...fvArgs, adverseSigmas: sig.adverseSigmas ?? 2 });
    if (!dp) continue;

    const yesAsk = bar.ask;
    const noAsk = 1 - bar.bid;
    const probe = evaluate({
      pYes: dp.yes, pNo: dp.no, yesAsk, noAsk,
      contracts: 1, feeCoef: FEE_COEF,
      minPrice: sig.minPrice ?? 0, maxPrice: sig.maxPrice ?? 1,
    });
    if (!probe) { note('no side in the tradable price band'); continue; }

    const decision = risk.check({
      side: probe.side, price: probe.price, prob: probe.prob,
      kelly: probe.kelly, bookDepth: null,        // candles carry no depth
    });
    if (!decision.ok) {
      note(decision.reason.replace(/[\d.]+/g, 'N'));
      continue;
    }

    const fee = tradingFee(decision.contracts, probe.price);
    const mid = (bar.bid + bar.ask) / 2;
    const id = store.openPosition({
      ticker: market.ticker, series, side: probe.side,
      contracts: decision.contracts, avgPrice: probe.price, fee,
      openedAt: clock, closeTs: market.close_ts,
      modelP: probe.side === 'yes' ? dp.yes : dp.no,
      marketP: probe.side === 'yes' ? mid : 1 - mid,
      mode: cfg.mode,
    });
    store.insertOrder({
      ts: clock, ticker: market.ticker, series, side: probe.side,
      contracts: decision.contracts, limitPrice: probe.price, mode: cfg.mode,
      status: 'filled', fillPrice: probe.price, fee,
    });
    openByTicker.set(market.ticker, id);
    if (verbose) {
      process.stdout.write(
        `${new Date(clock).toISOString()} ${probe.side} ${decision.contracts} ${market.ticker} ` +
        `@${probe.price.toFixed(3)} edge ${decision.netEdge.toFixed(4)}\n`);
    }
  }

  const killed = risk.killed();
  const killReason = store.getState('killReason', null);
  store.close();
  try { fs.rmSync(dbFile, { force: true }); fs.rmSync(dbFile + '-wal', { force: true }); fs.rmSync(dbFile + '-shm', { force: true }); } catch {}

  return {
    trades, equityCurve, rejects, killed, killReason,
    forecast: forecastN
      ? { n: forecastN, model: brierModel / forecastN, market: brierMarket / forecastN }
      : null,
  };
}

function summarise(cfg, res) {
  const t = res.trades;
  const pnl = t.reduce((a, x) => a + x.pnl, 0);
  const fees = t.reduce((a, x) => a + x.fee, 0);
  const wins = t.filter((x) => x.won).length;
  const staked = t.reduce((a, x) => a + x.contracts * x.price + x.fee, 0);

  let peak = cfg.bankroll, maxDD = 0;
  for (const p of res.equityCurve) {
    peak = Math.max(peak, p.equity);
    maxDD = Math.max(maxDD, peak - p.equity);
  }
  const final = cfg.bankroll + pnl;
  return {
    trades: t.length, wins, losses: t.length - wins,
    hitRate: t.length ? wins / t.length : null,
    pnl, fees, staked, final, maxDD,
    perContract: t.length ? pnl / t.reduce((a, x) => a + x.contracts, 0) : null,
    returnPct: (final / cfg.bankroll - 1) * 100,
    killed: res.killed, killReason: res.killReason,
    forecast: res.forecast,
  };
}

// ---------------------------------------------------------------------------

function main() {
  const cfgBase = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'config.json'), 'utf8'));
  const args = Object.fromEntries(process.argv.slice(2)
    .map((a) => /^--([^=]+)=?(.*)$/.exec(a)).filter(Boolean).map((m) => [m[1], m[2] || true]));

  const wanted = args.series ? String(args.series).split(',') : null;
  const seriesList = [];
  for (const [series, file] of Object.entries({ ...CRYPTO, ...COMMODITY })) {
    if (wanted && !wanted.includes(series)) continue;
    const index = loadIndex(file);
    const rows = loadSeries(series);
    if (!index || !rows.length) {
      process.stdout.write(`skip ${series}: ${!index ? 'no index data' : 'no tape'}\n`);
      continue;
    }
    seriesList.push({ series, index, rows });
  }
  if (!seriesList.length) { process.stdout.write('no data\n'); return; }

  const span = seriesList.flatMap((s) => s.rows).reduce(
    (a, r) => ({ lo: Math.min(a.lo, r.open_ts), hi: Math.max(a.hi, r.close_ts) }),
    { lo: Infinity, hi: -Infinity });
  const windows = seriesList.reduce((a, s) => a + s.rows.length, 0);

  process.stdout.write(
    `\nBACKTEST  ${seriesList.map((s) => s.series).join(', ')}\n` +
    `${windows} settled windows, ` +
    `${new Date(span.lo * 1000).toISOString().slice(0, 16)} -> ${new Date(span.hi * 1000).toISOString().slice(0, 16)} ` +
    `(${((span.hi - span.lo) / 3600).toFixed(0)}h)\n` +
    `bankroll $${cfgBase.bankroll}, minEdge ${cfgBase.signal.minEdge}, ` +
    `maxStake $${cfgBase.risk.maxStakePerTrade}, quarter Kelly\n\n`);

  // 1) the shipped configuration
  const shipped = summarise(cfgBase, run(cfgBase, seriesList, { verbose: !!args.verbose }));
  report('AS SHIPPED', cfgBase, shipped);

  // 2) does ANY edge threshold make money? If the signal were real, profit
  //    would rise with a stricter threshold. Watch which way it actually goes.
  process.stdout.write('\nSENSITIVITY TO minEdge\n');
  process.stdout.write(`  ${'minEdge'.padStart(8)} ${'trades'.padStart(7)} ${'hit%'.padStart(6)} ` +
    `${'P&L'.padStart(9)} ${'final'.padStart(9)} ${'per ct'.padStart(8)} ${'maxDD'.padStart(8)}\n`);
  for (const e of [0.01, 0.02, 0.03, 0.04, 0.06, 0.08, 0.10, 0.15]) {
    const cfg = JSON.parse(JSON.stringify(cfgBase));
    cfg.signal.minEdge = e;
    const s = summarise(cfg, run(cfg, seriesList));
    process.stdout.write(
      `  ${String(e).padStart(8)} ${String(s.trades).padStart(7)} ` +
      `${(s.hitRate == null ? '--' : (s.hitRate * 100).toFixed(1)).padStart(6)} ` +
      `${fmt(s.pnl).padStart(9)} ${('$' + s.final.toFixed(2)).padStart(9)} ` +
      `${(s.perContract == null ? '--' : s.perContract.toFixed(4)).padStart(8)} ` +
      `${('$' + s.maxDD.toFixed(2)).padStart(8)}${s.killed ? '  KILLED' : ''}\n`);
  }

  // 3) per-series, at the shipped settings
  process.stdout.write('\nPER SERIES (shipped settings)\n');
  process.stdout.write(`  ${'series'.padEnd(13)} ${'trades'.padStart(7)} ${'hit%'.padStart(6)} ` +
    `${'P&L'.padStart(9)} ${'per ct'.padStart(8)}\n`);
  for (const s of seriesList) {
    const r = summarise(cfgBase, run(cfgBase, [s]));
    process.stdout.write(
      `  ${s.series.padEnd(13)} ${String(r.trades).padStart(7)} ` +
      `${(r.hitRate == null ? '--' : (r.hitRate * 100).toFixed(1)).padStart(6)} ` +
      `${fmt(r.pnl).padStart(9)} ${(r.perContract == null ? '--' : r.perContract.toFixed(4)).padStart(8)}\n`);
  }

  process.stdout.write('\nWHY TRADES WERE SKIPPED (shipped settings, top reasons)\n');
  const finalRun = run(cfgBase, seriesList);
  const rj = [...finalRun.rejects.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
  for (const [why, n] of rj) process.stdout.write(`  ${String(n).padStart(7)}  ${why}\n`);

  // Per-trade records, so significance can be tested separately. A backtest
  // that only reports a total is untestable.
  fs.writeFileSync(path.join(__dirname, 'backtest_trades.json'),
    JSON.stringify(finalRun.trades, null, 1));
  process.stdout.write(`\nwrote research/backtest_trades.json (${finalRun.trades.length} trades)\n\n`);
}

function fmt(x) { return `${x >= 0 ? '+' : '-'}$${Math.abs(x).toFixed(2)}`; }

function report(label, cfg, s) {
  process.stdout.write(`${label}\n`);
  process.stdout.write(`  trades        ${s.trades}  (${s.wins}W / ${s.losses}L` +
    `${s.hitRate == null ? '' : `, hit ${(s.hitRate * 100).toFixed(1)}%`})\n`);
  process.stdout.write(`  P&L           ${fmt(s.pnl)}   fees ${fmt(-s.fees)}   staked $${s.staked.toFixed(2)}\n`);
  process.stdout.write(`  final equity  $${s.final.toFixed(2)}  from $${cfg.bankroll.toFixed(2)}  (${s.returnPct >= 0 ? '+' : ''}${s.returnPct.toFixed(1)}%)\n`);
  process.stdout.write(`  max drawdown  $${s.maxDD.toFixed(2)}\n`);
  if (s.perContract != null) process.stdout.write(`  per contract  ${s.perContract >= 0 ? '+' : ''}${s.perContract.toFixed(4)}\n`);
  if (s.killed) process.stdout.write(`  KILL SWITCH   ${s.killReason}\n`);
  if (s.forecast) {
    const skill = (s.forecast.market - s.forecast.model) / s.forecast.market;
    process.stdout.write(`  forecast      n=${s.forecast.n}  Brier model ${s.forecast.model.toFixed(4)} ` +
      `vs market ${s.forecast.market.toFixed(4)}  skill ${(skill * 100).toFixed(2)}%\n`);
  }
}

main();
