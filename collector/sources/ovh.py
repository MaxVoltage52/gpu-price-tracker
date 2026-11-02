"""OVHcloud — public cloud order catalog (EUR, excl. VAT), Linux pay-as-you-go GPU instances."""
from common import canon, eur_usd, http_json

NAME = "OVHcloud"


def fetch():
    catalog = http_json("https://eu.api.ovh.com/1.0/order/catalog/public/cloud?ovhSubsidiary=IE", timeout=120)
    rate = eur_usd() if catalog.get("locale", {}).get("currencyCode", "EUR") == "EUR" else 1.0
    prices = []
    for a in catalog["addons"]:
        gpu_spec = ((a.get("blobs") or {}).get("technical") or {}).get("gpu")
        code = a.get("planCode", "")
        if a.get("product") != "publiccloud-instance" or not code.endswith(".consumption") or code.startswith("win-") or not gpu_spec:
            continue
        p = next((x for x in a.get("pricings", []) if x.get("intervalUnit") == "hour"), None)
        gpu, n = canon(gpu_spec.get("model", ""), (gpu_spec.get("memory") or {}).get("size")), gpu_spec.get("number") or 0
        if gpu and n and p and p.get("price"):
            prices.append((gpu, NAME, "on_demand", p["price"] / 1e8 * rate / n))
    return prices, []
