"""Akash Network — decentralized compute marketplace, public console API.

Prices: per model min / median of the providers' bids (USD per GPU-hour).
Demand: total GPUs on the network and how many are leased right now.
"""
from common import canon, http_json

NAME = "Akash"


def model_name(m):
    model = m["model"].lower()
    if model.startswith("pro6000"):
        return "RTX PRO 6000"
    ram = float((m.get("ram") or "0").rstrip("Gi") or 0)
    return canon(f"{model} {m.get('interface', '')}", ram)


def fetch():
    data = http_json("https://console-api.akash.network/v1/gpu-prices")
    prices, totals = [], {}
    for m in data["models"]:
        gpu = model_name(m)
        if not gpu:
            continue
        p, avail = m.get("price") or {}, m.get("availability") or {}
        if p.get("min") and p.get("med"):
            prices.append((gpu, NAME, "on_demand", (p["min"], p["med"], m["providerAvailability"]["total"])))
        if avail.get("total"):
            t = totals.setdefault(gpu, [0, 0])
            t[0] += avail["total"]
            t[1] += avail["total"] - avail["available"]
    demand = [row for gpu, (total, rented) in totals.items()
              for row in [(gpu, NAME, "gpus_total", total), (gpu, NAME, "gpus_rented", rented)]]
    return prices, demand
