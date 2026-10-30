"""Read-only: actual usage/cost by call class and by simulated day (V or B)."""
import sys, collections, json
sys.path.insert(0, sys.path[0])
from common import *
run = sys.argv[1] if len(sys.argv) > 1 else "V"
ev = load(RUNS[run])
resp = {e["data"]["call_id"]: e["data"] for s, e in ev if e["event_type"] == "model_response"}
agg = collections.defaultdict(lambda: [0, 0, 0, 0]); dayagg = collections.defaultdict(lambda: [0, 0, 0, 0])
for s, e in ev:
    if e["event_type"] != "model_request":
        continue
    d = e["data"]; r = d["request"]; u = resp[d["call_id"]]["response"]["usage"]
    if is_decision(r):
        c = "fresh" if len(r["messages"]) == 2 else "history"
    else:
        c = d.get("purpose") or ("reflection" if any(t["function"]["name"] == "submit_reflection" for t in r.get("tools") or []) else "consolidation")
    h, m, o = u["prompt_cache_hit_tokens"], u["prompt_cache_miss_tokens"], u["completion_tokens"]
    for a in (agg[c], dayagg[(day(e["occurred_at"]), c)]):
        a[0] += h; a[1] += m; a[2] += o; a[3] += 1
def cost(a): return a[0] * P_HIT + a[1] * P_MISS + a[2] * P_OUT
tot = sum(cost(a) for a in agg.values())
print(run, "total $%.4f" % tot)
for c, a in sorted(agg.items()):
    print(f"  {c:14s} n={a[3]:4d} hit={a[0]:9d} miss={a[1]:9d} out={a[2]:8d} ${cost(a):.4f} hit%={a[0]/(a[0]+a[1]):.3f}"
          f" in$/call={(a[0]*P_HIT+a[1]*P_MISS)/a[3]:.5f} out$/call={a[2]*P_OUT/a[3]:.5f}")
days = sorted({k[0] for k in dayagg})
for dd in days:
    row = {c: dayagg[(dd, c)] for c in ("fresh", "history")}
    print(dd, {c: (a[3], round(cost(a), 4)) for c, a in row.items()}, "all $%.4f" % sum(cost(a) for k, a in dayagg.items() if k[0] == dd))
