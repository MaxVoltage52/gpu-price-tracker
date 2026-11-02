"""Spheron — GPU aggregator reselling capacity from several clouds, public offers list.

Prices: every available offer, per GPU-hour; spot offers are tracked separately.
"""
import os

from common import canon, http_json

NAME = "Spheron"


def fetch():
    headers = {"Authorization": f"Bearer {os.environ['SPHERON_API_KEY']}"} if os.environ.get("SPHERON_API_KEY") else None
    data = http_json("https://app.spheron.ai/api/gpu-offers?limit=100", headers=headers)["data"]
    prices = []
    for group in data:
        gpu = canon(group.get("gpuModel") or group.get("displayName") or "")
        if not gpu:
            continue
        for o in group.get("offers", []):
            n = o.get("gpuCount") or 1
            if not o.get("available") or o.get("maintenance"):
                continue
            is_spot = str(o.get("offerId", "")).endswith("::spot")
            price = o.get("spot_price") if is_spot else o.get("price")
            if price:
                prices.append((gpu, NAME, "spot" if is_spot else "on_demand", price / n))
    return prices, []
