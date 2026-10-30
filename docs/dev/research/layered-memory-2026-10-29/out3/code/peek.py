import sqlite3, json, sys, collections
run = sys.argv[1]
con = sqlite3.connect(f"file:{run}/run/footprints.sqlite?mode=ro", uri=True)
rows = con.execute("select event_seq, body from events").fetchall()
types = collections.Counter()
ex = {}
for seq, body in rows:
    b = json.loads(body)
    t = b["event_type"]; types[t]+=1
    if t == "action_requested":
        tool = b["data"]["tool_name"]
        ex.setdefault(tool, (seq, b))
print(types)
for tool,(seq,b) in ex.items():
    print(tool, seq, json.dumps(b["data"], ensure_ascii=False)[:700])
    print(sorted(b.keys()))
