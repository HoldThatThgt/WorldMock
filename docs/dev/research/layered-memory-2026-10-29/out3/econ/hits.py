import sys, collections
sys.path.insert(0, sys.path[0])
from common import *
for run in ("V", "B"):
    ev = load(RUNS[run])
    resp = {e["data"]["call_id"]: e["data"] for s, e in ev if e["event_type"] == "model_response"}
    c = collections.Counter(); seq = []
    for s, e in ev:
        if e["event_type"] == "model_request" and is_decision(e["data"]["request"]) and len(e["data"]["request"]["messages"]) == 2:
            u = resp[e["data"]["call_id"]]["response"]["usage"]
            c[u["prompt_cache_hit_tokens"]] += 1; seq.append((u["prompt_cache_hit_tokens"], u["prompt_tokens"]))
    print(run, sorted(c.items()))
    print(seq[:40])
