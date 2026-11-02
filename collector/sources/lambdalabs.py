"""Lambda — GPU cloud, public pricing page (lambda.ai/pricing).

Prices: on-demand USD per GPU-hour of every self-serve instance row (8x / 4x / 2x / 1x tables).
The 1-Click Cluster tables (contract durations, recognised by their DURATION column) are skipped.
"""
import re

from common import canon, http

NAME = "Lambda"
URL = "https://lambda.ai/pricing"


def _cell(row, label):
    m = re.search(r'data-label="%s[^"]*"[^>]*>(.*?)</t[dh]>' % re.escape(label), row, re.S | re.I)
    return re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""


def _num(s, pattern):
    m = re.search(pattern, s)
    return float(m.group(1).replace(",", "")) if m else None


def fetch():
    html = http(URL).decode("utf-8", "replace")
    prices, tables = [], 0
    for table in re.findall(r"<table\b.*?</table>", html, re.S):
        labels = {label.upper() for label in re.findall(r'data-label="([^"]*)"', table)}
        if not any(label.startswith("PRICE/GPU/HR") for label in labels):
            continue
        if "DURATION" in labels or "GPU COUNT" in labels:
            continue  # 1-Click Clusters: contract pricing
        tables += 1
        for plan, row in re.findall(r'<tr\b[^>]*\bdata-plan="([^"]+)"[^>]*>(.*?)</tr>', table, re.S):
            gpu = canon(plan, _num(_cell(row, "VRAM/GPU"), r"([\d.]+)\s*GB"))
            price = _num(_cell(row, "PRICE/GPU/HR"), r"\$\s*(\d[\d,]*\.?\d*)")
            if gpu and price and 0.05 <= price <= 50:
                prices.append((gpu, NAME, "on_demand", price))
    if not tables or not prices:
        raise RuntimeError(f"Lambda: {tables} instance price tables, {len(prices)} prices parsed from {URL} "
                           "(expected <table> rows with data-plan and data-label='PRICE/GPU/HR*')")
    return prices, []
