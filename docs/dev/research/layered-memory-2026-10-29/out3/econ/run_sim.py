"""Run cache/cost scenarios on V (read-only). Usage: run_sim.py [calibrate|scenarios] [G] [T_ST] [ENTRY_LEN]"""
import sys, json, collections
sys.path.insert(0, "/Users/atishoo/Project/WorldMock/docs/dev/research/layered-memory-2026-10-29/out3/econ")
from sim import *

CACHE_OUTPUT = True
CUR = ("工具层", "人设层", "记忆层", "待办层", "环境层", "进度层")
PROP = ("人设层", "记忆层", "待办层", "工具层", "环境层", "进度层")


def user_stream(msgs, user_content, extra=None):
    """after-tools part of the prompt stream; extra maps message index -> text appended to that message"""
    parts = ["<|user|>" + user_content]
    for i, m in enumerate(msgs[2:], start=2):
        s = "<|" + m["role"] + "|>" + (m.get("reasoning_content") or "") + (m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            s += tc["function"]["name"] + tc["function"]["arguments"]
        if extra and i in extra:
            s += extra[i]
        parts.append(s)
    return SEP + SEP.join(parts)


def simulate(data, build_user, G, T_ST, chain_delta=None, label=""):
    """returns per-request rows (date, class, prompt, hit, out)"""
    cache = Cache()
    head = data["reqs"][0]
    st_prefix = head["msgs"][0]["content"] + SEP + head["tools"]
    old2new = {}
    rows = []
    for r in data["reqs"]:
        msgs = r["msgs"]; fresh = len(msgs) == 2
        old_user = msgs[1]["content"]
        if fresh:
            new_user = build_user(r)
            old2new[old_user] = new_user
        else:
            new_user = old2new.get(old_user)
            if new_user is None:
                new_user = build_user(r); data["unmapped"] += 1
        extra = chain_delta(r) if (chain_delta and not fresh) else None
        old_s = user_stream(msgs, old_user)
        new_s = user_stream(msgs, new_user, extra)
        prompt = r["prompt"] + (ctok(new_s) - ctok(old_s) if new_s != old_s else 0)
        s = st_prefix + new_s
        n = cache.best(s)
        if n >= len(st_prefix):
            tokp = T_ST + ctok(s[len(st_prefix):n])
        else:
            tokp = 0
        hit = int(min(tokp, prompt) // G * G)
        cache.add(s)
        if CACHE_OUTPUT:
            a = r["answer"]
            cache.add(s + SEP + "<|assistant|>" + (a.get("reasoning_content") or "") + (a.get("content") or "")
                      + "".join(tc["function"]["name"] + tc["function"]["arguments"] for tc in a.get("tool_calls") or []))
        rows.append(dict(date=day(r["t"]), cls="fresh" if fresh else "history", prompt=prompt, hit=hit,
                         out=r["out"], actual_hit=r["hit"], actual_prompt=r["prompt"]))
    return rows


def costs(rows, key="hit"):
    by = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0])
    for x in rows:
        for k in (x["date"], "all", x["cls"]):
            a = by[k]; a[0] += x[key]; a[1] += x["prompt"] - x[key]; a[2] += x["out"]; a[3] += 1
    return {k: dict(n=a[3], hit=a[0], miss=a[1], out=a[2], in_cost=a[0] * P_HIT + a[1] * P_MISS,
                    cost=a[0] * P_HIT + a[1] * P_MISS + a[2] * P_OUT) for k, a in by.items()}


def prep(run="V"):
    data = load_run(run)
    data["answer_of"] = {}
    for r in data["reqs"]:
        data["answer_of"][r["did"]] = r["answer"]  # last request's answer = the decision's final answer
    data["unmapped"] = 0
    data["date_of"] = {d: day(t) for d, (t, s) in data["dec_time"].items()}
    data["pos"] = {d: i for i, d in enumerate(data["dec_order"])}
    import bisect as _b
    data["seg_of"] = {d: _b.bisect_left(data["seg_marks"], s) for d, (t, s) in data["dec_time"].items()}
    return data


def layers_of(r):
    return {l["layer"]: l for l in json.loads(r["msgs"][1]["content"])}


FINAL = {"completed", "cancelled", "rejected", "failed", "concluded"}


def slim_progress(prog):
    """P2 estimate (research econ/slim.py): keep open tool calls, query answers, the newest round and later events"""
    events = prog["events"]
    req_times = [e["occurred_at"] for e in events if e["type"] == "tool_call"]
    last_t = max(req_times) if req_times else None
    keep = []
    for e in events:
        if e["type"] == "tool_call":
            r = e.get("result") or {}
            det = r.get("details") or {}
            if r.get("status") not in FINAL or "query" in det or e["occurred_at"] == last_t \
                    or r.get("occurred_at", "") > (last_t or ""):
                keep.append(e)
        elif last_t is None or e["occurred_at"] >= last_t:
            keep.append(e)
    return dict(prog, events=keep)


