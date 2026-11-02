"""RunPod: monthly Community / Secure Cloud GPU list prices rebuilt from the Internet Archive (Wayback Machine).

    python collector/backfill/runpod_history.py      ->  data/backfill/runpod.csv

Pages and layout eras (recognised by markup, not by date):
  runpod.io/gpu-instance/pricing  2022-07..2024-06  Next.js page. The tables ("1x A100 80GB ... Spot / On-Demand
                                  ($/hr)") and later cards are rendered from the GraphQL gpuTypes embedded in
                                  <script id="__NEXT_DATA__">, which is what we read:
                                    2022: one object per GPU *per cloud* (secureCloud / communityCloud flag) with
                                          lowestPrice.uninterruptablePrice (on-demand) and .minimumBidPrice (spot)
                                    2023+: one object per GPU with securePrice / communityPrice and
                                          secureSpotPrice / communitySpotPrice
  runpod.io/pricing               2024-07..2025-06  same Next.js data (cards "$ 2.99 /hr Secure Cloud")
                                  2025-07..         Webflow page: every GPU list item has the prices as attributes,
                                                    data-secure-cloud-price / data-community-cloud-price, from 2026
                                                    data-secure / data-community (JS shows one, toggled by cloud)
Older captures (runpod.io, runpod.io/gpu-cloud, 2021-2022) are JS shells without prices.
Rules: a price is kept only when that cloud's flag is set (RunPod leaves stale prices on disabled clouds) and it is
> $0.05. Spot is recorded only when the page itself shows a Spot column (2022-07..09, 2023-08..2024-04); other
pages carry spot fields in their data but do not display them. All prices are per GPU-hour already; when several
RunPod SKUs map to one canonical GPU (A100 PCIe / A100 SXM -> A100 80GB, V100 variants) the lowest price is kept.
One snapshot per month: the first that parses, runpod.io/pricing before the older page. Downloads are cached
outside the project (env BACKFILL_CACHE, default <tmp>/gpu-backfill-cache).
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import canon  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "data" / "backfill" / "runpod.csv"
PAGES = ["runpod.io/pricing", "runpod.io/gpu-instance/pricing"]
TRIES_PER_MONTH = 3
FIRST_MONTH = "202210"  # 2022-07..09 pages show the lowest live offer, not the list price: not comparable

# ---------------------------------------------------------------- Wayback Machine access (polite, cached)
CACHE = Path(os.environ.get("BACKFILL_CACHE") or Path(tempfile.gettempdir()) / "gpu-backfill-cache") / "runpod"
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
        time.sleep(10 * 2 ** attempt)


def snapshots(page):
    """[(timestamp, original_url)] of 200-status captures since 2020, at most one per day (cached for a day)."""
    fn = CACHE / ("cdx_" + re.sub(r"\W", "_", page) + ".txt")
    if not fn.exists() or time.time() - fn.stat().st_mtime > 86400:
        q = ("https://web.archive.org/cdx/search/cdx?url=%s&output=json&fl=timestamp,original,statuscode"
             "&filter=statuscode:200&collapse=timestamp:8&from=2020") % urllib.parse.quote(page, safe="/:")
        rows = json.loads(_get(q) or b"[]")[1:]
        fn.parent.mkdir(parents=True, exist_ok=True)
        fn.write_text("\n".join("%s %s" % (r[0], r[1]) for r in rows), encoding="utf-8")
    return [tuple(line.split(" ", 1)) for line in fn.read_text(encoding="utf-8").splitlines() if line.strip()]


def snapshot_html(ts, original):
    fn = CACHE / ("%s_%s.html" % (ts, re.sub(r"\W", "_", original.split("//", 1)[-1])[:80]))
    if not fn.exists():
        body = _get("https://web.archive.org/web/%sid_/%s" % (ts, original))  # id_ = original HTML, no toolbar
        if body is None:
            return ""
        fn.parent.mkdir(parents=True, exist_ok=True)
        fn.write_bytes(body)
    body = fn.read_bytes()
    while body[:2] == b"\x1f\x8b":  # many captures are stored with the site's gzip Content-Encoding
        body = gzip.decompress(body)
    return body.decode("utf-8", "replace")


def wayback_url(ts, original):
    return "https://web.archive.org/web/%s/%s" % (ts, original)


# ---------------------------------------------------------------- parsing
def _text(fragment):
    s = re.sub(r"(?is)<(script|style|svg)\b.*?</\1>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()


def _price(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if 0.05 <= v <= 50 else None  # 0 / null = not offered in that cloud


def _gpu_types(node):
    """Every GraphQL GpuType object (a dict with displayName) anywhere in the Next.js page data."""
    if isinstance(node, dict):
        if "displayName" in node:
            yield node
        else:
            for v in node.values():
                yield from _gpu_types(v)
    elif isinstance(node, list):
        for v in node:
            yield from _gpu_types(v)


def parse_next_data(page, shows_spot):
    """2022-07..2025: Next.js page; the price tables are rendered from GraphQL gpuTypes in __NEXT_DATA__."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))["props"]["pageProps"]
    except (ValueError, KeyError):
        return []
    out = []
    for g in _gpu_types(data):
        gpu = canon(g.get("displayName") or g.get("id") or "", g.get("memoryInGb"))
        if not gpu:
            continue
        if "securePrice" in g or "communityPrice" in g:
            # 2023+: one object per GPU with securePrice / communityPrice (+ *SpotPrice); the cloud flags say
            # whether that price is actually offered (RunPod leaves stale prices on disabled clouds)
            for tier, flag in (("Secure", "secureCloud"), ("Community", "communityCloud")):
                if not g.get(flag):
                    continue
                key = tier.lower()
                out.append((gpu, "RunPod " + tier, "on_demand", _price(g.get(key + "Price"))))
                if shows_spot:
                    out.append((gpu, "RunPod " + tier, "spot", _price(g.get(key + "SpotPrice"))))
        else:
            # 2022: one object per GPU *per cloud* with lowestPrice {uninterruptablePrice, minimumBidPrice}
            flags = [t for t, f in (("Secure", "secureCloud"), ("Community", "communityCloud")) if g.get(f)]
            lp = g.get("lowestPrice") or {}
            if len(flags) != 1:
                continue
            out.append((gpu, "RunPod " + flags[0], "on_demand", _price(lp.get("uninterruptablePrice"))))
            if shows_spot:
                out.append((gpu, "RunPod " + flags[0], "spot", _price(lp.get("minimumBidPrice"))))
    return [o for o in out if o[3]]


