import sys, collections, os
sys.path.insert(0, sys.path[0])
from common import *
ev = load(RUNS["V"])
resp = {e["data"]["call_id"]: e["data"] for s, e in ev if e["event_type"] == "model_response"}
prev = None; rows = []
sysset = collections.Counter(); toolset = collections.Counter()
for s, e in ev:
    if e["event_type"] != "model_request": continue
    r = e["data"]["request"]
    if not is_decision(r): continue
    sysset[r["messages"][0]["content"][:50] + str(len(r["messages"][0]["content"]))] += 1
    toolset[len(ser(r["tools"]))] += 1
    if len(r["messages"]) != 2: continue
    u = resp[e["data"]["call_id"]]["response"]["usage"]
    user = r["messages"][1]["content"]
    if prev is not None:
        n = len(os.path.commonprefix([user, prev]))
        rows.append((u["prompt_cache_hit_tokens"], n, user[max(0,n-30):n+30]))
    prev = user
print(sysset, toolset)
by = collections.defaultdict(list)
for h, n, ctx in rows: by[h].append(n)
for h in sorted(by): 
    v = sorted(by[h]); print(h, len(v), "lcp chars min/med/max", v[0], v[len(v)//2], v[-1])
for h, n, ctx in rows[:30]: print(h, n, ctx.replace("\n"," ")[:60])
