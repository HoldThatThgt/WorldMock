import json, collections
from common import *
ev, parts, db = load("V")
seen=set()
for e in ev:
    if e["event_type"]=="action_accepted" and "activity" in e["data"]:
        a=e["data"]["activity"]; key=(a["kind"], a["data"].get("action"))
        if key in seen: continue
        seen.add(key)
        print(key, json.dumps({k:a[k] for k in a if k not in ("agent_id",)},ensure_ascii=False)[:900])
        print("  REQ", )
