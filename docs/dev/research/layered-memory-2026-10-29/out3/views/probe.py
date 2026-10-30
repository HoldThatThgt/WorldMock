import json, collections
from common import *
ev, parts, db = load("V")
obs=[e for e in ev if e["event_type"]=="observation"]
print(len(obs), obs[5].get("decision_id"))
pv=part(parts, obs[-1]["data"]["persona_views"])
print(json.dumps(pv,ensure_ascii=False)[:600])
print(len(pv["preferences"]), len(pv["relationships"]))
# conclude request structure
for e in ev:
    if e["event_type"]=="action_requested" and e["data"]["tool_name"]=="conclude_activity":
        print(json.dumps(e["data"],ensure_ascii=False)[:1500]); break
for e in ev:
    if e["event_type"]=="action_result" and e["caused_by"] and e["data"].get("data",{}).get("action")=="conclude_activity":
        print(json.dumps(e["data"],ensure_ascii=False)[:800]); break