def parse_webflow(page):
    """2025-07..: Webflow page; each GPU list item carries both list prices as attributes:
    data-secure-cloud-price / data-community-cloud-price (2025), later data-secure / data-community (2026)."""
    out = []
    for item in re.split(r'role="listitem"', page)[1:]:
        secure = re.search(r'data-secure(?:-cloud-price)?="([^"]*)"', item)
        community = re.search(r'data-community(?:-cloud-price)?="([^"]*)"', item)
        if not (secure or community):
            continue
        name = (re.search(r'class="gpu_name[^"]*">([^<]+)<', item) or                          # 2026
                re.search(r'gpu-pricing-row__model-wrapper"><div[^>]*>([^<]+)<', item) or     # 2025
                re.search(r'deploy\?gpu=([^"&]+)"', item))
        if not name:
            continue
        name = urllib.parse.unquote_plus(html.unescape(name.group(1))).strip()
        vram = re.search(r">\s*(\d+)\s*(?:</div>\s*<div[^>]*>\s*)?GB (?:VRAM|HBM)", item)
        gpu = canon(name, float(vram.group(1)) if vram else None)
        if not gpu:
            continue
        for tier, attr in (("Secure", secure), ("Community", community)):
            p = _price(attr.group(1)) if attr else None
            if p:
                out.append((gpu, "RunPod " + tier, "on_demand", p))
    return out


def parse(page):
    """{(gpu, source, market): lowest USD per GPU-hour} from one snapshot ({} if nothing confidently parsed)."""
    # a "Spot" column / label on the page itself ("Spot<br/>($/hr)" header in 2022, "Spot" label per card 2023-24)
    shows_spot = bool(re.search(r">\s*(Spot|Interruptible)\s*(<br\s*/?>\s*\(\$/hr\)\s*)?<", page))
    obs = parse_next_data(page, shows_spot) or parse_webflow(page)
    best = {}
    for gpu, source, market, price in obs:
        best[(gpu, source, market)] = min(best.get((gpu, source, market), 99), price)
    return best


# ---------------------------------------------------------------- main
def main():
    by_month = {}  # "YYYY-MM" -> [(page priority, ts, original)]
    for prio, page in enumerate(PAGES):
        for ts, original in snapshots(page):
            by_month.setdefault(ts[:6], []).append((prio, ts, original))
    rows, skipped = [], []
    for month in sorted(m for m in by_month if m >= FIRST_MONTH):
        result = None
        for prio in range(len(PAGES)):
            for _, ts, original in sorted(c for c in by_month[month] if c[0] == prio)[:TRIES_PER_MONTH]:
                prices = parse(snapshot_html(ts, original))
                if prices:
                    result = (ts, original, prices)
                    break
            if result:
                break
        if not result:
            skipped.append(month)
            continue
        ts, original, prices = result
        date = "%s-%s-%s" % (ts[:4], ts[4:6], ts[6:8])
        for (gpu, source, market), price in sorted(prices.items()):
            rows.append({"date": date, "gpu": gpu, "source": source, "market": market,
                         "price": round(price, 4), "url": wayback_url(ts, original)})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "gpu", "source", "market", "price", "url"])
        w.writeheader()
        w.writerows(rows)
    print("%d rows, %d months -> %s" % (len(rows), len({r["date"][:7] for r in rows}), OUT))
    print("months without parseable prices:", ", ".join(skipped) or "none")
    timeline = {}
    for r in rows:
        timeline.setdefault((r["source"], r["market"], r["gpu"]), []).append((r["date"][:7], r["price"]))
    for key in sorted(timeline):
        print("  %-16s %-9s %-12s %s" % (key + ("  ".join("%s %.2f" % mp for mp in timeline[key]),)))


if __name__ == "__main__":
    main()
