'use strict';

/** @brief 活动日历只消费真实活动事件，验证当地日期、实际区间及来源原文。 */
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const {viewer, event, tableRows, withCatalog} = require('./run_viewer_harness.cjs');
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
const select = (view,index) => view.get('#calendar-activity').onchange({target:{value:String(index)}});
const zoom = (view,span) => view.get('#calendar-zoom').onchange({target:{value:String(span)}});
const detail = view => view.get('#activity-detail').innerHTML;
const laneCount = view => Number(/data-lanes="(\d+)"/.exec(calendar(view))[1]);

test('calendar clips cross-day intervals, keeps empty days and retains the full activity table', async t=>{
  const start = at('2026-01-05T23:50:00+08:00'), end = at('2026-01-06T00:20:00+08:00');
  const later = at('2026-01-08T08:00:00+08:00');
  const view = await viewer([...activity('night',start,[[start,end]]),...activity('later',later,[[later,later+600]])]);
  t.after(()=>view.close());
  view.activities();
  assert.equal(date(view),'2026-01-05');
  assert.deepEqual(bars(view),[{left:85800/86400*100,width:600/86400*100}]);
  select(view,0);
  assert.match(detail(view),/23:50:00–次日 00:00:00 · 当天已结算 10分/);
  view.get('#calendar-next').onclick();
  assert.equal(date(view),'2026-01-06');
  assert.deepEqual(bars(view),[{left:0,width:1200/86400*100}]);
  select(view,0);
  assert.match(detail(view),/00:00:00–00:20:00 · 当天已结算 20分/);
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
  assert.match(css,/var\(--line\) var\(--calendar-step\)\)/);
  assert.match(css,/\.calendar-scroll\{container-type:inline-size;/);
  assert.match(css,/\.calendar-grid\{width:100%\}/);
  assert.match(css,/\.calendar-scroll\{[^}]*max-height:320px/);
  assert.match(css,/\.calendar-picker input,\.calendar-picker select\{width:100%;min-width:0;max-width:100%/);
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
    const step=Number(/--calendar-step:([^%]+)%/.exec(calendar(view))[1]);
    const axis = calendar(view).split('<div class="calendar-axis">')[1].split('</div>')[0];
    const ticks=[...axis.matchAll(/style="left:([^%]+)%"/g)].map(match=>Number(match[1]));
    for (const width of [320,600,2400]){
      for (const [index,tick] of ticks.slice(0,-1).entries()){
        assert.ok(Math.abs(width*step/100*index-width*tick/100)<1e-9);
      }
    }
    assert.equal(step,3/hours*100);
    select(view,0);
    assert.match(detail(view),new RegExp(`当天已结算 ${hours}小时`));
    zoom(view,21600);
    const hourAxis = calendar(view).split('<div class="calendar-axis">')[1].split('</div>')[0];
    if (missing) assert.ok(!hourAxis.includes(`>${missing}</span>`));
    else assert.equal((hourAxis.match(/>01:00<\/span>/g)||[]).length,hours===25?2:1);
    if (hours===25){
      zoom(view,3600);
      view.get('#calendar-later').onclick();
      assert.match(calendar(view),/GMT-4 01:00–GMT-5 01:00/);
    }
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
  assert.deepEqual(bars(view).map(bar=>bar.width),[300,60,300,5].map(seconds=>seconds/86400*100));
  assert.equal(laneCount(view),2);
  select(view,0);
  assert.match(detail(view),/08:00:00–08:05:00、08:20:00–08:25:00/);
  assert.equal(view.get('calendar-bar:0').attributes['aria-pressed'],'true');
  assert.equal(view.get('calendar-bar:1').attributes['aria-pressed'],'false');
  assert.equal(view.get('calendar-bar:2').attributes['aria-pressed'],'true');
  select(view,2);
  assert.match(detail(view),/08:30:00–08:30:05 · 当天已结算 5秒/);
  select(view,3);
  assert.match(detail(view),/尚无结算区间/);
  assert.equal((calendar(view).match(/class="act-lane-label"/g)||[]).length,0);
  view.get('#act-body').onclick({target:{closest(selector){
    return selector==='[data-calendar-row]' ? {dataset:{calendarRow:'0',calendarCenter:String(start+1350)}} : null;
  }}});
  zoom(view,3600);
  assert.match(calendar(view),/07:52:30–GMT\+8 08:52:30/);
  assert.match(detail(view),/08:00:00–08:05:00、08:20:00–08:25:00/);
  assert.equal(view.get('calendar-bar:0').attributes['aria-pressed'],'true');
  assert.equal(view.get('calendar-bar:2').attributes['aria-pressed'],'true');
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

test('ninety-six touching activities with long labels share one row and retain every choice', async t=>{
  const start=at('2026-01-05T00:00:00+08:00');
  const content='long-name',title='完整活动名称必须保留'.repeat(40);
  const records=Array.from({length:96},(_,index)=>activity(`a-${index}`,start+index*900,
    [[start+index*900,start+(index+1)*900]],{app:index%2?'bilibili':'douyin',content})).flat();
  const view=await viewer(ordered(records),{catalog:withCatalog({contents:[{content_id:content,title}]})});
  t.after(()=>view.close());
  view.activities();
  assert.equal(bars(view).length,96);
  assert.equal(laneCount(view),1);
  assert.match(calendar(view),/data-lanes="1" style="height:32px"/);
  const choices=calendar(view).split('id="calendar-activity">')[1].split('</select>')[0];
  assert.equal((choices.match(/<option value="\d+"/g)||[]).length,96);
  select(view,95);
  assert.ok(detail(view).includes(title));
  assert.match(detail(view),/23:45:00–次日 00:00:00/);
  assert.doesNotMatch(view.get('#act-body').innerHTML,/<details class="calendar-table" id="activity-table" open>/);
});

test('row count equals real concurrency and paused gaps can be reused by another activity', async t=>{
  const start=at('2026-01-05T08:00:00+08:00');
  const view=await viewer(ordered([
    ...activity('paused',start,[[start,start+300],[start+600,start+900]]),
    ...activity('inside-gap',start+300,[[start+300,start+600]]),
    ...activity('overlap',start+30,[[start+30,start+700]]),
    ...activity('third',start+300,[[start+300,start+400]]),
  ]));
  t.after(()=>view.close());
  view.activities();
  assert.equal(laneCount(view),3);
  const rendered=[...calendar(view).matchAll(/left:([^%]+)%;width:([^%]+)%;top:(\d+)px/g)]
    .map(([,left,width,top])=>({from:Number(left),to:Number(left)+Number(width),top}));
  for(const one of rendered) for(const two of rendered){
    if(one!==two && one.top===two.top) assert.ok(one.to<=two.from+1e-9 || two.to<=one.from+1e-9);
  }
  assert.equal(rendered[0].top,rendered.find(item=>Math.abs(item.from-(8*3600+300)/86400*100)<1e-9).top);
  const many=await viewer(ordered(Array.from({length:40},(_,index)=>
    activity(`parallel-${index}`,start,[[start,start+600]])).flat()));
  t.after(()=>many.close());
  many.activities();
  assert.equal(laneCount(many),40);
  assert.equal(bars(many).length,40);
  select(many,39);
  assert.match(detail(many),/08:00:00–08:10:00/);
});

test('search and zoom reach off-screen short activities and preserve the selected identity', async t=>{
  const start=at('2026-01-05T08:00:00+08:00'),late=at('2026-01-05T20:00:00+08:00');
  const view=await viewer(ordered([
    ...activity('early',start,[[start,start+600]],{app:'bilibili',content:'c-article'}),
    ...activity('late',late,[[late,late+5]],{app:'douyin',content:'c-short'}),
    ...activity('pending',late+120,[],{agent:2}),
  ]));
  t.after(()=>view.close());
  view.activities();
  select(view,0);
  zoom(view,3600);
  assert.match(calendar(view),/07:30–GMT\+8 08:30/);
  assert.equal(bars(view).length,1);
  view.get('#calendar-search').oninput({target:{value:'抖音'}});
  assert.match(view.get('#calendar-activity').innerHTML,/<option value="1"/);
  assert.doesNotMatch(view.get('#calendar-activity').innerHTML,/<option value="0"/);
  select(view,1);
  assert.match(calendar(view),/19:30–GMT\+8 20:30/);
  assert.match(detail(view),/20:00:00–20:00:05 · 当天已结算 5秒/);
  assert.equal(bars(view)[0].width,5/3600*100);
  view.get('#calendar-later').onclick();
  assert.equal(bars(view).length,0);
  zoom(view,21600);
  assert.match(calendar(view),/17:00–GMT\+8 23:00/);
  assert.match(detail(view),/使用抖音/);
  view.run("AT.sortKey='kind'; AT.sortDir=-1");
  view.activities();
  assert.match(detail(view),/使用抖音/);
  view.get('#calendar-search').oninput({target:{value:'不存在的活动'}});
  assert.match(view.get('#calendar-activity').innerHTML,/没有匹配的当天活动/);
  view.get('#calendar-search').oninput({target:{value:'李明'}});
  select(view,2);
  assert.match(detail(view),/尚无结算区间/);
  assert.equal(bars(view).length,1);
  view.get('#agent-filter').value='2';
  view.activities();
  assert.match(calendar(view),/<option value="0" selected>全天/);
});