def make_builder(data, order, mem="new", persona="card", todo="orders", publish="decision", entry_len=145,
                 recent_cell=200, progress="actual"):
    pool = prose_pool(data)
    entries = {d: make_entry(d, data, entry_len) for d in data["dec_order"]}
    data["entries"] = entries
    first = layers_of(data["reqs"][0])["人设层"]
    card = {k: v for k, v in first.items() if k != "preferences"}

    def visible(r):
        k = data["pos"][r["did"]]; date = data["date_of"][r["did"]]
        out = []
        for d in data["dec_order"][:k]:
            if data["date_of"][d] != date:
                continue
            if publish == "second" and not data["dec_time"][d][0] < r["t"]:
                continue
            if publish == "segment" and not data["seg_of"][d] < data["seg_of"][r["did"]]:
                continue
            out.append(entries[d])
        return out

    def build(r):
        L = layers_of(r)
        if persona == "card":
            L["人设层"] = card
        if mem == "new":
            t0 = r["t"]
            midnight = int(datetime.fromtimestamp(t0, TZ).replace(hour=0, minute=0, second=0).timestamp())
            today = "[" + ",".join(visible(r)) + "]"
            m = ser({"layer": "记忆层", "可查": KECHA, "近三天": recent_block(midnight, pool, recent_cell)})
            L["记忆层"] = m[:-1] + ',"今天":' + today + "}"
        if todo in ("orders", "orders_tail"):
            rows = [o for o in data["orders"] if o["placed"] < r["seq"] < o["arrived"]]
            lst = [{"商品": o["name"], "下单": hhmm(o["created"]), "预计送达": hhmm(o["eta"]) if o["eta"] else None}
                   for o in rows[:6]]
            if len(rows) > 6:
                lst.append(f"另有 {len(rows) - 6} 笔在途")
            if todo == "orders":
                L["待办层"] = dict(L["待办层"], orders=lst)
            else:
                L["进度层"] = dict(L["进度层"], orders=lst)
        if progress in ("slim", "slim+segtail"):
            L["进度层"] = slim_progress(L["进度层"])
        if progress == "slim+segtail":
            k = data["pos"][r["did"]]; date = data["date_of"][r["did"]]
            tail = [entries[d] for d in data["dec_order"][:k] if data["date_of"][d] == date
                    and data["seg_of"][d] == data["seg_of"][r["did"]]]
            L["进度层"] = ser(L["进度层"])[:-1] + ',"本段条目":[' + ",".join(tail) + "]}"
        return "[" + ",".join(L[k] if isinstance(L[k], str) else ser(L[k]) for k in order) + "]"
    return build


def make_chain_delta(data):
    def delta(r):
        msgs = r["msgs"]; out = {}
        for i in range(2, len(msgs)):
            m = msgs[i]
            if m["role"] != "tool":
                continue
            nxt = msgs[i + 1]["role"] if i + 1 < len(msgs) else None
            try:
                payload = json.loads(m["content"])
            except Exception:
                continue
            if "result" in payload and nxt != "tool":
                owner = data["tool_owner"].get(m.get("tool_call_id"))
                if owner in data["entries"]:
                    out[i] = ',"今天新增":[' + data["entries"][owner] + "]"
        return out
    return delta


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "calibrate"
    data = prep("V")
    if mode == "calibrate":
        ident = lambda r: r["msgs"][1]["content"]
        for G in (64, 128):
            for T in range(5500, 5861, 40):
                rows = simulate(data, ident, G, T)
                fr = [x for x in rows if x["cls"] == "fresh"]; hi = [x for x in rows if x["cls"] == "history"]
                mae = sum(abs(x["hit"] - x["actual_hit"]) for x in rows) / len(rows)
                exact = sum(x["hit"] == x["actual_hit"] for x in rows)
                c = costs(rows); ca = costs(rows, "actual_hit")
                print(f"G={G} T_ST={T} MAE={mae:6.0f} exact={exact}/{len(rows)} fresh hit sim/act="
                      f"{sum(x['hit'] for x in fr)}/{sum(x['actual_hit'] for x in fr)} history="
                      f"{sum(x['hit'] for x in hi)}/{sum(x['actual_hit'] for x in hi)} decision cost sim/act "
                      f"${c['all']['cost']:.4f}/${ca['all']['cost']:.4f}")
