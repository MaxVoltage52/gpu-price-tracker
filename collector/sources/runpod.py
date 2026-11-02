"""RunPod — public GraphQL.

Prices: Community and Secure Cloud list prices.
Demand: stock level of a single GPU in each cloud (High / Medium / Low, or none left).
"""
from common import canon, http_json

NAME = "RunPod"
STOCK = {"High": 3, "Medium": 2, "Low": 1}
QUERY = """query { gpuTypes {
  displayName memoryInGb communityCloud secureCloud communityPrice securePrice
  secure: lowestPrice(input: {gpuCount: 1, secureCloud: true}) { stockStatus }
  community: lowestPrice(input: {gpuCount: 1, secureCloud: false}) { stockStatus }
} }"""


def fetch():
    data = http_json("https://api.runpod.io/graphql", {"query": QUERY}, method="POST")
    prices, demand = [], []
    for g in data["data"]["gpuTypes"]:
        gpu = canon(g["displayName"], g.get("memoryInGb"))
        if not gpu:
            continue
        for tier, flag, price_field, stock_field in [("Community", "communityCloud", "communityPrice", "community"),
                                                     ("Secure", "secureCloud", "securePrice", "secure")]:
            if not g.get(flag):
                continue
            source = f"RunPod {tier}"
            if g.get(price_field):
                prices.append((gpu, source, "on_demand", g[price_field]))
            status = (g.get(stock_field) or {}).get("stockStatus")
            demand.append((gpu, source, "stock", STOCK.get(status, 0)))
    # several RunPod SKUs can map to one GPU (e.g. PRO 6000 variants): keep the best stock level
    best = {}
    for gpu, source, metric, value in demand:
        best[(gpu, source)] = max(best.get((gpu, source), 0), value)
    return prices, [(g, s, "stock", v) for (g, s), v in best.items()]
