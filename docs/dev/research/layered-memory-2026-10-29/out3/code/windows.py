"""Per-decision window measurement for the 'one 今天 entry per decision' ruling (read-only on V/B).

Approximations (stated in the report):
- 核验 line length: each settled fact counted as 59 chars (brief F16 average), not rendered.
- 自述 items rendered as the proposal's prefixes: "<短名>：summary", "记住：text" (non-routine memories only),
  "看法·<about>：text", "进展·<短名>：summary" (report_status), "正文：self_note".
- JSON overhead per entry: {"时刻":"HH:MM","类别":"xx","核验":[..],"自述":[..]} ~= 36 chars + 3 per string.
"""
import collections
import json
import re
import sqlite3
import statistics
import sys

START_SUFFIX = re.compile(r"（\d{1,2}月\d{1,2}日 \d{2}:\d{2} 起）")
ENDED = {"completed", "cancelled", "failed", "truncated"}
TZ = 8 * 3600
LINE = 59


def day(epoch):
    import datetime
    return datetime.datetime.utcfromtimestamp(epoch + TZ).date().isoformat()


def load(run):
    con = sqlite3.connect(f"file:{run}/run/footprints.sqlite?mode=ro", uri=True)
    rows = con.execute("select event_seq, body from events order by event_seq").fetchall()
    return [(seq, json.loads(body)) for seq, body in rows]


def snapshots(b):
    t, d = b["event_type"], b["data"]
    out = []
    if t in ("activity_progress", "environment_event") and isinstance(d.get("activity"), dict):
        out.append(d["activity"])
    if t == "action_result" and isinstance(d.get("data"), dict):
        out.extend(d["data"].get("activities", []) or [])
    return out


