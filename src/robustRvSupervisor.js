'use strict';

/** Deterministic forward-performance gate. It never changes mode or sends orders. */
function promotionAudit(rows) {
  const settled = [...rows].filter((row) => Number.isFinite(Number(row.pnl)))
    .sort((a, b) => Number(a.settled_at || 0) - Number(b.settled_at || 0));
  const pnl = settled.reduce((sum, row) => sum + Number(row.pnl), 0);
  const half = Math.floor(settled.length / 2);
  const firstHalf = settled.slice(0, half).reduce((sum, row) => sum + Number(row.pnl), 0);
  const secondHalf = settled.slice(half).reduce((sum, row) => sum + Number(row.pnl), 0);
  const group = (field) => {
    const out = new Map();
    for (const row of settled) out.set(row[field], (out.get(row[field]) || 0) + Number(row.pnl));
    return Object.fromEntries([...out.entries()].sort());
  };
  const bySeries = group('series');
  const bySide = group('side');
  const positiveSeries = Object.values(bySeries).filter((value) => value > 0).length;
  const bestFive = settled.map((row) => Number(row.pnl)).sort((a, b) => b - a).slice(0, 5)
    .reduce((sum, value) => sum + value, 0);
  const checks = {
    atLeast200: settled.length >= 200,
    netPositive: pnl > 0,
    splitHalvesPositive: firstHalf > 0 && secondHalf > 0,
    bothSidesPositive: ['yes', 'no'].every((side) => Number(bySide[side]) > 0),
    fourOfFiveSeriesPositive: positiveSeries >= 4,
    concentrationBelowHalf: pnl > 0 && bestFive / pnl < 0.5,
  };
  return {
    n: settled.length, pnl, pnlPerTrade: settled.length ? pnl / settled.length : null,
    firstHalf, secondHalf, bySeries, bySide, positiveSeries, bestFive,
    bestFiveShare: pnl > 0 ? bestFive / pnl : null,
    checks, mechanicalGatePassed: Object.values(checks).every(Boolean),
    manualRequirement: 'Underlying-up and underlying-down forward P&L must both be nonnegative before live promotion.',
    livePromotionApproved: false,
  };
}

module.exports = { promotionAudit };

