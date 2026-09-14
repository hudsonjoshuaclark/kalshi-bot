#!/usr/bin/env node
'use strict';

// Isolated paper launcher.  It never reads config.json and never constructs an
// API/phone executor because config.robust-rv.paper.json fixes mode="paper".
const path = require('node:path');
const ROOT = path.join(__dirname, '..');
process.env.KALSHI_BOT_CONFIG = path.join(ROOT, 'config.robust-rv.paper.json');

const { Store } = require('../src/store');
const { Controls, loadConfig } = require('../src/controls');

async function main() {
  const cfg = loadConfig();
  if (cfg.mode !== 'paper' || cfg.strategy !== 'robust-rv' || cfg.risk.maxContracts !== 1) {
    throw new Error('paper launcher safety invariant failed');
  }
  const store = new Store(path.join(ROOT, 'data', 'robust-rv-paper.db'));
  const controls = new Controls(store, {
    label: 'robust-rv-paper',
    onLog: (e) => process.stdout.write(`[${new Date(e.ts).toISOString()}] ${e.source}: ${e.message}\n`),
  });
  const started = await controls.start();
  if (!started.ok) throw new Error(started.reason);
  process.stdout.write('Robust RV PAPER engine running: one contract max, no real orders. Ctrl-C to stop.\n');
  const stop = async () => {
    await controls.stop();
    store.close();
    process.exit(0);
  };
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);
}

main().catch((err) => { console.error(err.stack || err.message); process.exit(1); });
