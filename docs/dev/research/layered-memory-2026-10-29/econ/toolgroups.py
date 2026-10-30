"""Read-only: per-group change rate of 工具层 groups between consecutive fresh requests."""
import json, sqlite3, sys
from collections import Counter
path = sys.argv[1]
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
rows = con.execute("select event_seq, body from events where json_extract(body,'$.event_type')='model_request' order by event_seq")
def L(x): return len(json.dumps(x, ensure_ascii=False, separators=(",", ":")))
prev = None; changed = Counter(); sizes = Counter(); seen = Counter(); n = 0
for seq, body in rows:
    ev = json.loads(body); msgs = ev["data"]["request"]["messages"]
    if not msgs[0]["content"].startswith('{"layer":"系统层"') or len(msgs) != 2: continue
    tl = next(l for l in json.loads(msgs[1]["content"]) if l["layer"] == "工具层")
    n += 1
    cur = {}
    for g in tl["groups"]:
        key = (g.get("kind"), g.get("action"))
        cur[key] = g; sizes[key] += L(g); seen[key] += 1
    if prev is not None:
        for k in cur:
            if k in prev and cur[k] != prev[k]: changed[k] += 1
    prev = cur
for k, v in sizes.most_common(): print(f"{str(k):40s} mean {v/seen[k]:7.0f} present {seen[k]} changed {changed[k]}")
