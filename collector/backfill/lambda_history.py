"""Lambda: monthly on-demand GPU list prices rebuilt from the Internet Archive (Wayback Machine).

    python collector/backfill/lambda_history.py      ->  data/backfill/lambda.csv

Pages and layout eras (the parser recognises them by markup, not by date):
  lambdalabs.com/service/gpu-cloud           2020..2021-04  instance cards "GPU Details 8x NVIDIA V100 (16 GB) ...
                                             Dedicated 8-GPU instance pricing $12.00 / hr" (per instance); the
                                             early-2020 "4x NVIDIA GPUs $1.50" card names no GPU and is skipped
  lambdalabs.com/service/gpu-cloud/pricing   2021-05..2023-07  <table> rows "2x A6000 | ... | $4.50 / hr" or
                                             "1x NVIDIA RTX A6000 (48 GB) | ... | $1.45 / hr"; the price is per
                                             INSTANCE and is divided by the stated GPU count
  lambdalabs.com/service/gpu-cloud           2022-06..2025-03  "On-demand pricing" <table>:
                                             "8x NVIDIA H100 SXM | 80 GB | ... | $27.92 / hr" (per instance), from
                                             2024-08 "On-demand | 8x NVIDIA H100 SXM | ... | $2.99 / GPU / hr" under a
                                             PRICE / GPU / HR column (per GPU). The 2024-01-01 capture shows both a
                                             "2023 Price" and a "2024 Price" column: ambiguous, so it is skipped and
                                             the next capture of the month is used
  lambda.ai/service/gpu-cloud                2025-03..2025-11  PRICE/GPU/HR tables, one per 8x/4x/2x/1x tab
  lambda.ai/pricing                          2025-06..         same tables ("$2.99"), from 2025-12 rows with
                                             data-plan / data-label="PRICE/GPU/HR*" (the live collector's layout)
Skipped: reserved rows and tables (by "Reserved" row label, a reserved / cluster heading above the table, or Term /
DURATION / GPU COUNT / contract-length columns), 1-Click Clusters, "Coming soon" / "Contact sales" rows,
Quadro RTX 6000 and GH200 (canon() does not track them).
Rule when one page lists a GPU several times (8x / 4x / 2x / 1x, SXM and PCIe A100 40GB...): keep the LOWEST
per-GPU price among the standard on-demand instances (in practice the 8x price for H100 SXM / B200).
One snapshot per month: the first snapshot of the month that parses, trying lambda.ai/pricing first, then the
older pages. Downloads are cached outside the project (env BACKFILL_CACHE, default <tmp>/gpu-backfill-cache).
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
OUT = ROOT / "data" / "backfill" / "lambda.csv"
SOURCE = "Lambda"
# preferred page first: when several pages have a snapshot in the same month, the first that parses wins
PAGES = ["lambda.ai/pricing", "lambda.ai/service/gpu-cloud", "lambdalabs.com/service/gpu-cloud/pricing",
         "lambdalabs.com/service/gpu-cloud"]
TRIES_PER_MONTH = 3  # snapshots tried per page and month before giving up on that page

# ---------------------------------------------------------------- Wayback Machine access (polite, cached)
CACHE = Path(os.environ.get("BACKFILL_CACHE") or Path(tempfile.gettempdir()) / "gpu-backfill-cache") / "lambda"
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
    s = re.sub(r"<[^>]+>", " ", fragment)
    return re.sub(r"\s+", " ", html.unescape(s)).replace("​", "").strip()


# tables to ignore, by column labels / by the nearest heading above them
SKIP_TABLE = re.compile(r"DURATION|GPU COUNT|\bTERM\b|COMMITMENT|MONTHS|YEARS?\b|AWS|AZURE|GOOGLE|GCP", re.I)
SKIP_HEADING = re.compile(r"reserved|1-click|cluster|private cloud|inference|commit|contract", re.I)
BADGES = re.compile(r"\b(new( price)?|coming soon|sold out|limited( availability)?|available now|popular)\b|\(.*?\)",
                    re.I)


def parse(page):
    """{gpu: lowest on-demand USD per GPU-hour} from one snapshot, or {} when no instance price table is found."""
    best = {}
    for t in re.finditer(r"<table\b.*?</table>", page, re.S | re.I):
        table = t.group(0)
        headings = re.findall(r"<h[1-6]\b[^>]*>(.*?)</h[1-6]>", page[max(0, t.start() - 20000):t.start()], re.S | re.I)
        if headings and SKIP_HEADING.search(_text(headings[-1])):
            continue  # e.g. 2022 "Reserved pricing" table, same columns as the on-demand one
        # column labels: <th> cells, data-label attributes, or a header row made of plain <td> cells (no "$")
        labels = re.findall(r"<th\b[^>]*>(.*?)</th>", table, re.S | re.I) + re.findall(r'data-label="([^"]*)"', table)
        labels += [r for r in re.findall(r"<tr\b.*?</tr>", table, re.S | re.I) if "$" not in r]
        header = " ".join(_text(x) for x in labels).upper()
        if SKIP_TABLE.search(header):
            continue  # reserved / 1-Click Cluster / comparison tables
        # "PRICE/GPU/HR" or "Price per GPU" columns are per GPU; a plain "Price" column is per instance
        # (careful: "VRAM / GPU" is also a column label)
        per_gpu_table = bool(re.search(r"PRICE(/|PER)GPU", header.replace(" ", "")))
        for row in re.findall(r"<tr\b.*?</tr>", table, re.S | re.I):
            row = re.sub(r'(?is)<span[^>]*class="[^"]*\b(tag|badge)\b[^"]*"[^>]*>.*?</span>', " ", row)
            cells = [_text(c) for c in re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
            if len(cells) >= 2 and re.fullmatch(r"(?i)on-?demand|reserved", cells[0]):
                cells = [cells[0] + " " + cells[1]] + cells[2:]  # 2024: "On-demand" | "8x NVIDIA H100 SXM" | ...
            if len(cells) < 3:
                continue
            name = re.sub(r"(?i)^on-?demand\s+", "", cells[0])
            if re.match(r"(?i)reserved", name):
                continue
            m = re.match(r"(\d+)\s*x\s+(.+)", name)
            count, name = (int(m.group(1)), m.group(2)) if m else (None, name)
            vram = next((float(v.group(1)) for v in (re.fullmatch(r"(\d+)\s*GB", c) for c in cells[1:]) if v), None)
            in_name = re.search(r"\((\d+)\s*GB\)", name)  # 2022: "8x NVIDIA A100 (80 GB)"
            vram = vram or (float(in_name.group(1)) if in_name else None)
            name = BADGES.sub(" ", name).strip()
            price_cells = [c for c in cells[1:] if re.search(r"\$\s*\d", c)]
            if len(price_cells) != 1:
                continue  # no price (Coming soon / Contact sales) or an ambiguous row
            pc = price_cells[0]
            if re.search(r"(?i)/\s*(mo|month|yr|year)\b", pc):
                continue
            price = float(re.search(r"\$\s*(\d[\d,]*\.?\d*)", pc).group(1).replace(",", ""))
            if per_gpu_table or re.search(r"(?i)/\s*GPU", pc):
                per_gpu = price
            elif count:
                per_gpu = price / count  # per-instance price of an "Nx GPU" instance
            else:
                continue
            gpu = canon(name, vram)
            if gpu and 0.05 <= per_gpu <= 50:
                best[gpu] = min(best.get(gpu, 99), round(per_gpu, 4))
    if not best:
        # 2020 instance cards: "GPU Details 8x NVIDIA V100 Tensor Core (16 GB) ... instance pricing $12.00 / hr"
        text = _text(re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page))
        for count, name, vram, price in re.findall(r"GPU Details (\d+)x (.+?) \((\d+) GB\).{0,300}?instance pricing "
                                                   r"\$\s*(\d+\.?\d*) / hr", text):
            name = re.sub(r"(?i)tensor core|[^\w ]", " ", name)
            gpu = canon(name, float(vram))
            if gpu:
                best[gpu] = min(best.get(gpu, 99), round(float(price) / int(count), 4))
    return best


# ---------------------------------------------------------------- main
def main():
    by_month = {}  # "YYYY-MM" -> [(page priority, ts, original)]
    for prio, page in enumerate(PAGES):
        for ts, original in snapshots(page):
            by_month.setdefault(ts[:6], []).append((prio, ts, original))
    rows, skipped = [], []
    for month in sorted(by_month):
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
        for gpu, price in sorted(prices.items()):
            rows.append({"date": date, "gpu": gpu, "source": SOURCE, "market": "on_demand",
                         "price": price, "url": wayback_url(ts, original)})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "gpu", "source", "market", "price", "url"])
        w.writeheader()
        w.writerows(rows)
    print("%d rows, %d months -> %s" % (len(rows), len({r["date"][:7] for r in rows}), OUT))
    print("months without a parseable price table:", ", ".join(skipped) or "none")
    timeline = {}
    for r in rows:
        timeline.setdefault(r["gpu"], []).append((r["date"][:7], r["price"]))
    for gpu in sorted(timeline):
        print("  %-12s %s" % (gpu, "  ".join("%s %.2f" % mp for mp in timeline[gpu])))


if __name__ == "__main__":
    main()
