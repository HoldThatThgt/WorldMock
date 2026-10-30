"""Do 看法 items survive the 120字 per-entry cap? Two item orders compared (read-only, uses windows_*.json)."""
import json, collections
LINE = 59
for name in ("V", "B"):
    rows = json.load(open(f"windows_{name}.json"))
    res = collections.Counter()
    for r in rows:
        views = [t for t in r["texts"] if t.startswith("看法·")]
        if not views:
            continue
        res["entries_with_views"] += 1; res["views"] += len(views)
        verified = r["nfacts"] * LINE
        for order in ("summary_first", "views_first"):
            items = r["texts"] if order == "summary_first" else views + [t for t in r["texts"] if t not in views]
            room = 120 - verified; whole = 0; started = 0
            for t in items:
                if room <= 0: break
                if t in views:
                    started += 1; whole += len(t) <= room
                room -= len(t)
            res[f"{order}_views_started"] += started; res[f"{order}_views_whole"] += whole
    print(name, dict(res))
