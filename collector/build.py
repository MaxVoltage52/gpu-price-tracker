"""Turn the hourly snapshots in data/prices and data/demand into the JSON the website reads.

    python collector/build.py [--data data] [--out site/data]

Outputs
  summary.json      every GPU now: typical & cheapest price, changes, per-source prices, demand
  gpu/<slug>.json   one GPU over time: price trend, cheapest, utilization, per-source prices

Price trend = chained index. Each hour, every source's median is compared with that same
source's previous value, and the index moves by the geometric mean of those changes. Sources
appearing or dropping out therefore never cause jumps. "Typical" is today's median across
sources, carried back in time along the index.
"""
import argparse
import csv
import json
import math
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOUR = 3600
DAY = 24 * HOUR
CARRY = 6 * HOUR          # a source missing for up to this long keeps its last value
HOURLY_WINDOW = 31 * DAY
HEAT_HOURS = 48
HEAT_DAYS = 30
PERIODS = {"24h": DAY, "7d": 7 * DAY, "30d": 30 * DAY}
WINDOWS = {"1d": DAY, "7d": 7 * DAY, "30d": 30 * DAY, "90d": 90 * DAY, "1y": 365 * DAY, "all": None}  # site ranges
MARKETS = ["spot", "on_demand"]

FEATURED = ["B300", "B200", "H200", "H100 SXM"]
# name: (VRAM GB, tier). Order = display order. GPUs not listed are ignored.
CATALOG = {
    "B300": (288, "Blackwell"), "B200": (180, "Blackwell"),
    "H200": (141, "Hopper"), "H200 NVL": (141, "Hopper"), "H100 SXM": (80, "Hopper"),
    "H100 NVL": (94, "Hopper"), "H100 PCIe": (80, "Hopper"),
    "MI325X": (256, "AMD Instinct"), "MI300X": (192, "AMD Instinct"),
    "A100 80GB": (80, "Ampere"), "A100 40GB": (40, "Ampere"),
    "RTX PRO 6000": (96, "Workstation"), "L40S": (48, "Workstation"), "L40": (48, "Workstation"),
    "RTX 6000 Ada": (48, "Workstation"), "RTX A6000": (48, "Workstation"), "A40": (48, "Workstation"),
    "RTX 5090": (32, "Consumer"), "RTX 4090": (24, "Consumer"), "RTX 3090": (24, "Consumer"),
    "L4": (24, "Inference"), "A10": (24, "Inference"), "V100": (16, "Legacy"), "T4": (16, "Legacy"),
}
def _src(url, kind, unit, about):
    return {"url": url, "kind": kind, "unit": unit, "about": about}


