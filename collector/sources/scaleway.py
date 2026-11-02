"""Scaleway — public product catalog (EUR per minute per instance) and live server availability.

Prices: GPU instances per zone, converted to USD per GPU-hour.
Demand: stock level per GPU from the zone availability API (available / scarce / shortage).
"""
from common import canon, eur_usd, http_json

NAME = "Scaleway"
CATALOG = "https://api.scaleway.com/product-catalog/v2alpha1/public-catalog/products?product_types=instance&page_size=500&page={}"
STOCK = {"available": 3, "scarce": 1, "shortage": 0}


def fetch():
    rate = eur_usd()
    prices, offers = [], {}  # offer id -> (gpu, zones)
    for page in range(1, 6):
        products = http_json(CATALOG.format(page))["products"]
        for p in products:
            hw = ((p.get("properties") or {}).get("hardware") or {}).get("gpu") or {}
            n, zone = hw.get("count") or 0, (p.get("locality") or {}).get("zone")
            gpu = canon(hw.get("type") or "")
            retail = (p.get("price") or {}).get("retail_price") or {}
            per_minute = (retail.get("units") or 0) + (retail.get("nanos") or 0) / 1e9
            if not gpu or not n or not zone:
                continue
            offers.setdefault(p["product"], (gpu, set()))[1].add(zone)
            if per_minute and retail.get("currency_code", "EUR") == "EUR":
                prices.append((gpu, NAME, "on_demand", per_minute * 60 * rate / n))
        if len(products) < 500:
            break

    best = {}
    for zone in sorted({z for _, zones in offers.values() for z in zones}):
        servers = http_json(f"https://api.scaleway.com/instance/v1/zones/{zone}/products/servers/availability")["servers"]
        for offer, (gpu, zones) in offers.items():
            if zone in zones and offer in servers:
                level = STOCK.get(servers[offer].get("availability"), 0)
                best[gpu] = max(best.get(gpu, 0), level)
    return prices, [(gpu, NAME, "stock", v) for gpu, v in best.items()]
