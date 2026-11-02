"""CoreWeave: monthly GPU list prices rebuilt from the Internet Archive (Wayback Machine).

    python collector/backfill/coreweave_history.py      ->  data/backfill/coreweave.csv

Pages and layout eras (recognised by markup, not by date):
  docs.coreweave.com/resources/resource-based-pricing   2021-03..2023-02
      GitBook <table> "GPU Instance Resource Pricing": Type | Description | (Resource) Label | Cost per Hour | VRAM.
      "Cost per Hour" of a GPU row is the a-la-carte GPU *component* price (vCPU and RAM billed on top).
  coreweave.com/gpu-cloud-pricing                       2022-08..2024-12
      Webflow table with header "GPU Component Cost Per Hour" (desktop table-body-row rows; the duplicated
      mobile cards, which sometimes disagree, are ignored). Same a-la-carte GPU component price.
  coreweave.com/pricing                                 2025-01..2026-02
      "On-demand GPU instances" table: GPU Model | GPU Count | VRAM | ... | Instance Price (Per Hour).
      Price is per INSTANCE (incl. CPU/RAM/storage) and divided by GPU Count.
  coreweave.com/pricing                                 2026-03..
      same table with columns On-Demand Price | Spot Price | Inference Single GPU Price, one table per region
      (North America, Europe). Per-instance prices divided by GPU Count; spot recorded as market "spot".
Not used: coreweave.com/pricing 2020-12 (one "$/GPU/Hour" all-in table with only V100/Quadro cards; a different
convention from the component prices that follow) and 2021-01..2022-07 (table filled client-side from Contentful,
the archived HTML has no prices). Reserved / "Contact sales" / "Reserve Now" entries carry no price and are skipped.

NOTE the convention break at 2025-01: up to 2024-12 CoreWeave published the GPU component price only (e.g. HGX H100
$4.76); from 2025-01 the page lists all-in 8-GPU instances (HGX H100 $49.24 / 8 = $6.155). The step is a change of
what is listed, not a price rise.

Rule when one month lists a GPU several times: prefer the 8-GPU instance (else 1-GPU), and for the component-price
eras the HGX/NVLINK (SXM) part over the PCIe part when both map to the same canonical GPU (A100 40GB / 80GB, V100);
among equals the first listed row wins (North America is listed before Europe).
One snapshot per month: the first capture of the month that parses, trying coreweave.com/pricing first, then
gpu-cloud-pricing, then the docs page (up to 3 captures per page and month). A GPU missing from that page is
taken from a lower-priority page of the same convention in the same month (only the docs page's HGX H100 price in
2023-01/02, while the pricing page still showed "Reserve Now"); the url column names the snapshot of each row.
Downloads are cached outside the project (env BACKFILL_CACHE, default <tmp>/gpu-backfill-cache/coreweave).
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
OUT = ROOT / "data" / "backfill" / "coreweave.csv"
SOURCE = "CoreWeave"
# (page, first month, last month) in priority order: in a month with several pages the first that parses wins
PAGES = [("coreweave.com/pricing", "202412", "999999"),
         ("coreweave.com/gpu-cloud-pricing", "202201", "202512"),
         ("docs.coreweave.com/resources/resource-based-pricing", "202101", "202312")]
TRIES_PER_MONTH = 3

# ---------------------------------------------------------------- Wayback Machine access (polite, cached)
CACHE = Path(os.environ.get("BACKFILL_CACHE") or Path(tempfile.gettempdir()) / "gpu-backfill-cache") / "coreweave"
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


def captures(page):
    """{YYYYMM: [(timestamp, original_url), ...]} of 200-status captures since 2020, one per day (cached a day)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    fn = CACHE / ("cdx_" + re.sub(r"\W", "_", page) + ".json")
    if not fn.exists() or time.time() - fn.stat().st_mtime > 86400:
        q = ("https://web.archive.org/cdx/search/cdx?url=%s&output=json&fl=timestamp,original,statuscode"
             "&filter=statuscode:200&collapse=timestamp:8&from=2020") % urllib.parse.quote(page, safe="")
        body = _get(q)
        if body is None:
            return {}
        fn.write_bytes(body)
    rows = json.loads(fn.read_bytes() or b"[]")[1:]
    out = defaultdict(list)
    for ts, orig, _ in rows:
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
    m = re.fullmatch(r"\$\s*(\d[\d,]*(?:\.\d+)?)", (s or "").strip())
    return float(m.group(1).replace(",", "")) if m else None


