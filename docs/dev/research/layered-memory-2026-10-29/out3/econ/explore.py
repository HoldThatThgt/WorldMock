import sys, collections, json
sys.path.insert(0, sys.path[0])
from common import *
ev = load(RUNS[sys.argv[1] if len(sys.argv) > 1 else "V"])
resp = {}
for s, e in ev:
    if e["event_type"] == "model_response":
        resp[e["data"]["call_id"]] = e["data"]
reqs = [(s, e) for s, e in ev if e["event_type"] == "model_request"]
print("requests", len(reqs), "responses", len(resp))
cls = collections.Counter(); keys = set()
for s, e in reqs[:]:
    d = e["data"]; r = d["request"]
    k = ("decision" if is_decision(r) else d.get("purpose") or "other", len(r["messages"]) == 2)
    cls[k] += 1
print(cls)
s, e = reqs[0]
print(json.dumps({k: v for k, v in e["data"].items() if k != "request"}, ensure_ascii=False)[:800])
print("request keys", list(e["data"]["request"].keys()))
rd = resp[e["data"]["call_id"]]
print("resp keys", list(rd.keys()))
print(json.dumps(rd.get("response", {}).get("usage"), ensure_ascii=False))
print("recorded_at?", e.get("recorded_at"), e.get("occurred_at"), list(e.keys()))
