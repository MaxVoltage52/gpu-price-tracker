"""io.net — decentralized GPU network, public explorer API.

Prices: base price per GPU-hour per hardware type.
Demand: total vs hired GPUs per hardware type.
"""
from common import canon, http_json
from sources.lium import merge_counts

NAME = "io.net"


def fetch():
    data = http_json("https://api.io.solutions/v1/io-explorer/network/market-snapshot")["data"]
    prices, demand = [], []
    for h in data:
        if h.get("type") != "GPU":
            continue
        gpu = canon(h["hardware_name"])
        if not gpu:
            continue
        if h.get("price"):
            prices.append((gpu, NAME, "on_demand", h["price"]))
        if h.get("total"):
            demand += [(gpu, NAME, "gpus_total", h["total"]), (gpu, NAME, "gpus_rented", h.get("hired") or 0)]
    return prices, merge_counts(demand)
