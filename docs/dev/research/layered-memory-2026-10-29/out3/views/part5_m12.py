"""Part 5: baseline of M12 「表态食品回购」 in V and B.

M12 = among food purchases (meal/snack/drink/kit order lines) from run day 2 on, the share whose goods was named by a
preference view written on an EARLIER local day. 'Named' = the view subject, NFKC-normalised, equals the goods name or
contains it, or is contained in it with ≥4 chars, and the subject names at most 2 catalog goods (so generic subjects such
as 一人份食材包 or 外卖 do not count). Computed from committed events and the frozen catalog only, so it is identical
whether or not the views were in the prompt."""
import json, collections
from common import *
from model import Run

def named(subject, gname, all_names):
    s, g = norm(subject), norm(gname)
    if len(s) < 3: return False
    hits = [n for n in all_names if s == n or (len(s) >= 4 and (s in n or n in s))]
    if len(hits) > 2: return False
    return s == g or (len(s) >= 4 and (s in g or g in s))

def main(run):
    R = Run(run)
    all_names = [norm(g["name"]) for g in R.goods.values()]
    prefs = [v for v in R.view_rows if v["type"] == "preference"]
    first_day = min(R.workday)
    rows = []
    for o in sorted(R.orders.values(), key=lambda x: x["created_at"]):
        if not R.food_goods(o["goods_id"]): continue
        dday = day(o["created_at"])
        if dday == first_day: continue
        gname = R.goods[o["goods_id"]]["name"]
        src = [(v["about"], v["day"]) for v in prefs if v["day"] < dday and named(v["about"], gname, all_names)]
        rows.append({"t": hm(o["created_at"]), "goods": gname, "endorsed_by": sorted(set(src))})
    n = len(rows); k = sum(1 for r in rows if r["endorsed_by"])
    return {"food_purchases_day2plus": n, "endorsed_earlier_day": k, "M12": round(k / n, 3) if n else None,
            "rows": rows}

if __name__ == "__main__":
    res = {r: main(r) for r in ("V", "B")}
    json.dump(res, open("part5_m12.json", "w"), ensure_ascii=False, indent=1)
    for r, o in res.items():
        print(r, o["food_purchases_day2plus"], o["endorsed_earlier_day"], o["M12"])
        for x in o["rows"]:
            if x["endorsed_by"]: print("   ", x["t"], x["goods"], x["endorsed_by"])
