"""Builds the per-run model used by every part: decisions, persona views per decision, views written, categories."""
import json, collections
from common import *

def catalog(run):
    base = RUNS[run] + "/input/catalog/"
    goods = {}; contents = {}; apps = {}
    for l in open(base + "merchant-catalog.jsonl"):
        r = json.loads(l); goods[r["goods_id"]] = r
    for l in open(base + "content-catalog.jsonl"):
        r = json.loads(l); contents[r["content_id"]] = r
    for l in open(base + "app-catalog.jsonl"):
        r = json.loads(l); apps[r["app_id"]] = r
    return goods, contents, apps

def export_rows(run, name):
    return [json.loads(l) for l in open(RUNS[run] + f"/export/{name}.jsonl")]

class Run:
    def __init__(self, run):
        self.run = run
        self.events, self.parts, self.db = load(run)
        self.goods, self.contents, self.apps = catalog(run)
        self.acts = {a["activity_id"]: a for a in export_rows(run, "activities")}
        self.orders = {}
        for p in export_rows(run, "purchases"):
            if p.get("fact_kind") == "purchase":
                self.orders[p["order_id"]] = p
        self.byid = {e["event_id"]: e for e in self.events}
        self._decisions()
        self._views()

    def _decisions(self):
        """每个决策：观察、首个请求、全部响应、世界请求。"""
        dec = collections.OrderedDict()
        for e in self.events:
            t = e["event_type"]
            if t == "decision_started":
                dec[e["decision_id"]] = {"id": e["decision_id"], "t": e["occurred_at"], "requests": [], "responses": [],
                                         "actions": [], "obs": None}
        reqs = {}
        for e in self.events:
            t = e["event_type"]; did = e.get("decision_id")
            if t == "observation" and did in dec:
                dec[did]["obs"] = e
            elif t == "model_request" and e["data"].get("purpose") is None:
                cid = e["data"]["call_id"]
                d = cid.split(":model:")[0]
                if d in dec: dec[d]["requests"].append(e)
            elif t == "model_response" and e["data"].get("purpose") is None:
                cid = e["data"]["call_id"]; d = cid.split(":model:")[0]
                if d in dec: dec[d]["responses"].append(e)
            elif t == "action_requested" and did in dec:
                dec[did]["actions"].append(e); reqs[e["event_id"]] = e
        res = {}
        for e in self.events:
            if e["event_type"] in ("action_result", "action_rejected", "action_accepted"):
                for cb in e["caused_by"]:
                    if cb in reqs: res.setdefault(cb, []).append(e)
        self.results = res
        self.decisions = list(dec.values())
        for d in self.decisions:
            o = d["obs"]["data"]
            d["pv"] = part(self.parts, o["persona_views"])
            d["duty_day"] = o["environment_layer"]["duty"]["day"]
            d["day"] = day(d["t"])
            first = d["requests"][0] if d["requests"] else None
            d["fresh"] = first is not None and len(first["data"]["request"]["messages"]) == 2
        self.workday = {}
        for d in self.decisions:
            self.workday.setdefault(d["day"], d["duty_day"] == "工作日")

    def concluded_ok(self, req):
        for r in self.results.get(req["event_id"], []):
            if r["event_type"] == "action_result" and r["data"].get("status") == "completed":
                return r
        return None

    def _views(self):
        """全部成功收尾写下的看法，带类别。"""
        rows = []
        for d in self.decisions:
            for a in d["actions"]:
                if a["data"]["tool_name"] != "conclude_activity":
                    continue
                r = self.concluded_ok(a)
                if r is None:
                    continue
                ids = r["data"]["data"]["activity_ids"]
                items = a["data"]["request"]["items"]
                for i, it in enumerate(items):
                    act = self.acts.get(ids[i]) if i < len(ids) else None
                    cat = self.category(act)
                    for v in it.get("views", []):
                        rows.append({"decision": d["id"], "t": a["occurred_at"], "day": day(a["occurred_at"]),
                                     "type": v["type"], "about": v["about"], "text": v["text"],
                                     "activity": it["activity_name"], "act": act, "cat": cat,
                                     "summary": it["summary"]})
        self.view_rows = rows

    def target(self, act):
        """活动的目标身份：商品、内容、app、目的地。"""
        if act is None: return None
        k = act["activity_type"]; dd = act["data"]
        if k == "purchase":
            return tuple(sorted(("goods", self.orders[o]["goods_id"]) for o in dd.get("order_ids", []) if o in self.orders))
        if k == "interact":
            return (("content", dd.get("content_id")), ("app", dd.get("app_id")))
        if k == "travel":
            return (("dest", act.get("destination_id")),)
        return ((k, None),)

    def food_goods(self, gid):
        g = self.goods.get(gid, {})
        return g.get("category") in ("meal", "snack", "drink") or "食材包" in g.get("name", "")

    def category(self, act):
        """近似提案第二章的类别规则 0–7（office-eat-lunch 视为已加覆盖）。"""
        if act is None: return "生活"
        k = act["activity_type"]; dd = act["data"]; dt = day(act["started_at"]) if act.get("started_at") else None
        work = self.workday.get(dt, False)
        if k == "sleep": return "生活"
        if k == "purchase":
            gids = [self.orders[o]["goods_id"] for o in dd.get("order_ids", []) if o in self.orders]
            return "饮食" if any(self.food_goods(g) for g in gids) else "生活"
        if k == "query":
            cats = [t.get("category") for t in dd.get("targets", [])]
            names = [self.goods.get(t.get("goods_id"), {}).get("name", "") for t in dd.get("targets", [])]
            if any(c in ("meal", "snack", "drink") for c in cats) or any(self.food_goods(t.get("goods_id")) for t in dd.get("targets", []) if t.get("goods_id")):
                return "饮食"
            return "生活"
        if k == "travel":
            ends = {act.get("location_id"), act.get("destination_id"), dd.get("origin_id")}
            return "工作" if work and "wc-office" in ends and "wc-home" in ends else "生活"
        if k == "interact":
            cid = dd.get("content_id"); c = self.contents.get(cid, {})
            if cid == "office-eat-lunch": return "饮食"
            if (c.get("completion_effects") or {}).get("food") or c.get("consumes_state") in ("groceries", "team_dinner"):
                return "饮食"
            if cid and cid.startswith("home-dinner"): return "饮食"
            if dd.get("app_id"):
                return "工作" if (c.get("prerequisite") or {}).get("location_kinds") == ["研发中心"] else "生活"
            if work and act.get("location_id") == "wc-office": return "工作"
            return "生活"
        return "生活"

    def decision_text(self, d, include_args=True):
        """决策里模型写的文字：思考、正文和工具参数。"""
        parts_ = []
        for r in d["responses"]:
            resp = r["data"].get("response") or {}
            for ch in resp.get("choices", []):
                m = ch.get("message", {})
                parts_.append(m.get("reasoning_content") or "")
                parts_.append(m.get("content") or "")
                if include_args:
                    for tc in m.get("tool_calls") or []:
                        parts_.append(tc["function"]["arguments"])
        return "\n".join(parts_)

    def request_text(self, d):
        """首个请求的全部消息文字（含续接链）。"""
        if not d["requests"]: return ""
        msgs = d["requests"][0]["data"]["request"]["messages"]
        return "\n".join(m.get("content") or "" if isinstance(m.get("content"), str) else compact(m.get("content")) for m in msgs)
