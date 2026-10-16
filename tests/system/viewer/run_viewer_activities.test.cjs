'use strict';

/** @brief 活动日历只消费真实活动事件，验证当地日期、实际区间及来源原文。 */
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const {viewer, event, tableRows} = require('./run_viewer_harness.cjs');
const at = value => Date.parse(value)/1000;

/** @brief 一条受理与若干结算记录，间隔为空时就是尚未结算的活动。 */
function activity(id, start, intervals, options={}){
  const snapshot = {activity_id:id, kind:options.app?'interact':'sleep', location_id:'home', started_at:start,
    data:options.app?{action:'interact',app_id:options.app,content_id:options.content}: {}, status:'active'};
  const envelope = {activity_id:id,agent_id:options.agent??1};
  return [event('action_accepted',{status:'in_progress',activity:snapshot},{...envelope,occurred_at:start}),
    ...intervals.map(([from,to])=>event('activity_progress',{
      activity:{...snapshot,status:'completed',ended_at:to},interval_start:from,interval_end:to},
    {...envelope,occurred_at:to}))];
}

/** @brief 日历主体不含保持完整数据的折叠表格。 */
const calendar = view => view.get('#act-body').innerHTML.split('<details class="calendar-table"')[0];
const date = view => /id="calendar-date"[^>]*value="([^"]+)"/.exec(calendar(view))[1];
const bars = view => [...calendar(view).matchAll(/class="act-lane-bar" style="left:([^%]+)%;width:([^%]+)%/g)]
  .map(match=>({left:Number(match[1]),width:Number(match[2])}));
const choose = (view,day) => view.get('#calendar-date').onchange({target:{value:day}});
const ordered = rows => rows.sort((left,right)=>left.occurred_at-right.occurred_at);

test('calendar clips cross-day intervals, keeps empty days and retains the full activity table', async t=>{
  const start = at('2026-01-05T23:50:00+08:00'), end = at('2026-01-06T00:20:00+08:00');
  const later = at('2026-01-08T08:00:00+08:00');
  const view = await viewer([...activity('night',start,[[start,end]]),...activity('later',later,[[later,later+600]])]);
  t.after(()=>view.close());
  view.activities();
  assert.equal(date(view),'2026-01-05');
  assert.deepEqual(bars(view),[{left:85800/86400*100,width:600/86400*100}]);
  assert.match(calendar(view),/23:50:00–次日 00:00:00 · 当天已结算 10分/);
  view.get('#calendar-next').onclick();
  assert.equal(date(view),'2026-01-06');
  assert.deepEqual(bars(view),[{left:0,width:1200/86400*100}]);
  assert.match(calendar(view),/00:00:00–00:20:00 · 当天已结算 20分/);
  view.get('#calendar-next').onclick();
  assert.equal(date(view),'2026-01-07');
  assert.match(calendar(view),/当天没有活动记录/);
  assert.equal(tableRows(view.get('#act-body').innerHTML,'act-table').length,2);
  view.get('#calendar-prev').onclick();
  assert.equal(date(view),'2026-01-06');
  assert.match(calendar(view),/Asia\/Shanghai/);
});

test('calendar aligns hourly grid lines and ticks on narrow and wide 23-hour, 24-hour and 25-hour days', async t=>{
  const css=readFileSync(path.join(__dirname,'../../../tools/run-viewer-activities.css'),'utf8');
  assert.match(css,/var\(--line\) var\(--calendar-hour\)\)/);
  assert.match(css,/\.calendar-scroll\{container-type:inline-size;/);
  assert.match(css,/\.calendar-meta\{[^}]*width:min\(800px,100cqw\)/);
  assert.equal((css.match(/\.calendar-meta\{/g)||[]).length,1);
  for (const [day,start,end,hours,missing] of [
    ['2026-03-07','2026-03-07T00:00:00-05:00','2026-03-08T00:00:00-05:00',24,null],
    ['2026-03-08','2026-03-08T00:00:00-05:00','2026-03-09T00:00:00-04:00',23,'02:00'],
    ['2026-11-01','2026-11-01T00:00:00-04:00','2026-11-02T00:00:00-05:00',25,null],
  ]){
    const view = await viewer(activity(day,at(start),[[at(start),at(end)]]),{timezone:'America/New_York'});
    t.after(()=>view.close());
    view.activities();
    assert.equal(date(view),day);
    assert.deepEqual(bars(view),[{left:0,width:100}]);
    assert.match(calendar(view),new RegExp(`--calendar-hours:${hours*64}px`));
    const hour=Number(/--calendar-hour:([^%]+)%/.exec(calendar(view))[1]);
    const axis = calendar(view).split('<div class="calendar-axis">')[1].split('</div>')[0];
    const ticks=[...axis.matchAll(/style="left:([^%]+)%"/g)].map(match=>Number(match[1]));
    for (const containerWidth of [1000,2400]){
      const trackWidth=Math.max(hours*64,containerWidth-280);
      assert.ok(trackWidth*hour/100>=64-1e-9);
      for (const [index,tick] of ticks.entries()){
        assert.ok(Math.abs(trackWidth*hour/100*index-trackWidth*tick/100)<1e-9);
      }
    }
    if (missing) assert.ok(!axis.includes(`>${missing}</span>`));
    else assert.equal((axis.match(/>01:00<\/span>/g)||[]).length,hours===25?2:1);
    assert.match(calendar(view),new RegExp(`当天已结算 ${hours}小时`));
  }
});

test('an export with recorded time but no activities still offers its local dates', async t=>{
  const start=at('2026-01-05T06:00:00+08:00'),end=at('2026-01-07T22:00:00+08:00');
  const view=await viewer([event('run_started',{}, {occurred_at:start}),
    event('run_ended',{}, {occurred_at:end})]);
  t.after(()=>view.close());
  view.activities();
  assert.equal(date(view),'2026-01-05');
  assert.match(calendar(view),/min="2026-01-05" max="2026-01-07"/);
  assert.match(calendar(view),/当天没有活动记录/);
  assert.deepEqual(tableRows(view.get('#act-body').innerHTML,'act-table'),[]);
  view.get('#calendar-next').onclick();
  assert.equal(date(view),'2026-01-06');
  choose(view,'2026-01-07');
  assert.match(calendar(view),/id="calendar-next" disabled/);
  view.get('#calendar-next').onclick();
  assert.equal(date(view),'2026-01-07');
  assert.match(calendar(view),/当天没有活动记录/);
});

test('calendar preserves overlapping intervals, pause gaps and readable five-second activities', async t=>{
  const start=at('2026-01-05T08:00:00+08:00');
  const view=await viewer(ordered([
    ...activity('paused',start,[[start,start+300],[start+1200,start+1500]]),
    ...activity('parallel',start+120,[[start+120,start+180]]),
    ...activity('short',start+1800,[[start+1800,start+1805]]),
    ...activity('pending',start+2400,[]),
  ]));
  t.after(()=>view.close());
  view.activities();
  assert.equal(bars(view).length,4);
  assert.deepEqual(bars(view).map(bar=>bar.width),[300,300,60,5].map(seconds=>seconds/86400*100));
  assert.match(calendar(view),/08:00:00–08:05:00、08:20:00–08:25:00/);
  assert.match(calendar(view),/08:30:00–08:30:05 · 当天已结算 5秒/);
  assert.match(calendar(view),/尚无结算区间/);
  assert.equal((calendar(view).match(/查看活动与事件/g)||[]).length,4);
  assert.equal((calendar(view).match(/class="act-lane-label"/g)||[]).length,4);
});

test('person changes and new exports reset the date while table sorting preserves it', async t=>{
  const one=at('2026-01-05T08:00:00+08:00'),two=at('2026-01-08T09:00:00+08:00');
  const view=await viewer([...activity('one',one,[[one,one+60]]),...activity('two',two,[[two,two+60]],{agent:2})]);
  t.after(()=>view.close());
  view.activities();
  choose(view,'2026-01-06');
  view.get('#activity-table').ontoggle({target:{open:true}});
  view.run("AT.sortKey='kind'; AT.sortDir=-1");
  view.activities();
  assert.equal(date(view),'2026-01-06');
  assert.match(view.get('#act-body').innerHTML,/<details class="calendar-table" id="activity-table" open>/);
  view.get('#agent-filter').value='2';
  view.activities();
  assert.equal(date(view),'2026-01-08');
  assert.equal(bars(view).length,1);
  view.get('#agent-filter').value='1';
  view.activities();
  assert.equal(date(view),'2026-01-05');
  const next=at('2026-04-20T11:00:00+09:00');
  await view.reload(activity('new',next,[[next,next+60]]),{timezone:'Asia/Tokyo'});
  view.activities();
  assert.equal(date(view),'2026-04-20');
  assert.match(calendar(view),/Asia\/Tokyo/);
});

test('cross-midnight continuous uses keep all members and link only to their own recorded events', async t=>{
  const start=at('2026-01-05T23:50:00+08:00'),midnight=at('2026-01-06T00:00:00+08:00');
  const view=await viewer(ordered([
    ...activity('first',start,[[start,midnight]],{app:'bilibili',content:'c-article'}),
    event('observation',{unrelated:'must not be linked'},{occurred_at:start+60}),
    ...activity('second',midnight,[[midnight,midnight+600]],{app:'bilibili',content:'c-image'}),
  ]));
  t.after(()=>view.close());
  view.activities();
  choose(view,'2026-01-06');
  assert.equal(bars(view).length,1);
  assert.match(calendar(view),/使用哔哩哔哩《测试文章》《测试图文》/);
  view.run('ActivityCalendar.select(0)');
  const detail=view.get('#activity-detail').innerHTML;
  const sources=[...detail.matchAll(/data-calendar-event="(\d+)"/g)].map(match=>Number(match[1]));
  assert.deepEqual(sources,[1,3,4,5]);
  assert.match(detail,/2026\/01\/05 23:50:00/);
  assert.match(detail,/2026\/01\/06 00:10:00/);
  await view.run('ActivityCalendar.readEvent(5)');
  const actual=JSON.parse(view.get('#calendar-event-body').textContent);
  assert.equal(actual.activity_id,'second');
  assert.equal(actual.event_seq,5);
  assert.equal(view.state.bodyCache.has(2),false);
});

test('event read failures are visible and a previous day read cannot overwrite the next selection', async t=>{
  const start=at('2026-01-05T23:50:00+08:00'),end=start+1200;
  const view=await viewer(activity('night',start,[[start,end]]));
  t.after(()=>view.close());
  view.activities();
  view.run('ActivityCalendar.select(0)');
  let release;
  const original=view.state.eventsFile;
  view.state.eventsFile={slice(...args){return {async text(){await new Promise(resolve=>{release=resolve;});
    return original.slice(...args).text();}};}};
  const pending=view.run('ActivityCalendar.readEvent(1)');
  choose(view,'2026-01-06');
  view.run('ActivityCalendar.select(0)');
  view.get('#calendar-event-body').textContent='new selection';
  release();
  await pending;
  assert.equal(view.get('#calendar-event-body').textContent,'new selection');
  view.state.eventsFile={slice(){throw new Error('Selected event was removed');}};
  await view.run('ActivityCalendar.readEvent(2)');
  assert.equal(view.get('#calendar-event-body').textContent,'读取失败：Selected event was removed');
  assert.ok(view.errors.includes('Activity event body could not be read: 2 Selected event was removed'));
});
