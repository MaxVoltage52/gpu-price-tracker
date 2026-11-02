"""Hyperstack: monthly GPU list prices rebuilt from the Internet Archive (Wayback Machine).

    python collector/backfill/hyperstack_history.py      ->  data/backfill/hyperstack.csv

Page: www.hyperstack.cloud/gpu-pricing (the only pricing URL Hyperstack has used; captures with tracking query
strings such as ?utm_source=... are the same page and fill months without a clean capture). First capture 2023-11.
Layout eras (recognised by markup, not by date):
  2023-11..2024-04  HubSpot "pricing__price-item" rows: GPU | VRAM | Max vCPUs | Max RAM | "$ 2.10 per GPU".
                    In 2023-11 the page also listed vCPU / RAM add-ons ($0.01 per vCPU, $0.005 per GB RAM per
                    GPU-hour); from 2024-02 only free egress/ingress, i.e. the GPU price became all-in.
  2024-07..2025-06  one <table>: GPU Model | VRAM | Max pCPUs | Max RAM | Pricing Per Hour ("$ 3.00 per Hour") |
                    Reservation Pricing ("Starts from ..." -> ignored).
  2025-07..         cards (as the live collector): "On-Demand GPU Pricing" (Pricing Per Hour) and "Spot VM
                    Pricing" (market spot); the Reservation card is ignored.
All prices are per GPU-hour on the page. Names are mapped with canon() after Hyperstack's own spelling is
normalised: plain "H100" / "H100 PCIe NVLink" are 80 GB PCIe cards (canon() would read NVLINK as H100 NVL), and the
combined "RTX A6000/A40" tier is recorded as RTX A6000 (the name the same tier carries from 2025-07), not as A40.

Rule when one month lists a GPU several times (A100 80GB: plain PCIe, PCIe NVLink, SXM; H100 PCIe: plain, NVLink):
keep the plain PCIe row - the one listed in every era, so the series doesn't jump when variants come and go; the
NVLink row, then the SXM row, only when there is no plain one.
One snapshot per month: the first capture of the month that parses (up to 4 tried). Downloads are cached outside
the project (env BACKFILL_CACHE, default <tmp>/gpu-backfill-cache/hyperstack).
"""
import csv
import gzip
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import canon  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "data" / "backfill" / "hyperstack.csv"
SOURCE = "Hyperstack"
PAGE = "hyperstack.cloud/gpu-pricing"
TRIES_PER_MONTH = 4

# ---------------------------------------------------------------- Wayback Machine access (polite, cached)
CACHE = Path(os.environ.get("BACKFILL_CACHE") or Path(tempfile.gettempdir()) / "gpu-backfill-cache") / "hyperstack"
UA = {"User-Agent": "gpu-price-tracker-backfill/1.0 (+https://github.com)"}
_last = [0.0]


def _get(url, tries=6):
    for attempt in range(tries):
        wait = 1.6 - (time.time() - _last[0])  # >= 1.5 s between requests to archive.org
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                return None
            if attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == tries - 1:
                raise
        time.sleep(min(160, 10 * 2 ** attempt))


def captures(page, prefix=False):
    """{YYYYMM: [(timestamp, original_url), ...]} of 200-status captures since 2020, at most one per day and URL
    (cached a day). prefix=True also returns the page with query strings; the bare URL is listed first."""
    CACHE.mkdir(parents=True, exist_ok=True)
    fn = CACHE / ("cdx_" + re.sub(r"\W", "_", page) + ("_prefix" if prefix else "") + ".json")
    if not fn.exists() or time.time() - fn.stat().st_mtime > 86400:
        q = ("https://web.archive.org/cdx/search/cdx?url=%s&output=json&fl=timestamp,original,statuscode"
             "&filter=statuscode:200&collapse=timestamp:8&from=2020%s"
             % (urllib.parse.quote(page, safe=""), "&matchType=prefix" if prefix else ""))
        body = _get(q)
        if body is None:
            return {}
        fn.write_bytes(body)
    rows = json.loads(fn.read_bytes() or b"[]")[1:]
    out = defaultdict(list)
    for ts, orig, _ in sorted(rows, key=lambda r: ("?" in r[1], r[0])):
        out[ts[:6]].append((ts, orig))
    return out


