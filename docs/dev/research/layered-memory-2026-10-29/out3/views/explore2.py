import sqlite3, json, collections
V="/Users/atishoo/Project/WorldMock-Dataset/execution/20261029T023604Z-white-collar-food-variety/run/footprints.sqlite"
db=sqlite3.connect(f"file:{V}?mode=ro", uri=True)
n=0
for seq,body in db.execute("select event_seq,body from events order by event_seq"):
    b=json.loads(body)
    if b["event_type"]=="observation":
        n+=1
        if n in (1,200):
            d=b["data"]
            print(sorted(d.keys()))
            for k,v in d.items():
                s=json.dumps(v,ensure_ascii=False)
                print(k, len(s), s[:200])
    if b["event_type"]=="model_request" and n==200:
        r=b["data"]; print("MR", r["purpose"], r["call_id"], len(r["request"]["messages"]))
        break
print(db.execute("select count(*) from parts").fetchone())
row=db.execute("select sha256, body from parts limit 3").fetchall()
for r in row: print(r[0], r[1][:300])
