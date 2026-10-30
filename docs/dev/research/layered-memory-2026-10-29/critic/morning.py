import sqlite3, json, datetime, zoneinfo, sys
tz=zoneinfo.ZoneInfo("Asia/Shanghai")
db=sqlite3.connect("file:/Users/atishoo/Project/WorldMock-Dataset/execution/20261029T023604Z-white-collar-food-variety/run/footprints.sqlite?mode=ro", uri=True)
lo,hi=sys.argv[1],sys.argv[2]
def t(e): return datetime.datetime.fromtimestamp(e,tz).strftime("%m-%d %H:%M:%S")
for seq,eid,body in db.execute("select event_seq,event_id,body from events order by event_seq"):
    b=json.loads(body)
    oc=b.get("occurred_at")
    if oc is None: continue
    ts=t(oc)
    if not (lo<=ts<=hi): continue
    et=b["event_type"]
    if et in ("observation","decision_started","model_request","model_response","memory_updated"):
        if et=="decision_started": print(seq,ts,"DECISION")
        continue
    d=b.get("data",{})
    extra=""
    if et=="action_requested":
        r=d.get("request",{})
        extra=json.dumps({"tool":d.get("tool") or r.get("action"), "req":r},ensure_ascii=False)[:200]
    elif et=="action_result":
        extra=json.dumps(d,ensure_ascii=False)[:160]
    elif et=="environment_event":
        extra=d.get("kind","")+" "+json.dumps({k:v for k,v in d.items() if k!="kind"},ensure_ascii=False)[:140]
    else:
        extra=json.dumps(d,ensure_ascii=False)[:120]
    print(seq,ts,et,extra)
