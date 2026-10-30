import sqlite3, json, datetime, zoneinfo, collections, sys
tz=zoneinfo.ZoneInfo("Asia/Shanghai")
path=sys.argv[1]
db=sqlite3.connect(f"file:{path}?mode=ro", uri=True)
work_words=("功能开发","代码评审","需求评审","会议分析","处理消息","钉钉","驾车前往练秋湖")
cnt=collections.Counter(); chars=collections.Counter()
for seq,body in db.execute("select event_seq,body from events order by event_seq"):
    b=json.loads(body)
    if b["event_type"]!="action_requested": continue
    r=b["data"].get("request",{})
    for it in r.get("items",[]) if isinstance(r,dict) else []:
        for v in it.get("views",[]) or []:
            d=datetime.datetime.fromtimestamp(b["occurred_at"],tz).strftime("%m-%d")
            a=it.get("activity_name","")
            if v["type"]=="relationship": cat="关系"
            elif a.startswith("购买") or "做饭" in a or "吃午饭" in a: cat="饮食"
            elif any(w in a for w in work_words) or ("驾车" in a): cat="工作"
            else: cat="生活"
            cnt[(d,cat)]+=1
            chars[(d,cat)]+=len(json.dumps({"对象":v["about"],"看法":v["text"],"记于":"2026-10-11"},ensure_ascii=False,separators=(",",":")))+1
for k in sorted(cnt): print(k,cnt[k],chars[k])
