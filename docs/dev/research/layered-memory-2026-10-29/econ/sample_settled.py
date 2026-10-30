"""Read-only: print settled lines and self_notes of the earlier_today in one late request."""
import json, sqlite3, sys
con = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
best = None
for seq, body in con.execute("select event_seq, body from events where json_extract(body,'$.event_type')='model_request' order by event_seq"):
    ev = json.loads(body); msgs = ev["data"]["request"]["messages"]
    if not msgs[0]["content"].startswith('{"layer":"系统层"') or len(msgs) != 2: continue
    layers = json.loads(msgs[1]["content"])
    mem = next(l for l in layers if l["layer"] == "记忆层")
    if sum(len(e["settled"]) for e in mem["earlier_today"]) > 12:
        best = mem; break
for e in best["earlier_today"]:
    print("ENTRY", e["recorded_at"])
    for s in e["settled"]: print("  S", s)
    for n in e["self_notes"][:6]: print("  N", n)
p = next(l for l in layers if l["layer"] == "人设层")
print(json.dumps({k: v for k, v in p.items() if k not in ("preferences", "relationships")}, ensure_ascii=False)[:1500])
print(len(json.dumps({k: v for k, v in p.items() if k not in ("preferences", "relationships")}, ensure_ascii=False, separators=(",", ":"))))
print(json.dumps(p.get("preferences")[:3], ensure_ascii=False))
print(json.dumps(next(l for l in layers if l["layer"] == "待办层"), ensure_ascii=False)[:800])
