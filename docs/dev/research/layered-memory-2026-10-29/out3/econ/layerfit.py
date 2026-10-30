"""Fit fresh prompt_tokens = T0 + sum_layer r_layer * chars_layer (V+B fresh requests), read-only."""
import sys, json
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
import numpy as np
from common import *
from tok import feats
L = ("工具层", "人设层", "记忆层", "待办层", "环境层", "进度层")
X = []; y = []; mem_texts = []
for run in ("V", "B"):
    ev = load(RUNS[run])
    resp = {e["data"]["call_id"]: e["data"] for s, e in ev if e["event_type"] == "model_response"}
    for s, e in ev:
        if e["event_type"] != "model_request" or not is_decision(e["data"]["request"]): continue
        r = e["data"]["request"]
        if len(r["messages"]) != 2: continue
        by = {l["layer"]: ser(l) for l in json.loads(r["messages"][1]["content"])}
        X.append([len(by[k]) for k in L] + [1.0]); y.append(resp[e["data"]["call_id"]]["response"]["usage"]["prompt_tokens"])
        mem_texts.append(by["记忆层"])
X = np.array(X, float); y = np.array(y, float)
c, *_ = np.linalg.lstsq(X, y, rcond=None)
print("per-layer tok/char", dict(zip(list(L) + ["T0"], np.round(c, 4))))
print("abs err mean %.1f max %.1f" % (np.mean(abs(X @ c - y)), np.max(abs(X @ c - y))))
# bootstrap CI for memory ratio
rng = np.random.default_rng(1); bs = []
for _ in range(300):
    i = rng.integers(0, len(y), len(y)); cc, *_ = np.linalg.lstsq(X[i], y[i], rcond=None); bs.append(cc)
bs = np.array(bs)
for j, k in enumerate(L): print(f"  {k} 95% [{np.percentile(bs[:, j], 2.5):.3f}, {np.percentile(bs[:, j], 97.5):.3f}]")
# class mix of memory layer text
f = np.array([feats(t) for t in mem_texts]).sum(0); print("记忆层 class mix", np.round(f / f.sum(), 3))
