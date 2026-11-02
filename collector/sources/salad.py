"""Salad — distributed consumer-GPU cloud, public demand monitor.

Demand only: network utilization per GPU model (consumer cards). Salad publishes what hosts
earn, not what renters pay, so no prices are taken from here.
"""
from common import canon, http_json

NAME = "Salad"


def fetch():
    demand = {}
    for g in http_json("https://app-api.salad.com/api/v2/demand-monitor/gpu"):
        gpu = canon(g.get("name") or g.get("displayName") or "")
        if gpu and g.get("utilizationPct") is not None:
            demand[gpu] = max(demand.get(gpu, 0), min(100, g["utilizationPct"]))
    return [], [(gpu, NAME, "util_pct", v) for gpu, v in demand.items()]
