"""Which agent-visible progress-layer world events are committed inside a decision commit (seq in [first result, C_k])
versus during time advance; and what follows rejected-only decisions (chain or fresh). Read-only."""
import json, sys, collections
sys.path.insert(0, ".")
from windows import load
def run(path):
    events = load(path); by = {s: b for s, b in events}; last = events[-1][0]
    rows = json.load(open(sys.argv[2] + "/windows_" + sys.argv[3] + ".json"))
    spans = [(r["c"], r) for r in rows]
    # commit spans: from first action_accepted/result/rejected of decision to c
    first = {}
    for s, b in events:
        if b["event_type"] in ("action_accepted", "action_result", "action_rejected") and b.get("decision_id"):
            first.setdefault(b["decision_id"], s)
    inside = set()
    for r in rows:
        inside.update(range(first[r["decision_id"]], r["c"] + 1))
    kinds = collections.Counter()
    for s, b in events:
        if b["event_type"] == "environment_event" and b["visibility"] == "agent" and b.get("agent_id") == 1:
            k = b["data"].get("kind")
            if k in ("app_push", "callback_reminder", "balance_decreased", "errand_reminded", "errand_completed", "delivery_arrival"):
                kinds[(k, "in_commit" if s in inside else "time_advance")] += 1
    print(dict(kinds))
    # what follows a rejected decision
    for i, r in enumerate(rows):
        if r["rejected"] and i + 1 < len(rows):
            n = rows[i + 1]
            print("rejected", r["decision_id"][-30:], r["tools"], "next fresh" if n["fresh"] else "next chain", n["t"] - r["t"], "s later")
run(sys.argv[1])
