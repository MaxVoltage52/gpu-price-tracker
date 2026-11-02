"""Jarvislabs — GPU cloud, public pricing page (server-rendered, USD rates).

Prices: the GPU table at its default 1-GPU setting, on-demand and spot, per GPU-hour.
Parsed from the labelled per-GPU cards ("80 GB VRAM · … On-demand / hr $3.49 Spot / hr $1.54").
"""
import html
import re

from common import canon, http

NAME = "Jarvislabs"
URL = "https://jarvislabs.ai/pricing"
CARD = re.compile(r"((?:NVIDIA|AMD) [^$·]+?)\s+(\d+)\s*GB VRAM\s*·[^$]*?"
                  r"On-demand / hr\s*(\$\s*[\d.]+|\S+)\s*Spot / hr\s*(\$\s*[\d.]+)?")


def fetch():
    page = http(URL).decode("utf-8", "replace")
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))
    if "GPUs per instance" not in text:
        raise RuntimeError(f"Jarvislabs: GPU pricing table not found on {URL}")
    cards = CARD.findall(text)
    if not cards:
        raise RuntimeError(f"Jarvislabs: no 'GB VRAM … On-demand / hr' GPU cards found on {URL}")
    prices = []
    for name, vram, on_demand, spot in cards:
        gpu = canon(name, int(vram))
        for market, cell in (("on_demand", on_demand), ("spot", spot)):
            m = re.fullmatch(r"\$\s*([\d.]+)", cell or "")
            if gpu and m and 0.05 <= float(m.group(1)) <= 50:
                prices.append((gpu, NAME, market, float(m.group(1))))
    if not any(p[2] == "on_demand" for p in prices):
        raise RuntimeError(f"Jarvislabs: none of the GPU cards {[c[0] for c in cards]} gave an on-demand price")
    return prices, []
