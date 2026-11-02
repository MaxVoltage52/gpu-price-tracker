"""Crusoe: monthly GPU list prices rebuilt from the Internet Archive (Wayback Machine).

    python collector/backfill/crusoe_history.py      ->  data/backfill/crusoe.csv

Pages and layout eras (recognised by markup, not by date):
  crusoecloud.com/pricing   2022-05..2023-10  "price-card" cards: name (h5, e.g. "A100 PCIe - 80GB"), price (h4,
                            "$1.65 /hr") for the GPU count shown ("GPUs 1"; the A100 SXM card is an 8-GPU instance,
                            "$15.60 /hr" -> divided by 8). "AS LOW AS $x**" (36-month commitment) is ignored, and
                            cards marked "COMING SOON!" are skipped (not orderable yet).
  crusoe.ai/cloud/pricing   2024-02..2024-10  Astro rows "NVIDIA A100 Tensor Core GPU 80GB SXM $ 1.95 $ 1.56 ..."
                            with labelled cells "On-Demand $ 1.95 / 6-month reserved ..."; only On-Demand is used.
                            2025-04..2025-10  <table> GPU Pricing | On-Demand | (Current) Spot | 6-month / 1-year /
                            3-year reserved, cells "$ 4.29 /hr" or "Contact Sales"; spot -> market "spot".
                            2025-12..         Webflow CMS rows (h4 name + "141GB" / "HGX" tags) under the headings
                            GPU model | On-demand | Current spot, cells "$4.29/GPU-hr" or "Contact sales".
All prices on the page are per GPU-hour except the 2022-23 8-GPU card. Captures with query strings / index.html of
the same page fill months without a clean capture. No captures exist for 2023-11..2024-01 and 2024-11..2025-03.
Names are mapped with canon() after dropping memory sizes and words like "Tensor Core GPU"; form factor (PCIe /
SXM / HGX) is kept only for H100, where it matters to canon().

Rule when one month lists a GPU several times (A100 80GB as PCIe and as SXM): keep the PCIe row - the variant listed
in every era, so the series doesn't jump when the SXM row appears (2023-10); the SXM row only when there is no PCIe
one. Downloads are cached outside the project (env BACKFILL_CACHE, default <tmp>/gpu-backfill-cache/crusoe).
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
OUT = ROOT / "data" / "backfill" / "crusoe.csv"
SOURCE = "Crusoe"
PAGES = ["crusoe.ai/cloud/pricing", "crusoecloud.com/pricing"]
TRIES_PER_MONTH = 3

# ---------------------------------------------------------------- Wayback Machine access (polite, cached)
CACHE = Path(os.environ.get("BACKFILL_CACHE") or Path(tempfile.gettempdir()) / "gpu-backfill-cache") / "crusoe"
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


def _cdx(page, prefix):
    CACHE.mkdir(parents=True, exist_ok=True)
    fn = CACHE / ("cdx_" + re.sub(r"\W", "_", page) + ("_prefix" if prefix else "") + ".json")
    if not fn.exists() or time.time() - fn.stat().st_mtime > 86400:
        q = ("https://web.archive.org/cdx/search/cdx?url=%s&output=json&fl=timestamp,original,statuscode"
             "&filter=statuscode:200&collapse=timestamp:8&from=2020%s"
             % (urllib.parse.quote(page, safe=""), "&matchType=prefix" if prefix else ""))
        body = _get(q)
        if body is None:
            return []
        fn.write_bytes(body)
    return json.loads(fn.read_bytes() or b"[]")[1:]


def captures(page):
    """{YYYYMM: [(timestamp, original_url), ...]}: the page itself first, then its variants (trailing slash,
    index.html, ?utm=...). Both CDX listings are merged because the prefix index can lag behind."""
    rows = {(ts, orig) for ts, orig, _ in _cdx(page, False) + _cdx(page, True)}
    bare = lambda o: re.sub(r"^https?://(www\.)?", "", o).rstrip("/") == page  # noqa: E731
    out = defaultdict(list)
    for ts, orig in sorted(rows, key=lambda r: (not bare(r[1]), r[0])):
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
    fragment = re.sub(r"<(script|style|svg)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    return re.sub(r"[\s​]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def money(s):
    m = re.fullmatch(r"\$\s*(\d+(?:\.\d+)?)\s*(?:/\s*(?:GPU-)?hr)?", (s or "").strip(), re.I)
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------- parsing
def gpu_of(name):
    """canon() of a Crusoe name such as 'NVIDIA A100 Tensor Core GPU 80GB SXM' or 'RTX A6000 - 48GB'."""
    mem = re.search(r"(\d+)\s*GB", name)
    n = re.sub(r"(?i)\b(tensor core|gpu|oam|nvl72)\b|\d+\s*GB|\s-\s", " ", name)
    if "H100" not in n.upper():
        n = re.sub(r"(?i)\b(pcie|sxm|hgx)\b", " ", n)
    return canon(n, int(mem.group(1)) if mem else None)


def variant_rank(name):
    return 1 if re.search(r"(?i)\bSXM\b", name) else 0


def parse_cards(page):
    """crusoecloud.com 2022-05..2023-10: price cards with a per-instance price and a 'GPUs n' line."""
    out = []
    for k, card in enumerate(page.split('class="price-card')[1:]):
        name = re.search(r"<h5\b[^>]*>(.*?)</h5>", card, re.S)
        price = re.search(r"<h4\b[^>]*>(.*?)</h4>", card, re.S)
        count = re.search(r"\bGPUs (\d+)\b", text(card))
        if not (name and price and count) or "COMING SOON" in card.upper():  # the badge is SVG <text>: raw HTML
            continue
        p = re.fullmatch(r"\$\s*([\d.]+)\s*/hr", text(price.group(1)))
        if p:
            out.append((text(name.group(1)), "on_demand", float(p.group(1)) / int(count.group(1)), k))
    return out


def parse_astro_rows(page):
    """crusoe.ai 2024: one block per GPU with labelled 'On-Demand $ 1.95' cells."""
    i = page.find('id="gpu"')
    if i < 0:
        return None
    end = page.find('id="cpu"', i)
    out = []
    for k, chunk in enumerate(re.split(r'<p class="min-w-\[280px\][^"]*">', page[i:end if end > 0 else None])[1:]):
        t = text("<p>" + chunk)
        name = re.match(r"(.+?)(?= \$| On-Demand| Contact)", t)
        od = re.search(r"On-Demand \$ ([\d.]+)", t)
        if name and od:
            out.append((name.group(1), "on_demand", float(od.group(1)), k))
    return out


def parse_table(page):
    """crusoe.ai 2025-04..10: <table> GPU Pricing | On-Demand | (Current) Spot | reserved columns."""
    for table in re.findall(r"<table\b.*?</table>", page, re.S):
        head = [text(h).lower() for h in re.findall(r"<th\b.*?</th>", table, re.S)]
        if not head or "gpu" not in head[0]:
            continue
        cols = {"on_demand": next((i for i, h in enumerate(head) if h == "on-demand"), None),
                "spot": next((i for i, h in enumerate(head) if h in ("spot", "current spot")), None)}
        if cols["on_demand"] is None:
            continue
        out = []
        for k, tr in enumerate(re.findall(r"<tr\b.*?</tr>", table, re.S)):
            cells = [text(c) for c in re.findall(r"<td\b.*?</td>", tr, re.S)]
            if len(cells) != len(head):
                continue
            for market, c in cols.items():
                p = money(cells[c]) if c is not None else None
                if p:
                    out.append((cells[0], market, p, k))
        return out
    return None


def parse_cms(page):
    """crusoe.ai 2025-12..: Webflow list items (h4 name + tags) under headings GPU model | On-demand | Current spot."""
    start, end = page.find(">GPU instances pricing<"), page.find(">CPU instances pricing<")
    if start < 0 or end < start:
        return None
    sec = page[start:end]
    heads = [text(h).lower() for h in re.findall(r'class="pricing-heading[^"]*">(.*?)</div>', sec, re.S)]
    if len(heads) < 2 or "gpu" not in heads[0] or 'role="listitem"' not in sec:
        return None
    cols = {"on_demand": next((i for i, h in enumerate(heads[1:]) if h in ("on-demand", "current on-demand")), None),
            "spot": next((i for i, h in enumerate(heads[1:]) if h in ("spot", "current spot")), None)}
    out = []
    for k, item in enumerate(sec.split('role="listitem"')[1:]):
        name = re.search(r"<h4\b[^>]*>(.*?)</h4>", item, re.S)
        tags = [text(t) for t in re.findall(r'class="pricing-tag[^"]*">(.*?)</div>', item, re.S)]
        cells = [text(c) for c in re.findall(r'class="pricing-rich[^"]*">(.*?)</div>', item, re.S)]
        if not name or len(cells) != len(heads) - 1:
            continue
        full = " ".join([text(name.group(1))] + tags)
        for market, c in cols.items():
            p = money(cells[c]) if c is not None else None
            if p:
                out.append((full, market, p, k))
    return out


def parse(page):
    for parser in (parse_cms, parse_table, parse_astro_rows, parse_cards):
        rows = parser(page)
        if rows:
            return rows
    return None


def pick(rows):
    """One price per (gpu, market): PCIe / unspecified A100 before SXM, then first listed."""
    best = {}
    for name, market, price, k in rows:
        gpu = gpu_of(name)
        if not gpu or not 0.05 <= price <= 50:
            continue
        rank = (variant_rank(name), k)
        if (gpu, market) not in best or rank < best[(gpu, market)][1]:
            best[(gpu, market)] = (round(price, 4), rank, name)
    return best


# ---------------------------------------------------------------- main
def main():
    caps = {page: captures(page) for page in PAGES}
    months = sorted(set().union(*caps.values()))
    out, skipped = [], []
    for month in months:
        got = None
        for page in PAGES:
            for ts, orig in caps[page].get(month, [])[:TRIES_PER_MONTH]:
                body = snapshot(ts, orig)
                best = pick(parse(body) or []) if body else {}
                if any(m == "on_demand" for _, m in best):
                    got = (ts, orig, best)
                    break
            if got:
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
