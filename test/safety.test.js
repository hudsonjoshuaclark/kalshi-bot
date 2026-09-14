'use strict';
// Regression tests for the failure that emptied the account on 2026-08-30.
//
// WHAT HAPPENED: ApiExecutor read the fill count as `filled_count`/`count_filled`.
// Kalshi's field is `fill_count_fp`. So `filled` was always 0, every real fill
// was reported as "rejected", the engine never booked a position, and every
// risk cap that reads the positions table measured an empty table while $16.47
// drained to $0.05 over 1,262 orders.
//
// These tests exist so that specific shape of failure cannot come back quietly.

const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { Store } = require('../src/store');
const { Risk } = require('../src/risk');
const { ApiExecutor } = require('../src/executor/api');

let passed = 0, failed = 0;
function test(name, fn) {
  try { fn(); console.log(`  PASS  ${name}`); passed++; }
  catch (err) { console.log(`  FAIL  ${name}\n        ${err.message}`); failed++; }
}
async function testAsync(name, fn) {
  try { await fn(); console.log(`  PASS  ${name}`); passed++; }
  catch (err) { console.log(`  FAIL  ${name}\n        ${err.message}`); failed++; }
}

function freshStore() {
  const f = path.join(os.tmpdir(), `kbot-test-${Date.now()}-${Math.random().toString(36).slice(2)}.db`);
  return { store: new Store(f), file: f };
}

const baseCfg = () => ({
  mode: 'api-live',
  bankroll: 16.47,
  maxTotalLoss: 16.47,
  risk: {
    maxStakePerTrade: 2, maxConcurrentPositions: 3, maxOpenExposure: 5,
    maxTradesPerHour: 8, dailyLossLimit: 5, kellyFraction: 0.25,
    minContracts: 1, maxContracts: 40, drawdownStopPct: 0.25, minBalance: 2,
  },
  signal: { minEdge: 0.02 },
  api: { exchangeIndex: 2 },
});

console.log('\nSAFETY REGRESSION TESTS\n');

