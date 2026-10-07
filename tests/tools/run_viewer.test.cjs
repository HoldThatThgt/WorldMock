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
    assert.deepEqual(JSON.parse(app.get('#ctx-raw').textContent), {...layers[0].groups[0].shared, ...layers[0].groups[0].targets[0], kind:layers[0].groups[0].kind});
    app.get('#ctx-item').value = '';
    app.get('#ctx-item').onchange();
    assert.ok(app.get('#ctx-raw').textContent.startsWith('[{"layer":"工具层"'));

    app.get('button:message:1:progress').onclick();
    const progressOptions = app.get('#ctx-item').innerHTML;
    assert.ok(progressOptions.includes('tool_call · start_activity · self_note（未经核验）'));
    assert.ok(progressOptions.includes('message_arrival · 王磊'));
    assert.ok(progressOptions.includes('errand_completed · 囤咖啡豆'));
    assert.ok(progressOptions.includes('errand_reminder · 买牛奶'));
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
    event('action_requested', {tool_name: 'start_activity', request: {items: [{target:'自己',way:'等待',from:null,to:null}],answering:null,prefer:null}, request_sha256: 'f'.repeat(64), reused: false, self_note: '先歇一会儿，再去买菜。'}),
    event('action_requested', {tool_name: 'start_activity', request: {items: [{target:'自己',way:'睡觉',from:null,to:null}],answering:null,prefer:null}, request_sha256: 'e'.repeat(64), reused: false, self_note: null}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '请求 开始活动 · 自己 · 等待 · self_note（未经核验）：先歇一会儿，再去买菜。');
  assert.equal(view.summary(2), '请求 开始活动 · 自己 · 睡觉');
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
    items: items.map(([target, , way]) => ({target,way,from:null,to:null})), answering:null,prefer:null,
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
    assert.ok(view.summary(index * 3 + 3).endsWith(`${place.name} · 步行`));
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
    `${row.title || row.name} · ${row.way}`)));
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
  assert.equal(view.summary(2), '请求 开始活动 · 菜饭 · 远程下单；自己 · 等待');
  assert.equal(view.summary(3), '请求 开始活动 · 张江公寓 · 驾车');
  assert.equal(view.summary(4), '请求 开始活动 · 家里的菜饭 · 食用');
  assert.equal(view.summary(5), '请求 开始活动 · 自己 · 睡觉');
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
    event('environment_event', {kind: 'kind_without_label'}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '世界事件 · 零点计划生成 · 2 项目标 · 提醒 01/05 08:00');
  assert.equal(view.summary(2), '世界事件 · 零点计划生成 · 0 项目标');
  lifecycle.forEach(([, label], index) => assert.equal(
    view.summary(index + 3), `世界事件 · ${label} · 等待（1月5日 06:00 起）`));
  assert.equal(view.summary(lifecycle.length + 3), '世界事件 · 身体结算');
  // 标签表里没有的种类原样显示，不编造中文说法。
  assert.equal(view.summary(lifecycle.length + 4), '世界事件 · kind_without_label');
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
  assert.equal(names.length, 25);
  const due = epoch + 3600;
  const view = viewer([
    event('decision_started', {round_order: 0, wake_reasons: ['run_start']}),
    event('decision_started', {round_order: 0, wake_reasons: [
      'no_active_activity', 'plan_reminder_due', 'progress_report_due']}),
    event('decision_started', {round_order: 1, wake_reasons: []}),
    event('decision_started', {round_order: 0}),
    // 词表含 errand_reminder_due：同一人物同一时刻先交付一条事务提醒，该原因才有事务名称可写。
    event('observation', {products: [{product_id: 'food-milk', name: '纯牛奶'}]}),
    errandEvent('errand_reminded', errandSnapshot({reminded_at: due}), {}, {occurred_at: due}),
    event('decision_started', {round_order: 0, wake_reasons: names}, {occurred_at: due}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '决策开始 · 第 0 轮 · 唤醒：运行开始');
  assert.equal(view.summary(2), '决策开始 · 第 0 轮 · 唤醒：没有进行中的活动、计划提醒到点、该播报进展');
  assert.equal(view.summary(3), '决策开始 · 第 1 轮');
  assert.equal(view.summary(4), '决策开始 · 第 0 轮');
  const labels = view.summary(7).split(' · 唤醒：')[1].split('、');
  assert.equal(labels.length, names.length);
  labels.forEach((label, index) => assert.match(label,
    names[index] === 'errand_reminder_due' ? /^[一-鿿]+「买牛奶」$/ : /^[一-鿿]+$/, names[index]));
  assert.deepEqual(view.errors, []);
});

/** @brief 控制请求里的一条个人事务变更：八个键全部写出（src/worldmock/life/models.py 的 ErrandChange），fields 覆盖
 *  该变更种类要写的键。 */
function errandChange(change, name, fields = {}) {
  return {change, name, outcome: null, item: null, quantity: null, memo: null, remind_time: null, reason: null,
    ...fields};
}

