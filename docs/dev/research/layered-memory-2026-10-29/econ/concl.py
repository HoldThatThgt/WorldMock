"""Read-only: conclude/pause text volume per local day, split summary / memories / views / self_note."""
import json, sqlite3, sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
path = sys.argv[1]
tz = timezone(timedelta(hours=8))
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
rows = con.execute("select body from events where json_extract(body,'$.event_type')='action_requested' order by event_seq")
def bigr(s): return {s[i:i+2] for i in range(len(s)-1)}
def jac(a, b):
    A, B = bigr(a), bigr(b); return len(A & B) / max(1, len(A | B))
day = defaultdict(lambda: defaultdict(int))
for (body,) in rows:
    ev = json.loads(body); d = ev["data"]; t = ev["occurred_at"]
    dd = datetime.fromtimestamp(t, tz).strftime("%m-%d")
    tool = d.get("tool_name"); req = d.get("request") or {}
    if d.get("self_note"): day[dd]["self_note"] += len(d["self_note"])
    if tool not in ("conclude_activity", "pause_activity", "report_status"): continue
    for it in req.get("items", []):
        s = it.get("summary") or ""
        day[dd][tool + ".summary"] += len(s)
        for m in it.get("memories", []) or []:
            day[dd]["memories"] += len(m["text"])
            if jac(m["text"], s) < 0.35: day[dd]["memories_kept"] += len(m["text"])
        for v in it.get("views", []) or []:
            day[dd]["views"] += len(v["text"]) + len(v.get("about", ""))
for dd in sorted(day):
    print(dd, dict(day[dd]))