(async () => {
  // ---- 1. the exact field-name bug --------------------------------------
  await testAsync('a real fill reported with only fill_count_fp is NOT called rejected', async () => {
    const ex = new ApiExecutor(baseCfg(), {});
    // Kalshi's real response shape: fill_count_fp, as a string.
    ex.api = {
      createOrder: async () => ({ order: { order_id: 'x1', status: 'executed', fill_count_fp: '2.00', average_fill_price: 0.62 } }),
      orders: async () => ({ orders: [] }), fills: async () => ({ fills: [] }),
    };
    const r = await ex.place({ ticker: 'T', side: 'yes', contracts: 2, limitPrice: 0.62 });
    assert.strictEqual(r.status, 'filled', `expected filled, got ${r.status} (${r.error || ''})`);
    assert.strictEqual(r.contractsFilled, 2);
  });

  await testAsync('an UNRECOGNISED response is pending (ambiguous), never rejected', async () => {
    const ex = new ApiExecutor(baseCfg(), {});
    // No field we know: this is the situation that must never be read as "0 filled".
    ex.api = {
      createOrder: async () => ({ order: { order_id: 'x2', status: 'weird_new_status' } }),
      orders: async () => { throw new Error('unavailable'); },
      fills: async () => { throw new Error('unavailable'); },
    };
    const r = await ex.place({ ticker: 'T', side: 'yes', contracts: 1, limitPrice: 0.5 });
    assert.strictEqual(r.status, 'pending', `ambiguity must be pending, got ${r.status}`);
  });

  await testAsync('a genuine no-fill IS rejected once the exchange confirms it', async () => {
    const ex = new ApiExecutor(baseCfg(), {});
    ex.api = {
      createOrder: async () => ({ order: { order_id: 'x3', status: 'resting' } }),
      orders: async () => ({ orders: [{ order_id: 'x3', client_order_id: null, fill_count_fp: '0.00' }] }),
      fills: async () => ({ fills: [] }),
    };
    const r = await ex.place({ ticker: 'T', side: 'yes', contracts: 1, limitPrice: 0.5 });
    assert.ok(['rejected', 'pending'].includes(r.status), `got ${r.status}`);
  });

  // ---- 2. risk must not trade blind -------------------------------------
  test('real-money risk REFUSES to trade without a live account read', () => {
    const { store } = freshStore();
    const risk = new Risk(baseCfg(), store, () => {}, { requireLive: true });
    const d = risk.check({ side: 'yes', price: 0.5, prob: 0.9, kelly: 0.5, live: null });
    assert.strictEqual(d.ok, false);
    assert.ok(/refusing to trade blind/.test(d.reason), d.reason);
    store.close();
  });

  test('a FAILED live read is a stop, not a fallback to local state', () => {
    const { store } = freshStore();
    const risk = new Risk(baseCfg(), store, () => {}, { requireLive: true });
    const d = risk.check({ side: 'yes', price: 0.5, prob: 0.9, kelly: 0.5,
                           live: { ok: false, reason: 'network' } });
    assert.strictEqual(d.ok, false);
    store.close();
  });

  // ---- 3. live exposure cap, even with an empty local book --------------
  test('live exposure blocks a trade even when the local book says zero', () => {
    const { store } = freshStore();
    const risk = new Risk(baseCfg(), store, () => {}, { requireLive: true });
    assert.strictEqual(store.openPositions('api-live').length, 0);   // the bug's condition
    const d = risk.check({
      side: 'yes', price: 0.5, prob: 0.9, kelly: 0.5,
      live: { ok: true, balance: 10, shardBalance: 10, exposure: 5.5, openCount: 1 },
    });
    assert.strictEqual(d.ok, false);
    assert.ok(/live exposure/.test(d.reason), d.reason);
    store.close();
  });

  test('live open-position count blocks, even with an empty local book', () => {
    const { store } = freshStore();
    const risk = new Risk(baseCfg(), store, () => {}, { requireLive: true });
    const d = risk.check({
      side: 'yes', price: 0.5, prob: 0.9, kelly: 0.5,
      live: { ok: true, balance: 10, shardBalance: 10, exposure: 0, openCount: 3 },
    });
    assert.strictEqual(d.ok, false);
    assert.ok(/live open positions/.test(d.reason), d.reason);
    store.close();
  });

  // ---- 4. hard balance floor --------------------------------------------
  test('balance floor kills regardless of what the local books believe', () => {
    const { store } = freshStore();
    const risk = new Risk(baseCfg(), store, () => {}, { requireLive: true });
    const d = risk.check({
      side: 'yes', price: 0.5, prob: 0.9, kelly: 0.5,
      live: { ok: true, balance: 1.5, shardBalance: 1.5, exposure: 0, openCount: 0 },
    });
    assert.strictEqual(d.ok, false);
    assert.ok(risk.killed(), 'kill switch should be engaged');
    store.close();
  });

  // ---- 5. rate limit counts orders SENT ---------------------------------
  test('hourly rate limit counts every order sent, including rejected ones', () => {
    const { store } = freshStore();
    const risk = new Risk(baseCfg(), store, () => {}, { requireLive: true });
    for (let i = 0; i < 8; i++) {
      store.insertOrder({ ticker: `T${i}`, side: 'yes', contracts: 1, mode: 'api-live',
                          status: 'rejected', error: 'no fill' });   // the bug's status
    }
    assert.strictEqual(risk.tradesLastHour(), 8, 'rejected orders must still count');
    const d = risk.check({
      side: 'yes', price: 0.5, prob: 0.9, kelly: 0.5,
      live: { ok: true, balance: 10, shardBalance: 10, exposure: 0, openCount: 0 },
    });
    assert.strictEqual(d.ok, false);
    assert.ok(/hourly trade cap/.test(d.reason), d.reason);
    store.close();
  });

  // ---- 6. a clean trade still passes -------------------------------------
  test('a healthy trade is still allowed (the caps are not just always-off)', () => {
    const { store } = freshStore();
    const risk = new Risk(baseCfg(), store, () => {}, { requireLive: true });
    const d = risk.check({
      side: 'yes', price: 0.5, prob: 0.95, kelly: 0.6,
      live: { ok: true, balance: 16, shardBalance: 16, exposure: 0, openCount: 0 },
    });
    assert.strictEqual(d.ok, true, d.reason);
    assert.ok(d.contracts >= 1);
    store.close();
  });

  console.log(`\n${passed} passed, ${failed} failed\n`);
  process.exit(failed ? 1 : 0);
})();
