"""Short activity labels (START_SUFFIX stripped) that name two different activities on the same day in V/B
conclude/pause/report_status items — these become ambiguous if labels are unique only per entry (read-only)."""
import json, re, sys, collections
sys.path.insert(0, ".")
from windows import load, day
START_SUFFIX = re.compile(r"（\d{1,2}月\d{1,2}日 \d{2}:\d{2} 起）")
D = "/Users/atishoo/Project/WorldMock-Dataset/execution/"
for name, run in (("V", D + "20261029T023604Z-white-collar-food-variety"), ("B", D + "20261029T023604Z-white-collar-food-baseline")):
    fulls = collections.defaultdict(set)
    for seq, b in load(run):
        if b["event_type"] == "action_requested" and b["data"]["tool_name"] in ("conclude_activity", "pause_activity", "report_status"):
            for item in b["data"]["request"].get("items", []):
                full = item["activity_name"]
                fulls[(day(b["occurred_at"]), START_SUFFIX.sub("", full, count=1))].add(full)
    amb = {k: v for k, v in fulls.items() if len(v) > 1}
    print(name, "day-labels", len(fulls), "ambiguous within a day", len(amb), "e.g.", list(amb.items())[:3])
