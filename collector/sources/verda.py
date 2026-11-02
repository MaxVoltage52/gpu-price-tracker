"""Verda (formerly DataCrunch) — GPU cloud, public instance-type API.

Prices: on-demand and spot price per instance, divided by its GPU count.
"""
import re

from common import canon, http_json

NAME = "Verda"


def fetch():
    prices = []
    for it in http_json("https://api.verda.com/v1/instance-types"):
        gpu_info = it.get("gpu") or {}
        n = gpu_info.get("number_of_gpus") or 0
        gpu = canon(re.sub(r"^\d+x\s*", "", gpu_info.get("description") or it.get("model") or ""))
        if not gpu or not n:
            continue
        for market, field in (("on_demand", "price_per_hour"), ("spot", "spot_price")):
            price = float(it.get(field) or 0)
            if price:
                prices.append((gpu, NAME, market, price / n))
    return prices, []
