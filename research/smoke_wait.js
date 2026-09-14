'use strict';
// Wait for a freshly-opened 15-minute window (they start near 50c) and validate
// the whole live order path against it with zero risk: rest a buy far below the
// bid, confirm the exchange accepted it, cancel it, confirm the balance never
// moved.
//
// Near expiry these contracts go degenerate (bid 0.002 / ask 0.003), and a
// post_only order priced above the ask is correctly REJECTED for crossing -
// which is why this waits for a window with a real two-sided quote instead.

const { loadConfig } = require('../src/controls');
const { KalshiApi } = require('../src/kalshiApi');
const kalshi = require('../src/kalshi');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const SERIES = ['KXBTC15M', 'KXETH15M', 'KXSOL15M', 'KXXRP15M', 'KXDOGE15M'];

(async () => {
  const cfg = loadConfig();
  const api = new KalshiApi({ env: 'prod', keyId: cfg.api.keyId, privateKeyPath: cfg.api.privateKeyPath });
  const bal = async () => Number((await api.request('GET', '/portfolio/balance'))
    .balance_breakdown.find((e) => e.exchange_index === 2).balance);

  const deadline = Date.now() + 20 * 60 * 1000;
  let m = null, book = null;
  process.stdout.write('waiting for a window with a two-sided quote...\n');
  while (Date.now() < deadline && !m) {
    for (const st of SERIES) {
      try {
        const c = await kalshi.currentMarket(st);
        if (!c) continue;
        const bk = await kalshi.orderbook(c.ticker);
        if (bk.bestYesBid > 0.20 && bk.bestYesBid < 0.80) { m = c; book = bk; break; }
      } catch { /* keep looking */ }
    }
    if (!m) await sleep(20000);
  }
  if (!m) { process.stdout.write('no suitable window appeared within 20 minutes\n'); return; }

  const b0 = await bal();
  const restAt = Math.max(0.01, Math.round((book.bestYesBid - 0.15) * 100) / 100);
  process.stdout.write(`market ${m.ticker} | yes bid ${book.bestYesBid} ask ${book.bestYesAsk}\n`);
  process.stdout.write(`exchange-2 balance before: $${b0.toFixed(4)}\n`);
  process.stdout.write(`\n1. resting buy YES at $${restAt.toFixed(2)} (well below the bid - cannot fill)\n`);

  let o;
  try {
    const r = await api.createOrder({
      ticker: m.ticker, side: 'yes', contracts: 1, price: restAt,
      exchangeIndex: m.exchangeIndex, timeInForce: 'good_till_canceled',
      postOnly: true, clientOrderId: `smoke-${Date.now()}`,
    });
    o = r.order || r;
    process.stdout.write(`   ACCEPTED order_id=${o.order_id} status=${o.status}\n`);
  } catch (err) {
    process.stdout.write(`   FAILED ${err.status} ${err.code} | ${String(err.message).slice(0, 200)}\n`);
    return;
  }

  process.stdout.write('2. cancelling\n');
  try { await api.cancelOrder(o.order_id); process.stdout.write('   cancelled\n'); }
  catch (err) { process.stdout.write(`   CANCEL FAILED ${err.code} - cancel ${o.order_id} MANUALLY\n`); }

  const b1 = await bal();
  process.stdout.write(`\nexchange-2 balance after:  $${b1.toFixed(4)}\n`);
  process.stdout.write(b0 === b1
    ? 'ORDER PATH VERIFIED end to end. No money moved.\n'
    : 'balance CHANGED - investigate before going live.\n');
})().catch((e) => { process.stdout.write(`failed: ${e.message}\n`); });
