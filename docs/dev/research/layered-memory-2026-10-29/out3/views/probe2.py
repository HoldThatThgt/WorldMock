import json, collections
from common import *
ev, parts, db = load("V")
byid={e["event_id"]:e for e in ev}
req={e["event_id"]:e for e in ev if e["event_type"]=="action_requested"}
c=collections.Counter()
for e in ev:
    if e["event_type"] in ("action_result","action_accepted","action_rejected"):
        for cb in e["caused_by"]:
            if cb in req: c[(req[cb]["data"]["tool_name"], e["event_type"])]+=1
for k,v in sorted(c.items()): print(k,v)
n=0
for e in ev:
    if e["event_type"]=="action_result":
        r=[req[cb] for cb in e["caused_by"] if cb in req]
        if r and r[0]["data"]["tool_name"]=="conclude_activity":
            print(json.dumps(e["data"],ensure_ascii=False)[:1200]); n+=1
            if n>0: break
