'use strict';
// Fair value for Kalshi's 15-minute "price up?" contracts.
//
// The contract, precisely:
//
//   K = mean of the settlement index over the 60s before the window OPENED
//       (fixed, and published by Kalshi as floor_strike once the window opens)
//   S = mean of the settlement index over the 60s before the window CLOSES
//   YES pays $1 if S >= K
//
// So this is a digital on a *60-second average*, not on a point price. That
// distinction is the whole game in the last minute: once you are inside the
// averaging window, part of S is already realised and no longer random, which
// collapses the variance far faster than a naive point-price model expects. A
// point model is wildly overconfident at 3 minutes out and wildly underconfident
// at 30 seconds out.
//
// Everything is done in price space with a normal approximation. Over a 15
// minute horizon the index moves ~0.3%, so lognormal-vs-normal and
// average-of-logs-vs-log-of-average are both second-order effects, well below
// the tracking error of our index proxy.

/** Abramowitz & Stegun 7.1.26 error function; ~1e-7 absolute, plenty here. */
function erf(x) {
  const s = x < 0 ? -1 : 1;
  const a = Math.abs(x);
  const t = 1 / (1 + 0.3275911 * a);
  const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t
    + 0.254829592) * t * Math.exp(-a * a);
  return s * y;
}

function normCdf(z) {
  if (!Number.isFinite(z)) return z > 0 ? 1 : 0;
  return 0.5 * (1 + erf(z / Math.SQRT2));
}

const AVG_WINDOW_SEC = 60;

/**
 * Probability the contract settles YES.
 *
 * @param {object} a
 * @param {number} a.price        current composite index
 * @param {number} a.strike       published floor_strike
 * @param {number} a.secondsLeft  seconds until close
 * @param {number} a.sigmaPerSqrtSec  fractional vol per sqrt(second)
 * @param {?{mean:number,n:number}} a.trailingMean  realised mean of the index so
 *        far inside the final minute (null if not yet inside it)
 * @param {number} a.trackingErrorFrac  fractional 1-sigma error between our
 *        composite proxy and the true CF Benchmarks index
 */
function fairValue({
  price, strike, secondsLeft, sigmaPerSqrtSec,
  trailingMean = null, trackingErrorFrac = 0.0002,
}) {
  if (!(price > 0) || !(strike > 0) || !Number.isFinite(secondsLeft)) return null;
  if (!(sigmaPerSqrtSec > 0)) return null;

  const tau = Math.max(secondsLeft, 0);
  const sigAbs = price * sigmaPerSqrtSec;      // absolute vol per sqrt(second)

  let expected, variance;

  if (tau > AVG_WINDOW_SEC) {
    // Before the averaging window opens. Diffuse for (tau - 60) seconds, then
    // average over 60: Var = sig^2 * ((tau-60) + 60/3) = sig^2 * (tau - 40).
    expected = price;
    variance = sigAbs * sigAbs * (tau - 40);
  } else if (tau > 0) {
    // Inside the averaging window. The elapsed part of the mean is realised;
    // only the remaining tau seconds are still random.
    const elapsed = AVG_WINDOW_SEC - tau;
    const realisedMean = trailingMean && trailingMean.n > 0 ? trailingMean.mean : price;
    expected = (elapsed * realisedMean + tau * price) / AVG_WINDOW_SEC;
    // Var of (1/60) * integral of a BM over the remaining tau seconds.
    variance = (sigAbs * sigAbs * tau * tau * tau) / (3 * AVG_WINDOW_SEC * AVG_WINDOW_SEC);
  } else {
    expected = trailingMean && trailingMean.n > 0 ? trailingMean.mean : price;
    variance = 0;
  }

  // Our index is a proxy for the real settlement index. That tracking error is
  // irreducible and does NOT shrink as tau goes to zero, which is exactly what
  // stops the model claiming 99.9% certainty on a feed it cannot fully trust.
  const track = price * trackingErrorFrac;
  variance += track * track;

  const sd = Math.sqrt(variance);
  if (!(sd > 0)) return { p: expected >= strike ? 1 : 0, sd: 0, z: Infinity, expected, tau };

  const z = (expected - strike) / sd;
  let p = normCdf(z);
  // Never emit a certainty the feed cannot support.
  p = Math.min(Math.max(p, 0.0005), 0.9995);
  return { p, sd, z, expected, tau, distanceFrac: (expected - strike) / price };
}

