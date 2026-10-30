"""Read-only: which 工具层 top-level groups change between consecutive fresh requests, and their sizes."""
import json, sqlite3, sys
from collections import Counter
path = sys.argv[1]
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
rows = con.execute("select event_seq, body from events where json_extract(body,'$.event_type')='model_request' order by event_seq")
def L(x): return len(json.dumps(x, ensure_ascii=False, separators=(",", ":")))
prev = None; changed = Counter(); sizes = Counter(); n = 0; first = None
for seq, body in rows:
    ev = json.loads(body); msgs = ev["data"]["request"]["messages"]
    if not msgs[0]["content"].startswith('{"layer":"系统层"') or len(msgs) != 2: continue
    tl = next(l for l in json.loads(msgs[1]["content"]) if l["layer"] == "工具层")
    if first is None: first = tl
    n += 1
    for k, v in tl.items(): sizes[k] += L(v)
    if prev is not None:
        for k in set(tl) | set(prev):
            if tl.get(k) != prev.get(k): changed[k] += 1
    prev = tl
print("n", n)
for k, v in sizes.most_common(): print(f"{k:24s} mean {v/n:8.0f}  changed {changed[k]}/{n-1}")
print(json.dumps(first, ensure_ascii=False)[:2500])
