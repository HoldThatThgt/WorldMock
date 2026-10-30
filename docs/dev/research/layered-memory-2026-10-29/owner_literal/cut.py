import sqlite3, json, datetime, zoneinfo, statistics as st
z=zoneinfo.ZoneInfo("Asia/Shanghai")
base="/Users/atishoo/Project/WorldMock-Dataset/execution/20261029T023604Z-white-collar-food-variety/run"
db=sqlite3.connect(f"file:{base}/footprints.sqlite?mode=ro",uri=True)
before=[];after=[];pers=[];mem=[];total=[];affected=0;n=0
for (b,) in db.execute("select body from events where json_extract(body,'$.event_type')='model_request' and json_extract(body,'$.data.call_id') like '%:model:0001:attempt:1' order by event_seq"):
    e=json.loads(b); msgs=e["data"]["request"]["messages"]
    try:
        if json.loads(msgs[0]["content"]).get("layer")!="系统层": continue
    except Exception: continue
    lay=json.loads(msgs[1]["content"]); prog=lay[5]
    now=datetime.datetime.fromtimestamp(e["occurred_at"],z)
    midnight=now.replace(hour=0,minute=0,second=0,microsecond=0)
    full=len(json.dumps(prog,ensure_ascii=False,separators=(",",":")))
    keep=[ev for ev in prog["events"] if datetime.datetime.fromisoformat(ev["occurred_at"])>=midnight]
    cut=dict(prog,events=keep)
    c=len(json.dumps(cut,ensure_ascii=False,separators=(",",":")))
    n+=1; before.append(full); after.append(c); affected+= c<full
    p=dict(lay[1]); p.pop("preferences",None)
    pers.append(len(json.dumps(p,ensure_ascii=False,separators=(",",":"))))
    total.append(len(msgs[1]["content"]))
def q(a,p): a=sorted(a); return a[int(p*(len(a)-1))]
print("n",n,"affected",affected)
print("progress before median/p90/max",st.median(before),q(before,.9),max(before))
print("progress after  median/p90/max",st.median(after),q(after,.9),max(after))
print("sum before",sum(before),"after",sum(after))
print("persona without preferences median/max",st.median(pers),max(pers))
print("user total median",st.median(total))
