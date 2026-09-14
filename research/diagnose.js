'use strict';
// Live sanity check on the whole pricing pipeline: composite index vs each
// venue, published strike, model probability, and what the book actually says.
// Run this whenever the model disagrees loudly with the market - it is almost
// always the feed, not the market.

const path = require('node:path');
const kalshi = require('../src/kalshi');
const { PriceFeed } = require('../src/feed');
const { fairValue, decisionProbabilities } = require('../src/model');

const cfg = require(path.join(__dirname, '..', 'config.json'));

(async () => {
  const enabled = cfg.series.filter((s) => s.enabled);
  const feed = new PriceFeed({
    symbols: enabled.map((s) => s.symbol),
    log: () => {},
  });
  feed.start();
  await Promise.all(enabled.map((s) => feed.warmup(s.symbol)));
  process.stdout.write('warming feed for 20s...\n');
  await new Promise((r) => setTimeout(r, 20000));

  for (const s of enabled) {
    const m = await kalshi.currentMarket(s.ticker);
    if (!m) { console.log(`${s.ticker}: no open market`); continue; }
    const q = feed.quote(s.symbol);
    const book = await kalshi.orderbook(m.ticker);
    const secondsLeft = (m.closeTime - Date.now()) / 1000;

    // Raw venue prices, so a single bad feed is visible rather than averaged in.
    const raw = { coinbase: feed.cb.get(s.symbol) ? feed.cb.get(s.symbol).price : null };
    const P = require('../src/feed').PRODUCTS[s.symbol];
    await Promise.allSettled([
      feed._kraken(P.kraken).then((v) => { raw.kraken = v; }),
      feed._bitstamp(P.bitstamp).then((v) => { raw.bitstamp = v; }),
      feed._gemini(P.gemini).then((v) => { raw.gemini = v; }),
    ]);

    const fv = fairValue({
      price: q.price, strike: m.strike, secondsLeft,
      sigmaPerSqrtSec: q.sigmaPerSqrtSec,
      trailingMean: secondsLeft <= 60 ? feed.trailingMean(s.symbol, (60 - secondsLeft) * 1000) : null,
      trackingErrorFrac: cfg.signal.trackingErrorFrac,
    });
    const dp = decisionProbabilities({
      price: q.price, strike: m.strike, secondsLeft,
      sigmaPerSqrtSec: q.sigmaPerSqrtSec,
      trackingErrorFrac: cfg.signal.trackingErrorFrac,
      adverseSigmas: cfg.signal.adverseSigmas,
    });

    const marketYes = book.bestYesBid != null && book.bestYesAsk != null
      ? (book.bestYesBid + book.bestYesAsk) / 2 : null;

    console.log(`\n=== ${m.ticker} ===`);
    console.log(`  venues        ${Object.entries(raw).map(([k, v]) => `${k}=${v ? v.toFixed(2) : 'na'}`).join('  ')}`);
    console.log(`  composite     ${q.price.toFixed(2)}   (basis ${(feed.basis.get(s.symbol) || {}).basis?.toFixed(2) ?? 'na'}, venues ${q.venues}, spread ${(q.venueSpread * 100).toFixed(4)}%)`);
    console.log(`  strike        ${m.strike}`);
    console.log(`  distance      ${((q.price - m.strike) / m.strike * 100).toFixed(4)}%  (${(q.price - m.strike).toFixed(2)})`);
    console.log(`  seconds left  ${secondsLeft.toFixed(0)}`);
    const pct = (sg) => sg ? (sg * Math.sqrt(secondsLeft) * 100).toFixed(4) + '%' : 'na';
    console.log(`  sigma used    ${pct(q.sigmaPerSqrtSec)} over the remaining window`);
    console.log(`     sparse30s  ${pct(q.sigmaSparse)}    candle1m ${pct(q.sigmaCandle)}   (samples ${q.volSamples})`);
    console.log(`  MODEL  P(yes) ${fv.p.toFixed(4)}   (decision yes ${dp.yes.toFixed(4)} / no ${dp.no.toFixed(4)})`);
    console.log(`  MARKET P(yes) ${marketYes != null ? marketYes.toFixed(4) : 'na'}   ` +
      `[yesBid ${book.bestYesBid} yesAsk ${book.bestYesAsk}]`);
    console.log(`  disagreement  ${marketYes != null ? (fv.p - marketYes >= 0 ? '+' : '') + (fv.p - marketYes).toFixed(4) : 'na'}`);
  }

  feed.stop();
  process.exit(0);
})().catch((e) => { console.error(e); process.exit(1); });