def main(run):
    events = load(run)
    by_seq = {seq: b for seq, b in events}
    last = events[-1][0]
    # decisions
    decisions = collections.OrderedDict()
    for seq, b in events:
        if b["event_type"] == "action_requested" and b.get("agent_id") == 1:
            decisions.setdefault(b["decision_id"], []).append((seq, b))
    # commit seq per decision
    first_commit = {}
    for seq, b in events:
        if b["event_type"] in ("action_accepted", "action_result", "action_rejected", "action_requested"):
            pass
        if b["event_type"] in ("action_accepted", "action_result", "action_rejected") and b.get("decision_id") in decisions:
            first_commit.setdefault(b["decision_id"], seq)
    def commit_end(seq):
        pair = (by_seq[seq]["state_version_before"], by_seq[seq]["state_version_after"])
        s = seq
        while s + 1 <= last and (by_seq[s + 1]["state_version_before"], by_seq[s + 1]["state_version_after"]) == pair:
            s += 1
        return s
    # fresh / chain
    requests = collections.defaultdict(list)
    for seq, b in events:
        if b["event_type"] == "model_request" and b["data"].get("phase") == "request" and b["data"].get("purpose") is None:
            tools = [t["function"]["name"] for t in b["data"]["request"].get("tools", [])]
            if "start_main_activity" in tools:
                requests[b["decision_id"]].append(len(b["data"]["request"]["messages"]))
    # earliest ending per activity
    ending_seq = {}
    kind_of = {}
    for seq, b in events:
        if b.get("agent_id") != 1:
            continue
        for snap in snapshots(b):
            kind_of[snap["activity_id"]] = snap.get("kind")
            if snap.get("status") in ENDED and snap["activity_id"] not in ending_seq:
                ending_seq[snap["activity_id"]] = seq
    endings_at = collections.Counter(ending_seq.values())
    rows = []
    prev_c = 0
    prev_t = None
    for decision_id, reqs in decisions.items():
        if decision_id not in first_commit:
            continue
        c = commit_end(first_commit[decision_id])
        t = reqs[0][1]["occurred_at"]
        tools = [b["data"]["tool_name"] for _, b in reqs]
        rejected = any(by_seq[s]["event_type"] == "action_rejected" and by_seq[s]["decision_id"] == decision_id
                       for s in range(first_commit[decision_id], c + 1))
        facts = {"end": 0, "paid": 0, "delivery": 0, "errand": 0}
        midnight_split = 0
        for s in range(prev_c + 1, c + 1):
            b = by_seq[s]
            if b.get("agent_id") not in (1, None):
                continue
            n_end = endings_at.get(s, 0)
            facts["end"] += n_end
            if b["event_type"] == "action_result" and b["data"].get("status") == "completed":
                orders = (b["data"].get("data") or {}).get("orders", []) or []
                facts["paid"] += sum(1 for o in orders if o.get("paid_at") is not None
                                     and (o.get("fulfillment"), o.get("status")) != ("station_pickup", "delivered"))
            if b["event_type"] == "environment_event" and b["visibility"] == "agent":
                k = b["data"].get("kind")
                if k == "delivery_arrival":
                    facts["delivery"] += sum(1 for o in b["data"].get("orders", []) if o.get("agent_id") == 1)
                if k == "errand_completed":
                    facts["errand"] += 1
            if prev_t is not None and day(b["occurred_at"]) != day(t):
                midnight_split = 1
        texts = []
        for _, b in reqs:
            tool, req = b["data"]["tool_name"], b["data"]["request"]
            if tool in ("conclude_activity", "pause_activity", "report_status"):
                for item in req.get("items", []):
                    short = START_SUFFIX.sub("", item["activity_name"], count=1)
                    if item.get("summary", "").strip():
                        prefix = "进展·" if tool == "report_status" else ""
                        texts.append(f"{prefix}{short}：{item['summary']}")
                    for m in item.get("memories", []) or []:
                        if m.get("type") != "routine" and m["text"].strip():
                            texts.append(f"记住：{m['text']}")
                    for v in item.get("views", []) or []:
                        texts.append(f"看法·{v.get('about')}：{v['text']}")
        note = reqs[0][1]["data"].get("self_note")
        if note:
            texts.append(f"正文：{note}")
        nfacts = sum(facts.values())
        verified = nfacts * LINE
        stated = sum(len(x) for x in texts)
        fresh = bool(requests[decision_id]) and requests[decision_id][0] == 2
        rows.append({"decision_id": decision_id, "t": t, "date": day(t), "tools": tools, "rejected": rejected,
                     "facts": facts, "nfacts": nfacts, "verified": verified, "stated": stated, "ntexts": len(texts),
                     "texts": texts, "fresh": fresh, "midnight_split": midnight_split, "c": c, "prev_c": prev_c,
                     "self_note": bool(note)})
        prev_c, prev_t = c, t
    return rows


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def report(name, rows):
    out = {"run": name, "decisions": len(rows)}
    out["fresh"] = sum(r["fresh"] for r in rows)
    out["chain"] = len(rows) - out["fresh"]
    per_day = collections.Counter(r["date"] for r in rows)
    fresh_day = collections.Counter(r["date"] for r in rows if r["fresh"])
    out["per_day"] = dict(sorted(per_day.items()))
    out["fresh_per_day"] = dict(sorted(fresh_day.items()))
    # classification
    cls = collections.Counter()
    for r in rows:
        tools = set(r["tools"])
        if r["rejected"]:
            key = "rejected"
        elif tools <= {"start_main_activity", "start_side_activity"}:
            key = "start"
        else:
            key = r["tools"][0]
        cls[(key, "facts" if r["nfacts"] else "nofacts", "note" if r["self_note"] else "nonote")] += 1
    out["classes"] = {"|".join(k): v for k, v in sorted(cls.items())}
    raw = [r["verified"] + r["stated"] for r in rows]
    out["raw_entry_chars"] = {"median": statistics.median(raw), "p90": pct(raw, 0.9), "max": max(raw),
                              "mean": round(statistics.mean(raw), 1)}
    out["entries_over_120"] = sum(1 for x in raw if x > 120)
    out["verified_only_over_120"] = sum(1 for r in rows if r["verified"] > 120)
    out["nfacts_dist"] = dict(sorted(collections.Counter(r["nfacts"] for r in rows).items()))
    out["stated_only_over_120"] = sum(1 for r in rows if r["stated"] > 120)
    out["empty_entries"] = sum(1 for r in rows if r["nfacts"] == 0 and r["ntexts"] == 0)
    # day totals with cap: 120 text + overhead 36 + 3 per string (cap applies to texts; overhead fixed)
    capped_day = collections.Counter()
    raw_day = collections.Counter()
    for r in rows:
        strings = r["nfacts"] + r["ntexts"]
        overhead = 36 + 3 * max(1, strings)
        capped_day[r["date"]] += min(120, r["verified"] + r["stated"]) + overhead
        raw_day[r["date"]] += r["verified"] + r["stated"] + overhead
    out["day_chars_capped120"] = dict(sorted(capped_day.items()))
    out["day_chars_uncapped"] = dict(sorted(raw_day.items()))
    # chains: length of runs of consecutive non-fresh decisions after a fresh head
    runs, cur = [], 0
    for r in rows:
        if r["fresh"]:
            if cur:
                runs.append(cur)
            cur = 0
        else:
            cur += 1
    if cur:
        runs.append(cur)
    out["chain_runs"] = dict(sorted(collections.Counter(runs).items()))
    out["midnight_split_windows"] = sum(r["midnight_split"] for r in rows)
    texts = [len(x) for r in rows for x in r["texts"]]
    out["stated_items"] = {"n": len(texts), "over_120": sum(1 for x in texts if x > 120),
                           "median": statistics.median(texts), "max": max(texts)}
    return out


if __name__ == "__main__":
    results = {}
    for name, run in (("V", sys.argv[1]), ("B", sys.argv[2])):
        rows = main(run)
        results[name] = report(name, rows)
        json.dump(rows, open(f"{sys.argv[3]}/windows_{name}.json", "w"), ensure_ascii=False, indent=0)
    print(json.dumps(results, ensure_ascii=False, indent=1))
    json.dump(results, open(f"{sys.argv[3]}/windows_summary.json", "w"), ensure_ascii=False, indent=1)
