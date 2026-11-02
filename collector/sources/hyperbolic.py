"""Hyperbolic — GPU cloud; its public on-demand rental options (only listed while in stock).

Commitment: its reserve options give a % discount for 7/14/30-day terms, applied to the on-demand price.
"""
from common import canon, http_json, term_market

NAME = "Hyperbolic"


def fetch():
    on_demand = []
    for o in http_json("https://api.hyperbolic.ai/v2/on-demand/rental-options"):
        gpu = canon(f"{o.get('gpuType', '')} {o.get('gpuFormFactor', '')}")
        n = o.get("gpuCount") or 1
        if gpu and o.get("enabled", True) and o.get("costPerHourCents"):
            on_demand.append((gpu, NAME, "on_demand", o["costPerHourCents"] / 100 / n))

    # several options can fall in one bucket (7, 14 and 30 days -> 1 month): use the longest
    longest = {}
    for opt in http_json("https://api.hyperbolic.ai/v2/on-demand/reserve-options").get("options") or []:
        days, pct = opt.get("termPeriodDays"), opt.get("discountPercentage")
        if days and pct is not None and days >= longest.get(term_market(days), (0, 0))[0]:
            longest[term_market(days)] = (days, pct)
    terms = [(g, NAME, market, p * (1 - pct / 100)) for market, (_, pct) in longest.items() for g, _, _, p in on_demand]
    return on_demand + terms, []
