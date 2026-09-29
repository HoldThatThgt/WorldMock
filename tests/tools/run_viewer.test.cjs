'use strict';

const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {DatabaseSync} = require('node:sqlite');
const test = require('node:test');
const vm = require('node:vm');

const html = readFileSync(path.join(__dirname, '../../tools/run-viewer.html'), 'utf8');
const scripts = [...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)].map(match => {
  const source = /src="([^"]+)"/.exec(match[1]);
  return source ? readFileSync(path.join(__dirname, '../../tools', source[1]), 'utf8') : match[2];
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

/** @brief 执行整段页面脚本并暴露真实索引和渲染结果。 */
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
  run('buildIndex()');
  return {
    state, errors, run, get,
    activities() { run('renderActivities()'); return get('#act-body').innerHTML; },
    body() { run('renderBodyCurves()'); return get('#bd-body').innerHTML; },
    economy() { run('renderEconomy()'); return get('#eco-body').innerHTML; },
    memory() { run('renderMemory()'); return get('#mem-body').innerHTML; },
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

/** @brief 使用七层行动、续接形状与旧运行两阶段请求的测试输入，不重写提示结构。 */
function contextRequest(kind = 'action') {
  const fixture = JSON.parse(readFileSync(path.join(__dirname, '../fixtures/viewer-context.json'), 'utf8'));
  return fixture[kind];
}

/** @brief 构造实际事件形状，并故意复用调用标记以验证因果关联。 */
function contextEvent(id, request, agent = 1) {
  return event('model_request', {request, call_id:'reused-call', attempt:1},
    {event_id:id, agent_id:agent, decision_id:'decision-' + id});
}

test('context reads seven-layer action stages, sliced layers and recorded tools while legacy calls stay other', () => {
  const app = viewer([
    contextEvent('legacy', contextRequest('legacy_prelude')),
    contextEvent('action', contextRequest()),
    contextEvent('continuation', contextRequest('continuation')),
    contextEvent('legacy-action', contextRequest('legacy_action')),
  ]);
  try {
    const items = app.run('ContextView.index()');
    assert.deepEqual(Array.from(items, item => item.stage), ['other', 'action', 'action', 'legacy']);
    assert.equal(items[0].label, '其他调用·未解析');
    assert.equal(items[3].label, '选择动作·旧模板');
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
    const legacy = app.run('ContextView.read(4)');
    assert.equal(legacy.current.blocks.filter(block => block.role === 'user').map(block => block.text).join(''),
      contextRequest('legacy_action').messages[1].content);
    app.run('ContextView.select(4)');
    const legacyHtml = app.get('#ctx-body').innerHTML;
    assert.ok(legacyHtml.includes('模板拆分') && legacyHtml.includes('来源未核验'));
    assert.ok(legacyHtml.includes('人物卡片 · 来源未核验'));
    assert.ok(legacyHtml.includes('历史运行的旧模板行动请求'));
    assert.equal(app.errors.length, 1, app.errors.join('\n'));
  } finally { app.close(); }
});

test('a decision first request with extra messages is a continuation matched to its source decision', () => {
  const continued = contextRequest('continuation');
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
  changed.messages[1].content = changed.messages[1].content.replace('牛肉', '羊肉');
  const app = viewer([
    contextEvent('first', original), contextEvent('other-agent', changed, 2),
    contextEvent('legacy', contextRequest('legacy_prelude')), contextEvent('later', changed),
  ]);
  try {
    const model = app.run('ContextView.read(4)');
    assert.equal(model.previous.seq, 1);
    assert.ok(model.changes.some(row => row.key === 'progress' && row.status === 'changed' && row.delta === 0));
    assert.ok(model.changes.some(row => row.key === 'tool' && row.status === 'unchanged'));
    app.run('ContextView.select(4)');
    app.get('#agent-filter').value = '2';
    const filtered = app.context();
    assert.ok(filtered.includes('事件 2'));
    assert.ok(!filtered.includes('事件 4'));
    app.reload([contextEvent('new-run', contextRequest('legacy_prelude'), 2)]);
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
    assert.equal(app.get('#ctx-prev').disabled, true);
    assert.equal(app.get('#ctx-next').disabled, true);
    app.get('#ctx-stage').onchange({target:{value:'other'}});
    assert.ok(app.get('#ctx-body').innerHTML.includes('没有匹配的模型请求'));
    assert.ok(!app.get('#ctx-body').innerHTML.includes('形成自述'));
    assert.equal(app.errors.length, 0, app.errors.join('\n'));
  } finally { app.close(); }
});

test('context drills into tool layer targets and progress records and marks self_note as unverified', () => {
  const app = viewer([contextEvent('action', contextRequest())]);
  try {
    app.run('ContextView.select(1)');
    app.get('button:message:1:tool').onclick();
    const toolOptions = app.get('#ctx-item').innerHTML;
    assert.ok(toolOptions.includes('整层原文'));
    assert.ok(toolOptions.includes('工作：季度报告 · submit_work · 不可用：先结束正在做的工作'));
    assert.ok(toolOptions.includes('星巴克出售的拿铁 · obtain：远程下单 · 可用'));
    const layers = JSON.parse(contextRequest().messages[1].content);
    app.get('#ctx-item').value = '0';
    app.get('#ctx-item').onchange();
    assert.deepEqual(JSON.parse(app.get('#ctx-raw').textContent), layers[0].targets[0]);
    app.get('#ctx-item').value = '';
    app.get('#ctx-item').onchange();
    assert.ok(app.get('#ctx-raw').textContent.startsWith('[{"layer":"工具层"'));

    app.get('button:message:1:progress').onclick();
    const progressOptions = app.get('#ctx-item').innerHTML;
    assert.ok(progressOptions.includes('tool_call · start_activity · self_note（未经核验）'));
    assert.ok(progressOptions.includes('message_arrival · 王磊'));
    app.get('#ctx-item').value = '2';
    app.get('#ctx-item').onchange();
    assert.ok(app.get('#ctx-raw').textContent.startsWith('self_note（未经核验）：先给王磊点上'));

    app.get('button:message:1:persona').onclick();
    assert.ok(!app.get('#ctx-body').innerHTML.includes('ctx-item'));
    assert.equal(app.errors.length, 0, app.errors.join('\n'));
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
    event('action_requested', {tool_name: 'start_activity', request: {targets: ['自己'], actions: ['interact'],
      ways: ['等待']}, request_sha256: 'f'.repeat(64), reused: false, self_note: '先歇一会儿，再去买菜。'}),
    event('action_requested', {tool_name: 'start_activity', request: {targets: ['自己'], actions: ['sleep'],
      ways: ['睡觉']}, request_sha256: 'e'.repeat(64), reused: false, self_note: null}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '请求 开始活动 · 自己 · 交互：等待 · self_note（未经核验）：先歇一会儿，再去买菜。');
  assert.equal(view.summary(2), '请求 开始活动 · 自己 · 睡眠：睡觉');
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
function browse(items) {
  return event('action_accepted', {status: 'completed', data: {tool: 'browse_activities', items}});
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

/** @brief 构造 start_activity 请求：对象、动词与方式三个等长数组（第五章「请求形状与菜单」）。 */
function startRequest(...items) {
  return event('action_requested', {tool_name: 'start_activity', request: {
    targets: items.map(([target]) => target), actions: items.map(([, verb]) => verb),
    ways: items.map(([, , way]) => way),
  }});
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
    events.push(startRequest([place.name, 'travel', '步行']));
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
    assert.ok(view.summary(index * 3 + 3).endsWith(`${place.name} · 出行：步行`));
  });
  assert.deepEqual(view.errors, []);
});

test('products mail and tasks remain named after later sparse observations', t => {
  const view = viewer([
    event('observation', {
      products: [{product_id: 'breakfast-custom', name: '红烧牛肉面'}],
      messages: [{message_id: 'mail-team', subject: '项目周会通知'}],
      tasks: [{task_id: 'hourly-2026-01-05', title: '梳理客户访谈记录',
        remaining_contract_seconds: 3600}],
    }),
    event('observation', {products: [], messages: [], tasks: []}, {agent_id: 2}),
    ...[
      ['consume', 'breakfast-custom'], ['read_mail', 'mail-team'], ['work', 'hourly-2026-01-05'],
    ].map(([kind, target_id]) => activity('action_accepted', kind, {kind, target_id})),
    event('action_result', {status: 'completed', order: {
      product_id: 'breakfast-custom', total_price_fen: 1800,
    }}),
  ]);
  t.after(() => view.close());
  const rendered = targets(view.activities());
  for (const [index, name] of ['红烧牛肉面', '项目周会通知', '梳理客户访谈记录'].entries()) {
    assert.ok(rendered[index].includes(name));
    assert.ok(view.summary(index + 3).includes(name));
  }
  assert.ok(view.economy().includes('购买 红烧牛肉面'));
  assert.ok(view.summary(6).includes('红烧牛肉面'));
  assert.deepEqual(view.errors, []);
});

test('browse results name movies dramas novels games and lifestyle activities without external catalogs', t => {
  const rows = [
    {item_id: 'movie-0001', title: '海边的清晨', kind: 'feature_film', way: '观看'},
    {item_id: 'drama-0007', title: '华语短剧台词选段（源编号 16013）', kind: 'short_drama', way: '接着看'},
    {item_id: 'novel-0003', title: '老城故事', kind: 'online_novel', way: '阅读'},
    {item_id: 'textworld-0004', title: '森林迷宫', kind: 'text_game', way: '游玩'},
    {activity_id: 'stretch-evening', name: '晚间舒展', kind: 'exercise', way: '进行'},
  ];
  const events = [browse(rows)];
  for (const row of rows) {
    const target_id = row.item_id || row.activity_id;
    events.push(startRequest([row.title || row.name, 'interact', row.way]));
    events.push(activity('action_accepted', `do-${target_id}`, {kind: row.kind, target_id}));
  }
  const view = viewer(events);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), rows.map(row => row.title || row.name));
  rows.forEach((row, index) => assert.ok(view.summary(index * 2 + 2).endsWith(
    `${row.title || row.name} · 交互：${row.way}`)));
  assert.doesNotMatch(view.activities(), /剧集 #|电影 #|小说 #|游戏 #|第\d+段/);
  assert.deepEqual(view.errors, []);
});

test('each activity keeps its last nonempty title instead of another activity or catalog title', t => {
  const view = viewer([
    browse([{item_id: 'drama-0007', title: '目录描述'}]),
    activity('action_accepted', 'watch-first', {kind: 'watch', target_id: 'drama-0007', title: '初始描述'}),
    activity('activity_progress', 'watch-first', {kind: 'watch', target_id: 'drama-0007', title: '本次选段描述'}),
    activity('activity_progress', 'watch-first', {kind: 'watch', target_id: 'drama-0007', title: ''}),
    activity('action_accepted', 'watch-second', {
      kind: 'watch', target_id: 'drama-0007', title: '另一次选段描述',
    }, {agent_id: 2}),
    activity('activity_progress', 'watch-first', {
      kind: 'watch', target_id: 'drama-0007', title: ' drama-0007 ',
    }),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), ['本次选段描述', '另一次选段描述']);
  assert.ok(view.summary(2).includes('初始描述'));
  assert.ok(view.summary(3).includes('本次选段描述'));
  assert.ok(view.summary(5).includes('另一次选段描述'));
});

test('progress-only activity titles name both activity rows and request summaries', t => {
  const title = '华语短剧台词选段（源编号 16013）';
  const view = viewer([
    startRequest([title, 'interact', '观看']),
    activity('activity_progress', 'only-progress', {kind: 'watch', target_id: 'drama-0007', title}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), [title]);
  assert.ok(view.summary(1).includes(title));
  assert.ok(view.summary(2).includes(title));
  assert.deepEqual(view.errors, []);
});

test('start activity request displays each object with its verb and way', t => {
  const view = viewer([
    event('observation', {locations: [{location_id: 'home', name: '张江公寓'}],
      products: [{product_id: 'food-rice', name: '菜饭'}]}),
    startRequest(['菜饭', 'obtain', '远程下单'], ['自己', 'interact', '等待']),
    startRequest(['张江公寓', 'travel', '驾车']),
    startRequest(['家里的菜饭', 'consume', '食用']),
    startRequest(['自己', 'sleep', '睡觉']),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(2), '请求 开始活动 · 菜饭 · 取得：远程下单；自己 · 交互：等待');
  assert.equal(view.summary(3), '请求 开始活动 · 张江公寓 · 出行：驾车');
  assert.equal(view.summary(4), '请求 开始活动 · 家里的菜饭 · 使用：食用');
  assert.equal(view.summary(5), '请求 开始活动 · 自己 · 睡眠：睡觉');
  for (const sequence of [2, 3, 4, 5]) {
    assert.doesNotMatch(view.summary(sequence), /food-rice|obtain|interact|travel|consume|sleep/);
  }
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

test('interaction bindings name catalog activity without overriding a recorded title', t => {
  const view = viewer([
    event('observation', {interaction_bindings: [
      {object_id: 'product_catalog', object: '商品目录'},
      {object_id: 'drama-0001', object: '内容：目录说明'},
    ]}),
    activity('action_accepted', 'browse', {kind: 'browse', target_id: 'product_catalog'}),
    activity('action_accepted', 'watch', {kind: 'watch', target_id: 'drama-0001', title: '本次选段'}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), ['商品目录', '本次选段']);
  assert.deepEqual(view.errors, []);
});

test('session requests display each task with its own progress sentence', t => {
  const memory = text => ({type: 'routine', text});
  const opinion = (about, text) => ({type: 'relationship', about, text});
  const item = (activity_name, summary, extra = {}) => ({activity_name, summary, ...extra});
  const view = viewer([
    event('action_requested', {tool_name: 'report_status', request: {
      task: ['整理访谈', '准备周会'], summary: ['完成提纲', '列出议题'],
    }}),
    event('action_requested', {tool_name: 'conclude_activity', request: {
      task: ['整理访谈'], summary: ['已经提交纪要'], memories: [[memory('纪要要当天交'), memory('先列提纲')]],
    }}),
    event('action_requested', {tool_name: 'conclude_activity', request: {
      task: ['准备周会', '午饭'], summary: ['议题定了', '吃完了'], memories: [[], [memory('菜饭偏咸')]],
    }}),
    event('action_requested', {tool_name: 'pause_activity', request: {
      task: ['准备周会'], summary: ['先放一放'],
    }}),
    event('action_requested', {tool_name: 'conclude_activity', request: {
      task: ['午饭'], summary: ['吃完了'],
    }}),
    event('action_requested', {tool_name: 'report_status', request: {items: [
      item('整理访谈', '完成提纲'), item('准备周会', '列出议题'),
    ]}}),
    event('action_requested', {tool_name: 'conclude_activity', request: {items: [
      item('整理访谈', '已经提交纪要', {memories: [memory('纪要要当天交')], views: [opinion('王磊', '王磊很靠谱')]}),
      item('午饭', '吃完了', {memories: [], views: []}),
    ]}}),
    event('action_requested', {tool_name: 'pause_activity', request: {items: [item('准备周会', '先放一放')]}}),
    event('action_requested', {tool_name: 'conclude_activity', request: {items: [
      item('准备周会', '议题定了', {memories: [], views: []}),
    ]}}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '请求 播报进展 · 整理访谈：完成提纲；准备周会：列出议题');
  assert.equal(view.summary(2), '请求 结束活动 · 整理访谈：已经提交纪要 · 记忆 2 条');
  assert.equal(view.summary(3), '请求 结束活动 · 准备周会：议题定了；午饭：吃完了 · 记忆 1 条');
  assert.equal(view.summary(4), '请求 暂停活动 · 准备周会：先放一放');
  assert.equal(view.summary(5), '请求 结束活动 · 午饭：吃完了');
  assert.ok(!view.summary(1).includes('task='));
  // 现行一项一个对象的请求经 controlItems 读出同样的逐项配对，看法与普通记忆合计计数（第六章「一项一个对象」）。
  assert.equal(view.summary(6), view.summary(1));
  assert.equal(view.summary(7), '请求 结束活动 · 整理访谈：已经提交纪要；午饭：吃完了 · 记忆 2 条');
  assert.equal(view.summary(8), view.summary(4));
  assert.equal(view.summary(9), '请求 结束活动 · 准备周会：议题定了 · 记忆 0 条');
  const read = request => JSON.parse(JSON.stringify(view.run(`controlItems('conclude_activity', ${
    JSON.stringify(request)}).map(entry => [entry.name, entry.summary, entry.memories.map(one => one.text)])`)));
  assert.deepEqual(read({task: ['整理访谈'], summary: ['已经提交纪要'], memories: [[memory('纪要要当天交'),
    {type: 'relationship', text: '王磊很靠谱', subject: '王磊'}]]}), read({items: [
    item('整理访谈', '已经提交纪要', {memories: [memory('纪要要当天交')], views: [opinion('王磊', '王磊很靠谱')]}),
  ]}));
  assert.deepEqual(view.errors, []);
  for (const entry of view.state.events) assert.doesNotMatch(entry.sum, /undefined/);
});

test('activity end reasons name reminders route and partner endings pauses and cancellations', t => {
  const reasons = [
    ['plan_reminder', '计划提醒结束'], ['route_ended', '随出行结束'], ['partner_ended', '对方结束交谈'],
    ['paused', '已暂停'], ['cancelled', '主动结束'], ['planned_end', '按计划结束'],
  ];
  const view = viewer([
    ...reasons.map(([boundary_type], index) => activity('activity_progress', `ended-${index}`, {
      kind: 'wait', status: 'completed', boundary_type,
    })),
    activity('activity_progress', 'truncated', {kind: 'wait', status: 'truncated', boundary_type: 'experiment_end'}),
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
      activity_id: 'act-1', kind: 'wait', name: '等待（1月5日 06:00 起）',
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
    event('action_rejected', {error_code: 'WORK_REQUIRES_REWORK', consecutive_rejections: 4,
      maximum_rejections: 10}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '被拒绝 · ACTION_REQUEST_INVALID · 同刻第 2 轮（上限 3）');
  assert.equal(view.summary(2), '被拒绝 · 工作需返工 · 同刻第 ? 轮');
  for (const sequence of [1, 2]) assert.doesNotMatch(view.summary(sequence), /连续拒绝/);
});

test('decision starts show their wake reasons from the frozen vocabulary', t => {
  const models = readFileSync(path.join(__dirname, '../../src/worldmock/footprints/models.py'), 'utf8');
  const vocabulary = models.match(/WAKE_REASON_NAMES = frozenset\(\{([\s\S]*?)\}\)/)[1];
  const names = [...vocabulary.matchAll(/"([a-z_]+)"/g)].map(match => match[1]);
  assert.equal(names.length, 20);
  const view = viewer([
    event('decision_started', {round_order: 0, wake_reasons: ['run_start']}),
    event('decision_started', {round_order: 0, wake_reasons: [
      'no_active_activity', 'plan_reminder_due', 'progress_report_due']}),
    event('decision_started', {round_order: 1, wake_reasons: []}),
    event('decision_started', {round_order: 0}),
    event('decision_started', {round_order: 0, wake_reasons: names}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '决策开始 · 第 0 轮 · 唤醒：运行开始');
  assert.equal(view.summary(2), '决策开始 · 第 0 轮 · 唤醒：没有进行中的活动、计划提醒到点、该播报进展');
  assert.equal(view.summary(3), '决策开始 · 第 1 轮');
  assert.equal(view.summary(4), '决策开始 · 第 0 轮');
  const labels = view.summary(5).split(' · 唤醒：')[1].split('、');
  assert.equal(labels.length, names.length);
  labels.forEach((label, index) => assert.match(label, /^[一-鿿]+$/, names[index]));
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
      'legacy-task-session': {session_id: 'legacy-task-session', agent_id: 1, session_name: '旧的任务会话',
        status: 'active', started_at: epoch},
    },
  };
  const view = viewer([activity('action_accepted', 'act-sleep', {kind: 'sleep'}),
    activity('action_accepted', 'act-wait-2', {kind: 'wait'}, {agent_id: 2})], world);
  t.after(() => view.close());
  const rows = tableRows(view.activities(), 'session-table');
  assert.deepEqual(rows.map(cells => [cells[2], cells[3], cells[5], cells[6]]), [
    ['张江公寓（1月5日 00:00 起）', '停留', '已结束', '睡觉（1月5日 00:00 起）'],
    ['李明的家（1月5日 00:00 起）', '停留', '进行中', '0 项活动'],
    ['去菜饭骨头汤的路上（1月5日 07:30 起）', '路上', '已结束',
      '步行前往菜饭骨头汤（1月5日 07:30 起）、听播客&lt;节目&gt;（1月5日 07:30 起）'],
    ['菜饭骨头汤（1月5日 07:40 起）', '停留', '进行中', '1 项活动'],
  ]);
  assert.ok(!view.activities().includes('旧的任务会话'));
  view.get('#agent-filter').value = '2';
  assert.deepEqual(tableRows(view.activities(), 'session-table').map(cells => cells[2]),
    ['李明的家（1月5日 00:00 起）']);
});

test('runs without location sessions render no session table', t => {
  const view = viewer([activity('action_accepted', 'act-sleep', {kind: 'sleep'})]);
  t.after(() => view.close());
  assert.ok(!view.activities().includes('地点会话'));
});

test('unknown names are explicit and logged in English while basic activities have no invented target', t => {
  const ids = [
    'movie-0099', 'drama-0099', 'game-0099', 'food-0099', 'poi-missing',
    'novel-0099', 'task-2026-01-05', 'jokes-feed', 'sintel-en-subtitles',
  ];
  const events = ids.map((id, index) => activity('action_accepted', `unknown-${index}`, {
    kind: id.startsWith('poi-') ? 'travel' : 'watch',
    ...(id.startsWith('poi-') ? {destination_id: id} : {target_id: id}),
  }));
  events.push(activity('action_accepted', 'sleep-basic', {kind: 'sleep'}));
  const view = viewer(events);
  t.after(() => view.close());
  const rendered = targets(view.activities());
  ids.forEach((id, index) => {
    assert.ok(rendered[index].includes(`名称缺失（${id}）`));
    assert.ok(view.errors.some(error => error.includes(id) && /[A-Za-z]/.test(error)));
    assert.ok(view.summary(index + 1).includes(`名称缺失（${id}）`));
  });
  assert.ok(rendered[ids.length] === '—' || rendered[ids.length] === '');
  assert.ok(view.errors.every(error => !/[\u4e00-\u9fff]/.test(error)));
});

test('empty names are reported and cannot erase earlier recorded names', t => {
  const view = viewer([
    event('observation', {
      locations: [{location_id: 'poi-keep', name: '菜饭骨头汤'},
        {location_id: 'poi-empty', name: ''}, {location_id: 'poi-space', name: ' \t '}],
      products: [{product_id: 'food-empty', name: ''}],
      messages: [{message_id: 'mail-space', subject: '   '}],
      tasks: [{task_id: 'task-space', title: ' \t '}],
    }),
    browse([{item_id: 'movie-keep', title: '海边的清晨'},
      {item_id: 'movie-empty', title: ''}, {item_id: 'movie-space', title: '   '}]),
    event('observation', {locations: [{location_id: 'poi-keep', name: ''}]}),
    browse([{item_id: 'movie-keep', title: '   '}]),
    ...['poi-keep', 'poi-empty', 'poi-space'].map(destination_id =>
      activity('action_accepted', destination_id, {kind: 'travel', destination_id})),
    ...['movie-keep', 'food-empty', 'mail-space', 'task-space', 'movie-empty', 'movie-space'].map(target_id =>
      activity('action_accepted', target_id, {kind: 'watch', target_id})),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), [
    '→ 菜饭骨头汤', '→ 名称缺失（poi-empty）', '→ 名称缺失（poi-space）', '海边的清晨',
    '名称缺失（food-empty）', '名称缺失（mail-space）', '名称缺失（task-space）',
    '名称缺失（movie-empty）', '名称缺失（movie-space）',
  ]);
  for (const id of ['poi-empty', 'poi-space', 'food-empty', 'mail-space', 'task-space', 'movie-empty', 'movie-space']) {
    assert.ok(view.errors.some(error => error.includes(id) && /[A-Za-z]/.test(error)));
  }
  assert.ok(view.errors.every(error => !/[\u4e00-\u9fff]/.test(error)));
});

test('reload clears both recorded names and missing-name diagnostics', t => {
  const missing = activity('action_accepted', 'missing', {kind: 'watch', target_id: 'drama-0099'});
  const view = viewer([
    event('observation', {locations: [{location_id: 'poi-known', name: '前次地点'}]}), missing,
  ]);
  t.after(() => view.close());
  const previousErrors = view.errors.filter(error => error.includes('drama-0099')).length;
  assert.ok(previousErrors > 0);
  view.reload([
    missing, activity('action_accepted', 'travel-new-run', {kind: 'travel', destination_id: 'poi-known'}),
  ]);
  assert.ok(!view.activities().includes('前次地点'));
  assert.ok(view.activities().includes('名称缺失（poi-known）'));
  assert.ok(view.errors.filter(error => error.includes('drama-0099')).length > previousErrors);
  assert.equal(view.state.events.length, 2);
});

test('identifiers repeated as names or titles remain explicit missing names', t => {
  const view = viewer([
    event('observation', {
      locations: [{location_id: 'poi-raw', name: 'poi-raw'}],
      products: [{product_id: 'food-raw', name: 'food-raw'}],
      messages: [{message_id: 'mail-raw', subject: 'mail-raw'}],
      tasks: [{task_id: 'task-raw', title: 'task-raw'}],
    }),
    browse([{item_id: 'movie-raw', title: 'movie-raw'}]),
    ...['food-raw', 'mail-raw', 'task-raw', 'movie-raw'].map(target_id =>
      activity('action_accepted', target_id, {kind: 'watch', target_id, title: target_id})),
    activity('action_accepted', 'travel-raw', {kind: 'travel', destination_id: 'poi-raw'}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), [
    '名称缺失（food-raw）', '名称缺失（mail-raw）', '名称缺失（task-raw）',
    '名称缺失（movie-raw）', '→ 名称缺失（poi-raw）',
  ]);
});

test('names are escaped in activities timeline and economy without changing stored events', t => {
  const name = '<img src=x onerror=alert(1)>&"\'';
  const escaped = '&lt;img src=x onerror=alert(1)&gt;&amp;&quot;&#39;';
  const view = viewer([
    event('observation', {products: [{product_id: 'food-html', name}],
      locations: [{location_id: 'poi-html', name}]}),
    activity('action_accepted', 'unsafe-title', {kind: 'watch', target_id: 'drama-html', title: name}),
    activity('action_accepted', 'unsafe-location', {kind: 'travel', destination_id: 'poi-html'}),
    event('action_result', {status: 'completed', order: {product_id: 'food-html', total_price_fen: 100}}),
  ]);
  t.after(() => view.close());
  const timeline = view.run('tlRow(S.events[1]).innerHTML');
  for (const markup of [view.activities(), timeline, view.economy()]) {
    assert.ok(markup.includes(escaped));
    assert.ok(!markup.includes('<img'));
  }
  assert.equal(JSON.parse(view.run('fetchBody(2)')).data.activity.title, name);
  assert.ok(view.summary(2).includes(name));
});

test('obsolete content catalogs neither supply names nor invent drama segment numbers', t => {
  const title = '华语短剧台词选段（源编号 16013）';
  const view = viewer([
    event('observation', {content_catalog: [
      {item_id: 'drama-0007', title: '过时目录', typical_segment_seconds: 30},
      {item_id: 'drama-unknown', title: '历史外部名称'},
    ]}),
    activity('activity_progress', 'known', {kind: 'watch', target_id: 'drama-0007', title,
      media_position_seconds: 120}),
    activity('activity_progress', 'unknown', {kind: 'watch', target_id: 'drama-unknown'}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), [title, '名称缺失（drama-unknown）']);
  assert.doesNotMatch(view.activities(), /过时目录|历史外部名称|第\d+段/);
});

test('directory loading ignores external items.json names', async t => {
  const view = viewer([activity('action_accepted', 'watch', {kind: 'watch', target_id: 'movie-0001'})]);
  t.after(() => view.close());
  let reads = 0;
  const entry = {isFile: true, name: 'items.json', file(callback) {
    reads++;
    callback({text: async () => JSON.stringify([{item_id: 'movie-0001', title: '外部片名'}])});
  }};
  await view.run('loadMemFiles')([entry]);
  assert.equal(reads, 0);
  assert.deepEqual(targets(view.activities()), ['名称缺失（movie-0001）']);
});

test('body curves read current observation values rather than obsolete body fields', t => {
  const view = viewer([
    event('observation', {balance_fen: 1000, body: {values: {
      energy_kcal: 1356.25, sleep_deficit_hours: 1.875, entertainment_need: 0.3,
    }}, active_activities: [{kind: 'watch'}, {kind: 'wait'}]}),
    event('observation', {balance_fen: 900, body: {values: {
      energy_kcal: 1230, sleep_deficit_hours: 2.5, entertainment_need: 0.5,
    }}}, {occurred_at: epoch + 60}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(JSON.parse(JSON.stringify(view.state.obs.map(({en, sl, et}) => [en, sl, et]))), [
    [1356.25, 1.875, 0.3], [1230, 2.5, 0.5],
  ]);
  assert.ok(view.body().includes('睡眠缺口 sleep_deficit_hours'));
  assert.ok(view.summary(1).includes('1356kcal'));
  assert.ok(view.summary(1).includes('进行中：观看、等待'));
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

test('activity lanes preserve simultaneous work and pauses using settled intervals', t => {
  const first = 'work-a', second = 'wait-b';
  const view = viewer([
    activity('action_accepted', first, {kind: 'work', started_at: epoch + 60},
      {occurred_at: epoch + 60}),
    activity('action_accepted', second, {kind: 'wait', started_at: epoch + 90},
      {occurred_at: epoch + 90}),
    activity('activity_progress', first, {kind: 'work', started_at: epoch + 60},
      {occurred_at: epoch + 180, data: {activity: {
        activity_id: first, kind: 'work', started_at: epoch + 60,
      }, interval_start: epoch + 60, interval_end: epoch + 180}}),
    activity('activity_progress', second, {kind: 'wait', started_at: epoch + 90},
      {occurred_at: epoch + 150, data: {activity: {
        activity_id: second, kind: 'wait', started_at: epoch + 90,
      }, interval_start: epoch + 90, interval_end: epoch + 150}}),
    activity('activity_progress', first, {kind: 'work', started_at: epoch + 60},
      {occurred_at: epoch + 240, data: {activity: {
        activity_id: first, kind: 'work', started_at: epoch + 60,
      }, interval_start: epoch + 210, interval_end: epoch + 240}}),
  ]);
  t.after(() => view.close());
  const work = view.state.acts.find(row => row.aid === first);
  const wait = view.state.acts.find(row => row.aid === second);
  assert.equal(work.actualSeconds, 150);
  assert.equal(wait.actualSeconds, 60);
  assert.deepEqual(JSON.parse(JSON.stringify(work.intervals)),
    [[epoch + 60, epoch + 180], [epoch + 210, epoch + 240]]);
  assert.deepEqual(JSON.parse(JSON.stringify(wait.intervals)), [[epoch + 90, epoch + 150]]);
  const markup = view.activities();
  assert.equal((markup.match(/class="act-lane-bar"/g) || []).length, 3);
  assert.ok(markup.includes('同一时间轴上的条块表示实际执行区间'));
});
