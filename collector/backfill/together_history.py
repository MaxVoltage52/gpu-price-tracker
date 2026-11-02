"""Together AI: monthly GPU-cluster list prices rebuilt from the Internet Archive (Wayback Machine).

    python collector/backfill/together_history.py      ->  data/backfill/together.csv

Together sells self-serve "Instant" / on-demand GPU clusters (HGX H100 / H200 / B200 / B300) priced per GPU-hour.
Pages and layout eras (recognised by markup, not by date):
  together.ai/pricing               2025-09..   <table> (in the GPU Cloud / GPU Clusters section, not under the
                                    "Dedicated Inference" heading) with a <thead> of Hardware | Hourly | 1 - 6 Days |
                                    1 Week - 3 Months (columns reordered in 2025-10), from 2026-04 a separate
                                    "On-demand" table (Hardware | Hourly), from 2026-07 also Hardware | ON-Demand |
                                    Reserved..., from 2026-10 with a "Preemptible Compute" column (-> market spot).
                                    Only the Hourly / ON-Demand and Preemptible columns are used.
  together.ai/instant-gpu-clusters  2025-07..2025-08  cards "NVIDIA H100 Pricing On-demand hourly: $3.19 GPU/hr"
                                    (2025-09.. same table as the pricing page).
  together.ai/gpu-clusters          2026-03..   cards "Hardware NVIDIA HGX H100 SXM (80GB) On-demand $2.99/hr per
                                    GPU" (fallback when the pricing page has no capture that parses).
Skipped as not on-demand: the "Starting at $x/hr" reserved-cluster prices (pricing page 2024-11..), the
1-6 day / 1 week-3 month / Reserved columns, instant-cluster prices "up to 16: $2.85 GPU/hr" (2025-03..06, sold
by duration in days), dedicated-inference endpoints ("1x H100 80GB $3.36"), promotional rows ("Promo ... valid
until"), "Contact us" / "—" cells. Before 2025-07 the archive shows no hourly on-demand cluster price at all.
Rule when one month lists a GPU several times: the "HGX H100 Inference" SKU (H100 SXM with reduced InfiniBand) is
dropped in favour of the standard "HGX H100 SXM" row; otherwise the first table/row that prices it wins.
One snapshot per month: the first capture that parses, trying the pricing page first, then instant-gpu-clusters,
then gpu-clusters (up to 3 captures per page and month). Downloads are cached outside the project
(env BACKFILL_CACHE, default <tmp>/gpu-backfill-cache/together).
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
OUT = ROOT / "data" / "backfill" / "together.csv"
SOURCE = "Together AI"
# (page, first month searched): earlier captures were checked by hand and carry no hourly on-demand price
PAGES = [("together.ai/pricing", "202506"), ("together.ai/instant-gpu-clusters", "202506"),
         ("together.ai/gpu-clusters", "202601")]
TRIES_PER_MONTH = 3

# ---------------------------------------------------------------- Wayback Machine access (polite, cached)
CACHE = Path(os.environ.get("BACKFILL_CACHE") or Path(tempfile.gettempdir()) / "gpu-backfill-cache") / "together"
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
    fragment = re.sub(r"<(script|style|svg)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    return re.sub(r"[\s​]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def money(s):
    m = re.fullmatch(r"\$\s*(\d+(?:\.\d+)?)", (s or "").strip())
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------- parsing
def gpu_of(name):
    if re.search(r"(?i)inference|promo", name):
        return None  # reduced-network inference SKU / promotional price: not the standard on-demand list price
    return canon(re.sub(r"\(.*?\)", " ", name))


def _cells(fragment, tag):
    """Cell texts of one header/row, repeated per colspan so indexes line up."""
    out = []
    for attrs, body in re.findall(r"<%s\b([^>]*)>(.*?)</%s>" % (tag, tag), fragment, re.S):
        span = re.search(r'colspan="(\d+)"', attrs)
        out += [text(body)] * int(span.group(1) if span else 1)
    return out


def parse_tables(page):
    """together.ai/pricing (2025-09..): GPU tables whose header has an Hourly / ON-Demand / Preemptible column."""
    out = []
    for m in re.finditer(r"<table\b.*?</table>", page, re.S):
        table = m.group(0)
        section = re.findall(r"<h2\b[^>]*>(.*?)</h2>", page[:m.start()], re.S)
        if section and re.search(r"(?i)dedicated|inference|endpoint", text(section[-1])):
            continue  # Dedicated Inference endpoints (managed single-GPU instances), not GPU clusters
        thead = re.search(r"<thead\b.*?</thead>", table, re.S)
        if not thead:
            continue
        first = re.search(r"<tr\b.*?</tr>", thead.group(0), re.S)
        head = [h.lower().strip(" *") for h in _cells((first or thead).group(0), "th")]
        if not head or not head[0].startswith("hardware"):
            continue
        cols = {"on_demand": next((i for i, h in enumerate(head) if h in ("hourly", "on-demand")), None),
                "spot": next((i for i, h in enumerate(head) if "preemptible" in h), None)}
        body = table[thead.end():]
        for tr in re.findall(r"<tr\b.*?</tr>", body, re.S):
            cells = _cells(tr, "td")
            if len(cells) != len(head):
                continue
            gpu = gpu_of(cells[0])
            for market, i in cols.items():
                p = money(cells[i]) if i is not None else None
                if gpu and p:
                    out.append((gpu, market, p, cells[0]))
    return out


def parse_instant_cards(page):
    """instant-gpu-clusters 2025-07..08: 'NVIDIA H100 Pricing On-demand hourly: $3.19 GPU/hr'."""
    t = text(page)
    return [(gpu_of(name), "on_demand", float(p), name)
            for name, p in re.findall(r"(NVIDIA [A-Z0-9]+) Pricing On-demand hourly: \$\s*([\d.]+) GPU/hr", t)
            if gpu_of(name)]


def parse_cluster_cards(page):
    """gpu-clusters 2026-03..: 'Hardware NVIDIA HGX H100 SXM (80GB) On-demand $2.99/hr per GPU'."""
    t = text(page)
    return [(gpu_of(name), "on_demand", float(p), name)
            for name, p in re.findall(r"Hardware (NVIDIA [^$]{2,60}?) On-demand \$\s*([\d.]+)\s*/\s*hr per GPU", t)
            if gpu_of(name)]


def parse(page):
    for parser in (parse_tables, parse_instant_cards, parse_cluster_cards):
        rows = parser(page)
        if rows:
            return rows
    return None


def pick(rows):
    best = {}
    for gpu, market, price, name in rows:
        if 0.05 <= price <= 50:
            best.setdefault((gpu, market), (price, name))
    return best


# ---------------------------------------------------------------- main
def main():
    caps = {page: {m: v for m, v in captures(page).items() if m >= start} for page, start in PAGES}
    months = sorted(set().union(*caps.values()))
    out, skipped = [], []
    for month in months:
        got = None
        for page, _ in PAGES:
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
        for (gpu, market), (price, _) in sorted(best.items()):
            out.append({"date": "%s-%s-%s" % (ts[:4], ts[4:6], ts[6:8]), "gpu": gpu, "source": SOURCE,
                        "market": market, "price": price, "url": "https://web.archive.org/web/%s/%s" % (ts, orig)})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "gpu", "source", "market", "price", "url"])
        w.writeheader()
        w.writerows(out)
    print("%s: %d rows, %d months -> %s" % (SOURCE, len(out), len({r["date"][:7] for r in out}), OUT))
    if skipped:
        print("months without an hourly on-demand price:", ", ".join(skipped))
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
