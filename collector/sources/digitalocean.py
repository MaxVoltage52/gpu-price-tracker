"""DigitalOcean — GPU Droplets, public pricing page (Next.js; plan data embedded in the RSC payload).

Prices: on-demand and spot Droplet sizes (1 or 8 GPUs), divided by the GPU count. Only sizes whose
card is actually shown in the page's On-Demand / Spot section are kept; 12-month commitments are ignored.
"""
import html
import json
import re

from common import canon, http

NAME = "DigitalOcean"
URL = "https://www.digitalocean.com/pricing/gpu-droplets"


def section(page, heading):
    """Rendered HTML of one pricing section, from its <h2> to the next one."""
    m = re.search(rf">\s*{heading}\s*</h2>(.*?)(?:<h2|$)", page, re.S | re.I)
    if not m:
        raise RuntimeError(f"DigitalOcean: '{heading}' section not found on {URL}")
    return html.unescape(m.group(1))


def fetch():
    page = http(URL).decode("utf-8", "replace")
    flight = "".join(json.loads(s) for s in
                     re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', page))
    start = flight.find('{"data":{"plans":')
    if start < 0:
        raise RuntimeError(f"DigitalOcean: plan data not found in the page payload of {URL}")
    data = json.JSONDecoder().raw_decode(flight, start)[0]["data"]
    shown = {"on_demand": section(page, "On-Demand Plans"), "spot": section(page, "Spot Plans")}
    prices = []
    for market, plans in (("on_demand", data["plans"]), ("spot", data.get("spot") or [])):
        for p in plans:
            title = re.sub(r"\s*[×x]\s*\d+$", "", p.get("description") or p.get("title") or "").strip()
            slug_n = re.search(r"x(\d+)-\d+gb", p.get("slug") or "")
            n = int((p.get("gpu") or {}).get("variant") or 1)
            if slug_n and int(slug_n.group(1)) != n:
                raise RuntimeError(f"DigitalOcean: GPU count mismatch in {p.get('slug')} ({n})")
            gpu = canon(title)
            hourly = float((p.get("price") or {}).get("hourly") or 0) / n
            if not gpu or f">{title}<" not in shown[market]:
                continue  # untracked model, or a plan the page doesn't offer in this section
            if f"${hourly:.2f}" not in shown[market]:  # the card shows the per-GPU price
                raise RuntimeError(f"DigitalOcean: {p.get('slug')} works out to ${hourly:.2f}/GPU-hr "
                                   f"but the {market} section doesn't show that price")
            if 0.05 <= hourly <= 50:
                prices.append((gpu, NAME, market, round(hourly, 4)))
    if not any(p[2] == "on_demand" for p in prices):
        raise RuntimeError(f"DigitalOcean: no on-demand GPU prices parsed from {URL}")
    return prices, []
