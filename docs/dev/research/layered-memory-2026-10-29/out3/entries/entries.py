"""Read-only simulation: one 今天 entry per decision (owner ruling, 2026-10-29) on a WorldMock run.

Usage: python3 entries.py <run-dir> <tag> <out-dir>
Opens <run-dir>/run/footprints.sqlite read-only (mode=ro) and the run's input catalog; writes only to <out-dir>.

Window of decision k = events with seq in (C_{k-1}, C_k], C_k = last event of decision k's commit
(last event at the decision's own second before the next decision's observation). Events in the window that
happened before local midnight of the decision go to the previous day's closing entry (digest-only).
"""
import bisect
import collections
import datetime
import json
import re
import sqlite3
import statistics
import sys
import tomllib
import zoneinfo

RUN, TAG, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
Z = zoneinfo.ZoneInfo("Asia/Shanghai")
CAP = 120
MIN_CUT = 8  # an item is cut (with …) only if at least this many characters of room remain
FOOD = {"meal", "snack", "drink"}
CAT_ZH = {"meal": "食品", "snack": "零食", "drink": "饮料", "goods": "商品"}
START_SUFFIX = re.compile(r"（\d{1,2}月\d{1,2}日 \d{2}:\d{2} 起）")
WORKDAY_REASON = "work_schedule weekly + exceptions"

# ---------------------------------------------------------------- catalog / persona
cat_dir = f"{RUN}/input/catalog"
goods, contents, apps, locs = {}, {}, {}, {}
for line in open(f"{cat_dir}/merchant-catalog.jsonl"):
    r = json.loads(line)
    if "goods_id" in r:
        goods[r["goods_id"]] = r
for line in open(f"{cat_dir}/content-catalog.jsonl"):
    r = json.loads(line)
    contents[r["content_id"]] = r
for line in open(f"{cat_dir}/app-catalog.jsonl"):
    r = json.loads(line)
    apps[r["app_id"]] = r["name"]
for line in open(f"{cat_dir}/location.jsonl"):
    r = json.loads(line)
    locs[r["location_id"]] = r
callbacks = [json.loads(line) for line in open(f"{cat_dir}/callbacks.jsonl")]
persona = tomllib.load(open(f"{RUN}/input/persona.toml", "rb"))
HOME = next(k for k, v in locs.items() if v["name"] == persona["home_location"])
WORK = next(k for k, v in locs.items() if v["name"] == persona["work_schedule"]["workplace"])
WORK_KIND = locs[WORK]["kind"]
food_reset = set()
for cb in callbacks:
    if "state" in cb and cb.get("reset_on_goods"):
        food_reset.update(g for g in cb["reset_on_goods"] if g.startswith("kit-"))  # kits reset the groceries state
FOOD_STATES = {c.get("consumes_state") for c in contents.values()
               if (c.get("completion_effects") or {}).get("food") and c.get("consumes_state")}
OVERRIDES = {"office-eat-lunch": "饮食"}  # rule 0: package 0001 override the proposal requires before the pilot


def loc_name(lid):
    """display_name (name + branch in full-width brackets)."""
    r = locs[lid]
    return r["name"] if not r.get("branch_label") else f"{r['name']}（{r['branch_label']}）"


def shift(date):
    """Work arrangement of a local date: (starts, ends, breaks) minutes or None."""
    ws = persona["work_schedule"]
    exc = ws.get("exceptions", {})
    key = date.isoformat()
    if key in exc:
        v = exc[key]
        return None if v == [] else (v["starts"], v["ends"], v.get("breaks", []))
    w = ws.get("weekly", {}).get(str(date.weekday()))
    return None if w is None else (w["starts"], w["ends"], w.get("breaks", []))


def is_food_goods(gid):
    g = goods.get(gid)
    return g is not None and (g.get("category") in FOOD or gid in food_reset)


# ---------------------------------------------------------------- events
con = sqlite3.connect(f"file:{RUN}/run/footprints.sqlite?mode=ro", uri=True)
EV = []
for seq, body in con.execute("select event_seq, body from events order by event_seq"):
    b = json.loads(body)
    b["seq"] = seq
    EV.append(b)
SEQS = [e["seq"] for e in EV]
by_seq = {e["seq"]: e for e in EV}


def lt(epoch):
    return datetime.datetime.fromtimestamp(epoch, Z)


def hm(epoch):
    return lt(epoch).strftime("%H:%M")


def day_of(epoch):
    return lt(epoch).date()


def midnight(epoch):
    d = lt(epoch)
    return int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


# activity snapshots (state) and endings (activity_endings semantics, settled_text.py:156-169)
snaps = collections.defaultdict(list)  # aid -> [(seq, snap)]
endings = {}
ENDED = {"completed", "cancelled", "failed", "truncated"}
for e in EV:
    d = e["data"]
    payload = d.get("data") if isinstance(d.get("data"), dict) else {}
    rows = payload.get("activities", []) if isinstance(payload, dict) else []
    cands = [d.get("activity"), *rows, payload.get("activity") if isinstance(payload, dict) else None]
    for s in cands:
        if isinstance(s, dict) and "activity_id" in s and "kind" in s:
            snaps[s["activity_id"]].append((e["seq"], s))
    if e["event_type"] in ("activity_progress", "environment_event", "action_result"):
        for s in (d.get("activity"), *rows):
            if isinstance(s, dict) and s.get("status") in ENDED and s["activity_id"] not in endings:
                endings[s["activity_id"]] = e["seq"]


def snap_at(aid, seq):
    rows = snaps[aid]
    keys = [r[0] for r in rows]
    i = bisect.bisect_right(keys, seq) - 1
    return rows[i][1] if i >= 0 else rows[0][1]


def first_seq(aid):
    return snaps[aid][0][0]


requests, results, rejected, accepted = {}, collections.defaultdict(list), {}, {}
orders_by_id = {}
for e in EV:
    t, d = e["event_type"], e["data"]
    if t == "action_requested":
        requests[e["action_id"]] = e
    elif t == "action_result":
        results[e["action_id"]].append(e)
        for o in (d.get("data") or {}).get("orders", []) or []:
            orders_by_id[o["order_id"]] = o
    elif t == "action_rejected":
        rejected[e["action_id"]] = e
    elif t == "action_accepted":
        accepted[e["action_id"]] = e
    elif t == "environment_event" and d.get("kind") == "delivery_arrival":
        for o in d.get("orders", []):
            orders_by_id.setdefault(o["order_id"], o)

