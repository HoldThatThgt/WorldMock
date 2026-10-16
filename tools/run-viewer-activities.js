'use strict';

/** @brief 按当地日期查看真实执行区间，按实际重叠复用行，原文按需读取。 */
const ActivityCalendar = {};
{
  const state = {day:'', agent:null, rows:[], selected:null, span:0, offset:0, focus:null, query:'',
    sequences:new Set(), ticket:0};

  /** @brief 取得运行时区中的日期。 */
  function dateKey(seconds){
    const parts = new Intl.DateTimeFormat('en-CA', {timeZone:S.tz, year:'numeric', month:'2-digit', day:'2-digit'})
      .formatToParts(new Date(seconds*1000)).reduce((result, part)=>(result[part.type]=part.value,result),{});
    return `${parts.year}-${parts.month}-${parts.day}`;
  }

  /** @brief 日历日期加减天数，不假设当地一天恒为 86400 秒。 */
  function shiftDate(day, amount){
    const value = new Date(day+'T00:00:00Z');
    value.setUTCDate(value.getUTCDate()+amount);
    return value.toISOString().slice(0,10);
  }

  /** @brief 用现有时区偏移计算当地零点，重新求偏移以覆盖夏令时切换。 */
  function midnight(day){
    const wall = Date.parse(day+'T00:00:00Z')/1000;
    let result = wall;
    for (let step=0;step<3;step++) result = wall-tzOffsetSec(S.tz,result);
    return result;
  }

  /** @brief 显示当地时分秒；可见时段增加偏移以区分夏令时重复小时。 */
  function clock(seconds, secondsShown=false, offset=false){
    return new Intl.DateTimeFormat('zh-CN', {timeZone:S.tz, hourCycle:'h23', hour:'2-digit', minute:'2-digit',
      ...(secondsShown ? {second:'2-digit'} : {}), ...(offset ? {timeZoneName:'shortOffset'} : {})})
      .format(new Date(seconds*1000));
  }

  /** @brief 换日期或人物时清空选择、搜索和缩放，并令旧读取过期。 */
  function clearSelection(){
    AT.page = 0;
    state.selected = null;
    state.span = 0;
    state.offset = 0;
    state.focus = null;
    state.query = '';
    state.sequences.clear();
    state.ticket++;
  }

  /** @brief 换导出时清空日历状态。 */
  function reset(){
    state.day = '';
    state.agent = null;
    state.rows = [];
    clearSelection();
  }

  /** @brief 复用共享活动写法，出行与睡眠保留现有名称。 */
  function rowLabel(row){
    const one = row.activities[0];
    return row.joined ? row.label : ViewerWording.targetPhrase(one,one.contents,S.orders)
      ?? `${actTypeLabel(one.kind,one.tg)} ${actTargetText(one)}`.trim();
  }

  /** @brief 保留连续使用全部成员，只裁切可见区间；当天受理但尚无结算的活动也可选。 */
  function dayRows(activities, start, end){
    const rows = [];
    for (const agent of [...new Set(activities.map(activity=>activity.agent))]){
      for (const row of ViewerWording.useLaneRows(activities.filter(activity=>activity.agent===agent),S.uses)){
        const label = rowLabel(row), one = row.activities[0];
        const original = row.joined ? row.bars : one.intervals.map(([start,end])=>({start,end,text:label}));
        const bars = original.filter(bar=>bar.start<end && bar.end>start)
          .map(bar=>({...bar, from:Math.max(start,bar.start), to:Math.min(end,bar.end)}));
        const accepted = row.activities.filter(activity=>activity.start>=start && activity.start<end);
        if (!bars.length && !accepted.length) continue;
        const first = Math.min(...bars.map(bar=>bar.from),...accepted.map(activity=>activity.start));
        rows.push({...row, aid:one.aid, agent, label, bars, first,
          seconds:bars.reduce((sum,bar)=>sum+bar.to-bar.from,0)});
      }
    }
    return rows.sort((left,right)=>left.first-right.first || left.agent-right.agent);
  }

  /** @brief 可视时段限定在当天，以实际秒数处理 23 小时与 25 小时自然日。 */
  function bounds(){
    const first = midnight(state.day), last = midnight(shiftDate(state.day,1));
    const span = state.span || last-first;
    state.offset = Math.max(0,Math.min(state.offset,last-first-span));
    return {first,last,start:first+state.offset,end:Math.min(last,first+state.offset+span)};
  }

  /** @brief 活动页各表共享所选自然日，不随时间图缩放缩短。 */
  function selectedDay(){
    const {first,last} = bounds();
    return {date:state.day,start:first,end:last};
  }

  /** @brief 按开始时刻摆放真实区间，已结束的行立即复用，标签尺寸不参与分行。 */
  function layout(start, end){
    const bars = state.rows.flatMap((row,index)=>row.bars.map(bar=>({...bar,index})))
      .filter(bar=>bar.from<end && bar.to>start)
      .map(bar=>({...bar,from:Math.max(start,bar.from),to:Math.min(end,bar.to)}))
      .sort((left,right)=>left.from-right.from || left.to-right.to || left.index-right.index);
    const ends = [];
    for (const bar of bars){
      const available = ends.findIndex(end=>end<=bar.from);
      bar.lane = available<0 ? ends.length : available;
      ends[bar.lane] = bar.to;
    }
    return {bars,lanes:ends.length};
  }

  /** @brief 条块保持真实时长，短标签裁切；完整名称和准确时段由选择器与详情提供。 */
  function barMarkup(bar, start, end){
    const row = state.rows[bar.index], left = (bar.from-start)/(end-start)*100;
    const width = (bar.to-bar.from)/(end-start)*100, selected = state.selected===row.aid;
    return `<button class="act-lane-bar" style="left:${left}%;width:${width}%;top:${bar.lane*32+4}px;` +
      `background:${kindColor(row.activities[0].kind)}" ` +
      `title="${esc(fmtT(bar.start)+'–'+fmtT(bar.end)+' · '+bar.text)}" ` +
      `data-calendar-row="${bar.index}" data-calendar-center="${(bar.from+bar.to)/2}" aria-pressed="${selected}" ` +
      `aria-label="${esc(agentName(row.agent)+' · '+row.label)}">${esc(row.label)}</button>`;
  }

  /** @brief 刻度与背景网格使用相同秒数比例，终点写清跨日关系。 */
  function axis(start, end, step){
    const ticks = [];
    for (let time=start;time<end;time+=step){
      ticks.push(`<span style="left:${(time-start)/(end-start)*100}%">${clock(time,time%60!==0)}</span>`);
    }
    const label = (dateKey(end)!==state.day ? '次日 ' : '')+clock(end,end%60!==0);
    ticks.push(`<span class="calendar-axis-end" style="left:100%">${label}</span>`);
    return `<div class="calendar-axis">${ticks.join('')}</div>`;
  }

  /** @brief 图高仅由实际并发决定，容器限制高度但不删除区间。 */
  function chart(){
    const {start,end} = bounds(), drawn = layout(start,end);
    const step = state.span===3600 ? 600 : state.span===21600 ? 3600 : 10800;
    const bars = drawn.bars.map(bar=>barMarkup(bar,start,end)).join('');
    return `<div class="calendar-range">${clock(start,start%60!==0,true)}–${clock(end,end%60!==0,true)} · ` +
      `${drawn.bars.length} 段执行区间 · 最大同时执行 ${drawn.lanes} 段</div>` +
      `<div class="calendar-scroll"><div class="calendar-grid" style="--calendar-step:${step/(end-start)*100}%">` +
      `${axis(start,end,step)}<div class="calendar-track" data-lanes="${drawn.lanes}" ` +
      `style="height:${Math.max(1,drawn.lanes)*32}px">${bars}</div></div></div>` +
      (bars ? '' : '<div class="hint">当前时段没有结算区间；当天活动仍可在下方选择。</div>');
  }

  /** @brief 日期导航与固定尺度放大，不提供编辑或任意拖拽。 */
  function toolbar(first, last){
    const {start,end,first:dayStart,last:dayEnd} = bounds();
    const options = [[0,'全天'],[21600,'6 小时'],[3600,'1 小时']]
      .map(([value,label])=>`<option value="${value}" ${state.span===value?'selected':''}>${label}</option>`).join('');
    return `<div class="filter-bar calendar-toolbar"><button id="calendar-prev" ` +
      `${state.day<=first?'disabled':''}>← 前一天</button><label>日期 ` +
      `<input id="calendar-date" type="date" value="${state.day}" min="${first}" max="${last}"></label>` +
      `<button id="calendar-next" ${state.day>=last?'disabled':''}>后一天 →</button>` +
      `<span>${esc(S.tz)} · 当天 ${state.rows.length} 项活动或连续使用</span></div>` +
      `<div class="filter-bar calendar-zoom"><label>查看范围 <select id="calendar-zoom">${options}</select></label>` +
      `<button id="calendar-earlier" ${start<=dayStart?'disabled':''}>← 前一时段</button>` +
      `<button id="calendar-later" ${end>=dayEnd?'disabled':''}>后一时段 →</button></div>`;
  }

  /** @brief 搜索只缩小选择器列表，不隐藏时间轴里的事实。 */
  function choices(){
    const options = state.rows.map((row,index)=>{
      const label = `${clock(row.first,true)} · ${agentName(row.agent)} · ${row.label}`;
      if (!label.toLocaleLowerCase().includes(state.query.toLocaleLowerCase())) return '';
      return `<option value="${index}" ${state.selected===row.aid?'selected':''}>${esc(label)}</option>`;
    }).join('');
    return `<option value="">${options?'选择当天活动…':'没有匹配的当天活动'}</option>` + options;
  }

  /** @brief 极短活动与未结算活动都有独立于条块大小的选择入口。 */
  function picker(){
    return `<div class="calendar-picker"><label>搜索名称或人物 ` +
      `<input type="search" id="calendar-search" value="${esc(state.query)}" placeholder="输入名称"></label>` +
      `<label>当天活动 <select id="calendar-activity">${choices()}</select></label></div>`;
  }

  /** @brief 图外显示准确区间，不用标签宽度改变条块时长或显示行数。 */
  function timing(row){
    const intervals = row.bars.map(bar=>`${clock(bar.from,true)}–` +
      `${dateKey(bar.to)!==state.day?'次日 ':''}${clock(bar.to,true)}`);
    return intervals.length ? `${intervals.join('、')} · 当天已结算 ${fmtDur(row.seconds)}` : '尚无结算区间';
  }

  /** @brief 重画保留同一天的选择与时段，旧原文读取在重画后失效。 */
  function render(activities){
    state.sequences.clear();
    state.ticket++;
    const agent = agentFilterVal(), first = dateKey(S.t0), last = dateKey(S.t1);
    if (!state.day || state.agent!==agent){
      state.agent = agent;
      state.day = activities.length ? dateKey(Math.min(...activities.map(activity=>activity.start))) : first;
      clearSelection();
    }
    const start = midnight(state.day), end = midnight(shiftDate(state.day,1));
    state.rows = dayRows(activities,start,end);
    return toolbar(first,last) + '<div class="hint">同一时间轴上的条块表示实际执行区间；空白表示尚无结算记录。' +
      '首尾相接的同一 app 使用合成条块；先后执行的区间复用显示行，只有实际重叠时分行。' +
      '点击条块或搜索选择活动查看详情。</div>' + (state.rows.length ? chart()+picker() :
      '<div class="empty">当天没有活动记录</div>') + '<div id="activity-detail" aria-live="polite"></div>';
  }

  /** @brief 只登记活动自身已记录的受理与进度序号。 */
  function sourceSequences(row){
    return [...new Set(row.activities.flatMap(activity=>[activity.acceptSeq,...activity.progress.map(part=>part.seq)]))]
      .filter(sequence=>sequence!=null).sort((left,right)=>left-right);
  }

  /** @brief 显示完整成员与来源，选中同一对象的所有可见区间。 */
  function select(index, focus=null){
    const row = state.rows[index];
    state.selected = row.aid;
    state.focus = focus ?? row.first;
    state.ticket++;
    state.sequences = new Set(sourceSequences(row));
    $$('#act-body [data-calendar-row]').forEach(button=>
      button.setAttribute('aria-pressed',String(Number(button.dataset.calendarRow)===index)));
    $('#calendar-activity').value = String(index);
    const sources = [...state.sequences].map(sequence=>{
      const event = S.events.find(event=>event.seq===sequence);
      return `<button data-calendar-event="${sequence}">${event.type==='action_accepted'?'受理':'进度'} ` +
        `#${sequence} · ${esc(fmtT(event.t))}</button>`;
    }).join('');
    const members = row.activities.map(activity=>`<li>${esc(actTargetText(activity))} · ` +
      `${esc(fmtT(activity.start))}–${esc(fmtT(activity.end))} · 已结算 ${fmtDur(activity.actualSeconds)}</li>`).join('');
    $('#activity-detail').innerHTML = `<div class="card calendar-detail"><h3>${esc(row.label)}</h3>` +
      `<p>${esc(agentName(row.agent))} · ${esc(timing(row))}</p>` +
      `<details><summary>完整活动与来源（可跨日）</summary><ul>${members}</ul>` +
      `<h4>相关事件 · 活动全部来源</h4><div class="calendar-sources">${sources||'没有记录来源事件'}</div>` +
      '<pre id="calendar-event-body">选择事件查看记录原文</pre></details></div>';
  }

  /** @brief 原文按字节范围读取，过期结果和错误不覆盖新选择。 */
  async function readEvent(sequence){
    if (!state.sequences.has(sequence)) return;
    const ticket = ++state.ticket, output = $('#calendar-event-body');
    output.textContent = '读取中 …';
    try{
      const body = await prettyBody(sequence);
      if (ticket===state.ticket) output.textContent = body;
    }catch(error){
      console.error('Activity event body could not be read:',sequence,error.message);
      if (ticket===state.ticket) output.textContent = '读取失败：'+error.message;
    }
  }

  /** @brief 换日重置选择、缩放与页码，不改变表格排序。 */
  function changeDay(day){
    if (!day || day<dateKey(S.t0) || day>dateKey(S.t1)) return;
    state.day = day;
    clearSelection();
    renderActivities();
  }

  /** @brief 缩放围绕选中区间或当前时段中心，前后移动以当前可视时长为步长。 */
  function changeRange(span, direction=0){
    const current = bounds(), focus = state.focus ?? (current.start+current.end)/2;
    state.span = span;
    state.offset = direction ? state.offset+direction*(current.end-current.start) : focus-current.first-span/2;
    if (!span) state.offset = 0;
    renderActivities();
  }

  /** @brief 选择器定位到当前范围外的活动，短活动不依赖鼠标命中。 */
  function choose(index){
    select(index);
    if (state.span){
      state.offset = state.focus-midnight(state.day)-state.span/2;
      renderActivities();
    }
    $('#activity-detail').scrollIntoView({block:'nearest'});
  }

  /** @brief 绑定图外选择与搜索，条块点击携带真实可见区间中心。 */
  function bindPicker(){
    if (!state.rows.length) return;
    $('#calendar-search').oninput = event=>{
      state.query = event.target.value;
      $('#calendar-activity').innerHTML = choices();
    };
    $('#calendar-activity').onchange = event=>{ if (event.target.value!=='') choose(Number(event.target.value)); };
    const selected = state.rows.findIndex(row=>row.aid===state.selected);
    if (selected>=0) select(selected,state.focus);
  }

  /** @brief 绑定日期、缩放与事件委托，详情入口位于图外。 */
  function bind(){
    $('#calendar-date').onchange = event=>changeDay(event.target.value);
    $('#calendar-prev').onclick = ()=>changeDay(shiftDate(state.day,-1));
    $('#calendar-next').onclick = ()=>changeDay(shiftDate(state.day,1));
    $('#calendar-zoom').onchange = event=>changeRange(Number(event.target.value));
    $('#calendar-earlier').onclick = ()=>changeRange(state.span,-1);
    $('#calendar-later').onclick = ()=>changeRange(state.span,1);
    $('#act-body').onclick = event=>{
      const row = event.target.closest('[data-calendar-row]'), source = event.target.closest('[data-calendar-event]');
      if (row){ select(Number(row.dataset.calendarRow),Number(row.dataset.calendarCenter));
        $('#activity-detail').scrollIntoView({block:'nearest'}); }
      if (source) return readEvent(Number(source.dataset.calendarEvent));
    };
    bindPicker();
  }

  Object.assign(ActivityCalendar,{render,bind,reset,select,readEvent,selectedDay});
}
