"""Shadeform — aggregator listing on-demand instances from ~18 GPU clouds (Lambda, Nebius, Crusoe, ...).

Needs a free API key (SHADEFORM_API_KEY, from shadeform.ai); skipped without one.
Prices: every instance type, per GPU-hour. Demand: share of instance-type/region pairs in stock.
"""
import os

from common import canon, http_json

NAME = "Shadeform"


def fetch():
    key = os.environ.get("SHADEFORM_API_KEY")
    if not key:
        return [], []
    types = http_json("https://api.shadeform.ai/v1/instances/types", headers={"X-API-KEY": key})["instance_types"]
    prices, stock = [], {}
    for t in types:
        cfg = t.get("configuration") or t
        n = cfg.get("num_gpus") or 0
        gpu = canon(f"{cfg.get('gpu_type', '')} {cfg.get('interconnect', '')}", cfg.get("vram_per_gpu_in_gb"))
        if not gpu or not n or not t.get("hourly_price"):
            continue
        prices.append((gpu, NAME, "on_demand", t["hourly_price"] / 100 / n))
        regions = t.get("availability") or []
        s = stock.setdefault(gpu, [0, 0])
        s[0] += sum(1 for r in regions if r.get("available"))
        s[1] += len(regions)
    demand = [(gpu, NAME, "in_stock_pct", round(a / b * 100, 1)) for gpu, (a, b) in stock.items() if b]
    return prices, demand
