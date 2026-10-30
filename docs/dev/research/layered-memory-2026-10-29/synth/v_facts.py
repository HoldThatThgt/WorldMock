"""Read-only: facts used by the final proposal (10-12 11:50 prompt sizes, groceries consume/reset, dinner self-reports)."""
import sqlite3, json, sys, datetime, zoneinfo
run = sys.argv[1]
tz = zoneinfo.ZoneInfo('Asia/Shanghai')
t = lambda x: datetime.datetime.fromtimestamp(x, tz).strftime('%m-%d %H:%M:%S')
c = sqlite3.connect(f'file:{run}/run/footprints.sqlite?mode=ro', uri=True)
done = False
for seq, eid, body in c.execute("select event_seq,event_id,body from events"):
    b = json.loads(body)
    et = b.get('event_type'); d = b.get('data', {})
    if et == 'model_request' and not done and t(b['occurred_at']).startswith('10-12 11:50'):
        msgs = d.get('request', {}).get('messages', [])
        if len(msgs) >= 2:
            layers = json.loads(msgs[1]['content'])
            print('PROMPT 10-12 11:50', {L['layer']: len(json.dumps(L, ensure_ascii=False, separators=(',', ':'))) for L in layers})
            done = True
    if et == 'environment_event' and d.get('kind') == 'callback_effect' and d.get('state') == 'groceries':
        print('GROCERIES', t(b['occurred_at']), d.get('type'), d.get('before'), '->', d.get('after'), d.get('goods_id') or d.get('content_id'))
    if et == 'action_requested' and d.get('tool_name') == 'conclude_activity':
        for it in d['request'].get('items', []):
            if '做饭' in it.get('activity_name', ''):
                print('DINNER NOTE', t(b['occurred_at']), it['summary'][:90])
