"""Part 2: views written per day per category against the 今天/日/月/年 caps (V, B)."""
import json, collections
from common import *
from model import Run

ENTRY_CAP, DAY_CAP, MONTH_CAP, YEAR_CAP = 120, 200, 800, 2000

def main(run):
    R = Run(run)
    rows = R.view_rows
    out = {"run": run}
    # 关系看法另计（选项 B 下它跟随所在活动的类别）
    rel_cat = collections.Counter((v["about"], v["cat"]) for v in rows if v["type"] == "relationship")
    out["relationship_view_categories"] = {f"{a}|{c}": n for (a, c), n in rel_cat.items()}
    cell = collections.defaultdict(lambda: {"n": 0, "text": 0, "rendered": 0, "objects": set()})
    for v in rows:
        c = cell[(v["day"], v["cat"])]
        c["n"] += 1; c["text"] += len(v["text"])
        c["rendered"] += len(f"看法·{v['about']}：{v['text']}")
        c["objects"].add(norm(v["about"]))
    table = {}
    for (d, c), x in sorted(cell.items()):
        table[f"{d}|{c}"] = {"n": x["n"], "text_chars": x["text"], "rendered_chars": x["rendered"],
                             "objects": len(x["objects"]), "x_day_cap": round(x["rendered"] / DAY_CAP, 2)}
    out["per_day_category"] = table
    for c in ("饮食", "工作", "生活"):
        xs = [v["rendered_chars"] for k, v in table.items() if k.endswith(c)]
        ns = [v["n"] for k, v in table.items() if k.endswith(c)]
        days = len({v["day"] for v in rows})
        xs_all = xs + [0] * (days - len(xs))
        out[f"cat_{c}"] = {"rendered_per_day": stats(xs_all), "views_per_day": stats(ns + [0] * (days - len(ns))),
                           "days_over_day_cap": sum(1 for x in xs_all if x > DAY_CAP)}
    # 单条看法长度
    out["view_text_len"] = stats([len(v["text"]) for v in rows])
    out["view_rendered_len"] = stats([len(f"看法·{v['about']}：{v['text']}") for v in rows])
    out["views_over_entry_cap_alone"] = sum(1 for v in rows if len(f"看法·{v['about']}：{v['text']}") > ENTRY_CAP)
    # 每次收尾决策：总结、看法、记忆（非 routine）、正文
    per = []
    for d in R.decisions:
        for a in d["actions"]:
            if a["data"]["tool_name"] != "conclude_activity" or R.concluded_ok(a) is None:
                continue
            items = a["data"]["request"]["items"]
            summ = sum(len(f"{i['activity_name']}：{i['summary']}") for i in items)
            views = sum(len(f"看法·{v['about']}：{v['text']}") for i in items for v in i.get("views", []))
            mems = sum(len("记住：" + m["text"]) for i in items for m in i.get("memories", []) if m["type"] != "routine")
            body = len(a["data"].get("self_note") or "")
            per.append({"items": len(items), "summary": summ, "views": views, "mems": mems, "body": body,
                        "n_views": sum(len(i.get("views", [])) for i in items)})
    withv = [p for p in per if p["n_views"]]
    out["conclude_decisions"] = len(per)
    out["conclude_with_views"] = len(withv)
    out["conclude_views_chars"] = stats([p["views"] for p in withv])
    out["conclude_summary_chars"] = stats([p["summary"] for p in per])
    # 120 字条目：核验按每项 59 字（F16 平均）估计
    VER = 59
    out["entry_budget_left_for_views_after_verified_and_summary"] = stats(
        [max(0, ENTRY_CAP - VER * p["items"] - p["summary"]) for p in withv])
    out["entries_where_views_fit_after_verified"] = sum(1 for p in withv if VER * p["items"] + p["views"] <= ENTRY_CAP)
    out["entries_where_all_selfreport_fits"] = sum(
        1 for p in withv if VER * p["items"] + p["summary"] + p["views"] + p["mems"] <= ENTRY_CAP)
    out["mean_view_chars_per_view_decision"] = round(sum(p["views"] for p in withv) / max(1, len(withv)), 1)
    # 复现：一个对象被写过几天
    days_by_obj = collections.defaultdict(set); cat_by_obj = {}
    for v in rows:
        k = (v["type"], norm(v["about"])); days_by_obj[k].add(v["day"]); cat_by_obj[k] = v["cat"]
    rec = collections.Counter(len(s) for s in days_by_obj.values())
    out["objects_by_days_written"] = dict(sorted(rec.items()))
    out["recurring_objects"] = sorted([(cat_by_obj[k], k[1], len(s)) for k, s in days_by_obj.items() if len(s) >= 2],
                                      key=lambda x: -x[2])
    # 饮食日回顾里覆盖守卫必须写出的名字（当天收到的食品 + 做饭用的食材包，按下单日近似）
    names_by_day = collections.defaultdict(set)
    for o in R.orders.values():
        g = R.goods.get(o["goods_id"], {})
        if R.food_goods(o["goods_id"]):
            names_by_day[day(o["created_at"])].add(g.get("name", o["goods_id"]))
    out["food_coverage_name_chars_per_day"] = {d: sum(len(n) for n in s) for d, s in sorted(names_by_day.items())}
    return out, R

if __name__ == "__main__":
    res = {}
    for run in ("V", "B"):
        o, R = main(run)
        res[run] = o
    json.dump(res, open("part2_flow.json", "w"), ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1)[:9000])