# ---------------------------------------------------------------- parsing
def gpu_of(name, vram=None):
    """canon() after normalising CoreWeave spellings: HGX / NVLINK = the SXM part (canon would read NVLINK as NVL)."""
    n = re.sub(r"(?i)\b(HGX|NVLINK)\b", " SXM ", name.replace("_", " "))
    return canon(n, vram)


def variant_rank(name):
    return 0 if re.search(r"(?i)HGX|NVLINK|SXM", name) else 1


def docs_rows(page):
    """Cell rows of the docs 'GPU Instance Resource Pricing' table: an HTML <table> in most GitBook versions,
    rows of <div tabindex="0"> cells (5 per row) in the 2021-10..2022-10 versions."""
    i = page.find("GPU Instance Resource Pricing")
    if i < 0:
        return None
    end = page.find("An example", i)
    sec = page[i:end if end > 0 else len(page)]
    t = sec.find("<table")
    if t >= 0:
        sec = sec[t:sec.find("</table>", t)]
        rows = [[text(c) for c in re.findall(r"<t[dh]\b.*?</t[dh]>", tr, re.S)]
                for tr in re.findall(r"<tr\b.*?</tr>", sec, re.S)]
    else:
        cells = [text(c) for c in re.split(r'<div tabindex="0"[^>]*>', sec)[1:]]
        rows = [cells[j:j + 5] for j in range(0, len(cells) - len(cells) % 5, 5)]
    if not any(len(r) == 5 and r[0] == "Type" and "Cost per Hour" in r[3] for r in rows):
        return None  # header Type | Description | (Resource) Label | Cost per Hour | VRAM not found
    return rows


def parse_docs(page):
    """docs.coreweave.com GitBook table: GPU | Description | Label | Cost per Hour | VRAM."""
    rows = docs_rows(page)
    if not rows:
        return None
    out = []
    for k, cells in enumerate(rows):
        if len(cells) != 5 or cells[0] != "GPU":
            continue
        desc, label, cost = cells[1], re.sub(r"\s+", "", cells[2]), money(cells[3])
        # memory from the VRAM cell, else "(80GB)" in the description, else the label (A100_PCIE_80GB)
        mem = re.search(r"(\d+)\s*GB", cells[4]) or re.search(r"(\d+)GB", desc) or re.search(r"_(\d+)GB", label)
        gpu = gpu_of(desc, int(mem.group(1)) if mem else None)
        if gpu and cost:
            out.append((gpu, "on_demand", cost, (variant_rank(desc + " " + label), k), desc))
    return out


def parse_component_table(page):
    """coreweave.com/gpu-cloud-pricing: desktop rows 'name | VRAM | vCPUs | RAM | $component' (2022-08..2024-12)."""
    i = page.find("GPU Component")
    if i < 0:
        return None
    end = page.find('class="mobile', i)
    sec = page[i:end if end > 0 else page.find("CPU Cloud Pricing", i)]
    out = []
    for k, row in enumerate(sec.split('class="table-body-row')[1:]):
        m = re.match(r"(.+?) (\d+) (\d+) (\d+) (\$\s*[\d.,]+)(?: |$)", text('<x class="' + row))
        if not m:
            continue
        name, vram, cost = m.group(1), int(m.group(2)), money(m.group(5))
        gpu = gpu_of(name, vram)
        if gpu and cost:
            out.append((gpu, "on_demand", cost, (variant_rank(name), k), name))
    return out


