"""Read-only: the 今天 list visible at V 10-12 11:50 (rule B, views rendered), taken from entries/v_1012_entries.json."""
import json, sys, os, re
here = os.path.dirname(os.path.abspath(__file__))
rows = json.load(open(os.path.join(here, "..", "entries", "v_1012_entries.json"), encoding="utf-8"))
cut = sys.argv[1] if len(sys.argv) > 1 else "11:50:00"
def adopt(e):
    """采用变更规格的两处写法：播报前缀写「进展·短名：」，被拒行不带原因。"""
    e = dict(e)
    if "自述" in e:
        e["自述"] = [re.sub(r"^(.+?)进行中：", r"进展·\1：", x, count=1) for x in e["自述"]]
    if "核验" in e:
        e["核验"] = [re.sub(r"^(被拒：.+?)（.*）$", r"\1", x) for x in e["核验"]]
    return e
visible = [adopt(r["条目"]) for r in rows if r["时间"] < cut]
dump = lambda x: json.dumps(x, ensure_ascii=False, separators=(",", ":"))
body = sum(len(s) for e in visible for k in ("核验", "自述") for s in e.get(k, []))
total = len(dump(visible))
json.dump(visible, open(os.path.join(here, "example1150.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=0)
print("entries", len(visible), "today_chars", total, "content_chars", body, "keys_chars", total - body)
for e in visible: print(dump(e))
