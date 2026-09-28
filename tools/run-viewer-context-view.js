'use strict';

/** @brief 将模型请求的按需读取、选择和渲染接入查看器。 */
const ContextView = {};
{
  const state = {items:null, seq:null, stage:'all', view:'structure', block:null, rule:null};
  const stages = {all:'全部调用', action:'选择动作', other:'其他调用'};
  const views = {structure:'结构与填充', growth:'增长与退出', comparison:'前后输入对照'};
  const statusNames = {added:'新增', changed:'内容变化', unchanged:'未变', removed:'退出当前输入'};

  /** @brief 只投影请求元数据，避免提前读取整日提示正文。 */
  function index() {
    if (state.items) return state.items;
    const rows = q(`SELECT event_seq AS seq, json_extract(body,'$.event_id') AS id,
      json_extract(body,'$.agent_id') AS agent, json_extract(body,'$.occurred_at') AS t,
      json_extract(body,'$.decision_id') AS decision, json_extract(body,'$.data.purpose') AS purpose,
      json_extract(body,'$.data.request.messages[0].content') AS system,
      json_extract(body,'$.data.request.messages[0].role') AS role,
      json_type(body,'$.data.request.messages[0].content') AS content_type,
      json_type(body,'$.data.request.tools') AS tools_type
      FROM events WHERE json_extract(body,'$.event_type')='model_request' ORDER BY event_seq`);
    state.items = rows.map(row => {
      const request = {messages:[{role:row.role,content:row.content_type === 'text' ? row.system : null}]};
      if (row.tools_type !== null) request.tools = row.tools_type === 'array' ? [] : null;
      const kind = ViewerContext.classify(request, row.purpose);
      return {seq:row.seq, id:row.id, agent:row.agent, t:row.t, decision:row.decision,
        stage:kind.stage, label:kind.label};
    });
    return state.items;
  }

  /** @brief 通过请求事件的因果身份查找响应，不猜测相邻调用。 */
  function responseFor(item) {
    const rows = q(`SELECT events.event_seq AS seq FROM events, json_each(events.body,'$.caused_by') AS cause
      WHERE json_extract(events.body,'$.event_type')='model_response' AND cause.value=?
      ORDER BY events.event_seq`, [item.id]);
    if (!rows.length) return null;
    if (rows.length !== 1) throw new Error('Multiple model responses reference one request');
    return {seq:rows[0].seq, event:JSON.parse(fetchBody(rows[0].seq))};
  }

  /** @brief 一次决策的首个请求才可能是续接：前序 assistant 的每个 tool_call 身份在本人更早的模型响应中找来源决策。 */
  function continuationFor(item, event) {
    if (index().some(row => row.decision === item.decision && row.seq < item.seq)) return null;
    const sources = {};
    for (const id of ViewerContext.prefixCallIds(event?.data?.request)) {
      const rows = q(`SELECT json_extract(events.body,'$.decision_id') AS decision,
        json_extract(events.body,'$.occurred_at') AS t FROM events,
        json_each(events.body,'$.data.response.choices[0].message.tool_calls') AS call
        WHERE json_extract(events.body,'$.event_type')='model_response' AND json_extract(call.value,'$.id')=?
        AND json_extract(events.body,'$.agent_id')=? AND events.event_seq<?
        ORDER BY events.event_seq DESC LIMIT 1`, [id, item.agent, item.seq]);
      if (rows.length) sources[id] = `${fmtT(rows[0].t)} · ${rows[0].decision}`;
    }
    return {sources};
  }

  /** @brief 读取选中请求及同人物同阶段的前一次输入；决策首个请求多于两条消息时按续接解析。 */
  function read(seq) {
    const item = index().find(row => row.seq === seq);
    if (!item) throw new Error('Model request is not present in the loaded run');
    const previous = index().filter(row => row.seq < seq && row.agent === item.agent &&
      row.stage === item.stage).at(-1);
    const event = JSON.parse(fetchBody(seq));
    const current = ViewerContext.parseRequest(event, continuationFor(item, event));
    const prior = previous ? JSON.parse(fetchBody(previous.seq)) : null;
    const before = prior ? ViewerContext.parseRequest(prior, continuationFor(previous, prior)) : null;
    return {item, event, current, previous, before, response:responseFor(item),
      changes:before ? ViewerContext.compare(current.blocks, before.blocks) : []};
  }

  /** @brief 生成已转义的选项；调用方只提供真实记录值。 */
  function options(entries, selected) {
    return entries.map(([value,label]) => `<option value="${esc(value)}"` +
      `${String(value) === String(selected) ? ' selected' : ''}>${esc(label)}</option>`).join('');
  }

  /** @brief 用完整原文生成可展开内容，不作展示截断。 */
  function disclosure(label, text) {
    return `<details><summary>${esc(label)}</summary><pre>${esc(text)}</pre></details>`;
  }

  /** @brief 构造请求、阶段与分析视图选择控件。 */
  function controls(items) {
    const entries = items.map(item => [item.seq,
      `${fmtT(item.t)} · ${agentName(item.agent)} · ${item.label} · 事件 ${item.seq}`]);
    return `<div class="cx-controls">
      <label>调用阶段<select id="ctx-stage">${options(Object.entries(stages), state.stage)}</select></label>
      <label class="cx-request">实际请求<select id="ctx-request">${options(entries, state.seq)}</select></label>
      <button id="ctx-prev" type="button">上一请求</button><button id="ctx-next" type="button">下一请求</button>
      <label>查看内容<select id="ctx-view">${options(Object.entries(views), state.view)}</select></label></div>`;
  }

  /** @brief 显示单一行动阶段的决策顺序；其他调用不套用人物决策阶段。 */
  function flow(stage) {
    if (stage === 'other') return '<div class="cx-meta">其他模型调用按原始消息结构展示。</div>';
    return `<div class="cx-flow" aria-label="人物决策流程"><span>冻结观察</span><span>→</span>
      <span class="active">系统层与六层输入 → 选择世界请求</span><span>→</span>
      <span>世界执行 → 下一轮重建</span></div>`;
  }

  /** @brief 绘制按请求实际顺序排列的内容块与字符数量条。 */
  function blockList(blocks) {
    const maximum = Math.max(1, ...blocks.map(block => block.chars));
    return '<div class="cx-blocks">' + blocks.map(block =>
      `<button type="button" class="cx-block" data-context-block="${esc(block.id)}"
        aria-pressed="${block.id === state.block}"><span>${esc(block.label)}</span><span>${nf(block.chars)}</span>
        <small>${esc(block.role)}${block.interpretation === 'layer' ? ' · 层切片' : ''}</small>
        <small>字符</small><span class="cx-track" aria-hidden="true">
        <span class="cx-bar" style="display:block;width:${block.chars / maximum * 100}%"></span></span></button>`
    ).join('') + '</div>';
  }

  /** @brief 展示所选内容块的来源、规则与完整实际文本。 */
  function blockDetail(block) {
    if (!block) return '<div class="empty">本次请求没有消息或工具定义。</div>';
    const rule = ViewerContext.rules[block.rule];
    const metadata = rule ? `<dl><dt>构建来源</dt><dd>${esc(rule.source)}</dd>
      <dt>填入</dt><dd>${esc(rule.fill)}</dd></dl>` : '';
    const provenance = block.interpretation === 'layer' ?
      '<p class="cx-meta">按顶层数组无损切片；层内原文未改动，分隔符计入本层。</p>' : '';
    const drillable = ViewerContext.drillItems(block).length > 0;
    return `<div class="cx-detail card"><h3>${esc(block.label)}</h3>${provenance}${metadata}
      ${drillable ? '<label>层内条目<select id="ctx-item"></select></label>' : ''}
      <details open><summary>本轮完整原文</summary><pre id="ctx-raw">${esc(block.text)}</pre></details></div>`;
  }

  /** @brief 按工具层与进度层的真实字段下钻；self_note 条目标明未经核验，不重构未出现的内容。 */
  function bindLayer(block) {
    const items = ViewerContext.drillItems(block);
    if (!items.length || state.view !== 'structure') return;
    const select = $('#ctx-item');
    select.innerHTML = options([['','整层原文'], ...items.map((item, index) => [index, item.label])], '');
    select.onchange = () => {
      $('#ctx-raw').textContent = select.value === '' ? block.text : items[Number(select.value)].text;
    };
  }

  /** @brief 绘制源码规则，明确与所选运行的实测输入区别。 */
  function growth(block) {
    const key = state.rule || block?.rule || Object.keys(ViewerContext.rules)[0];
    const rule = ViewerContext.rules[key];
    const choices = Object.entries(ViewerContext.rules).map(([id, value]) => [id, value.label]);
    return `<div class="cx-detail"><label>内容规则<select id="ctx-rule">${options(choices, key)}</select></label>
      <h3>${esc(rule.label)}</h3>
      <p class="cx-meta">当前源码规则；默认参数不代表该运行记录了相同配置。</p>
      ${rule.steps.map(([label,text]) => `<div class="cx-rule-step"><span>${esc(label)}</span>
        <span>${esc(text)}</span></div>`).join('')}<p class="cx-meta">${esc(rule.source)}</p></div>`;
  }

  /** @brief 对照内容变化而非仅比较长度，并保留退出内容的原文。 */
  function comparison(model) {
    if (!model.before) return '<div class="hint">没有同人物、同阶段的前一次请求可供对照。</div>';
    const change = model.changes.find(row => row.id === state.block) || model.changes[0];
    const rows = model.changes.map(row => `<tr><td><button type="button" data-context-block="${esc(row.id)}">
      ${esc(row.label)}</button></td><td>${statusNames[row.status]}</td><td>${nf(row.before?.chars ?? 0)}
      → ${nf(row.after?.chars ?? 0)}</td><td>${row.delta > 0 ? '+' : ''}${nf(row.delta)}</td></tr>`).join('');
    const detail = change ? `<h3>${esc(change.label)} · ${statusNames[change.status]}</h3><div class="cx-pair">
      <div><h3>上次输入</h3><pre>${esc(change.before?.text ?? '该块未出现')}</pre></div>
      <div><h3>本次输入</h3><pre>${esc(change.after?.text ?? '该块已退出')}</pre></div></div>` : '';
    return `<p class="cx-meta">对照 ${fmtT(model.previous.t)} · 事件 ${model.previous.seq}。
      退出提示窗口不代表底层记录被删除。</p><table class="cx-table"><thead><tr>
      <th>内容块</th><th>变化</th><th>字符</th><th>增减</th></tr></thead><tbody>${rows}</tbody></table>${detail}`;
  }

  /** @brief 展示严格关联的外显响应、工具调用、思考原文、缓存命中与 reasoning_tokens 等归档 token 用量。 */
  function output(model) {
    if (!model.response) return '<div class="cx-meta">该请求尚无关联响应记录。</div>';
    const data = model.response.event.data;
    const response = data.response;
    const usage = response?.usage ?? null;
    const visible = {choices:(response?.choices || []).map(choice => ({index:choice.index,
      content:choice.message?.content, tool_calls:choice.message?.tool_calls, finish_reason:choice.finish_reason})),
      usage, cache_hit_tokens:usage?.prompt_cache_hit_tokens ?? null,
      reasoning_tokens:usage?.completion_tokens_details?.reasoning_tokens ?? null, error:data.error ?? null};
    const reasoning = (response?.choices || []).map(choice => choice.message?.reasoning_content)
      .filter(text => typeof text === 'string' && text).join('\n\n');
    return `<div class="cx-output">${data.error ? `<p role="alert">${esc(data.error)}</p>` : ''}
      ${disclosure(`本次响应与用量 · 事件 ${model.response.seq}`, JSON.stringify(visible,null,2))}
      ${reasoning ? disclosure('本次思考原文', reasoning) : ''}</div>`;
  }

  /** @brief 展示当前请求结构、规则或前后对照。 */
  function content(model) {
    const blocks = model.current.blocks;
    const removedSelection = state.view === 'comparison' && model.changes.some(row => row.id === state.block);
    if (!blocks.some(block => block.id === state.block) && !removedSelection) state.block = blocks[0]?.id;
    const block = blocks.find(row => row.id === state.block);
    const warnings = model.current.warnings.map(text => `<p role="alert">${esc(text)}</p>`).join('');
    const displayed = state.view === 'comparison' ? [...blocks, ...(model.before?.blocks || [])] : blocks;
    const provenance = (displayed.some(block => block.interpretation === 'layer') ?
      '<p class="cx-meta">首个 user 按顶层数组无损切片，每片可解析且层序符合七层合同；self_note 未经核验。</p>' : '') +
      (model.current.continuation ? '<p class="cx-meta">同秒续接：本决策首个请求多于两条消息；前序 assistant 按 ' +
        'tool_call 身份匹配来源决策，回传思考单列，不另计字符。</p>' : '');
    const detail = state.view === 'growth' ? growth(block) : blockDetail(block);
    const body = state.view === 'comparison' ? comparison(model) :
      `<div class="cx-layout">${blockList(blocks)}${detail}</div>`;
    return `${flow(model.current.stage)}<p class="cx-meta">${esc(model.current.stageLabel)} ·
      消息 ${nf(model.current.messageChars)} 字符 · 工具定义 ${nf(model.current.toolChars)} 字符。
      Unicode 字符计数，分隔符计入随后块，对象按 JSON 计量；不等于 token。</p>${provenance}${warnings}${body}
      ${output(model)}${disclosure('完整请求消息与工具', JSON.stringify(model.event.data.request,null,2))}`;
  }

  /** @brief 绑定控件到当前请求集合，按钮不越过筛选后的边界。 */
  function bindControls(items, model) {
    $('#ctx-stage').onchange = event => {state.stage = event.target.value; state.seq = null; render();};
    $('#ctx-request').onchange = event => {state.seq = Number(event.target.value); render();};
    $('#ctx-view').onchange = event => {state.view = event.target.value; render();};
    const position = items.findIndex(item => item.seq === state.seq);
    $('#ctx-prev').disabled = position <= 0;
    $('#ctx-next').disabled = position < 0 || position >= items.length - 1;
    $('#ctx-prev').onclick = () => {state.seq = items[position - 1].seq; render();};
    $('#ctx-next').onclick = () => {state.seq = items[position + 1].seq; render();};
    $$('#ctx-body [data-context-block]').forEach(button => {
      button.onclick = () => {
        state.block = button.dataset.contextBlock;
        state.rule = model.current.blocks.find(block => block.id === state.block)?.rule || null;
        render();
      };
    });
    if (state.view === 'growth' && model) {
      $('#ctx-rule').onchange = event => {state.rule = event.target.value; render();};
    }
    bindLayer(model?.current.blocks.find(block => block.id === state.block));
  }

  /** @brief 应用人物与阶段筛选，只读取当前与对照请求全文。 */
  function render() {
    const root = $('#ctx-body');
    try {
      const agent = agentFilterVal();
      const items = index().filter(item => (!agent || String(item.agent) === agent) &&
        (state.stage === 'all' || item.stage === state.stage));
      if (!items.some(item => item.seq === state.seq)) state.seq = items[0]?.seq ?? null;
      const model = state.seq === null ? null : read(state.seq);
      root.innerHTML = controls(items) + (model ? content(model) : '<div class="empty">没有匹配的模型请求。</div>');
      bindControls(items, model);
    } catch (error) {
      console.error('Context viewer failed:', error);
      root.innerHTML = `<div role="alert">Context viewer failed: ${esc(error.message)}</div>`;
    }
  }

  /** @brief 从时间线进入指定真实模型请求。 */
  function select(seq) {
    const item = index().find(row => row.seq === seq);
    if (!item) throw new Error('Model request is not present in the loaded run');
    $('#agent-filter').value = item.agent === null ? '' : String(item.agent);
    state.seq = seq; state.stage = 'all'; state.view = 'structure'; state.block = null; state.rule = null;
    switchView('context');
  }

  /** @brief 更换运行时清除全部上下文状态。 */
  function reset() {
    state.items = null; state.seq = null; state.stage = 'all'; state.view = 'structure';
    state.block = null; state.rule = null;
  }

  Object.assign(ContextView, {index, read, render, select, reset});
}
