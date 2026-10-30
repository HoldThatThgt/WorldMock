"""Where else (besides 人设层) a then-present view text appears in the decision request, by layer."""
import json, collections
from common import *
from model import Run
for run in ("V", "B"):
    R = Run(run)
    c = collections.Counter()
    for d in R.decisions:
        if not d["fresh"]: continue
        arr = json.loads(d["requests"][0]["data"]["request"]["messages"][1]["content"])
        lay = {l["layer"]: compact(l) for l in arr}
        for p in d["pv"]["preferences"]:
            c["pairs"] += 1
            for name, s in lay.items():
                if name != "人设层" and p["preference"] in s: c[name] += 1
    print(run, dict(c))
