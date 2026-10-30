import sqlite3, json, collections
V="/Users/atishoo/Project/WorldMock-Dataset/execution/20261029T023604Z-white-collar-food-variety/run/footprints.sqlite"
db=sqlite3.connect(f"file:{V}?mode=ro", uri=True)
c=collections.Counter(); shown=0
for seq,body in db.execute("select event_seq,body from events order by event_seq"):
    b=json.loads(body)
    if b["event_type"]=="model_request":
        d=b["data"]; c[(d.get("purpose"), tuple(sorted(d.keys()))[:8])]+=1
        if d.get("purpose") is None and shown<1:
            shown+=1
            print(json.dumps({k:(v if k!="request" else list(v.keys())) for k,v in d.items()},ensure_ascii=False)[:800])
            msgs=d["request"]["messages"]; print(len(msgs), [m["role"] for m in msgs])
            arr=json.loads(msgs[1]["content"]); print([l["layer"] for l in arr])
            p=[l for l in arr if l["layer"]=="人设层"][0]; print(json.dumps(p,ensure_ascii=False)[:1500])
    if b["event_type"]=="model_response" and shown==1:
        d=b["data"]; m=d["response"]["choices"][0]["message"]; print("RESP keys", list(m.keys())); shown+=1
        print(json.dumps(m,ensure_ascii=False)[:1200])
for k,v in c.items(): print(v,k)
