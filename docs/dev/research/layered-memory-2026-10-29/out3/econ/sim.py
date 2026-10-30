"""Read-only DeepSeek prefix-cache simulation over V's real decision-request sequence.

Model: every earlier decision request's full prompt stays in the cache (shared, no eviction); a request hits the
longest common prefix with any earlier request, floored to G tokens (G=128 observed: every V/B hit is a multiple
of 128). Prefix tokens = T_ST (system+tools+template, calibrated) + class-model tokens of the user/continuation
text in the prefix (tok.py coefficients fitted on usage.prompt_tokens, tokfit.json).

Scenarios rebuild the user message (layer order and 记忆层 content) and keep every other byte of the real request.
Prompt tokens of a rebuilt request = actual prompt_tokens + class-model delta of the changed text.
Output tokens are the actual completion tokens (behaviour assumed unchanged).
"""
import bisect, json, sys, collections, re
from datetime import datetime
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from common import *
from tok import feats, CLASSES

COEF = json.load(open(sys.path[0] + "/tokfit.json"))["coef"]
SEP = "\x1e"


def ctok(text):
    """class-model tokens of text (no intercept)"""
    f = feats(text)
    return sum(COEF[k] * v for k, v in zip(CLASSES, f)) + COEF["per_msg"] * text.count(SEP)


def lcp(a, b):
    """length of the common prefix (binary search on C-level slice compares)"""
    lo, hi = 0, min(len(a), len(b))
    if a[:hi] == b[:hi]:
        return hi
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


class Cache:
    """all earlier prompts; max LCP with the set = max LCP with sorted neighbours"""

    def __init__(self):
        self.items = []

    def best(self, s):
        i = bisect.bisect_left(self.items, s)
        n = 0
        for j in (i - 1, i):
            if 0 <= j < len(self.items):
                n = max(n, lcp(s, self.items[j]))
        return n

    def add(self, s):
        bisect.insort(self.items, s)


# ---------------------------------------------------------------- run data
def load_run(run="V"):
    """decision requests in order, with per-decision metadata"""
    ev = load(RUNS[run])
    by_id = {e["event_id"]: (s, e) for s, e in ev}
    resp = {e["data"]["call_id"]: e["data"] for s, e in ev if e["event_type"] == "model_response"}
    reqs, dec_order, dec_time = [], [], {}
    seg_marks = [s for s, e in ev if e["event_type"] == "environment_event"
                 and e["data"].get("kind") in ("location_left", "location_entered")]
    tool_owner = {}
    for s, e in ev:
        if e["event_type"] != "model_request" or not is_decision(e["data"]["request"]):
            continue
        d = e["data"]; r = d["request"]; rr = resp[d["call_id"]]
        u = rr["response"]["usage"]
        did = e["decision_id"]
        if did not in dec_time:
            dec_order.append(did); dec_time[did] = (e["occurred_at"], s)
        msg = rr["response"]["choices"][0]["message"]
        for tc in msg.get("tool_calls") or []:
            tool_owner[tc["id"]] = did
        reqs.append(dict(seq=s, t=e["occurred_at"], did=did, msgs=r["messages"], tools=ser(r["tools"]),
                         prompt=u["prompt_tokens"], hit=u["prompt_cache_hit_tokens"], out=u["completion_tokens"],
                         answer=msg))
    # orders in transit (delivery only): placed (purchase source event seq) -> delivery_arrival seq
    names = {}
    for s, e in ev:
        if e["event_type"] == "environment_event" and e["data"].get("kind") == "order_notification":
            for oid in e["data"].get("order_ids") or []:
                names.setdefault(oid, (e["data"].get("summary") or "").replace("已下单：", ""))
    arrive = {}
    for s, e in ev:
        if e["event_type"] == "environment_event" and e["data"].get("kind") == "delivery_arrival":
            for o in e["data"]["orders"]:
                arrive[o.get("order_id")] = (s, o.get("arrived_at"))
    orders = []
    for line in open(RUNS[run] + "/export/purchases.jsonl"):
        p = json.loads(line)
        if p.get("fact_kind") != "purchase" or p.get("fulfillment") != "delivery":
            continue
        placed = by_id[p["source_event_id"]][0]
        a = arrive.get(p["order_id"])
        orders.append(dict(placed=placed, arrived=a[0] if a else 10 ** 12, created=p["created_at"],
                           eta=a[1] if a else None, name=names.get(p["order_id"], p["goods_id"])))
    return dict(reqs=reqs, dec_order=dec_order, dec_time=dec_time, seg_marks=seg_marks, orders=orders,
                tool_owner=tool_owner)


