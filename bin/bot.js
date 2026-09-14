#!/usr/bin/env node
'use strict';
// CLI. `node bin/bot.js <command>`
//
// `run` also serves the control API on 127.0.0.1:8770, which is what the
// tracker window talks to.

const path = require('node:path');
const fs = require('node:fs');
const { Store } = require('../src/store');
const { Controls, loadConfig, saveConfig, CONFIG } = require('../src/controls');
const { createServer, listen, DEFAULT_PORT } = require('../src/server');
const { PhoneExecutor } = require('../src/executor/phone');
const { ApiExecutor } = require('../src/executor/api');

const ROOT = path.join(__dirname, '..');
const DB = path.join(ROOT, 'data', 'bot.db');

function money(x) {
  if (x == null || Number.isNaN(x)) return '  --  ';
  return `${x >= 0 ? '+' : '-'}$${Math.abs(x).toFixed(2)}`;
}

async function cmdRun(args) {
  if (args.mode) {
    const cfg = loadConfig();
    cfg.mode = args.mode;
    saveConfig(cfg);
  }
  const cfg = loadConfig();
  const store = new Store(DB);
  const controls = new Controls(store, {
    label: 'cli',
    onLog: (e) => process.stdout.write(`[${new Date(e.ts).toISOString()}] ${e.source}: ${e.message}\n`),
  });

  if (cfg.mode === 'live') {
    process.stdout.write(
      '\n  ***  LIVE MODE  ***  real orders will be tapped into the Kalshi app on the phone.\n' +
      `       bankroll $${cfg.bankroll}, max stake $${cfg.risk.maxStakePerTrade}/trade, ` +
      `stops at $${cfg.maxTotalLoss} of losses.\n` +
      '       Ctrl-C stops the loop.  `node bin/bot.js kill` engages the kill switch.\n\n');
  }

  const server = createServer({ store, controls });
  let port = Number(args.port || DEFAULT_PORT);
  try {
    await listen(server, port);
    process.stdout.write(`control API on http://127.0.0.1:${port}\n`);
  } catch (err) {
    process.stdout.write(`could not bind ${port} (${err.code}); running without the API\n`);
  }

  const started = await controls.start();
  if (!started.ok) {
    process.stderr.write(`could not start: ${started.reason}\n`);
    process.exit(1);
  }

  const shutdown = async () => {
    process.stdout.write('\nshutting down...\n');
    await controls.stop();
    try { server.close(); } catch {}
    store.close();
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

function cmdStatus() {
  const cfg = loadConfig();
  const store = new Store(DB);
  const s = store.summary(cfg.mode);
  const fc = store.forecastScore();
  const { Risk } = require('../src/risk');
  const realised = new Risk(cfg, store, () => {}).lifetimeRealised();

  console.log(`\nmode           ${cfg.mode}`);
  console.log(`bankroll       $${cfg.bankroll.toFixed(2)}`);
  console.log(`equity         $${(cfg.bankroll + realised).toFixed(2)}   (realised ${money(realised)})`);
  const killed = store.getState('killSwitch', false);
  console.log(`kill switch    ${killed ? 'ENGAGED - ' + store.getState('killReason', '') : 'off'}`);
  console.log(`\npositions      ${s.openCount} open, ${s.settledCount} settled`);
  if (s.settledCount) {
    console.log(`hit rate       ${(s.hitRate * 100).toFixed(1)}%  (${s.wins}W / ${s.losses}L)`);
    console.log(`fees paid      $${s.fees.toFixed(2)}`);
  }

  console.log('\nforecast record (the scoreboard that matters):');
  if (!fc.n) console.log('  no resolved forecasts yet');
  else {
    console.log(`  n=${fc.n}  Brier model=${fc.model.toFixed(4)}  market=${fc.market.toFixed(4)}  ` +
      `skill=${(fc.skill * 100).toFixed(2)}%`);
    console.log(fc.skill > 0
      ? '  model is beating the market price.'
      : '  model is NOT beating the market price - no demonstrated edge.');
  }

  if (s.recent.length) {
    console.log('\nrecent settled:');
    for (const p of s.recent.slice(0, 10)) {
      console.log(`  ${p.ticker.padEnd(28)} ${p.side.padEnd(4)} x${String(p.contracts).padEnd(4)} ` +
        `@${p.avg_price.toFixed(3)} -> ${(p.result || '?').padEnd(4)} ${money(p.pnl)}`);
    }
  }
  console.log('');
  store.close();
}

function cmdSetMode(mode) {
  const cfg = loadConfig();
  cfg.mode = mode;
  saveConfig(cfg);
  console.log(`mode set to ${mode}`);
  const note = {
    paper: 'simulated fills only - no account is touched.',
    api: 'REAL orders will be sent to the Kalshi DEMO exchange (fake money).',
    'api-live': '*** REAL orders with REAL MONEY on production. ***',
    live: 'real orders will be tapped into the Kalshi app on the phone.',
  }[mode];
  if (note) console.log(note);
}

function cmdKill(reason) {
  const store = new Store(DB);
  new Controls(store).kill(reason);
  console.log('kill switch ENGAGED - no further orders will be placed.');
  store.close();
}

function cmdRevive() {
  const store = new Store(DB);
  new Controls(store).revive();
  console.log('kill switch cleared.');
  store.close();
}

/**
 * Walk the whole Kalshi order flow on the phone but stop before the final
 * confirm. This is how the UI selectors get calibrated without spending money.
 */
async function cmdPhoneDryRun(args) {
  const cfg = loadConfig();
  const ex = new PhoneExecutor(cfg, { log: (s, m) => process.stdout.write(`${s}: ${m}\n`) });
  const rd = await ex.ready();
  if (!rd.ok) {
    console.error(`phone not ready: ${rd.reason}`);
    console.error('start it with:  cd ../phone-harness && node bin/phone.js serve');
    process.exit(1);
  }
  const kalshi = require('../src/kalshi');
  const seriesTicker = args.series || cfg.series.find((s) => s.enabled).ticker;
  const market = await kalshi.currentMarket(seriesTicker);
  if (!market) { console.error(`no open market in ${seriesTicker}`); process.exit(1); }
  console.log(`dry run against ${market.ticker}`);
  const side = args.side || 'yes';
  const limitPrice = side === 'yes'
    ? (market.yesAsk ?? 0.5)
    : (market.noAsk ?? (market.yesBid != null ? 1 - market.yesBid : 0.5));
  const res = await ex.place({
    ticker: market.ticker, side,
    contracts: Number(args.contracts || 1),
    limitPrice, strike: market.strike, dryRun: true,
  });
  console.log(JSON.stringify(res, null, 2));
}

/** End-to-end auth check against Kalshi. Run this first with a new key. */
async function cmdApiVerify(args) {
  const cfg = loadConfig();
  if (args.env) cfg.mode = args.env === 'prod' ? 'api-live' : 'api';
  else if (!['api', 'api-live'].includes(cfg.mode)) cfg.mode = 'api';   // default to demo
  const ex = new ApiExecutor(cfg, { log: (s, m) => console.log(`${s}: ${m}`) });
  console.log(`environment: ${ex.env.toUpperCase()}${ex.env === 'prod' ? '  ** REAL MONEY **' : '  (fake money)'}`);
  console.log(`key id:      ${cfg.api.keyId || '(not set)'}`);
  console.log(`private key: ${cfg.api.privateKeyPath || '(not set)'}
`);
  const r = await ex.ready();
  if (r.ok) {
    console.log(`AUTH OK. balance $${r.balance != null ? r.balance.toFixed(2) : '?'}`);
    const pos = await ex.reconcile();
    console.log(`open positions: ${pos ? pos.length : 'could not read'}`);
  } else {
    console.error(`AUTH FAILED: ${r.reason}`);
    console.error('\nchecklist:');
    console.error('  1. api.keyId matches the key ID shown in Kalshi settings');
    console.error('  2. api.privateKeyPath points at the .pem you downloaded');
    console.error('  3. the key was created in the SAME environment you are calling');
    console.error('     (a demo key will not authenticate against production)');
    process.exit(1);
  }
}

/** Cancel every resting order. Use after any ambiguous failure. */
async function cmdFlatten() {
  const cfg = loadConfig();
  const { KalshiApi } = require('../src/kalshiApi');
  const env = cfg.mode === 'api-live' ? 'prod' : (cfg.mode === 'api' ? 'demo' : 'prod');
  const api = new KalshiApi({ env, keyId: cfg.api.keyId, privateKeyPath: cfg.api.privateKeyPath });
  const res = await api.cancelAllResting();
  if (!res.length) { console.log('no resting orders'); return; }
  for (const r of res) console.log(`  ${r.ok ? 'cancelled' : 'FAILED  '} ${r.ticker} ${r.orderId}${r.error ? ' - ' + r.error : ''}`);
  const left = await api.orders({ status: 'resting' });
  console.log(`resting orders remaining: ${(left.orders || []).length}`);
}

function usage() {
  console.log(`
kalshi-bot

  node bin/bot.js run [--mode=paper|live] [--port=8770]
                                            start the loop + control API
  node bin/bot.js status                    equity, positions, forecast record
  node bin/bot.js api-verify [--env=demo|prod]
                                            check API credentials end to end
  node bin/bot.js paper                     simulated fills, no account touched
  node bin/bot.js api                       REAL orders on Kalshi DEMO (fake money)
  node bin/bot.js api-live                  REAL orders on PRODUCTION (real money)
  node bin/bot.js live                      real orders by tapping the phone
  node bin/bot.js kill [reason]             stop all order placement
  node bin/bot.js revive                    clear the kill switch
  node bin/bot.js flatten                   cancel ALL resting orders
  node bin/bot.js phone-dryrun [--series=KXBTC15M] [--side=yes] [--contracts=1]
                                            walk the app UI, stop before confirm

  Desktop tracker:  npm start
  Research:         python research/*.py    (see README)
`);
}

function parseArgs(argv) {
  const out = { _: [] };
  for (const a of argv) {
    const m = /^--([^=]+)(?:=(.*))?$/.exec(a);
    if (m) out[m[1]] = m[2] === undefined ? true : m[2];
    else out._.push(a);
  }
  return out;
}

async function main() {
  const argv = process.argv.slice(2);
  const cmd = argv[0];
  const args = parseArgs(argv.slice(1));
  switch (cmd) {
    case 'run': return cmdRun(args);
    case 'status': return cmdStatus();
    case 'paper': return cmdSetMode('paper');
    case 'live': return cmdSetMode('live');
    case 'kill': return cmdKill(args._.join(' '));
    case 'revive': return cmdRevive();
    case 'phone-dryrun': return cmdPhoneDryRun(args);
    case 'api-verify': return cmdApiVerify(args);
    case 'flatten': return cmdFlatten();
    case 'api': return cmdSetMode('api');
    case 'api-live': return cmdSetMode('api-live');
    default: return usage();
  }
}

main().catch((err) => { console.error(err.stack || err.message); process.exit(1); });
