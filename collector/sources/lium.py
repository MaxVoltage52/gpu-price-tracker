"""Lium (Bittensor subnet 51) — GPU rental marketplace.

Prices: per GPU class, current price and median listed price (USD per GPU-hour).
Demand: total GPUs per class and the share rented right now.
"""
from common import canon, http_json

NAME = "Lium"


def fetch():
    prices, demand = [], []
    for c in http_json("https://lium.io/api/machines"):
        total = c.get("total_gpu_count") or 0
        gpu = canon(c.get("gpu_class") or c["name"])
        if not gpu or not total:
            continue
        price, median = c.get("price"), c.get("median_price") or c.get("price")
        if price and median:
            prices.append((gpu, NAME, "on_demand", (min(price, median), median, total)))
        if c.get("rental_rate") is not None:
            demand += [(gpu, NAME, "gpus_total", total), (gpu, NAME, "gpus_rented", round(total * c["rental_rate"]))]
    return prices, merge_counts(demand)


def merge_counts(rows):
    """Several provider classes can map to one GPU (e.g. PCIe and SXM variants): add their counts up."""
    out = {}
    for gpu, source, metric, value in rows:
        out[(gpu, source, metric)] = out.get((gpu, source, metric), 0) + value
    return [(g, s, m, v) for (g, s, m), v in out.items()]
