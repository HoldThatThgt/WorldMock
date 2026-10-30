import json, sys, datetime, zoneinfo
base=sys.argv[1]
tz=zoneinfo.ZoneInfo('Asia/Shanghai')
goods={}
for l in open(base+'/input/catalog/merchant-catalog.jsonl'):
    r=json.loads(l)
    for g in r.get('goods',[r]) if isinstance(r.get('goods'),list) else [r]:
        if 'goods_id' in g: goods[g['goods_id']]=g.get('name')
def t(x): return datetime.datetime.fromtimestamp(x,tz).strftime('%m-%d %H:%M')
for l in open(base+'/export/purchases.jsonl'):
    r=json.loads(l)
    if r.get('fact_kind')=='purchase':
        print('BUY',t(r['created_at']),r['category'],goods.get(r['goods_id'],r['goods_id']),r['total_price_fen'],r['app_id'])
