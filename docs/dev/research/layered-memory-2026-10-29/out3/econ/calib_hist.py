"""Check history-class residuals: does the cache also hold the generated assistant turn?"""
import sys
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from run_sim import *
data = prep("V")
ident = lambda r: r["msgs"][1]["content"]
rows = simulate(data, ident, 128, 5620)
hist = [(x, r) for x, r in zip(rows, data["reqs"]) if x["cls"] == "history"]
import statistics as st
d = [x["actual_hit"] - x["hit"] for x, r in hist]
print("history residual actual-sim: mean %.0f median %.0f min %d max %d" % (st.mean(d), st.median(d), min(d), max(d)))
# compare with tokens of the replayed assistant messages
for x, r in hist[:12]:
    m = r["msgs"]; a = [mm for mm in m[2:] if mm["role"] == "assistant"]
    at = sum(ctok((mm.get("reasoning_content") or "") + (mm.get("content") or "") + "".join(tc["function"]["name"] + tc["function"]["arguments"] for tc in mm.get("tool_calls") or [])) for mm in a)
    print(x["actual_hit"] - x["hit"], "assistant tok %.0f" % at, "prompt", x["prompt"], "act", x["actual_hit"], "sim", x["hit"])
