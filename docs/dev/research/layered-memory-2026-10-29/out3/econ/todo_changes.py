"""How often 待办层 (+synthetic orders) changes between consecutive fresh requests, by key (V, read-only)."""
import sys, json, collections
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from run_sim import *
data = prep("V")
b = make_builder(data, PROP)
prev = None; ch = collections.Counter(); n = 0; any_ch = 0
for r in data["reqs"]:
    if len(r["msgs"]) != 2: continue
    todo = {l["layer"]: l for l in json.loads(b(r))}["待办层"]
    if prev is not None:
        n += 1; c = [k for k in todo if todo[k] != prev.get(k)]
        any_ch += bool(c)
        for k in c: ch[k] += 1
    prev = todo
print("fresh pairs", n, "待办 changed", any_ch, dict(ch))
