"""Read-only: count byte-identical duplicate records within a 进度层, and estimate slimming savings."""
import json, sqlite3, sys
from collections import Counter
path = sys.argv[1]
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
rows = con.execute("select event_seq, body from events where json_extract(body,'$.event_type')='model_request' order by event_seq")
def L(x): return len(json.dumps(x, ensure_ascii=False, separators=(",", ":")))
n = dup_req = dup_chars = tot = 0
save_contents = save_query = save_args_mem = save_sum_dupe = 0
for seq, body in rows:
    ev = json.loads(body); msgs = ev["data"]["request"]["messages"]
    if not msgs[0]["content"].startswith('{"layer":"系统层"') or len(msgs) != 2: continue
    prog = next(l for l in json.loads(msgs[1]["content"]) if l["layer"] == "进度层")
    n += 1; tot += L(prog)
    seen = Counter(json.dumps(e, ensure_ascii=False, sort_keys=True) for e in prog["events"])
    d = [(k, c) for k, c in seen.items() if c > 1]
    if d:
        dup_req += 1; dup_chars += sum(len(k) * (c - 1) for k, c in d)
    for e in prog["events"]:
        if e["type"] != "tool_call": continue
        det = (e.get("result") or {}).get("details") or {}
        if "contents" in det:
            # keep only titles + finished
            slim = [{"title": c.get("title"), "finished": c.get("finished")} for c in det["contents"]]
            save_contents += L(det["contents"]) - L(slim)
        if "query" in det:
            save_query += L(det["query"])
print("fresh requests", n, "mean prog", tot / n)
print("requests with duplicate records", dup_req, "dup chars share", dup_chars / tot)
print("contents summaries share", save_contents / tot)
print("query answers share", save_query / tot)
