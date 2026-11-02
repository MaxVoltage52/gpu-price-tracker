"""Azure — public Retail Prices API, every region.

Prices: Linux spot and pay-as-you-go. Commitment: 1/3/5-year reservations (listed as the total price
for the term, so divided by the hours in it) -> term_365d / term_1095d / term_1825d.
"""
import re
import urllib.parse

from common import http_json, term_market

NAME = "Azure"
SKUS = {
    "Standard_ND96isr_H200_v5": ("H200", 8),
    "Standard_ND96isr_H100_v5": ("H100 SXM", 8),
    "Standard_NC40ads_H100_v5": ("H100 NVL", 1),
    "Standard_ND96amsr_A100_v4": ("A100 80GB", 8),
    "Standard_NC24ads_A100_v4": ("A100 80GB", 1),
    "Standard_ND96asr_v4": ("A100 40GB", 8),
    "Standard_ND96isr_MI300X_v5": ("MI300X", 8),
    "Standard_NV36ads_A10_v5": ("A10", 1),
    "Standard_NC4as_T4_v3": ("T4", 1),
    "Standard_NC6s_v3": ("V100", 1),
}


def fetch():
    skus = " or ".join(f"armSkuName eq '{s}'" for s in SKUS)
    flt = f"serviceName eq 'Virtual Machines' and (priceType eq 'Consumption' or priceType eq 'Reservation') and ({skus})"
    url = "https://prices.azure.com/api/retail/prices?" + urllib.parse.urlencode({"$filter": flt})
    prices = []
    while url:
        page = http_json(url)
        for item in page["Items"]:
            if "Windows" in item["productName"] or "Low Priority" in item["skuName"]:
                continue
            if item.get("unitOfMeasure") != "1 Hour" or not item["unitPrice"]:
                continue
            gpu, count = SKUS[item["armSkuName"]]
            if item.get("type") == "Reservation" or item.get("priceType") == "Reservation":
                years = re.match(r"(\d+)\s*Year", item.get("reservationTerm") or "")
                if years:
                    hours = int(years.group(1)) * 8760
                    prices.append((gpu, NAME, term_market(int(years.group(1)) * 365), item["unitPrice"] / hours / count))
                continue
            market = "spot" if "Spot" in item["skuName"] else "on_demand"
            prices.append((gpu, NAME, market, item["unitPrice"] / count))
        url = page.get("NextPageLink")
    return prices, []
