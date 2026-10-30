import sqlite3, json, datetime, zoneinfo
tz=zoneinfo.ZoneInfo("Asia/Shanghai")
db=sqlite3.connect("file:/Users/atishoo/Project/WorldMock-Dataset/execution/20261029T023604Z-white-collar-food-variety/run/footprints.sqlite?mode=ro", uri=True)
cut=datetime.datetime(2026,10,12,0,0,tzinfo=tz).timestamp()
food_acts=("吃午饭","做饭","购买")
views={}
for seq,body in db.execute("select event_seq,body from events order by event_seq"):
    b=json.loads(body)
    if b["event_type"]!="action_requested" or b["occurred_at"]>=cut: continue
    r=b["data"].get("request",{})
    for it in r.get("items",[]) if isinstance(r,dict) else []:
        for v in it.get("views",[]) or []:
            if v["type"]!="preference": continue
            d=datetime.datetime.fromtimestamp(b["occurred_at"],tz).strftime("%Y-%m-%d")
            a=it.get("activity_name","")
            # food: purchase of food/kit/drink, 做饭, 吃午饭
            food = a.startswith("购买") or "做饭" in a or "吃午饭" in a
            if food: views[v["about"]]=(d,v["text"])
items=[{"对象":s,"看法":t,"记于":d} for s,(d,t) in views.items()]
items.sort(key=lambda x:x["记于"],reverse=True)
def c(x): return len(json.dumps(x,ensure_ascii=False,separators=(",",":")))
print("food views before dedupe:",[ (x["对象"],x["记于"]) for x in items])
# naive dedupe by containment
import re
def norm(s): return re.sub(r"[的了\s，。、·《》（）]","",s)
kept=[]
for x in items:
    if any(norm(x["对象"]) in norm(k["对象"]) or norm(k["对象"]) in norm(x["对象"]) for k in kept): continue
    kept.append(x)
print("after dedupe:",[(x["对象"],x["记于"],len(x["看法"])) for x in kept])
acc=[];
for x in kept:
    if c(acc+[x])>600: break
    acc.append(x)
print("fits 600:",[(x["对象"],x["记于"]) for x in acc], c(acc))
