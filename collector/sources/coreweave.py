"""CoreWeave — GPU cloud, public pricing page (coreweave.com/pricing).

Prices: on-demand and spot price of each instance in the "On-demand GPU instances" section
(one row per region), divided by the GPU count shown on that row. Reserved capacity is not listed.
"""
import re

from common import canon, http

NAME = "CoreWeave"
URL = "https://www.coreweave.com/pricing"


def _num(s):
    m = re.search(r"\d[\d,]*\.?\d*", s or "")
    return float(m.group().replace(",", "")) if m else None


def _meta(item, label):
    """Value printed above a caption, e.g. <div ...>8</div><div>GPU Count</div>."""
    m = re.search(r">([^<]*)</div>\s*<div>\s*%s\s*</div>" % re.escape(label), item, re.I)
    return _num(m.group(1)) if m else None


def _price(item, label):
    """Value after a caption, e.g. On-Demand Price: <span ...>$68.80</span>."""
    m = re.search(re.escape(label) + r":\s*(?:<[^>]+>\s*)*([^<]*)", item, re.I)
    return _num(m.group(1)) if m else None


def fetch():
    html = http(URL).decode("utf-8", "replace")
    sec = re.search(r"<h2[^>]*>\s*On-demand GPU instances\s*</h2>(.*?)(?=<h2[\s>]|\Z)", html, re.S | re.I)
    if not sec:
        raise RuntimeError(f"CoreWeave: 'On-demand GPU instances' section not found on {URL}")
    prices, rows = [], 0
    for item in re.split(r'<div role="listitem"', sec.group(1))[1:]:
        name = re.search(r'<h3[^>]*\bdata-product="[^"]*"[^>]*>(.*?)</h3>', item, re.S)
        count = _meta(item, "GPU Count")
        if not name or not count:
            continue
        rows += 1
        model = re.sub(r"<[^>]+>|\(.*?\)", "", name.group(1)).strip()
        gpu = canon(model, _meta(item, "VRAM"))
        for market, label in (("on_demand", "On-Demand Price"), ("spot", "Spot Price")):
            p = _price(item, label)
            if gpu and p and 0.05 <= p / count <= 50:
                prices.append((gpu, NAME, market, round(p / count, 4)))
    if not prices:
        raise RuntimeError(f"CoreWeave: {rows} GPU rows found but no prices parsed from {URL} "
                           "(expected 'GPU Count' caption and 'On-Demand Price:' / 'Spot Price:' labels)")
    return prices, []
