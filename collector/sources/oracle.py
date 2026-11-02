"""Oracle Cloud (OCI) — public price list API, list price per GPU-hour."""
import re

from common import canon, http_json

NAME = "Oracle Cloud"


def fetch():
    items = http_json("https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?currencyCode=USD")["items"]
    prices = []
    for i in items:
        name = i.get("displayName") or ""
        if i.get("metricName") != "GPU Per Hour" or not re.search(r"Compute - GPU -", name):
            continue
        if any(x in name for x in ("Cloud@Customer", "AI Enterprise", "Commit")):
            continue  # on-prem, software licences and committed-use lines
        gpu = canon(name.rsplit(" - ", 1)[-1].replace("A100 80", "A100 80GB"))
        try:
            price = float(i["currencyCodeLocalizations"][0]["prices"][0]["value"])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if gpu and price:
            prices.append((gpu, NAME, "on_demand", price))
    return prices, []
