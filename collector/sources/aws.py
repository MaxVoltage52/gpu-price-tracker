"""AWS EC2.

Prices: spot from the public feed behind aws.amazon.com/ec2/spot/pricing (every region), and
on-demand from the pricing JSON the AWS website loads (a few large regions). One representative
instance per GPU.
Demand: the Spot Instance Advisor feed — per instance and region, the interruption-frequency
band and the average discount vs on-demand. More interruptions / smaller discounts mean
spot capacity is scarcer.
"""
import json
import statistics
import urllib.error
import urllib.parse

from common import http, http_json

NAME = "AWS"
ON_DEMAND_REGIONS = ["US East (N. Virginia)", "US East (Ohio)", "US West (Oregon)", "EU (Frankfurt)"]
INSTANCES = {
    "p6-b300.48xlarge": ("B300", 8),
    "p6-b200.48xlarge": ("B200", 8),
    "p5en.48xlarge": ("H200", 8),
    "p5e.48xlarge": ("H200", 8),
    "p5.48xlarge": ("H100 SXM", 8),
    "p4de.24xlarge": ("A100 80GB", 8),
    "p4d.24xlarge": ("A100 40GB", 8),
    "g6e.xlarge": ("L40S", 1),
    "g6.xlarge": ("L4", 1),
    "g5.xlarge": ("A10", 1),
    "g4dn.xlarge": ("T4", 1),
    "p3.2xlarge": ("V100", 1),
}


def fetch():
    text = http("https://website.spot.ec2.aws.a2z.com/spot.js", timeout=120).decode()
    feed = json.loads(text[text.index("(") + 1: text.rindex(")")])
    prices = []
    for region in feed["config"]["regions"]:
        for family in region["instanceTypes"]:
            for size in family["sizes"]:
                if size["size"] not in INSTANCES:
                    continue
                gpu, count = INSTANCES[size["size"]]
                for col in size["valueColumns"]:
                    try:
                        if col["name"] == "linux":
                            prices.append((gpu, NAME, "spot", float(col["prices"]["USD"]) / count))
                    except (KeyError, ValueError):
                        pass  # "N/A*"

    for region in ON_DEMAND_REGIONS:
        url = ("https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/"
               f"{urllib.parse.quote(region)}/Linux/index.json")
        try:
            regions = http_json(url, timeout=120)["regions"]
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue  # region renamed or removed; the others still count
            raise
        for rows in regions.values():
            for r in rows.values():
                if r.get("Instance Type") in INSTANCES:
                    gpu, count = INSTANCES[r["Instance Type"]]
                    prices.append((gpu, NAME, "on_demand", float(r["price"]) / count))

    advisor = http_json("https://spot-bid-advisor.s3.amazonaws.com/spot-advisor-data.json", timeout=120)
    band_mid = {0: 2.5, 1: 7.5, 2: 12.5, 3: 17.5, 4: 25}  # bands: <5%, 5-10%, 10-15%, 15-20%, >20%
    interrupt, discount = {}, {}
    for region in advisor["spot_advisor"].values():
        for inst, v in region.get("Linux", {}).items():
            if inst in INSTANCES:
                gpu = INSTANCES[inst][0]
                interrupt.setdefault(gpu, []).append(band_mid[v["r"]])
                if v.get("s"):
                    discount.setdefault(gpu, []).append(v["s"])
    demand = [(g, NAME, "interrupt_pct", round(statistics.mean(v), 2)) for g, v in interrupt.items()]
    demand += [(g, NAME, "spot_discount_pct", round(statistics.mean(v), 2)) for g, v in discount.items()]
    return prices, demand
