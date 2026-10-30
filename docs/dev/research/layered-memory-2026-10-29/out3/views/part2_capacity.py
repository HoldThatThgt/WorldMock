"""Part 2c: capacity estimate for attitudes in a 200字 day digest, and raw-view-to-cap ratios for month/year.

Assumption (stated in the report): an attitude-aware day digest spends about half of its 200 chars on the course
(food: the coverage-guard names alone are 24-44 chars/day) and ~90-100 chars on attitudes, compressing each view to a
~15-20 char clause (object + stance), i.e. 4-6 view objects per category-day. Measured view text: mean 42 chars."""
import json
from common import *
flow = json.load(open("part2_flow.json"))
out = {}
for run in ("V", "B"):
    cells = flow[run]["per_day_category"]
    res = {}
    for slots in (4, 5, 6):
        per = {}
        for k, v in cells.items():
            c = k.split("|")[1]
            a = per.setdefault(c, [0, 0]); a[0] += min(v["objects"], slots); a[1] += v["objects"]
        tot = [sum(x[0] for x in per.values()), sum(x[1] for x in per.values())]
        res[slots] = {c: f"{a[0]}/{a[1]}={a[0]/a[1]:.0%}" for c, a in per.items()} | {"all": f"{tot[0]}/{tot[1]}={tot[0]/tot[1]:.0%}"}
    days = 5
    proj = {}
    for c in ("饮食", "工作", "生活"):
        mean = flow[run][f"cat_{c}"]["rendered_per_day"]["mean"]
        proj[c] = {"views_chars_per_day": mean, "x_day_cap": round(mean / 200, 2),
                   "month_raw": round(mean * 30), "month_cap_share": f"{800 / (mean * 30):.0%}",
                   "year_raw": round(mean * 365), "year_cap_share": f"{2000 / (mean * 365):.1%}"}
    out[run] = {"objects_kept_by_slots": res, "projection": proj}
json.dump(out, open("part2_capacity.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False, indent=1))
