import sqlite3, json, collections, sys
V="/Users/atishoo/Project/WorldMock-Dataset/execution/20261029T023604Z-white-collar-food-variety/run/footprints.sqlite"
db=sqlite3.connect(f"file:{V}?mode=ro", uri=True)
print([r for r in db.execute("select name, sql from sqlite_master where type='table'")][:5])
c=collections.Counter(); ex={}
for seq,body in db.execute("select event_seq,body from events order by event_seq"):
    b=json.loads(body); t=b["event_type"]; c[t]+=1
    if t not in ex: ex[t]=b
print(c)
for t,b in ex.items():
    s=json.dumps(b,ensure_ascii=False)
    print("==",t, s[:900])
