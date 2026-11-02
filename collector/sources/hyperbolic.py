"""Hyperbolic — GPU cloud; its public on-demand rental options (only listed while in stock)."""
from common import canon, http_json

NAME = "Hyperbolic"


def fetch():
    prices = []
    for o in http_json("https://api.hyperbolic.ai/v2/on-demand/rental-options"):
        gpu = canon(f"{o.get('gpuType', '')} {o.get('gpuFormFactor', '')}")
        n = o.get("gpuCount") or 1
        if gpu and o.get("enabled", True) and o.get("costPerHourCents"):
            prices.append((gpu, NAME, "on_demand", o["costPerHourCents"] / 100 / n))
    return prices, []