def snapshot(ts, orig):
    """Raw archived HTML (id_ = without the Wayback toolbar), cached on disk."""
    fn = CACHE / ("snap_%s_%s.html" % (ts, re.sub(r"\W", "_", orig)[-60:]))
    if fn.exists():
        body = fn.read_bytes()
    else:
        body = _get("https://web.archive.org/web/%sid_/%s" % (ts, orig))
        if body is None:
            return None
        fn.write_bytes(body)
    if body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return body.decode("utf-8", "replace")


def text(fragment):
    fragment = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    return re.sub(r"[\s​]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def money(s):
    m = re.fullmatch(r"\$\s*(\d+(?:\.\d+)?)(?:\s*(?:per|/)\s*(?:GPU|hour|hr))?", (s or "").strip(), re.I)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------- parsing
def gpu_of(name, mem=None):
    n = name.upper().replace("RTX A6000/A40", "RTX A6000")
    if "/" in n:
        return None  # any other combined tier: don't guess
    if "H100" in n and "SXM" not in n and (mem or 80) < 90:
        n = "H100 PCIE"  # Hyperstack's plain / NVLink H100 = 80 GB PCIe (not the 94 GB H100 NVL)
    return canon(n, mem)


def variant_rank(name):
    n = name.upper()
    return 2 if "SXM" in n else 1 if "NVLINK" in n else 0


def parse_items(page):
    """2023-11..2024-04: <div class="pricing__price-item"> rows with pricing__name / __vram / __price cells."""
    if "pricing__price-item" not in page or not re.search(r"Price per hour", page, re.I):
        return None
    out = []
    for k, item in enumerate(page.split('class="pricing__price-item"')[1:]):
        cell = lambda c: re.search(r'class="pricing__price-item-\w+ pricing__%s">(.*?)</div>' % c, item, re.S)  # noqa
        name, vram, price = cell("name"), cell("vram"), cell("price")
        if not (name and price):
            continue
        name, price = text(name.group(1)), text(price.group(1))
        if not re.search(r"per GPU", price, re.I):
            continue
        mem = int(text(vram.group(1))) if vram and text(vram.group(1)).isdigit() else None
        out.append((name, mem, "on_demand", money(price), k))
    return out


def parse_table(page):
    """2024-07..2025-06: first <table> with 'GPU Model' and 'Pricing Per Hour' headers."""
    for table in re.findall(r"<table\b.*?</table>", page, re.S):
        rows = [[text(c) for c in re.findall(r"<t[hd]\b.*?</t[hd]>", tr, re.S)]
                for tr in re.findall(r"<tr\b.*?</tr>", table, re.S)]
        if not rows or not rows[0] or "gpu model" not in rows[0][0].lower():
            continue
        head = [h.lower() for h in rows[0]]
        price = next((i for i, h in enumerate(head) if h.startswith("pricing per hour")), None)
        vram = next((i for i, h in enumerate(head) if "vram" in h), None)
        if price is None:
            continue
        out = []
        for k, r in enumerate(rows[1:]):
            if len(r) != len(head):
                continue
            mem = int(r[vram]) if vram is not None and r[vram].isdigit() else None
            out.append((r[0], mem, "on_demand", money(r[price]), k))
        return out
    return None


def parse_cards(page):
    """2025-07..: 'On-Demand GPU Pricing' and 'Spot VM Pricing' cards (same as the live collector)."""
    found, vram = [], {}
    for card in page.split('class="page-price_card_top_content"')[1:]:
        title = text(card.split("</h3>")[0].split(">", 1)[-1]).lower()
        market = "spot" if "spot" in title else "on_demand" if "on-demand gpu" in title else None
        cells = lambda chunk, kind: [text(c) for c in re.findall(  # noqa: E731
            r'class="[^"]*\b%s\d+">(.*?)</div>' % kind, chunk, re.S)]
        head = [h.lower() for h in cells(card, "page-price_card_head_item")]
        if not market or not head or "gpu model" not in head[0]:
            continue
        col = next((i for i, h in enumerate(head) if "per hour" in h or h.startswith("spot")), None)
        vcol = next((i for i, h in enumerate(head) if "vram" in h), None)
        if col is None:
            continue
        for k, row in enumerate(card.split('class="page-price_card_row_item df"')[1:]):
            c = cells(row, "page-price_card_row_item_col")
            if len(c) != len(head):
                continue
            if vcol is not None and c[vcol].isdigit():
                vram[c[0]] = int(c[vcol])
            found.append((c[0], market, money(c[col]), k))
    return [(name, vram.get(name), market, price, k) for name, market, price, k in found] or None


def parse(page):
    for parser in (parse_cards, parse_table, parse_items):
        rows = parser(page)
        if rows:
            return rows
    return None


def pick(rows):
    """One price per (gpu, market): plain PCIe variant first, then NVLink, then SXM; first listed among equals."""
    best = {}
    for name, mem, market, price, k in rows:
        gpu = gpu_of(name, mem)
        if not gpu or not price or not 0.05 <= price <= 50:
            continue
        rank = (variant_rank(name), k)
        if (gpu, market) not in best or rank < best[(gpu, market)][1]:
            best[(gpu, market)] = (price, rank, name)
    return best


# ---------------------------------------------------------------- main
def main():
    # the prefix listing adds the ?utm=... captures, but can lag behind the exact-URL index: merge both
    caps = captures(PAGE)
    for month, extra in captures(PAGE, prefix=True).items():
        caps[month] += [c for c in extra if c not in caps[month]]
    out, skipped = [], []
    for month in sorted(caps):
        got = None
        for ts, orig in caps[month][:TRIES_PER_MONTH]:
            body = snapshot(ts, orig)
            best = pick(parse(body) or []) if body else {}
            if any(m == "on_demand" for _, m in best):
                got = (ts, orig, best)
                break
        if not got:
            skipped.append(month)
            continue
        ts, orig, best = got
        for (gpu, market), (price, _, _) in sorted(best.items()):
            out.append({"date": "%s-%s-%s" % (ts[:4], ts[4:6], ts[6:8]), "gpu": gpu, "source": SOURCE,
                        "market": market, "price": price, "url": "https://web.archive.org/web/%s/%s" % (ts, orig)})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "gpu", "source", "market", "price", "url"])
        w.writeheader()
        w.writerows(out)
    print("%s: %d rows, %d months -> %s" % (SOURCE, len(out), len({r["date"][:7] for r in out}), OUT))
    if skipped:
        print("months without a parseable snapshot:", ", ".join(skipped))
    timeline(out)


def timeline(rows):
    """Per-GPU timeline (changes only), flagging month-over-month moves above 25%."""
    series = defaultdict(list)
    for r in rows:
        series[(r["gpu"], r["market"])].append((r["date"], r["price"]))
    for (gpu, market), pts in sorted(series.items()):
        print("\n%s / %s" % (gpu, market))
        prev = None
        for date, p in pts:
            flag = "   <-- %+.0f%%" % (100 * (p / prev - 1)) if prev and abs(p / prev - 1) > 0.25 else ""
            if prev is None or p != prev:
                print("  %s  %8.4f%s" % (date, p, flag))
            prev = p
        if len(pts) > 1 and pts[-1][1] == pts[-2][1]:
            print("  %s  %8.4f  (last)" % pts[-1])


if __name__ == "__main__":
    main()
