"""Per-day query-answer volume as rendered in 进度层 tool_call records (V, read-only), deduplicated."""
import sys, json, collections
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from common import *
from tok import feats
COEF = json.load(open(sys.path[0] + "/tokfit.json"))["coef"]
def ctok(t): return sum(COEF[k] * v for k, v in zip(("cjk", "cjkp", "alnum", "asciip", "other"), feats(t)))
ev = load(RUNS[sys.argv[1] if len(sys.argv) > 1 else "V"])
seen = {}
for s, e in ev:
    if e["event_type"] != "model_request" or not is_decision(e["data"]["request"]) or len(e["data"]["request"]["messages"]) != 2: continue
    for l in json.loads(e["data"]["request"]["messages"][1]["content"]):
        if l["layer"] != "进度层": continue
        for rec in l["events"]:
            det = ((rec.get("result") or {}).get("details") or {})
            if rec.get("type") == "tool_call" and "query" in det:
                key = (rec["occurred_at"], ser(rec.get("request") or rec.get("call") or "")[:200])
                q = ser(det["query"])
                if len(q) > len(seen.get(key, "")): seen[key] = q
per = collections.defaultdict(list)
for (t, _), q in seen.items(): per[t[:10]].append(q)
for d in sorted(per):
    v = per[d]; print(d, "queries", len(v), "chars", sum(map(len, v)), "max", max(map(len, v)), "tok %.0f" % ctok("".join(v)))
