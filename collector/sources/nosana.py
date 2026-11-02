"""Nosana — decentralized GPU network on Solana, public API.

Prices: each GPU market's price per hour (a market may bundle several GPUs, e.g. "(6x)").
Demand: nodes running jobs vs nodes queued idle in each market.
"""
import re

from common import canon, http_json
from sources.lium import merge_counts

NAME = "Nosana"
API = "https://api.nosana.com"


def parse(name):
    m = re.match(r"(.+?)\s*\((\d+)x\)\s*$", name)
    base, count = (m.group(1), int(m.group(2))) if m else (name, 1)
    base = re.sub(r"^(NVIDIA\s+)?(\d{4})\b", r"RTX \2", base.strip())  # "NVIDIA 5090" -> "RTX 5090"
    return canon(base), count


def fetch():
    prices_by_market = {p["address"]: p for p in http_json(f"{API}/markets/prices")}
    markets = {m["address"]: m for m in http_json(f"{API}/markets/")}
    running = http_json(f"{API}/jobs/running")
    queued = {}
    for n in http_json(f"{API}/nodes/queued-nodes"):
        queued[n["marketAddress"]] = queued.get(n["marketAddress"], 0) + 1

    prices, demand = [], []
    for addr in set(markets) | set(prices_by_market):
        name = (prices_by_market.get(addr) or markets.get(addr) or {}).get("name", "")
        gpu, count = parse(name)
        if not gpu:
            continue
        price = (prices_by_market.get(addr) or {}).get("usd_reward_per_hour")
        if price:
            prices.append((gpu, NAME, "on_demand", price / count))
        busy, idle = (running.get(addr) or {}).get("running", 0), queued.get(addr, 0)
        if busy or idle:
            demand += [(gpu, NAME, "gpus_total", (busy + idle) * count), (gpu, NAME, "gpus_rented", busy * count)]
    return prices, merge_counts(demand)
