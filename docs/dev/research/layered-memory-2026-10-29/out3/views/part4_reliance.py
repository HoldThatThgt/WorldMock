"""Part 4: how much V's (and B's) behaviour leaned on persona-layer views.

For each decision: the then-present views come from that decision's observation (persona_views part).
Decision text = reasoning_content + content (正文) + tool-call arguments of every turn of the decision.
"""
import json, re, collections, sys
from common import *
from model import Run
from part1_sizes import persona_from_request

def lcs_len(a, b):
    best = 0
    for i in range(len(a)):
        j = i + best + 1
        while j <= len(a) and a[i:j] in b:
            best = j - i; j += 1
    return best

def strip_views(text, pv):
    """去掉请求里人设层看法本身（看法正文和 subject 键），剩下的是看法以外的提示。"""
    for p in pv["preferences"]:
        text = text.replace(compact(p["preference"])[1:-1], "").replace(p["preference"], "")
        text = text.replace(f'"subject":"{p["subject"]}"', "").replace(f'\\"subject\\":\\"{p["subject"]}\\"', "")
    for r in pv["relationships"]:
        text = text.replace(r["view"], "")
    return text

def non_view_args(d):
    """决策里模型写的文字，去掉本次 conclude 新写的看法正文（它们本来就点名对象）。"""
    out = []
    for r in d["responses"]:
        resp = r["data"].get("response") or {}
        for ch in resp.get("choices", []):
            m = ch.get("message", {})
            out.append(m.get("reasoning_content") or ""); out.append(m.get("content") or "")
            for tc in m.get("tool_calls") or []:
                try:
                    a = json.loads(tc["function"]["arguments"])
                except Exception:
                    out.append(tc["function"]["arguments"]); continue
                if isinstance(a, dict) and isinstance(a.get("items"), list):
                    for it in a["items"]:
                        if isinstance(it, dict):
                            it = dict(it); it.pop("views", None)
                            out.append(compact(it))
                    a = {k: v for k, v in a.items() if k != "items"}
                out.append(compact(a))
    return "\n".join(out)

def main(run):
    R = Run(run)
    first = next(d for d in R.decisions if d["fresh"])
    base = persona_from_request(first)
    card_text = norm(compact({k: v for k, v in base.items() if k not in ("preferences",)}))
    res = collections.Counter(); examples = collections.defaultdict(list)
    echo_rows = []
    subj_hits = collections.Counter()
    for d in R.decisions:
        pv = d["pv"]
        if not pv["preferences"] and not pv["relationships"]:
            continue
        res["decisions_with_views_present"] += 1
        T = non_view_args(d); nT = norm(T)
        req = R.request_text(d)
        outside = norm(strip_views(req, pv))
        broad = card_free = view_only = False
        for p in pv["preferences"]:
            s = norm(p["subject"])
            if len(s) < 2 or s not in nT: continue
            broad = True
            if s in card_text: continue
            card_free = True
            if s not in outside:
                view_only = True; subj_hits[p["subject"]] += 1
                examples["view_only"].append((hm(d["t"]), p["subject"]))
        res["mention_any_subject"] += broad
        res["mention_noncard_subject"] += card_free
        res["mention_subject_absent_elsewhere"] += view_only
        # 措辞回声：≥8 字连续子串出自当时的看法正文，且不在请求的其他部分出现
        best = (0, None)
        texts = [(p["subject"], p["preference"], p["noted_on"]) for p in pv["preferences"]] + \
                [(r["person"], r["view"], None) for r in pv["relationships"]]
        for subj, vt, noted in texts:
            k = lcs_len(vt, T)
            if k >= 8:
                # 取最长公共子串并确认它不在请求的其他部分
                for i in range(len(vt) - k + 1):
                    sub = vt[i:i + k]
                    if sub in T:
                        break
                if sub not in strip_views(req, pv) and k > best[0]:
                    best = (k, (subj, sub, noted))
        if best[1]:
            res["echo_ge8"] += 1
            subj, sub, noted = best[1]
            age = None
            if noted:
                age = (int(d["day"][3:]) - int(noted[8:]))
            echo_rows.append({"t": hm(d["t"]), "subject": subj, "echo": sub, "noted_on": noted, "age_days": age})
    res["decisions"] = len(R.decisions)
    out = {"run": run, "counts": dict(res), "view_only_subjects": subj_hits.most_common(),
           "echo_examples": echo_rows[:40], "echo_by_age": dict(collections.Counter(e["age_days"] for e in echo_rows))}
    out["food"] = food(R)
    return out

POS = ("想", "愿意", "喜欢", "合适", "舒服", "踏实", "对味", "开胃", "顶饱", "正好", "省心", "很香", "满足", "还会", "再点",
       "合口味", "值得", "稳", "顺", "清爽", "暖")
NEG = ("不想", "腻", "太咸", "太油", "下次换", "不再", "失望", "一般", "不值", "少点", "别再", "难吃", "偏贵", "太贵")

def polarity(t):
    n = sum(1 for w in NEG if w in t)
    p = sum(1 for w in POS if w in t) - sum(1 for w in NEG if w.startswith("不") and w[1:] in POS)
    return "neg" if n and n >= p else ("pos" if p > 0 else "neutral")

def food(R):
    """每笔食品购买：当时人设层有没有指向这件商品的看法，看法的倾向，是否回购。"""
    dec_by_action = {}
    for d in R.decisions:
        for a in d["actions"]:
            dec_by_action[a["action_id"]] = d
    rows = []; bought = collections.Counter()
    for o in sorted(R.orders.values(), key=lambda x: x["created_at"]):
        if not R.food_goods(o["goods_id"]): continue
        name = R.goods.get(o["goods_id"], {}).get("name", o["goods_id"])
        d = dec_by_action.get(o["action_id"])
        pv = d["pv"] if d else {"preferences": []}
        match = []
        nn = norm(name)
        for p in pv["preferences"]:
            s = norm(p["subject"])
            if len(s) >= 2 and (s in nn or nn in s or lcs_len(s, nn) >= 4):
                match.append((p["subject"], p["noted_on"], polarity(p["preference"]), p["preference"]))
        rows.append({"t": hm(o["created_at"]), "goods": name, "category": R.goods.get(o["goods_id"], {}).get("category"),
                     "repeat": bought[o["goods_id"]] > 0, "views": match})
        bought[o["goods_id"]] += 1
    summ = collections.Counter()
    for r in rows:
        summ["purchases"] += 1
        summ["repeat"] += r["repeat"]
        if r["views"]:
            summ["with_view"] += 1
            pol = {v[2] for v in r["views"]}
            summ["with_pos_view" if "pos" in pol else ("with_neg_view" if "neg" in pol else "with_neutral_view")] += 1
            summ["repeat_with_view"] += r["repeat"]
    return {"summary": dict(summ), "rows": rows}

if __name__ == "__main__":
    res = {run: main(run) for run in ("V", "B")}
    json.dump(res, open("part4_reliance.json", "w"), ensure_ascii=False, indent=1)
    for run, o in res.items():
        print(run, o["counts"], o["view_only_subjects"][:15], o["echo_by_age"])
        print(" food", o["food"]["summary"])
        for r in o["food"]["rows"]:
            print("  ", r["t"], r["goods"], r["category"], "repeat" if r["repeat"] else "", [(v[0], v[1], v[2]) for v in r["views"]])