# decisions
obs = {e["decision_id"]: e["seq"] for e in EV if e["event_type"] == "observation"}
decs = [e for e in EV if e["event_type"] == "decision_started"]
first_req = {}
for e in EV:
    if e["event_type"] == "model_request" and e["data"]["call_id"].endswith(":model:0001:attempt:1"):
        first_req[e["decision_id"]] = len(e["data"]["request"]["messages"])
D = []
for i, e in enumerate(decs):
    did, t = e["decision_id"], e["occurred_at"]
    lo = obs[did]
    hi = obs[decs[i + 1]["decision_id"]] if i + 1 < len(decs) else SEQS[-1] + 1
    a, b_ = bisect.bisect_left(SEQS, lo), bisect.bisect_left(SEQS, hi)
    commit = max(EV[j]["seq"] for j in range(a, b_) if EV[j]["occurred_at"] == t)
    D.append({"did": did, "t": t, "obs": lo, "C": commit, "replay": first_req.get(did, 2) > 2,
              "chain": i > 0 and decs[i - 1]["occurred_at"] == t})
for i, d in enumerate(D):
    d["lo"] = D[i - 1]["C"] if i else 0


# ---------------------------------------------------------------- wording helpers
def cclock(epoch, ref):
    """compact clock relative to the entry's local date."""
    a, b_ = day_of(epoch), day_of(ref)
    if a == b_:
        return hm(epoch)
    if (b_ - a).days == 1:
        return "昨" + hm(epoch)
    return f"{a.month}月{a.day}日{hm(epoch)}"


def oclock(epoch, ref):
    """current clock_phrase (readable.py:45-50)."""
    if day_of(epoch) == day_of(ref):
        return hm(epoch)
    m = lt(epoch)
    return f"{m.month}月{m.day}日 {hm(epoch)}"


