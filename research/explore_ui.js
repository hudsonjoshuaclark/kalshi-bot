'use strict';
// Learn the Kalshi app's real accessibility labels, one step at a time, and
// STOP at the order ticket. Never taps review or confirm - it exists so the
// selectors in src/executor/phone.js can be written against what is actually on
// screen instead of a guess.
//
//   node research/explore_ui.js            walk to the order ticket and stop
//   node research/explore_ui.js --tree     just dump what is on screen now

const fs = require('node:fs');
const path = require('node:path');
const kalshi = require('../src/kalshi');

const BASE = 'http://127.0.0.1:8760';
const SHOTS = path.join(__dirname, '..', 'data', 'shots');
fs.mkdirSync(SHOTS, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function req(p, { method = 'GET', body = null, raw = false } = {}) {
  const r = await fetch(BASE + p, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`${r.status} ${p}: ${(await r.text()).slice(0, 200)}`);
  return raw ? Buffer.from(await r.arrayBuffer()) : r.json();
}

function flatten(node, out = []) {
  if (!node || typeof node !== 'object') return out;
  const rect = node.rect || node.frame || null;
  const label = [node.label, node.name, node.value, node.text]
    .filter((v) => typeof v === 'string' && v.trim())
    .join(' | ');
  if (rect && Number.isFinite(rect.x) && rect.width > 0 && rect.height > 0) {
    out.push({
      type: node.type || node.elementType || '',
      label,
      enabled: node.enabled !== false,
      visible: node.visible !== false,
      x: rect.x, y: rect.y, w: rect.width, h: rect.height,
      cx: Math.round(rect.x + rect.width / 2),
      cy: Math.round(rect.y + rect.height / 2),
    });
  }
  for (const c of node.children || []) flatten(c, out);
  return out;
}

async function elements() {
  const body = await req('/source');
  const root = body.value || body.tree || body.source || body;
  return flatten(root);
}

async function shot(tag) {
  const buf = await req('/screenshot', { raw: true });
  const f = path.join(SHOTS, `explore-${tag}.png`);
  fs.writeFileSync(f, buf);
  return f;
}

async function tap(x, y, why) {
  console.log(`   TAP ${why} @ ${x},${y}`);
  await req('/tap', { method: 'POST', body: { x, y } });
  await sleep(900);
}

function show(els, title, filter = null) {
  console.log(`\n--- ${title} (${els.length} elements) ---`);
  const rows = els.filter((e) => e.visible && e.label && (!filter || filter(e)));
  for (const e of rows.slice(0, 40)) {
    console.log(`   ${e.type.padEnd(12)} @${String(e.cx).padStart(4)},${String(e.cy).padStart(4)}  ${e.label.replace(/\n/g, ' / ').slice(0, 88)}`);
  }
}

(async () => {
  const dumpOnly = process.argv.includes('--tree');
  if (dumpOnly) {
    show(await elements(), 'CURRENT SCREEN');
    console.log('\nshot:', await shot('now'));
    return;
  }

  // Pick a live window with real time left, so the market is actually tradable
  // by the time we have navigated to it.
  let market = null;
  for (const s of ['KXBTC15M', 'KXETH15M', 'KXSOL15M']) {
    const m = await kalshi.currentMarket(s);
    if (!m) continue;
    const left = (m.closeTime - Date.now()) / 1000;
    console.log(`${s}: ${m.ticker} strike ${m.strike} left ${left.toFixed(0)}s`);
    if (left > 240 && (!market || left > market.left)) market = { ...m, left };
  }
  if (!market) { console.log('no market with enough time left; try again shortly'); return; }

  // The row Kalshi renders does NOT contain the ticker - it shows the strike.
  const strikeText = market.strike.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  console.log(`\nTARGET ${market.ticker}  strike text "${strikeText}"  ${market.left.toFixed(0)}s left\n`);

  await req('/app/launch', { method: 'POST', body: { bundleId: 'com.kalshi.mobile' } });
  await sleep(2000);
  await shot('01-home');

  // Reset to a clean Explore screen first. Leaving a previous search in the box
  // is what made the first run match its own stale text: the field's VALUE
  // becomes its label once something is typed, so the placeholder is gone.
  let els = await elements();
  const cancel = els.find((e) => e.visible && /cancel search/i.test(e.label));
  if (cancel) { await tap(cancel.cx, cancel.cy, 'cancel stale search'); els = await elements(); }
  const explore = els.find((e) => e.visible && /^Explore\b/i.test(e.label));
  if (explore) { await tap(explore.cx, explore.cy, 'Explore tab'); await sleep(800); els = await elements(); }

  const search = els.find((e) => e.visible
    && (/search for markets/i.test(e.label) || /SearchField/i.test(e.type)));
  if (!search) { show(els, 'HOME - no search box found'); return; }
  await tap(search.cx, search.cy, 'search box');
  await req('/type', { method: 'POST', body: { text: market.ticker } });
  await sleep(2200);
  await shot('02-search');

  els = await elements();
  show(els, 'SEARCH RESULTS', (e) => !/TextField|SearchField/i.test(e.type));

  // Match the RESULT ROW, never the search field: the field's own value
  // contains the ticker we just typed, which is what made the first attempt
  // tap itself in a loop.
  const row = els.find((e) => e.visible
    && !/TextField|SearchField|Keyboard|Key$/i.test(e.type)
    && e.label.includes(strikeText)
    && !/closed/i.test(e.label));
  if (!row) {
    console.log(`\n!! no OPEN result row containing "${strikeText}".`);
    console.log('   candidate rows:');
    for (const e of els.filter((x) => x.visible && /15 min|target/i.test(x.label)).slice(0, 10)) {
      console.log(`     ${e.type} @${e.cx},${e.cy}  ${e.label.replace(/\n/g, ' / ').slice(0, 90)}`);
    }
    return;
  }
  console.log(`\n   result row: "${row.label.replace(/\n/g, ' / ').slice(0, 90)}"`);
  await tap(row.cx, row.cy, 'result row');
  await sleep(1500);
  await shot('03-market');

  els = await elements();
  show(els, 'MARKET PAGE');

  // Find the Yes/Up control. Kalshi labels these variously Yes / Up / a price.
  const yes = els.find((e) => e.visible && e.enabled
    && /^(yes|up)\b/i.test(e.label.trim())
    && /Button|Other|Cell/i.test(e.type));
  if (!yes) {
    console.log('\n!! no Yes/Up control found - see MARKET PAGE dump above');
    return;
  }
  console.log(`\n   yes control: "${yes.label.replace(/\n/g, ' / ').slice(0, 60)}" @${yes.cx},${yes.cy}`);
  await tap(yes.cx, yes.cy, 'YES / UP side');
  await sleep(1600);
  await shot('04-ticket');

  els = await elements();
  show(els, 'ORDER TICKET  *** STOPPING HERE - NOTHING CONFIRMED ***');
  console.log('\nNo order was placed. Screenshots in data/shots/explore-*.png');
})().catch((e) => { console.error('explore failed:', e.message); process.exit(1); });
