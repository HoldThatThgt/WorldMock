"""Read-only: collect distinct settled sentences shown in 记忆层.earlier_today of fresh V/B requests; classify forms and lengths."""
import sqlite3, json, sys, re, statistics as st, collections
run=sys.argv[1]
c=sqlite3.connect(f"file:{run}/run/footprints.sqlite?mode=ro",uri=True)
seen=collections.OrderedDict(); notes=collections.OrderedDict(); entry_sizes=[]
for seq,body in c.execute("select event_seq, body from events where json_extract(body,'$.event_type')='model_request' order by event_seq"):
    b=json.loads(body); msgs=b['data']['request']['messages']
    if len(msgs)!=2 or not msgs[0]['content'].startswith('{"layer":"系统层"'): continue
    layers=json.loads(msgs[1]['content'])
    mem=next(l for l in layers if l['layer']=='记忆层')
    for e in mem['earlier_today']:
        entry_sizes.append(len(json.dumps(e,ensure_ascii=False,separators=(',',':'))))
        for s in e['settled']: seen.setdefault(s,0); seen[s]+=1
        for n in e['self_notes']: notes.setdefault(n['text'],n['activity'])
def form(s):
    if re.match(r'^\d\d:\d\d 买了',s) or re.match(r'^\d+月\d+日 \d\d:\d\d 买了',s): return 'purchase'
    if '：已送到' in s or '送达' in s or re.match(r'^(\d+月\d+日 )?\d\d:\d\d .*（.*购买）：',s): return 'delivery'
    if '办成' in s: return 'errand'
    return 'activity'
by=collections.defaultdict(list)
for s in seen: by[form(s)].append(s)
for k,v in by.items():
    L=[len(x) for x in v]
    print(k,len(v),'median',st.median(L),'max',max(L),'mean',round(sum(L)/len(L),1))
    for x in v[:6]: print('   ',len(x),x)
print('distinct self_notes',len(notes))
