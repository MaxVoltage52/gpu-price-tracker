"""Together AI — AI cloud, public pricing page (together.ai/pricing).

Prices: the GPU table with Preemptible (= spot) and On-demand columns, already USD per GPU-hour.
Its reserved columns, and the Dedicated Inference / Post-Training tables, are ignored.
"""
import re

from common import canon, http

NAME = "Together AI"
URL = "https://www.together.ai/pricing"


def _cells(tr, tag):
    """Cell texts of one row, repeated per colspan so indexes line up with the header."""
    out = []
    for attrs, body in re.findall(r"<%s\b([^>]*)>(.*?)</%s>" % (tag, tag), tr, re.S):
        span = re.search(r'colspan="(\d+)"', attrs)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", body)).strip()
        out += [text] * int(span.group(1) if span else 1)
    return out


def fetch():
    html = http(URL).decode("utf-8", "replace")
    prices, tables = [], 0
    for table in re.findall(r"<table\b.*?</table>", html, re.S):
        head = re.search(r"<tr\b[^>]*>(.*?)</tr>", table, re.S)
        cols = [c.lower() for c in _cells(head.group(1), "th")] if head else []
        idx = {market: next((i for i, c in enumerate(cols) if key in c), None)
               for market, key in (("spot", "preemptible"), ("on_demand", "on-demand"))}
        if None in idx.values():
            continue
        tables += 1
        for row in re.findall(r'<tr\b[^>]*fs-list-element="item"[^>]*>(.*?)</tr>', table, re.S):
            cells = _cells(row, "td")
            gpu = canon(cells[0]) if cells else None
            for market, i in idx.items():
                m = re.search(r"\$\s*(\d[\d,]*\.?\d*)", cells[i]) if gpu and i < len(cells) else None
                p = float(m.group(1).replace(",", "")) if m else None
                if p and 0.05 <= p <= 50:
                    prices.append((gpu, NAME, market, p))
    if not prices:
        raise RuntimeError(f"Together AI: {tables} tables with Preemptible/On-demand headers, "
                           f"no prices parsed from {URL}")
    return prices, []
