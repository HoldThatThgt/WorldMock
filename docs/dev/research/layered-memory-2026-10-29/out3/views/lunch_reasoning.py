"""Print the reasoning around V's lunch orders to see whether the persona-layer view was cited."""
import json, re
from common import *
from model import Run
R = Run("V")
want = {"10-09 11:4", "10-12 11:4", "10-12 11:5", "10-09 11:3", "10-10 11:", "10-08 11:"}
for d in R.decisions:
    t = hm(d["t"])
    if not any(t.startswith(w) for w in want): continue
    txt = ""
    for r in d["responses"]:
        m = (r["data"].get("response") or {}).get("choices", [{}])[0].get("message", {})
        txt += (m.get("reasoning_content") or "")
    hits = [m.start() for m in re.finditer("刀削面|换口味|换着|不同的菜|上次|昨天|前几天|noted_on|偏好|看法", txt)]
    if not hits: continue
    print("==", t, d["fresh"], [a["data"]["tool_name"] for a in d["actions"]])
    shown = set()
    for h in hits[:6]:
        s = max(0, h - 80)
        if any(abs(s - x) < 80 for x in shown): continue
        shown.add(s); print("   …" + txt[s:h + 100].replace("\n", " ") + "…")
