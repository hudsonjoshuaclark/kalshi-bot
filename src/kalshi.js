'use strict';
// Kalshi public market data. No API key is needed to read markets, order books
// or the trade tape - only order entry is authenticated, and that is what the
// phone is for.
//
// Two things about this API bite every time:
//
// 1. The order book returns BIDS ONLY, on both sides. There is no ask array. A
//    NO contract at price p and a YES contract at (1 - p) are the same dollar of
//    risk, so crossing to buy YES means lifting resting NO bids:
//        yes_ask(p) = 1 - no_bid(p)
//    Getting this backwards silently inverts every price in the system.
//
// 2. Kalshi is mid-migration to decimal-dollar fields. The same payload may
//    carry `yes_bid` (integer cents) and `yes_bid_dollars` ("0.4300"), and the
//    book arrives as either `orderbook` or `orderbook_fp`. Read both shapes.

const BASE = 'https://api.elections.kalshi.com/trade-api/v2';

// Brotli is advertised by default but Kalshi's stream trips Node's decoder on
// large paginated bodies, exactly as it trips httpx. Ask for gzip only.
const HEADERS = { 'accept-encoding': 'gzip', accept: 'application/json' };

function num(v, fallback = null) {
  if (v === null || v === undefined || v === '') return fallback;
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

// A price field may be dollars ("0.4300") or integer cents (43). Anything above
// 1 is cents; 1 itself is ambiguous but only ever means $1.00 in practice.
function toDollars(v, fallback = null) {
  const n = num(v, null);
  if (n === null) return fallback;
  return n > 1 ? n / 100 : n;
}

// Prefer the explicit *_dollars field when present, else fall back to cents.
function priceOf(obj, base, fallback = null) {
  if (obj[`${base}_dollars`] !== undefined) return toDollars(obj[`${base}_dollars`], fallback);
  return toDollars(obj[base], fallback);
}

async function req(path, params = {}, { tries = 4, timeout = 20000 } = {}) {
  const url = new URL(BASE + path);
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
  }
  let lastErr = null;
  for (let i = 0; i < tries; i++) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeout);
    try {
      const r = await fetch(url, { headers: HEADERS, signal: ctl.signal });
      clearTimeout(timer);
      // 429 and 5xx are worth waiting out; 4xx is not.
      if (r.status === 429 || r.status >= 500) {
        lastErr = new Error(`kalshi ${r.status} ${path}`);
        await sleep(600 * (i + 1));
        continue;
      }
      if (!r.ok) throw new Error(`kalshi ${r.status} ${path}: ${(await r.text()).slice(0, 200)}`);
      return await r.json();
    } catch (err) {
      clearTimeout(timer);
      lastErr = err;
      if (i === tries - 1) break;
      await sleep(500 * (i + 1));
    }
  }
  throw lastErr || new Error('kalshi request failed');
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------

/** Normalise one market record into the only shape the rest of the bot uses. */
function normaliseMarket(m) {
  return {
    ticker: m.ticker,
    eventTicker: m.event_ticker,
    // Kalshi runs several exchange venues (0 Default, 1 Combos, 2 Crypto,
    // 3 Tennis & Baseball) with SEPARATE balances. Order entry requires the
    // market's exchange_index and rejects a mismatch with a confusing
    // `user_not_found`, so it has to travel with the market.
    exchangeIndex: m.exchange_index ?? 0,
    title: m.title,
    subtitle: m.yes_sub_title || m.subtitle || '',
    status: m.status,
    openTime: m.open_time ? Date.parse(m.open_time) : null,
    closeTime: m.close_time ? Date.parse(m.close_time) : null,
    // The strike is the settled average of the index over the minute before the
    // window opened. Kalshi publishes it as floor_strike once the window opens,
    // which is what makes this contract priceable at all.
    strike: num(m.floor_strike, null),
    strikeType: m.strike_type,
    result: m.result || null,
    expirationValue: num(m.expiration_value, null),
    yesBid: priceOf(m, 'yes_bid'),
    yesAsk: priceOf(m, 'yes_ask'),
    noBid: priceOf(m, 'no_bid'),
    noAsk: priceOf(m, 'no_ask'),
    lastPrice: priceOf(m, 'last_price'),
    volume: num(m.volume_fp ?? m.volume, 0),
    openInterest: num(m.open_interest_fp ?? m.open_interest, 0),
    // Sub-penny ticks exist below $0.10 and above $0.90 on these series.
    priceStructure: m.price_level_structure || null,
    priceRanges: (m.price_ranges || []).map((r) => ({
      start: num(r.start), end: num(r.end), step: num(r.step),
    })),
  };
}

async function listMarkets({ seriesTicker, status = 'open', limit = 200, maxPages = 1 }) {
  const out = [];
  let cursor = null;
  for (let page = 0; page < maxPages; page++) {
    const body = await req('/markets', {
      series_ticker: seriesTicker, status, limit, cursor,
    });
    for (const m of body.markets || []) out.push(normaliseMarket(m));
    cursor = body.cursor;
    if (!cursor) break;
  }
  return out;
}

