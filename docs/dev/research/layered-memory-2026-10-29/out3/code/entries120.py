"""Refined per-decision entry sizes under the 120字 per-entry cap (read-only; reuses windows_*.json from windows.py).

Adds a code start line per accepted start ("HH:MM 开始<短名>"), a rejection line ("HH:MM 提交的<tool>没有受理")
and a plan line ("HH:MM 改了今日计划"). Cap rule measured: text = sum of len() of all 核验 and 自述 strings; 核验 kept
whole; 自述 cut to fill the rest of 120 (items cut whole-prefix-first, last kept item truncated with '…').
Overhead measured by serialising a real entry skeleton.
"""
import json, re, sys, collections, statistics
START_SUFFIX = re.compile(r"（\d{1,2}月\d{1,2}日 \d{2}:\d{2} 起）")
LINE = 59
def skeleton(nv, ns):
    e = {"时刻": "12:28", "类别": "饮食"}
    if nv: e["核验"] = [""] * nv
    if ns: e["自述"] = [""] * ns
    return len(json.dumps(e, ensure_ascii=False, separators=(",", ":"))) + 1  # +1 list comma
def main(name):
    rows = json.load(open(f"windows_{name}.json"))
    out = collections.Counter(); per_day = collections.Counter(); per_day_n = collections.Counter()
    concl_full = concl = 0; label_survives = 0; sizes = []
    by_day_entries = collections.defaultdict(list)
    for r in rows:
        nv = r["nfacts"]; verified = nv * LINE
        tools = r["tools"]
        if r["rejected"]:
            nv += 1; verified += 6 + 4 + len(tools[0]) + 4
        elif set(tools) <= {"start_main_activity", "start_side_activity"}:
            nv += len(tools); verified += len(tools) * (6 + 2 + 12)  # 'HH:MM 开始' + ~12-char short name
        elif tools[0] == "revise_day_plan":
            nv += 1; verified += 12
        stated = r["stated"]; full = verified + stated
        room = max(0, 120 - verified)
        kept = min(stated, room)
        text = verified + kept
        size = text + skeleton(nv, 1 if kept else 0) + 3 * max(0, nv - 1) + 3 * max(0, r["ntexts"] - 1 if kept else 0)
        sizes.append(size)
        per_day[r["date"]] += size; per_day_n[r["date"]] += 1
        by_day_entries[r["date"]].append(size)
        if tools[0] == "conclude_activity":
            concl += 1
            concl_full += full <= 120
            label_survives += room > 0
        out["over120_before_cap"] += full > 120
        out["verified_over120"] += verified > 120
    res = {"entries": len(rows), "entry_chars_median": statistics.median(sizes), "entry_chars_p90": sorted(sizes)[int(.9*len(sizes))],
           "entry_chars_max": max(sizes), "conclude_decisions": concl, "conclude_entries_untruncated": concl_full,
           "conclude_entries_with_room_for_a_label": label_survives, **out,
           "today_chars_at_day_end": dict(sorted(per_day.items())), "entries_per_day": dict(sorted(per_day_n.items()))}
    # cumulative today size seen by each decision (prefix sum, entries visible = earlier decisions same day)
    seen = []
    for day, xs in by_day_entries.items():
        acc = 0
        for x in xs:
            seen.append(acc); acc += x
    res["today_chars_seen_by_decision_median"] = statistics.median(seen)
    res["today_chars_seen_by_decision_max"] = max(seen)
    return res
print(json.dumps({n: main(n) for n in ("V", "B")}, ensure_ascii=False, indent=1))
