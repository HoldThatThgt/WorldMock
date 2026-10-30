"""Read-only: food orders per local day (time, goods name, category) from action_result orders."""
import json, sqlite3, sys
from datetime import datetime, timezone, timedelta
run = sys.argv[1]
tz = timezone(timedelta(hours=8))
goods = {}
import glob, os
cat = os.path.join(os.path.dirname(run.rstrip('/')), 'input', 'catalog', 'merchant-catalog.jsonl')
for line in open(cat):
    r = json.loads(line)
    for g in r.get("goods", []) if isinstance(r.get("goods"), list) else [r]:
        if "goods_id" in g: goods[g["goods_id"]] = (g.get("name"), g.get("category"))
con = sqlite3.connect(f"file:{run}/footprints.sqlite?mode=ro", uri=True)
seen = set()
for (body,) in con.execute("select body from events where json_extract(body,'$.event_type')='action_result' order by event_seq"):
    ev = json.loads(body); orders = ((ev["data"].get("data") or {}).get("orders")) or []
    for o in orders:
        if o["order_id"] in seen: continue
        seen.add(o["order_id"])
        name, c = goods.get(o["goods_id"], (o["goods_id"], "?"))
        t = datetime.fromtimestamp(o.get("created_at", ev["occurred_at"]), tz)
        print(t.strftime("%m-%d %a %H:%M"), c, name)
