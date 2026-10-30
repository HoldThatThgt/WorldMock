"""Output tokens per char for Chinese prose (consolidation/reflection/plan_day outputs) and plan_day retry caching (V+B)."""
import sys, json, re
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from common import *
from tok import feats
import numpy as np
X = []; y = []; lab = []
for run in ("V", "B"):
    ev = load(RUNS[run])
    reqs = {e["data"]["call_id"]: e["data"]["request"] for s, e in ev if e["event_type"] == "model_request"}
    for s, e in ev:
        if e["event_type"] != "model_response": continue
        d = e["data"]; r = reqs.get(d["call_id"]); 
        if r is None or is_decision(r): continue
        m = d["response"]["choices"][0]["message"]; u = d["response"]["usage"]
        txt = (m.get("content") or "") + "".join(tc["function"]["arguments"] for tc in m.get("tool_calls") or [])
        rc = m.get("reasoning_content") or ""
        if rc: continue
        X.append(feats(txt)); y.append(u["completion_tokens"]); lab.append((d.get("purpose"), len(txt)))
X = np.array(X, float); y = np.array(y, float)
print("n", len(y), "kinds", {k: sum(1 for l in lab if l[0] == k) for k in set(l[0] for l in lab)})
tot = X.sum(1)
print("overall completion tok/char %.3f" % (y.sum() / tot.sum()))
cons = [i for i, l in enumerate(lab) if l[0] is None]
for name, idx in (("no-purpose(consolidation/reflection)", cons), ("plan_day", [i for i, l in enumerate(lab) if l[0] == "plan_day"])):
    if idx:
        print(name, "tok/char %.3f" % (y[idx].sum() / tot[idx].sum()), "class mix", np.round(X[idx].sum(0) / X[idx].sum(), 3), "mean chars %.0f" % tot[idx].mean())
# predicted by input class model
coef = json.load(open(sys.path[0] + "/tokfit.json"))["coef"]
pred = X[:, :5] @ np.array([coef[k] for k in ("cjk", "cjkp", "alnum", "asciip", "other")])
print("input class model predicts %.0f vs actual %.0f output tokens" % (pred.sum(), y.sum()))
# plan_day retries
for run in ("V", "B"):
    ev = load(RUNS[run])
    resp = {e["data"]["call_id"]: e["data"] for s, e in ev if e["event_type"] == "model_response"}
    for s, e in ev:
        if e["event_type"] == "model_request" and e["data"].get("purpose") == "plan_day":
            u = resp[e["data"]["call_id"]]["response"]["usage"]
            print(run, e["data"]["call_id"].split(":")[-3:], "msgs", len(e["data"]["request"]["messages"]), "prompt", u["prompt_tokens"], "hit", u["prompt_cache_hit_tokens"], "out", u["completion_tokens"])
