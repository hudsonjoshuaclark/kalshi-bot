'use strict';
// Persistence. Node 24 ships SQLite, so this needs no native module and no
// build step - which matters because the tracker is an Electron app and
// rebuilding a native sqlite binding against Electron's ABI is a whole evening.
//
// Two design rules learned the hard way on the arbs-local market maker:
//
//  * Every table has an INTEGER PRIMARY KEY, never a timestamp key. Windows'
//    clock resolution is ~15ms, fast ticks share a timestamp, and a timestamp
//    primary key silently DROPS rows - which flatters your equity curve.
//  * The market price is snapshotted at forecast time. It cannot be recovered
//    afterwards, and without it there is no way to score the model against the
//    market later.

const { DatabaseSync } = require('node:sqlite');
const path = require('node:path');
const fs = require('node:fs');

const SCHEMA = `
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  series TEXT NOT NULL,
  ticker TEXT NOT NULL,
  symbol TEXT,
  spot REAL, strike REAL, seconds_left REAL, sigma REAL,
  model_p REAL,                 -- our probability
  market_yes_bid REAL, market_yes_ask REAL,
  market_no_bid REAL, market_no_ask REAL,
  market_mid REAL,              -- snapshot: unrecoverable later
  side TEXT, edge REAL, kelly REAL, contracts INTEGER,
  acted INTEGER NOT NULL DEFAULT 0,
  reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(ts);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  signal_id INTEGER,
  ticker TEXT NOT NULL,
  series TEXT,
  side TEXT NOT NULL,           -- yes | no
  contracts INTEGER NOT NULL,
  limit_price REAL,
  mode TEXT NOT NULL,           -- paper | live
  status TEXT NOT NULL,         -- pending | filled | rejected | failed | aborted
  fill_price REAL,
  fee REAL,
  notional REAL,
  error TEXT,
  evidence TEXT,                -- screenshot path / confirmation text from the phone
  latency_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_orders_ts ON orders(ts);

CREATE TABLE IF NOT EXISTS positions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticker TEXT NOT NULL,
  series TEXT,
  side TEXT NOT NULL,
  contracts INTEGER NOT NULL,
  avg_price REAL NOT NULL,
  fee REAL NOT NULL DEFAULT 0,
  opened_at INTEGER NOT NULL,
  close_ts INTEGER,
  settled_at INTEGER,
  result TEXT,                  -- yes | no
  pnl REAL,
  model_p REAL,
  market_p REAL,
  mode TEXT NOT NULL DEFAULT 'paper'
);
CREATE INDEX IF NOT EXISTS idx_positions_open ON positions(settled_at);

CREATE TABLE IF NOT EXISTS equity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  cash REAL NOT NULL,
  exposure REAL NOT NULL,
  realised REAL NOT NULL,
  total REAL NOT NULL,
  mode TEXT NOT NULL DEFAULT 'paper'
);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity(ts);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  level TEXT NOT NULL,          -- info | warn | error
  source TEXT NOT NULL,
  message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

-- One row per market window, whether or not we traded it. This is the honest
-- scoreboard: it accumulates a calibration record even while the bot sits on
-- its hands, so "does the model beat the market" can be answered from live data
-- rather than only from backtest.
CREATE TABLE IF NOT EXISTS forecasts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL,
  ticker TEXT NOT NULL UNIQUE,
  series TEXT,
  seconds_left REAL,
  model_p REAL NOT NULL,
  market_p REAL NOT NULL,       -- snapshot; unrecoverable after the fact
  spot REAL, strike REAL, sigma REAL,
  close_ts INTEGER,
  result TEXT,
  scored_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_forecasts_open ON forecasts(scored_at);

CREATE TABLE IF NOT EXISTS state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);
`;

