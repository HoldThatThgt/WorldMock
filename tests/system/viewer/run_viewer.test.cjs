'use strict';

const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {DatabaseSync} = require('node:sqlite');
const test = require('node:test');
const vm = require('node:vm');

const html = readFileSync(path.join(__dirname, '../../../tools/run-viewer.html'), 'utf8');
const scripts = [...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)].map(match => {
  const source = /src="([^"]+)"/.exec(match[1]);
  return source ? readFileSync(path.join(__dirname, '../../../tools', source[1]), 'utf8') : match[2];
});
const epoch = Date.parse('2026-01-05T06:00:00+08:00') / 1000;

/** @brief 提供渲染所需的最小 DOM，不替换页面业务逻辑。 */
function element() {
  const classes = new Set();
  return {
    innerHTML: '', textContent: '', value: '', dataset: {}, style: {}, children: [],
    classList: {
      add: name => classes.add(name), remove: name => classes.delete(name),
      contains: name => classes.has(name), toggle: name => classes.has(name)
        ? classes.delete(name) : classes.add(name),
    },
    addEventListener() {}, appendChild(child) { this.children.push(child); },
    querySelector() { return element(); }, click() {},
    after(child) { this.nextElementSibling = child; },
  };
}

/** @brief 用真实 SQLite 实现 sql.js 查询接口，SQL 原样执行；world 覆盖最终世界状态的字段（如地点会话）。 */
function database(events, world = {}) {
  const db = new DatabaseSync(':memory:');
  db.exec('CREATE TABLE events(event_seq INTEGER PRIMARY KEY, body TEXT NOT NULL)');
  db.exec('CREATE TABLE world_state(body TEXT NOT NULL)');
  const insert = db.prepare('INSERT INTO events(event_seq, body) VALUES (?, ?)');
  events.forEach((event, index) => insert.run(index + 1, JSON.stringify({
    run_id: 'viewer-test-run', occurred_at: epoch + index * 60, agent_id: 1, ...event,
  })));
  db.prepare('INSERT INTO world_state(body) VALUES (?)').run(JSON.stringify({
    timezone: 'Asia/Shanghai', agents: {1: {name: '陈曦'}, 2: {name: '李明'}}, ...world,
  }));
  return {
    close: () => db.close(),
    prepare(sql) {
      const statement = db.prepare(sql);
      let rows = [], index = -1;
      return {
        bind(parameters) { rows = statement.all(...parameters); },
        step() { return ++index < rows.length; },
        getAsObject() { return rows[index]; }, free() {},
      };
    },
  };
}

/** @brief 执行整段页面脚本并暴露真实索引和渲染结果；建索引（页面加载数据库的同一入口）报错时关库后原样抛出。 */
function viewer(events, world = {}) {
  const nodes = new Map();
  const errors = [];
  const get = selector => {
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  };
  /** @brief 上下文内容块按钮取自渲染结果中的 data-context-block 属性，其余选择器没有元素。 */
  const selectAll = selector => {
    if (selector !== '#ctx-body [data-context-block]') return [];
    return Array.from(get('#ctx-body').innerHTML.matchAll(/data-context-block="([^"]+)"/g), match => {
      const button = get(`button:${match[1]}`);
      button.dataset.contextBlock = match[1];
      return button;
    });
  };
  const context = vm.createContext({
    document: {
      querySelector: get, querySelectorAll: selector => selectAll(selector),
      createElement: element, createDocumentFragment: element,
    },
    window: {addEventListener() {}},
    console: {error: (...args) => errors.push(args.map(String).join(' '))},
    setTimeout, clearTimeout, requestAnimationFrame() {},
  });
  for (const script of scripts) vm.runInContext(script, context, {filename: 'run-viewer.html'});
  const run = source => vm.runInContext(source, context);
  const state = run('S');
  state.db = database(events, world);
  try { run('buildIndex()'); } catch (error) { state.db.close(); throw error; }
  return {
    state, errors, run, get,
    activities() { run('renderActivities()'); return get('#act-body').innerHTML; },
    body() { run('renderBodyCurves()'); return get('#bd-body').innerHTML; },
    economy() { run('renderEconomy()'); return get('#eco-body').innerHTML; },
    memory() { run('renderMemory()'); return get('#mem-body').innerHTML; },
    overview() { run('renderOverview()'); return get('#ov-body').innerHTML; },
    context() { run('ContextView.render()'); return get('#ctx-body').innerHTML; },
    summary(sequence) { return state.events.find(event => event.seq === sequence).sum; },
    reload(nextEvents) {
      get('#btn-reload').onclick();
      state.db = database(nextEvents);
      run('buildIndex()');
    },
    close() { state.db.close(); },
  };
}

/** @brief 保持事件级 data 和 activity_id 的现行存储形状。 */
function event(type, data, extra = {}) {
  return {event_type: type, data, ...extra};
}

/** @brief 使用真实冻结的当前行动与续接请求，不重写提示结构。 */
function contextRequest(kind = 'action') {
  const fixture = JSON.parse(readFileSync(path.join(__dirname, '../../fixtures/viewer-context.json'), 'utf8'));
  return fixture[kind];
}

/** @brief 构造实际事件形状，并故意复用调用标记以验证因果关联。 */
function contextEvent(id, request, agent = 1) {
  return event('model_request', {request, call_id:'reused-call', attempt:1},
    {event_id:id, agent_id:agent, decision_id:'decision-' + id});
}

/** @brief 构造不属于人物行动的当前调用，验证原文保持未解析。 */
function otherRequest() {
  const request = contextRequest();
  request.messages[0].content = JSON.stringify({layer:'系统层', role:'依据人物记忆回答问题。', rules:[]});
  delete request.tools;
  return request;
}

test('context reads current seven-layer actions and complete other requests', () => {
  const app = viewer([
    contextEvent('other', otherRequest()),
    contextEvent('action', contextRequest()),
    contextEvent('continuation', contextRequest('continuation')),
  ]);
  try {
    const items = app.run('ContextView.index()');
    assert.deepEqual(Array.from(items, item => item.stage), ['other', 'action', 'action']);
    assert.equal(items[0].label, '其他调用·未解析');
    assert.equal(app.state.bodyCache.size, 0, 'Index must not load complete requests');
    const model = app.run('ContextView.read(2)');
    assert.deepEqual(Array.from(model.current.blocks.filter(block => block.role === 'user'), block => block.key),
      ['tool', 'persona', 'memory', 'todo', 'environment', 'progress']);
    assert.ok(model.current.blocks.some(block => block.key === 'tools'));
    assert.equal(model.current.warnings.length, 0);
    const user = model.current.blocks.filter(block => block.role === 'user').map(block => block.text).join('');
    assert.equal(user, contextRequest().messages[1].content);
    app.run('ContextView.select(2)');
    const html = app.get('#ctx-body').innerHTML;
    assert.ok(html.includes('结构与填充'));
    assert.ok(html.includes('层切片'));
    assert.ok(html.includes('按顶层数组无损切片'));
    assert.ok(html.includes('self_note 未经核验'));
    assert.ok(html.includes('完整请求消息与工具'));
    assert.ok(!html.includes('形成自述'));
    const continuation = app.run('ContextView.read(3)');
    assert.deepEqual(Array.from(continuation.current.blocks.filter(block => block.messageIndex > 1),
      block => `${block.role}:${block.key}`), ['assistant:message', 'assistant:reasoning', 'tool:message']);
    app.run('ContextView.select(1)');
    assert.ok(app.get('#ctx-body').innerHTML.includes('未解析'));
    assert.equal(app.run('ContextView.read(1)').current.blocks.find(block => block.role === 'user').text,
      otherRequest().messages[1].content);
    assert.deepEqual(app.errors, Array(2).fill(
      'Context message 1 was not segmented: the seven-layer contract was not matched.'));
  } finally { app.close(); }
});

test('a decision first request with extra messages is a continuation matched to its source decision', () => {
  const continued = contextRequest('continuation');
  continued.messages[2].reasoning_content = '上一决策的思考。';
  const response = {choices: [{message: {content: '', reasoning_content: '上一决策的思考。',
    tool_calls: continued.messages[2].tool_calls}}],
  usage: {prompt_tokens: 900, prompt_cache_hit_tokens: 640, prompt_cache_miss_tokens: 260, completion_tokens: 80,
    completion_tokens_details: {reasoning_tokens: 55}}};
  const app = viewer([
    contextEvent('head', contextRequest()),
    event('model_response', {call_id: 'reused-call', response}, {caused_by: ['head'], decision_id: 'decision-head'}),
    contextEvent('next', continued),
    event('model_request', {request: continued, call_id: 'second', attempt: 1},
      {event_id: 'later', decision_id: 'decision-next'}),
  ]);
  try {
    const model = app.run('ContextView.read(3)');
    assert.equal(model.current.continuation, true);
    const reasoning = model.current.blocks.find(block => block.key === 'reasoning');
    assert.equal(reasoning.text, continued.messages[2].reasoning_content);
    assert.ok(reasoning.label.endsWith(' · decision-head'), reasoning.label);
    assert.equal(app.run('ContextView.read(4)').current.continuation, false, 'only a first request continues');
    app.run('ContextView.select(3)');
    assert.ok(app.get('#ctx-body').innerHTML.includes('同秒续接：本决策首个请求多于两条消息'));
    assert.ok(app.get('#ctx-body').innerHTML.includes('续接 · assistant · 来源决策'));
    // 选中续接的 assistant 块：说明写每个世界请求各一条 tool 消息，开始组两条，changes 与 events 只在最后一条上。
    app.get('button:message:2:message').onclick();
    const filled = app.get('#ctx-body').innerHTML;
    assert.ok(filled.includes('为它的每个世界请求各追加一条 tool 消息'), filled);
    assert.ok(filled.includes('一个世界请求一条，开始组两条，changes 与 events 只写在最后一条上。'));
    assert.ok(!filled.includes('与一条 tool 消息'));
    app.run('ContextView.select(1)');
    const output = app.get('#ctx-body').innerHTML;
    assert.ok(output.includes('本次思考原文') && output.includes('上一决策的思考。'));
    assert.ok(output.includes('&quot;cache_hit_tokens&quot;: 640'));
    assert.ok(output.includes('&quot;reasoning_tokens&quot;: 55'));
    assert.equal(app.errors.length, 0, app.errors.join('\n'));
  } finally { app.close(); }
});

test('context response matching follows caused_by across interleaved agents and reused call identifiers', () => {
  const request = contextRequest();
  const app = viewer([
    contextEvent('request-one', request), contextEvent('request-two', request, 2),
    event('model_response', {call_id:'reused-call', response:{choices:[{message:{content:'second'}}]}},
      {agent_id:2, caused_by:['request-two']}),
    event('model_response', {call_id:'reused-call', response:{choices:[{message:{content:'first'}}],
      usage:{prompt_tokens:123}}}, {caused_by:['request-one']}),
    contextEvent('no-response', request),
  ]);
  try {
    assert.equal(app.run('ContextView.read(1).response.seq'), 4);
    assert.equal(app.run('ContextView.read(2).response.seq'), 3);
    assert.equal(app.run('ContextView.read(5).response'), null);
    app.run('ContextView.select(1)');
    assert.ok(app.get('#ctx-body').innerHTML.includes('123'));
    assert.ok(!app.get('#ctx-body').innerHTML.includes('&quot;second&quot;'));
    app.run('ContextView.select(5)');
    assert.ok(app.get('#ctx-body').innerHTML.includes('该请求尚无关联响应记录'));
  } finally { app.close(); }
});

