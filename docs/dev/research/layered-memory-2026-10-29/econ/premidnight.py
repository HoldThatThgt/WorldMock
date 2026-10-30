"""Read-only: chars of closed 进度层 records from before the request's local midnight (fresh requests)."""
import json, sqlite3, sys
from datetime import datetime, timezone, timedelta
tz = timezone(timedelta(hours=8))
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
def L(x): return len(json.dumps(x, ensure_ascii=False, separators=(",", ":")))
FINAL = {"completed", "cancelled", "rejected"}
n = hit = saved = tot = 0
for seq, body in con.execute("select event_seq, body from events where json_extract(body,'$.event_type')='model_request' order by event_seq"):
    ev = json.loads(body); msgs = ev["data"]["request"]["messages"]
    if not msgs[0]["content"].startswith('{"layer":"系统层"') or len(msgs) != 2: continue
    prog = next(l for l in json.loads(msgs[1]["content"]) if l["layer"] == "进度层")
    day = datetime.fromtimestamp(ev["occurred_at"], tz).strftime("%Y-%m-%d")
    n += 1; tot += L(prog)
    s = 0
    for e in prog["events"]:
        if e["occurred_at"][:10] < day:
            r = e.get("result") or {}
            if e["type"] != "tool_call" or (r.get("status") in FINAL and (r.get("occurred_at") or "")[:10] < day):
                s += L(e)
    if s: hit += 1; saved += s
print("fresh", n, "requests with pre-midnight closed records", hit, "chars saved total", saved, "share of all prog chars", saved / tot, "mean per affected", saved / max(1, hit))
