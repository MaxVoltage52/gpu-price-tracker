"""Jarvislabs: monthly GPU list prices rebuilt from the Internet Archive (Wayback Machine).

    python collector/backfill/jarvislabs_history.py      ->  data/backfill/jarvislabs.csv

Page: jarvislabs.ai/pricing (captures with a trailing slash or a query string are the same page; Next.js "?_rsc="
payload captures don't parse and are skipped). Layout eras (recognised by markup, not by date):
  2021-08..2022-01  cards "NVIDIA RTX A100 40 GB of GPU Memory $ 2.39 per hour" (on-demand, per GPU).
  2022-05..2023-07  Docusaurus <table>s: GPU | vCPUS | Ram | Reserved | Spot | Weekly | Monthly (column order
                    varies). Jarvislabs called its normal non-preemptible hourly instance "Reserved" (the section is
                    titled "On Demand instances", and the 2022-05 "Reserved" price equals the 2021 on-demand price),
                    so the "$x/hr" Reserved column is recorded as on_demand and Spot as spot; the Weekly / Monthly
                    commitments and the bare-metal 30..365-day table are ignored.
  2025-12..2026-05  <table>s GPU Type | (Number of GPUs) | Generation | VRAM | RAM | vCPUs | $/hour (on-demand,
                    "billed to the minute, no commitments"); 8-GPU VM rows are divided by the GPU count.
  2026-06..         per-GPU rows "H100 SXM Hopper · ... 80 GB 200 GB 16 1 × 2 × 4 × 8 × $2.69 On-demand · /hr"
                    (the default On-Demand tab, USD; the Spot / term tabs are client-side and not in the HTML).
Not usable: 2024-02..04 (an interactive calculator showing one default configuration only) and 2024-05..2025-11
(client-rendered "Loading..." pages without prices). So the series has a gap from 2023-08 to 2025-11.
Names are mapped with canon() after dropping " - 40GB" style suffixes (memory passed to canon); Quadro RTX 5000 /
RTX 6000 (24 GB), A5000, A30 are not tracked by canon() and are skipped.
Rule when one month lists a GPU several times: the 1-GPU row (workbench table) over multi-GPU VM rows, else the
first listed. One snapshot per month: the first capture of the month that parses (up to 4 tried). Downloads are
cached outside the project (env BACKFILL_CACHE, default <tmp>/gpu-backfill-cache/jarvislabs).
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
OUT = ROOT / "data" / "backfill" / "jarvislabs.csv"
SOURCE = "Jarvislabs"
PAGE = "jarvislabs.ai/pricing"
TRIES_PER_MONTH = 4

# ---------------------------------------------------------------- Wayback Machine access (polite, cached)
CACHE = Path(os.environ.get("BACKFILL_CACHE") or Path(tempfile.gettempdir()) / "gpu-backfill-cache") / "jarvislabs"
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
    """{YYYYMM: [(timestamp, original_url), ...]}: the page itself first, then variants (trailing slash, query
    strings); '?_rsc=' payloads are dropped. Both CDX listings are merged because the prefix index can lag."""
    rows = {(ts, orig) for ts, orig, _ in _cdx(page, False) + _cdx(page, True) if "_rsc=" not in orig}
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
    m = re.fullmatch(r"\$\s*(\d+(?:\.\d+)?)\s*(?:/\s*hr)?", (s or "").strip())
    return float(m.group(1)) if m else None


# ---------------------------------------------------------------- parsing
def gpu_of(name, mem=None):
    m = re.search(r"(\d+)\s*GB", name)
    if m:
        mem = mem or int(m.group(1))
        name = name[:m.start()] + name[m.end():]
    return canon(re.sub(r"\s-\s|-$|/", " ", name).strip(" -"), mem)


def parse_cards_2021(page):
    """2021-08..2022-01: 'NVIDIA RTX A6000 48 GB of GPU Memory $ 1.79 per hour'."""
    return [(name, int(mem), "on_demand", float(p), (0, k)) for k, (name, mem, p) in enumerate(re.findall(
        r"(NVIDIA [A-Z0-9 ]+?) (\d+) GB of GPU Memory \$ ?([\d.]+) per hour", text(page)))]


def parse_tables(page):
    """2022-05..2023-07 (Reserved / Spot columns) and 2025-12..2026-05 ($/hour column) <table>s."""
    out, k = [], 0
    for table in re.findall(r"<table\b.*?</table>", page, re.S):
        rows = [[text(c) for c in re.findall(r"<t[hd]\b.*?</t[hd]>", tr, re.S)]
                for tr in re.findall(r"<tr\b.*?</tr>", table, re.S)]
        if not rows or not rows[0] or not rows[0][0].lower().startswith("gpu"):
            continue
        head = [h.lower() for h in rows[0]]
        col = lambda *keys: next((i for i, h in enumerate(head) if h in keys), None)  # noqa: E731
        cols = {"on_demand": col("$/hour", "reserved"), "spot": col("spot")}
        count, vram = col("number of gpus"), col("vram")
        if cols["on_demand"] is None:
            continue  # bare-metal 30..365-day table, or anything else without an hourly price
        for r in rows[1:]:
            if len(r) != len(head):
                continue
            n = int(r[count]) if count is not None and r[count].isdigit() else 1
            mem = re.match(r"(\d+)", r[vram]) if vram is not None else None
            k += 1
            for market, c in cols.items():
                p = money(r[c]) if c is not None else None
                if p:
                    out.append((r[0], int(mem.group(1)) // n if mem else None, market, p / n, (0 if n == 1 else 1, k)))
    return out


def parse_rows_2026(page):
    """2026-06..: '<name> <generation> · <blurb> 80 GB 200 GB 16 1 × 2 × 4 × 8 × $2.69 On-demand · /hr'."""
    t = text(page)
    rows = re.findall(r"(?:(?<=Price )|(?<=/hr ))((?:NVIDIA )?[A-Za-z0-9][A-Za-z0-9 -]{0,40}?) "
                      r"(?:Hopper|Ampere|Ada|Blackwell|Quadro|Turing|Volta) · [^$]{0,200}? (\d+) GB \d+ GB \d+ "
                      r"(?:\d+ × )+\$\s*([\d.]+) On-demand · /hr", t)
    return [(name, int(mem), "on_demand", float(p), (0, k)) for k, (name, mem, p) in enumerate(rows)]


def parse(page):
    for parser in (parse_rows_2026, parse_tables, parse_cards_2021):
        rows = parser(page)
        if rows:
            return rows
    return None


def pick(rows):
    best = {}
    for name, mem, market, price, rank in rows:
        gpu = gpu_of(name, mem)
        if not gpu or not 0.05 <= price <= 50:
            continue
        if (gpu, market) not in best or rank < best[(gpu, market)][1]:
            best[(gpu, market)] = (round(price, 4), rank, name)
    return best


# ---------------------------------------------------------------- main
def main():
    caps = captures(PAGE)
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
