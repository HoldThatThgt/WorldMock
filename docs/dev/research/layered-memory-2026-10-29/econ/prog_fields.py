"""Read-only: break down 进度层 chars by record field across decision requests."""
import json, sqlite3, sys, statistics
from collections import Counter, defaultdict
path = sys.argv[1]
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
rows = con.execute("select event_seq, body from events where json_extract(body,'$.event_type')='model_request' order by event_seq")
def L(x): return len(json.dumps(x, ensure_ascii=False, separators=(",", ":")))
field_tot = Counter(); total = 0; n = 0
res_tot = Counter()
biggest = None
for seq, body in rows:
    ev = json.loads(body)
    msgs = ev["data"]["request"]["messages"]
    if not msgs[0]["content"].startswith('{"layer":"系统层"') or len(msgs) != 2:
        continue
    layers = json.loads(msgs[1]["content"])
    prog = next(l for l in layers if l["layer"] == "进度层")
    n += 1
    s = L(prog); total += s
    if biggest is None or s > biggest[0]: biggest = (s, seq, prog)
    for e in prog["events"]:
        if e["type"] != "tool_call":
            field_tot["other:" + e["type"]] += L(e); continue
        for k, v in e.items():
            field_tot["tc." + k] += L(v) + len(k) + 3
        r = e.get("result") or {}
        if isinstance(r, dict):
            for k, v in r.items():
                res_tot["result." + k] += L(v)
                if isinstance(v, dict):
                    for k2, v2 in v.items():
                        res_tot[f"result.{k}.{k2}"] += L(v2)
print("fresh requests", n, "mean prog chars", total / n)
for k, v in field_tot.most_common(20): print(f"{k:40s} {v/total:6.1%}")
print('--- result breakdown')
for k, v in res_tot.most_common(25): print(f"{k:40s} {v/total:6.1%}")
s, seq, prog = biggest
print("biggest", s, seq, len(prog["events"]))
ex = [e for e in prog["events"] if e["type"] == "tool_call"]
for e in ex[:3] + [x for x in ex if x.get("tool") == "start_side_activity"][:2]:
    print(json.dumps(e, ensure_ascii=False)[:1800]); print()
