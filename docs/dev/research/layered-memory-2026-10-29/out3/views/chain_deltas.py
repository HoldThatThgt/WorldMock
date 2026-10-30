"""How often same-second continuation requests carried a persona-view delta (changes.preferences / relationships)."""
import json, collections
from common import *
from model import Run
for run in ("V", "B"):
    R = Run(run); c = collections.Counter(); chars = []
    for d in R.decisions:
        if d["fresh"] or not d["requests"]: continue
        c["chain_decisions"] += 1
        msgs = d["requests"][0]["data"]["request"]["messages"]
        keys = set(); n = 0
        for m in msgs:
            if m.get("role") == "tool":
                try: p = json.loads(m["content"])
                except Exception: continue
                ch = p.get("changes", {}) if isinstance(p, dict) else {}
                for k in ("preferences", "relationships"):
                    if k in ch: keys.add(k); n += len(compact(ch[k]))
        for k in keys: c[k] += 1
        if keys: c["any_persona_delta"] += 1; chars.append(n)
    print(run, dict(c), stats(chars))