def hhmm(t):
    return datetime.fromtimestamp(t, TZ).strftime("%H:%M")


def cjk_text(msg):
    """Chinese strings from the decision's answer (tool args + reasoning) for realistic entry composition"""
    out = []
    for tc in msg.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"]["arguments"])
        except Exception:
            continue
        stack = [args]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                stack.extend(x.values())
            elif isinstance(x, list):
                stack.extend(x)
            elif isinstance(x, str) and re.search(r"[一-鿿]", x):
                out.append(x)
    out.append(msg.get("reasoning_content") or "")
    return "；".join(out)


def text_pools(data):
    """real 核验 sentences and 自述 strings from V's 记忆层 (earlier_today settled / self_notes)"""
    if "pools" in data:
        return data["pools"]
    settled, notes = [], []
    seen = set()
    for r in data["reqs"]:
        if len(r["msgs"]) != 2:
            continue
        for l in json.loads(r["msgs"][1]["content"]):
            if l["layer"] != "记忆层":
                continue
            for it in l.get("earlier_today") or []:
                for x in it.get("settled") or []:
                    if x not in seen:
                        seen.add(x); settled.append(x)
                stack = list(it.get("self_notes") or [])
                while stack:
                    y = stack.pop()
                    if isinstance(y, dict):
                        stack.extend(v for k, v in y.items() if k != "activity")
                    elif isinstance(y, list):
                        stack.extend(y)
                    elif isinstance(y, str) and re.search(r"[\u4e00-\u9fff]", y) and y not in seen:
                        seen.add(y); notes.append(y)
    data["pools"] = (settled, notes)
    return data["pools"]


def make_entry(did, data, length):
    """one 今天 entry rendered to ~length chars: real 核验 sentence(s) + real 自述 text, unique per decision"""
    settled, notes = text_pools(data)
    k = data["pos"][did]
    t = data["dec_time"][did][0]
    shell = ser({"时刻": hhmm(t), "类别": "生活", "核验": [""], "自述": [""]})
    body = max(10, length - len(shell))
    a = settled[(k * 7) % len(settled)]
    b = notes[(k * 5) % len(notes)] + "；" + notes[(k * 5 + 1) % len(notes)]
    a = a[: max(20, body * 45 // 100)]
    b = b[: body - len(a)]
    return ser({"时刻": hhmm(t), "类别": "生活", "核验": [a], "自述": [b]})


def prose_pool(data):
    """Chinese first-person prose (real 自述 strings) for synthetic 近三天 digests"""
    return "".join(text_pools(data)[1])


WEEK = "一二三四五六日"


def recent_block(date_t, pool, cell=200):
    """近三天: three earlier days x three categories x cell字 (steady-state, full)"""
    days = []
    for k in (3, 2, 1):
        t = date_t - k * 86400
        dt = datetime.fromtimestamp(t, TZ)
        off = (int(t // 86400) * 997) % max(1, len(pool) - 4 * cell)
        days.append({"日期": dt.strftime("%m-%d"), "星期": "周" + WEEK[dt.weekday()],
                     "饮食": pool[off: off + cell], "工作": pool[off + cell: off + 2 * cell],
                     "生活": pool[off + 2 * cell: off + 3 * cell]})
    return days


KECHA = "更早的每天、每月、每年的回顾和今天已收起的内容，都可以用 search_memory 按类别和时间查，可加关键词。"