# Display metadata, in display order. Sources without data in the current build are left out.
SOURCES = {
    "Vast.ai": _src("https://vast.ai", "Marketplace", "offers",
                    "Peer-to-peer marketplace. Spot = minimum interruptible bid. Demand = rented share of listed GPUs, per machine."),
    "Clore.ai": _src("https://clore.ai/marketplace", "Marketplace", "servers",
                     "Peer-to-peer marketplace (price per server-day). Demand = GPUs on rented vs all listed servers."),
    "Lium": _src("https://lium.io", "Marketplace", "GPUs", "Bittensor subnet 51 marketplace. Demand = current rental rate per GPU class."),
    "Akash": _src("https://akash.network/pricing/gpus/", "Decentralized", "providers",
                  "Decentralized compute network. Price = provider bids; demand = leased share of all GPUs."),
    "io.net": _src("https://io.net/explorer", "Decentralized", "listings", "Decentralized GPU network. Demand = hired vs total GPUs."),
    "Nosana": _src("https://nosana.com", "Decentralized", "markets", "Solana GPU network. Demand = nodes running jobs vs nodes queued idle."),
    "Salad": _src("https://salad.com", "Decentralized", "models",
                  "Consumer-GPU cloud; demand monitor utilization only (it publishes host earnings, not renter prices)."),
    "RunPod Community": _src("https://www.runpod.io/pricing", "GPU cloud", "SKUs", "Community Cloud list price; demand = single-GPU stock level."),
    "RunPod Secure": _src("https://www.runpod.io/pricing", "GPU cloud", "SKUs", "Secure Cloud list price; demand = single-GPU stock level."),
    "Lambda": _src("https://lambda.ai/pricing", "GPU cloud", "plans", "On-demand price per GPU-hour from the pricing page."),
    "CoreWeave": _src("https://www.coreweave.com/pricing", "GPU cloud", "plans", "On-demand and spot instance prices from the pricing page."),
    "Nebius": _src("https://nebius.com/prices", "GPU cloud", "plans", "On-demand and preemptible prices from the pricing page."),
    "Together AI": _src("https://www.together.ai/pricing", "GPU cloud", "plans", "On-demand and preemptible GPU-hour prices from the pricing page."),
    "Crusoe": _src("https://www.crusoe.ai/cloud/pricing", "GPU cloud", "plans", "On-demand GPU-hour prices from the pricing page."),
    "Hyperstack": _src("https://www.hyperstack.cloud/gpu-pricing", "GPU cloud", "plans", "On-demand and spot GPU-hour prices from the pricing page."),
    "Verda": _src("https://verda.com/pricing", "GPU cloud", "instance types", "Formerly DataCrunch. Public API, on-demand and spot per instance."),
    "DigitalOcean": _src("https://www.digitalocean.com/pricing/gpu-droplets", "GPU cloud", "plans", "GPU Droplet on-demand prices."),
    "Jarvislabs": _src("https://jarvislabs.ai/pricing", "GPU cloud", "plans", "On-demand and spot GPU-hour prices from the pricing page."),
    "Hyperbolic": _src("https://hyperbolic.ai", "GPU cloud", "offers", "On-demand rental options (only listed while in stock)."),
    "Cudo": _src("https://www.cudocompute.com", "GPU cloud", "data centers", "GPU component of the hourly VM price, per data center."),
    "Scaleway": _src("https://www.scaleway.com/en/pricing/gpu/", "GPU cloud", "offers",
                     "Public catalog (EUR, converted at the ECB rate); demand = live zone availability."),
    "OVHcloud": _src("https://www.ovhcloud.com/en/public-cloud/prices/", "GPU cloud", "plans",
                     "Public cloud catalog, pay-as-you-go (EUR excl. VAT, converted at the ECB rate)."),
    "Spheron": _src("https://spheron.network", "Aggregator", "offers", "Resold capacity from several clouds; on-demand and spot offers."),
    "Shadeform": _src("https://www.shadeform.ai", "Aggregator", "instance types",
                      "Instances across ~18 GPU clouds (needs a free API key); demand = share of instance types in stock."),
    "AWS": _src("https://aws.amazon.com/ec2/spot/pricing/", "Hyperscaler", "regions",
                "EC2 spot (all regions) and on-demand (4 regions) for one instance per GPU. Demand = Spot Advisor interruptions and discount."),
    "Azure": _src("https://azure.microsoft.com/pricing/details/virtual-machines/linux/", "Hyperscaler", "regions",
                  "Retail Prices API, Linux spot and pay-as-you-go across regions."),
    "Oracle Cloud": _src("https://www.oracle.com/cloud/compute/gpu/pricing/", "Hyperscaler", "SKUs", "Public price list, per GPU-hour."),
}
OUTLIER = 0.25  # a source whose median is below this share of the cross-source median is treated as a listing error


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def parse_ts(s):
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc).timestamp())


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def r4(v):
    return None if v is None else round(v, 4)


def read_csvs(folder):
    for path in sorted(folder.glob("**/*.csv")):
        with path.open(newline="") as f:
            yield from csv.DictReader(f)


