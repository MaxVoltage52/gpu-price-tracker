"""Nebius — AI cloud, public price list (nebius.com/prices).

Prices: the GPU instance table embedded as JSON in the Next.js page data, USD per GPU-hour:
On-demand and Preemptible (= spot). On-demand "from $X" is taken as X (cheapest config); spot
"from $X" floors are skipped, only flat preemptible prices count.
"""
import json
import re

from common import canon, http

NAME = "Nebius"
URL = "https://nebius.com/prices"


def _tables(node):
    """Every table.content (list of rows, first row = header) anywhere in the page JSON."""
    if isinstance(node, dict):
        t = node.get("table")
        if isinstance(t, dict) and isinstance(t.get("content"), list) and t["content"]:
            yield t["content"]
        node = list(node.values())
    if isinstance(node, list):
        for x in node:
            yield from _tables(x)


def _price(s):
    m = re.search(r"\$\s*(\d[\d,]*\.?\d*)", str(s))
    return float(m.group(1).replace(",", "")) if m else None


def fetch():
    html = http(URL).decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError(f"Nebius: no __NEXT_DATA__ script on {URL}")
    props = json.loads(m.group(1)).get("props") or {}
    state = (props.get("pageProps") or {}).get("__APOLLO_STATE__") or {}
    pages = []
    for key, val in state.items():
        if key.startswith("pages:") and isinstance(val.get("content"), str):
            try:
                pages.append(json.loads(val["content"]))
            except ValueError:
                pass
    prices, tables = [], 0
    for rows in _tables(pages):
        head = [str(h).lower() for h in rows[0]]
        cols = {market: next((i for i, h in enumerate(head) if key in h), None)
                for market, key in (("spot", "preemptible"), ("on_demand", "on-demand"))}
        if None in cols.values():
            continue
        tables += 1
        for row in rows[1:]:
            name = re.sub(r"\s+with\b.*$", "", str(row[0]), flags=re.I)  # "NVIDIA L40S with Intel CPU"
            gpu = canon(name)
            for market, i in cols.items():
                cell = str(row[i]) if gpu and i < len(row) else ""
                if market == "spot" and re.match(r"\s*from\b", cell, re.I):
                    continue  # "from $0.99" spot floors aren't comparable to real spot prices
                p = _price(cell)
                if p and 0.05 <= p <= 50:
                    prices.append((gpu, NAME, market, p))
    if not prices:
        raise RuntimeError(f"Nebius: {tables} tables with Preemptible/On-demand columns, no prices parsed from {URL}")
    return prices, []
