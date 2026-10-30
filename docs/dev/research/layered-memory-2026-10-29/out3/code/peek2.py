import sqlite3, json, sys, collections
run = sys.argv[1]
con = sqlite3.connect(f"file:{run}/run/footprints.sqlite?mode=ro", uri=True)
rows = con.execute("select event_seq, body from events order by event_seq").fetchall()
kinds = collections.Counter()
seen=set()
for seq, body in rows:
    b = json.loads(body)
    t = b["event_type"]
    if t=="environment_event":
        k=b["data"].get("kind"); kinds[(k,b["visibility"])]+=1
    if t in ("action_result","action_rejected","action_accepted") and t not in seen:
        seen.add(t); print(t, json.dumps(b["data"],ensure_ascii=False)[:600])
    if t=="model_request" and "mr" not in seen:
        seen.add("mr"); d=b["data"]; print("model_request keys", list(d.keys()), list(d["request"].keys()), len(d["request"]["messages"]))
print(kinds)
