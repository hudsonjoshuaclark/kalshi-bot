'use strict';

// Frozen from research/s8_vol_estimators_train.json before held-out scoring.
// This module is deliberately pure so the exact live rule can be regression
// tested without market/API state.

function normPpf(p) {
  if (!(p > 0 && p < 1)) return null;
  const a = [-39.6968302866538, 220.946098424521, -275.928510446969,
    138.357751867269, -30.6647980661472, 2.50662827745924];
  const b = [-54.4760987982241, 161.585836858041, -155.698979859887,
    66.8013118877197, -13.2806815528857];
  const c = [-0.00778489400243029, -0.322396458041136, -2.40075827716184,
    -2.54973253934373, 4.37466414146497, 2.93816398269878];
  const d = [0.00778469570904146, 0.32246712907004, 2.445134137143,
    3.75440866190742];
  if (p < 0.02425) {
    const q = Math.sqrt(-2 * Math.log(p));
    return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  if (p > 1 - 0.02425) {
    const q = Math.sqrt(-2 * Math.log(1 - p));
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
      ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  const q = p - 0.5;
  const r = q * q;
  return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q /
    (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
}

function robustRvSignal({
  spot, strike, secondsLeft, rvRelativePerSqrtSec,
  yesBid, yesAsk, threshold = 2.0, minAbsZ = 0.15,
}) {
  if (!(spot > 0) || !(strike > 0) || !(secondsLeft > 40) || !(rvRelativePerSqrtSec > 0)) {
    return { ok: false, reason: 'invalid spot/strike/time/RV input' };
  }
  if (!(yesBid >= 0 && yesAsk > yesBid && yesAsk <= 1)) {
    return { ok: false, reason: 'invalid book' };
  }
  const mid = (yesBid + yesAsk) / 2;
  const z = normPpf(mid);
  if (!Number.isFinite(z) || Math.abs(z) < minAbsZ) {
    return { ok: false, reason: 'mid too close to 0.50 for stable inversion' };
  }
  const displacement = spot - strike;
  if (displacement * z <= 0) {
    return { ok: false, reason: 'spot/market direction mismatch' };
  }
  const tauEff = secondsLeft - 40;
  const impliedAbsPerSqrtSec = Math.abs(displacement / z) / Math.sqrt(tauEff);
  // Critical units match: realised return sigma -> absolute-price sigma.
  const realisedAbsPerSqrtSec = spot * rvRelativePerSqrtSec;
  const ratio = impliedAbsPerSqrtSec / realisedAbsPerSqrtSec;
  if (!Number.isFinite(ratio) || ratio < threshold) {
    return { ok: false, reason: `IV/RV ${Number.isFinite(ratio) ? ratio.toFixed(3) : 'invalid'} < ${threshold}`,
      ratio };
  }
  const buyYes = displacement > 0;
  const paid = buyYes ? yesAsk : 1 - yesBid;
  if (!(paid > 0 && paid < 1)) return { ok: false, reason: 'selected side is not tradable' };
  return {
    ok: true, side: buyYes ? 'yes' : 'no', paid, mid, z, absZ: Math.abs(z),
    ratio, tauEff, impliedAbsPerSqrtSec, realisedAbsPerSqrtSec,
  };
}

module.exports = { normPpf, robustRvSignal };

