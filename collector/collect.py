"""Fetch current GPU prices and demand signals from every source and save one hourly snapshot.

    python collector/collect.py [--only vast,aws]

Writes
  data/prices/YYYY/MM/DD.csv   ts, gpu, source, market, min, median, n   (USD per GPU-hour)
  data/demand/YYYY/MM/DD.csv   ts, gpu, source, metric, value
Sources live in collector/sources/, one module each; no API keys are needed.
"""
import argparse
import csv
import statistics
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from sources import ALL

ROOT = Path(__file__).resolve().parent.parent
PRICE_FIELDS = ["ts", "gpu", "source", "market", "min", "median", "n"]
DEMAND_FIELDS = ["ts", "gpu", "source", "metric", "value"]
ANNOUNCED_FIELDS = ["ts", "gpu", "source", "market", "price", "effective"]


def summarise_prices(ts, observations):
    groups, pre = {}, {}
    for gpu, source, market, price in observations:
        if isinstance(price, tuple):
            pre[(gpu, source, market)] = price
        elif price > 0:
            groups.setdefault((gpu, source, market), []).append(price)
    rows = [{"ts": ts, "gpu": g, "source": s, "market": m, "min": round(min(v), 4),
             "median": round(statistics.median(v), 4), "n": len(v)} for (g, s, m), v in groups.items()]
    rows += [{"ts": ts, "gpu": g, "source": s, "market": m, "min": round(lo, 4), "median": round(med, 4), "n": n}
             for (g, s, m), (lo, med, n) in pre.items() if (g, s, m) not in groups]
    return sorted(rows, key=lambda r: (r["gpu"], r["source"], r["market"]))


def append(path, fields, ts, rows):
    """Add rows to a day file. A re-run in the same hour replaces that hour's rows, but only for the
    sources that reported this time (so `--only` runs and failed sources keep earlier data)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = {r["source"] for r in rows}
    keep = []
    if path.exists():
        with path.open(newline="") as f:
            keep = [r for r in csv.DictReader(f) if r["ts"] != ts or r["source"] not in fresh]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(keep + rows)


def has_snapshot(path, ts):
    if not path.exists():
        return False
    with path.open(newline="") as f:
        return any(r["ts"] == ts for r in csv.DictReader(f))


def run(module):
    t = time.time()
    try:
        prices, demand, *announced = module.fetch()
        return module.NAME, prices, demand, (announced or [[]])[0], None, time.time() - t
    except Exception as e:  # one source being down must never lose the others
        traceback.print_exc()
        return module.NAME, [], [], [], e, time.time() - t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated source module names, e.g. vast,aws")
    ap.add_argument("--skip-if-fresh", action="store_true",
                    help="do nothing if this hour already has a snapshot (for redundant schedules)")
    args = ap.parse_args()
    modules = [m for m in ALL if not args.only or m.__name__.split(".")[-1] in args.only.split(",")]

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    ts = now.strftime("%Y-%m-%dT%H:%MZ")
    if args.skip_if_fresh and has_snapshot(ROOT / "data" / "prices" / now.strftime("%Y/%m/%d.csv"), ts):
        print(f"{ts} already has a snapshot; nothing to do")
        return
    prices, demand, announced, failed = [], [], [], []
    with ThreadPoolExecutor(max_workers=8) as pool:  # different hosts, so run them side by side
        for name, p, d, a, err, secs in pool.map(run, modules):
            print(f"{name:14} {len(p):5} prices {len(d):4} demand  {secs:6.1f}s" + (f"  FAILED: {err!r}" if err else ""))
            prices += p
            demand += d
            announced += a
            if err:
                failed.append(name)
    if not prices and not demand:
        sys.exit("nothing collected")

    price_rows = summarise_prices(ts, prices)
    demand_rows = sorted(({"ts": ts, "gpu": g, "source": s, "metric": m, "value": v} for g, s, m, v in demand),
                         key=lambda r: (r["gpu"], r["source"], r["metric"]))
    day = now.strftime("%Y/%m/%d.csv")
    append(ROOT / "data" / "prices" / day, PRICE_FIELDS, ts, price_rows)
    append(ROOT / "data" / "demand" / day, DEMAND_FIELDS, ts, demand_rows)
    if announced:
        rows = sorted({(g, s, m, round(p, 4), e) for g, s, m, p, e in announced})
        append(ROOT / "data" / "announced" / day, ANNOUNCED_FIELDS, ts,
               [{"ts": ts, "gpu": g, "source": s, "market": m, "price": p, "effective": e} for g, s, m, p, e in rows])
    print(f"{ts}: {len(price_rows)} price rows, {len(demand_rows)} demand rows" +
          (f"; failed: {', '.join(failed)}" if failed else ""))
    if len(failed) == len(modules):
        sys.exit(1)


if __name__ == "__main__":
    main()