def load(data_dir):
    price = defaultdict(dict)    # (gpu, source, market) -> {t: (min, median, n)}
    demand = defaultdict(dict)   # (gpu, source, metric) -> {t: value}
    stamps = set()
    for r in read_csvs(data_dir / "prices"):
        if r["gpu"] in CATALOG:
            t = parse_ts(r["ts"])
            stamps.add(t)
            price[(r["gpu"], r["source"], r["market"])][t] = (float(r["min"]), float(r["median"]), int(r["n"]))
    for r in read_csvs(data_dir / "demand"):
        if r["gpu"] in CATALOG:
            t = parse_ts(r["ts"])
            stamps.add(t)
            demand[(r["gpu"], r["source"], r["metric"])][t] = float(r["value"])
    return price, demand, sorted(stamps)


def latest(points, t, carry=CARRY):
    """Value at t, or the most recent one within `carry` seconds before it."""
    for off in range(0, carry + 1, HOUR):
        if t - off in points:
            return points[t - off]
    return None


def near(points, target, tol):
    for off in range(0, int(tol) + 1, HOUR):
        for t in (target - off, target + off):
            if t in points:
                return points[t]
    return None


def changes(points, now_t, relative=True):
    now = points.get(now_t)
    out = {}
    for label, span in PERIODS.items():
        then = near(points, now_t - span, max(2 * HOUR, span * 0.1))
        if now is None or then is None:
            out[label] = None
        elif relative:
            out[label] = round((now / then - 1) * 100, 2) if then else None
        else:
            out[label] = round(now - then, 2)
    return out


def chained_index(per_source, stamps):
    idx, last, cur = {}, {}, None
    for t in stamps:
        obs = {s: pts[t] for s, pts in per_source.items() if t in pts and pts[t] > 0}
        if not obs:
            if cur is not None and any(t - lt <= CARRY for lt, _ in last.values()):
                idx[t] = cur  # every source skipped this hour: hold the level
            continue
        logs = [math.log(p / last[s][1]) for s, p in obs.items() if s in last and t - last[s][0] <= CARRY]
        cur = 100.0 if cur is None else cur * math.exp(sum(logs) / len(logs)) if logs else cur
        idx[t] = cur
        for s, p in obs.items():
            last[s] = (t, p)
    return idx


def window_mean(points, now_t, span):
    """Average of the hourly values in the last `span` seconds (None = all history), rounded."""
    vals = [v for t, v in points.items() if span is None or t > now_t - span]
    return round(sum(vals) / len(vals), 1) if vals else None


def sane(entries):
    """Drop sources whose median is implausibly far below the others (stale or mislabelled listings)."""
    entries = [(s, v) for s, v in entries if v]
    if len(entries) < 3:
        return entries
    ref = statistics.median(v[1] for _, v in entries)
    return [(s, v) for s, v in entries if v[1] >= OUTLIER * ref]


def daily(points):
    days = defaultdict(list)
    for t, v in points.items():
        if v is not None:
            days[t - t % DAY].append(v)
    return {d: sum(v) / len(v) for d, v in days.items()}


