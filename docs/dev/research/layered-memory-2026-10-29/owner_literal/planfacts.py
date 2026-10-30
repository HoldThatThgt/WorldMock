import sqlite3, json, re
base="/Users/atishoo/Project/WorldMock-Dataset/execution/20261029T023604Z-white-collar-food-variety/run/footprints.sqlite"
db=sqlite3.connect(f"file:{base}?mode=ro",uri=True)
FOOD=re.compile(r"吃|饭|面|食材包|咖啡|奶茶|拿铁|美式|外卖|餐|粥|包子|饮|茶|零食|水果|牛奶|豆浆|饼|粉|买了")
WORK=re.compile(r"钉钉|打卡|开发|评审|会议|需求|缺陷|消息与收尾|工作")
for (b,) in db.execute("select body from events where json_extract(body,'$.event_type')='model_request' and json_extract(body,'$.data.call_id') like 'plan-day:%:attempt:1' order by event_seq"):
    e=json.loads(b); u=e["data"]["request"]["messages"][-1]["content"]
    try: p=json.loads(u)
    except Exception: continue
    res=p.get("动作结果",[]); con=p.get("收尾记录",[])
    cats={"food":[0,0],"work":[0,0],"life":[0,0]}
    for line in res:
        k="food" if FOOD.search(line) else "work" if WORK.search(line) else "life"
        cats[k][0]+=1; cats[k][1]+=len(line)
    cs=sum(len(r.get("收尾说明",""))+sum(len(x) for x in r.get("想记住的",[])) for r in con)
    print(p.get("计划日期"), p.get("回顾时段"), "results",len(res),sum(len(x) for x in res), cats, "concl",len(con),cs)
    if p.get("计划日期")=="2026-10-11":
        for l in res: print("   ",l)