/** @brief 事件载荷里的事务快照，按 ErrandState 的十二个字段（src/worldmock/life/errand_state.py）；fields 覆盖个别字段。 */
function errandSnapshot(fields = {}) {
  return {errand_id: 'action-0007:errand:0', name: '买牛奶', outcome: 'obtain', product_id: 'food-milk', quantity: 2,
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
  const added = errandChange('新增', '买牛奶', {outcome: '取得', item: '纯牛奶', quantity: 2,
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

test('errand changes are read by the same rule as control_errands: eight keys present, values as written', t => {
  const view = viewer([]);
  t.after(() => view.close());
  // 八个键与顺序取自 Python 读取入口的 _ERRAND_KEYS，镜像实现不得另立一套。
  const reader = readFileSync(path.join(__dirname, '../../src/worldmock/life/control_items.py'), 'utf8');
  const keys = [.../_ERRAND_KEYS = \(([^)]*)\)/.exec(reader)[1].matchAll(/"([a-z_]+)"/g)].map(match => match[1]);
  assert.equal(keys.length, 8);
  assert.deepEqual(JSON.parse(JSON.stringify(view.run('ERRAND_CHANGE_KEYS'))), keys);
  const read = request => JSON.parse(JSON.stringify(view.run(`controlErrands(${JSON.stringify(request)})`)));
  const added = errandChange('新增', '买牛奶', {outcome: '取得', item: '纯牛奶', quantity: 2, remind_time: '18:30'});
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
  assert.throws(() => read({items: [], errands: [{change: '新增', name: '买牛奶'}]}), /errand change lacks outcome$/);
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
    // 条目级的出入与缺键同等对待：一条变更不是对象或缺八键之一，整库不加载。
    assert.throws(() => viewer(requested(tool, {items, errands: [null]})), /errand change is not an object/, tool);
    assert.throws(() => viewer(requested(tool, {items, errands: [{change: '新增', name: '买牛奶'}]})),
      /errand change lacks outcome/, tool);
    const view = viewer(requested(tool, {items, errands: [errandChange('取消', '买牛奶', {reason: '不买了'})]}));
    assert.ok(view.summary(1).endsWith(' · 事务：取消「买牛奶」'), view.summary(1));
    view.close();
  }
  // 其他工具的请求不带 errands，照常显示。
  const other = viewer([startRequest(['自己', 'interact', '等待'])]);
  assert.equal(other.summary(1), '请求 开始活动 · 自己 · 等待');
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
    event('observation', {products: [{product_id: 'food-milk', name: '纯牛奶'}]}),
    activity('action_accepted', 'act-wait', {kind: 'wait'}),
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
  // 引擎的观察只在 data.products 里列看得到或持有的商品，事务却可以指目录里的任一商品；全部商品名记在每次观察的
  // errand_item_bindings（src/worldmock/life/errands.py 的 bindings：按商品身份排序的 {name, product_id}）。
  const bindings = [{name: '康师傅 冰红茶', product_id: 'food-tea'}, {name: '纯牛奶', product_id: 'food-milk'},
    {name: '素炒什锦食材包', product_id: 'ingredient-kit'}];
  const observed = (fields = {}, envelope = {}) => event('observation',
    {products: [], errand_bindings: [], errand_item_bindings: bindings, ...fields}, envelope);
  const view = viewer([
    observed(),
    errandEvent('errand_registered', errandSnapshot()),
    errandEvent('errand_registered', errandSnapshot({name: '补食材包', errand_id: 'action-0007:errand:1',
      product_id: 'ingredient-kit'})),
    errandEvent('errand_reminded', errandSnapshot({name: '囤冰红茶', errand_id: 'action-0007:errand:2',
      product_id: 'food-tea'})),
    observed({}, {agent_id: 2}),
    event('action_result', {status: 'completed', order: {product_id: 'food-tea', total_price_fen: 450}}),
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
    event('observation', {products: [{product_id: 'food-milk', name: '纯牛奶'}],
      interaction_bindings: [{object_id: 'food-held', object: '持有物：全麦面包'}],
      errand_item_bindings: [{name: '臻浓牛奶', product_id: 'food-milk'}, {name: '全麦面包', product_id: 'food-held'},
        {name: '', product_id: 'food-empty'}, {name: ' food-raw ', product_id: 'food-raw'},
        {name: '咖啡豆', product_id: 'food-coffee'}]}),
    // 同一商品在后来的观察里另有写法时，取最先记录的有效名称；后来才出现在 data.products 的名称仍然优先。
    event('observation', {products: [{product_id: 'food-late', name: '店里的叫法'}],
      errand_item_bindings: [{name: '咖啡豆（新包装）', product_id: 'food-coffee'},
        {name: '目录里的叫法', product_id: 'food-late'}]}),
    ...['food-milk', 'food-held', 'food-empty', 'food-raw', 'food-coffee', 'food-late'].map((product_id, index) =>
      errandEvent('errand_registered', errandSnapshot({product_id, errand_id: `action-0007:errand:${index}`}))),
  ]);
  t.after(() => view.close());
  const products = [3, 4, 5, 6, 7, 8].map(sequence => view.summary(sequence).split(' · 商品 ')[1]);
  assert.deepEqual(products, ['纯牛奶', '持有物：全麦面包', '名称缺失（food-empty）', '名称缺失（food-raw）', '咖啡豆',
    '店里的叫法']);
  assert.deepEqual(view.errors, ['Missing recorded object name: food-empty', 'Missing recorded object name: food-raw']);
});

test('an errand product without a recorded name is reported as missing and an errand is never named by its identity', t => {
  const name = '<img src=x onerror=alert(1)>';
  const view = viewer([
    event('observation', {products: [{product_id: 'food-milk', name: '纯牛奶'}]}),
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

test('the errand reminder wake reason names the errands reminded for that person at that moment', t => {
  const due = epoch + 3600;
  const reminded = (fields, envelope = {}) => errandEvent('errand_reminded',
    errandSnapshot({remind_at: due, reminded_at: due, ...fields}), {}, {occurred_at: due, ...envelope});
  const started = (wake_reasons, envelope = {}) => event('decision_started',
    {round_id: 'round-0003', round_order: 0, world_state_version: 12, wake_reasons}, {occurred_at: due, ...envelope});
  const view = viewer([
    event('observation', {products: [{product_id: 'food-milk', name: '纯牛奶'},
      {product_id: 'food-bread', name: '全麦面包'}, {product_id: 'food-coffee', name: '咖啡豆'}]}, {occurred_at: epoch}),
    reminded({}),
    reminded({name: '买面包', errand_id: 'action-0011:errand:1', product_id: 'food-bread'}),
    reminded({name: '买咖啡豆', errand_id: 'action-0020:errand:0', product_id: 'food-coffee'}, {agent_id: 2}),
    started(['plan_reminder_due', 'errand_reminder_due']),
    started(['errand_reminder_due'], {agent_id: 2}),
    started(['no_active_activity']),
    started(['errand_reminder_due', 'plan_reminder_due'], {occurred_at: due + 3600}),
    reminded({name: '买面包', errand_id: 'action-0011:errand:1', product_id: 'food-bread'}, {occurred_at: due + 3600}),
    // 提醒事件在、快照却没有有效名称：两条提醒逐个写出，缺名的那条照缺失提示。
    reminded({name: 'action-0030:errand:0', errand_id: 'action-0030:errand:0'}, {occurred_at: due + 7200}),
    reminded({name: '买面包', errand_id: 'action-0011:errand:1', product_id: 'food-bread'}, {occurred_at: due + 7200}),
    started(['errand_reminder_due'], {occurred_at: due + 7200}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(5), '决策开始 · 第 0 轮 · 唤醒：计划提醒到点、事务提醒到点「买牛奶」「买面包」');
  assert.equal(view.summary(6), '决策开始 · 第 0 轮 · 唤醒：事务提醒到点「买咖啡豆」');
  assert.equal(view.summary(7), '决策开始 · 第 0 轮 · 唤醒：没有进行中的活动');
  // 另一时刻的提醒与排在决策之后的提醒都不算进来：记录里没有对应的提醒事件时，原因说法之后显示缺失提示，并输出
  // 英文错误日志（「缺失与重新加载」），不是只留原因说法。
  assert.equal(view.summary(8), '决策开始 · 第 0 轮 · 唤醒：事务提醒到点 名称缺失（个人事务）、计划提醒到点');
  assert.equal(view.summary(12), '决策开始 · 第 0 轮 · 唤醒：事务提醒到点 名称缺失（个人事务）「买面包」');
  assert.deepEqual(view.errors, ['Missing errand reminder record for wake reason at event 8',
    'Missing recorded errand name at event 10']);
  const row = view.run('tlRow(S.events[7]).innerHTML');
  assert.ok(row.includes('事务提醒到点 名称缺失（个人事务）'), row);
  assert.equal(view.summary(2), '世界事件 · 事务提醒 · 「买牛奶」 · 商品 纯牛奶');
  assert.equal(view.summary(10), '世界事件 · 事务提醒 · 名称缺失（个人事务） · 商品 纯牛奶');
  view.state.events.forEach((entry, index) => {
    assert.doesNotMatch(entry.sum, ERRAND_IDENTITY);
    assert.doesNotMatch(view.run(`tlRow(S.events[${index}]).innerHTML`), ERRAND_IDENTITY);
  });
  // 同一缺失只记一次日志：重排时间线不重复输出。
  view.run('S.events.forEach(entry => { entry.sum = makeSummary(entry); })');
  assert.equal(view.errors.length, 2, view.errors.join('\n'));
  view.reload([started(['errand_reminder_due'])]);
  assert.equal(view.summary(1), '决策开始 · 第 0 轮 · 唤醒：事务提醒到点 名称缺失（个人事务）',
    'reminders of the previous run are dropped');
  assert.equal(view.errors.at(-1), 'Missing errand reminder record for wake reason at event 1');
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
  await view.run('loadRunFiles')([], [entry]);
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

const HOUR = 3600;
/** @brief 距 2026-01-05 当地零点 hours 小时的时刻（epoch 是当天 06:00）。 */
const at = hours => epoch + (hours - 6) * HOUR;

/** @brief 一段已结算的活动：受理一条、进展一条，已结算区间为当地 [from, to] 小时（从 2026-01-05 零点起算）。 */
function settled(id, kind, from, to, fields = {}) {
  const snapshot = {activity_id: id, kind, started_at: at(from), ...fields};
  return [
    activity('action_accepted', id, snapshot, {occurred_at: at(from)}),
    activity('activity_progress', id, snapshot, {occurred_at: at(to), data: {
      activity: snapshot, interval_start: at(from), interval_end: at(to)}}),
  ];
}

/** @brief 取出泳道各行：行首日期、是否周末、轨道高度与其中的条块（位置、所在排、悬停文字与条上短名）。 */
function lanes(markup) {
  const lane = new RegExp('<div class="act-lane( weekend)?">\\s*<button type="button" class="act-lane-label" ' +
    'data-day="(\\d+)"[^>]*>([^<]*)</button>\\s*<div class="act-lane-track" style="height:(\\d+)px">' +
    '([\\s\\S]*?)</div></div>', 'g');
  const bar = new RegExp('<span class="act-lane-bar" style="left:([\\d.]+)%;width:([\\d.]+)%;top:(\\d+)px;' +
    'background:[^"]*" title="([^"]*)"><span class="act-lane-text">([^<]*)</span></span>', 'g');
  return [...markup.matchAll(lane)].map(row => ({
    weekend: Boolean(row[1]), day: Number(row[2]), label: row[3], height: Number(row[4]),
    bars: [...row[5].matchAll(bar)].map(match => ({left: Number(match[1]), width: Number(match[2]),
      top: Number(match[3]), tip: match[4], text: match[5]})),
  }));
}

/** @brief 一场六天的运行：周一工作并同时听播客，周一 23:00 睡到周二 07:00，周六做生活活动。 */
function sixDayRun() {
  return [
    ...settled('act-work', 'work', 9, 12, {target_id: 'task-1', title: '会议纪要'}),
    ...settled('act-podcast', 'watch', 10, 10.5, {target_id: 'podcast-1', title: '播客·工位闲谈'}),
    ...settled('act-sleep', 'sleep', 23, 31),
    ...settled('act-wash', 'lifestyle', 5 * 24 + 8, 5 * 24 + 8.25, {target_id: 'wash-1', title: '晨间洗漱'}),
  ];
}

test('activity lanes draw one row per local day on a 24-hour axis and split intervals at local midnight', t => {
  const view = viewer(sixDayRun());
  t.after(() => view.close());
  const markup = view.activities();
  const rows = lanes(markup);
  // 周一到周六每天一行（中间没有活动的日子也占一行），而不是每个活动一行；周六的行标作周末。
  assert.deepEqual(rows.map(row => row.label),
    ['01/05 周一', '01/06 周二', '01/07 周三', '01/08 周四', '01/09 周五', '01/10 周六']);
  assert.deepEqual(rows.map(row => row.weekend), [false, false, false, false, false, true]);
  assert.deepEqual(rows.map(row => row.bars.length), [3, 1, 0, 0, 0, 1]);
  const close = (actual, expected) => assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} != ${expected}`);
  const [work, podcast, evening] = rows[0].bars;
  // 横轴是当天 00:00–24:00：09:00–12:00 的工作从 37.5% 起、宽 12.5%，与运行总时长无关。
  close(work.left, 9 / 24 * 100); close(work.width, 3 / 24 * 100);
  close(podcast.left, 10 / 24 * 100); close(podcast.width, 0.5 / 24 * 100);
  // 同时进行的两个活动上下并排，轨道随之加高；只有一排的日子保持一排的高度。
  assert.deepEqual([work.top, podcast.top], [4, 21]);
  assert.deepEqual(rows.map(row => row.height), [41, 24, 24, 24, 24, 24]);
  // 跨零点的睡眠在两天各占一段，悬停文字都给出整段起止与时长。
  close(evening.left, 23 / 24 * 100); close(evening.width, 1 / 24 * 100);
  const [morning] = rows[1].bars;
  close(morning.left, 0); close(morning.width, 7 / 24 * 100);
  for (const part of [evening, morning]) assert.equal(part.tip, '01/05 23:00–01/06 07:00（8小时） · 睡眠');
  // 条上短名：有对象写对象，没有对象写活动类型；类型一律中文。
  assert.deepEqual([work.text, podcast.text, evening.text, rows[5].bars[0].text],
    ['会议纪要', '播客·工位闲谈', '睡眠', '晨间洗漱']);
  assert.equal(rows[5].bars[0].tip, '01/10 08:00–01/10 08:15（15分） · 生活活动 晨间洗漱');
  assert.ok(markup.includes('00:00</span>') && markup.includes('24:00</span>'));
  assert.doesNotMatch(markup, />lifestyle<|lifestyle 晨间洗漱/);
});

test('the day filter narrows lanes, the activity table and sessions to one local day', t => {
  const session = (id, name, from, to) => ({session_id: id, agent_id: 1, kind: 'stay', status: 'ended',
    name, started_at: at(from), ended_at: at(to), activity_ids: []});
  const view = viewer(sixDayRun(), {sessions: {
    monday: session('monday', '公司（1月5日 09:00 起）', 9, 18),
    night: session('night', '家（1月5日 18:30 起）', 18.5, 32),
    saturday: {...session('saturday', '家（1月10日 00:00 起）', 120, 121), status: 'active', ended_at: null},
  }});
  t.after(() => view.close());
  const sessions = () => tableRows(view.activities(), 'session-table').map(cells => cells[2]);
  assert.equal(targets(view.activities()).length, 4);
  assert.equal(sessions().length, 3);
  const tuesday = view.run(`dayIndex(${at(30)})`);
  view.get('#day-filter').value = String(tuesday);
  const rows = lanes(view.activities());
  assert.deepEqual(rows.map(row => [row.day, row.label, row.bars.length]), [[tuesday, '01/06 周二', 1]]);
  // 表格与会话只留与这一天有交集的：跨零点的睡眠与停留算在两天里。
  assert.deepEqual(targets(view.activities()), ['—']);
  assert.deepEqual(sessions(), ['家（1月5日 18:30 起）']);
  assert.ok(view.activities().includes('01/06 周二 共 1 段活动'));
  // 没有活动的日子仍画出那一天的空行，表格写明没有活动。
  view.get('#day-filter').value = String(tuesday + 1);
  assert.deepEqual(lanes(view.activities()).map(row => [row.label, row.bars.length]), [['01/07 周三', 0]]);
  assert.ok(view.activities().includes('所选日期没有活动'));
  // 进行中的会话（没有结束时刻）按运行末尾计。
  view.get('#day-filter').value = String(tuesday + 4);
  assert.deepEqual(sessions(), ['家（1月10日 00:00 起）']);
  // 换一个文件时日期回到全部。
  view.reload(sixDayRun());
  assert.equal(view.get('#day-filter').value, '');
  assert.equal(lanes(view.activities()).length, 6);
});

/** @brief 重排时间线并取出列表里的行：分隔行给出 {day} 文字，事件行给出 {seq}。 */
function timelineRows(view) {
  const list = view.get('#tl-list');
  list.children.length = 0;
  view.run('timelineBounds(); refreshTimeline()');
  return list.children.flatMap(fragment => fragment.children).map(row =>
    row.className === 'tl-day' ? {day: row.innerHTML} : {seq: Number(row.dataset.seq)});
}

test('the timeline groups rows under local day headers and follows the day filter', t => {
  const view = viewer([
    event('checkpoint_created', {checkpoint_id: 'c-1'}, {occurred_at: at(8), agent_id: null}),
    event('checkpoint_created', {checkpoint_id: 'c-2'}, {occurred_at: at(23.99), agent_id: null}),
    event('checkpoint_created', {checkpoint_id: 'c-3'}, {occurred_at: at(24), agent_id: null}),
    event('checkpoint_created', {checkpoint_id: 'c-4'}, {occurred_at: at(74), agent_id: null}),
  ]);
  t.after(() => view.close());
  view.run('TL.types = new Set(Object.keys(S.typeCounts))');
  assert.deepEqual(timelineRows(view), [
    {day: '01/05 周一<span class="n">2 条</span>'}, {seq: 1}, {seq: 2},
    {day: '01/06 周二<span class="n">1 条</span>'}, {seq: 3},
    {day: '01/08 周四<span class="n">1 条</span>'}, {seq: 4},
  ]);
  // 行内只写时分秒，完整时刻留在悬停文字里；摘要正文完整显示，不再带悬停副本。
  const row = view.run('tlRow(S.events[2]).innerHTML');
  assert.match(row, /<span class="tl-time" title="2026\/01\/06 00:00:00">00:00:00<\/span>/);
  assert.match(row, /<span class="tl-sum">存档<\/span>/);
  // 选中周二：只剩当天的事件，零点整属于新的一天；滑块两端收到这一天之内，步长随之变细。
  view.get('#day-filter').value = String(view.run(`dayIndex(${at(24)})`));
  assert.deepEqual(timelineRows(view), [{day: '01/06 周二<span class="n">1 条</span>'}, {seq: 3}]);
  assert.deepEqual(JSON.parse(JSON.stringify(view.run('[TL.lo, TL.hi, TL.tmin, TL.tmax, TL.step]'))),
    [at(24), at(48) - 1, at(24), at(48) - 1, 60]);
  // 回到全部：两端回到整个运行，多日运行用十分钟步长。
  view.get('#day-filter').value = '';
  assert.equal(timelineRows(view).filter(entry => 'seq' in entry).length, 4);
  assert.deepEqual(JSON.parse(JSON.stringify(view.run('[TL.lo, TL.hi, TL.step]'))), [at(8), at(74), 600]);
});

test('memory, thinking records and context requests follow the day filter', t => {
  const reply = text => event('model_response', {response: {choices: [{message: {
    provider_specific_fields: {reasoning_content: text}}}]}});
  const memory = text => event('memory_updated', {operation: 'append_episodes', created_episode_ids: ['m'],
    merged_episode_ids: [], episodes: [{id: 'm', type: 'routine', text}]});
  const monday = {occurred_at: at(9)}, tuesday = {occurred_at: at(33)};
  const view = viewer([
    {...memory('周一的记忆'), ...monday}, {...memory('周二的记忆'), ...tuesday},
    {...reply('周一的思考'), ...monday}, {...reply('周二的思考'), ...tuesday},
    {...contextEvent('monday-request', contextRequest()), ...monday},
    {...contextEvent('tuesday-request', contextRequest()), ...tuesday},
  ]);
  t.after(() => view.close());
  const thinking = () => Array.from(view.run("RS.items = rsQuery(''); refreshRs(); RS.filtered.map(item => item.seq)"));
  const requests = () => [...view.context().matchAll(/<option value="(\d+)"/g)].map(match => Number(match[1]));
  // 未筛选：记忆按写入日分组（新的在前），条目只写时分秒。
  const all = view.memory();
  assert.ok(all.includes('人物记忆正文（2 条）'));
  assert.ok(all.indexOf('<div class="tl-day">01/06 周二</div>') < all.indexOf('周二的记忆'));
  assert.ok(all.indexOf('周二的记忆') < all.indexOf('<div class="tl-day">01/05 周一</div>'));
  assert.match(all, /<div class="meta" title="2026\/01\/05 09:00:00">09:00:00 · 陈曦 ·/);
  assert.deepEqual(thinking(), [3, 4]);
  assert.deepEqual(requests(), [5, 6]);
  view.get('#day-filter').value = String(view.run(`dayIndex(${at(33)})`));
  const filtered = view.memory();
  assert.ok(filtered.includes('人物记忆正文（1 条）') && filtered.includes('周二的记忆'));
  assert.ok(!filtered.includes('周一的记忆') && !filtered.includes('01/05 周一'));
  assert.deepEqual(thinking(), [4]);
  assert.deepEqual(requests(), [6]);
  // 从时间线进入不在所选日期里的请求：日期回到全部，请求本身照常打开。
  view.run('ContextView.select(5)');
  assert.equal(view.get('#day-filter').value, '');
  assert.deepEqual(requests(), [5, 6]);
  // 请求就在所选日期里时不动日期。
  view.get('#day-filter').value = String(view.run(`dayIndex(${at(33)})`));
  view.run('ContextView.select(6)');
  assert.notEqual(view.get('#day-filter').value, '');
});

test('curves and money flows of a filtered day keep one neighbour point on each side', t => {
  const observed = (hours, balance, energy) => event('observation', {balance_fen: balance, body: {values: {
    energy_kcal: energy, sleep_deficit_hours: 0, entertainment_need: 0}}}, {occurred_at: at(hours)});
  const view = viewer([observed(8, 1000, 1500), observed(22, 900, 1300), observed(31, 900, 1700),
    observed(44, 650, 1400), observed(55, 650, 1600), observed(60, 500, 1500)]);
  t.after(() => view.close());
  const points = key => JSON.parse(JSON.stringify(view.run(`chartSeries(S.obs, '${key}')[0].points`)));
  assert.equal(points('en').length, 6);
  assert.equal(view.run('chartDomain()'), undefined);
  assert.ok(view.body().includes('共 6 个观测点'));
  assert.equal((view.economy().match(/余额变动/g) || []).length, 3);
  view.get('#day-filter').value = String(view.run(`dayIndex(${at(24)})`));
  // 周二当天的两个观测，加上周一最后一个与周三第一个，线才能连到当天两端；横轴固定为这一天。
  assert.deepEqual(points('en'), [[at(22), 1300], [at(31), 1700], [at(44), 1400], [at(55), 1600]]);
  assert.deepEqual(JSON.parse(JSON.stringify(view.run('chartDomain()'))), [at(24), at(48)]);
  assert.ok(view.body().includes('共 2 个观测点'));
  // 流水只列发生在当天的变动。
  const economy = view.economy();
  assert.equal((economy.match(/余额变动/g) || []).length, 1);
  assert.ok(economy.includes('2026/01/06 20:00:00') && economy.includes('-250'));
  // 所选日期之后才有观测时只留最后一个点，不把整条曲线画进来。
  view.get('#day-filter').value = String(view.run(`dayIndex(${at(24 * 9)})`));
  assert.deepEqual(points('en'), [[at(60), 1500]]);
});

test('chart ticks stay apart at every run length and weekly labels fall on Mondays', t => {
  const view = viewer([event('checkpoint_created', {checkpoint_id: 'c'}, {occurred_at: at(0)})]);
  t.after(() => view.close());
  const ticks = (days, width) => JSON.parse(JSON.stringify(view.run(
    `timeTicks(${at(0)}, ${at(0) + days * 86400}, ${width})`)));
  /** @brief 相邻带标签刻度的最小像素间距。 */
  const gap = (rows, days, width) => {
    const labelled = rows.filter(row => row.label).map(row => (row.t - at(0)) / (days * 86400) * width);
    return Math.min(...labelled.slice(1).map((x, index) => x - labelled[index]));
  };
  // 一天：整点刻度，零点写日期。
  const day = ticks(1, 900);
  assert.deepEqual(day.map(row => row.label),
    ['01/05', '02:00', '04:00', '06:00', '08:00', '10:00', '12:00', '14:00', '16:00', '18:00', '20:00', '22:00', '01/06']);
  assert.deepEqual(ticks(1, 300).map(row => row.label), ['01/05', '06:00', '12:00', '18:00', '01/06']);
  // 两周：每天一条线、每天一个日期。
  const fortnight = ticks(14, 900);
  assert.equal(fortnight.length, 15);
  assert.ok(fortnight.every(row => row.label) && fortnight[0].label === '01/05' && fortnight[14].label === '01/19');
  // 十二周：每天仍有分界线，日期只写在周一。
  const formal = ticks(84, 900);
  assert.equal(formal.length, 85);
  const mondays = formal.filter(row => row.label);
  assert.equal(mondays.length, 13);
  assert.ok(mondays.every(row => view.run(`S.fmt.wd(new Date(${row.t} * 1000))`) === '周一'));
  // 更窄更长时连没有标签的日分界线也不画。
  const year = ticks(365, 300);
  assert.ok(year.length < 10 && year.every(row => row.label));
  for (const [rows, days, width] of [[day, 1, 900], [fortnight, 14, 900], [formal, 84, 900], [year, 365, 300],
    [ticks(14, 300), 14, 300], [ticks(84, 400), 84, 400], [ticks(2, 500), 2, 500], [ticks(3, 500), 3, 500]]) {
    assert.ok(gap(rows, days, width) >= 46, `${days} days at ${width}px: ${gap(rows, days, width)}`);
  }
});

test('accepted requests without an activity and the remaining event kinds read in Chinese', t => {
  const view = viewer([
    event('action_accepted', {status: 'completed', message: 'Activities concluded', data: {
      task: ['睡觉（1月5日 00:00 起）', '听播客<节目>（1月5日 07:30 起）'], activity_ids: ['act-1', 'act-2']}}),
    event('action_accepted', {status: 'completed', message: 'Purchase accepted', data: {total_price_fen: 1200}}),
    activity('action_accepted', 'act-call', {kind: 'call', title: '和妈妈通话'}),
    activity('action_accepted', 'act-chat', {kind: 'chat'}),
    event('environment_event', {kind: 'message_arrival'}),
    event('environment_event', {kind: 'model_npc_reply_pending'}),
    event('environment_event', {kind: 'model_npc_reply'}),
    event('environment_event', {kind: 'branch_created'}),
    event('memory_updated', {operation: 'refresh_summary', created_episode_ids: [], merged_episode_ids: []}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '已受理 · 睡觉（1月5日 00:00 起）、听播客<节目>（1月5日 07:30 起）');
  assert.equal(view.summary(2), '已受理');
  assert.ok(view.run('tlRow(S.events[0]).innerHTML').includes('听播客&lt;节目&gt;'));
  assert.equal(view.summary(3), '已受理 · 通话 · 计划 10分');
  assert.equal(view.summary(4), '已受理 · 交谈 · 计划 10分');
  assert.deepEqual([5, 6, 7, 8].map(sequence => view.summary(sequence)), ['世界事件 · 消息到达',
    '世界事件 · 联系人回复待生成', '世界事件 · 联系人回复', '世界事件 · 分支创建']);
  assert.equal(view.summary(9), '记忆更新 · 刷新主观总结 · 0 段记忆');
  for (const entry of view.state.events) assert.doesNotMatch(entry.sum, /undefined|null|·  ·/);
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
  return {schema_version: 2, run_id: runId, source_group_id: runId, agent_ids: [1, 2], ...fields};
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
  const injection = readFileSync(path.join(__dirname, '../../src/worldmock/profile/injection.py'), 'utf8')
    .replace(/\r\n/g, '\n');
  const codes = [.../InputWarningCode = Literal\[\n([\s\S]*?)\n\]/.exec(injection)[1].matchAll(/"([A-Z_]+)"/g)]
    .map(match => match[1]);
  assert.equal(codes.length, 10);
  const scenarioCodes = ['DELIVERY_ROUTE_UNAVAILABLE', 'LOCATION_NAME_SUSPICIOUS'];
  const personal = codes.filter(code => !scenarioCodes.includes(code));
  const second = personal.filter((_, index) => index % 2), first = personal.filter((_, index) => !(index % 2));
  const route = '门店 dinner（鹅庄台湾美食）到目的地 home（家）没有可达的驾车路线（unknown），3 项配送供给送不到。';
  // 清单顺序故意打乱：人物 2 的警告在前，一条场景级夹在两人之间；页面按人物编号、再场景级排列。
  const warnings = [
    ...second.map(code => inputWarning(code, 2, `李明：${code}`)), inputWarning(scenarioCodes[0], null, route),
    ...first.map(code => inputWarning(code, 1, `陈曦：${code}`)),
    inputWarning(scenarioCodes[1], null, '地点 poi-1 的显示名「肯德基<东经121.50°>」带坐标。'),
  ];
  const view = await loadedViewer(t, [droppedFile('manifest.json', runManifest({input_warnings: warnings}))]);
  const card = warningCard(view.overview());
  assert.deepEqual(warningRows(card), [
    ...first.map(code => ['陈曦', code, `陈曦：${code}`]), ...second.map(code => ['李明', code, `李明：${code}`]),
    ['场景级', scenarioCodes[0], route],
    ['场景级', scenarioCodes[1], '地点 poi-1 的显示名「肯德基&lt;东经121.50°&gt;」带坐标。'],
  ]);
  assert.deepEqual(new Set(warningRows(card).map(row => row[1])), new Set(codes));
  assert.ok(card.includes('共 10 条：人物 8 条、场景级 2 条') && card.includes('manifest.json'));
  assert.ok(!card.includes('<东经'));
  view.get('#agent-filter').value = '2';
  const filtered = warningCard(view.overview());
  assert.deepEqual(warningRows(filtered).map(row => row[0]), [...second.map(() => '李明'), '场景级', '场景级']);
  assert.ok(filtered.includes('当前筛选 6 条'));
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
    input_warnings: [inputWarning('TASTE_GENRES_UNMATCHED', 1, '子运行清单冻结的警告')]});
  const exported = {run_id: 'viewer-test-run', frozen_run_manifest: {...child,
    input_warnings: [inputWarning('TASTE_GENRES_UNMATCHED', 1, '导出清单中的副本')]}};
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
  assert.deepEqual(warningRows(card), [['陈曦', 'TASTE_GENRES_UNMATCHED', '子运行清单冻结的警告']]);
  assert.ok(card.includes('resumed/manifest.json') && !card.includes('frozen_run_manifest'));
  assert.equal(view.state.ignoredManifests, 2);
  assert.equal(view.state.memFiles['1'].memoryMd, '今天把纪要交了。');
  const exportDirectory = () => directoryEntry('worldmock-export', [fileEntry('manifest.json', exported)]);
  // 运行清单在前、导出副本在后时同样取运行清单本身。
  const directFirst = await loadedViewer(t, [droppedFile('manifest.json', child)], [exportDirectory()]);
  assert.deepEqual(warningRows(warningCard(directFirst.overview())),
    [['陈曦', 'TASTE_GENRES_UNMATCHED', '子运行清单冻结的警告']]);

  const exportOnly = await loadedViewer(t, [], [exportDirectory()]);
  const copy = warningCard(exportOnly.overview());
  assert.deepEqual(warningRows(copy), [['陈曦', 'TASTE_GENRES_UNMATCHED', '导出清单中的副本']]);
  assert.ok(copy.includes('worldmock-export/manifest.json · frozen_run_manifest'));

  const strangers = await loadedViewer(t, [parent, droppedFile('manifest.json', '{"run_id": "viewer-test-run"')]);
  assert.ok(warningCard(strangers.overview()).includes('随库提供的 2 个 manifest.json 都不是本库所属运行的运行清单'));
  assert.ok(strangers.errors.some(error => error.startsWith('Unreadable manifest file')));
  assert.ok(strangers.errors.every(error => !/[\u4e00-\u9fff]/.test(error)));
});

test('reloading another database drops the previous run manifest and its warnings', async t => {
  const view = await loadedViewer(t, [droppedFile('manifest.json',
    runManifest({input_warnings: [inputWarning('COVERAGE_BELOW_THRESHOLD', 1, '上一份库的警告')]}))]);
  assert.equal(warningRows(warningCard(view.overview())).length, 1);
  view.reload([event('observation', {})]);
  const card = warningCard(view.overview());
  assert.ok(card.includes('未读取本库所属运行的运行清单') && !card.includes('上一份库的警告'));
  assert.equal(view.state.runManifest, null);
});

/** @brief 给查看器的数据库装上记录器：此后经它准备的每条语句的 SQL 依次记入返回的数组。 */
function recordStatements(view) {
  const statements = [], prepare = view.state.db.prepare;
  view.state.db.prepare = sql => { statements.push(sql.replace(/\s+/g, ' ')); return prepare(sql); };
  return statements;
}

test('the index reads each event type once through the type table and never filters events by parsing every body', t => {
  const view = viewer([
    event('observation', {balance_fen: 100, locations: [{location_id: 'poi-1', name: '家'}]}),
    event('decision_started', {round_order: 0, wake_reasons: ['run_start']}),
    event('custom_kind', {anything: 1}, {occurred_at: epoch + 500}),
    {data: {note: 'an event without a type'}, occurred_at: epoch + 600},
    event('model_response', {response: {usage: {prompt_tokens: 7}}}),
  ]);
  t.after(() => view.close());
  const before = JSON.stringify(view.state.events);
  // 同一个连接上重建索引：临时表先删后建，结果与第一次相同。
  const statements = recordStatements(view);
  view.run('S.events=[]; S.summaries=new Map(); S.typeCounts={}; S.obs=[]; S.responses=new Map(); buildIndex()');
  assert.equal(JSON.stringify(view.state.events), before);
  // 没有专属字段的类型与没有类型的事件照样进入事件列表，时刻取自它自己的正文。
  assert.deepEqual(JSON.parse(JSON.stringify(view.state.events.map(row => [row.seq, row.type, row.t]))), [
    [1, 'observation', epoch], [2, 'decision_started', epoch + 60], [3, 'custom_kind', epoch + 500],
    [4, null, epoch + 600], [5, 'model_response', epoch + 240]]);
  assert.equal(view.summary(3), '');
  assert.equal(view.state.typeCounts.custom_kind, 1);
  // 只有建类型表的那一条语句对全部事件取类型；读事件正文的语句都从类型表按类型取行，每类一条。
  const typed = statements.filter(sql => sql.includes('events e'));
  assert.ok(typed.every(sql => sql.includes('FROM viewer_event v CROSS JOIN events e ON e.event_seq=v.event_seq WHERE v.type IS ?')));
  assert.equal(typed.length, 13 + 2, 'thirteen projected types, one unlisted type and the typeless events');
  assert.deepEqual(statements.filter(sql => sql.includes("json_extract(body,'$.event_type')")).map(sql => sql.slice(0, 34)),
    ['CREATE TEMP TABLE viewer_event AS ']);
  assert.ok(statements.every(sql => !sql.includes('json_each')), 'names come with the typed rows, not from extra scans');
  assert.equal(statements.filter(sql => /FROM events\b/.test(sql)).length, 2, 'the type table and the run identity');
  // 总览的模型用量取自索引，不再查库；记忆、思考记录与上下文索引各只读自己那一类事件。
  statements.length = 0;
  view.overview();
  assert.deepEqual(statements, []);
  view.memory();
  view.run("rsQuery('')");
  view.run('ContextView.index()');
  assert.equal(statements.length, 3);
  assert.ok(statements.every(sql => sql.includes('WHERE v.type IS ?') && !sql.includes("'$.event_type'")));
});

test('model usage on the overview is summed per person from the index with typeless responses first', t => {
  const usage = (fields, extra = {}) => event('model_response', {response: {usage: fields}}, extra);
  const view = viewer([
    usage({prompt_tokens: 100, completion_tokens: 10, total_tokens: 110}, {agent_id: 2}),
    usage({prompt_tokens: 5, completion_tokens: 1, total_tokens: 6}),
    usage({prompt_tokens: 7, total_tokens: 7}),
    usage({}, {agent_id: null}),
    event('model_response', {error: 'no usage at all'}, {agent_id: 3}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(JSON.parse(view.run('JSON.stringify(modelUsage())')), [
    {agent: null, n: 1, p: null, c: null, t: null}, {agent: 1, n: 2, p: 12, c: 1, t: 13},
    {agent: 2, n: 1, p: 100, c: 10, t: 110}, {agent: 3, n: 1, p: null, c: null, t: null}]);
  const cells = markup => [...markup.matchAll(/<tr><td>([^<]*)<\/td>((?:<td class="num">[^<]*<\/td>)+)<\/tr>/g)]
    .map(row => [row[1], ...[...row[2].matchAll(/>([^<]*)<\/td>/g)].map(cell => cell[1])]);
  assert.deepEqual(cells(view.overview()), [['系统', '1', '—', '—', '—'], ['陈曦', '2', '12', '1', '13'],
    ['李明', '1', '100', '10', '110'], ['agent 3', '1', '—', '—', '—']]);
  assert.ok(view.overview().includes('<span class="k">总 tokens</span><span class="v">123</span>'));
  view.get('#agent-filter').value = '2';
  assert.deepEqual(cells(view.overview()), [['李明', '1', '100', '10', '110']]);
});

test('names read with the typed rows keep the override, fill-in and earliest-name rules across observations', t => {
  const observed = fields => event('observation', fields);
  const view = viewer([
    // 同一次观察里一个商品有两种写法：取码点序最小的；数组里的非对象项与空数组不影响其余条目。
    observed({products: [{product_id: 'food-a', name: '第一种叫法'}, 'stray', null],
      locations: {home: {location_id: 'poi-home', name: '家'}},
      messages: [{message_id: 'mail-1', subject: '周报'}, {message_id: 'mail-2', subject: ' '}],
      errand_item_bindings: [{name: '乙名', product_id: 'food-x'}, {name: '甲名', product_id: 'food-x'},
        {name: '😀表情名', product_id: 'food-y'}, {name: '￮全角名', product_id: 'food-y'}]}),
    // 名称改了：覆盖来源取后来的写法；补缺来源仍取最早的。
    observed({products: [{product_id: 'food-a', name: '第二种叫法'}],
      errand_item_bindings: [{name: '啊更小的名', product_id: 'food-x'}, {name: '后来才有', product_id: 'food-z'}]}),
    // 与第一次观察逐字相同的数组再次出现：覆盖来源照样改回去。
    observed({products: [{product_id: 'food-a', name: '第一种叫法'}, 'stray', null]}),
    event('action_accepted', {status: 'completed', data: {tool: 'browse_activities', items: [
      {item_id: null, product_id: 'food-b', title: null, name: '按名称'}, {task_id: 'task-9', title: '按标题', name: '不用'},
      {activity_id: '', title: '空身份不记'}]}}),
  ]);
  t.after(() => view.close());
  const entries = map => JSON.parse(JSON.stringify([...map]));
  assert.deepEqual(entries(view.state.nameIdx.locations), [['poi-home', '家']]);
  assert.deepEqual(entries(view.state.nameIdx.targets), [['food-a', '第一种叫法'], ['mail-1', '邮件「周报」'],
    ['food-b', '按名称'], ['task-9', '按标题'], ['food-x', '乙名'], ['food-y', '￮全角名'], ['food-z', '后来才有']]);
  assert.deepEqual(view.errors, []);
});

test('one request answered twice is reported whether by two responses or by one that lists it twice', () => {
  const answered = (...causes) => event('model_response', {response: {choices: [{message: {content: 'x'}}]}},
    {caused_by: causes});
  for (const responses of [[answered('asked'), answered('asked')], [answered('asked', 'asked')]]) {
    const app = viewer([contextEvent('asked', contextRequest()), ...responses, contextEvent('quiet', contextRequest()),
      answered(null, 7, 'someone-else')]);
    try {
      assert.throws(() => app.run('ContextView.read(1)'), /Multiple model responses reference one request/);
      app.run('ContextView.select(1)');
      assert.ok(app.get('#ctx-body').innerHTML.includes('Context viewer failed: Multiple model responses reference one request'));
      assert.equal(app.run(`ContextView.read(${responses.length + 2}).response`), null);
      // 换一个文件后，上一份库的对应关系不留下。
      app.reload([contextEvent('asked', contextRequest())]);
      assert.equal(app.run('ContextView.read(1).response'), null);
      assert.equal(app.state.responses.size, 0);
    } finally { app.close(); }
  }
});

/** @brief 只提供页面读取的那几样的假文件：名称、大小与 slice().arrayBuffer()；broken 为真时读到的块少一个字节。 */
function chunkedFile(bytes, broken = false) {
  return {name: 'footprints.sqlite', size: bytes.length, slice: (start, end) => ({
    arrayBuffer: async () => Uint8Array.from(bytes.slice(start, broken ? end - 1 : end)).buffer})};
}

test('a database file is read in fixed chunks and reads are stitched across chunk borders up to the end of file', async t => {
  const view = viewer([event('observation', {})]);
  t.after(() => view.close());
  const engine = view.run('ViewerEngine');
  const bytes = Array.from({length: 10}, (_, index) => index + 1), progress = [];
  const store = await engine.readChunks(chunkedFile(bytes), (done, total) => progress.push([done, total]), 4);
  assert.deepEqual(Array.from(store.chunks, chunk => Array.from(chunk)), [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10]]);
  assert.deepEqual([store.size, store.chunkBytes, progress], [10, 4, [[4, 10], [8, 10], [10, 10]]]);
  /** @brief 从 offset 读 count 个字节：返回 [拷贝的字节数, 缓冲区内容]，没读到的位置保持 0。 */
  const read = (offset, count) => {
    const dest = new Uint8Array(count);
    return [engine.copyFromChunks(store, dest, offset), Array.from(dest)];
  };
  assert.deepEqual(read(0, 4), [4, [1, 2, 3, 4]]);
  assert.deepEqual(read(2, 7), [7, [3, 4, 5, 6, 7, 8, 9]]);
  assert.deepEqual(read(7, 3), [3, [8, 9, 10]]);
  assert.deepEqual(read(8, 5), [2, [9, 10, 0, 0, 0]], 'a read past the end stops at the end of the file');
  assert.deepEqual(read(10, 2), [0, [0, 0]]);
  assert.equal((await engine.readChunks(chunkedFile([]), () => {}, 4)).chunks.length, 0);
  await assert.rejects(engine.readChunks(chunkedFile(bytes, true), () => {}, 4), /File changed while it was being read/);
});

test('the engine database is used only through the statement interface and frees its file when closed', t => {
  const view = viewer([event('observation', {})]);
  t.after(() => view.close());
  const calls = [];
  const statement = {bind: values => calls.push(['bind', values]), step: () => { calls.push(['step']); return calls.length < 6; },
    get: target => { calls.push(['get', target]); return {seq: 1}; }, finalize: () => calls.push(['finalize'])};
  const wrapped = view.run('ViewerEngine').wrap(
    {prepare: sql => { calls.push(['prepare', sql]); return statement; }, close: () => calls.push(['close'])},
    () => calls.push(['release']));
  const previous = view.state.db;
  view.state.db = wrapped;
  try {
    // 没有绑定值时不调引擎的 bind（它不接受空数组）；每行取成以列名为键的对象；用完即释放语句。
    assert.deepEqual(JSON.parse(JSON.stringify(view.run("q('SELECT 1')"))), [{seq: 1}, {seq: 1}]);
    assert.deepEqual(calls.map(call => call[0]), ['prepare', 'step', 'get', 'step', 'get', 'step', 'finalize']);
    calls.length = 0;
    view.run("q('SELECT ?', [null])");
    assert.deepEqual(JSON.parse(JSON.stringify(calls.slice(0, 2))), [['prepare', 'SELECT ?'], ['bind', [null]]]);
    calls.length = 0;
    wrapped.close();
    assert.deepEqual(calls, [['close'], ['release']]);
  } finally { view.state.db = previous; }
});

test('loading a dropped database reports progress and explains a file that changed while it was read', async t => {
  const view = viewer([event('observation', {})]);
  t.after(() => view.close());
  const engine = view.run('ViewerEngine');
  const opened = [];
  engine.load = async () => ({name: 'stub engine'});
  engine.open = (sqlite3, store) => { opened.push([sqlite3.name, store.size]); return database([event('run_ended', {})]); };
  view.get('#btn-reload').onclick();
  await view.run('handleFiles')([chunkedFile([1, 2, 3, 4, 5, 6, 7, 8])], null);
  assert.deepEqual(opened, [['stub engine', 8]]);
  assert.equal(view.state.events.length, 1);
  const log = view.get('#drop-log').textContent;
  assert.ok(log.includes('· 读取 footprints.sqlite（0.0 / 0.0 MB）…\n· 打开数据库并建立索引'), log);
  assert.equal(log.match(/读取 footprints\.sqlite/g).length, 1, 'progress rewrites one line instead of adding lines');
  assert.equal(view.get('#drop-err').style.display, 'none');
  view.get('#btn-reload').onclick();
  await view.run('handleFiles')([chunkedFile([1, 2, 3, 4], true)], null);
  assert.equal(opened.length, 1, 'a truncated read never reaches the engine');
  assert.equal(view.get('#drop-err').style.display, 'block');
  assert.match(view.get('#drop-err').textContent, /^加载失败：读取 footprints\.sqlite 失败（File changed while it was being read）。.*请重试，或先复制一份再拖入。$/);
  assert.deepEqual(view.errors, ['Reading the database file failed: Error: File changed while it was being read']);
  view.state.db = database([event('observation', {})]);
});
