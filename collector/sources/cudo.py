"""Cudo Compute — GPU cloud, public machine-type list.

Prices: the GPU component of the hourly price (vCPU and RAM are billed on top), per data center.
"""
from common import canon, http_json

NAME = "Cudo"


def fetch():
    prices = []
    for m in http_json("https://rest.compute.cudo.org/v1/vms/machine-types-2")["machineTypes"]:
        gpu = canon((m.get("gpuModel") or "").split(" (")[0])
        price = float((m.get("gpuPriceHr") or {}).get("value") or 0)
        if gpu and price:
            prices.append((gpu, NAME, "on_demand", price))
    return prices, []
