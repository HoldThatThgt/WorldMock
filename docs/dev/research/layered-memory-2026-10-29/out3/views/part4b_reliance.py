"""Part 4 (strict): reliance on persona-layer views, counting only what was visible nowhere else in the request.

outside(d) = every message of the decision's first request except the 人设层 object (fresh: layer array minus 人设层;
continuation: chain head minus 人设层, plus all later assistant/tool messages incl. tool-call arguments).
A view is persona-only for d when its text does not occur in outside(d)."""
import json, re, collections
from common import *
from model import Run
from part1_sizes import persona_from_request
from part4_reliance import lcs_len, non_view_args

POS = ("想", "愿意", "喜欢", "合适", "舒服", "踏实", "对味", "开胃", "顶饱", "正好", "省心", "满足", "还会", "再点",
       "合口味", "值得", "清爽", "好吃", "定心", "提神")
NEG = ("不想", "腻", "太咸", "太油", "下次换", "不再", "失望", "不值", "别再", "难吃", "太贵")
def polarity(t):
    n = sum(1 for w in NEG if w in t)
    p = sum(1 for w in POS if w in t and not ("不" + w) in t)
    return "neg" if n > p else ("pos" if p else "neutral")

def outside(d):
    msgs = d["requests"][0]["data"]["request"]["messages"]
    parts_ = [msgs[0].get("content") or ""]
    arr = json.loads(msgs[1]["content"])
    parts_.append(compact([l for l in arr if l["layer"] != "人设层"]))
    for m in msgs[2:]:
        parts_.append(m.get("content") or "")
        parts_.append(m.get("reasoning_content") or "")
        for tc in m.get("tool_calls") or []:
            parts_.append(tc["function"]["arguments"])
    return "\n".join(parts_)

def main(run):
    R = Run(run)
    base = persona_from_request(next(d for d in R.decisions if d["fresh"]))
    card = norm(compact({k: v for k, v in base.items() if k != "preferences"}))
    c = collections.Counter(); rows = []; subj = collections.Counter(); ages = collections.Counter()
    for d in R.decisions:
        pv = d["pv"]
        views = [(p["subject"], p["preference"], p["noted_on"]) for p in pv["preferences"]] + \
                [(r["person"], r["view"], None) for r in pv["relationships"]]
        if not views: continue
        c["with_views"] += 1
        out = outside(d); nout = norm(out)
        T = non_view_args(d); nT = norm(T)
        po = [v for v in views if v[1] not in out]
        c["persona_only_views_total"] += len(po)
        mention = [v for v in po if len(norm(v[0])) >= 2 and norm(v[0]) in nT and norm(v[0]) not in card
                   and norm(v[0]) not in nout]
        echo = []
        for v in po:
            k = lcs_len(v[1], T)
            if k >= 8:
                sub = next(v[1][i:i + k] for i in range(len(v[1]) - k + 1) if v[1][i:i + k] in T)
                if sub not in out:
                    echo.append((v, sub))
        if mention: c["subject_mention_view_only"] += 1
        if echo: c["wording_echo_ge8"] += 1
        if mention or echo:
            c["either"] += 1
            used = {x[0] for x in mention} | {x[0][0] for x in echo}
            for s in used: subj[s] += 1
            ag = []
            for v in mention + [e[0] for e in echo]:
                if v[2]:
                    ag.append(int(d["day"][3:]) - int(v[2][8:]))
            a = max(ag) if ag else None
            ages["none" if a is None else ("0" if a == 0 else ("1-3" if a <= 3 else ">=4"))] += 1
            rows.append({"t": hm(d["t"]), "fresh": d["fresh"], "tools": [x["data"]["tool_name"] for x in d["actions"]],
                         "mention": [x[0] for x in mention], "echo": [(e[0][0], e[1]) for e in echo], "max_age": a})
    c["decisions"] = len(R.decisions)
    # 食品购买
    dec_by_action = {a["action_id"]: d for d in R.decisions for a in d["actions"]}
    food = []; bought = collections.Counter(); fs = collections.Counter()
    for o in sorted(R.orders.values(), key=lambda x: x["created_at"]):
        if not R.food_goods(o["goods_id"]): continue
        name = R.goods.get(o["goods_id"], {}).get("name", o["goods_id"]); nn = norm(name)
        d = dec_by_action.get(o["action_id"])
        m = [(p["subject"], p["noted_on"], polarity(p["preference"])) for p in d["pv"]["preferences"]
             if len(norm(p["subject"])) >= 2 and (norm(p["subject"]) in nn or nn in norm(p["subject"]) or
                                                  lcs_len(norm(p["subject"]), nn) >= 4)] if d else []
        in_card = lcs_len(nn, card) >= min(4, len(nn))
        rep = bought[o["goods_id"]] > 0; bought[o["goods_id"]] += 1
        fs["purchases"] += 1; fs["repeat"] += rep; fs["card_named"] += in_card
        if m:
            pol = {x[2] for x in m}
            fs["with_view"] += 1
            fs["with_pos_view"] += "pos" in pol; fs["with_neg_view"] += "neg" in pol
            fs["repeat_with_view"] += rep
            if not in_card: fs["with_view_not_card"] += 1; fs["repeat_with_view_not_card"] += rep
        food.append({"t": hm(o["created_at"]), "goods": name, "repeat": rep, "card_named": in_card, "views": m})
    return {"counts": dict(c), "age_buckets": dict(ages), "subjects": subj.most_common(25), "rows": rows,
            "food_summary": dict(fs), "food": food}

if __name__ == "__main__":
    res = {r: main(r) for r in ("V", "B")}
    json.dump(res, open("part4b_reliance.json", "w"), ensure_ascii=False, indent=1)
    for r, o in res.items():
        print(r, o["counts"], o["age_buckets"]); print("  subjects", o["subjects"][:20]); print("  food", o["food_summary"])
        for x in o["rows"][:12]: print("   ", x)
