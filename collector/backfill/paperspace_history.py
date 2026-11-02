"""Paperspace: monthly on-demand GPU list prices rebuilt from the Internet Archive (Wayback Machine).

    python collector/backfill/paperspace_history.py      ->  data/backfill/paperspace.csv

Page: paperspace.com/pricing (www.), the only URL; it lists Gradient (notebook plans) and CORE (virtual machines).
We read the CORE "Hourly" machine cards, one price per single-GPU machine (all per GPU-hour already):
  2020         "V100 | $ 2.30 / hour 1343 / month + $5 monthly storage fee | ... | Sign up"
  2021..       "V100 | <description> | $ | 2.30 | / hour | 16 GB GPU | ... | Create"
  2022..2024-0x adds "A6000 | New | $ | 1.89 | / hour | NVIDIA A6000 GPU | ... | Create" and "A100" ($3.09, the 40GB
               A100; canon() maps a bare "A100" to A100 40GB)
  2024-0x..    the A100 card becomes "A100-80G $1.15** / hour" and H100 shows "$2.24* / hour"; both are 3-year
               commitment prices (footnote), so they are skipped. The footnote "On-demand pricing for H100 is
               $8.96 per hour" (2024-01), later "$5.95/hour", is the H100 on-demand price (an HGX H100, i.e.
               canon "H100 SXM").
Skipped: monthly cards ("/ month", "effective hourly price"), starred (commitment) prices, "Reserve Now" cards,
cards whose spec line names another GPU (2023 "H100 ... NVIDIA A100 GPU $3.09" was a copy of the A100 card),
cards with two prices, Gradient notebook instances (they have no Create / Sign up button and list two prices).
The page has been effectively frozen since the DigitalOcean acquisition (2023), so most series are flat.
One snapshot per month (the first that parses). Downloads are cached outside the project (env BACKFILL_CACHE,
default <tmp>/gpu-backfill-cache).
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
OUT = ROOT / "data" / "backfill" / "paperspace.csv"
PAGES = ["paperspace.com/pricing"]
SOURCE = "Paperspace"
TRIES_PER_MONTH = 3

# ---------------------------------------------------------------- Wayback Machine access (polite, cached)
CACHE = Path(os.environ.get("BACKFILL_CACHE") or Path(tempfile.gettempdir()) / "gpu-backfill-cache") / "paperspace"
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
def _lines(page):
    """Visible text, one block element per line (cards are sequences of short lines)."""
    s = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", page)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|td|th|tr|a|button)>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    lines = (re.sub(r"[\s\u200b\u200d]+", " ", line).strip() for line in s.split("\n"))
    return [line for line in lines if line]


BADGE = r"(?:(?:New|NewLy|Available|Popular|Limited)\|)*"
# 2021+: "A100|New|$|3.09|/ hour|NVIDIA A100 GPU|90GB RAM|12 vCPU|Create", sometimes with a one-line
# description before the price ("V100|The most powerful GPU in the world with 112 TeraFLOPS|$|2.30|/ hour|...")
CARD = re.compile(r"(?<=\|)(?P<name>[A-Za-z0-9+-]+(?: ?x ?\d)?)\|" + BADGE + r"(?:[^|$]{15,200}\|)?"
                  r"\$\|(?P<price>[\d.]+)(?P<star>\**)\|/ hour\|(?P<body>(?:[^|]+\|){1,12}?)(?P<end>Create|Reserve Now)(?=\|)")
# 2020: "V100|$ 2.30 / hour 1343 / month + $5 monthly storage fee|...|Sign up"
CARD_2020 = re.compile(r"(?<=\|)(?P<name>[A-Za-z0-9+-]+)\|\$ (?P<price>[\d.]+) / hour \d+ / month[^|]*\|"
                       r"(?P<body>(?:[^|]+\|){1,8}?)(?P<end>Sign up)(?=\|)")
FOOTNOTE = re.compile(r"On-demand pricing for (?P<name>H100|A100[\w-]*) is \$(?P<price>[\d.]+)\s*(?:/|per)\s*hour", re.I)


def _model(name):
    return re.sub(r"[-\s].*|x\d+$", "", name.upper())


def parse(page):
    """{gpu: on-demand USD per GPU-hour} from the Core machine cards of one snapshot."""
    text = "|" + "|".join(_lines(page)) + "|"
    found = []
    for m in list(CARD.finditer(text)) + list(CARD_2020.finditer(text)):
        body = re.sub(r"\$[\d.]+ effective hourly price", "", m.group("body"))  # 2020: monthly plan's rate
        if m.group("end") != "Create" and m.group("end") != "Sign up":
            continue  # "Reserve Now": not self-serve on-demand
        if (m.groupdict().get("star") or "") or "$" in body or "/ hour" in body:
            continue  # starred = 3-year commitment price; a second price in the card = ambiguous
        label = re.search(r"NVIDIA (?:HGX |Quadro |RTX )?([A-Z]+\d+)\w* GPU", body)
        if label and label.group(1).upper() != _model(m.group("name")):
            continue  # copy-paste card: e.g. 2023 "H100 ... NVIDIA A100 GPU" repeating the A100 price
        name = m.group("name")
        count = re.search(r" ?x ?(\d)$", name)
        if count:
            name = name[:count.start()]
        found.append((name, float(m.group("price")) / (int(count.group(1)) if count else 1)))
    for m in FOOTNOTE.finditer(text):  # 2024+: "On-demand pricing for H100 is $5.95/hour" (card shows 3-yr price)
        found.append((m.group("name"), float(m.group("price"))))
    best = {}
    for name, price in found:
        gpu = canon(name.replace("-", " "))  # "A100-80G" -> A100 80GB, "A100" -> A100 40GB, "A6000" -> RTX A6000
        if gpu and 0.05 <= price <= 50:
            best[gpu] = min(best.get(gpu, 99), round(price, 4))
    return best


# ---------------------------------------------------------------- main
def main():
    rows, skipped = [], []
    by_month = {}
    for ts, original in snapshots(PAGES[0]):
        by_month.setdefault(ts[:6], []).append((ts, original))
    for month in sorted(by_month):
        result = None
        for ts, original in by_month[month][:TRIES_PER_MONTH]:
            prices = parse(snapshot_html(ts, original))
            if prices:
                result = (ts, original, prices)
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
    print("months without parseable prices:", ", ".join(skipped) or "none")
    timeline = {}
    for r in rows:
        timeline.setdefault(r["gpu"], []).append((r["date"][:7], r["price"]))
    for gpu in sorted(timeline):
        print("  %-12s %s" % (gpu, "  ".join("%s %.2f" % mp for mp in timeline[gpu])))


if __name__ == "__main__":
    main()
