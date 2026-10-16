'use strict';

/** @brief 按运行当地日期浏览活动，只绘制已记录的执行区间，详情原文按需读取。 */
const ActivityCalendar = {};
{
  const state = {day:'', agent:null, rows:[], sequences:new Set(), ticket:0};

  /** @brief 取得运行时区中的日期，不采用浏览器所在时区。 */
  function dateKey(seconds){
    const parts = new Intl.DateTimeFormat('en-CA', {timeZone:S.tz, year:'numeric', month:'2-digit', day:'2-digit'})
      .formatToParts(new Date(seconds*1000)).reduce((result, part)=>(result[part.type]=part.value,result),{});
    return `${parts.year}-${parts.month}-${parts.day}`;
  }

  /** @brief 对日历日期加减天数，日期运算不假设当地一天恒为 86400 秒。 */
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

  /** @brief 显示当天的时分秒；小时刻度保留夏令时跳过或重复的小时。 */
  function clock(seconds, secondsShown=false){
    return new Intl.DateTimeFormat('zh-CN', {timeZone:S.tz, hourCycle:'h23', hour:'2-digit', minute:'2-digit',
      ...(secondsShown ? {second:'2-digit'} : {})}).format(new Date(seconds*1000));
  }

  /** @brief 日期或人物变化时清空详情，禁止旧异步读取覆盖新的选择。 */
  function clearSelection(){
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

  /** @brief 活动名称复用共享写法；出行和睡眠保留已有标签。 */
  function rowLabel(row){
    const one = row.activities[0];
    return row.joined ? row.label : ViewerWording.targetPhrase(one,one.contents,S.orders)
      ?? `${actTypeLabel(one.kind,one.tg)} ${actTargetText(one)}`.trim();
  }

  /** @brief 同一行保留全部连续使用成员，只有可见条块裁切到当天；受理后尚无结算的行也保留。 */
  function dayRows(activities, start, end){
    const rows = [];
    for (const agent of [...new Set(activities.map(activity=>activity.agent))]){
      for (const row of ViewerWording.useLaneRows(activities.filter(activity=>activity.agent===agent),S.uses)){
        const label = rowLabel(row), one = row.activities[0];
        const original = row.joined ? row.bars
          : one.intervals.map(([start,end])=>({start,end,text:label}));
        const bars = original.filter(bar=>bar.start<end && bar.end>start)
          .map(bar=>({...bar, from:Math.max(start,bar.start), to:Math.min(end,bar.end)}));
        const accepted = row.activities.filter(activity=>activity.start>=start && activity.start<end);
        if (!bars.length && !accepted.length) continue;
        const first = Math.min(...bars.map(bar=>bar.from),...accepted.map(activity=>activity.start));
        rows.push({...row, agent, label, bars, first, seconds:bars.reduce((sum,bar)=>sum+bar.to-bar.from,0)});
      }
    }
    return rows.sort((left,right)=>left.first-right.first || left.agent-right.agent);
  }

  /** @brief 条块的宽度严格等于当天实际结算秒数，完整起止仍在悬停文字中。 */
  function barMarkup(bar, row, start, end){
    const left = (bar.from-start)/(end-start)*100, width = (bar.to-bar.from)/(end-start)*100;
    return `<span class="act-lane-bar" style="left:${left}%;width:${width}%;` +
      `background:${kindColor(row.activities[0].kind)}" ` +
      `title="${esc(fmtT(bar.start)+'–'+fmtT(bar.end)+' · '+bar.text)}"></span>`;
  }

  /** @brief 每行完整名称与独立详情按钮保证短活动可读，时间轴不为放文字而夸大时长。 */
  function rowMarkup(row, index, start, end){
    const intervals = row.bars.map(bar=>`${clock(bar.from,true)}–${bar.to===end?'次日 00:00:00':clock(bar.to,true)}`);
    const timing = intervals.length ? `${intervals.join('、')} · 当天已结算 ${fmtDur(row.seconds)}` : '尚无结算区间';
    return `<div class="calendar-activity"><div class="act-lane">` +
      `<div class="act-lane-label" title="${esc(row.label)}">${esc(row.label)}</div>` +
      `<div class="act-lane-track">${row.bars.map(bar=>barMarkup(bar,row,start,end)).join('')}</div></div>` +
      `<div class="calendar-meta"><span>${esc(agentName(row.agent))} · ${esc(timing)}</span>` +
      `<button data-calendar-row="${index}">查看活动与事件</button></div></div>`;
  }

  /** @brief 每小时一个刻度；横轴保持实际秒数，切换夏令时的自然日也不挤成 24 小时。 */
  function axis(start, end){
    const ticks = [];
    for (let time=start;time<end;time+=3600){
      ticks.push(`<span style="left:${(time-start)/(end-start)*100}%">${clock(time)}</span>`);
    }
    ticks.push('<span class="calendar-axis-end" style="left:100%">次日 00:00</span>');
    return `<div class="calendar-axis">${ticks.join('')}</div>`;
  }

  /** @brief 日期导航始终覆盖导出的当地日期范围，保留没有活动的日期。 */
  function toolbar(first, last){
    return `<div class="filter-bar calendar-toolbar"><button id="calendar-prev" ` +
      `${state.day<=first?'disabled':''}>← 前一天</button><label>日期 ` +
      `<input id="calendar-date" type="date" value="${state.day}" min="${first}" max="${last}"></label>` +
      `<button id="calendar-next" ${state.day>=last?'disabled':''}>后一天 →</button>` +
      `<span>${esc(S.tz)} · 当天 ${state.rows.length} 项活动或连续使用</span></div>`;
  }

  /** @brief 只渲染选定日；切换人物回到该人物首个活动日，完整活动表不参与日期过滤。 */
  function render(activities){
    clearSelection();
    const agent = agentFilterVal(), first = dateKey(S.t0), last = dateKey(S.t1);
    if (!state.day || state.agent!==agent){
      state.agent = agent;
      state.day = activities.length ? dateKey(Math.min(...activities.map(activity=>activity.start))) : first;
    }
    const start = midnight(state.day), end = midnight(shiftDate(state.day,1));
    state.rows = dayRows(activities,start,end);
    const rows = state.rows.map((row,index)=>rowMarkup(row,index,start,end)).join('');
    const width = Math.ceil((end-start)/3600)*64, hour = 3600/(end-start)*100;
    return toolbar(first,last) + '<div class="hint">同一时间轴上的条块表示实际执行区间；空白表示尚无结算记录。' +
      '同一个 app 首尾相接的几次使用画在同一行，连成一个条块。名称与准确起止独立显示，短活动也可查看详情。</div>' +
      '<div id="activity-detail" aria-live="polite"></div>' + (rows ?
      `<div class="calendar-scroll"><div class="calendar-grid" ` +
      `style="--calendar-hours:${width}px;--calendar-hour:${hour}%">` +
      `${axis(start,end)}${rows}</div></div>` : '<div class="empty">当天没有活动记录</div>');
  }

  /** @brief 只登记活动自身已记录的受理与进度序号，不猜测相邻事件。 */
  function sourceSequences(row){
    return [...new Set(row.activities.flatMap(activity=>[activity.acceptSeq,...activity.progress.map(part=>part.seq)]))]
      .filter(sequence=>sequence!=null).sort((left,right)=>left-right);
  }

  /** @brief 选择一行后列出完整活动及来源；连续使用的成员和跨日区间全部保留。 */
  function select(index){
    const row = state.rows[index];
    clearSelection();
    state.sequences = new Set(sourceSequences(row));
    const sources = [...state.sequences].map(sequence=>{
      const event = S.events.find(event=>event.seq===sequence);
      return `<button data-calendar-event="${sequence}">${event.type==='action_accepted'?'受理':'进度'} ` +
        `#${sequence} · ${esc(fmtT(event.t))}</button>`;
    }).join('');
    const members = row.activities.map(activity=>`<li>${esc(actTargetText(activity))} · ` +
      `${esc(fmtT(activity.start))}–${esc(fmtT(activity.end))} · 已结算 ${fmtDur(activity.actualSeconds)}</li>`).join('');
    $('#activity-detail').innerHTML = `<div class="card calendar-detail"><h3>${esc(row.label)}</h3>` +
      `<p>${esc(agentName(row.agent))} · 当天已结算 ${fmtDur(row.seconds)}</p><ul>${members}</ul>` +
      `<h4>相关事件 · 活动全部来源</h4><div class="calendar-sources">${sources||'没有记录来源事件'}</div>` +
      '<pre id="calendar-event-body">选择事件查看记录原文</pre></div>';
    $('#activity-detail').scrollIntoView({block:'nearest'});
  }

  /** @brief 按字节区间读取选中来源，过期结果和错误都不覆盖新日期或新运行。 */
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

  /** @brief 换日后重画当前人物，不改变完整表格的排序。 */
  function changeDay(day){
    if (!day || day<dateKey(S.t0) || day>dateKey(S.t1)) return;
    state.day = day;
    clearSelection();
    renderActivities();
  }

  /** @brief 绑定日期按钮和事件委托，完整名称和详情按钮均不依赖条块最小宽度。 */
  function bind(){
    $('#calendar-date').onchange = event=>changeDay(event.target.value);
    $('#calendar-prev').onclick = ()=>changeDay(shiftDate(state.day,-1));
    $('#calendar-next').onclick = ()=>changeDay(shiftDate(state.day,1));
    $('#act-body').onclick = event=>{
      const row = event.target.closest('[data-calendar-row]'), source = event.target.closest('[data-calendar-event]');
      if (row) select(Number(row.dataset.calendarRow));
      if (source) return readEvent(Number(source.dataset.calendarEvent));
    };
  }

  Object.assign(ActivityCalendar,{render,bind,reset,select,readEvent});
}
