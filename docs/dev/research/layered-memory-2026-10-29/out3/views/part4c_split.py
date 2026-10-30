"""Part 4c: where the reliance shows (reasoning vs written args; start vs conclude), and a stricter ≥10-char echo."""
import json, collections
from common import *
from model import Run
from part1_sizes import persona_from_request
from part4_reliance import lcs_len
from part4b_reliance import outside

def texts(d):
    rs, args = [], []
    for r in d["responses"]:
        m = ((r["data"].get("response") or {}).get("choices") or [{}])[0].get("message", {})
        rs.append((m.get("reasoning_content") or "") + "\n" + (m.get("content") or ""))
        for tc in m.get("tool_calls") or []:
            try:
                a = json.loads(tc["function"]["arguments"])
            except Exception:
                continue
            if isinstance(a, dict) and isinstance(a.get("items"), list):
                a = dict(a); a["items"] = [{k: v for k, v in it.items() if k != "views"} for it in a["items"] if isinstance(it, dict)]
            args.append(compact(a))
    return "\n".join(rs), "\n".join(args)

for run in ("V", "B"):
    R = Run(run)
    base = persona_from_request(next(d for d in R.decisions if d["fresh"]))
    card = norm(compact({k: v for k, v in base.items() if k != "preferences"}))
    c = collections.Counter()
    for d in R.decisions:
        pv = d["pv"]
        views = [(p["subject"], p["preference"], p["noted_on"]) for p in pv["preferences"]] + \
                [(r["person"], r["view"], None) for r in pv["relationships"]]
        if not views: continue
        out = outside(d); nout = norm(out)
        rs, args = texts(d)
        po = [v for v in views if v[1] not in out]
        kind = "start" if any(a["data"]["tool_name"].startswith("start") for a in d["actions"]) else \
            ("conclude" if any(a["data"]["tool_name"] == "conclude_activity" for a in d["actions"]) else "other")
        hit_r = hit_a = hit10 = False
        for v in po:
            s = norm(v[0])
            ment = len(s) >= 2 and s not in card and s not in nout
            for T, flag in ((rs, "r"), (args, "a")):
                k = lcs_len(v[1], T)
                ok = k >= 8 and all(v[1][i:i + k] not in out for i in range(len(v[1]) - k + 1) if v[1][i:i + k] in T)
                m = ment and s in norm(T)
                if ok or m:
                    if flag == "r": hit_r = True
                    else: hit_a = True
                if k >= 10 and ok: hit10 = True
        if hit_r or hit_a:
            c[f"{kind}"] += 1
            c["in_reasoning"] += hit_r; c["in_args"] += hit_a; c["echo_ge10"] += hit10
        c[f"all_{kind}"] += 1
    print(run, dict(c))