class Store {
  constructor(file) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    this.db = new DatabaseSync(file);
    this.db.exec('PRAGMA journal_mode = WAL;');
    this.db.exec('PRAGMA busy_timeout = 4000;');
    this.db.exec(SCHEMA);
  }

  close() { try { this.db.close(); } catch {} }

  // -- generic helpers ------------------------------------------------------
  run(sql, params = {}) { return this.db.prepare(sql).run(params); }
  all(sql, params = {}) { return this.db.prepare(sql).all(params); }
  get(sql, params = {}) { return this.db.prepare(sql).get(params); }

  // -- state ----------------------------------------------------------------
  setState(key, value) {
    this.run(
      `INSERT INTO state (key, value, updated_at) VALUES ($k, $v, $t)
       ON CONFLICT(key) DO UPDATE SET value = $v, updated_at = $t`,
      { k: key, v: JSON.stringify(value), t: Date.now() });
  }

  getState(key, fallback = null) {
    const row = this.get('SELECT value FROM state WHERE key = $k', { k: key });
    if (!row) return fallback;
    try { return JSON.parse(row.value); } catch { return fallback; }
  }

  // -- writes ---------------------------------------------------------------
  logEvent(level, source, message) {
    this.run('INSERT INTO events (ts, level, source, message) VALUES ($ts,$l,$s,$m)',
      { ts: Date.now(), l: level, s: source, m: String(message).slice(0, 2000) });
  }

  insertSignal(s) {
    const r = this.run(
      `INSERT INTO signals (ts, series, ticker, symbol, spot, strike, seconds_left, sigma,
         model_p, market_yes_bid, market_yes_ask, market_no_bid, market_no_ask, market_mid,
         side, edge, kelly, contracts, acted, reason)
       VALUES ($ts,$series,$ticker,$symbol,$spot,$strike,$sl,$sigma,$mp,$yb,$ya,$nb,$na,$mid,
               $side,$edge,$kelly,$contracts,$acted,$reason)`,
      {
        ts: s.ts, series: s.series, ticker: s.ticker, symbol: s.symbol ?? null,
        spot: s.spot ?? null, strike: s.strike ?? null, sl: s.secondsLeft ?? null,
        sigma: s.sigma ?? null, mp: s.modelP ?? null,
        yb: s.yesBid ?? null, ya: s.yesAsk ?? null, nb: s.noBid ?? null, na: s.noAsk ?? null,
        mid: s.mid ?? null, side: s.side ?? null, edge: s.edge ?? null,
        kelly: s.kelly ?? null, contracts: s.contracts ?? null,
        acted: s.acted ? 1 : 0, reason: s.reason ?? null,
      });
    return Number(r.lastInsertRowid);
  }

  insertOrder(o) {
    const r = this.run(
      `INSERT INTO orders (ts, signal_id, ticker, series, side, contracts, limit_price, mode,
         status, fill_price, fee, notional, error, evidence, latency_ms)
       VALUES ($ts,$sig,$ticker,$series,$side,$c,$lp,$mode,$status,$fp,$fee,$n,$err,$ev,$lat)`,
      {
        ts: o.ts ?? Date.now(), sig: o.signalId ?? null, ticker: o.ticker, series: o.series ?? null,
        side: o.side, c: o.contracts, lp: o.limitPrice ?? null, mode: o.mode,
        status: o.status, fp: o.fillPrice ?? null, fee: o.fee ?? null,
        n: o.notional ?? null, err: o.error ?? null, ev: o.evidence ?? null,
        lat: o.latencyMs ?? null,
      });
    return Number(r.lastInsertRowid);
  }

  updateOrder(id, fields) {
    const keys = Object.keys(fields);
    if (!keys.length) return;
    const sets = keys.map((k) => `${toCol(k)} = $${k}`).join(', ');
    this.run(`UPDATE orders SET ${sets} WHERE id = $id`, { ...fields, id });
  }

  openPosition(p) {
    const r = this.run(
      `INSERT INTO positions (ticker, series, side, contracts, avg_price, fee, opened_at,
         close_ts, model_p, market_p, mode)
       VALUES ($ticker,$series,$side,$c,$px,$fee,$open,$close,$mp,$mkt,$mode)`,
      {
        ticker: p.ticker, series: p.series ?? null, side: p.side, c: p.contracts,
        px: p.avgPrice, fee: p.fee ?? 0, open: p.openedAt ?? Date.now(),
        close: p.closeTs ?? null, mp: p.modelP ?? null, mkt: p.marketP ?? null,
        mode: p.mode ?? 'paper',
      });
    return Number(r.lastInsertRowid);
  }

  openPositions(mode = null) {
    return mode
      ? this.all('SELECT * FROM positions WHERE settled_at IS NULL AND mode = $m', { m: mode })
      : this.all('SELECT * FROM positions WHERE settled_at IS NULL');
  }

  settlePosition(id, result, pnl, settledAt = Date.now()) {
    this.run('UPDATE positions SET settled_at = $t, result = $r, pnl = $p WHERE id = $id',
      { t: settledAt, r: result, p: pnl, id });
  }

  /** One forecast per market window. Ignored if that window already has one. */
  recordForecast(f) {
    this.run(
      `INSERT INTO forecasts (ts, ticker, series, seconds_left, model_p, market_p,
         spot, strike, sigma, close_ts)
       VALUES ($ts,$ticker,$series,$sl,$mp,$kp,$spot,$strike,$sigma,$close)
       ON CONFLICT(ticker) DO NOTHING`,
      {
        ts: f.ts ?? Date.now(), ticker: f.ticker, series: f.series ?? null,
        sl: f.secondsLeft ?? null, mp: f.modelP, kp: f.marketP,
        spot: f.spot ?? null, strike: f.strike ?? null, sigma: f.sigma ?? null,
        close: f.closeTs ?? null,
      });
  }

  unscoredForecasts(before = Date.now()) {
    return this.all(
      'SELECT * FROM forecasts WHERE scored_at IS NULL AND close_ts IS NOT NULL AND close_ts <= $b',
      { b: Math.floor(before / 1000) });
  }

  scoreForecast(id, result) {
    this.run('UPDATE forecasts SET result = $r, scored_at = $t WHERE id = $id',
      { r: result, t: Date.now(), id });
  }

  /** Live Brier comparison: our model against the market's own snapshotted price. */
  forecastScore() {
    const rows = this.all("SELECT model_p, market_p, result FROM forecasts WHERE result IN ('yes','no')");
    if (!rows.length) return { n: 0, model: null, market: null, skill: null };
    let m = 0, k = 0;
    for (const r of rows) {
      const y = r.result === 'yes' ? 1 : 0;
      m += (r.model_p - y) ** 2;
      k += (r.market_p - y) ** 2;
    }
    const model = m / rows.length, market = k / rows.length;
    return { n: rows.length, model, market, skill: market > 0 ? (market - model) / market : null };
  }

  recordEquity(e) {
    this.run(
      `INSERT INTO equity (ts, cash, exposure, realised, total, mode)
       VALUES ($ts,$cash,$exp,$real,$total,$mode)`,
      {
        ts: e.ts ?? Date.now(), cash: e.cash, exp: e.exposure,
        real: e.realised, total: e.total, mode: e.mode ?? 'paper',
      });
  }

  // -- reads for the tracker ------------------------------------------------

  summary(mode = null) {
    const where = mode ? 'WHERE mode = $m' : '';
    const p = mode ? { m: mode } : {};
    const settled = this.all(
      `SELECT * FROM positions ${where ? where + ' AND' : 'WHERE'} settled_at IS NOT NULL
       ORDER BY settled_at DESC`, p);
    const open = this.openPositions(mode);
    const wins = settled.filter((s) => (s.pnl || 0) > 0).length;
    const realised = settled.reduce((a, s) => a + (s.pnl || 0), 0);
    const fees = settled.reduce((a, s) => a + (s.fee || 0), 0);
    return {
      settledCount: settled.length,
      openCount: open.length,
      wins,
      losses: settled.length - wins,
      hitRate: settled.length ? wins / settled.length : null,
      realised,
      fees,
      brier: brierPair(settled),
      recent: settled.slice(0, 25),
      open,
    };
  }

  equityCurve(mode = null, limit = 2000) {
    return mode
      ? this.all('SELECT * FROM equity WHERE mode = $m ORDER BY ts DESC LIMIT $l', { m: mode, l: limit }).reverse()
      : this.all('SELECT * FROM equity ORDER BY ts DESC LIMIT $l', { l: limit }).reverse();
  }

  recentEvents(limit = 100) {
    return this.all('SELECT * FROM events ORDER BY id DESC LIMIT $l', { l: limit });
  }

  recentSignals(limit = 100) {
    return this.all('SELECT * FROM signals ORDER BY id DESC LIMIT $l', { l: limit });
  }

  recentOrders(limit = 100) {
    return this.all('SELECT * FROM orders ORDER BY id DESC LIMIT $l', { l: limit });
  }
}

/**
 * Brier score of the model against the market, on the same resolved outcomes.
 * This, not P&L, is the honest scoreboard: over a few hundred 15-minute crypto
 * windows, P&L is dominated by whether the underlying happened to drift, while
 * Brier isolates forecasting skill. If the model cannot beat the market's own
 * snapshotted price here, it has no edge no matter what the P&L says.
 */
function brierPair(settled) {
  const rows = settled.filter((s) => s.model_p != null && s.market_p != null && s.result);
  if (!rows.length) return { n: 0, model: null, market: null, skill: null };
  let m = 0, k = 0;
  for (const r of rows) {
    const y = r.result === 'yes' ? 1 : 0;
    m += (r.model_p - y) ** 2;
    k += (r.market_p - y) ** 2;
  }
  const model = m / rows.length, market = k / rows.length;
  return { n: rows.length, model, market, skill: market > 0 ? (market - model) / market : null };
}

const COLS = {
  signalId: 'signal_id', limitPrice: 'limit_price', fillPrice: 'fill_price',
  latencyMs: 'latency_ms',
};
function toCol(k) { return COLS[k] || k; }

module.exports = { Store, brierPair };
