"""What would the favourite strategy actually do to a small bankroll?

Per-trade ROI is the flattering number. This strategy buys at ~0.89 and wins
~92% of the time, so it is SHORT VOLATILITY: many small wins, occasional loss of
the entire stake. That shape is exactly the one that looks wonderful in an
average and ruins an account in a sequence, so the questions here are drawdown
and ruin, not mean return.

Concurrency matters too. Eight series run 15-minute windows simultaneously, so
capital is committed in parallel; sizing each trade as if it were alone
overstates how much can actually be deployed and understates correlated loss.
"""
import json, os, math, statistics, sys, io, random, collections

if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
random.seed(20260824)

from patterns import load, sample_obs, TRAIN_FRAC
from strategy import pick, trade_pnl, fee_total

BANKROLL = 16.77
THRESHOLD = 0.80
FRAC = 0.5


def run_path(trades, kelly_frac, bankroll=BANKROLL, max_concurrent=3, cap_frac=0.15):
    """
    Walk trades in time order, sizing at a fraction of Kelly.

    Trades whose windows overlap are treated as concurrent: capital committed to
    an open window is unavailable, capped at `max_concurrent` positions. That is
    the constraint a real account faces and it is what stops the simulation
    compounding a single dollar 400 times in a day.
    """
    equity = bankroll
    open_pos = []          # (close_ts, stake, price, win)
    peak, maxdd = equity, 0.0
    n_taken, n_skipped = 0, 0
    curve = []

    for t in sorted(trades, key=lambda x: x["open_ts"]):
        now = t["open_ts"] + (t["close_ts"] - t["open_ts"]) * FRAC
        # settle anything that closed before this entry
        still = []
        for (cts, stake, price, win) in open_pos:
            if cts <= now:
                contracts = stake / price
                equity += contracts * ((1 - price) if win else -price)
                equity -= fee_total(max(1, round(contracts)), price)
            else:
                still.append((cts, stake, price, win))
        open_pos = still

        peak = max(peak, equity)
        maxdd = max(maxdd, peak - equity)
        curve.append(equity)
        if equity <= 0.50:
            break

        if len(open_pos) >= max_concurrent:
            n_skipped += 1
            continue

        p_est = 0.918          # win rate estimated on TRAIN, not this sample
        price = t["price"]
        if price >= 1 or price <= 0:
            continue
        kelly = max(0.0, (p_est - price) / (1 - price))
        committed = sum(s for (_, s, _, _) in open_pos)
        free = max(0.0, equity - committed)
        stake = min(kelly * kelly_frac * equity, cap_frac * equity, free)
        if stake < price:      # cannot afford even one contract
            n_skipped += 1
            continue
        open_pos.append((t["close_ts"], stake, price, t["win"]))
        n_taken += 1

    for (cts, stake, price, win) in open_pos:
        contracts = stake / price
        equity += contracts * ((1 - price) if win else -price)
        equity -= fee_total(max(1, round(contracts)), price)
    curve.append(equity)
    peak = max(peak, equity)
    maxdd = max(maxdd, peak - equity)
    return {"final": equity, "maxdd": maxdd, "taken": n_taken,
            "skipped": n_skipped, "curve": curve}


def main():
    markets = load(sys.argv[1] if len(sys.argv) > 1 else "tape2/*.json")
    markets.sort(key=lambda m: m["open_ts"])
    split = markets[int(len(markets) * TRAIN_FRAC)]["open_ts"]
    test_m = [m for m in markets if m["open_ts"] >= split]
    te = pick(sample_obs(test_m, FRAC), THRESHOLD)
    print(f"TEST set: {len(te)} candidate trades over "
          f"{(max(t['close_ts'] for t in te) - min(t['open_ts'] for t in te))/3600:.1f} hours\n")

    print("SHAPE OF THE BET")
    wins = [t for t in te if t["win"]]
    losses = [t for t in te if not t["win"]]
    print(f"  win  {len(wins):>4} times, average gain  {statistics.mean(1-t['price'] for t in wins):+.4f}/contract")
    print(f"  lose {len(losses):>4} times, average loss {statistics.mean(-t['price'] for t in losses):+.4f}/contract")
    print(f"  -> {statistics.mean(t['price'] for t in losses)/statistics.mean(1-t['price'] for t in wins):.1f} "
          f"wins needed to repay one loss")
    runs, cur = [], 0
    for t in sorted(te, key=lambda x: x["open_ts"]):
        if t["win"]: cur += 1
        else: runs.append(cur); cur = 0
    print(f"  longest winning streak {max(runs) if runs else 0}; "
          f"worst gap between losses {min(runs) if runs else 0}")

    print(f"\nBANKROLL PATHS from ${BANKROLL:.2f} (actual test-set outcomes, in order)")
    print(f"  {'sizing':<16}{'final':>10}{'maxDD':>9}{'taken':>7}{'skipped':>9}")
    for kf, name in [(1.0, 'full Kelly'), (0.5, 'half Kelly'), (0.25, 'quarter Kelly'),
                     (0.125, 'eighth Kelly')]:
        r = run_path(te, kf)
        print(f"  {name:<16}${r['final']:>9.2f}${r['maxdd']:>8.2f}{r['taken']:>7}{r['skipped']:>9}")

    # The realised order is one sample. Reshuffle to see the distribution of
    # paths the SAME trades could have produced.
    print(f"\nSHUFFLED PATHS (same trades, random order, quarter Kelly, 2000 runs)")
    finals = []
    for _ in range(2000):
        sh = te[:]
        random.shuffle(sh)
        for i, t in enumerate(sh):        # re-stamp times to keep ordering logic
            t = dict(t); t["open_ts"] = i * 900; t["close_ts"] = i * 900 + 900
            sh[i] = t
        finals.append(run_path(sh, 0.25)["final"])
    finals.sort()
    print(f"  median ${statistics.median(finals):.2f}   mean ${statistics.mean(finals):.2f}")
    print(f"  5th ${finals[100]:.2f}   25th ${finals[500]:.2f}   "
          f"75th ${finals[1500]:.2f}   95th ${finals[1900]:.2f}")
    print(f"  share ending below ${BANKROLL:.2f}: {sum(1 for f in finals if f < BANKROLL)/len(finals):.1%}")
    print(f"  share losing >half: {sum(1 for f in finals if f < BANKROLL/2)/len(finals):.1%}")


if __name__ == "__main__":
    main()
