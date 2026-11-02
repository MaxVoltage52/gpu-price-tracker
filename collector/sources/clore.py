"""Clore.ai — peer-to-peer GPU marketplace (the JSON its public marketplace page loads).

Prices: available servers, USD per server per day -> per GPU-hour. On-demand and spot.
Demand: GPUs on all listed servers vs GPUs on rented servers.
"""
import re

from common import canon, http_json
from sources.lium import merge_counts

NAME = "Clore.ai"


def fetch():
    servers = http_json("https://clore.ai/webapi/marketplace/servers", timeout=120)["all_servers"]
    prices, demand = [], []
    for s in servers:
        spec = (s.get("specs") or {}).get("gpu") or ""
        m = re.fullmatch(r"\s*(\d+)x\s+(.+?)\s*", spec)
        if not m or "," in spec:
            continue  # mixed-GPU servers
        count = int(m.group(1))
        gpu = canon(m.group(2), (s["specs"].get("gpuram") or 0))
        if not gpu or not count:
            continue
        demand += [(gpu, NAME, "gpus_total", count), (gpu, NAME, "gpus_rented", count if s.get("rented") else 0)]
        if s.get("rented"):
            continue
        usd = (s.get("price") or {}).get("usd") or {}
        on_demand = usd.get("on_demand_usd") or usd.get("on_demand_clore")
        if on_demand:
            prices.append((gpu, NAME, "on_demand", on_demand / 24 / count))
        if usd.get("spot"):
            prices.append((gpu, NAME, "spot", usd["spot"] / 24 / count))
    return prices, merge_counts(demand)
