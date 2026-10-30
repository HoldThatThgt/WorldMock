"""Read-only: compact event dump for a time window."""
import sqlite3, json, datetime, zoneinfo, sys
z=zoneinfo.ZoneInfo("Asia/Shanghai")
run=sys.argv[1]; a=sys.argv[2]; b_=sys.argv[3]
c=sqlite3.connect(f"file:{run}/run/footprints.sqlite?mode=ro",uri=True)
def ts(s): return datetime.datetime.fromisoformat(s).replace(tzinfo=z).timestamp()
t0,t1=ts(a),ts(b_)
for seq,eid,body in c.execute("select event_seq,event_id,body from events"):
    b=json.loads(body)
    if not (t0<=b['occurred_at']<=t1): continue
    et=b['event_type']; d=b['data']
    if et=='checkpoint_created': continue
    if et=='environment_event' and d.get('kind')=='body_settlement': continue
    tm=datetime.datetime.fromtimestamp(b['occurred_at'],z).strftime('%m-%d %H:%M:%S')
    if et=='model_request':
        msgs=d['request']['messages']; s=f"nmsg={len(msgs)} call={d.get('call_id')}"
    elif et=='model_response':
        s=f"call={d.get('call_id')}"
    elif et=='activity_progress':
        a_=d.get('activity',{}); s=f"{a_.get('name')} status={a_.get('status')} bt={a_.get('boundary_type')} content={json.dumps(d.get('content'),ensure_ascii=False)[:150]}"
    elif et=='observation':
        s=json.dumps({k:d[k] for k in d if k not in('layers','prompt')},ensure_ascii=False)[:300]
    elif et=='memory_updated':
        s=json.dumps(d,ensure_ascii=False)[:250]
    else:
        s=json.dumps(d,ensure_ascii=False)[:700]
    print(seq,tm,et,(b.get('decision_id') or '')[-30:],s)
