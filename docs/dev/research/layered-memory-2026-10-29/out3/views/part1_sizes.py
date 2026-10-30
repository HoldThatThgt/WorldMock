"""Part 1: what the persona layer carries besides the card, and its size per decision (V and B)."""
import json, sys, collections, copy
from common import *
from model import Run

def persona_from_request(d):
    msgs = d["requests"][0]["data"]["request"]["messages"]
    arr = json.loads(msgs[1]["content"])
    return next(l for l in arr if l["layer"] == "人设层")

def render(base, pv):
    p = copy.deepcopy(base)
    latest = {i["person"]: i["view"] for i in pv["relationships"]}
    rels = [dict(r) for r in p["relationships"]]
    listed = {r["person"] for r in rels}
    rels += [{"person": x, "role": None, "closeness": None, "channels": []} for x in latest if x not in listed]
    p["relationships"] = [{**{k: v for k, v in r.items() if k != "view"}, "view": latest.get(r["person"])} for r in rels]
    p["preferences"] = [{"subject": i["subject"], "preference": i["preference"], "noted_on": i["noted_on"]} for i in pv["preferences"]]
    return p

def main(run):
    R = Run(run)
    first = next(d for d in R.decisions if d["fresh"])
    base = persona_from_request(first)
    base_card = render(base, {"relationships": [], "preferences": []})
    card_chars = len(compact(base_card))
    mism = 0; checked = 0
    rows = []
    for d in R.decisions:
        p = render(base, d["pv"])
        if d["fresh"]:
            checked += 1
            if compact(p) != compact(persona_from_request(d)):
                mism += 1
        total = len(compact(p))
        prefs = d["pv"]["preferences"]; rels = d["pv"]["relationships"]
        pref_chars = len(compact(prefs))
        rel_view_chars = sum(len(compact(r["view"])) - 4 for r in rels)  # 'null' -> "text"
        text_chars = sum(len(x["preference"]) + len(x["subject"]) for x in prefs) + sum(len(r["view"]) for r in rels)
        rows.append({"day": d["day"], "fresh": d["fresh"], "total": total, "dyn": total - card_chars,
                     "pref_json": pref_chars, "rel_view": rel_view_chars, "n_pref": len(prefs), "n_rel": len(rels),
                     "dyn_hanzi": hanzi(compact(prefs)) + sum(hanzi(r["view"]) for r in rels), "text": text_chars})
    out = {"run": run, "decisions": len(rows), "fresh_checked": checked, "fresh_mismatch": mism,
           "card_only_persona_chars": card_chars,
           "persona_total": stats([r["total"] for r in rows]),
           "leaves_prompt_chars": stats([r["dyn"] for r in rows]),
           "leaves_prompt_hanzi": stats([r["dyn_hanzi"] for r in rows]),
           "preferences_json": stats([r["pref_json"] for r in rows]),
           "relationship_view_chars": stats([r["rel_view"] for r in rows]),
           "n_preferences": stats([r["n_pref"] for r in rows]),
           "share_of_persona_layer": stats([round(r["dyn"] / r["total"], 3) for r in rows]),
           "by_day": {}}
    for dd in sorted({r["day"] for r in rows}):
        xs = [r for r in rows if r["day"] == dd]
        out["by_day"][dd] = {"decisions": len(xs), "dyn_median": pct([r["dyn"] for r in xs], .5),
                             "dyn_max": max(r["dyn"] for r in xs), "n_pref_max": max(r["n_pref"] for r in xs),
                             "n_rel_max": max(r["n_rel"] for r in xs)}
    # nonzero only
    nz = [r["dyn"] for r in rows if r["dyn"] > 0]
    out["leaves_prompt_nonzero"] = stats(nz)
    # final state detail
    last = R.decisions[-1]["pv"]
    subj = [p["subject"] for p in last["preferences"]]
    out["final_subjects"] = subj
    out["final_relationships"] = [(r["person"], len(r["view"])) for r in last["relationships"]]
    # how many successful views overwritten by same subject (last wins) and dated
    allv = R.view_rows
    out["views_written_total"] = len(allv)
    out["views_by_type"] = dict(collections.Counter(v["type"] for v in allv))
    out["distinct_subjects"] = len({(v["type"], v["about"]) for v in allv})
    out["distinct_subjects_normalized"] = len({(v["type"], norm(v["about"])) for v in allv})
    # importance / keywords not rendered
    return out

if __name__ == "__main__":
    res = {r: main(r) for r in ("V", "B")}
    json.dump(res, open("part1_sizes.json", "w"), ensure_ascii=False, indent=1)
    for r, o in res.items():
        print(r, json.dumps({k: v for k, v in o.items() if k not in ("final_subjects",)}, ensure_ascii=False))
