"""Rebuild the 长期 block at 10-12 00:00 from run V with the revised rule:
keyed by normalized subject within a category (containment or bigram Jaccard>=0.5 merge, newest text wins),
rank by distinct days written desc, then newest; fill each category budget in rank order."""
import sqlite3, json, datetime, zoneinfo, re, unicodedata, sys
tz=zoneinfo.ZoneInfo("Asia/Shanghai")
db=sqlite3.connect("file:/Users/atishoo/Project/WorldMock-Dataset/execution/20261029T023604Z-white-collar-food-variety/run/footprints.sqlite?mode=ro", uri=True)
cut=datetime.datetime(2026,10,12,0,0,tzinfo=tz).timestamp()
BUDGET={"饮食":800,"工作":700,"生活":1000,"关系":500}
WORK=("功能开发与自测","处理消息与收尾","需求评审会议","代码评审与缺陷修复","会议分析与工作总结","钉钉")
def category(kind, act, ts):
    if kind=="relationship": return "关系"
    if act.startswith("购买") or "做饭" in act or "吃午饭" in act: return "饮食"
    if any(w in act for w in WORK): return "工作"
    wd=datetime.datetime.fromtimestamp(ts,tz).date()
    workday = wd.isoformat() in ("2026-10-08","2026-10-09","2026-10-10","2026-10-12")
    if act.startswith("驾车") and workday: return "工作"
    return "生活"
def norm(s):
    s=unicodedata.normalize("NFKC",s)
    return re.sub(r"[\s的了，。、·《》（）\(\)\-—…!！?？:：;；\"“”'‘’]","",s)
def bigrams(s): return {s[i:i+2] for i in range(len(s)-1)}
def same(a,b):
    a,b=norm(a),norm(b)
    short=min(a,b,key=len)
    if len(short)>=2 and (a in b or b in a): return True
    A,B=bigrams(a),bigrams(b)
    return bool(A|B) and len(A&B)/len(A|B)>=0.5
groups={c:[] for c in BUDGET}
for seq,body in db.execute("select event_seq,body from events order by event_seq"):
    b=json.loads(body)
    if b["event_type"]!="action_requested" or b["occurred_at"]>=cut: continue
    r=b["data"].get("request",{})
    for it in r.get("items",[]) if isinstance(r,dict) else []:
        for v in it.get("views",[]) or []:
            c=category(v["type"],it.get("activity_name",""),b["occurred_at"])
            d=datetime.datetime.fromtimestamp(b["occurred_at"],tz).date().isoformat()
            hit=next((g for g in groups[c] if same(g["about"],v["about"])),None)
            if hit is None:
                groups[c].append({"about":v["about"],"text":v["text"],"date":d,"days":{d}})
            else:
                hit.update(about=v["about"],text=v["text"],date=d); hit["days"].add(d)
def size(x): return len(json.dumps(x,ensure_ascii=False,separators=(",",":")))
out={}; dropped={}
for c,items in groups.items():
    items.sort(key=lambda g:(len(g["days"]),g["date"]),reverse=True)
    key="人" if c=="关系" else "对象"
    acc=[]
    for g in items:
        row={key:g["about"],"看法":g["text"][:100]+("…" if len(g["text"])>100 else ""),"记于":g["date"]}
        if len(g["days"])>1: row["记过天数"]=len(g["days"])
        if size(acc+[row])>BUDGET[c]: dropped.setdefault(c,[]).append((g["about"],g["date"],len(g["days"]))); continue
        acc.append(row)
    out[c]=acc
for c in out: print(c, size(out[c]), [(r.get("对象") or r.get("人"), r["记于"], r.get("记过天数",1)) for r in out[c]])
print("dropped:", dropped)
print("total", size(out))
json.dump(out, open("critic/longterm_1012.json","w"), ensure_ascii=False, separators=(",",":"))