def parse_instances(page):
    """coreweave.com/pricing (2025-01..): 'On-demand GPU instances' tables of 8-GPU (or 1-GPU) instances."""
    m = re.search(r"<(h[1-6])\b[^>]*>\s*On-demand GPU instances\s*</h[1-6]>", page, re.I)
    if not m:
        return None
    end = re.compile(r"<%s[\s>]" % m.group(1)).search(page, m.end())
    sec = page[m.end():end.start() if end else len(page)]
    out, k = [], 0
    for table in sec.split('class="table-v2-header"')[1:]:
        body = table.find('role="listitem"')
        heads = [text(h).lower() for h in re.findall(r'class="table-header-text">(.*?)</div>', table[:body], re.S)]
        col = lambda key: next((j for j, h in enumerate(heads) if h.startswith(key)), None)  # noqa: E731
        c_count, c_vram = col("gpu count"), col("vram")
        prices = {"on_demand": col("instance price") if col("instance price") is not None else col("on-demand"),
                  "spot": col("spot")}
        if c_count is None or prices["on_demand"] is None:
            raise ValueError("unexpected CoreWeave instance table header %r" % heads)
        for row in table[body:].split('role="listitem"')[1:]:
            name = re.search(r'<h3[^>]*class="table-model-name[^"]*"[^>]*>(.*?)</h3>', row, re.S)
            cells = [text(re.split(r'<div[^>]*class="table-cell-column', c)[0])
                     for c in re.split(r'<div[^>]*class="table-v2-cell[^"]*"[^>]*>', row)[1:]]
            if not name or len(cells) < len(heads):
                continue
            name = text(name.group(1))
            count = cells[c_count]
            if not re.fullmatch(r"\d+", count):
                continue
            vram = int(cells[c_vram]) if c_vram is not None and cells[c_vram].isdigit() else None
            gpu = gpu_of(name, vram)
            k += 1
            for market, c in prices.items():
                p = money(cells[c]) if c is not None else None
                if gpu and p:
                    rank = {8: 0, 1: 1}.get(int(count), 2)
                    out.append((gpu, market, p / int(count), (rank, k), "%s x%s" % (name, count)))
    return out


def parse(page):
    """(convention, rows): 'instance' = all-in instance price / GPU count, 'component' = a-la-carte GPU part."""
    for convention, parser in (("instance", parse_instances), ("component", parse_component_table),
                               ("component", parse_docs)):
        rows = parser(page)
        if rows:
            return convention, rows
    return None, None


def pick(rows):
    """One price per (gpu, market): the best-ranked row (standard config / SXM part / first listed)."""
    best = {}
    for gpu, market, price, rank, label in rows:
        if not 0.05 <= price <= 50:
            continue
        if (gpu, market) not in best or rank < best[(gpu, market)][1]:
            best[(gpu, market)] = (round(price, 4), rank, label)
    return best


def month_prices(caps, month):
    """{(gpu, market): (price, ts, orig)} for one month: the first page (in PAGES order) that parses, plus GPUs
    missing from it that a lower-priority page of the SAME convention lists that month (e.g. the docs page
    priced HGX H100 in 2023-01 while the pricing page still said 'Reserve Now'). None if nothing parses."""
    result, convention = {}, None
    for page, _, _ in PAGES:
        for ts, orig in caps[page].get(month, [])[:TRIES_PER_MONTH]:
            body = snapshot(ts, orig)
            try:
                conv, rows = parse(body) if body else (None, None)
            except ValueError as e:
                print("  %s %s: %s" % (ts, orig, e))
                conv, rows = None, None
            best = pick(rows or [])
            if not best or (convention and conv != convention):
                continue
            convention = conv
            for key, (price, _, _) in best.items():
                result.setdefault(key, (price, ts, orig))
            break
    return result or None


# ---------------------------------------------------------------- main
def main():
    caps = {page: {m: v for m, v in captures(page).items() if lo <= m <= hi} for page, lo, hi in PAGES}
    months = sorted(set().union(*caps.values()))
    out, skipped = [], []
    for month in months:
        got = month_prices(caps, month)
        if not got:
            skipped.append(month)
            continue
        for (gpu, market), (price, ts, orig) in sorted(got.items()):
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
    """Per-GPU timeline, flagging month-over-month moves above 25%."""
    series = defaultdict(list)
    for r in rows:
        series[(r["gpu"], r["market"])].append((r["date"], r["price"]))
    for (gpu, market), pts in sorted(series.items()):
        print("\n%s / %s" % (gpu, market))
        prev = None
        for date, p in pts:
            flag = ""
            if prev and abs(p / prev - 1) > 0.25:
                flag = "   <-- %+.0f%%" % (100 * (p / prev - 1))
            if prev is None or p != prev or flag:
                print("  %s  %8.4f%s" % (date, p, flag))
            prev = p
        if len(pts) > 1 and pts[-1][1] == pts[-2][1]:
            print("  %s  %8.4f  (last)" % pts[-1])


if __name__ == "__main__":
    main()