/**
 * The one market in `seriesTicker` that is open right now, i.e. the live
 * 15-minute window. These series run exactly one market at a time.
 */
async function currentMarket(seriesTicker) {
  const open = await listMarkets({ seriesTicker, status: 'open', limit: 20 });
  if (!open.length) return null;
  open.sort((a, b) => (a.closeTime || 0) - (b.closeTime || 0));
  return open[0];
}

/**
 * Order book as executable ask ladders.
 *
 * Kalshi hands back bid ladders for both sides. We convert to the two ladders
 * an aggressor actually pays: to buy YES you lift NO bids at (1 - p), and to
 * buy NO you lift YES bids at (1 - p). Both ladders come back sorted cheapest
 * first, which is the order you would consume them in.
 */
async function orderbook(ticker) {
  const body = await req(`/markets/${encodeURIComponent(ticker)}/orderbook`);
  const ob = body.orderbook_fp || body.orderbook || {};
  const yesBidsRaw = ob.yes_dollars || ob.yes || [];
  const noBidsRaw = ob.no_dollars || ob.no || [];

  const parse = (rows) => rows
    .map((r) => ({ price: toDollars(r[0]), size: num(r[1], 0) }))
    .filter((r) => r.price !== null && r.size > 0);

  const yesBids = parse(yesBidsRaw).sort((a, b) => b.price - a.price); // best first
  const noBids = parse(noBidsRaw).sort((a, b) => b.price - a.price);

  // Buying YES consumes NO bids: cost = 1 - noBidPrice, best (cheapest) is the
  // highest NO bid.
  const yesAsks = noBids.map((r) => ({ price: round4(1 - r.price), size: r.size }));
  const noAsks = yesBids.map((r) => ({ price: round4(1 - r.price), size: r.size }));

  return {
    ticker,
    yesBids,
    noBids,
    yesAsks,
    noAsks,
    bestYesBid: yesBids.length ? yesBids[0].price : null,
    bestYesAsk: yesAsks.length ? yesAsks[0].price : null,
    bestNoBid: noBids.length ? noBids[0].price : null,
    bestNoAsk: noAsks.length ? noAsks[0].price : null,
  };
}

function round4(x) { return Math.round(x * 10000) / 10000; }

/**
 * Walk an ask ladder for `qty` contracts and return the true average fill
 * price. Quoting off the top of book alone overstates edge the moment size
 * exceeds the first level, which on a $19 bankroll is rare but free to model.
 */
function sweep(asks, qty) {
  let need = qty, cost = 0, filled = 0, worst = null;
  for (const lvl of asks) {
    if (need <= 0) break;
    const take = Math.min(need, lvl.size);
    cost += take * lvl.price;
    worst = lvl.price;
    filled += take;
    need -= take;
  }
  if (filled === 0) return null;
  return { filled, avgPrice: cost / filled, worstPrice: worst, complete: filled >= qty };
}

async function trades(ticker, { limit = 1000, maxPages = 1, minTs = null } = {}) {
  const out = [];
  let cursor = null;
  for (let page = 0; page < maxPages; page++) {
    const body = await req('/markets/trades', { ticker, limit, cursor, min_ts: minTs });
    const rows = body.trades || [];
    for (const t of rows) {
      out.push({
        t: t.created_time ? Date.parse(t.created_time) : null,
        yesPrice: priceOf(t, 'yes_price'),
        noPrice: priceOf(t, 'no_price'),
        count: num(t.count_fp ?? t.count, 0),
        takerSide: t.taker_side || null,
      });
    }
    cursor = body.cursor;
    if (!cursor || !rows.length) break;
  }
  return out;
}

// ---------------------------------------------------------------------------
// Fees. Kalshi's trading fee on these series is quadratic:
//     fee = ceil(0.07 * C * P * (1 - P))   in dollars, rounded UP to the cent
// It is charged on entry and again on exit if you sell before settlement;
// settling a winner costs nothing extra. The round-up is brutal on small
// orders, so it is modelled exactly rather than as a rate.

const FEE_COEF = 0.07;

function tradingFee(contracts, price, coef = FEE_COEF) {
  if (!contracts || contracts <= 0) return 0;
  const raw = coef * contracts * price * (1 - price);
  return Math.ceil(raw * 100) / 100;
}

/** Break-even probability for buying at `price`, including the entry fee. */
function breakEven(contracts, price, coef = FEE_COEF) {
  return price + tradingFee(contracts, price, coef) / contracts;
}

module.exports = {
  BASE,
  req,
  listMarkets,
  currentMarket,
  orderbook,
  sweep,
  trades,
  tradingFee,
  breakEven,
  normaliseMarket,
  toDollars,
  FEE_COEF,
};
