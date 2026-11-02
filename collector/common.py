"""Shared helpers for the source modules: HTTP with retries and GPU name normalisation.

Every source module exposes NAME and fetch() -> (prices, demand):
  prices: (gpu, source, market, price_per_gpu_hour)            one observation (an offer, a region, a SKU)
       or (gpu, source, market, (min, median, n))              already aggregated by the provider
  demand: (gpu, source, metric, value)                         see METRICS in build.py
"""
import gzip
import json
import re
import time
import urllib.error
import urllib.request

UA = {"User-Agent": "gpu-price-tracker/1.0 (+https://github.com)", "Accept": "application/json, text/plain, */*",
      "Accept-Encoding": "gzip"}


def http(url, data=None, method=None, headers=None, timeout=60, tries=5):
    h = dict(UA, **(headers or {}))
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body
        except urllib.error.HTTPError as e:
            if attempt == tries - 1 or (e.code != 429 and e.code < 500):
                raise
            time.sleep(float(e.headers.get("Retry-After") or 5 * 2 ** attempt))
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
            time.sleep(5 * 2 ** attempt)


def http_json(url, data=None, method=None, headers=None, timeout=60):
    return json.loads(http(url, data, method, headers, timeout))


_fx = {}


def eur_usd():
    """ECB reference rate, fetched once per run."""
    if "EUR" not in _fx:
        xml = http("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml").decode()
        _fx["EUR"] = float(re.search(r"currency=['\"]USD['\"] rate=['\"]([\d.]+)", xml).group(1))
    return _fx["EUR"]


def canon(name, mem_gb=None):
    """Map a provider's model name to one canonical GPU name, or None if we don't track it."""
    n = re.sub(r"\b(NVIDIA|GEFORCE|TESLA|AMD|INSTINCT)\b", "", name.upper())
    n = re.sub(r"[-_]", " ", n)
    n = re.sub(r"\bRTX(\d)", r"RTX \1", n)
    n = re.sub(r"\s+", " ", n).strip()
    if "MIG" in n:
        return None
    if any(x in n for x in ("GB300", "GB200", "GH200")):
        return None  # Grace superchips: different product (and GB parts are sold as whole racks)
    if "B300" in n:
        return "B300"
    if "B200" in n:
        return "B200"
    if "H200" in n:
        return "H200 NVL" if "NVL" in n else "H200"
    if "H100" in n:
        if "NVL" in n:
            return "H100 NVL"
        return "H100 PCIe" if "PCIE" in n else "H100 SXM"
    if "A100" in n:
        big = "80G" in n or (mem_gb or 0) >= 70 or n == "A100X"
        return "A100 80GB" if big else "A100 40GB"
    if "MI300X" in n:
        return "MI300X"
    if "MI325X" in n:
        return "MI325X"
    if re.search(r"PRO ?6000", n):
        return "RTX PRO 6000"
    if re.search(r"6000 ?ADA", n):
        return "RTX 6000 Ada"
    if re.fullmatch(r"(RTX )?A6000", n):
        return "RTX A6000"
    if re.fullmatch(r"RTX (5090|4090|3090)", n):
        return n
    simple = {"L40S": "L40S", "L40": "L40", "L4": "L4", "A40": "A40",
              "A10": "A10", "A10G": "A10", "T4": "T4"}
    if n in simple:
        return simple[n]
    if n.startswith("V100"):
        return "V100"
    return None
