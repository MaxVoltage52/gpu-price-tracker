"""Vast.ai — open peer-to-peer marketplace.

Prices: every available offer, on-demand (listed price) and spot (minimum interruptible bid).
Demand: per machine, how many of its GPUs are rented. Vast lists one machine as several
bundles (1, 2, 4, 8 GPUs), so we count machines, not offers. `rentable: false` returns the
bundles that are currently rented out.

Anonymous search returns at most 64 offers, so we page through each model in price order.
"""
import json
import time
import urllib.parse

from common import canon, http_json

NAME = "Vast.ai"
GPU_NAMES = [
    "B300", "B200", "H200", "H200 NVL", "H100 SXM", "H100 PCIE", "H100 NVL",
    "A100 SXM4", "A100 PCIE", "A100X", "L40S", "L40", "L4", "A10",
    "RTX PRO 6000 WS", "RTX PRO 6000 S", "RTX PRO 6000 Max-Q", "RTX 6000Ada",
    "RTX A6000", "A40", "RTX 5090", "RTX 4090", "RTX 3090", "Tesla V100", "Tesla T4",
]
DELAY = 1.0  # seconds between requests; the anonymous API rate-limits bursts
PASSES = {  # name -> (extra filters, vast "type", price field)
    "on_demand": ({"rentable": {"eq": True}, "rented": {"eq": False}}, "on-demand", "dph_total"),
    "spot": ({"rentable": {"eq": True}, "rented": {"eq": False}}, "bid", "min_bid"),
    "rented": ({"rentable": {"eq": False}}, "on-demand", "dph_total"),
}


def offers(gpu_name, filters, vast_type, key, max_pages=20):
    seen, cursor = {}, 0.0
    for _ in range(max_pages):
        q = dict(filters, external={"eq": False}, gpu_name={"eq": gpu_name}, type=vast_type,
                 order=[[key, "asc"]], limit=64)
        q[key] = {"gte": cursor}
        page = http_json("https://console.vast.ai/api/v0/bundles/?" + urllib.parse.urlencode({"q": json.dumps(q)}))["offers"]
        time.sleep(DELAY)
        fresh = [o for o in page if o["id"] not in seen]
        for o in page:
            seen[o["id"]] = o
        if len(page) < 64 or not fresh:
            break
        cursor = max(o[key] for o in page)
    return [o for o in seen.values() if o.get("verification") != "deverified" and o.get("num_gpus")]


def fetch():
    prices, machines = [], {}  # (gpu, machine_id) -> [total GPUs, free GPUs]
    for name in GPU_NAMES:
        for pass_name, (filters, vast_type, key) in PASSES.items():
            for o in offers(name, filters, vast_type, key):
                gpu = canon(o["gpu_name"], (o.get("gpu_ram") or 0) / 1024)
                if not gpu:
                    continue
                n = o["num_gpus"]
                m = machines.setdefault((gpu, o["machine_id"]), [0, 0])
                m[0] = max(m[0], round(n / o["gpu_frac"]) if o.get("gpu_frac") else n)
                if pass_name == "rented":
                    continue
                if pass_name == "on_demand":
                    m[1] = max(m[1], n)
                if o.get(key, 0) > 0:
                    prices.append((gpu, NAME, pass_name, o[key] / n))
    totals = {}
    for (gpu, _), (total, free) in machines.items():
        t = totals.setdefault(gpu, [0, 0])
        t[0] += total
        t[1] += max(0, total - free)
    demand = [row for gpu, (total, rented) in totals.items()
              for row in [(gpu, NAME, "gpus_total", total), (gpu, NAME, "gpus_rented", rented)]]
    return prices, demand