def dec(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s


def money(fen):
    return f"{dec(fen / 100)} 元"


def cmoney(fen):
    return f"{dec(fen / 100)}元"


def short_name(name):
    s = START_SUFFIX.sub("", name, count=1)
    return s[2:] if s.startswith("进行") else s


def titles_of(snap):
    return [contents[r["content_id"]]["title"] for r in snap["data"].get("played", [])]


def title_list(ts, limit=3):
    listed = "".join(f"《{x}》" for x in ts[:limit])
    return listed if len(ts) <= limit else f"{listed}等 {len(ts)} 条"


def ending_phrase(s):
    if s["status"] == "failed":
        return "失败"
    if s["kind"] == "travel" and s["status"] == "completed":
        return "到达"
    if s["status"] == "completed" and s.get("boundary_type") == "planned_end":
        over = s["data"].get("oversleep_seconds", 0) if s["kind"] == "sleep" else 0
        return f"比打算的多睡了 {over // 60} 分钟" if over > 0 else "按计划结束"
    if s["status"] == "cancelled" and s["kind"] == "interact" and s["data"].get("app_id") is not None:
        return "结束使用"
    if s["status"] == "truncated":
        return "提前结束"
    return "提前结束"


def partial_title(aid, upto):
    """app_progress semantics (settled_text.py:97-110): last advanced, not completed content of this activity."""
    cur = None
    for e in EV:
        if e["seq"] > upto:
            break
        if e["event_type"] != "activity_progress" or e.get("activity_id") != aid:
            continue
        c = e["data"].get("content")
        if not c or e["data"]["activity"]["kind"] != "interact":
            continue
        if c["completed"]:
            cur = None
        elif c["position_after_seconds"] > c["position_before_seconds"]:
            cur = c["content_id"]
    return cur


partial_cache = {}


def kit_for(aid):
    """groceries kit used by a cooking activity: latest reset_to_cap before the activity's first event."""
    start = first_seq(aid)
    best = None
    for e in EV:
        if e["seq"] >= start:
            break
        d = e["data"]
        if d.get("kind") == "callback_effect" and d.get("state") == "groceries" and d.get("type") == "reset_to_cap":
            best = e
    return best


def activity_sentences(aid, upto, ref):
    """(current form, compact form) of an ended activity."""
    s = snap_at(aid, upto)
    name = s["name"]
    start, end = s["started_at"], s.get("ended_at") or s["last_settled_at"]
    ending = ending_phrase(s)
    cur = f"{name}：{oclock(start, ref)}–{oclock(end, ref)}，{ending}"
    short = short_name(name)
    span = f"{cclock(start, ref)}–{cclock(end, ref)}"
    tail = ""
    if s["kind"] == "interact":
        ts = titles_of(s)
        app = s["data"].get("app_id")
        key = (aid, upto)
        if key not in partial_cache:
            partial_cache[key] = partial_title(aid, upto) if app else None
        p = partial_cache[key]
        part = f"（《{contents[s['data']['content_id']]['title']}》未完）" if (app and p == s["data"]["content_id"]) else ""
        cur += (f"，{title_list(ts)}{part}" if (title_list(ts) + part) else "")
        # compact content: drop a place title equal to the activity's own name; apps keep 2 titles then 等N条
        if app is None:
            ts2 = [x for x in ts if x != short]
            tail = "".join(f"《{x}》" for x in ts2[:3]) + (f"等{len(ts2)}条" if len(ts2) > 3 else "")
        else:
            # apps: first finished title, then 等N条; the unfinished title stays in the full (searchable) text only
            tail = (f"《{ts[0]}》" + (f"等{len(ts)}条" if len(ts) > 1 else "")) if ts else ""
        cid = s["data"].get("content_id")
        if cid and contents.get(cid, {}).get("consumes_state") == "groceries":
            k = kit_for(aid)
            if k is None:
                kit = "，用的是家里原有的食材"
            else:
                kd = lt(k["occurred_at"])
                kit = f"，用的是{kd.month}月{kd.day}日收到的{goods[k['data']['goods_id']]['name']}"
            cur += kit
            tail += kit.replace("用的是", "用")
    # compact ending: omit the default outcomes (planned end, app use ended, travel arrival)
    if s["kind"] == "travel" and ending == "到达":
        cshort = short.replace("前往", "到", 1)
        comp = f"{cshort} {span}"
    else:
        cend = {"按计划结束": "", "结束使用": ""}.get(ending, ending)
        if cend.startswith("比打算的多睡了"):
            cend = "多睡" + cend[len("比打算的多睡了 "):].replace(" ", "")
        comp = f"{short} {span}" + (f"，{cend}" if cend else "")
    if tail:
        comp += ("，" if not tail.startswith("，") else "") + tail
    return cur, comp, s


def qty(q):
    return dec(q)


def purchase_sentences(orders, ref, own_time):
    """current: one purchase_sentence per order (settled_text.py:112-121); compact: one line per purchase action."""
    cur = []
    for o in orders:
        x = f"{oclock(o['paid_at'], ref)} 买了{goods[o['goods_id']]['name']} {qty(o['quantity'])} 份，付 {money(o['total_price_fen'])}"
        if o["fulfillment"] in ("delivery", "station_pickup"):
            x += f"，预计 {oclock(o['expected_at'], ref)} 送到{loc_name(o['destination_id'])}"
        cur.append(x)
    names = [goods[o["goods_id"]]["name"] for o in orders]
    qs = {o["quantity"] for o in orders}
    if len(orders) == 1:
        items = f"{names[0]}{qty(orders[0]['quantity'])}份"
    elif qs == {1}:
        items = "、".join(names) + "各1份"
    else:
        items = "、".join(f"{n}{qty(o['quantity'])}份" for n, o in zip(names, orders))
    total = sum(o["total_price_fen"] for o in orders)
    paid = orders[0]["paid_at"]
    pre = "" if hm(paid) == own_time else cclock(paid, ref) + " "
    comp = f"{pre}买{items}，{'共' if len(orders) > 1 else ''}{cmoney(total)}"
    dels = [o for o in orders if o["fulfillment"] in ("delivery", "station_pickup")]
    if dels:
        etas = sorted({cclock(o["expected_at"], ref) for o in dels})
        dests = sorted({o["destination_id"] for o in dels})
        place = "、".join("家" if x == HOME else loc_name(x) for x in dests)
        comp += f"，预计{'、'.join(etas)}送到{place}"
    return cur, comp


def delivery_sentences(ev, ref, own_time):
    d = ev["data"]
    cur, comp_items, msgs = [], [], set()
    for o in d.get("orders", []):
        place = loc_name(o["destination_id"])
        msg = {"pending": "等待送达", "ready_for_pickup": f"已到{place}，请本人领取", "delivered": f"已送到{place}"}[o["status"]]
        cur.append(f"{oclock(ev['occurred_at'], ref)} {goods[o['goods_id']]['name']} {qty(o['quantity'])} 份（"
                   f"{oclock(o['created_at'], ref)} 购买）：{msg}")
        comp_items.append(goods[o["goods_id"]]["name"])
        msgs.add(o["status"])
    pre = "" if hm(ev["occurred_at"]) == own_time else hm(ev["occurred_at"]) + " "
    if msgs == {"delivered"}:
        comp = f"{pre}送达{'、'.join(comp_items)}"
    else:
        comp = f"{pre}{'、'.join(comp_items)}已到取货点待取" if "ready_for_pickup" in msgs else f"{pre}{'、'.join(comp_items)}等待送达"
    return cur, comp


def query_gist(s, ref):
    data = s["data"]
    ans = data.get("answer") or {}
    rows = ans.get("rows", [])
    tgt = "、".join(CAT_ZH.get(x.get("category"), "") if x.get("kind") == "category"
                   else goods.get(x.get("goods_id"), {}).get("name", "?") for x in data.get("targets", []))
    app = apps.get(data.get("app_id"), "") if data.get("app_id") else ""
    avail = [r for r in rows if r.get("available")]
    prices = [r["price_fen"] for r in avail if r.get("price_fen") is not None]
    rng = f"，{dec(min(prices) / 100)}–{dec(max(prices) / 100)}元" if prices else ""
    om = f"（另{ans['omitted']}项未列）" if ans.get("omitted") else ""
    end = s.get("ended_at") or s["last_settled_at"]
    cur = f"{s['name']}：{oclock(s['started_at'], ref)}–{oclock(end, ref)}，{ending_phrase(s)}"
    comp = f"查完{app}{'·' if app else ''}{tgt}：{len(avail)}项可买{om}{rng}"
    return cur, comp


REASON_ZH = {"SUPPLY_NOT_FOUND": "没有叫「{w}」的商品"}


def rejection_line(reqs, rej):
    parts = []
    for r in reqs:
        q = r["data"]["request"] or {}
        tool = r["data"]["tool_name"]
        act = q.get("action")
        tgt = q.get("target")
        tgt = "、".join(tgt) if isinstance(tgt, list) else (tgt or "")
        if act == "interact":
            parts.append(f"开始{q.get('detail') or tgt}")
        elif act == "purchase":
            parts.append(f"购买{tgt}")
        elif act == "query":
            parts.append(f"查询{tgt}")
        elif act in ("sleep", "travel"):
            parts.append(f"{'睡觉' if act == 'sleep' else '前往' + tgt}")
        else:
            items = (q.get("items") or [])
            nm = "、".join(short_name(i.get("activity_name", "")) for i in items)
            parts.append({"conclude_activity": "收尾", "report_status": "播报", "pause_activity": "暂停"}.get(tool, tool) + nm)
    d = rej["data"]
    w = (d.get("items") or [{}])[0].get("written", "")
    why = REASON_ZH.get(d.get("error_code"), d.get("error_code") or "").format(w=w)
    return f"被拒：{'、'.join(parts)}（{why}）"


# ---------------------------------------------------------------- categories (proposal rules 0–7)
def cat_activity(s):
    kind, data = s["kind"], s.get("data") or {}
    cid = data.get("content_id")
    if cid in OVERRIDES:
        return OVERRIDES[cid], "0"
    if kind == "sleep":
        return "生活", "1"
    if kind == "purchase":
        gids = [orders_by_id[o]["goods_id"] for o in data.get("order_ids", []) if o in orders_by_id]
        return ("饮食" if any(is_food_goods(g) for g in gids) else "生活"), "2"
    if kind == "query":
        tg = data.get("targets", [])
        food = any((x.get("kind") == "category" and x.get("category") in FOOD)
                   or (x.get("kind") != "category" and is_food_goods(x.get("goods_id"))) for x in tg)
        rows = (data.get("answer") or {}).get("rows", [])
        food = food or any(is_food_goods(r.get("goods_id")) for r in rows)
        return ("饮食" if food else "生活"), "2"
    c = contents.get(cid, {}) if cid else {}
    date = day_of(s["started_at"])
    sh = shift(date)
    if kind == "interact" and data.get("app_id") is None:
        if (c.get("completion_effects") or {}).get("food") or c.get("consumes_state") in FOOD_STATES:
            return "饮食", "3"
    if kind == "travel":
        ends = {s.get("location_id"), s.get("destination_id")}
        if sh and ends <= {HOME, WORK} and len(ends) == 2:
            return "工作", "4"
        return "生活", "7"
    if kind == "interact" and data.get("app_id") is None:
        if sh:
            mins = lt(s["started_at"]).hour * 60 + lt(s["started_at"]).minute
            in_duty = sh[0] <= mins < sh[1] and not any(a <= mins < b for a, b in sh[2])
            if s.get("location_id") == WORK or (in_duty and s.get("location_id") != HOME):
                return "工作", "5"
        return "生活", "7"
    if kind == "interact":
        if WORK_KIND in ((c.get("prerequisite") or {}).get("location_kinds") or []):
            return "工作", "6"
        return "生活", "6"
    return "生活", "7"


def cat_goods(gids):
    return "饮食" if any(is_food_goods(g) for g in gids) else "生活"


def cat_text_target(text):
    """rejected purchase/query target (approximation: food goods name, food category word or 食材/饭/菜)."""
    if any(w in text for w in ("食品", "零食", "饮料", "食材", "饭", "菜", "面", "咖啡", "奶")):
        return "饮食"
    if any(g["name"] in text and is_food_goods(gid) for gid, g in goods.items()):
        return "饮食"
    return "生活"


name_to_aid = {}
for aid, rows in snaps.items():
    for _, s in rows:
        name_to_aid[s["name"]] = aid


# ---------------------------------------------------------------- build entries
ROUTINE = {"routine"}


def build(d):
    """facts and self texts of one decision's window."""
    ref, own = d["t"], hm(d["t"])
    mid = midnight(d["t"])
    a, b_ = bisect.bisect_right(SEQS, d["lo"]), bisect.bisect_right(SEQS, d["C"])
    win = EV[a:b_]
    facts, closing = [], []  # dict(kind, cur, comp, cat, t)

    def put(f, t):
        (closing if t < mid else facts).append(f)
    paid_actions = set()
    for e in win:
        t, dd = e["event_type"], e["data"]
        if t == "action_result" and dd.get("status") == "completed":
            req = requests.get(e["action_id"])
            ords = [o for o in (dd.get("data") or {}).get("orders", []) or [] if o.get("paid_at") is not None]
            if req and req["data"]["tool_name"].startswith("start_") and ords and e["action_id"] not in paid_actions:
                paid_actions.add(e["action_id"])
                cur, comp = purchase_sentences(ords, ref if e["occurred_at"] >= mid else e["occurred_at"], own)
                put({"kind": "purchase", "cur": cur, "comp": comp, "cat": cat_goods([o["goods_id"] for o in ords]),
                     "n_items": len(ords), "t": e["occurred_at"]}, e["occurred_at"])
        if t == "environment_event" and dd.get("kind") == "delivery_arrival" and e.get("visibility") == "agent":
            cur, comp = delivery_sentences(e, ref if e["occurred_at"] >= mid else e["occurred_at"], own)
            put({"kind": "delivery", "cur": cur, "comp": comp, "cat": cat_goods([o["goods_id"] for o in dd["orders"]]),
                 "n_items": len(dd["orders"]), "t": e["occurred_at"]}, e["occurred_at"])
        if t == "environment_event" and dd.get("kind") == "errand_completed" and e.get("visibility") == "agent":
            er = dd["errand"]
            put({"kind": "errand_done", "cur": [f"{oclock(e['occurred_at'], ref)} 办成「{er['name']}」"],
                 "comp": f"办成「{er['name']}」", "cat": cat_goods([er.get("product_id")]), "t": e["occurred_at"]},
                e["occurred_at"])
    # endings in window
    for aid, seq in endings.items():
        if not (d["lo"] < seq <= d["C"]):
            continue
        et = by_seq[seq]["occurred_at"]
        r_ = ref if et >= mid else et
        s = snap_at(aid, d["C"])
        cat, rule = cat_activity(s)
        if s["kind"] == "purchase":
            paid = any(orders_by_id.get(o, {}).get("paid_at") for o in s["data"].get("order_ids", []))
            if paid:
                continue  # merge rule: the purchase sentence replaces the purchase activity's ending
        if s["kind"] == "query" and s["status"] == "completed":
            cur, comp = query_gist(s, r_)
            put({"kind": "query", "cur": [cur], "comp": comp, "cat": cat, "rule": rule, "t": et}, et)
            continue
        cur, comp, _ = activity_sentences(aid, d["C"], r_)
        kind = "natural_end" if s.get("boundary_type") == "planned_end" and s["status"] == "completed" else "end"
        put({"kind": kind, "cur": [cur], "comp": comp, "cat": cat, "rule": rule, "t": et, "aid": aid,
             "titles": len(titles_of(s)) if s["kind"] == "interact" else 0}, et)
    # the decision's own requests
    own_reqs = [e for e in win if e["event_type"] == "action_requested" and e.get("decision_id") == d["did"]]
    selfs, kinds, prim = [], [], None
    rej_reqs = [r for r in own_reqs if r["action_id"] in rejected]
    acc_reqs = [r for r in own_reqs if r["action_id"] not in rejected]
    notes_seen = set()
    starts = []
    for r in own_reqs:
        tool, q = r["data"]["tool_name"], r["data"]["request"] or {}
        sn = (r["data"].get("self_note") or "").strip()
        if sn and sn not in notes_seen:
            notes_seen.add(sn)
            selfs.append({"kind": "body", "text": f"正文：{sn}", "cat": None})
        if r in rej_reqs:
            continue
        res = results.get(r["action_id"], [])
        if tool.startswith("start_"):
            aids = []
            for x in res:
                p = (x["data"].get("data") or {})
                aids += [s_["activity_id"] for s_ in p.get("activities", [])]
                if isinstance(p.get("activity"), dict):
                    aids.append(p["activity"]["activity_id"])
                if x["data"].get("activity_id"):
                    aids.append(x["data"]["activity_id"])
            aid = next((x for x in dict.fromkeys(aids) if x in snaps), None)
            if aid:
                s = snap_at(aid, d["C"])
                cat, _ = cat_activity(snap_at(aid, max(d["C"], endings.get(aid, d["C"]))))
                starts.append((tool, short_name(s["name"]), cat, q.get("action")))
            kinds.append(q.get("action") if q.get("action") in ("purchase", "query") else "start")
        elif tool in ("conclude_activity", "pause_activity", "report_status"):
            kinds.append({"conclude_activity": "conclude", "pause_activity": "pause", "report_status": "report"}[tool])
            ok = any(x["data"]["status"] == "completed" for x in res)
            if not ok:
                continue
            for it in q.get("items", []):
                nm = it.get("activity_name", "")
                aid = name_to_aid.get(nm)
                cat = cat_activity(snap_at(aid, d["C"]))[0] if aid else None
                sh = short_name(nm)
                summ = (it.get("summary") or "").strip()
                if tool == "report_status":
                    if summ:
                        selfs.append({"kind": "report", "text": f"{sh}进行中：{summ}", "cat": cat})
                    prim = prim or cat
                    continue
                if tool == "pause_activity":
                    facts.append({"kind": "pause", "cur": [], "comp": f"暂停{sh}", "cat": cat, "t": d["t"]})
                if summ:
                    selfs.append({"kind": "summary", "text": f"{sh}：{summ}", "cat": cat})
                for m in it.get("memories", []) or []:
                    tx = (m.get("text") or "").strip()
                    if not tx or tx == summ:
                        continue
                    k = "routine" if m.get("type") in ROUTINE else "memory"
                    selfs.append({"kind": k, "text": (tx if k == "routine" else f"记住：{tx}"), "cat": cat,
                                  "mtype": m.get("type")})
                for v in it.get("views", []) or []:
                    vc = cat if v.get("type") != "relationship" else (cat or "生活")
                    selfs.append({"kind": "view", "text": f"看法·{v.get('about')}：{v.get('text')}", "cat": vc,
                                  "vtype": v.get("type")})
                prim = prim or cat
            for er in q.get("errands", []) or []:
                facts.append({"kind": "errand_change", "cur": [], "comp": f"记下待办「{er.get('name')}」" if er.get("change") == "add"
                              else f"{er.get('change')}待办「{er.get('name')}」", "cat": None, "t": d["t"]})
        elif tool == "revise_day_plan":
            kinds.append("revise")
            ok = any(x["data"]["status"] == "completed" for x in res)
            if ok:
                facts.append({"kind": "revise", "cur": [], "comp": "改了今日计划", "cat": "生活", "t": d["t"]})
    if starts:
        main = [s_ for s_ in starts if s_[0] == "start_main_activity"] or starts
        prim = prim or main[0][2]
        facts.append({"kind": "start", "cur": [], "comp": "开始" + "、".join(s_[1] for s_ in starts),
                      "cat": main[0][2], "t": d["t"], "n": len(starts)})
    if rej_reqs:
        txt = rejection_line(rej_reqs, rejected[rej_reqs[0]["action_id"]])
        q0 = rej_reqs[0]["data"]["request"] or {}
        tg = q0.get("target")
        tg = "、".join(tg) if isinstance(tg, list) else (q0.get("detail") or tg or "")
        main_rej = next((r for r in rej_reqs if r["data"]["tool_name"] == "start_main_activity"), rej_reqs[0])
        qm = main_rej["data"]["request"] or {}
        tgm = qm.get("detail") or ("、".join(qm["target"]) if isinstance(qm.get("target"), list) else qm.get("target") or "")
        rc = cat_text_target(tgm) if qm.get("action") in ("purchase", "query") else "生活"
        facts.append({"kind": "rejection", "cur": [], "comp": txt, "cat": rc, "t": d["t"]})
        prim = prim or rc
        if not acc_reqs:
            kinds.append("rejected_only")
    for f in facts:
        f["cat"] = f["cat"] or prim
    prim = prim or next((f["cat"] for f in facts if f["cat"]), None) or "生活"
    for s_ in selfs:
        s_["cat"] = s_["cat"] or prim
    # errand changes / body inherit prim
    order = {"start": 9, "pause": 8, "rejection": 10, "errand_change": 11, "revise": 12}
    facts.sort(key=lambda f: (order.get(f["kind"], 0), f["t"]))
    return facts, closing, selfs, kinds, prim


PRI = {"summary": 1, "report": 1, "view": 2, "memory": 3, "body": 4}
HEAD = 40  # rule B: first pass gives every visible 自述 item up to this many characters
ALMOST = 10  # an item at most this much longer than the head is given whole in the first pass


BOUNDARY = "，。；！？、"
MAX_WASTE = 10  # a cut moves back to the last punctuation mark if that gives up at most this many characters


def cut_text(x, keep):
    """x shortened to keep characters including the closing …, at a punctuation boundary when that wastes ≤ MAX_WASTE."""
    body = x[:keep - 1]
    j = max(body.rfind(p) for p in BOUNDARY)
    if j >= len(body) // 2 and len(body) - j <= MAX_WASTE:
        body = body[:j]
    return body + "…"


def ordered_selfs(selfs):
    return [s["text"] for s in sorted([s for s in selfs if s["kind"] != "routine"], key=lambda s: PRI[s["kind"]])]


def cap(facts, selfs, limit=CAP):
    """rule A (strict priority): 核验, then summaries, views, non-routine memories, body; the first item that does not
    fit is cut to the remaining room with …, or replaced by a lone … when fewer than MIN_CUT characters remain;
    everything after it is left out. Routine memories are never rendered."""
    kv, sv = [f["comp"] for f in facts], ordered_selfs(selfs)
    out_k, out_s, used, mode = [], [], 0, "fit"
    if sum(len(x) for x in kv) > limit:
        mode = "verify_over"
    for sec, x in [("k", x) for x in kv] + [("s", x) for x in sv]:
        tgt = out_k if sec == "k" else out_s
        if used + len(x) <= limit:
            tgt.append(x)
            used += len(x)
            continue
        room = limit - used
        if room >= MIN_CUT:
            tgt.append(cut_text(x, room))
            used += len(tgt[-1])
            mode = mode if mode == "verify_over" else "cut"
        else:
            tgt.append("…")
            used += 1
            mode = mode if mode == "verify_over" else "dropped"
        break
    return out_k, out_s, used, mode


def cap_b(facts, selfs, limit=CAP, head=HEAD):
    """rule B (heads first): 核验 whole; then each 自述 item, in priority order, gets its first `head` characters (or the
    whole item when at most ALMOST more remain); at the first head that does not fit, the item gets the remaining room
    if that is at least MIN_CUT, and every later item is left out. Remaining room then extends the cut items in the same
    order, and room freed by punctuation-boundary cuts is handed back (up to 3 passes). A cut item ends with …; when an
    item is left out and the last shown item is whole, a lone … is appended (or replaces the last character)."""
    kv, sv = [f["comp"] for f in facts], ordered_selfs(selfs)
    used = sum(len(x) for x in kv)
    if used > limit:
        return cap(facts, selfs, limit)
    room = limit - used
    give = [0] * len(sv)
    for i, x in enumerate(sv):
        h = len(x) if len(x) - head <= ALMOST else head
        if h <= room:
            give[i] = h
            room -= h
            continue
        if room >= MIN_CUT:
            give[i] = room
            room = 0
        break
    def outs():
        gone = any(g == 0 for g in give)
        res = [x if g >= len(x) else cut_text(x, g) for x, g in zip(sv, give) if g]
        if gone and (not res or not res[-1].endswith("…")):
            res.append("…")
        return res

    for i, x in enumerate(sv):
        if give[i] and give[i] < len(x) and room > 0:
            extra = min(room, len(x) - give[i])
            give[i] += extra
            room -= extra
    for _ in range(3):
        spare = limit - used - sum(len(y) for y in outs())
        k = next((i for i, x in enumerate(sv) if 0 < give[i] < len(x)), None)
        if spare < 2 or k is None:
            break
        shown_k = len(sv[k]) if give[k] >= len(sv[k]) else len(cut_text(sv[k], give[k]))
        give[k] = min(len(sv[k]), shown_k + spare)
    out_s = outs()
    while used + sum(len(y) for y in out_s) > limit:
        # the closing … pushed the entry over the cap: shorten the last shown item (or leave it out)
        k = max(i for i, g in enumerate(give) if g)
        over = used + sum(len(y) for y in out_s) - limit
        give[k] = give[k] - over if give[k] - over >= 2 else 0
        out_s = outs()
    left_out = any(g == 0 for g in give)
    mode = "dropped" if left_out else ("cut" if any(0 < g < len(x) for x, g in zip(sv, give)) else "fit")
    return kv, out_s, used + sum(len(y) for y in out_s), mode


def render(e):
    o = {"时刻": e["时刻"], "类别": e["类别"]}
    if e["核验"]:
        o["核验"] = e["核验"]
    if e["自述"]:
        o["自述"] = e["自述"]
    return o


def render_line(e):
    """alternative one-string form: 「HH:MM 类别｜核验…｜自述…」."""
    parts = [f"{e['时刻']} {e['类别']}"]
    if e["核验"]:
        parts.append("；".join(e["核验"]))
    if e["自述"]:
        parts.append("自述：" + "；".join(e["自述"]))
    return "｜".join(parts)


def size(x):
    return len(json.dumps(x, ensure_ascii=False, separators=(",", ":")))


def q(v, p):
    v = sorted(v)
    return v[int(round(p * (len(v) - 1)))] if v else 0


def dist(v):
    return {"n": len(v), "median": statistics.median(v) if v else 0, "p90": q(v, 0.9), "max": max(v) if v else 0,
            "mean": round(sum(v) / len(v), 1) if v else 0}


rows = []
closings = collections.defaultdict(list)
for d in D:
    facts, closing, selfs, kinds, prim = build(d)
    cats = list(dict.fromkeys([prim] + [f["cat"] for f in facts] + [s["cat"] for s in selfs if s["kind"] != "routine"]))
    k_txt = [f["comp"] for f in facts]
    s_vis = [s["text"] for s in selfs if s["kind"] != "routine"]
    s_all = [s["text"] for s in selfs]
    ck, cs, used, mode = cap(facts, selfs)
    bk, bs, bused, bmode = cap_b(facts, selfs)
    date = day_of(d["t"])
    entry = {"时刻": hm(d["t"]), "类别": "、".join(cats), "核验": ck, "自述": cs}
    entry_b = {"时刻": hm(d["t"]), "类别": "、".join(cats), "核验": bk, "自述": bs}
    full = {"时刻": hm(d["t"]), "类别": "、".join(cats), "核验": k_txt, "自述": s_vis, "routine": [
        s["text"] for s in selfs if s["kind"] == "routine"]}
    rows.append({"did": d["did"], "date": date.isoformat(), "t": d["t"], "time": lt(d["t"]).strftime("%H:%M:%S"),
                 "chain": d["chain"], "replay": d["replay"], "kinds": kinds, "prim": prim, "cats": cats,
                 "facts": facts, "selfs": selfs,
                 "len_verify": sum(len(x) for x in k_txt),
                 "len_verify_current": sum(len(c) for f in facts for c in f["cur"]),
                 "len_self_visible": sum(len(x) for x in s_vis), "len_self_all": sum(len(x) for x in s_all),
                 "len_capped": used, "mode": mode, "entry": entry,
                 "len_capped_b": bused, "mode_b": bmode, "entry_b": entry_b, "full": full})
    if closing:
        prev = (date - datetime.timedelta(days=1)).isoformat()
        closings[prev].append({"from_decision": d["did"], "facts": closing})


def crossing(day_iso):
    """activities started before the next midnight and still running at it (digest-only closing lines)."""
    m = int(datetime.datetime.fromisoformat(day_iso).replace(tzinfo=Z).timestamp()) + 86400
    lines = []
    for aid, rs in snaps.items():
        s = rs[-1][1]
        st = s["started_at"]
        en = s.get("ended_at")
        if st < m and (en is None or en > m):
            lines.append(f"{hm(st)} 开始{short_name(s['name'])}，跨过零点")
    return lines


# ---------------------------------------------------------------- measurements
def classify(r):
    k = r["kinds"]
    has_fact = any(f["kind"] not in ("start", "rejection", "errand_change", "revise", "pause") for f in r["facts"])
    body = any(s["kind"] == "body" for s in r["selfs"])
    if "rejected_only" in k:
        return "rejected_only"
    if "conclude" in k:
        return "conclude"
    if "pause" in k:
        return "pause"
    if "revise" in k:
        return "revise_day_plan"
    if "report" in k:
        return "report_status" if not has_fact else "report_status+settled"
    if "purchase" in k:
        return "purchase_start"
    if "query" in k:
        return "query_start"
    if "start" in k:
        n = sum(1 for x in k if x == "start")
        base = "start_pair" if n > 1 else "start"
        if not has_fact and not body:
            return base + "_only"
        return base + "+settled" if has_fact else base + "+body"
    return "other"


summary = {"run": TAG, "decisions": len(rows), "same_second_chain": sum(r["chain"] for r in rows),
           "replay_requests": sum(r["replay"] for r in rows)}
per_day = collections.defaultdict(lambda: collections.Counter())
types = collections.Counter()
for r in rows:
    r["type"] = classify(r)
    types[r["type"]] += 1
    pd = per_day[r["date"]]
    pd["all"] += 1
    pd["chain" if r["chain"] else "fresh"] += 1
    pd["prim:" + r["prim"]] += 1
    for c in r["cats"]:
        pd["any:" + c] += 1
    if len(r["cats"]) > 1:
        pd["mixed"] += 1
summary["types"] = dict(types.most_common())
summary["chain_types"] = dict(collections.Counter(r["type"] for r in rows if r["chain"]))
summary["per_day"] = {k: dict(v) for k, v in sorted(per_day.items())}
summary["mixed_category_entries"] = sum(1 for r in rows if len(r["cats"]) > 1)
summary["fact_kinds"] = dict(collections.Counter(f["kind"] for r in rows for f in r["facts"]))
summary["category_rules_used"] = dict(collections.Counter(f.get("rule") for r in rows for f in r["facts"] if f.get("rule")))
summary["windows_with_n_ends"] = dict(collections.Counter(
    sum(1 for f in r["facts"] if f["kind"] in ("natural_end", "end", "query")) for r in rows))
summary["receipts_items"] = dict(collections.Counter(f.get("n_items") for r in rows for f in r["facts"]
                                                      if f["kind"] in ("purchase", "delivery")))
lv = [r["len_verify"] for r in rows]
lvc = [r["len_verify_current"] for r in rows]
lsv = [r["len_self_visible"] for r in rows]
lsa = [r["len_self_all"] for r in rows]
comb = [r["len_verify"] + r["len_self_visible"] for r in rows]
comb_all = [r["len_verify"] + r["len_self_all"] for r in rows]
summary["len_verify_compact"] = dist(lv)
summary["len_verify_compact_nonzero"] = dist([x for x in lv if x])
summary["len_verify_current_forms"] = dist(lvc)
summary["len_verify_current_nonzero"] = dist([x for x in lvc if x])
summary["verify_current_over120"] = sum(1 for x in lvc if x > CAP)
summary["verify_compact_over120"] = sum(1 for x in lv if x > CAP)
summary["verify_compact_over_60"] = sum(1 for x in lv if x > 60)
summary["len_self_visible"] = dist(lsv)
summary["len_self_all_incl_routine"] = dist(lsa)
summary["len_combined_visible"] = dist(comb)
summary["len_combined_incl_routine"] = dist(comb_all)
summary["over120_visible"] = sum(1 for x in comb if x > CAP)
summary["over120_incl_routine"] = sum(1 for x in comb_all if x > CAP)
summary["over120_by_type"] = dict(collections.Counter(r["type"] for r, x in zip(rows, comb) if x > CAP))
summary["type_counts_and_combined"] = {t: {"n": types[t], **dist([x for r, x in zip(rows, comb) if r["type"] == t])}
                                       for t in types}
summary["modes_rule_a"] = dict(collections.Counter(r["mode"] for r in rows))
summary["modes_rule_b"] = dict(collections.Counter(r["mode_b"] for r in rows))
fk = collections.defaultdict(lambda: [[], []])
for r in rows:
    for f in r["facts"]:
        fk[f["kind"]][0].append(len(f["comp"]))
        fk[f["kind"]][1].append(sum(len(c) for c in f["cur"]))
summary["fact_len_compact_vs_current"] = {k: {"compact": dist(v[0]), "current": dist(v[1])} for k, v in fk.items()}
sk = collections.defaultdict(list)
for r in rows:
    for s in r["selfs"]:
        sk[s["kind"]].append(len(s["text"]))
summary["self_len_by_kind"] = {k: dist(v) | {"total": sum(v)} for k, v in sk.items()}


def survival(key):
    """per 自述 kind: shown whole / cut / left out under a rule."""
    out = collections.defaultdict(collections.Counter)
    for r in rows:
        shown = r[key]["自述"]
        for s in r["selfs"]:
            if s["kind"] == "routine":
                continue
            t = s["text"]
            if t in shown:
                out[s["kind"]]["whole"] += 1
            elif any(x.endswith("…") and len(x) > 1 and t.startswith(x[:-1]) for x in shown):
                out[s["kind"]]["cut"] += 1
            else:
                out[s["kind"]]["left_out"] += 1
    return {k: dict(v) for k, v in out.items()}


summary["self_survival_rule_a"] = survival("entry")
summary["self_survival_rule_b"] = survival("entry_b")

# rendered day lists and the maximum 今天 at any decision
day_rows = collections.defaultdict(list)
best = {"a": (0, None, 0), "b": (0, None, 0), "line": (0, None, 0)}
sizes = {"a": [], "b": [], "line": []}
for r in rows:
    prev = day_rows[r["date"]]
    sa = size([render(x["entry"]) for x in prev]) if prev else 0
    sb = size([render(x["entry_b"]) for x in prev]) if prev else 0
    sl = size([render_line(x["entry"]) for x in prev]) if prev else 0
    for k, v in (("a", sa), ("b", sb), ("line", sl)):
        sizes[k].append(v)
        if v > best[k][0]:
            best[k] = (v, f"{r['date']} {r['time']}", len(prev))
    r["today_size_at_decision"] = sa
    r["today_size_at_decision_b"] = sb
    prev.append(r)
summary["today_size_at_decision_rule_a"] = dist(sizes["a"])
summary["today_size_at_decision_rule_b"] = dist(sizes["b"])
summary["today_size_at_decision_line_form"] = dist(sizes["line"])
summary["today_max_at_any_decision"] = best
summary["day_end_list"] = {}
for day, rs in sorted(day_rows.items()):
    lst = [render(x["entry"]) for x in rs]
    unc = [render({"时刻": x["full"]["时刻"], "类别": x["full"]["类别"], "核验": x["full"]["核验"],
                   "自述": x["full"]["自述"]}) for x in rs]
    summary["day_end_list"][day] = {
        "entries": len(lst), "rendered_rule_a": size(lst), "rendered_rule_b": size([render(x["entry_b"]) for x in rs]),
        "rendered_line_form": size([render_line(x["entry"]) for x in rs]),
        "text_chars_capped": sum(x["len_capped"] for x in rs), "rendered_uncapped_visible": size(unc),
        "key_overhead_rule_a": size(lst) - sum(x["len_capped"] for x in rs),
        "entries_by_primary_category": dict(collections.Counter(x["prim"] for x in rs)),
        "text_chars_by_primary_category": {c: sum(x["len_capped"] for x in rs if x["prim"] == c)
                                           for c in ("饮食", "工作", "生活")},
        "report_status_entries": sum(1 for x in rs if x["type"].startswith("report_status")),
        "report_status_rendered": size([render(x["entry"]) for x in rs if x["type"].startswith("report_status")]),
        "start_only_rendered": size([render(x["entry"]) for x in rs if x["type"] in ("start_only", "start_pair_only")]),
        "closing_facts_for_this_day": sum(len(c["facts"]) for c in closings.get(day, [])),
        "crossing_lines": crossing(day)}
summary["per_entry_rendered_overhead_mean"] = round(sum(size(render(r["entry"])) - r["len_capped"] for r in rows) / len(rows), 1)
summary["entry_rendered_size"] = dist([size(render(r["entry"])) for r in rows])
summary["closing_entries"] = {day: [[f["comp"] for f in c["facts"]] for c in cs] for day, cs in sorted(closings.items())}

# sensitivity of rule B to the head length, and a what-if 60-character sub-cap for report_status-only entries
summary["rule_b_head_sensitivity"] = {}
for hd in (30, 40, 50, 60):
    shown = collections.Counter()
    for r in rows:
        k_, s_, u_, m_ = cap_b(r["facts"], r["selfs"], CAP, hd)
        for s in r["selfs"]:
            if s["kind"] == "view":
                shown["whole" if s["text"] in s_ else ("cut" if any(x.endswith("…") and len(x) > 1 and s["text"].startswith(x[:-1]) for x in s_) else "left_out")] += 1
    summary["rule_b_head_sensitivity"][hd] = dict(shown)
whatif = collections.defaultdict(int)
for r in rows:
    lim = 60 if r["type"] == "report_status" else CAP
    k_, s_, u_, m_ = cap_b(r["facts"], r["selfs"], lim)
    whatif[r["date"]] += size(render({"时刻": r["entry"]["时刻"], "类别": r["entry"]["类别"], "核验": k_, "自述": s_})) + 1
summary["whatif_report_status_cap60_day_rendered"] = dict(whatif)
# entries that reach a decision inside a same-second chain (would travel in the chain's tool-message changes)
inchain = []
for i, r in enumerate(rows):
    if not r["chain"]:
        continue
    j, add = i - 1, []
    while j >= 0 and rows[j]["t"] == r["t"]:
        add.append(size(render(rows[j]["entry_b"])))
        if not rows[j]["chain"]:
            break
        j -= 1
    inchain.append((len(add), sum(add)))
summary["chain_new_entries_visible"] = {"decisions": len(inchain), "entries": dist([a for a, _ in inchain]),
                                        "rendered_chars": dist([b for _, b in inchain])}
json.dump(summary, open(f"{OUT}/{TAG}_summary.json", "w"), ensure_ascii=False, indent=1, default=str)
with open(f"{OUT}/{TAG}_rows.jsonl", "w") as fh:
    for r in rows:
        fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
if TAG == "V":
    day = [r for r in rows if r["date"] == "2026-10-12"]
    json.dump([{"决策": r["did"].split(":round:")[1], "时间": r["time"], "同秒链": r["chain"], "类型": r["type"],
                "条目": render(r["entry_b"]), "条目字数": r["len_capped_b"], "截断": r["mode_b"],
                "条目_严格优先": render(r["entry"]), "条目字数_严格优先": r["len_capped"],
                "全文": r["full"], "此刻可见的今天字符数": r["today_size_at_decision_b"]} for r in day],
              open(f"{OUT}/v_1012_entries.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps({k: summary[k] for k in ("decisions", "same_second_chain", "replay_requests", "types", "modes_rule_a",
                                         "over120_visible", "over120_incl_routine", "mixed_category_entries")},
                 ensure_ascii=False))