def build(data_dir, out_dir):
    price, demand, stamps = load(data_dir)
    if not stamps:
        raise SystemExit(f"no snapshots in {data_dir}")
    now_t = stamps[-1]
    hourly_t = [t for t in stamps if t > now_t - HOURLY_WINDOW]
    (out_dir / "gpu").mkdir(parents=True, exist_ok=True)
    src_order = {s: i for i, s in enumerate(SOURCES)}
    gpus_out, heat = [], {}

    for gpu, (vram, tier) in CATALOG.items():
        keys = [k for k in price if k[0] == gpu]
        dkeys = [k for k in demand if k[0] == gpu]
        if not keys and not dkeys:
            continue
        entry = {"gpu": gpu, "slug": slug(gpu), "vram": vram, "tier": tier, "market": {}}
        series = {"typical": {}, "cheapest": {}, "sources": {}}

        for market in MARKETS:
            mk = sorted((k for k in keys if k[2] == market), key=lambda k: src_order.get(k[1], 99))
            if not mk:
                continue
            medians = {k[1]: {t: v[1] for t, v in price[k].items()} for k in mk}
            idx = chained_index(medians, stamps)
            current = sane([(k[1], latest(price[k], now_t)) for k in mk])
            cheapest = {}
            for t in stamps:
                vals = sane([(k[1], price[k][t]) for k in mk if t in price[k]])
                if vals:
                    cheapest[t] = min((v[0], s) for s, v in vals)
            if not current or now_t not in idx:
                continue
            # lower median: with few sources, one hyperscaler list price shouldn't drag "typical" up
            typical_now = statistics.median_low(v[1] for _, v in current)
            typical = {t: typical_now * v / idx[now_t] for t, v in idx.items()}
            low = min(current, key=lambda x: x[1][0])
            entry["market"][market] = {
                "typical": r4(typical_now), "cheapest": low[1][0], "cheapest_src": low[0],
                "chg": changes(idx, now_t),
                "prices": [{"source": s, "min": v[0], "median": v[1], "n": v[2]} for s, v in current],
            }
            series["typical"][market] = typical
            series["cheapest"][market] = {t: v[0] for t, v in cheapest.items()}
            for k in mk:
                series["sources"][f"{k[1]}|{market}"] = medians[k[1]]

        # demand: utilization = rented / listed GPUs, summed over marketplaces that report counts
        util_src = sorted({k[1] for k in dkeys if k[2] == "gpus_total"}, key=lambda s: src_order.get(s, 99))
        util = {}
        for t in stamps:
            tot = ren = 0
            for s in util_src:
                a, b = latest(demand[(gpu, s, "gpus_total")], t), latest(demand.get((gpu, s, "gpus_rented"), {}), t)
                if a and b is not None:
                    tot, ren = tot + a, ren + b
            if tot:
                util[t] = (ren / tot * 100, ren, tot)
        d = {}
        if now_t in util:
            u, ren, tot = util[now_t]
            d = {"util": round(u, 1), "rented": int(ren), "total": int(tot),
                 "util_chg": changes({t: v[0] for t, v in util.items()}, now_t, relative=False), "by_source": {}}
            for s in util_src:
                a, b = latest(demand[(gpu, s, "gpus_total")], now_t), latest(demand.get((gpu, s, "gpus_rented"), {}), now_t)
                if a:
                    d["by_source"][s] = {"util": round(b / a * 100, 1), "rented": int(b), "total": int(a)}
        for metric in ("interrupt_pct", "spot_discount_pct"):
            v = latest(demand.get((gpu, "AWS", metric), {}), now_t)
            if v is not None:
                d.setdefault("aws", {})[metric] = v
        for k in sorted(dkeys, key=lambda k: src_order.get(k[1], 99)):
            v = latest(demand[k], now_t)
            if v is None:
                continue
            if k[2] == "stock":
                d.setdefault("stock", {})[k[1]] = int(v)
            elif k[2] == "util_pct":  # utilization published without counts (not part of the weighted total)
                d.setdefault("by_source", {})[k[1]] = {"util": round(v, 1)}
            elif k[2] == "in_stock_pct":
                d.setdefault("in_stock", {})[k[1]] = v

        # sold out: a source has no free GPUs of this model (counts) or reports no stock
        stock_src = sorted({k[1] for k in dkeys if k[2] == "stock"}, key=lambda s: src_order.get(s, 99))

        def sold_out(s, t, carry=0):
            if s in util_src:
                a = latest(demand[(gpu, s, "gpus_total")], t, carry)
                b = latest(demand.get((gpu, s, "gpus_rented"), {}), t, carry)
                return None if not a or b is None else b >= a
            v = latest(demand[(gpu, s, "stock")], t, carry)
            return None if v is None else v == 0

        avail_src = util_src + stock_src
        ratio, per_src = {}, {s: {} for s in avail_src}
        for t in stamps:
            flags = {s: f for s in avail_src if (f := sold_out(s, t)) is not None}
            for s, f in flags.items():
                per_src[s][t] = 100.0 if f else 0.0
            if flags:
                ratio[t] = sum(flags.values()) / len(flags) * 100
        if ratio:
            now_flags = {s: f for s in avail_src if (f := sold_out(s, now_t, CARRY)) is not None}
            d["soldout"] = {
                "now": [s for s, f in now_flags.items() if f], "of": len(now_flags),
                "share": {w: window_mean(ratio, now_t, span) for w, span in WINDOWS.items()},
                "full": {w: window_mean({t: 100.0 if r >= 100 else 0.0 for t, r in ratio.items()}, now_t, span)
                         for w, span in WINDOWS.items()},
                "sources": {s: {w: window_mean(per_src[s], now_t, WINDOWS[w]) for w in ("1d", "7d", "30d")}
                            for s in avail_src if per_src[s]},
            }
        entry["demand"] = d
        gpus_out.append(entry)

        util_pct = {t: v[0] for t, v in util.items()}
        listed, rented = {t: v[2] for t, v in util.items()}, {t: v[1] for t, v in util.items()}
        if util_pct or ratio:
            udaily, sdaily = daily(util_pct), daily(ratio)
            hours = range(now_t - (HEAT_HOURS - 1) * HOUR, now_t + 1, HOUR)
            days = range(now_t - now_t % DAY - (HEAT_DAYS - 1) * DAY, now_t + 1, DAY)
            heat[gpu] = {"h": [r4(util_pct.get(t)) for t in hours], "d": [r4(udaily.get(t)) for t in days],
                         "sh": [r4(ratio.get(t)) for t in hours], "sd": [r4(sdaily.get(t)) for t in days]}

        def block(ts, pick):
            def col(points):
                points = pick(points)
                return [r4(points.get(t)) for t in ts]
            return {
                "t": ts,
                "typical": {m: col(v) for m, v in series["typical"].items()},
                "cheapest": {m: col(v) for m, v in series["cheapest"].items()},
                "util": col(util_pct) if util_pct else [],
                "listed": col(listed) if listed else [],
                "rented": col(rented) if rented else [],
                "soldout": col(ratio) if ratio else [],
                "sources": {k: col(v) for k, v in series["sources"].items()},
            }
        all_days = sorted({t - t % DAY for t in stamps})
        hist = {"gpu": gpu, "hourly": block(hourly_t, lambda v: v),
                "daily": block(all_days, daily)}
        (out_dir / "gpu" / f"{slug(gpu)}.json").write_text(json.dumps(hist, separators=(",", ":")))

    used = {p["source"] for g in gpus_out for m in g["market"].values() for p in m["prices"]}
    used |= {s for g in gpus_out for s in list(g["demand"].get("by_source", {})) + list(g["demand"].get("stock", {}))}
    used |= {"AWS"} if any("aws" in g["demand"] for g in gpus_out) else set()
    summary = {
        "generated": iso(int(datetime.now(timezone.utc).timestamp())),
        "first_ts": iso(stamps[0]), "last_ts": iso(now_t), "snapshots": len(stamps),
        "featured": [g for g in FEATURED if any(x["gpu"] == g for x in gpus_out)],
        "sources": {s: m for s, m in SOURCES.items() if s in used},
        "gpus": gpus_out,
        "heat": {"hour0": iso(now_t - (HEAT_HOURS - 1) * HOUR), "day0": iso(now_t - now_t % DAY - (HEAT_DAYS - 1) * DAY),
                 "rows": heat},
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, separators=(",", ":")))
    print(f"built {len(gpus_out)} GPUs, {len(summary['sources'])} sources, {len(stamps)} snapshots "
          f"({iso(stamps[0])} .. {iso(now_t)}) -> {out_dir}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=ROOT / "site" / "data")
    a = ap.parse_args()
    build(a.data, a.out)
