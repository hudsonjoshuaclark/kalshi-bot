"""Backtest engine for Kalshi crypto perpetual futures.

WHY THIS CAN BE TESTED PROPERLY, UNLIKE EVERYTHING BEFORE IT
A perp tracks spot, so the strategy is backtested against YEARS of price
history and the venue only supplies the cost model. Kalshi's binary markets
gave 2-3 months and strangled every study; BTC gives a decade.

KALSHI PERP COSTS (Tier 0, verified from the live fee schedule 2026-09-01)
  taker 0.120% of NOTIONAL, maker 0.020% of notional, charged per side.
  funding every 8 hours, paid or received depending on which side is crowded.
  3.25% APY paid on margin cash (needs $250 average daily balance).

The fee dominates absolutely everything. Observed BTC spread is 0.004% - i.e.
the taker fee is THIRTY TIMES the spread. Two consequences drive the design:
  * maker-only round trip is 0.04%; taker-only is 0.24%. Six times cheaper.
  * any strategy whose per-trade edge is under ~25bp is dead as a taker. The
    user's equity ORB edge is 8-19bp, so it would NOT survive as a taker here
    and only barely as a maker.
So this tests SLOW strategies, where moves are 1-5% and 4-24bp is noise.
"""
import json, os, glob, math, statistics, sys, io, random, collections, datetime as dt

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
random.seed(20260901)

TAKER = 0.00120
MAKER = 0.00020
FUNDING_8H = 0.0001          # ~0.01%/8h typical; a cost when long the crowded side
TRAIN_FRAC = 0.70
HIST = "perp_history"


def load(gran="1d"):
    out = {}
    for f in sorted(glob.glob(os.path.join(HIST, f"*_{gran}.json"))):
        coin = os.path.basename(f).split("_")[0].replace("-USD", "")
        raw = json.load(open(f))
        bars = sorted((int(k), v) for k, v in raw.items())
        if len(bars) > 200:
            out[coin] = bars
    return out


def returns(bars):
    r = []
    for (t0, a), (t1, b) in zip(bars, bars[1:]):
        if a["close"] > 0 and b["close"] > 0:
            r.append((t1, math.log(b["close"] / a["close"])))
    return r


# ---------------------------------------------------------------------------
# Strategies. Each returns a target position in {-1, 0, +1} per bar, using ONLY
# information available at that bar's close. Any peek at the future here would
# invalidate everything, so every lookback is strictly backwards.
# ---------------------------------------------------------------------------

def sig_timeseries_momentum(bars, lookback):
    """Classic time-series momentum: long if the trailing return is positive.
    The single most documented systematic edge in futures - the whole managed
    futures industry runs on it."""
    sig = []
    for i, (t, b) in enumerate(bars):
        if i < lookback:
            sig.append((t, 0)); continue
        past = bars[i - lookback][1]["close"]
        sig.append((t, 1 if b["close"] > past else -1))
    return sig


def sig_ma_cross(bars, fast, slow):
    sig = []
    closes = [b["close"] for _, b in bars]
    for i, (t, _) in enumerate(bars):
        if i < slow:
            sig.append((t, 0)); continue
        f = sum(closes[i - fast + 1:i + 1]) / fast
        s = sum(closes[i - slow + 1:i + 1]) / slow
        sig.append((t, 1 if f > s else -1))
    return sig


def sig_breakout(bars, lookback):
    """Donchian breakout - the Turtle rule, and the closest thing to ORB that
    works on a slow horizon."""
    sig, pos = [], 0
    for i, (t, b) in enumerate(bars):
        if i < lookback:
            sig.append((t, 0)); continue
        window = [x[1] for x in bars[i - lookback:i]]
        hi = max(x["high"] for x in window)
        lo = min(x["low"] for x in window)
        if b["close"] > hi: pos = 1
        elif b["close"] < lo: pos = -1
        sig.append((t, pos))
    return sig


def sig_meanrev(bars, lookback, z):
    sig = []
    closes = [b["close"] for _, b in bars]
    for i, (t, b) in enumerate(bars):
        if i < lookback:
            sig.append((t, 0)); continue
        w = closes[i - lookback:i]
        m = statistics.mean(w); s = statistics.pstdev(w)
        if s <= 0: sig.append((t, 0)); continue
        dev = (b["close"] - m) / s
        sig.append((t, -1 if dev > z else (1 if dev < -z else 0)))
    return sig


# ---------------------------------------------------------------------------

def run(bars, signal, fee, bars_per_day, funding=True):
    """Walk the signal, charging fees on every position CHANGE and funding on
    every held bar. Enter at the NEXT bar's open after the signal, never the
    close that produced it."""
    equity, pos, trades, curve = 1.0, 0, 0, []
    for i in range(len(signal) - 1):
        t, want = signal[i]
        nxt = bars[i + 1][1]
        if want != pos:
            equity -= fee * abs(want - pos)      # cross the full size change
            trades += 1
            pos = want
        if pos != 0:
            r = (nxt["close"] - nxt["open"]) / nxt["open"] if nxt["open"] else 0.0
            equity *= (1 + pos * r)
            if funding:
                equity -= abs(pos) * FUNDING_8H * (24 / bars_per_day) / 8
        curve.append((t, equity))
    return {"equity": equity, "trades": trades, "curve": curve}


def stats(res, years):
    eq = res["equity"]
    curve = [e for _, e in res["curve"]]
    if len(curve) < 10 or eq <= 0:
        return None
    rets = [(b / a - 1) for a, b in zip(curve, curve[1:]) if a > 0]
    if not rets:
        return None
    sd = statistics.pstdev(rets)
    per_year = len(rets) / max(years, 1e-9)
    sharpe = (statistics.mean(rets) / sd * math.sqrt(per_year)) if sd > 0 else 0
    peak, maxdd = curve[0], 0.0
    for x in curve:
        peak = max(peak, x)
        maxdd = max(maxdd, (peak - x) / peak)
    cagr = eq ** (1 / max(years, 1e-9)) - 1
    return {"final": eq, "cagr": cagr, "sharpe": sharpe, "maxdd": maxdd,
            "trades": res["trades"]}
