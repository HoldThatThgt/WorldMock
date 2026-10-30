"""Part 5: baseline of the proposed deterministic pilot metric M12 「看法对象回访」 in V and B.

Unit: (target, day d) where a concluded activity on local day d had a concrete target (food goods for purchases,
content_id for location activities, app_id for app use, destination for travel). Group A: some successful conclude
of that activity on day d wrote a preference view. Group C (control): no view. Outcome: the same target is started
again on a later local day (window 1: d+1..d+3; window 2: >= d+4). Lift = rate(A) - rate(C)."""
import json, collections
from common import *
from model import Run

def targets(R, act):
    if act is None: return []
    k = act["activity_type"]; dd = act["data"]
    if k == "purchase":
        return [("goods", R.orders[o]["goods_id"]) for o in dd.get("order_ids", []) if o in R.orders
                and R.food_goods(R.orders[o]["goods_id"])]
    if k == "interact":
        return [("app", dd["app_id"])] if dd.get("app_id") else [("content", dd.get("content_id"))]
    if k == "travel":
        return [("dest", act.get("destination_id"))]
    return []

def dnum(s): return int(s[3:])

def main(run):
    R = Run(run)
    started = collections.defaultdict(set)  # target -> days started
    for a in R.acts.values():
        for t in targets(R, a):
            started[t].add(day(a["started_at"]))
    viewed = set()
    for v in R.view_rows:
        if v["type"] == "preference":
            for t in targets(R, v["act"]):
                viewed.add((t, v["day"]))
    units = set()
    for a in R.acts.values():
        if a.get("concluded_at"):
            for t in targets(R, a):
                units.add((t, day(a["concluded_at"])))
    last = max(dnum(d) for d in R.workday)
    out = {}
    for name, lo, hi in (("d+1..d+3", 1, 3), (">=d+4", 4, 99)):
        g = {"A": [0, 0], "C": [0, 0]}; kinds = collections.defaultdict(lambda: {"A": [0, 0], "C": [0, 0]})
        for t, d in units:
            if dnum(d) + lo > last: continue
            grp = "A" if (t, d) in viewed else "C"
            hit = any(dnum(d) + lo <= dnum(x) <= dnum(d) + hi for x in started[t])
            g[grp][0] += hit; g[grp][1] += 1
            kinds[t[0]][grp][0] += hit; kinds[t[0]][grp][1] += 1
        rate = {k: (round(v[0] / v[1], 2) if v[1] else None, v[1]) for k, v in g.items()}
        lift = rate["A"][0] - rate["C"][0] if rate["A"][0] is not None and rate["C"][0] is not None else None
        out[name] = {"rate_A_view": rate["A"], "rate_C_noview": rate["C"], "lift": round(lift, 2) if lift is not None else None,
                     "by_kind": {k: {gg: (round(x[0] / x[1], 2) if x[1] else None, x[1]) for gg, x in v.items()}
                                 for k, v in kinds.items()}}
    return out

if __name__ == "__main__":
    res = {r: main(r) for r in ("V", "B")}
    json.dump(res, open("part5_metric.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1))
