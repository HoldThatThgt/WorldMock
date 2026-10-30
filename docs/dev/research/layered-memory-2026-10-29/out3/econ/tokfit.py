"""Fit prompt_tokens = T0 + sum_k r_k * chars_k + m * (#messages beyond the first two) on V and B decision requests."""
import sys, json
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
import numpy as np
from common import *
from tok import feats, stream_parts, CLASSES
rows = []; ys = []; meta = []
for run in ("V", "B"):
    ev = load(RUNS[run])
    resp = {e["data"]["call_id"]: e["data"] for s, e in ev if e["event_type"] == "model_response"}
    for s, e in ev:
        if e["event_type"] != "model_request" or not is_decision(e["data"]["request"]): continue
        r = e["data"]["request"]; u = resp[e["data"]["call_id"]]["response"]["usage"]
        parts = stream_parts(r)
        f = np.array(feats("".join(parts[2:])), float)
        rows.append(list(f) + [len(r["messages"]) - 2, 1.0]); ys.append(u["prompt_tokens"]); meta.append((run, len(r["messages"])))
X = np.array(rows); y = np.array(ys, float)
coef, *_ = np.linalg.lstsq(X, y, rcond=None)
pred = X @ coef; err = pred - y
print("coef", dict(zip(list(CLASSES) + ["per_msg", "T0"], np.round(coef, 4))))
print("abs err mean %.1f  p90 %.1f max %.1f  rel mean %.4f" % (np.mean(abs(err)), np.percentile(abs(err), 90), np.max(abs(err)), np.mean(abs(err) / y)))
# overall single ratio on user content for fresh only
fr = [i for i, m in enumerate(meta) if m[1] == 2]
chars = X[fr, :5].sum(1); yy = y[fr]
A = np.vstack([chars, np.ones(len(fr))]).T; c2, *_ = np.linalg.lstsq(A, yy, rcond=None)
print("fresh single-ratio fit: tokens = %.1f + %.4f*chars ; abs err mean %.1f" % (c2[1], c2[0], np.mean(abs(A @ c2 - yy))))
# implied overall tokens/char of whole user content (excluding T0)
print("user chars mean %.0f, implied tok/char (class model) %.4f" % (chars.mean(), ((X[fr, :5] @ coef[:5]) / chars).mean()))
# class mix of user content
print("class share", dict(zip(CLASSES, np.round(X[fr, :5].sum(0) / X[fr, :5].sum(), 3))))
# system + tools features
s, e = next((s, e) for s, e in load(RUNS["V"]) if e["event_type"] == "model_request" and is_decision(e["data"]["request"]))
p = stream_parts(e["data"]["request"])
for name, t in (("system", p[0]), ("tools", p[1])):
    f = feats(t); print(name, len(t), "chars", f, "class-model tokens %.0f" % (np.array(f) @ coef[:5]))
json.dump({"coef": dict(zip(list(CLASSES) + ["per_msg", "T0"], coef.tolist()))}, open(sys.path[0] + "/tokfit.json", "w"), indent=1)