test('context comparison uses same agent and stage, detects same-length edits and never crosses runs', () => {
  const original = contextRequest();
  const changed = contextRequest();
  assert.ok(changed.messages[1].content.includes('小雨'));
  changed.messages[1].content = changed.messages[1].content.replace('小雨', '晴天');
  assert.equal(changed.messages[1].content.length, original.messages[1].content.length);
  const app = viewer([
    contextEvent('first', original), contextEvent('other-agent', changed, 2),
    contextEvent('other', otherRequest()), contextEvent('later', changed),
  ]);
  try {
    const model = app.run('ContextView.read(4)');
    assert.equal(model.previous.seq, 1);
    assert.ok(model.changes.some(row => row.key === 'environment' && row.status === 'changed' && row.delta === 0));
    assert.ok(model.changes.some(row => row.key === 'tool' && row.status === 'unchanged'));
    app.run('ContextView.select(4)');
    app.get('#agent-filter').value = '2';
    const filtered = app.context();
    assert.ok(filtered.includes('事件 2'));
    assert.ok(!filtered.includes('事件 4'));
    app.reload([contextEvent('new-run', otherRequest(), 2)]);
    assert.equal(app.run('ContextView.index().length'), 1);
    assert.equal(app.run('ContextView.read(1).before'), null);
    assert.ok(!app.context().includes('事件 4'));
  } finally { app.close(); }
});

test('context preserves unparsed inputs and escapes recorded HTML including failed responses', () => {
  const request = {messages:[{role:'user',content:'<img src=x onerror=alert(1)>😀\n\nwhole input'}]};
  const app = viewer([
    contextEvent('generic', request),
    event('model_response', {phase:'failure', error:'Transport failed <script>bad()</script>', response:null},
      {caused_by:['generic']}),
  ]);
  try {
    const html = app.context();
    assert.ok(html.includes('&lt;img'));
    assert.ok(html.includes('whole input'));
    assert.ok(!html.includes('<img src=x'));
    assert.ok(html.includes('Transport failed &lt;script&gt;'));
    assert.ok(html.includes('Unicode'));
  } finally { app.close(); }
});

test('context navigation, stage filter and all rule paths are available through the actual view', () => {
  const app = viewer([contextEvent('action', contextRequest())]);
  try {
    app.run('var contextTimelineRow = document.createElement("div"); toggleDetail(contextTimelineRow, 1)');
    assert.equal(app.run('contextTimelineRow.nextElementSibling.children[0].textContent'), '查看上下文');
    app.run('contextTimelineRow.nextElementSibling.children[0].onclick()');
    assert.equal(app.state.view, 'context');
    assert.ok(app.get('#ctx-body').innerHTML.includes('系统层与六层输入 → 选择世界请求'));
    app.get('#ctx-view').onchange({target:{value:'growth'}});
    const growth = app.get('#ctx-body').innerHTML;
    assert.ok(growth.includes('ctx-rule'));
    assert.ok(growth.includes('当前源码规则'));
    // 「增长与退出」随个人事务与工作安排规格修订的规则文字逐条在真实页面里可查看（页面一次显示所选的一条规则）。
    for (const [rule, sentence] of [['todo', '未完成的个人事务取自人物状态，跨零点保留'],
      ['environment', 'duty 由场景工作安排与世界时钟求值，今日计划改不了它'],
      ['progress', '事务提醒到点（errand_reminder）与环境确认办成（errand_completed）两种记录只写事务名称，' +
        '由人物可见的事务事件派生；二者不迁入记忆层。'],
      ['tools', '去掉全部查询工具（世界查询与记忆查询），只留世界请求工具']]) {
      app.get('#ctx-rule').onchange({target: {value: rule}});
      assert.ok(app.get('#ctx-body').innerHTML.includes(sentence), sentence);
    }
    assert.equal(app.get('#ctx-prev').disabled, true);
    assert.equal(app.get('#ctx-next').disabled, true);
    app.get('#ctx-stage').onchange({target:{value:'other'}});
    assert.ok(app.get('#ctx-body').innerHTML.includes('没有匹配的模型请求'));
    assert.ok(!app.get('#ctx-body').innerHTML.includes('形成自述'));
    assert.equal(app.errors.length, 0, app.errors.join('\n'));
  } finally { app.close(); }
});

test('context drills into current tools and real-shaped progress self notes', () => {
  const request = contextRequest();
  const values = JSON.parse(request.messages[1].content);
  values[5].events = [{occurred_at:'2026-01-05T08:00:00+08:00', type:'tool_call',
    tool:'start_side_activity', result:{status:'completed'}, self_note:'先浏览。'}];
  request.messages[1].content = JSON.stringify(values);
  const app = viewer([contextEvent('current', request)]);
  try {
    app.context();
    app.get('button:message:1:tool').onclick();
    const options = app.get('#ctx-item').innerHTML;
    assert.ok(options.includes('美团（app）'));
    assert.ok(options.includes('美团 · interact · 可用'));
    // 录制的工具层里出行只有 travel 一个动作，同一目的地的各行靠出行方式的名称区分；四个旧出行动作不再出现。
    for (const method of ['步行', '公共交通', '驾车', '打车']) {
      assert.ok(options.includes(`公司 · travel · ${method} · 可用`), method);
    }
    assert.ok(options.includes('家 · interact · 测试休息 · 可用'));
    assert.ok(options.includes('家 · sleep · 可用'));
    assert.doesNotMatch(options, / · (walk|drive|take_bus|take_taxi) · /);
    assert.ok(!options.includes('：进行'));
    app.get('button:message:1:progress').onclick();
    assert.ok(app.get('#ctx-item').innerHTML.includes('self_note（未经核验）'));
    app.get('#ctx-item').onchange({target:{value:'0'}});
    assert.ok(app.get('#ctx-body').innerHTML.includes('先浏览。'));
    // 工具定义块的说明按现行工具面计数：六个世界请求、两个世界查询、三个记忆查询，共十一个；录制的请求正是这十一个。
    assert.equal(request.tools.length, 11);
    assert.deepEqual(request.tools.slice(0, 2).map(tool => tool.function.name),
      ['start_main_activity', 'start_side_activity']);
    assert.ok(!request.tools.some(tool => tool.function.name === 'start_activity'));
    app.get('button:tools').onclick();
    const filled = app.get('#ctx-body').innerHTML;
    assert.ok(filled.includes('六个世界请求工具（两个开始工具、三个活动控制工具与计划修订）、' +
      '两个世界查询工具（query_world、recall_task_summary）与三个记忆查询工具，共十一个，'), filled);
    assert.ok(!filled.includes('五个世界请求工具'));
  } finally { app.close(); }
});

/** @brief 不读录制夹具，按七层合同内联构造一个现行行动请求：角色原文取自提示的唯一来源 LIFE_SYSTEM_LAYER，工具层是
 * 提示里的紧凑分组（出行与在地点做事的行带 detail，睡眠没有），工具数组含两个开始工具。 */
function inlineContextRequest() {
  const prompt = readFileSync(path.join(__dirname, '../../../src/worldmock/agents/life_prompt.py'), 'utf8');
  const role = /LIFE_SYSTEM_LAYER[^=]*= \{\s*"layer": "系统层",\s*"role": "([^"]+)"/.exec(prompt)[1];
  const toolLayer = {layer: '工具层', groups: [
    {kind: 'location', action: 'travel', shared: {state: '全天营业', available: true}, targets: [
      {name: '公司', detail: '步行', price: '0 元', duration: '约 32 分钟', note: '费力'},
      {name: '公司', detail: '公共交通', price: '2 元', duration: '约 8 分钟', note: '轻松'}]},
    {kind: 'location', action: 'interact', shared: {state: '全天营业', available: true}, targets: [
      {name: '家', detail: '测试休息', duration: '约 15 分钟'}]},
    {kind: 'location', action: 'sleep', shared: {state: '全天营业', available: true, note: '现在不欠觉'},
      targets: [{name: '家'}]},
  ], callable_index: {}};
  const layers = [toolLayer, ...['人设层', '记忆层', '待办层', '环境层', '进度层'].map(layer => ({layer}))];
  return {model: 'test-model', messages: [
    {role: 'system', content: JSON.stringify({layer: '系统层', role, rules: []})},
    {role: 'user', content: JSON.stringify(layers)}],
  tools: ['start_main_activity', 'start_side_activity'].map(name => ({type: 'function', function: {name}}))};
}

test('context drills into an inline current tool layer and lists travel methods and location units by detail', () => {
  const app = viewer([contextEvent('current', inlineContextRequest())]);
  try {
    assert.deepEqual(Array.from(app.run('ContextView.index()'), item => item.stage), ['action']);
    app.context();
    app.get('button:message:1:tool').onclick();
    const select = app.get('#ctx-item');
    const labels = Array.from(select.innerHTML.matchAll(/<option[^>]*>([^<]*)<\/option>/g), match => match[1]);
    assert.deepEqual(labels, ['整层原文',
      '公司（location）', '公司 · travel · 步行 · 可用',
      '公司（location）', '公司 · travel · 公共交通 · 可用',
      '家（location）', '家 · interact · 测试休息 · 可用',
      '家（location）', '家 · sleep · 可用']);
    // 选中「公司 · travel · 公共交通」一项：正文是这一行的完整字段，动作在前。
    select.value = '3';
    select.onchange();
    assert.deepEqual(JSON.parse(app.get('#ctx-raw').textContent), {action: 'travel', state: '全天营业', available: true,
      name: '公司', detail: '公共交通', price: '2 元', duration: '约 8 分钟', note: '轻松', kind: 'location'});
    assert.deepEqual(app.errors, []);
  } finally { app.close(); }
});

test('context index respects the actual first message role and tools field type', () => {
  const wrongRole = contextRequest();
  wrongRole.messages[0].role = 'user';
  const nullTools = contextRequest();
  nullTools.tools = null;
  const app = viewer([contextEvent('wrong-role', wrongRole),contextEvent('null-tools',nullTools)]);
  try {
    assert.deepEqual(Array.from(app.run('ContextView.index()'), item => item.stage), ['other','other']);
    assert.equal(app.run('ContextView.read(2).current.stage'), 'other');
  } finally { app.close(); }
});

test('context comparison keeps the slicing note when only the previous input was sliced into layers', () => {
  const current = contextRequest();
  current.messages[1].content = current.messages[1].content.replace('"layer":"记忆层"', '"layer":"记忆"');
  const app = viewer([contextEvent('before',contextRequest()),contextEvent('after',current)]);
  try {
    app.run('ContextView.select(2)');
    app.get('#ctx-view').onchange({target:{value:'comparison'}});
    const html = app.get('#ctx-body').innerHTML;
    assert.ok(html.includes('退出当前输入'));
    assert.ok(html.includes('按顶层数组无损切片'));
    assert.ok(html.includes('未解析'));
  } finally { app.close(); }
});

