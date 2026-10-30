import sqlite3, json, datetime, zoneinfo
tz=zoneinfo.ZoneInfo("Asia/Shanghai")
db=sqlite3.connect("file:/Users/atishoo/Project/WorldMock-Dataset/execution/20261029T023604Z-white-collar-food-variety/run/footprints.sqlite?mode=ro", uri=True)
cut=datetime.datetime(2026,10,12,0,0,tzinfo=tz).timestamp()
views={}
for seq,body in db.execute("select event_seq,body from events order by event_seq"):
    b=json.loads(body)
    if b["event_type"]!="action_requested": continue
    if b["occurred_at"]>=cut: continue
    r=b["data"].get("request",{})
    for it in r.get("items",[]) if isinstance(r,dict) else []:
        for v in it.get("views",[]) or []:
            d=datetime.datetime.fromtimestamp(b["occurred_at"],tz).strftime("%m-%d")
            views[(v.get("type"),v.get("about"))]=(d,it.get("activity_name"),v.get("text"))
print(len(views))
for (t,s),(d,a,x) in sorted(views.items(), key=lambda kv: kv[1][0], reverse=True):
    print(d,t,s,"|",a,"|",len(x))
