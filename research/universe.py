"""Enumerate every Kalshi crypto + commodity series and rank by tradability.

There are ~270 crypto series alone, most of them one-off novelties with no
liquidity. What matters for strategy research is: does the series have enough
SETTLED history to test on, and enough volume that a fill is plausible.
"""
import httpx, sys, io, json, os, time, collections

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://api.elections.kalshi.com/trade-api/v2"
c = httpx.Client(timeout=45, headers={"Accept-Encoding": "gzip"})

CATS = ["Crypto", "Commodities"]


def get(path, params=None, tries=5):
    for i in range(tries):
        try:
            r = c.get(BASE + path, params=params or {})
            if r.status_code == 429:
                time.sleep(1.2 * (i + 1)); continue
            r.raise_for_status()
            return r.json()
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(0.6 * (i + 1))
    return {}


def f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


rows = []
for cat in CATS:
    series = get("/series", {"category": cat}).get("series", [])
    print(f"{cat}: {len(series)} series")
    for i, s in enumerate(series):
        tk = s.get("ticker")
        if not tk:
            continue
        # One page of settled markets is enough to judge depth and history.
        try:
            b = get("/markets", {"series_ticker": tk, "status": "settled", "limit": 200})
        except Exception:
            continue
        mk = [m for m in b.get("markets", []) if m.get("result") in ("yes", "no")]
        if not mk:
            continue
        vols = sorted(f(m.get("volume_fp") or m.get("volume")) for m in mk)
        med = vols[len(vols) // 2]
        rows.append({
            "cat": cat, "ticker": tk, "title": (s.get("title") or "")[:48],
            "freq": s.get("frequency"), "settled": len(mk),
            "median_vol": med, "max_vol": vols[-1],
            "total_vol": sum(vols),
            "has_cursor": bool(b.get("cursor")),
        })
        if (i + 1) % 40 == 0:
            print(f"   scanned {i+1}/{len(series)}")

rows.sort(key=lambda r: -r["total_vol"])
json.dump(rows, open("universe.json", "w"), indent=1)

print(f"\n{len(rows)} series with settled markets\n")
print(f"{'ticker':<20}{'cat':<13}{'freq':<13}{'n':>5}{'med vol':>11}{'total vol':>14}  title")
for r in rows[:45]:
    print(f"{r['ticker']:<20}{r['cat']:<13}{str(r['freq']):<13}{r['settled']:>5}"
          f"{r['median_vol']:>11,.0f}{r['total_vol']:>14,.0f}  {r['title']}")

print("\nby frequency (series with median volume > 1000):")
byf = collections.Counter(r["freq"] for r in rows if r["median_vol"] > 1000)
for k, v in byf.most_common():
    print(f"   {str(k):<14} {v}")
