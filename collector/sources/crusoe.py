"""Crusoe Cloud — GPU cloud, public pricing page (Webflow CMS, laid out column by column).

Prices: the "Current on-demand" column of the "GPU instances pricing" table, per GPU-hour.
Announced: the "New on-demand" column with its effective date, when the page announces one.
"Contact sales" rows are skipped.
"""
import html
import re
from datetime import datetime

from common import canon, http

NAME = "Crusoe"
URL = "https://www.crusoe.ai/cloud/pricing"


def text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def fetch():
    page = http(URL).decode("utf-8", "replace")
    start, end = page.find(">GPU instances pricing<"), page.find(">CPU instances pricing<")
    if start < 0 or end < start:
        raise RuntimeError("Crusoe: 'GPU instances pricing' section not found")
    section = page[start:end]
    # Column headings, e.g. ["GPU model", "Current on-demand", "New on-demand"]; the name column
    # is one CMS list, then each price column is its own list of gpu-row cells in the same order.
    heads = [text(h).lower() for h in re.findall(r'class="pricing-heading[^"]*">(.*?)</div>', section, re.S)]
    lists = section.split('class="w-dyn-list"')[1:]
    if len(heads) < 2 or "gpu" not in heads[0] or len(lists) != len(heads):
        raise RuntimeError(f"Crusoe: unexpected table layout, headings {heads}, {len(lists)} lists")
    current = [i for i, h in enumerate(heads[1:]) if "current" in h or h == "on-demand"]
    if len(current) != 1:
        raise RuntimeError(f"Crusoe: can't tell which column is the current on-demand price: {heads}")
    names = lists[0].split("data-sort-name=")[1:]
    cols = [re.findall(r'class="gpu-row w-dyn-item">(.*?)</div>\s*</div>', c, re.S) for c in lists[1:]]
    if not names or any(len(c) != len(names) or raw.count('"gpu-row ') != len(names)
                        for c, raw in zip(cols, lists[1:])):
        raise RuntimeError(f"Crusoe: {len(names)} GPU names vs price cells {[len(c) for c in cols]}; "
                           "columns misaligned")
    # an announced future price list ("New on-demand" + "starting Dec 1, 2026") is kept separately
    future = [i for i, h in enumerate(heads[1:]) if "new" in h]
    when = re.search(r"(?:starting|effective|from)\s+([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", text(section))
    effective = datetime.strptime(" ".join(when.groups()), "%b %d %Y").strftime("%Y-%m-%d") if when else None

    prices, announced = [], []
    for k, (item, row) in enumerate(zip(names, cols[current[0]])):
        name = html.unescape(item.split('"')[1])
        mem = re.search(r'class="pricing-tag is-dark">\s*(\d+)\s*GB', item)
        gpu = canon(name, int(mem.group(1)) if mem else None)
        price = _price(text(row), name)
        if gpu and price:
            prices.append((gpu, NAME, "on_demand", price))
        if gpu and future and effective:
            cell = text(cols[future[0]][k])
            new = None if re.search(r"no change|contact|^[-–—]?$", cell, re.I) else _price(cell, name)
            if new and new != price:
                announced.append((gpu, NAME, "on_demand", new, effective))
    if not prices:
        raise RuntimeError(f"Crusoe: no current on-demand GPU prices parsed from {URL}")
    return prices, [], announced


def _price(cell, name):
    m = re.fullmatch(r"\$\s*([\d.]+)\s*/\s*GPU-hr", cell)
    if not m:
        if "contact" not in cell.lower():
            raise RuntimeError(f"Crusoe: unexpected price cell {cell!r} for {name}")
        return None
    return float(m.group(1)) if 0.05 <= float(m.group(1)) <= 50 else None
