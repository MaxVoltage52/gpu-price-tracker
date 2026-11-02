"""Hyperstack — GPU cloud, public pricing page (server-rendered HubSpot cards).

Prices: the "On-Demand GPU" and "Spot VM" cards, already per GPU-hour, one row per GPU model.
The reservation card (and the CPU card) is ignored.
"""
import html
import re

from common import canon, http

NAME = "Hyperstack"
URL = "https://www.hyperstack.cloud/gpu-pricing"


def text(fragment):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def cells(chunk, kind):
    return [text(c) for c in re.findall(rf'class="[^"]*\b{kind}\d+">(.*?)</div>', chunk, re.S)]


def gpu_name(name, mem):
    n = name.upper()
    # Hyperstack's plain "H100" and "H100 NVLink" are the 80 GB PCIe cards (NVLink = bridged PCIe),
    # not the 94 GB "H100 NVL" that canon() would map "NVLINK" to.
    if "H100" in n and "SXM" not in n and (mem or 80) < 90:
        n = "H100 PCIE"
    return canon(n, mem)


def fetch():
    page = http(URL).decode("utf-8", "replace")
    tables, vram = [], {}
    for card in page.split('class="page-price_card_top_content"')[1:]:
        title = text(card.split("</h3>")[0].split(">", 1)[-1]).lower()
        market = "spot" if "spot" in title else "on_demand" if "on-demand gpu" in title else None
        head = [h.lower() for h in cells(card, "page-price_card_head_item")]
        if not market or not head or "gpu model" not in head[0]:
            continue  # reservation / CPU / anything we don't understand
        price_col = next((i for i, h in enumerate(head) if "per hour" in h or h.startswith("spot")), None)
        vram_col = next((i for i, h in enumerate(head) if "vram" in h), None)
        if price_col is None:
            raise RuntimeError(f"Hyperstack: no price column in {title!r} card, headers {head}")
        for row in card.split('class="page-price_card_row_item df"')[1:]:
            cols = cells(row, "page-price_card_row_item_col")
            if len(cols) != len(head):
                raise RuntimeError(f"Hyperstack: {title!r} row {cols} doesn't match headers {head}")
            if vram_col is not None and cols[vram_col].isdigit():
                vram[cols[0]] = int(cols[vram_col])
            tables.append((market, cols[0], cols[price_col]))
    prices = []
    for market, name, cell in tables:
        m = re.fullmatch(r"\$\s*([\d.]+)", cell)
        gpu = gpu_name(name, vram.get(name))
        if m and gpu and 0.05 <= float(m.group(1)) <= 50:
            prices.append((gpu, NAME, market, float(m.group(1))))
    if not any(p[2] == "on_demand" for p in prices):
        raise RuntimeError(f"Hyperstack: no on-demand GPU prices parsed from {URL} (layout changed?)")
    return prices, []
