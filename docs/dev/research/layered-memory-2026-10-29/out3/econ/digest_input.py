"""Per-day volume of the day-digest input (V, read-only): self-report text by tool, reply bodies, query answers, 核验 lines."""
import sys, json, re, collections
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from common import *
from tok import feats
COEF = json.load(open(sys.path[0] + "/tokfit.json"))["coef"]
def ctok(t): return sum(COEF[k] * v for k, v in zip(("cjk", "cjkp", "alnum", "asciip", "other"), feats(t)))
run = sys.argv[1] if len(sys.argv) > 1 else "V"
ev = load(RUNS[run])
last = {}
for s, e in ev:
    if e["event_type"] == "model_response" and e.get("decision_id"):
        last[e["decision_id"]] = (e["occurred_at"], e["data"]["response"]["choices"][0]["message"])
per = collections.defaultdict(lambda: collections.Counter()); txt = collections.defaultdict(list)
def strings(x):
    if isinstance(x, dict):
        for k, v in x.items(): yield from strings(v)
    elif isinstance(x, list):
        for v in x: yield from strings(v)
    elif isinstance(x, str) and re.search(r"[一-鿿]", x): yield x
for did, (t, m) in last.items():
    d = day(t)
    for tc in m.get("tool_calls") or []:
        name = tc["function"]["name"]
        try: a = json.loads(tc["function"]["arguments"])
        except Exception: continue
        if name == "conclude_activity":
            # whole conclude args: summaries, memories (incl. routine), views
            ss = list(strings(a)); per[d]["conclude"] += sum(map(len, ss)); txt[d] += ss
        elif name in ("report_status", "pause_activity"):
            ss = list(strings(a)); per[d][name] += sum(map(len, ss)); txt[d] += ss
    if m.get("content"):
        per[d]["reply_body"] += len(m["content"]); txt[d].append(m["content"])
# query answers
for s, e in ev:
    if e["event_type"] == "action_result":
        det = ((e["data"].get("result") or e["data"]).get("details") or {}) if isinstance(e["data"], dict) else {}
        q = det.get("query") if isinstance(det, dict) else None
        if q:
            sq = ser(q); per[day(e["occurred_at"])]["query_answers"] += len(sq); txt[day(e["occurred_at"])].append(sq)
# 核验 lines from the next plan_day payload (动作结果) -> covers that day
for s, e in ev:
    if e["event_type"] == "model_request" and e["data"].get("purpose") == "plan_day" and e["data"]["call_id"].endswith("attempt:1"):
        p = json.loads(e["data"]["request"]["messages"][1]["content"])
        rng = p.get("回顾时段", "")
        mm = re.match(r"(\d+)月(\d+)日", rng)
        if mm and p.get("动作结果"):
            d = f"{int(mm.group(1)):02d}-{int(mm.group(2)):02d}"
            per[d]["settled(动作结果)"] += sum(map(len, p["动作结果"])); per[d]["settled_lines"] += len(p["动作结果"])
            txt[d] += p["动作结果"]
for d in sorted(per):
    c = per[d]; tot = sum(v for k, v in c.items() if k != "settled_lines")
    print(d, dict(c), "total chars", tot, "class-model tokens %.0f" % ctok("".join(txt[d])))
