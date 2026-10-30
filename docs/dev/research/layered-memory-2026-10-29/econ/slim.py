"""Read-only: estimate an 'open threads only' 进度层 on fresh decision requests."""
import json, sqlite3, sys, statistics
from collections import Counter
path = sys.argv[1]
con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
rows = con.execute("select event_seq, body from events where json_extract(body,'$.event_type')='model_request' order by event_seq")
def L(x): return len(json.dumps(x, ensure_ascii=False, separators=(",", ":")))
FINAL = {"completed", "cancelled", "rejected", "failed", "concluded"}
st = Counter(); old_sizes = []; new_sizes = []; tools_sizes = []; persona = []; mem = []; todo = []; env = []
for seq, body in rows:
    ev = json.loads(body); msgs = ev["data"]["request"]["messages"]
    if not msgs[0]["content"].startswith('{"layer":"系统层"') or len(msgs) != 2: continue
    layers = json.loads(msgs[1]["content"])
    by = {l["layer"]: l for l in layers}
    prog = by["进度层"]
    now = ev["occurred_at"]
    events = prog["events"]
    # last tool_call request time = previous decision moment
    req_times = [e["occurred_at"] for e in events if e["type"] == "tool_call"]
    last_t = max(req_times) if req_times else None
    keep = []
    for e in events:
        if e["type"] == "tool_call":
            r = e.get("result") or {}
            s = r.get("status"); st[s] += 1
            det = r.get("details") or {}
            if s not in FINAL:
                keep.append(e)  # still running / pending
            elif "query" in det:
                keep.append(e)  # query answers kept in session
            elif e["occurred_at"] == last_t or r.get("occurred_at", "") > (last_t or ""):
                keep.append(e)  # newest round / newly settled since last decision
        else:
            if last_t is None or e["occurred_at"] >= last_t:
                keep.append(e)
    new = dict(prog, events=keep)
    old_sizes.append(L(prog)); new_sizes.append(L(new))
    tools_sizes.append(L(by["工具层"])); persona.append(L(by["人设层"])); mem.append(L(by["记忆层"]))
    todo.append(L(by["待办层"])); env.append(L(by["环境层"]))
def q(v):
    v = sorted(v); return (statistics.median(v), v[int(0.9 * (len(v) - 1))], v[-1])
print("status", st.most_common())
for name, v in [("prog old", old_sizes), ("prog open-only", new_sizes), ("tool", tools_sizes), ("persona", persona), ("memory", mem), ("todo", todo), ("env", env)]:
    print(name, q(v))