test('action request summaries show self_note as unverified only when the reply had text', t => {
  const view = viewer([
    event('action_requested', {tool_name: 'start_side_activity', request: {action: 'interact', target: ['美团']},
      request_sha256: 'f'.repeat(64), reused: false, self_note: '先歇一会儿，再去买菜。'}),
    event('action_requested', {tool_name: 'start_main_activity', request: {action: 'sleep', target: '450', detail: null},
      request_sha256: 'e'.repeat(64), reused: false, self_note: null}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '请求 开始槽位 2 的活动 · 进行 · 美团 · self_note（未经核验）：先歇一会儿，再去买菜。');
  assert.equal(view.summary(2), '请求 开始槽位 1 的活动 · 睡觉 · 450 分钟');
  assert.deepEqual(view.errors, []);
});

/** @brief 构造受理或进展活动，目标名称由测试指定。 */
function activity(type, id, fields, extra = {}) {
  return event(type, {activity: {
    activity_id: id, status: 'active', planned_duration_seconds: 600,
    started_at: epoch, elapsed_seconds: 60, ...fields,
  }}, {activity_id: id, ...extra});
}

/** @brief 用目录浏览受理的实际嵌套位置提供条目。 */
function contentNames(contents) {
  return event('observation', {contents});
}

/** @brief 按表格类名提取表体各行的单元格文字，防止页面其他文字造成假阳性。 */
function tableRows(markup, className) {
  const table = new RegExp(`<table class="${className}">[\\s\\S]*?<tbody>([\\s\\S]*?)<\\/tbody>`, 'g');
  return [...markup.matchAll(table)].flatMap(body =>
    [...body[1].matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map(row =>
      [...row[1].matchAll(/<td(?:\s[^>]*)?>([\s\S]*?)<\/td>/g)].map(cell => cell[1])));
}

/** @brief 提取活动表的目标列。 */
function targets(markup) {
  return tableRows(markup, 'act-table').map(cells => cells[3]);
}

/** @brief 构造 start_main_activity 请求：action、target、detail 三个键都出现，睡眠的 detail 为 null。 */
function mainRequest(action, target, detail = null) {
  return event('action_requested', {tool_name: 'start_main_activity', request: {action, target, detail}});
}

/** @brief 构造 start_side_activity 请求：target 恒为名称数组，购买一份写一项。 */
function sideRequest(action, ...target) {
  return event('action_requested', {tool_name: 'start_side_activity', request: {action, target}});
}

test('locations accumulate across people and replace fixed home office market labels', t => {
  const locations = [
    {location_id: 'poi-3538660453', name: '菜饭骨头汤'},
    {location_id: 'home', name: '张江公寓'},
    {location_id: 'office', name: '人工智能研发中心'},
    {location_id: 'market', name: '浦东便民市场'},
  ];
  const events = [event('observation', {locations}), event('observation', {locations: []}, {agent_id: 2})];
  for (const place of locations) {
    events.push(mainRequest('travel', place.name, '步行'));
    events.push(activity('action_accepted', `go-${place.location_id}`, {
      kind: 'travel', destination_id: place.location_id,
    }));
    events.push(activity('activity_progress', `go-${place.location_id}`, {
      kind: 'travel', destination_id: place.location_id,
    }));
  }
  const view = viewer(events);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), locations.map(place => `→ ${place.name}`));
  locations.forEach((place, index) => {
    for (const offset of [3, 4, 5]) assert.ok(view.summary(index * 3 + offset).includes(place.name));
    assert.equal(view.summary(index * 3 + 3), `请求 开始槽位 1 的活动 · ${place.name} · 出行 · 步行`);
  });
  assert.deepEqual(view.errors, []);
});

test('graph travel shows recorded origin destination and every path place', t => {
  const names = ['张江公寓', '社区便利店', '人工智能研发中心'];
  const ids = ['home', 'shop', 'office'];
  const data = {trip_id:'home->office:walking', origin_id:'home', destination_id:'office',
    path:ids, distance_meters:3274};
  const view = viewer([
    event('observation', {locations:ids.map((location_id, index) => ({location_id, name:names[index]}))}),
    activity('action_accepted', 'commute', {kind:'travel', location_id:'home', destination_id:'office',
      travel_method_id:'walking', data}),
    activity('activity_progress', 'commute', {kind:'travel', location_id:'home', destination_id:'office',
      travel_method_id:'walking', data}),
  ]);
  t.after(() => view.close());
  // 自带的步行不依赖观察里的绑定，出行行在路线前写出行方式名称
  const path = '步行 · 张江公寓 → 人工智能研发中心 · 路径：张江公寓 → 社区便利店 → 人工智能研发中心';
  assert.deepEqual(targets(view.activities()), [path]);
  for (const sequence of [2, 3]) {
    assert.ok(view.summary(sequence).includes(path));
    assert.doesNotMatch(view.summary(sequence), /home|shop|office|3274|walking/);
  }
  assert.deepEqual(view.errors, []);
});

test('travel shows the dataset name of its travel method from the recorded catalog bindings', t => {
  const methods = [['walking', '步行'], ['bus', '公共交通'], ['ferry-line-2', '二号线轮渡']];
  const binding = {object: '公司', kind: 'location', object_id: 'office', actions: methods.map(
    ([travel_method_id, detail]) => ({action: 'travel', detail, note: null, travel_method_id}))};
  const trip = (id, travel_method_id) => ({kind: 'travel', destination_id: 'office', travel_method_id,
    data: {action: 'travel'}});
  const view = viewer([
    event('observation', {locations: [{location_id: 'office', name: '公司'}], catalog_bindings: [binding]}),
    activity('action_accepted', 'by-bus', trip('by-bus', 'bus')),
    activity('action_accepted', 'by-ferry', trip('by-ferry', 'ferry-line-2')),
    activity('action_accepted', 'on-foot', trip('on-foot', 'walking')),
    activity('action_accepted', 'unrecorded', trip('unrecorded', 'hovercraft')),
  ]);
  t.after(() => view.close());
  // 名称取本次运行记录的冻结目录绑定；没有记下名称的身份显式报告缺失，不猜测也不显示引擎自带的旧说法
  assert.deepEqual(targets(view.activities()), [
    '公共交通 · → 公司', '二号线轮渡 · → 公司', '步行 · → 公司', '名称缺失（hovercraft） · → 公司']);
  assert.equal(view.summary(2), '已受理 · 出行 · 公共交通 · → 公司 · 计划 10分');
  assert.doesNotMatch(view.activities(), /乘公交车|乘出租车|ferry-line-2|"bus"/);
  assert.deepEqual(view.errors, ['Missing recorded object name: hovercraft']);
});

test('goods actions apps and contents remain named after later sparse observations', t => {
  const view = viewer([
    event('observation', {
      goods: [{goods_id: 'breakfast-custom', name: '红烧牛肉面'}],
      apps: [{app_id: 'app-bili', name: '哔哩哔哩'}, {app_id: 'app-pay', name: '支付宝'}],
            contents: [{content_id: 'article-one', action: 'interact', title: '城市生活随笔'},
        {content_id: 'course-one', action: 'interact', title: '数据分析课'}],
    }),
    event('observation', {goods: [], apps: [], contents: []}, {agent_id: 2}),
    activity('action_accepted', 'browse', {kind: 'interact', content_title: '城市生活随笔',
      data: {action: 'interact', app_id: 'app-bili', content_id: 'article-one'}}),
    activity('action_accepted', 'work', {kind: 'interact', content_title: '项目会议纪要', data: {
      action: 'interact', app_id: null, content_id: 'meeting'}}),
    activity('action_accepted', 'rest', {kind: 'interact', content_title: '安静休息', data: {
      action: 'interact', app_id: null, content_id: 'resting'}}),
    activity('action_accepted', 'course', {kind: 'interact', content_title: '数据分析课',
      data: {action: 'interact', app_id: 'app-bili', content_id: 'course-one'}}),
    event('action_result', {status: 'completed', data: {
      orders: [{goods_id: 'breakfast-custom', total_price_fen: 1800, payment_app_id: 'app-pay'}]}}),
    event('environment_event',{kind:'purchase_payment'},{resource_changes:{'account:agent:1':-1800,'account:external:scenario':1800}}),
  ]);
  t.after(() => view.close());
  const rendered = targets(view.activities());
  assert.deepEqual(rendered, ['城市生活随笔', '项目会议纪要', '安静休息', '数据分析课']);
  assert.equal(view.run('targetLabel("article-one")'), '城市生活随笔');
  assert.ok(view.economy().includes('购买付款'));
  assert.ok(view.summary(7).includes('订单 红烧牛肉面 ¥18.00 · 支付 支付宝'));
  assert.deepEqual(view.errors, []);
});

test('app pushes preserve registered names and complete source facts', t => {
  const view = viewer([event('environment_event', {kind:'app_push', push_id:'push-one',
    app_id:'app-douyin', app:'抖音', content_id:'video-one', title:'城市生活短视频',
    summary:'下班路上的城市见闻。', issued_at:epoch}, {visibility:'agent'})]);
  t.after(() => view.close());
  assert.ok(view.summary(1).includes('抖音 · 《城市生活短视频》'));
  assert.ok(view.summary(1).includes('下班路上的城市见闻。'));
  assert.doesNotMatch(view.summary(1), /app-douyin|video-one|push-one|undefined/);
  assert.deepEqual(view.errors, []);
});

test('goods receipt and alarm events use the current environment labels', t => {
  const view = viewer([
    event('environment_event', {kind: 'goods_received', order_id: 'order-one', goods_id: 'goods-milk',
      category: 'drink', quantity: 1, provided: 100, applied: 100}),
    event('environment_event', {kind: 'callback_reminder', rule_id: 'state-reminder'}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '世界事件 · 商品收货');
  assert.equal(view.summary(2), '世界事件 · 状态提醒');
  assert.deepEqual(view.errors, []);
});

test('phone records written by the engine use their own environment labels', t => {
  const view = viewer([
    event('environment_event', {kind: 'app_foreground', app_id: 'amap', step: 'navigation',
      interval_start: epoch, interval_end: epoch + 660}, {visibility: 'system'}),
    event('environment_event', {kind: 'order_notification', app_id: 'luckin', order_ids: ['order-one'],
      title: '订单已发货', summary: '冰美式已发货'}, {visibility: 'system'}),
    event('environment_event', {kind: 'location_entered', session_id: 'session-1-0003', location_id: 'office',
      entry_kind: 'arrival'}, {visibility: 'system'}),
    event('environment_event', {kind: 'location_left', session_id: 'session-1-0001', location_id: 'home'},
      {visibility: 'system'}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '世界事件 · app 前台区间');
  assert.equal(view.summary(2), '世界事件 · 订单通知');
  assert.equal(view.summary(3), '世界事件 · 进入地点');
  assert.equal(view.summary(4), '世界事件 · 离开地点');
  assert.deepEqual(view.errors, []);
});

test('each activity shows its actual current episode title without mixing another activity', t => {
  const view = viewer([
    event('observation', {apps: [{app_id:'app-bili', name:'哔哩哔哩'}]}),
    activity('action_accepted', 'watch-first', {kind: 'interact', content_title: '春日剧集第一集',
      data: {action: 'interact', app_id: 'app-bili', content_id: 'episode-one'}}),
    activity('activity_progress', 'watch-first', {kind: 'interact', content_title: '春日剧集第二集',
      data: {action: 'interact', app_id: 'app-bili', content_id: 'episode-two'}}),
    activity('activity_progress', 'watch-first', {kind: 'interact', content_title: '春日剧集第二集',
      data: {action: 'interact', app_id: 'app-bili', content_id: 'episode-two'}}),
    activity('action_accepted', 'watch-second', {
      kind: 'interact', content_title: '城市散步',
      data: {action: 'interact', app_id: null, content_id: 'walk-talk'},
    }, {agent_id: 2}),
    activity('activity_progress', 'watch-first', {
      kind: 'interact', content_title: '春日剧集第二集',
      data: {action: 'interact', app_id: 'app-bili', content_id: 'episode-two'},
    }),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), ['春日剧集第二集', '城市散步']);
  assert.ok(view.summary(2).includes('春日剧集第一集'));
  assert.ok(view.summary(3).includes('春日剧集第二集'));
  assert.ok(view.summary(5).includes('城市散步'));
});

test('progress-only activity titles name both activity rows and request summaries', t => {
  const title = '城市生活随笔';
  const view = viewer([
    mainRequest('interact', '张江公寓', title),
    activity('activity_progress', 'only-progress', {kind: 'interact', content_title: title,
      data: {action: 'interact', app_id: null, content_id: 'article'}}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), [title]);
  assert.equal(view.summary(1), `请求 开始槽位 1 的活动 · 张江公寓 · 进行 · ${title}`);
  assert.ok(view.summary(2).includes(title));
  assert.deepEqual(view.errors, []);
});

test('the two start tools display their own arguments: place, action and detail, or action and the named objects', t => {
  const view = viewer([
    event('observation', {locations: [{location_id: 'home', name: '张江公寓'}],
      goods: [{goods_id: 'food-rice', name: '菜饭'}]}),
    sideRequest('purchase', '菜饭', '菜饭', '柠檬茶'),
    sideRequest('interact', '美团'),
    mainRequest('travel', '张江公寓', '驾车'),
    mainRequest('interact', '张江公寓', '测试休息'),
    mainRequest('sleep', '450'),
  ]);
  t.after(() => view.close());
  // 副工具：动作在前，名称数组照所写顺序以顿号相连，同名写两次就是两份。
  assert.equal(view.summary(2), '请求 开始槽位 2 的活动 · 购买 · 菜饭、菜饭、柠檬茶');
  assert.equal(view.summary(3), '请求 开始槽位 2 的活动 · 进行 · 美团');
  // 主工具：地点名、动作、detail（出行方式名称或内容单元名）；睡眠只写分钟数。
  assert.equal(view.summary(4), '请求 开始槽位 1 的活动 · 张江公寓 · 出行 · 驾车');
  assert.equal(view.summary(5), '请求 开始槽位 1 的活动 · 张江公寓 · 进行 · 测试休息');
  assert.equal(view.summary(6), '请求 开始槽位 1 的活动 · 睡觉 · 450 分钟');
  for (const sequence of [2, 3, 4, 5, 6]) {
    assert.doesNotMatch(view.summary(sequence),
      /food-rice|purchase|interact|travel|sleep|start_main_activity|start_side_activity|null|undefined/);
  }
  assert.deepEqual(view.errors, []);
});

test('readableReq reads the two start tools directly and keeps no branch for the deleted start_activity', t => {
  const view = viewer([
    event('action_requested', {tool_name: 'start_activity',
      request: {items: [{action: 'walk', target: '公司'}, {action: 'interact', target: '美团'}]}}),
  ]);
  t.after(() => view.close());
  const readable = (tool, request) => view.run(`readableReq(${JSON.stringify(tool)}, ${JSON.stringify(request)})`);
  assert.deepEqual(JSON.parse(JSON.stringify(view.run('ACTION_LABELS'))),
    {travel: '出行', purchase: '购买', sleep: '睡觉', interact: '进行'});
  assert.equal(view.run('TOOL_LABELS.start_main_activity'), '开始槽位 1 的活动');
  assert.equal(view.run('TOOL_LABELS.start_side_activity'), '开始槽位 2 的活动');
  for (const [detail, text] of [['步行', '公司 · 出行 · 步行'], ['驾车', '公司 · 出行 · 驾车'],
    ['打车', '公司 · 出行 · 打车'], ['公共交通', '公司 · 出行 · 公共交通']]) {
    assert.equal(readable('start_main_activity', {action: 'travel', target: '公司', detail}), text);
  }
  assert.equal(readable('start_main_activity', {action: 'interact', target: '家', detail: '测试闲聊'}),
    '家 · 进行 · 测试闲聊');
  assert.equal(readable('start_main_activity', {action: 'sleep', target: '10', detail: null}), '睡觉 · 10 分钟');
  // 请求在事件里既可能是对象也可能是 JSON 文本（时间线索引取出的原文），两种读法结果相同。
  assert.equal(readable('start_main_activity', JSON.stringify({action: 'sleep', target: '720', detail: null})),
    '睡觉 · 720 分钟');
  assert.equal(readable('start_side_activity', {action: 'purchase', target: ['牛肉面']}), '购买 · 牛肉面');
  assert.equal(readable('start_side_activity', {action: 'purchase', target: ['牛肉面', '薯片', '薯片']}),
    '购买 · 牛肉面、薯片、薯片');
  assert.equal(readable('start_side_activity', {action: 'interact', target: ['哔哩哔哩']}), '进行 · 哔哩哔哩');
  // 已删除的工具没有标签也没有读取分支：工具名原样显示，请求内容不被解读成两个开始工具的任何一种。
  assert.equal(view.run('TOOL_LABELS.start_activity'), undefined);
  assert.equal(readable('start_activity', {items: [{action: 'walk', target: '公司'}]}), '');
  assert.equal(view.summary(1), '请求 start_activity · ');
  assert.deepEqual(view.errors, []);
});

test('plan_day is no longer a person tool while day plan revisions keep their label', t => {
  const view = viewer([
    event('action_requested', {tool_name: 'plan_day', request: {goals: [{goal: '上班'}]}}),
    event('action_requested', {tool_name: 'revise_day_plan', request: {goals: [{goal: '上班'}, {goal: '买菜'}]}}),
  ]);
  t.after(() => view.close());
  assert.equal(view.run("TOOL_LABELS.plan_day"), undefined);
  assert.equal(view.summary(1), '请求 plan_day · ');
  assert.doesNotMatch(view.summary(1), /当日目标|项目标/);
  assert.equal(view.summary(2), '请求 调整当日目标 · 2 项目标');
});

test('registered app names describe platform browse while content titles remain authoritative', t => {
  const view = viewer([
    event('observation', {apps:[{app_id:'app-bili',name:'哔哩哔哩'}]}),
    activity('action_accepted','browse',{kind:'interact',data:{action:'interact',app_id:'app-bili',content_id:null}}),
    activity('action_accepted','content',{kind:'interact',content_title:'城市生活随笔',
      data:{action:'interact',app_id:'app-bili',content_id:'article-one'}}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), ['哔哩哔哩','城市生活随笔']);
  assert.deepEqual(view.errors, []);
});

test('session requests display each task with its own progress sentence', t => {
  const memory = text => ({type: 'routine', text});
  const opinion = (about, text) => ({type: 'relationship', about, text});
  const item = (activity_name, summary, extra = {}) => ({activity_name, summary, ...extra});
  const view = viewer([
    event('action_requested', {tool_name: 'report_status', request: {
      items: [item('整理访谈','完成提纲'),item('准备周会','列出议题')], errands: [],
    }}),
    event('action_requested', {tool_name: 'conclude_activity', request: {
      items: [item('整理访谈','已经提交纪要',{memories:[memory('纪要要当天交'),memory('先列提纲')],views:[]})], errands: [],
    }}),
    event('action_requested', {tool_name: 'conclude_activity', request: {
      items: [item('准备周会','议题定了',{memories:[],views:[]}),item('午饭','吃完了',{memories:[memory('菜饭偏咸')],views:[]})],
      errands: [],
    }}),
    event('action_requested', {tool_name: 'pause_activity', request: {
      items: [item('准备周会','先放一放')], errands: [],
    }}),
    event('action_requested', {tool_name: 'conclude_activity', request: {
      items: [item('午饭','吃完了',{memories:[],views:[]})], errands: [],
    }}),
    event('action_requested', {tool_name: 'report_status', request: {items: [
      item('整理访谈', '完成提纲'), item('准备周会', '列出议题'),
    ], errands: []}}),
    event('action_requested', {tool_name: 'conclude_activity', request: {items: [
      item('整理访谈', '已经提交纪要', {memories: [memory('纪要要当天交')], views: [opinion('王磊', '王磊很靠谱')]}),
      item('午饭', '吃完了', {memories: [], views: []}),
    ], errands: []}}),
    event('action_requested', {tool_name: 'pause_activity', request: {items: [item('准备周会', '先放一放')], errands: []}}),
    event('action_requested', {tool_name: 'conclude_activity', request: {items: [
      item('准备周会', '议题定了', {memories: [], views: []}),
    ], errands: []}}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '请求 播报进展 · 整理访谈：完成提纲；准备周会：列出议题');
  assert.equal(view.summary(2), '请求 结束活动 · 整理访谈：已经提交纪要 · 记忆 2 条');
  assert.equal(view.summary(3), '请求 结束活动 · 准备周会：议题定了；午饭：吃完了 · 记忆 1 条');
  assert.equal(view.summary(4), '请求 暂停活动 · 准备周会：先放一放');
  assert.equal(view.summary(5), '请求 结束活动 · 午饭：吃完了 · 记忆 0 条');
  assert.ok(!view.summary(1).includes('task='));
  // 现行一项一个对象的请求经 controlItems 读出同样的逐项配对，看法与普通记忆合计计数（第六章「一项一个对象」）。
  assert.equal(view.summary(6), view.summary(1));
  assert.equal(view.summary(7), '请求 结束活动 · 整理访谈：已经提交纪要；午饭：吃完了 · 记忆 2 条');
  assert.equal(view.summary(8), view.summary(4));
  assert.equal(view.summary(9), '请求 结束活动 · 准备周会：议题定了 · 记忆 0 条');
  const read = request => JSON.parse(JSON.stringify(view.run(`controlItems('conclude_activity', ${
    JSON.stringify(request)}).map(entry => [entry.name, entry.summary, entry.memories.map(one => one.text)])`)));
  assert.deepEqual(read({items: [
    item('整理访谈', '已经提交纪要', {memories: [memory('纪要要当天交')], views: [opinion('王磊', '王磊很靠谱')]}),
  ], errands: []}), [['整理访谈','已经提交纪要',['纪要要当天交','王磊很靠谱']]]);
  assert.throws(() => read({task:['整理访谈'],summary:['完成']}), /requires items/);
  // 带 items 而缺请求级 errands 的控制请求是旧形状记录，镜像实现同 control_items 一样报错（个人事务规格 2.9）。
  assert.throws(() => read({items: [item('整理访谈', '已经提交纪要', {memories: [], views: []})]}), /requires errands/);
  assert.throws(() => read({items: [item('整理访谈', '已经提交纪要', {memories: [], views: []})], errands: null}),
    /requires errands/);
  assert.deepEqual(view.errors, []);
  for (const entry of view.state.events) assert.doesNotMatch(entry.sum, /undefined/);
});

test('activity end reasons name reminders route and partner endings pauses and cancellations', t => {
  const reasons = [
    ['route_ended', '随出行结束'], ['feedback', '反馈中断'],
    ['paused', '已暂停'], ['cancelled', '主动结束'], ['planned_end', '按计划结束'],
  ];
  const view = viewer([
    ...reasons.map(([boundary_type], index) => activity('activity_progress', `ended-${index}`, {
      kind: 'interact', status: 'completed', boundary_type,
    })),
    activity('activity_progress', 'truncated', {kind: 'interact', status: 'truncated', boundary_type: 'experiment_end'}),
  ]);
  t.after(() => view.close());
  reasons.forEach(([, label], index) => assert.ok(view.summary(index + 1).endsWith(` · ${label}`)));
  assert.ok(view.summary(reasons.length + 1).endsWith(' · experiment_end'));
  for (const item of view.state.events) assert.doesNotMatch(item.sum, /undefined/);
});

test('environment events name midnight plans and activity lifecycle records', t => {
  const remindAt = Date.parse('2026-01-05T08:00:00+08:00') / 1000;
  const lifecycle = [
    ['activity_concluded', '活动收尾'], ['activity_paused', '活动暂停'],
    ['activity_resumed', '活动恢复'], ['activity_truncated', '活动截断'],
  ];
  const view = viewer([
    event('environment_event', {kind: 'day_plan_generated', day_plan: {day_end: epoch + 86400, goals: [
      {intent_kind: '工作', goal: '按时上班', registered_at: epoch, remind_at: remindAt},
      {intent_kind: '生活', goal: '买菜', registered_at: epoch, remind_at: null},
    ]}}, {visibility: 'system'}),
    event('environment_event', {kind: 'day_plan_generated', day_plan: {day_end: epoch + 86400, goals: []}}),
    ...lifecycle.map(([kind]) => event('environment_event', {kind, activity_id: 'act-1', activity: {
      activity_id: 'act-1', kind: 'interact', name: '等待（1月5日 06:00 起）',
    }})),
    event('environment_event', {kind: 'body_settlement'}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '世界事件 · 零点计划生成 · 2 项目标 · 提醒 01/05 08:00');
  assert.equal(view.summary(2), '世界事件 · 零点计划生成 · 0 项目标');
  lifecycle.forEach(([, label], index) => assert.equal(
    view.summary(index + 3), `世界事件 · ${label} · 等待（1月5日 06:00 起）`));
  assert.equal(view.summary(lifecycle.length + 3), '世界事件 · body_settlement');
});

test('rejections show the same-moment round instead of consecutive rejections', t => {
  const view = viewer([
    event('action_rejected', {error_code: 'ACTION_REQUEST_INVALID', same_moment_round: 2,
      max_same_moment_decisions: 3, next_decision_at: epoch, reason: '所选的对象不在本轮列出的范围内'}),
    event('action_rejected', {error_code: 'ACTIVITY_DURATION_INVALID', same_moment_round: 1}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '被拒绝 · ACTION_REQUEST_INVALID · 同刻第 2 轮（上限 3）');
  assert.equal(view.summary(2), '被拒绝 · ACTIVITY_DURATION_INVALID · 同刻第 1 轮');
  for (const sequence of [1, 2]) assert.doesNotMatch(view.summary(sequence), /连续拒绝/);
});

test('decision starts show their wake reasons from the frozen vocabulary', t => {
  const names = ['planned_end','felt_band_change','delivery_arrival','reconsideration_interval',
    'rejection_feedback','session_open','progress_report_due','no_active_activity','reminder_due','duty_boundary','app_push'];
  const due = epoch + 3600;
  const view = viewer([
    event('decision_started', {round_order: 0, wake_reasons: ['session_open']}),
    event('decision_started', {round_order: 0, wake_reasons: [
      'no_active_activity', 'reminder_due', 'progress_report_due']}),
    event('decision_started', {round_order: 1, wake_reasons: []}),
    event('decision_started', {round_order: 0}),
    // 词表含 errand_reminder_due：同一人物同一时刻先交付一条事务提醒，该原因才有事务名称可写。
    event('observation', {goods: [{goods_id: 'food-milk', name: '纯牛奶'}]}),
    errandEvent('errand_reminded', errandSnapshot({reminded_at: due}), {}, {occurred_at: due}),
    event('decision_started', {round_order: 0, wake_reasons: names}, {occurred_at: due}),
  ]);
  t.after(() => view.close());
  const labelsByName = JSON.parse(view.run('JSON.stringify(WAKE_LABELS)'));
  assert.deepEqual(Object.keys(labelsByName).sort(), [...names].sort());
  assert.equal(labelsByName.app_push, 'app 推送');
  assert.equal(view.summary(1), '决策开始 · 第 0 轮 · 唤醒：会话开始');
  assert.equal(view.summary(2), '决策开始 · 第 0 轮 · 唤醒：没有进行中的活动、提醒到点、该播报进展');
  assert.equal(view.summary(3), '决策开始 · 第 1 轮');
  assert.equal(view.summary(4), '决策开始 · 第 0 轮');
  const labels = view.summary(7).split(' · 唤醒：')[1].split('、');
  assert.equal(labels.length, names.length);
  labels.forEach((label, index) => assert.match(label,
    names[index] === 'reminder_due' ? /^[一-鿿]+「买牛奶」$/ :
      names[index] === 'app_push' ? /^app 推送$/ : /^[一-鿿]+$/, names[index]));
  assert.deepEqual(view.errors, []);
});

/** @brief 控制请求里的一条个人事务变更：七个键全部写出（src/worldmock/life/models.py 的 ErrandChange），fields 覆盖
 *  该变更种类要写的键。 */
function errandChange(change, name, fields = {}) {
  return {change, name, item: null, quantity: null, memo: null, remind_time: null, reason: null, ...fields};
}

/** @brief 事件载荷里的事务快照，按 ErrandState 的十二个字段（src/worldmock/life/errand_state.py）；fields 覆盖个别字段。 */
function errandSnapshot(fields = {}) {
  return {errand_id: 'action-0007:errand:0', name: '买牛奶', product_id: 'food-milk', quantity: 2,
    progress: 0, memo: '下班顺路去便利店', registered_at: epoch, remind_at: epoch + 3600, reminded_at: null,
    origin_action_id: 'action-0007', origin_event_id: 'event-0042', ...fields};
}

/** @brief 一条个人事务事件的存储形状（src/worldmock/life/errands.py 的 _draft）：environment_event，载荷为 {kind, errand}
 *  另加 extra（quantity_added 或 reason），决策、动作与活动身份为空，提醒与办成两种人物可见。 */
function errandEvent(kind, snapshot, extra = {}, envelope = {}) {
  return event('environment_event', {kind, errand: snapshot, ...extra}, {
    decision_id: null, action_id: null, activity_id: null, source_kind: 'environment',
    visibility: ['errand_completed', 'errand_reminded'].includes(kind) ? 'agent' : 'system', ...envelope,
  });
}

/** @brief 事务内部身份、来源动作与来源事件身份的形态：活动表、时间线与请求摘要里都不得出现。 */
const ERRAND_IDENTITY = /action-00\d\d|errand:\d|event-0042/;

test('control request summaries write each errand change kind and name after the activity summaries', t => {
  const item = (activity_name, summary, extra = {}) => ({activity_name, summary, ...extra});
  const added = errandChange('新增', '买牛奶', {item: '纯牛奶', quantity: 2,
    memo: '下班顺路去便利店', remind_time: '18:30'});
  const updated = errandChange('更新', '取快递', {memo: '改到明早去驿站', remind_time: '08:00'});
  const cancelled = errandChange('取消', '订<蛋糕>', {reason: '聚会改期了'});
  const view = viewer([
    event('action_requested', {tool_name: 'report_status', request: {
      items: [item('整理访谈', '完成提纲'), item('准备周会', '列出议题')], errands: [added]}}),
    event('action_requested', {tool_name: 'pause_activity', request: {
      items: [item('准备周会', '先放一放')], errands: [updated, cancelled]}}),
    event('action_requested', {tool_name: 'conclude_activity', request: {
      items: [item('午饭', '吃完了', {memories: [{type: 'routine', text: '菜饭偏咸'}], views: []})],
      errands: [cancelled, updated, added]}, self_note: '吃完顺手把牛奶记上。'}),
    event('action_requested', {tool_name: 'report_status', request: {items: [item('整理访谈', '完成提纲')], errands: []}}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '请求 播报进展 · 整理访谈：完成提纲；准备周会：列出议题 · 事务：新增「买牛奶」');
  assert.equal(view.summary(2), '请求 暂停活动 · 准备周会：先放一放 · 事务：更新「取快递」、取消「订<蛋糕>」');
  assert.equal(view.summary(3), '请求 结束活动 · 午饭：吃完了 · 记忆 1 条 · 事务：取消「订<蛋糕>」、更新「取快递」、新增「买牛奶」' +
    ' · self_note（未经核验）：吃完顺手把牛奶记上。');
  assert.equal(view.summary(4), '请求 播报进展 · 整理访谈：完成提纲');
  const row = view.run('tlRow(S.events[1]).innerHTML');
  assert.ok(row.includes('更新「取快递」、取消「订&lt;蛋糕&gt;」') && !row.includes('<蛋糕>'));
  assert.deepEqual(view.errors, []);
  for (const entry of view.state.events) assert.doesNotMatch(entry.sum, /undefined|null/);
});

test('errand changes are read by the same rule as control_errands: seven keys present, values as written', t => {
  const view = viewer([]);
  t.after(() => view.close());
  // 七个键与顺序取自 Python 读取入口的 _ERRAND_KEYS，镜像实现不得另立一套。
  const reader = readFileSync(path.join(__dirname, '../../../src/worldmock/life/control_items.py'), 'utf8');
  const keys = [.../_ERRAND_KEYS = \(([^)]*)\)/.exec(reader)[1].matchAll(/"([a-z_]+)"/g)].map(match => match[1]);
  assert.equal(keys.length, 7);
  assert.deepEqual(JSON.parse(JSON.stringify(view.run('ERRAND_CHANGE_KEYS'))), keys);
  const read = request => JSON.parse(JSON.stringify(view.run(`controlErrands(${JSON.stringify(request)})`)));
  const added = errandChange('新增', '买牛奶', {item: '纯牛奶', quantity: 2, remind_time: '18:30'});
  const cancelled = errandChange('取消', '订<蛋糕>', {reason: '聚会改期了'});
  assert.deepEqual(read({items: [], errands: [added, cancelled]}), [added, cancelled]);
  assert.deepEqual(read({items: [], errands: [{...added, extra: 1}]}).map(Object.keys), [keys]);
  assert.deepEqual(read({items: [], errands: []}), []);
  // 只查键在不在，不查值的类型：值照请求所写读出（请求模型在记录之前已校验类型）。
  assert.deepEqual(read({items: [], errands: [{...added, name: 5, quantity: '两份'}]}),
    [{...added, name: 5, quantity: '两份'}]);
  assert.throws(() => read({items: []}), /requires errands/);
  assert.throws(() => read({items: [], errands: {}}), /requires errands/);
  for (const entry of [null, '新增', 7, [added]]) {
    assert.throws(() => read({items: [], errands: [entry]}), /errand change is not an object/, JSON.stringify(entry));
  }
  for (const key of keys) {
    const {[key]: dropped, ...rest} = added;
    assert.throws(() => read({items: [], errands: [added, rest]}), new RegExp(`errand change lacks ${key}$`), key);
  }
  assert.throws(() => read({items: [], errands: [{change: '新增', name: '买牛奶'}]}), /errand change lacks item$/);
});

test('a control request that is not in the current shape fails the timeline index instead of being summarised', () => {
  const item = (activity_name, summary, extra = {}) => ({activity_name, summary, ...extra});
  const requested = (tool_name, request) => [event('action_requested', {tool_name, request})];
  const closing = [item('午饭', '吃完了', {memories: [], views: []})];
  // 页面加载数据库走的就是建索引这一步（handleFiles 把它的报错显示为加载失败）：缺 errands 的旧形状记录不留读取分支。
  for (const [tool, items] of [['report_status', [item('整理访谈', '完成提纲')]],
    ['pause_activity', [item('准备周会', '先放一放')]], ['conclude_activity', closing]]) {
    assert.throws(() => viewer(requested(tool, {items})), /Activity control request requires errands/, tool);
    assert.throws(() => viewer(requested(tool, {items, errands: null})), /requires errands/, tool);
    assert.throws(() => viewer(requested(tool, {items, errands: {}})), /requires errands/, tool);
    assert.throws(() => viewer(requested(tool, {errands: []})), /requires items/, tool);
    // 条目级的出入与缺键同等对待：一条变更不是对象或缺七键之一，整库不加载。
    assert.throws(() => viewer(requested(tool, {items, errands: [null]})), /errand change is not an object/, tool);
    assert.throws(() => viewer(requested(tool, {items, errands: [{change: '新增', name: '买牛奶'}]})),
      /errand change lacks item/, tool);
    const view = viewer(requested(tool, {items, errands: [errandChange('取消', '买牛奶', {reason: '不买了'})]}));
    assert.ok(view.summary(1).endsWith(' · 事务：取消「买牛奶」'), view.summary(1));
    view.close();
  }
  // 其他工具的请求不带 errands，照常显示。
  const other = viewer([sideRequest('interact', '美团')]);
  assert.equal(other.summary(1), '请求 开始槽位 2 的活动 · 进行 · 美团');
  other.close();
});

test('errand events show their Chinese wording, the errand name and the recorded product, never the errand identity', t => {
  const due = epoch + 3600;
  const kinds = [
    ['errand_registered', '事务记下', {}, {}],
    ['errand_updated', '事务更新', {memo: '改到晚饭后去', remind_at: due + 1800}, {}],
    ['errand_progressed', '事务进展', {progress: 1}, {quantity_added: 1}],
    ['errand_reminded', '事务提醒', {progress: 1, reminded_at: due}, {}],
    ['errand_completed', '事务办成', {progress: 2, reminded_at: due}, {quantity_added: 1}],
    ['errand_cancelled', '事务取消', {}, {reason: '聚会改期了'}],
  ];
  const view = viewer([
    event('observation', {goods: [{goods_id: 'food-milk', name: '纯牛奶'}]}),
    activity('action_accepted', 'act-wait', {kind: 'interact'}),
    ...kinds.map(([kind, , fields, extra]) => errandEvent(kind, errandSnapshot(fields), extra)),
  ]);
  t.after(() => view.close());
  kinds.forEach(([kind, label], index) => {
    assert.equal(view.summary(index + 3), `世界事件 · ${label} · 「买牛奶」 · 商品 纯牛奶`, kind);
    assert.equal(JSON.parse(view.run(`fetchBody(${index + 3})`)).data.errand.errand_id, 'action-0007:errand:0',
      'the stored event keeps its snapshot');
  });
  const hidden = new RegExp(`${ERRAND_IDENTITY.source}|food-milk`);
  view.state.events.forEach((entry, index) => {
    assert.doesNotMatch(entry.sum, hidden);
    assert.doesNotMatch(view.run(`tlRow(S.events[${index}]).innerHTML`), hidden);
  });
  assert.doesNotMatch(view.activities(), hidden);
  assert.deepEqual(view.errors, []);
});

test('errand products are named from the errand item bindings when the run lists them nowhere else', t => {
  // 事务绑定列出目录里的全部商品名（src/worldmock/life/errands.py 的 bindings：按商品身份排序的
  // {name, product_id}），名称索引把它作为只补缺来源。
  const bindings = [{name: '康师傅 冰红茶', product_id: 'food-tea'}, {name: '纯牛奶', product_id: 'food-milk'},
    {name: '素炒什锦食材包', product_id: 'ingredient-kit'}];
  const observed = (fields = {}, envelope = {}) => event('observation',
    {goods: [], errand_bindings: [], errand_item_bindings: bindings, ...fields}, envelope);
  const view = viewer([
    observed(),
    errandEvent('errand_registered', errandSnapshot()),
    errandEvent('errand_registered', errandSnapshot({name: '补食材包', errand_id: 'action-0007:errand:1',
      product_id: 'ingredient-kit'})),
    errandEvent('errand_reminded', errandSnapshot({name: '囤冰红茶', errand_id: 'action-0007:errand:2',
      product_id: 'food-tea'})),
    observed({}, {agent_id: 2}),
    event('action_result', {status: 'completed', data: {
      orders: [{goods_id: 'food-tea', total_price_fen: 450, payment_app_id: null}]}}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(2), '世界事件 · 事务记下 · 「买牛奶」 · 商品 纯牛奶');
  assert.equal(view.summary(3), '世界事件 · 事务记下 · 「补食材包」 · 商品 素炒什锦食材包');
  assert.equal(view.summary(4), '世界事件 · 事务提醒 · 「囤冰红茶」 · 商品 康师傅 冰红茶');
  assert.ok(view.summary(6).includes('订单 康师傅 冰红茶'), 'the same index names every other reader');
  assert.deepEqual(JSON.parse(JSON.stringify([...view.state.nameIdx.targets])),
    [['food-milk', '纯牛奶'], ['food-tea', '康师傅 冰红茶'], ['ingredient-kit', '素炒什锦食材包']],
    'one row per product however many observations repeat the bindings');
  assert.deepEqual(view.errors, []);
  for (const entry of view.state.events) assert.doesNotMatch(entry.sum, /food-|ingredient-|action-00|errand:\d/);
  view.reload([errandEvent('errand_registered', errandSnapshot())]);
  assert.equal(view.summary(1), '世界事件 · 事务记下 · 「买牛奶」 · 商品 名称缺失（food-milk）',
    'bindings of the previous run are dropped');
});

test('errand item bindings only fill names the run records nowhere else and never an empty or identity name', t => {
  const view = viewer([
    event('observation', {goods: [{goods_id: 'food-milk', name: '纯牛奶'}],
      interaction_bindings: [{object_id: 'food-held', object: '全麦面包'}],
      errand_item_bindings: [{name: '臻浓牛奶', product_id: 'food-milk'}, {name: '全麦面包', product_id: 'food-held'},
        {name: '', product_id: 'food-empty'}, {name: ' food-raw ', product_id: 'food-raw'},
        {name: '咖啡豆', product_id: 'food-coffee'}]}),
    // 同一商品在后来的观察里另有写法时，取最先记录的有效名称；后来才出现在 data.goods 的名称仍然优先。
    event('observation', {goods: [{goods_id: 'food-late', name: '店里的叫法'}],
      errand_item_bindings: [{name: '咖啡豆（新包装）', product_id: 'food-coffee'},
        {name: '目录里的叫法', product_id: 'food-late'}]}),
    ...['food-milk', 'food-held', 'food-empty', 'food-raw', 'food-coffee', 'food-late'].map((product_id, index) =>
      errandEvent('errand_registered', errandSnapshot({product_id, errand_id: `action-0007:errand:${index}`}))),
  ]);
  t.after(() => view.close());
  const products = [3, 4, 5, 6, 7, 8].map(sequence => view.summary(sequence).split(' · 商品 ')[1]);
  assert.deepEqual(products, ['纯牛奶', '全麦面包', '名称缺失（food-empty）', '名称缺失（food-raw）', '咖啡豆',
    '店里的叫法']);
  assert.deepEqual(view.errors, ['Missing recorded object name: food-empty', 'Missing recorded object name: food-raw']);
});

test('an errand product without a recorded name is reported as missing and an errand is never named by its identity', t => {
  const name = '<img src=x onerror=alert(1)>';
  const view = viewer([
    event('observation', {goods: [{goods_id: 'food-milk', name: '纯牛奶'}]}),
    errandEvent('errand_registered', errandSnapshot({name: '买早餐', product_id: 'food-unlisted'})),
    errandEvent('errand_completed', errandSnapshot({progress: 2}), {quantity_added: 2}),
    errandEvent('errand_registered', errandSnapshot({name: ' \t ', errand_id: 'action-0009:errand:1'})),
    errandEvent('errand_registered', errandSnapshot({name, errand_id: 'action-0010:errand:0'})),
    // 名称与事务内部身份相同（含两侧空白）不是有效名称：照缺失提示，身份不进时间线；没有名称键的快照同样缺失。
    errandEvent('errand_updated', errandSnapshot({name: 'action-0011:errand:0', errand_id: 'action-0011:errand:0'})),
    errandEvent('errand_cancelled', errandSnapshot({name: ' action-0012:errand:3 ', errand_id: 'action-0012:errand:3'}),
      {reason: '不办了'}),
    errandEvent('errand_registered', {errand_id: 'action-0013:errand:0', product_id: 'food-milk'}),
    errandEvent('errand_registered', errandSnapshot({name: 7, errand_id: 'action-0014:errand:0'})),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(2), '世界事件 · 事务记下 · 「买早餐」 · 商品 名称缺失（food-unlisted）');
  assert.equal(view.summary(3), '世界事件 · 事务办成 · 「买牛奶」 · 商品 纯牛奶');
  assert.equal(view.summary(4), '世界事件 · 事务记下 · 名称缺失（个人事务） · 商品 纯牛奶');
  assert.equal(view.summary(5), `世界事件 · 事务记下 · 「${name}」 · 商品 纯牛奶`);
  assert.equal(view.summary(6), '世界事件 · 事务更新 · 名称缺失（个人事务） · 商品 纯牛奶');
  assert.equal(view.summary(7), '世界事件 · 事务取消 · 名称缺失（个人事务） · 商品 纯牛奶');
  assert.equal(view.summary(8), '世界事件 · 事务记下 · 名称缺失（个人事务） · 商品 纯牛奶');
  assert.equal(view.summary(9), '世界事件 · 事务记下 · 名称缺失（个人事务） · 商品 纯牛奶');
  const row = view.run('tlRow(S.events[4]).innerHTML');
  assert.ok(row.includes('「&lt;img src=x onerror=alert(1)&gt;」') && !row.includes('<img'));
  assert.deepEqual(view.errors.filter(error => error.includes('food-unlisted')),
    ['Missing recorded object name: food-unlisted']);
  assert.deepEqual(view.errors.filter(error => error.includes('errand name')),
    [4, 6, 7, 8, 9].map(sequence => `Missing recorded errand name at event ${sequence}`));
  assert.equal(view.errors.length, 6, view.errors.join('\n'));
  assert.ok(view.errors.every(error => !/[一-鿿]/.test(error) && !ERRAND_IDENTITY.test(error)));
  view.state.events.forEach((entry, index) => {
    assert.doesNotMatch(entry.sum, ERRAND_IDENTITY);
    assert.doesNotMatch(view.run(`tlRow(S.events[${index}]).innerHTML`), ERRAND_IDENTITY);
  });
  // 事务内部身份只用于判定名称是否有效，不留在页面的索引里。
  assert.doesNotMatch(JSON.stringify([...view.state.summaries.values()]), ERRAND_IDENTITY);
});

test('current reminder wakes list delivered errands and preserve plan-only reminders', t => {
  const due = epoch + 60;
  const view = viewer([
    event('observation',{goods:[{goods_id:'food-milk',name:'纯牛奶'}]}),
    errandEvent('errand_reminded',errandSnapshot(),{},{occurred_at:due}),
    event('decision_started',{round_order:0,wake_reasons:['reminder_due']},{occurred_at:due}),
    event('decision_started',{round_order:0,wake_reasons:['reminder_due']},{occurred_at:due+60}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(3),'决策开始 · 第 0 轮 · 唤醒：提醒到点「买牛奶」');
  assert.equal(view.summary(4),'决策开始 · 第 0 轮 · 唤醒：提醒到点');
  assert.deepEqual(view.errors, []);
});

test('midnight plan model requests are named by their recorded purpose', t => {
  const request = {model: 'deepseek-flash', messages: []};
  const view = viewer([
    event('model_request', {phase: 'request', purpose: 'plan_day', attempt: 2, request}),
    event('model_request', {phase: 'request', attempt: 1, request}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '零点计划模型请求 deepseek-flash · 第2次尝试');
  assert.equal(view.summary(2), '模型请求 deepseek-flash · 第1次尝试');
});

test('activities page lists location sessions from the final world state', t => {
  const session = (id, fields) => ({session_id: id, agent_id: 1, status: 'ended', activity_ids: [], ...fields});
  const world = {
    activities: {'act-sleep': {name: '睡觉（1月5日 00:00 起）'}, 'act-walk': {name: '步行前往菜饭骨头汤（1月5日 07:30 起）'},
      'act-podcast': {name: '听播客<节目>（1月5日 07:30 起）'}},
    sessions: {
      'session-1-0002': session('session-1-0002', {kind: 'transit', origin_id: 'home', destination_id: 'poi-1',
        name: '去菜饭骨头汤的路上（1月5日 07:30 起）', started_at: epoch + 5400, ended_at: epoch + 6000,
        activity_ids: ['act-walk', 'act-podcast']}),
      'session-1-0001': session('session-1-0001', {kind: 'stay', location_id: 'home',
        name: '张江公寓（1月5日 00:00 起）', started_at: epoch, ended_at: epoch + 5400, activity_ids: ['act-sleep']}),
      'session-1-0003': session('session-1-0003', {kind: 'stay', location_id: 'poi-1', status: 'active',
        name: '菜饭骨头汤（1月5日 07:40 起）', started_at: epoch + 6000, ended_at: null, activity_ids: ['act-gone']}),
      'session-2-0001': session('session-2-0001', {agent_id: 2, kind: 'stay', location_id: 'home',
        name: '李明的家（1月5日 00:00 起）', started_at: epoch, ended_at: null, status: 'active'}),
    },
  };
  const view = viewer([activity('action_accepted', 'act-sleep', {kind: 'sleep'}),
    activity('action_accepted', 'act-wait-2', {kind: 'interact'}, {agent_id: 2})], world);
  t.after(() => view.close());
  const rows = tableRows(view.activities(), 'session-table');
  assert.deepEqual(rows.map(cells => [cells[2], cells[3], cells[5], cells[6]]), [
    ['张江公寓（1月5日 00:00 起）', '停留', '已结束', '睡觉（1月5日 00:00 起）'],
    ['李明的家（1月5日 00:00 起）', '停留', '进行中', '0 项活动'],
    ['去菜饭骨头汤的路上（1月5日 07:30 起）', '路上', '已结束',
      '步行前往菜饭骨头汤（1月5日 07:30 起）、听播客&lt;节目&gt;（1月5日 07:30 起）'],
    ['菜饭骨头汤（1月5日 07:40 起）', '停留', '进行中', '1 项活动'],
  ]);
  view.get('#agent-filter').value = '2';
  assert.deepEqual(tableRows(view.activities(), 'session-table').map(cells => cells[2]),
    ['李明的家（1月5日 00:00 起）']);
});

test('runs without location sessions render no session table', t => {
  const view = viewer([activity('action_accepted', 'act-sleep', {kind: 'sleep'})]);
  t.after(() => view.close());
  assert.ok(!view.activities().includes('地点会话'));
});

test('unknown app and location names are explicit and logged in English', t => {
  const view = viewer([
    activity('action_accepted','browse',{kind:'interact',data:{action:'interact',app_id:'app-missing'}}),
    activity('action_accepted','travel',{kind:'travel',destination_id:'poi-missing'}),
    activity('action_accepted','sleep',{kind:'sleep'}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()),['名称缺失（app-missing）','→ 名称缺失（poi-missing）','—']);
  assert.ok(view.errors.some(error => error.includes('app-missing')));
  assert.ok(view.errors.some(error => error.includes('poi-missing')));
  assert.ok(view.errors.every(error => !/[一-鿿]/.test(error)));
});

test('empty names cannot erase recorded names and missing names remain explicit', t => {
  const view = viewer([
    event('observation',{apps:[{app_id:'app',name:'美团'}],locations:[{location_id:'home',name:'家'}]}),
    event('observation',{apps:[{app_id:'app',name:''},{app_id:'empty',name:'  '}]}),
    activity('action_accepted','known',{kind:'interact',data:{action:'interact',app_id:'app'}}),
    activity('action_accepted','unknown',{kind:'interact',data:{action:'interact',app_id:'empty'}}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()),['美团','名称缺失（empty）']);
  assert.equal(view.errors.length,1);
});

test('reload clears recorded names and missing-name diagnostics', t => {
  const rows = [event('observation',{apps:[{app_id:'app',name:'美团'}]}),
    activity('action_accepted','browse',{kind:'interact',data:{action:'interact',app_id:'missing'}})];
  const view=viewer(rows);
  t.after(() => view.close());
  view.activities();
  assert.equal(view.errors.length,1);
  view.reload([event('observation',{apps:[{app_id:'missing',name:'支付宝'}]}),rows[1]]);
  assert.deepEqual(targets(view.activities()),['支付宝']);
  assert.equal(view.run('S.nameIdx.targets.has("app")'),false);
  assert.equal(view.run('S.missingNames.size'),0);
});

test('raw app identifiers cannot become user-facing names or titles', t => {
  const view=viewer([
    event('observation',{apps:[{app_id:'app-raw',name:'app-raw'}]}),
    activity('action_accepted','browse',{kind:'interact',data:{action:'interact',app_id:'app-raw',content_id:null}}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()),['名称缺失（app-raw）']);
  assert.equal(view.errors.length,1);
});

test('names are escaped in activities timeline and economy without changing stored events', t => {
  const name = '<img src=x onerror=alert(1)>&"\'';
  const escaped = '&lt;img src=x onerror=alert(1)&gt;&amp;&quot;&#39;';
  const view = viewer([
    event('observation', {goods: [{goods_id: 'goods-html', name}],
      locations: [{location_id: 'poi-html', name}]}),
    activity('action_accepted', 'unsafe-title', {kind: 'interact', content_title: name,
      data: {action: 'interact', app_id: 'app-html', content_id:'content-html'}}),
    activity('action_accepted', 'unsafe-location', {kind: 'travel', destination_id: 'poi-html'}),
    event('action_result', {status: 'completed', data: {
      orders: [{goods_id: 'goods-html', total_price_fen: 100, payment_app_id: null}]}}),
    event('environment_event',{kind:'purchase_payment'},{resource_changes:{'account:agent:1':-100,'account:external:scenario':100}}),
  ],{agents:{1:{name}}});
  t.after(() => view.close());
  const timeline = view.run('tlRow(S.events[1]).innerHTML');
  for (const markup of [view.activities(), timeline, view.economy()]) {
    assert.ok(markup.includes(escaped));
    assert.ok(!markup.includes('<img'));
  }
  assert.equal(JSON.parse(view.run('fetchBody(2)')).data.activity.content_title, name);
  assert.ok(view.summary(2).includes(name));
});

test('body curves read current observation values rather than obsolete body fields', t => {
  const view = viewer([
    event('observation', {balance_fen: 1000, body: {values: {
      energy_kcal: 1356.25, sleep_deficit_hours: 1.875, relaxation_need: 0.3, social_need: 0.4,
    }}, active_activities: [{kind: 'interact'}, {kind: 'interact'}]}),
    event('observation', {balance_fen: 900, body: {values: {
      energy_kcal: 1230, sleep_deficit_hours: 2.5, relaxation_need: 0.5, social_need: 0.6,
    }}}, {occurred_at: epoch + 60}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(JSON.parse(JSON.stringify(view.state.obs.map(({en, sl, et, so}) => [en, sl, et, so]))), [
    [1356.25, 1.875, 0.3, 0.4], [1230, 2.5, 0.5, 0.6],
  ]);
  assert.ok(view.body().includes('睡眠缺口 sleep_deficit_hours'));
  assert.ok(view.body().includes('放松需要 relaxation_need'));
  assert.ok(view.body().includes('社交需要 social_need'));
  assert.ok(view.summary(1).includes('1356kcal'));
  assert.ok(view.summary(1).includes('进行中：互动、互动'));
});

test('memory page shows stored episode text without requiring a directory upload', t => {
  const view = viewer([
    event('memory_updated', {operation: 'append_episodes',
      created_episode_ids: ['m-1', 'm-2'], merged_episode_ids: [], episodes: [
      {id: 'm-1', type: 'routine', text: '今天先完成工作。'},
      {id: 'm-2', type: 'preference', text: '<img src=x onerror=alert(1)>'},
    ]}),
  ]);
  t.after(() => view.close());
  const markup = view.memory();
  assert.ok(markup.includes('今天先完成工作。'));
  assert.ok(markup.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(!markup.includes('<img'));
  assert.ok(markup.includes('人物记忆正文（2 条）'));
  assert.ok(!markup.includes('展开 episode ID'));
  assert.ok(view.summary(1).includes('2 段记忆'));
});

test('activity lanes preserve simultaneous actions and pauses using settled intervals', t => {
  const first = 'action-a', second = 'wait-b';
  const action = {action: 'interact', app_id: null, content_id: null};
  const view = viewer([
    activity('action_accepted', first, {kind: 'interact', data: action, started_at: epoch + 60},
      {occurred_at: epoch + 60}),
    activity('action_accepted', second, {kind: 'interact', started_at: epoch + 90},
      {occurred_at: epoch + 90}),
    activity('activity_progress', first, {kind: 'interact', data: action, started_at: epoch + 60},
      {occurred_at: epoch + 180, data: {activity: {
        activity_id: first, kind: 'interact', data: action, started_at: epoch + 60,
      }, interval_start: epoch + 60, interval_end: epoch + 180}}),
    activity('activity_progress', second, {kind: 'interact', started_at: epoch + 90},
      {occurred_at: epoch + 150, data: {activity: {
        activity_id: second, kind: 'interact', started_at: epoch + 90,
      }, interval_start: epoch + 90, interval_end: epoch + 150}}),
    activity('activity_progress', first, {kind: 'interact', data: action, started_at: epoch + 60},
      {occurred_at: epoch + 240, data: {activity: {
        activity_id: first, kind: 'interact', data: action, started_at: epoch + 60,
      }, interval_start: epoch + 210, interval_end: epoch + 240}}),
  ]);
  t.after(() => view.close());
  const actionRow = view.state.acts.find(row => row.aid === first);
  const wait = view.state.acts.find(row => row.aid === second);
  assert.equal(actionRow.actualSeconds, 150);
  assert.equal(wait.actualSeconds, 60);
  assert.deepEqual(JSON.parse(JSON.stringify(actionRow.intervals)),
    [[epoch + 60, epoch + 180], [epoch + 210, epoch + 240]]);
  assert.deepEqual(JSON.parse(JSON.stringify(wait.intervals)), [[epoch + 90, epoch + 150]]);
  const markup = view.activities();
  assert.equal((markup.match(/class="act-lane-bar"/g) || []).length, 3);
  assert.ok(markup.includes('同一时间轴上的条块表示实际执行区间'));
});

/** @brief 拖入或选择的顶层文件：只提供页面读取的名称与文本；content 不是字符串时按 JSON 写出。 */
function droppedFile(name, content) {
  return {name, text: async () => typeof content === 'string' ? content : JSON.stringify(content)};
}

/** @brief 拖入目录中的文件条目（FileSystemFileEntry 的最小形状）。 */
function fileEntry(name, content) {
  return {isFile: true, isDirectory: false, name, file(callback) { callback(droppedFile(name, content)); }};
}

/** @brief 拖入的目录条目：readEntries 先交出全部子项，再交出空批次表示读完。 */
function directoryEntry(name, children) {
  return {isFile: false, isDirectory: true, name, createReader() {
    let listed = false;
    return {readEntries(callback) { callback(listed ? [] : children); listed = true; }};
  }};
}

/** @brief 运行清单：与测试世界状态同一 run_id、带 agent_ids；fields 覆盖 input_warnings 等键（缺省不带该键）。 */
function runManifest(fields = {}, runId = 'viewer-test-run') {
  return {schema_version: 3, run_id: runId, source_group_id: runId, agent_ids: [1, 2], ...fields};
}

/** @brief 一条运行输入警告，按清单条目形状 {code, agent_id, message, details}。 */
function inputWarning(code, agent_id, message) {
  return {code, agent_id, message, details: {}};
}

/** @brief 总览中「运行输入警告」卡片的标记（到下一张卡片「事件类型分布」为止）。 */
function warningCard(markup) {
  return markup.slice(markup.indexOf('运行输入警告'), markup.indexOf('事件类型分布'));
}

/** @brief 警告表逐行的归属、警告码与说明（去掉标签后的文字，实体保持转义）。 */
function warningRows(markup) {
  return tableRows(markup, 'warning-table').map(cells => cells.map(cell => cell.replace(/<[^>]*>/g, '').trim()));
}

/** @brief 世界状态带 run_id 的查看器，读入随库提供的顶层文件与目录（与拖入时同一入口）。 */
async function loadedViewer(t, files, directories = [], events = [event('observation', {})]) {
  const view = viewer(events, {run_id: 'viewer-test-run'});
  t.after(() => view.close());
  await view.run('loadRunFiles')(files, directories);
  return view;
}

test('overview lists every frozen input warning under its person or at scenario level with code and message', async t => {
  const source = readFileSync(path.join(__dirname, '../../../src/worldmock/input_warnings.py'), 'utf8')
    .replace(/\r\n/g, '\n');
  const codes = [.../InputWarningCode = Literal\[\n([\s\S]*?)\n\]/.exec(source)[1].matchAll(/"([A-Z_]+)"/g)]
    .map(match => match[1]);
  assert.deepEqual(codes, ['CONTENT_EMPTY_WITH_MEDIA_TASTES', 'LOCATION_NAME_SUSPICIOUS']);
  const [personal, scenario] = codes;
  // 清单顺序故意打乱：人物 2 的警告在前，一条场景级夹在两人之间；页面按人物编号、再场景级排列。
  const warnings = [
    inputWarning(personal, 2, `李明：${personal}`),
    inputWarning(scenario, null, '地点 poi-1 的显示名「肯德基<东经121.50°>」带坐标。'),
    inputWarning(personal, 1, `陈曦：${personal}`),
  ];
  const view = await loadedViewer(t, [droppedFile('manifest.json', runManifest({input_warnings: warnings}))]);
  const card = warningCard(view.overview());
  assert.deepEqual(warningRows(card), [
    ['陈曦', personal, `陈曦：${personal}`], ['李明', personal, `李明：${personal}`],
    ['场景级', scenario, '地点 poi-1 的显示名「肯德基&lt;东经121.50°&gt;」带坐标。'],
  ]);
  assert.deepEqual(new Set(warningRows(card).map(row => row[1])), new Set(codes));
  assert.ok(card.includes('共 3 条：人物 2 条、场景级 1 条') && card.includes('manifest.json'));
  assert.ok(!card.includes('<东经'));
  view.get('#agent-filter').value = '2';
  const filtered = warningCard(view.overview());
  assert.deepEqual(warningRows(filtered).map(row => row[0]), ['李明', '场景级']);
  assert.ok(filtered.includes('当前筛选 2 条'));
  assert.deepEqual(view.errors, []);
  const firstOnly = await loadedViewer(t, [droppedFile('manifest.json',
    runManifest({input_warnings: warnings.filter(item => item.agent_id === 1)}))]);
  firstOnly.get('#agent-filter').value = '2';
  const none = warningCard(firstOnly.overview());
  assert.ok(none.includes('所选人物没有输入警告') && none.includes('当前筛选 0 条') && !none.includes('warning-table'));
});

test('warnings that were not computed or are empty are stated and a missing manifest says how to supply it', async t => {
  const states = [
    [runManifest({input_warnings: null}), '未计算：清单的 input_warnings 为 null（引擎直驱建立的运行）'],
    [runManifest(), '未计算：运行清单没有 input_warnings 键（清单早于运行输入警告）'],
    [runManifest({input_warnings: []}), '无输入警告'],
  ];
  for (const [manifest, statement] of states) {
    const card = warningCard((await loadedViewer(t, [droppedFile('manifest.json', manifest)])).overview());
    assert.ok(card.includes(statement), statement);
    assert.ok(card.includes('来源') && card.includes('manifest.json'), statement);
    assert.ok(!card.includes('warning-table') && !card.includes('未读取'), statement);
  }
  const bare = warningCard((await loadedViewer(t, [])).overview());
  assert.ok(bare.includes('未读取本库所属运行的运行清单') && bare.includes('manifest.json'));
  assert.ok(!bare.includes('都不是本库所属运行') && !bare.includes('无输入警告'));
  const malformed = await loadedViewer(t, [droppedFile('manifest.json', runManifest({input_warnings: {}}))]);
  assert.ok(warningCard(malformed.overview()).includes('不是列表'));
  assert.ok(malformed.errors.some(error => error.includes('neither a list nor null')));
});

test('the manifest is bound to the world state run, preferred over the export copy and never another run', async t => {
  const child = runManifest({branch_kind: 'resume', parent_run_id: 'parent-run',
    input_warnings: [inputWarning('CONTENT_EMPTY_WITH_MEDIA_TASTES', 1, '子运行清单冻结的警告')]});
  const exported = {run_id: 'viewer-test-run', frozen_run_manifest: {...child,
    input_warnings: [inputWarning('CONTENT_EMPTY_WITH_MEDIA_TASTES', 1, '导出清单中的副本')]}};
  // 恢复分支的库以父运行的事件开头，世界状态与清单才是本运行；同目录的 LabelKit 输入清单也写着本运行的 run_id。
  const run = directoryEntry('resumed', [
    directoryEntry('labelkit-input', [fileEntry('manifest.json',
      {schema_version: 1, run_id: 'viewer-test-run', agent_id: 1})]),
    directoryEntry('worldmock-export', [fileEntry('manifest.json', exported)]),
    fileEntry('manifest.json', child),
    directoryEntry('as2', [directoryEntry('agents', [directoryEntry('agent_0001', [
      fileEntry('MEMORY.md', '今天把纪要交了。')])])]),
  ]);
  const parent = droppedFile('manifest.json', runManifest({input_warnings: []}, 'parent-run'));
  const view = await loadedViewer(t, [parent], [run],
    [event('observation', {}, {run_id: 'parent-run'}), event('observation', {})]);
  assert.equal(view.state.runId, 'parent-run', 'the first event belongs to the parent run');
  const card = warningCard(view.overview());
  assert.deepEqual(warningRows(card), [['陈曦', 'CONTENT_EMPTY_WITH_MEDIA_TASTES', '子运行清单冻结的警告']]);
  assert.ok(card.includes('resumed/manifest.json') && !card.includes('frozen_run_manifest'));
  assert.equal(view.state.ignoredManifests, 2);
  assert.equal(view.state.memFiles['1'].memoryMd, '今天把纪要交了。');
  const exportDirectory = () => directoryEntry('worldmock-export', [fileEntry('manifest.json', exported)]);
  // 运行清单在前、导出副本在后时同样取运行清单本身。
  const directFirst = await loadedViewer(t, [droppedFile('manifest.json', child)], [exportDirectory()]);
  assert.deepEqual(warningRows(warningCard(directFirst.overview())),
    [['陈曦', 'CONTENT_EMPTY_WITH_MEDIA_TASTES', '子运行清单冻结的警告']]);

  const exportOnly = await loadedViewer(t, [], [exportDirectory()]);
  const copy = warningCard(exportOnly.overview());
  assert.deepEqual(warningRows(copy), [['陈曦', 'CONTENT_EMPTY_WITH_MEDIA_TASTES', '导出清单中的副本']]);
  assert.ok(copy.includes('worldmock-export/manifest.json · frozen_run_manifest'));

  const strangers = await loadedViewer(t, [parent, droppedFile('manifest.json', '{"run_id": "viewer-test-run"')]);
  assert.ok(warningCard(strangers.overview()).includes('随库提供的 2 个 manifest.json 都不是本库所属运行的运行清单'));
  assert.ok(strangers.errors.some(error => error.startsWith('Unreadable manifest file')));
  assert.ok(strangers.errors.every(error => !/[\u4e00-\u9fff]/.test(error)));
});

test('reloading another database drops the previous run manifest and its warnings', async t => {
  const view = await loadedViewer(t, [droppedFile('manifest.json',
    runManifest({input_warnings: [inputWarning('CONTENT_EMPTY_WITH_MEDIA_TASTES', 1, '上一份库的警告')]}))]);
  assert.equal(warningRows(warningCard(view.overview())).length, 1);
  view.reload([event('observation', {})]);
  const card = warningCard(view.overview());
  assert.ok(card.includes('未读取本库所属运行的运行清单') && !card.includes('上一份库的警告'));
  assert.equal(view.state.runManifest, null);
});


test('money uses authoritative envelope changes once and allows signed external balances', t => {
  const view=viewer([
    event('action_result',{status:'in_progress',data:{orders:[{goods_id:'milk',total_price_fen:100}]}}),
    event('environment_event',{kind:'purchase_payment'},{resource_changes:{'account:agent:1':-100,'account:external:scenario':100}}),
    event('environment_event',{kind:'payment_notification'}),
    event('observation',{balance_fen:900}),
  ],{accounts:{'agent:1':900,'external:scenario':-900}});
  t.after(()=>view.close());
  const markup=view.economy();
  assert.ok(markup.includes('共 2 条'));
  assert.equal((markup.match(/购买付款/g)||[]).length,2);
  assert.ok(markup.includes('外部账户'));
  assert.ok(markup.includes('-9.00'));
});

test('an aggregate acceptance keeps both actual travel and interaction activities', t => {
  const travel={activity_id:'travel',kind:'travel',started_at:epoch,status:'active',elapsed_seconds:0,
    destination_id:'store',travel_method_id:'walking',data:{action:'travel'}};
  const interaction={activity_id:'browse',kind:'interact',started_at:epoch,status:'active',elapsed_seconds:0,
    data:{action:'interact',app_id:'meituan',content_id:null}};
  const view=viewer([
    event('observation',{locations:[{location_id:'store',name:'四季便利'}],apps:[{app_id:'meituan',name:'美团'}]}),
    event('action_accepted',{data:{activities:[travel,interaction]}}),
  ]);
  t.after(()=>view.close());
  // 聚合受理里的出行活动同样带出行方式身份，活动行在目的地前写出行方式名称
  assert.deepEqual(targets(view.activities()),['步行 · → 四季便利','美团']);
  assert.deepEqual(view.errors,[]);
});

test('SQLite content snapshots without retired title display actual content and pure browse app', t => {
  const place={kind:'interact',content_title:'晚间阅读',
    data:{action:'interact',app_id:null,content_id:'evening-reading'}};
  const app={kind:'interact',content_title:'数据分析课程',
    data:{action:'interact',app_id:'bilibili',content_id:'analysis-course'}};
  const browse={kind:'interact',data:{action:'interact',app_id:'meituan',content_id:null}};
  const view=viewer([
    event('observation',{apps:[{app_id:'bilibili',name:'哔哩哔哩'},{app_id:'meituan',name:'美团'}]}),
    activity('action_accepted','place-content',place),
    activity('activity_progress','place-content',{...place,status:'completed',ended_at:epoch+120}),
    activity('action_accepted','app-content',app),
    activity('action_accepted','platform-browse',browse),
  ]);
  t.after(()=>view.close());
  assert.equal(view.run("q(\"SELECT json_extract(body,'$.data.activity.content_title') AS content FROM events WHERE event_seq=2\")[0].content"),'晚间阅读');
  assert.equal(view.run("q(\"SELECT COUNT(*) AS present FROM events WHERE json_type(body,'$.data.activity.title') IS NOT NULL\")[0].present"),0);
  assert.deepEqual(targets(view.activities()),['晚间阅读','数据分析课程','美团']);
  assert.ok(view.summary(2).includes('晚间阅读'));
  assert.ok(view.summary(3).includes('晚间阅读'));
  assert.ok(view.summary(4).includes('数据分析课程'));
  assert.ok(view.summary(5).includes('美团'));
  for(const sequence of [2,3,4,5]){
    assert.ok(!Object.hasOwn(JSON.parse(view.run(`fetchBody(${sequence})`)).data.activity,'title'));
  }
  assert.deepEqual(view.errors,[]);
});