/**
 * Probabilities to make a BUY decision on - deliberately pessimistic.
 *
 * Why this exists: `fairValue` adds our index tracking error to the variance,
 * which is honest as a description of belief but disastrous as a trading
 * signal. Symmetric uncertainty pulls every probability toward 0.5, so on a
 * deep out-of-the-money contract the market says 0.2% and our noisier model
 * says 6.9% - and the bot reads that gap as a 6.6c edge and buys a worthless
 * lottery ticket. The gap is not edge. It is us knowing less than the market,
 * which is the exact adverse selection the backtest measured (disagree more,
 * lose more).
 *
 * So the decision probability drops the tracking-error variance entirely (no
 * credit for our own fog) and additionally shifts the index against the side
 * being bought by `adverseSigmas` tracking errors. An edge that survives both
 * is an edge that does not depend on our feed being right.
 */
function decisionProbabilities(args) {
  const { price, trackingErrorFrac = 0.0002, adverseSigmas = 2 } = args;
  const shift = price * trackingErrorFrac * adverseSigmas;

  const point = fairValue({ ...args, trackingErrorFrac: 0 });
  const down = fairValue({ ...args, price: price - shift, trackingErrorFrac: 0 });
  const up = fairValue({ ...args, price: price + shift, trackingErrorFrac: 0 });
  const belief = fairValue(args);
  if (!point || !down || !up || !belief) return null;

  return {
    belief: belief.p,                              // what we actually think
    yes: Math.min(point.p, down.p),                // worst case if buying YES
    no: 1 - Math.max(point.p, up.p),               // worst case if buying NO
    sd: belief.sd,
    tau: belief.tau,
    distanceFrac: belief.distanceFrac,
  };
}

/**
 * Kelly fraction of bankroll for a binary paying $1, bought at `cost`.
 * Risking `cost` to win `(1 - cost)` gives odds b = (1-cost)/cost, and
 *     f* = (p*b - (1-p)) / b = (p - cost) / (1 - cost)
 */
function kellyFraction(p, cost) {
  if (!(cost > 0) || !(cost < 1)) return 0;
  const f = (p - cost) / (1 - cost);
  return Math.max(0, Math.min(1, f));
}

/**
 * Evaluate both sides of a market and return the better trade, or null.
 *
 * Edge is computed against the price you would actually PAY after sweeping the
 * book, and net of the entry fee, which on Kalshi is quadratic and rounded up
 * to the cent. On a small order that rounding is a real cost: one contract at
 * 50c carries a 2c fee, so the true break-even is 52c, not 50c.
 */
function evaluate({
  pYes, pNo, yesAsk, noAsk, contracts = 1, feeCoef = 0.07,
  minPrice = 0, maxPrice = 1,
}) {
  const out = [];
  const fee = (c, px) => Math.ceil(feeCoef * c * px * (1 - px) * 100) / 100;
  const tradable = (px) => px != null && px > 0 && px < 1 && px >= minPrice && px <= maxPrice;

  if (tradable(yesAsk)) {
    const f = fee(contracts, yesAsk);
    const costPer = yesAsk + f / contracts;
    out.push({
      side: 'yes', price: yesAsk, feeTotal: f, costPer,
      prob: pYes, edge: pYes - costPer, edgeGross: pYes - yesAsk,
      kelly: kellyFraction(pYes, costPer),
    });
  }
  if (tradable(noAsk)) {
    const f = fee(contracts, noAsk);
    const costPer = noAsk + f / contracts;
    out.push({
      side: 'no', price: noAsk, feeTotal: f, costPer,
      prob: pNo, edge: pNo - costPer, edgeGross: pNo - noAsk,
      kelly: kellyFraction(pNo, costPer),
    });
  }
  if (!out.length) return null;
  out.sort((a, b) => b.edge - a.edge);
  return out[0];
}

module.exports = { fairValue, decisionProbabilities, kellyFraction, evaluate, normCdf, AVG_WINDOW_SEC };
