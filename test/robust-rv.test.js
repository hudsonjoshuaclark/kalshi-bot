'use strict';

const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const { normPpf, robustRvSignal } = require('../src/strategies/robustRv');
const { promotionAudit } = require('../src/robustRvSupervisor');

let passed = 0;
function test(name, fn) {
  fn();
  console.log(`  PASS  ${name}`);
  passed++;
}

console.log('\nROBUST RV STRATEGY TESTS\n');

test('inverse normal is accurate at common probabilities', () => {
  assert.ok(Math.abs(normPpf(0.5)) < 1e-12);
  assert.ok(Math.abs(normPpf(0.975) - 1.959964) < 1e-5);
});

test('signal uses absolute-price RV units and buys YES above strike', () => {
  const r = robustRvSignal({
    spot: 100, strike: 99, secondsLeft: 360,
    rvRelativePerSqrtSec: 0.00005, yesBid: 0.69, yesAsk: 0.71,
    threshold: 2, minAbsZ: 0.15,
  });
  assert.strictEqual(r.ok, true, r.reason);
  assert.strictEqual(r.side, 'yes');
  assert.strictEqual(r.paid, 0.71);
  assert.ok(r.ratio > 2);
  assert.ok(Math.abs(r.realisedAbsPerSqrtSec - 0.005) < 1e-12);
});

test('signal buys NO below strike at the executable 1-bid ask', () => {
  const r = robustRvSignal({
    spot: 99, strike: 100, secondsLeft: 360,
    rvRelativePerSqrtSec: 0.00005, yesBid: 0.29, yesAsk: 0.31,
    threshold: 2, minAbsZ: 0.15,
  });
  assert.strictEqual(r.ok, true, r.reason);
  assert.strictEqual(r.side, 'no');
  assert.ok(Math.abs(r.paid - 0.71) < 1e-12);
});

test('unstable near-50 inversion and sub-threshold IV/RV are rejected', () => {
  assert.strictEqual(robustRvSignal({ spot: 100.01, strike: 100, secondsLeft: 360,
    rvRelativePerSqrtSec: 0.0001, yesBid: 0.49, yesAsk: 0.51 }).ok, false);
  assert.strictEqual(robustRvSignal({ spot: 100.1, strike: 100, secondsLeft: 360,
    rvRelativePerSqrtSec: 0.01, yesBid: 0.69, yesAsk: 0.71 }).ok, false);
});

test('paper config cannot place real orders or exceed one contract', () => {
  const cfg = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'config.robust-rv.paper.json'), 'utf8'));
  assert.strictEqual(cfg.mode, 'paper');
  assert.strictEqual(cfg.strategy, 'robust-rv');
  assert.strictEqual(cfg.risk.maxContracts, 1);
  assert.ok(!cfg.api.keyId && !cfg.api.privateKeyPath);
});

test('supervisor never approves live and rejects concentrated or one-sided records', () => {
  const rows = Array.from({ length: 200 }, (_, i) => ({
    settled_at: i, pnl: i < 5 ? 1 : -0.01,
    series: ['KXBTC15M', 'KXETH15M', 'KXSOL15M', 'KXXRP15M', 'KXDOGE15M'][i % 5],
    side: 'yes',
  }));
  const a = promotionAudit(rows);
  assert.strictEqual(a.checks.atLeast200, true);
  assert.strictEqual(a.checks.bothSidesPositive, false);
  assert.strictEqual(a.checks.concentrationBelowHalf, false);
  assert.strictEqual(a.livePromotionApproved, false);
  assert.strictEqual(a.mechanicalGatePassed, false);
});

console.log(`\n${passed} passed\n`);
