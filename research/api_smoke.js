'use strict';
// Validate the live order path WITHOUT risking money.
//
// The trick: place a resting limit order priced far away from the market so it
// cannot fill, confirm the exchange accepted it, then cancel it. That exercises
// create -> list -> cancel end to end, which is everything the bot needs, while
// the order never has any chance of trading.
//
// Run this before trusting ApiExecutor with real orders. It is the only way to
// find out that a field name or price format is wrong that does not involve
// discovering it with money on the line.

const path = require('node:path');
const { loadConfig } = require('../src/controls');
const { KalshiApi } = require('../src/kalshiApi');
const kalshi = require('../src/kalshi');

(async () => {
  const cfg = loadConfig();
  const env = process.argv.includes('--prod') ? 'prod' : 'demo';
  const api = new KalshiApi({ env, keyId: cfg.api.keyId, privateKeyPath: cfg.api.privateKeyPath });

  console.log(`environment: ${env.toUpperCase()}`);
  const bal = await api.balance();
  const cents = bal.balance ?? bal.balance_cents;
  console.log(`balance: $${(cents / 100).toFixed(2)}\n`);

  // a liquid, currently-open market
  const series = cfg.series.find((s) => s.enabled) || { ticker: 'KXBTC15M' };
  const m = await kalshi.currentMarket(series.ticker);
  if (!m) { console.log('no open market right now; try again shortly'); return; }
  const book = await kalshi.orderbook(m.ticker);
  console.log(`market ${m.ticker}`);
  console.log(`  book: yes bid ${book.bestYesBid} / yes ask ${book.bestYesAsk}\n`);

  // Far below any plausible bid, so it rests and cannot fill.
  const restPrice = 0.02;
  console.log(`1. placing a RESTING buy YES at $${restPrice.toFixed(2)} (market is ~$${book.bestYesAsk}) - cannot fill`);
  let order;
  try {
    const res = await api.createOrder({
      ticker: m.ticker, side: 'yes', contracts: 1, price: restPrice,
      timeInForce: 'good_till_canceled', postOnly: true,
      clientOrderId: `smoke-${Date.now()}`,
    });
    order = (res && (res.order || res)) || {};
    console.log(`   accepted. order_id=${order.order_id} status=${order.status}`);
    console.log(`   raw: ${JSON.stringify(res).slice(0, 300)}`);
  } catch (err) {
    console.error(`   FAILED: ${err.message}`);
    console.error('   -> the order body or endpoint needs fixing before going live.');
    process.exit(1);
  }

  console.log('\n2. reading it back from /portfolio/orders');
  try {
    const list = await api.orders({ ticker: m.ticker, status: 'resting' });
    const rows = list.orders || [];
    const mine = rows.find((o) => o.order_id === order.order_id);
    console.log(`   ${rows.length} resting order(s); ours ${mine ? 'FOUND' : 'not found'}`);
    if (mine) console.log(`   ${JSON.stringify(mine).slice(0, 260)}`);
  } catch (err) {
    console.error(`   could not list: ${err.message}`);
  }

  console.log('\n3. cancelling');
  try {
    await api.cancelOrder(order.order_id);
    console.log('   cancelled.');
  } catch (err) {
    console.error(`   CANCEL FAILED: ${err.message}`);
    console.error(`   *** cancel order ${order.order_id} manually in the app ***`);
    process.exit(1);
  }

  const after = await api.balance();
  const c2 = after.balance ?? after.balance_cents;
  console.log(`\nbalance after: $${(c2 / 100).toFixed(2)}  (unchanged means nothing traded)`);
  console.log(c2 === cents ? 'SMOKE TEST PASSED - order path works, no money moved.'
                           : 'balance CHANGED - investigate before going live.');
})().catch((e) => { console.error('failed:', e.message); process.exit(1); });
