'use strict';

const assert = require('node:assert/strict');
const {createHash} = require('node:crypto');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const {CASES, ROOT, RUN_ID, epoch, viewer, event, droppedFile, fileEntry, directoryEntry, frozenCatalog, withCatalog,
  runManifest, runDirectory, assetFiles, tableRows, targets} = require('./run_viewer_harness.cjs');

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

test('context reads current seven-layer actions and complete other requests', async () => {
  const app = await viewer([
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

test('a decision first request with extra messages is a continuation matched to its source decision', async () => {
  const continued = contextRequest('continuation');
  continued.messages[2].reasoning_content = '上一决策的思考。';
  const response = {choices: [{message: {content: '', reasoning_content: '上一决策的思考。',
    tool_calls: continued.messages[2].tool_calls}}],
  usage: {prompt_tokens: 900, prompt_cache_hit_tokens: 640, prompt_cache_miss_tokens: 260, completion_tokens: 80,
    completion_tokens_details: {reasoning_tokens: 55}}};
  const app = await viewer([
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

test('context response matching follows caused_by across interleaved agents and reused call identifiers', async () => {
  const request = contextRequest();
  const app = await viewer([
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

test('context comparison uses same agent and stage, detects same-length edits and never crosses runs', async () => {
  const original = contextRequest();
  const changed = contextRequest();
  assert.ok(changed.messages[1].content.includes('小雨'));
  changed.messages[1].content = changed.messages[1].content.replace('小雨', '晴天');
  assert.equal(changed.messages[1].content.length, original.messages[1].content.length);
  const app = await viewer([
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
    await app.reload([contextEvent('new-run', otherRequest(), 2)]);
    assert.equal(app.run('ContextView.index().length'), 1);
    assert.equal(app.run('ContextView.read(1).before'), null);
    assert.ok(!app.context().includes('事件 4'));
  } finally { app.close(); }
});

test('context preserves unparsed inputs and escapes recorded HTML including failed responses', async () => {
  const request = {messages:[{role:'user',content:'<img src=x onerror=alert(1)>😀\n\nwhole input'}]};
  const app = await viewer([
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

test('context navigation, stage filter and all rule paths are available through the actual view', async () => {
  const app = await viewer([contextEvent('action', contextRequest())]);
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

test('context drills into current tools and real-shaped progress self notes', async () => {
  const request = contextRequest();
  const values = JSON.parse(request.messages[1].content);
  values[5].events = [{occurred_at:'2026-01-05T08:00:00+08:00', type:'tool_call',
    tool:'start_side_activity', result:{status:'completed'}, self_note:'先浏览。'}];
  request.messages[1].content = JSON.stringify(values);
  const app = await viewer([contextEvent('current', request)]);
  try {
    app.context();
    app.get('button:message:1:tool').onclick();
    const options = app.get('#ctx-item').innerHTML;
    // 目标类别保持原文，动作名换成中文：用内容 app 是「使用」，在地点做事是「做」。
    assert.ok(options.includes('美团（app）'));
    assert.ok(options.includes('美团 · 使用 · 可用'));
    // 录制的工具层里出行只有 travel 一个动作，同一目的地的各行靠出行方式的名称区分；四个旧出行动作不再出现。
    for (const method of ['步行', '公共交通', '驾车', '打车']) {
      assert.ok(options.includes(`公司 · 出行 · ${method} · 可用`), method);
    }
    assert.ok(options.includes('家 · 做 · 测试休息 · 可用'));
    assert.ok(options.includes('家 · 睡觉 · 可用'));
    assert.doesNotMatch(options, / · (walk|drive|take_bus|take_taxi|interact|travel|sleep|purchase) · /);
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

test('context drills into an inline current tool layer and lists travel methods and location units by detail', async () => {
  const app = await viewer([contextEvent('current', inlineContextRequest())]);
  try {
    assert.deepEqual(Array.from(app.run('ContextView.index()'), item => item.stage), ['action']);
    app.context();
    app.get('button:message:1:tool').onclick();
    const select = app.get('#ctx-item');
    const labels = Array.from(select.innerHTML.matchAll(/<option[^>]*>([^<]*)<\/option>/g), match => match[1]);
    assert.deepEqual(labels, ['整层原文',
      '公司（location）', '公司 · 出行 · 步行 · 可用',
      '公司（location）', '公司 · 出行 · 公共交通 · 可用',
      '家（location）', '家 · 做 · 测试休息 · 可用',
      '家（location）', '家 · 睡觉 · 可用']);
    // 选中「公司 · 出行 · 公共交通」一项：正文是这一行的完整字段，动作在前，保持原始 JSON。
    select.value = '3';
    select.onchange();
    assert.deepEqual(JSON.parse(app.get('#ctx-raw').textContent), {action: 'travel', state: '全天营业', available: true,
      name: '公司', detail: '公共交通', price: '2 元', duration: '约 8 分钟', note: '轻松', kind: 'location'});
    assert.deepEqual(app.errors, []);
  } finally { app.close(); }
});

test('context index respects the actual first message role and tools field type', async () => {
  const wrongRole = contextRequest();
  wrongRole.messages[0].role = 'user';
  const nullTools = contextRequest();
  nullTools.tools = null;
  const app = await viewer([contextEvent('wrong-role', wrongRole),contextEvent('null-tools',nullTools)]);
  try {
    assert.deepEqual(Array.from(app.run('ContextView.index()'), item => item.stage), ['other','other']);
    assert.equal(app.run('ContextView.read(2).current.stage'), 'other');
  } finally { app.close(); }
});

test('context comparison keeps the slicing note when only the previous input was sliced into layers', async () => {
  const current = contextRequest();
  current.messages[1].content = current.messages[1].content.replace('"layer":"记忆层"', '"layer":"记忆"');
  const app = await viewer([contextEvent('before',contextRequest()),contextEvent('after',current)]);
  try {
    app.run('ContextView.select(2)');
    app.get('#ctx-view').onchange({target:{value:'comparison'}});
    const html = app.get('#ctx-body').innerHTML;
    assert.ok(html.includes('退出当前输入'));
    assert.ok(html.includes('按顶层数组无损切片'));
    assert.ok(html.includes('未解析'));
  } finally { app.close(); }
});

test('action request summaries show self_note as unverified only when the reply had text', async t => {
  const view = await viewer([
    event('action_requested', {tool_name: 'start_side_activity', request: {action: 'interact', target: ['美团']},
      request_sha256: 'f'.repeat(64), reused: false, self_note: '先歇一会儿，再去买菜。'}),
    event('action_requested', {tool_name: 'start_main_activity', request: {action: 'sleep', target: '450', detail: null},
      request_sha256: 'e'.repeat(64), reused: false, self_note: null}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '请求 开始槽位 2 的活动 · 使用 · 美团 · self_note（未经核验）：先歇一会儿，再去买菜。');
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

/** @brief 构造 start_main_activity 请求：action、target、detail 三个键都出现，睡眠的 detail 为 null。 */
function mainRequest(action, target, detail = null) {
  return event('action_requested', {tool_name: 'start_main_activity', request: {action, target, detail}});
}

/** @brief 构造 start_side_activity 请求：target 恒为名称数组，购买一份写一项。 */
function sideRequest(action, ...target) {
  return event('action_requested', {tool_name: 'start_side_activity', request: {action, target}});
}

/** @brief 只换掉用例文件目录里某几组行的目录（其余组照用例文件）。 */
function catalogWith(tables) {
  return {...CASES.catalog, ...tables};
}

test('locations accumulate across people and replace fixed home office market labels', async t => {
  const locations = [
    {location_id: 'poi-3538660453', name: '菜饭骨头汤'},
    {location_id: 'home', name: '张江公寓'},
    {location_id: 'office', name: '人工智能研发中心'},
    {location_id: 'market', name: '浦东便民市场'},
  ];
  // 两个人物去过的地点都按同一份冻结目录取名；最后一处是第二个人物去的。
  const events = locations.flatMap((place, index) => {
    const person = {agent_id: index === locations.length - 1 ? 2 : 1};
    return [{...mainRequest('travel', place.name, '步行'), ...person},
      activity('action_accepted', `go-${place.location_id}`, {kind: 'travel', destination_id: place.location_id},
        person),
      activity('activity_progress', `go-${place.location_id}`, {kind: 'travel', destination_id: place.location_id},
        person)];
  });
  const view = await viewer(events, {catalog: catalogWith({locations})});
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), locations.map(place => `→ ${place.name}`));
  locations.forEach((place, index) => {
    for (const offset of [1, 2, 3]) assert.ok(view.summary(index * 3 + offset).includes(place.name));
    assert.equal(view.summary(index * 3 + 1), `请求 开始槽位 1 的活动 · ${place.name} · 出行 · 步行`);
  });
  assert.deepEqual(view.errors, []);
});

test('graph travel shows recorded origin destination and every path place', async t => {
  const names = ['张江公寓', '社区便利店', '人工智能研发中心'];
  const ids = ['home', 'shop', 'office'];
  const data = {trip_id:'home->office:walking', origin_id:'home', destination_id:'office',
    path:ids, distance_meters:3274};
  const view = await viewer([
    activity('action_accepted', 'commute', {kind:'travel', location_id:'home', destination_id:'office',
      travel_method_id:'walking', data}),
    activity('activity_progress', 'commute', {kind:'travel', location_id:'home', destination_id:'office',
      travel_method_id:'walking', data}),
  ], {catalog: catalogWith({locations: ids.map((location_id, index) => ({location_id, name: names[index]}))})});
  t.after(() => view.close());
  // 自带的步行不在目录里，名称固定；出行行在路线前写出行方式名称
  const route = '步行 · 张江公寓 → 人工智能研发中心 · 路径：张江公寓 → 社区便利店 → 人工智能研发中心';
  assert.deepEqual(targets(view.activities()), [route]);
  for (const sequence of [1, 2]) {
    assert.ok(view.summary(sequence).includes(route));
    assert.doesNotMatch(view.summary(sequence), /home|shop|office|3274|walking/);
  }
  assert.deepEqual(view.errors, []);
});

test('travel shows the dataset name of its travel method from the recorded catalog bindings', async t => {
  const trip = travel_method_id => ({kind: 'travel', destination_id: 'office', travel_method_id,
    data: {action: 'travel'}});
  const view = await viewer([
    activity('action_accepted', 'by-bus', trip('bus')),
    activity('action_accepted', 'by-ferry', trip('ferry-line-2')),
    activity('action_accepted', 'on-foot', trip('walking')),
    activity('action_accepted', 'unrecorded', trip('hovercraft')),
  ], {catalog: withCatalog({travel_methods: [{travel_method_id: 'ferry-line-2', name: '二号线轮渡'}]})});
  t.after(() => view.close());
  // 名称取本次运行冻结的出行方式目录；没有登记的身份显式报告缺失，不猜测也不显示引擎自带的旧说法
  assert.deepEqual(targets(view.activities()), [
    '公共交通 · → 公司', '二号线轮渡 · → 公司', '步行 · → 公司', '名称缺失（hovercraft） · → 公司']);
  assert.equal(view.summary(1), '已受理 · 出行 · 公共交通 · → 公司 · 计划 10分');
  assert.doesNotMatch(view.activities(), /乘公交车|乘出租车|ferry-line-2|"bus"/);
  assert.deepEqual(view.errors, ['Missing recorded object name: hovercraft']);
});

test('app pushes preserve registered names and complete source facts', async t => {
  const view = await viewer([event('environment_event', {kind:'app_push', push_id:'push-one',
    app_id:'app-douyin', app:'抖音', content_id:'video-one', title:'城市生活短视频',
    summary:'下班路上的城市见闻。', issued_at:epoch}, {visibility:'agent'})]);
  t.after(() => view.close());
  assert.ok(view.summary(1).includes('抖音 · 《城市生活短视频》'));
  assert.ok(view.summary(1).includes('下班路上的城市见闻。'));
  assert.doesNotMatch(view.summary(1), /app-douyin|video-one|push-one|undefined/);
  assert.deepEqual(view.errors, []);
});

test('goods receipt and alarm events use the current environment labels', async t => {
  const view = await viewer([
    event('environment_event', {kind: 'goods_received', order_id: 'order-one', goods_id: 'goods-milk',
      category: 'drink', quantity: 1, provided: 100, applied: 100}),
    event('environment_event', {kind: 'callback_reminder', rule_id: 'state-reminder'}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '世界事件 · 商品收货');
  assert.equal(view.summary(2), '世界事件 · 状态提醒');
  assert.deepEqual(view.errors, []);
});

test('phone records written by the engine use their own environment labels', async t => {
  const view = await viewer([
    event('environment_event', {kind: 'app_foreground', app_id: 'amap', step: 'navigation',
      interval_start: epoch, interval_end: epoch + 660}, {visibility: 'system'}),
    event('environment_event', {kind: 'order_notification', app_id: 'luckin', order_ids: ['order-one'],
      title: '订单已发货', summary: '冰美式已发货'}, {visibility: 'system'}),
    event('environment_event', {kind: 'location_entered', session_id: 'session-1-0003', location_id: 'office',
      entry_kind: 'arrival'}, {visibility: 'system'}),
    event('environment_event', {kind: 'location_left', session_id: 'session-1-0001', location_id: 'home'},
      {visibility: 'system'}),
  ], {catalog: withCatalog({apps: [{app_id: 'luckin', name: '瑞幸咖啡'}]})});
  t.after(() => view.close());
  assert.equal(view.summary(1), '世界事件 · app 前台 · 高德地图 · 导航');
  assert.equal(view.summary(2), '世界事件 · 订单通知 · 瑞幸咖啡 · 订单已发货');
  assert.equal(view.summary(3), '世界事件 · 进入地点');
  assert.equal(view.summary(4), '世界事件 · 离开地点');
  assert.deepEqual(view.errors, []);
});

test('each activity shows its actual current episode title without mixing another activity', async t => {
  const episodes = withCatalog({apps: [{app_id: 'app-bili', name: '哔哩哔哩'}], contents: [
    {content_id: 'episode-one', title: '春日剧集第一集', series: {episode: 1, title: '春日剧集'}},
    {content_id: 'episode-two', title: '春日剧集第二集', series: {episode: 2, title: '春日剧集'}},
    {content_id: 'walk-talk', title: '城市散步'}]});
  const watch = content_id => ({kind: 'interact', location_id: 'home', content_title: '旧快照里的标题',
    data: {action: 'interact', app_id: 'app-bili', content_id}});
  const view = await viewer([
    activity('action_accepted', 'watch-first', watch('episode-one')),
    activity('activity_progress', 'watch-first', watch('episode-two')),
    activity('activity_progress', 'watch-first', watch('episode-two')),
    activity('action_accepted', 'watch-second', {kind: 'interact', location_id: 'home', content_title: '旧快照里的标题',
      data: {action: 'interact', app_id: null, content_id: 'walk-talk'}}, {agent_id: 2}),
    activity('activity_progress', 'watch-first', watch('episode-two')),
  ], {catalog: episodes});
  t.after(() => view.close());
  // 活动表按活动列全各条事件指向的内容；app 里的系列只列各集标题，不写系列名；在地点做的事写「在<地点>：<单元名>」。
  assert.deepEqual(targets(view.activities()), ['使用哔哩哔哩《春日剧集第一集》《春日剧集第二集》', '在家：城市散步']);
  assert.equal(view.summary(1), '已受理 · 使用哔哩哔哩《春日剧集第一集》 · 计划 10分');
  assert.equal(view.summary(2), '活动段 · 使用哔哩哔哩《春日剧集第二集》 · 进行中 · 已进行 1分');
  assert.equal(view.summary(4), '已受理 · 在家：城市散步 · 计划 10分');
  // 标题只按内容身份取冻结目录，快照随带的 content_title 不读。
  assert.doesNotMatch(view.activities() + view.state.events.map(entry => entry.sum).join(''), /旧快照里的标题/);
  assert.deepEqual(view.errors, []);
});

test('progress-only activity titles name both activity rows and request summaries', async t => {
  const title = '城市生活随笔';
  const view = await viewer([
    mainRequest('interact', '家', title),
    activity('activity_progress', 'only-progress', {kind: 'interact', location_id: 'home',
      data: {action: 'interact', app_id: null, content_id: 'article'}}),
  ], {catalog: withCatalog({contents: [{content_id: 'article', title}]})});
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), [`在家：${title}`]);
  assert.equal(view.summary(1), `请求 开始槽位 1 的活动 · 家 · 做 · ${title}`);
  assert.equal(view.summary(2), `活动段 · 在家：${title} · 进行中 · 已进行 1分`);
  assert.deepEqual(view.errors, []);
});

test('the two start tools display their own arguments: place, action and detail, or action and the named objects', async t => {
  const view = await viewer([
    sideRequest('purchase', '菜饭', '菜饭', '柠檬茶'),
    sideRequest('interact', '美团'),
    mainRequest('travel', '张江公寓', '驾车'),
    mainRequest('interact', '张江公寓', '测试休息'),
    mainRequest('sleep', '450'),
  ]);
  t.after(() => view.close());
  // 副工具：动作在前，名称数组照所写顺序以顿号相连，同名写两次就是两份；用内容 app 的动作写「使用」。
  assert.equal(view.summary(1), '请求 开始槽位 2 的活动 · 购买 · 菜饭、菜饭、柠檬茶');
  assert.equal(view.summary(2), '请求 开始槽位 2 的活动 · 使用 · 美团');
  // 主工具：地点名、动作、detail（出行方式名称或内容单元名）；在地点做事写「做」；睡眠只写分钟数。
  assert.equal(view.summary(3), '请求 开始槽位 1 的活动 · 张江公寓 · 出行 · 驾车');
  assert.equal(view.summary(4), '请求 开始槽位 1 的活动 · 张江公寓 · 做 · 测试休息');
  assert.equal(view.summary(5), '请求 开始槽位 1 的活动 · 睡觉 · 450 分钟');
  for (const sequence of [1, 2, 3, 4, 5]) {
    assert.doesNotMatch(view.summary(sequence),
      /food-rice|purchase|interact|travel|sleep|start_main_activity|start_side_activity|null|undefined|进行/);
  }
  assert.deepEqual(view.errors, []);
});

test('readableReq reads the two start tools directly and keeps no branch for the deleted start_activity', async t => {
  const view = await viewer([
    event('action_requested', {tool_name: 'start_activity',
      request: {items: [{action: 'walk', target: '公司'}, {action: 'interact', target: '美团'}]}}),
  ]);
  t.after(() => view.close());
  const readable = (tool, request) => view.run(`readableReq(${JSON.stringify(tool)}, ${JSON.stringify(request)})`);
  assert.equal(view.run('TOOL_LABELS.start_main_activity'), '开始槽位 1 的活动');
  assert.equal(view.run('TOOL_LABELS.start_side_activity'), '开始槽位 2 的活动');
  for (const [detail, text] of [['步行', '公司 · 出行 · 步行'], ['驾车', '公司 · 出行 · 驾车'],
    ['打车', '公司 · 出行 · 打车'], ['公共交通', '公司 · 出行 · 公共交通']]) {
    assert.equal(readable('start_main_activity', {action: 'travel', target: '公司', detail}), text);
  }
  // 动作名取「上下文」画面的同一张中文表：槽位 1 的开始工具按地点取词写「做」，槽位 2 的按 app 取词写「使用」。
  assert.equal(readable('start_main_activity', {action: 'interact', target: '家', detail: '测试闲聊'}),
    '家 · 做 · 测试闲聊');
  assert.equal(readable('start_main_activity', {action: 'sleep', target: '10', detail: null}), '睡觉 · 10 分钟');
  // 请求在事件里既可能是对象也可能是 JSON 文本（时间线索引取出的原文），两种读法结果相同。
  assert.equal(readable('start_main_activity', JSON.stringify({action: 'sleep', target: '720', detail: null})),
    '睡觉 · 720 分钟');
  assert.equal(readable('start_side_activity', {action: 'purchase', target: ['牛肉面']}), '购买 · 牛肉面');
  assert.equal(readable('start_side_activity', {action: 'purchase', target: ['牛肉面', '薯片', '薯片']}),
    '购买 · 牛肉面、薯片、薯片');
  assert.equal(readable('start_side_activity', {action: 'interact', target: ['哔哩哔哩']}), '使用 · 哔哩哔哩');
  // 已删除的工具没有标签也没有读取分支：工具名原样显示，请求内容不被解读成两个开始工具的任何一种。
  assert.equal(view.run('TOOL_LABELS.start_activity'), undefined);
  assert.equal(readable('start_activity', {items: [{action: 'walk', target: '公司'}]}), '');
  assert.equal(view.summary(1), '请求 start_activity · ');
  assert.deepEqual(view.errors, []);
});

test('plan_day is no longer a person tool while day plan revisions keep their label', async t => {
  const view = await viewer([
    event('action_requested', {tool_name: 'plan_day', request: {goals: [{goal: '上班'}]}}),
    event('action_requested', {tool_name: 'revise_day_plan', request: {goals: [{goal: '上班'}, {goal: '买菜'}]}}),
  ]);
  t.after(() => view.close());
  assert.equal(view.run("TOOL_LABELS.plan_day"), undefined);
  assert.equal(view.summary(1), '请求 plan_day · ');
  assert.doesNotMatch(view.summary(1), /当日目标|项目标/);
  assert.equal(view.summary(2), '请求 调整当日目标 · 2 项目标');
});

test('registered app names describe platform browse while content titles remain authoritative', async t => {
  const view = await viewer([
    activity('action_accepted','browse',{kind:'interact',location_id:'home',
      data:{action:'interact',app_id:'bilibili',content_id:null}}),
    activity('action_accepted','content',{kind:'interact',location_id:'home',content_title:'城市生活随笔',
      data:{action:'interact',app_id:'bilibili',content_id:'c-article'}}),
  ]);
  t.after(() => view.close());
  // 过渡写法（收尾时随没有内容的 app 使用一起删去）：带 app 而内容身份为空写「使用<app 名>」，不带书名号。
  // 标题只按内容身份取冻结目录，快照随带的 content_title 不读。
  assert.deepEqual(targets(view.activities()), ['使用哔哩哔哩', '使用哔哩哔哩《测试文章》']);
  assert.equal(view.summary(1), '已受理 · 使用哔哩哔哩 · 计划 10分');
  assert.doesNotMatch(view.activities(), /城市生活随笔/);
  assert.deepEqual(view.errors, []);
});

test('session requests display each task with its own progress sentence', async t => {
  const memory = text => ({type: 'routine', text});
  const opinion = (about, text) => ({type: 'relationship', about, text});
  const item = (activity_name, summary, extra = {}) => ({activity_name, summary, ...extra});
  const view = await viewer([
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

test('activity end reasons name reminders route and partner endings pauses and cancellations', async t => {
  const reasons = [
    ['route_ended', '随出行结束'], ['feedback', '反馈中断'],
    ['paused', '已暂停'], ['cancelled', '主动结束'], ['planned_end', '按计划结束'],
  ];
  const rest = {kind: 'interact', location_id: 'home', data: {action: 'interact', app_id: null, content_id: 'c-rest'}};
  const view = await viewer([
    ...reasons.map(([boundary_type], index) => activity('activity_progress', `ended-${index}`, {
      ...rest, status: 'completed', boundary_type,
    })),
    activity('activity_progress', 'truncated', {...rest, status: 'truncated', boundary_type: 'experiment_end'}),
  ]);
  t.after(() => view.close());
  reasons.forEach(([, label], index) => assert.ok(view.summary(index + 1).endsWith(` · ${label}`)));
  assert.ok(view.summary(reasons.length + 1).endsWith(' · experiment_end'));
  for (const item of view.state.events) assert.doesNotMatch(item.sum, /undefined/);
});

test('environment events name midnight plans and activity lifecycle records', async t => {
  const remindAt = Date.parse('2026-01-05T08:00:00+08:00') / 1000;
  const lifecycle = [
    ['activity_concluded', '活动收尾'], ['activity_paused', '活动暂停'],
    ['activity_resumed', '活动恢复'], ['activity_truncated', '活动截断'],
  ];
  const view = await viewer([
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

test('rejections show the same-moment round instead of consecutive rejections', async t => {
  const view = await viewer([
    event('action_rejected', {error_code: 'ACTION_REQUEST_INVALID', same_moment_round: 2,
      max_same_moment_decisions: 3, next_decision_at: epoch, reason: '所选的对象不在本轮列出的范围内'}),
    event('action_rejected', {error_code: 'ACTIVITY_DURATION_INVALID', same_moment_round: 1}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '被拒绝 · ACTION_REQUEST_INVALID · 同刻第 2 轮（上限 3）');
  assert.equal(view.summary(2), '被拒绝 · ACTIVITY_DURATION_INVALID · 同刻第 1 轮');
  for (const sequence of [1, 2]) assert.doesNotMatch(view.summary(sequence), /连续拒绝/);
});

/** @brief 事务商品纯牛奶登记在冻结的商品目录里（在用例文件的目录行之外）。 */
const MILK_CATALOG = withCatalog({goods: [{goods_id: 'food-milk', name: '纯牛奶'}]});

test('decision starts show their wake reasons from the frozen vocabulary', async t => {
  const names = ['planned_end','felt_band_change','delivery_arrival','reconsideration_interval',
    'rejection_feedback','session_open','progress_report_due','no_active_activity','reminder_due','duty_boundary','app_push'];
  const due = epoch + 3600;
  const view = await viewer([
    event('decision_started', {round_order: 0, wake_reasons: ['session_open']}),
    event('decision_started', {round_order: 0, wake_reasons: [
      'no_active_activity', 'reminder_due', 'progress_report_due']}),
    event('decision_started', {round_order: 1, wake_reasons: []}),
    event('decision_started', {round_order: 0}),
    // 词表含 errand_reminder_due：同一人物同一时刻先交付一条事务提醒，该原因才有事务名称可写。
    errandEvent('errand_reminded', errandSnapshot({reminded_at: due}), {}, {occurred_at: due}),
    event('decision_started', {round_order: 0, wake_reasons: names}, {occurred_at: due}),
  ], {catalog: MILK_CATALOG});
  t.after(() => view.close());
  const labelsByName = JSON.parse(view.run('JSON.stringify(WAKE_LABELS)'));
  assert.deepEqual(Object.keys(labelsByName).sort(), [...names].sort());
  assert.equal(labelsByName.app_push, 'app 推送');
  assert.equal(view.summary(1), '决策开始 · 第 0 轮 · 唤醒：会话开始');
  assert.equal(view.summary(2), '决策开始 · 第 0 轮 · 唤醒：没有进行中的活动、提醒到点、该播报进展');
  assert.equal(view.summary(3), '决策开始 · 第 1 轮');
  assert.equal(view.summary(4), '决策开始 · 第 0 轮');
  const labels = view.summary(6).split(' · 唤醒：')[1].split('、');
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

test('control request summaries write each errand change kind and name after the activity summaries', async t => {
  const item = (activity_name, summary, extra = {}) => ({activity_name, summary, ...extra});
  const added = errandChange('新增', '买牛奶', {item: '纯牛奶', quantity: 2,
    memo: '下班顺路去便利店', remind_time: '18:30'});
  const updated = errandChange('更新', '取快递', {memo: '改到明早去驿站', remind_time: '08:00'});
  const cancelled = errandChange('取消', '订<蛋糕>', {reason: '聚会改期了'});
  const view = await viewer([
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

test('errand changes are read by the same rule as control_errands: seven keys present, values as written', async t => {
  const view = await viewer([]);
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

test('a control request that is not in the current shape fails the timeline index instead of being summarised', async () => {
  const item = (activity_name, summary, extra = {}) => ({activity_name, summary, ...extra});
  const requested = (tool_name, request) => [event('action_requested', {tool_name, request})];
  const closing = [item('午饭', '吃完了', {memories: [], views: []})];
  // 页面加载数据库走的就是加载入口这一步（handleFiles 把它的报错显示为加载失败）：缺 errands 的旧形状记录不留读取分支。
  for (const [tool, items] of [['report_status', [item('整理访谈', '完成提纲')]],
    ['pause_activity', [item('准备周会', '先放一放')]], ['conclude_activity', closing]]) {
    await assert.rejects(viewer(requested(tool, {items})), /Activity control request requires errands/, tool);
    await assert.rejects(viewer(requested(tool, {items, errands: null})), /requires errands/, tool);
    await assert.rejects(viewer(requested(tool, {items, errands: {}})), /requires errands/, tool);
    await assert.rejects(viewer(requested(tool, {errands: []})), /requires items/, tool);
    // 条目级的出入与缺键同等对待：一条变更不是对象或缺七键之一，整库不加载。
    await assert.rejects(viewer(requested(tool, {items, errands: [null]})), /errand change is not an object/, tool);
    await assert.rejects(viewer(requested(tool, {items, errands: [{change: '新增', name: '买牛奶'}]})),
      /errand change lacks item/, tool);
    const view = await viewer(requested(tool, {items, errands: [errandChange('取消', '买牛奶', {reason: '不买了'})]}));
    assert.ok(view.summary(1).endsWith(' · 事务：取消「买牛奶」'), view.summary(1));
    view.close();
  }
  // 其他工具的请求不带 errands，照常显示。
  const other = await viewer([sideRequest('interact', '美团')]);
  assert.equal(other.summary(1), '请求 开始槽位 2 的活动 · 使用 · 美团');
  other.close();
});

test('errand events show their Chinese wording, the errand name and the recorded product, never the errand identity', async t => {
  const due = epoch + 3600;
  const kinds = [
    ['errand_registered', '事务记下', {}, {}],
    ['errand_updated', '事务更新', {memo: '改到晚饭后去', remind_at: due + 1800}, {}],
    ['errand_progressed', '事务进展', {progress: 1}, {quantity_added: 1}],
    ['errand_reminded', '事务提醒', {progress: 1, reminded_at: due}, {}],
    ['errand_completed', '事务办成', {progress: 2, reminded_at: due}, {quantity_added: 1}],
    ['errand_cancelled', '事务取消', {}, {reason: '聚会改期了'}],
  ];
  const view = await viewer([
    activity('action_accepted', 'act-wait', {kind: 'interact', location_id: 'home',
      data: {action: 'interact', app_id: null, content_id: 'c-rest'}}),
    ...kinds.map(([kind, , fields, extra]) => errandEvent(kind, errandSnapshot(fields), extra)),
  ], {catalog: MILK_CATALOG});
  t.after(() => view.close());
  kinds.forEach(([kind, label], index) => {
    assert.equal(view.summary(index + 2), `世界事件 · ${label} · 「买牛奶」 · 商品 纯牛奶`, kind);
    assert.equal(JSON.parse(view.run(`fetchBody(${index + 2})`)).data.errand.errand_id, 'action-0007:errand:0',
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

test('an errand product without a recorded name is reported as missing and an errand is never named by its identity', async t => {
  const name = '<img src=x onerror=alert(1)>';
  const view = await viewer([
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
  ], {catalog: MILK_CATALOG});
  t.after(() => view.close());
  assert.equal(view.summary(1), '世界事件 · 事务记下 · 「买早餐」 · 商品 名称缺失（food-unlisted）');
  assert.equal(view.summary(2), '世界事件 · 事务办成 · 「买牛奶」 · 商品 纯牛奶');
  assert.equal(view.summary(3), '世界事件 · 事务记下 · 名称缺失（个人事务） · 商品 纯牛奶');
  assert.equal(view.summary(4), `世界事件 · 事务记下 · 「${name}」 · 商品 纯牛奶`);
  assert.equal(view.summary(5), '世界事件 · 事务更新 · 名称缺失（个人事务） · 商品 纯牛奶');
  assert.equal(view.summary(6), '世界事件 · 事务取消 · 名称缺失（个人事务） · 商品 纯牛奶');
  assert.equal(view.summary(7), '世界事件 · 事务记下 · 名称缺失（个人事务） · 商品 纯牛奶');
  assert.equal(view.summary(8), '世界事件 · 事务记下 · 名称缺失（个人事务） · 商品 纯牛奶');
  const row = view.run('tlRow(S.events[3]).innerHTML');
  assert.ok(row.includes('「&lt;img src=x onerror=alert(1)&gt;」') && !row.includes('<img'));
  assert.deepEqual(view.errors.filter(error => error.includes('food-unlisted')),
    ['Missing recorded object name: food-unlisted']);
  assert.deepEqual(view.errors.filter(error => error.includes('errand name')),
    [3, 5, 6, 7, 8].map(sequence => `Missing recorded errand name at event ${sequence}`));
  assert.equal(view.errors.length, 6, view.errors.join('\n'));
  assert.ok(view.errors.every(error => !/[一-鿿]/.test(error) && !ERRAND_IDENTITY.test(error)));
  view.state.events.forEach((entry, index) => {
    assert.doesNotMatch(entry.sum, ERRAND_IDENTITY);
    assert.doesNotMatch(view.run(`tlRow(S.events[${index}]).innerHTML`), ERRAND_IDENTITY);
  });
  // 事务内部身份只用于判定名称是否有效，不留在页面的索引里。
  assert.doesNotMatch(JSON.stringify([...view.state.summaries.values()]), ERRAND_IDENTITY);
});

test('current reminder wakes list delivered errands and preserve plan-only reminders', async t => {
  const due = epoch + 60;
  const view = await viewer([
    errandEvent('errand_reminded',errandSnapshot(),{},{occurred_at:due}),
    event('decision_started',{round_order:0,wake_reasons:['reminder_due']},{occurred_at:due}),
    event('decision_started',{round_order:0,wake_reasons:['reminder_due']},{occurred_at:due+60}),
  ], {catalog: MILK_CATALOG});
  t.after(() => view.close());
  assert.equal(view.summary(2),'决策开始 · 第 0 轮 · 唤醒：提醒到点「买牛奶」');
  assert.equal(view.summary(3),'决策开始 · 第 0 轮 · 唤醒：提醒到点');
  assert.deepEqual(view.errors, []);
});

test('midnight plan model requests are named by their recorded purpose', async t => {
  const request = {model: 'deepseek-flash', messages: []};
  const view = await viewer([
    event('model_request', {phase: 'request', purpose: 'plan_day', attempt: 2, request}),
    event('model_request', {phase: 'request', attempt: 1, request}),
  ]);
  t.after(() => view.close());
  assert.equal(view.summary(1), '零点计划模型请求 deepseek-flash · 第2次尝试');
  assert.equal(view.summary(2), '模型请求 deepseek-flash · 第1次尝试');
});

test('activities page lists location sessions from the final world state', async t => {
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
  const view = await viewer([activity('action_accepted', 'act-sleep', {kind: 'sleep'}),
    activity('action_accepted', 'act-wait-2', {kind: 'interact'}, {agent_id: 2})], {world});
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

test('runs without location sessions render no session table', async t => {
  const view = await viewer([activity('action_accepted', 'act-sleep', {kind: 'sleep'})]);
  t.after(() => view.close());
  assert.ok(!view.activities().includes('地点会话'));
});

test('unknown app and location names are explicit and logged in English', async t => {
  const view = await viewer([
    activity('action_accepted','browse',{kind:'interact',location_id:'home',
      data:{action:'interact',app_id:'app-missing'}}),
    activity('action_accepted','travel',{kind:'travel',destination_id:'poi-missing'}),
    activity('action_accepted','sleep',{kind:'sleep'}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()),['使用名称缺失（app-missing）','→ 名称缺失（poi-missing）','—']);
  assert.ok(view.errors.some(error => error.includes('app-missing')));
  assert.ok(view.errors.some(error => error.includes('poi-missing')));
  assert.ok(view.errors.every(error => !/[一-鿿]/.test(error)));
});

test('reload clears recorded names and missing-name diagnostics', async t => {
  const browse = app => activity('action_accepted', `browse-${app}`, {kind: 'interact', location_id: 'home',
    data: {action: 'interact', app_id: app, content_id: null}});
  const view = await viewer([browse('missing')]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), ['使用名称缺失（missing）']);
  assert.deepEqual(view.errors, ['Missing recorded object name: missing']);
  // 下一份库的冻结目录只登记了 missing：上一份库的名称（哔哩哔哩）不再有，上一份库的缺名记录也清空。
  const next = {apps: [{app_id: 'missing', name: '支付宝'}], contents: [], goods: [], locations: [{location_id: 'home',
    name: '家'}]};
  await view.reload([browse('missing'), browse('bilibili')], {directories: [runDirectory(next)]});
  assert.deepEqual(targets(view.activities()), ['使用支付宝', '使用名称缺失（bilibili）']);
  assert.deepEqual([...view.state.missingNames], ['bilibili']);
  assert.deepEqual(view.errors, ['Missing recorded object name: missing', 'Missing recorded object name: bilibili']);
});

test('raw app identifiers cannot become user-facing names or titles', async t => {
  const view=await viewer([
    activity('action_accepted','browse',{kind:'interact',location_id:'home',
      data:{action:'interact',app_id:'app-raw',content_id:null}}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()),['使用名称缺失（app-raw）']);
  assert.equal(view.summary(1), '已受理 · 使用名称缺失（app-raw） · 计划 10分');
  assert.equal(view.errors.length,1);
});

test('names are escaped in activities timeline and economy without changing stored events', async t => {
  const name = '<img src=x onerror=alert(1)>&"\'';
  const escaped = '&lt;img src=x onerror=alert(1)&gt;&amp;&quot;&#39;';
  const catalog = withCatalog({goods: [{goods_id: 'goods-html', name}], locations: [{location_id: 'poi-html', name}],
    apps: [{app_id: 'app-html', name}], contents: [{content_id: 'content-html', title: name}]});
  const view = await viewer([
    activity('action_accepted', 'unsafe-title', {kind: 'interact', location_id: 'home', content_title: name,
      data: {action: 'interact', app_id: 'app-html', content_id:'content-html'}}),
    activity('action_accepted', 'unsafe-location', {kind: 'travel', destination_id: 'poi-html'}),
    event('action_result', {status: 'completed', data: {
      orders: [{goods_id: 'goods-html', total_price_fen: 100, payment_app_id: null}]}}),
    event('environment_event',{kind:'purchase_payment'},{resource_changes:{'account:agent:1':-100,'account:external:scenario':100}}),
  ],{world: {agents:{1:{name}}}, catalog});
  t.after(() => view.close());
  const timeline = view.run('tlRow(S.events[0]).innerHTML');
  for (const markup of [view.activities(), timeline, view.economy()]) {
    assert.ok(markup.includes(escaped));
    assert.ok(!markup.includes('<img'));
  }
  assert.equal(JSON.parse(view.run('fetchBody(1)')).data.activity.content_title, name);
  assert.ok(view.summary(1).includes(name));
});

test('body curves read current observation values rather than obsolete body fields', async t => {
  const view = await viewer([
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

test('memory page shows stored episode text without requiring a directory upload', async t => {
  const view = await viewer([
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

test('activity lanes preserve simultaneous actions and pauses using settled intervals', async t => {
  const first = 'action-a', second = 'wait-b';
  const action = {action: 'interact', app_id: null, content_id: null};
  const view = await viewer([
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

/** @brief 读入随库提供的顶层文件与目录（与拖入时同一入口）；顶层另带用例文件冻结的目录素材，清单登记的正是它们。 */
async function loadedViewer(t, files, directories = [], events = [event('observation', {})]) {
  const view = await viewer(events, {files: [...files, ...assetFiles()], directories});
  t.after(() => view.close());
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
  assert.ok(strangers.errors.every(error => !/[一-鿿]/.test(error)));
});

test('reloading another database drops the previous run manifest and its warnings', async t => {
  const view = await loadedViewer(t, [droppedFile('manifest.json',
    runManifest({input_warnings: [inputWarning('CONTENT_EMPTY_WITH_MEDIA_TASTES', 1, '上一份库的警告')]}))]);
  assert.equal(warningRows(warningCard(view.overview())).length, 1);
  await view.reload([event('observation', {})], {files: []});
  const card = warningCard(view.overview());
  assert.ok(card.includes('未读取本库所属运行的运行清单') && !card.includes('上一份库的警告'));
  assert.equal(view.state.runManifest, null);
});


test('money uses authoritative envelope changes once and allows signed external balances', async t => {
  const view=await viewer([
    event('action_result',{status:'in_progress',data:{orders:[{goods_id:'milk',total_price_fen:100}]}}),
    event('environment_event',{kind:'purchase_payment'},{resource_changes:{'account:agent:1':-100,'account:external:scenario':100}}),
    event('environment_event',{kind:'payment_notification'}),
    event('observation',{balance_fen:900}),
  ],{world: {accounts:{'agent:1':900,'external:scenario':-900}}});
  t.after(()=>view.close());
  const markup=view.economy();
  assert.ok(markup.includes('共 2 条'));
  assert.equal((markup.match(/购买付款/g)||[]).length,2);
  assert.ok(markup.includes('外部账户'));
  assert.ok(markup.includes('-9.00'));
});

test('an aggregate acceptance keeps both actual travel and interaction activities', async t => {
  const travel={activity_id:'travel',kind:'travel',started_at:epoch,status:'active',elapsed_seconds:0,
    destination_id:'store',travel_method_id:'walking',data:{action:'travel'}};
  const interaction={activity_id:'browse',kind:'interact',started_at:epoch,status:'active',elapsed_seconds:0,
    location_id:'home',data:{action:'interact',app_id:'meituan',content_id:null}};
  const view=await viewer([
    event('action_accepted',{data:{activities:[travel,interaction]}}),
  ]);
  t.after(()=>view.close());
  // 聚合受理里的出行活动同样带出行方式身份，活动行在目的地前写出行方式名称；没有内容的 app 使用写过渡写法「使用<app 名>」。
  assert.deepEqual(targets(view.activities()),['步行 · → 四季便利','使用美团']);
  assert.deepEqual(view.errors,[]);
});

test('SQLite content snapshots without retired title display actual content and pure browse app', async t => {
  const place={kind:'interact',location_id:'home',content_title:'晚间阅读',
    data:{action:'interact',app_id:null,content_id:'evening-reading'}};
  const app={kind:'interact',location_id:'home',content_title:'数据分析课程',
    data:{action:'interact',app_id:'bilibili',content_id:'analysis-course'}};
  const browse={kind:'interact',location_id:'home',data:{action:'interact',app_id:'meituan',content_id:null}};
  const view=await viewer([
    activity('action_accepted','place-content',place),
    activity('activity_progress','place-content',{...place,status:'completed',ended_at:epoch+120}),
    activity('action_accepted','app-content',app),
    activity('action_accepted','platform-browse',browse),
  ], {catalog: withCatalog({contents: [{content_id: 'evening-reading', title: '晚间阅读'},
    {content_id: 'analysis-course', title: '数据分析课程'}]})});
  t.after(()=>view.close());
  assert.equal(view.run("q(\"SELECT json_extract(body,'$.data.activity.content_title') AS content FROM events WHERE event_seq=1\")[0].content"),'晚间阅读');
  assert.equal(view.run("q(\"SELECT COUNT(*) AS present FROM events WHERE json_type(body,'$.data.activity.title') IS NOT NULL\")[0].present"),0);
  // 标题按内容身份取冻结目录；没有内容的 app 使用是过渡写法「使用<app 名>」（收尾时随这种样本一起删去）。
  assert.deepEqual(targets(view.activities()),['在家：晚间阅读','使用哔哩哔哩《数据分析课程》','使用美团']);
  assert.ok(view.summary(1).includes('在家：晚间阅读'));
  assert.ok(view.summary(2).includes('在家：晚间阅读'));
  assert.ok(view.summary(3).includes('使用哔哩哔哩《数据分析课程》'));
  assert.ok(view.summary(4).includes('使用美团'));
  for(const sequence of [1,2,3,4]){
    assert.ok(!Object.hasOwn(JSON.parse(view.run(`fetchBody(${sequence})`)).data.activity,'title'));
  }
  assert.deepEqual(view.errors,[]);
});

/* ---------- 统一显示写法：名称只取这次运行冻结的环境目录 ---------- */

/** @brief 用给出的目录原文（素材名到文本）搭一个运行目录：assets 里以摘要为文件名放这些文本，清单登记它们；
 *  adjust 改写清单登记的 environment_assets。 */
function catalogRun(texts, adjust = assets => assets) {
  const assets = {}, entries = [];
  for (const [asset, text] of Object.entries(texts)) {
    const sha256 = createHash('sha256').update(text, 'utf8').digest('hex');
    assets[asset] = {sha256, size_bytes: Buffer.byteLength(text, 'utf8'), media_type: 'text/plain',
      source: {kind: 'environment_input', path: asset}};
    entries.push(fileEntry(sha256, text));
  }
  return directoryEntry('run', [fileEntry('manifest.json', {...runManifest(), environment_assets: adjust(assets)}),
    directoryEntry('assets', entries)]);
}

/** @brief 用例文件 catalog 冻结后的各份目录原文（素材名到文本）。 */
function caseTexts() {
  const frozen = frozenCatalog();
  return Object.fromEntries(Object.entries(frozen.assets).map(([asset, reference]) =>
    [asset, frozen.files.find(file => file.name === reference.sha256).text]));
}

/** @brief 要读名称的三项活动：用哔哩哔哩看《测试文章》、驾车去公司、步行去带分店的快递驿站。 */
function namedActivities() {
  return [
    activity('action_accepted', 'watch', {kind: 'interact', location_id: 'home',
      data: {action: 'interact', app_id: 'bilibili', content_id: 'c-article'}}),
    activity('action_accepted', 'drive', {kind: 'travel', location_id: 'home', destination_id: 'office',
      travel_method_id: 'driving', data: {action: 'travel'}}),
    activity('action_accepted', 'walk', {kind: 'travel', location_id: 'office', destination_id: 'parcel-station-south',
      travel_method_id: 'walking', data: {action: 'travel'}}),
  ];
}
/** @brief 三项活动在名称都读到时的「目标」。 */
const NAMED = ['使用哔哩哔哩《测试文章》', '驾车 · → 公司', '步行 · → 快递驿站（南门）'];
/** @brief 四份必需目录的素材名。 */
const REQUIRED_CATALOGS = ['catalog/app-catalog.jsonl', 'catalog/content-catalog.jsonl',
  'catalog/merchant-catalog.jsonl', 'catalog/location.jsonl'];

/** @brief 泳道各行：标签与行里各条块的左端、宽度和悬停文字（照页面写出的原样）。 */
function lanes(markup) {
  const row = new RegExp('<div class="act-lane"><div class="act-lane-label" title="[^"]*">([^<]*)</div>\\s*' +
    '<div class="act-lane-track">([\\s\\S]*?)</div></div>', 'g');
  const bar = /<span class="act-lane-bar" style="left:([^%]*)%;width:([^%]*)%;[^"]*"\s*title="([^"]*)"><\/span>/g;
  return [...markup.matchAll(row)].map(([, label, track]) => ({label,
    bars: [...track.matchAll(bar)].map(([, left, width, title]) => ({left, width, title}))}));
}

/** @brief 总览「对象名称」卡片的完整标记，缺的素材名以「、」相连。 */
function catalogCard(missing) {
  return `<div class="card"><h3 style="margin-top:0">对象名称</h3><div class="hint">未读到这次运行冻结的环境目录（缺 ` +
    `${missing.join('、')}），对象名称显示为「名称缺失（身份）」。请把整个 run 目录或导出目录拖入：` +
    `其中的 manifest.json 与 assets 目录都要在。</div></div>`;
}

test('catalog names are read from the frozen assets named by the run manifest', async t => {
  const opened = async options => {
    const view = await viewer(namedActivities(), options);
    t.after(() => view.close());
    return view;
  };
  // 运行目录：清单 environment_assets 登记的摘要在 assets 目录里找到五份目录文件。
  const view = await opened();
  assert.deepEqual(targets(view.activities()), NAMED);
  assert.ok(!view.overview().includes('对象名称'));
  assert.deepEqual(view.errors, []);
  // 顶层选中的、文件名恰为摘要的素材同样被采用（清单是同时选中的 manifest.json）。
  const topLevel = await opened({files: [droppedFile('manifest.json', runManifest()), ...assetFiles()]});
  assert.deepEqual(targets(topLevel.activities()), NAMED);
  assert.deepEqual(topLevel.errors, []);
  // 同一个摘要记到两次（例如运行目录和导出目录一起拖入）取先遇到的那个：这里先遇到的那份把哔哩哔哩写成同字节数的「哔哩哔哔」。
  const appsSha = frozenCatalog().assets['catalog/app-catalog.jsonl'].sha256;
  const variant = caseTexts()['catalog/app-catalog.jsonl'].replace('哔哩哔哩', '哔哩哔哔');
  const copy = () => directoryEntry('copy', [directoryEntry('assets', [fileEntry(appsSha, variant)])]);
  assert.equal(targets((await opened({directories: [copy(), runDirectory()]})).activities())[0],
    '使用哔哩哔哔《测试文章》');
  assert.equal(targets((await opened({directories: [runDirectory(), copy()]})).activities())[0],
    '使用哔哩哔哩《测试文章》');
  // 别的运行的清单不被采用：同样的素材也读不到名称。
  const stranger = directoryEntry('other', [fileEntry('manifest.json', runManifest({}, 'another-run')),
    directoryEntry('assets', frozenCatalog().files.map(file => fileEntry(file.name, file.text)))]);
  const foreign = await opened({directories: [stranger]});
  assert.deepEqual(targets(foreign.activities()), ['使用名称缺失（bilibili）《名称缺失（c-article）》',
    '名称缺失（driving） · → 名称缺失（office）', '步行 · → 名称缺失（parcel-station-south）']);
  assert.equal(foreign.errors[0], 'Frozen catalog is unavailable: no run manifest for this database');
  // 环境没有出行方式目录时清单里没有这个键，不算缺失；步行仍写「步行」。
  const {travel_methods: methods, ...withoutMethods} = CASES.catalog;
  assert.ok(methods.length);
  const walking = await viewer([namedActivities()[2]], {catalog: withoutMethods});
  t.after(() => walking.close());
  assert.deepEqual(targets(walking.activities()), ['步行 · → 快递驿站（南门）']);
  assert.ok(!walking.overview().includes('对象名称'));
  assert.deepEqual(walking.errors, []);
  // 文件选择框不限扩展名：素材文件没有扩展名，数据库仍按文件名后缀认。
  const page = readFileSync(path.join(ROOT, 'tools/run-viewer.html'), 'utf8');
  assert.match(page, /<input type="file" id="file-input" multiple>/);
  assert.doesNotMatch(page, /id="file-input"[^>]*accept=/);
});

test('export directories supply the same names through frozen_run_manifest', async t => {
  const exported = directoryEntry('worldmock-export', [
    fileEntry('manifest.json', {schema_version: 6, run_id: RUN_ID, frozen_run_manifest: runManifest()}),
    directoryEntry('assets', frozenCatalog().files.map(file => fileEntry(file.name, file.text)))]);
  const view = await viewer(namedActivities(), {directories: [exported]});
  t.after(() => view.close());
  assert.equal(view.state.runManifest.frozen, true);
  assert.deepEqual(targets(view.activities()), NAMED);
  assert.ok(!view.overview().includes('对象名称'));
  assert.deepEqual(view.errors, []);
});

test('a catalog file that is absent or has the wrong size is treated as missing and logged in English', async t => {
  const opened = async options => {
    const view = await viewer(namedActivities(), options);
    t.after(() => view.close());
    return view;
  };
  // 只给数据库：全部对象名缺失，每个身份一次英文错误，总览出现「对象名称」卡片，列出四份必需目录。
  const bare = await opened({files: []});
  assert.deepEqual(targets(bare.activities()), ['使用名称缺失（bilibili）《名称缺失（c-article）》',
    '名称缺失（driving） · → 名称缺失（office）', '步行 · → 名称缺失（parcel-station-south）']);
  assert.deepEqual([...bare.errors].sort(), ['Frozen catalog is unavailable: no run manifest for this database',
    ...['bilibili', 'c-article', 'driving', 'office', 'parcel-station-south']
      .map(identity => `Missing recorded object name: ${identity}`)].sort());
  assert.ok(bare.overview().includes(catalogCard(REQUIRED_CATALOGS)));
  const texts = caseTexts();
  const resized = asset => assets => ({...assets,
    [asset]: {...assets[asset], size_bytes: assets[asset].size_bytes + 1}});
  const cases = [
    // 字节数与清单不符：只核对字节数，不重算摘要；这一份按缺失处理。
    [catalogRun(texts, resized('catalog/app-catalog.jsonl')),
      'Frozen catalog file size disagrees with the run manifest: catalog/app-catalog.jsonl',
      ['使用名称缺失（bilibili）《测试文章》', ...NAMED.slice(1)], ['bilibili']],
    // 某一行不是 JSON 对象：整份读不了。
    [catalogRun({...texts, 'catalog/location.jsonl': `${texts['catalog/location.jsonl']}[1, 2]\n`}),
      'Frozen catalog file is unreadable: catalog/location.jsonl',
      [NAMED[0], '驾车 · → 名称缺失（office）', '步行 · → 名称缺失（parcel-station-south）'],
      ['office', 'parcel-station-south']],
    [catalogRun({...texts, 'catalog/content-catalog.jsonl': `${texts['catalog/content-catalog.jsonl']}not json\n`}),
      'Frozen catalog file is unreadable: catalog/content-catalog.jsonl',
      ['使用哔哩哔哩《名称缺失（c-article）》', ...NAMED.slice(1)], ['c-article']],
    // 清单的 environment_assets 里没有四份必需目录之一。
    [catalogRun(texts, ({'catalog/merchant-catalog.jsonl': dropped, ...rest}) => rest),
      'Frozen catalog file is missing: catalog/merchant-catalog.jsonl', NAMED, []],
  ];
  for (const [directory, error, expected, missing] of cases) {
    const view = await opened({directories: [directory]});
    assert.deepEqual(targets(view.activities()), expected, error);
    assert.deepEqual(view.errors, [error, ...missing.map(identity => `Missing recorded object name: ${identity}`)]);
    assert.ok(view.overview().includes(catalogCard([error.split(': ')[1]])), error);
  }
  // 清单登记的可选出行方式文件不在同一次拖入的文件里：照缺失处理并写英文错误；它不是必需目录，总览不出卡片。
  const methods = await opened({directories: [catalogRun(texts, assets => ({...assets,
    'catalog/travel-method.jsonl': {...assets['catalog/travel-method.jsonl'], sha256: 'f'.repeat(64)}}))]});
  assert.deepEqual(targets(methods.activities()), [NAMED[0], '名称缺失（driving） · → 公司', NAMED[2]]);
  assert.deepEqual(methods.errors, ['Frozen catalog file is missing: catalog/travel-method.jsonl',
    'Missing recorded object name: driving']);
  assert.ok(!methods.overview().includes('对象名称'));
  // 换一个数据库时名称表一起清空：只给下一份数据库时名称全部缺失。
  const reloaded = await opened();
  assert.deepEqual(targets(reloaded.activities()), NAMED);
  await reloaded.reload(namedActivities(), {files: []});
  assert.deepEqual(targets(reloaded.activities()), targets(bare.activities()));
  assert.ok(reloaded.overview().includes(catalogCard(REQUIRED_CATALOGS)));
  for (const view of [bare, reloaded]) assert.ok(view.errors.every(text => !/[一-鿿]/.test(text)));
});

test('observation names are never used', async t => {
  // 观察里写着 app、内容、商品、地点与各种绑定的名称，也不被读取：只给数据库时对象名仍然缺失。
  const observed = event('observation', {
    apps: [{app_id: 'bilibili', name: '哔哩哔哩'}], contents: [{content_id: 'c-article', action: 'interact',
      title: '测试文章'}], goods: [{goods_id: 'beef-noodles', name: '牛肉面'}],
    locations: [{location_id: 'office', name: '公司'}, {location_id: 'parcel-station-south', name: '快递驿站'}],
    interaction_bindings: [{object_id: 'bilibili', object: '哔哩哔哩'}],
    errand_item_bindings: [{name: '牛肉面', product_id: 'beef-noodles'}],
    catalog_bindings: [{object: '公司', kind: 'location', object_id: 'office', actions: [
      {action: 'travel', detail: '驾车', note: null, travel_method_id: 'driving'}]}]});
  const view = await viewer([observed, ...namedActivities(),
    errandEvent('errand_registered', errandSnapshot({product_id: 'beef-noodles'}))], {files: []});
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), ['使用名称缺失（bilibili）《名称缺失（c-article）》',
    '名称缺失（driving） · → 名称缺失（office）', '步行 · → 名称缺失（parcel-station-south）']);
  assert.equal(view.summary(5), '世界事件 · 事务记下 · 「买牛奶」 · 商品 名称缺失（beef-noodles）');
  assert.ok(view.errors.includes('Missing recorded object name: beef-noodles'));
});

test('activity table lanes and timeline write the shared wording and keep the kind label', async t => {
  const at = minutes => epoch + minutes * 60;
  const snapshot = (id, kind, fields) => ({activity_id: id, kind, location_id: 'home', status: 'active',
    planned_duration_seconds: 600, elapsed_seconds: 0, ...fields});
  const watch = content_id => ({action: 'interact', app_id: 'bilibili', content_id});
  const cook = content_id => ({action: 'interact', app_id: null, content_id});
  const played = (content_id, app_id) => ({content_id, app_id, completed: true});
  const step = (id, kind, start, fields, extra = {}) => event('activity_progress', {activity: snapshot(id, kind,
    {started_at: at(start), ...fields}), ...extra}, {activity_id: id, occurred_at: extra.interval_end ?? at(start)});
  const accepted = (id, kind, start, fields) => event('action_accepted', {status: 'in_progress',
    activity: snapshot(id, kind, {started_at: at(start), ...fields})}, {activity_id: id, occurred_at: at(start)});
  const orders = [
    {order_id: 'order-noodles', goods_id: 'beef-noodles', app_id: 'meituan', total_price_fen: 2800,
      payment_app_id: 'alipay'},
    {order_id: 'order-tissue', goods_id: 'tissue', app_id: null, total_price_fen: 600, payment_app_id: null}];
  const view = await viewer([
    // 用哔哩哔哩连播剧集：第一条进度事件就是第一集做到头的那一条，快照里的当前内容已经换成第二集。
    accepted('watch', 'interact', 0, {data: watch('c-drama-1')}),
    step('watch', 'interact', 0, {elapsed_seconds: 1800, data: watch('c-drama-2')},
      {interval_start: at(0), interval_end: at(30), content: played('c-drama-1', 'bilibili')}),
    step('watch', 'interact', 0, {status: 'cancelled', elapsed_seconds: 2700, data: watch('c-drama-2')},
      {interval_start: at(30), interval_end: at(45), content: played('c-drama-2', 'bilibili')}),
    // 在家做晚饭系列的两集。
    accepted('cook', 'interact', 60, {data: cook('c-dinner-1')}),
    step('cook', 'interact', 60, {elapsed_seconds: 1800, data: cook('c-dinner-2')},
      {interval_start: at(60), interval_end: at(90), content: played('c-dinner-1', null)}),
    step('cook', 'interact', 60, {status: 'completed', elapsed_seconds: 3300, data: cook('c-dinner-2')},
      {interval_start: at(90), interval_end: at(115), content: played('c-dinner-2', null)}),
    // 一次购买分成经美团下单和到店两张订单；出行与睡眠照原格式。
    accepted('buy', 'purchase', 120, {data: {action: 'purchase', order_ids: ['order-tissue', 'order-noodles']}}),
    event('action_result', {status: 'in_progress', data: {orders}}, {activity_id: 'buy', occurred_at: at(120)}),
    accepted('drive', 'travel', 130, {destination_id: 'office', travel_method_id: 'driving', data: {action: 'travel'}}),
    accepted('sleep', 'sleep', 150, {data: {action: 'sleep'}}),
  ]);
  t.after(() => view.close());
  const markup = view.activities();
  const phrases = ['使用哔哩哔哩《测试剧集第一集》《测试剧集第二集》', '在家：做晚饭《备菜做饭》《吃晚饭》',
    '购买牛肉面（经美团下单）；纸巾（到店）'];
  // 活动类型列仍是种类标签，「目标」列是写法；泳道的标签同一句，出行、睡眠的泳道照原格式「种类 目标」。
  assert.deepEqual(tableRows(markup, 'act-table').map(cells => [cells[2].replace(/<[^>]*>/g, ''), cells[3]]), [
    ['互动', phrases[0]], ['互动', phrases[1]], ['购买', phrases[2]], ['出行', '驾车 · → 公司'], ['睡眠', '—']]);
  assert.deepEqual(lanes(markup).map(lane => lane.label), [...phrases, '出行 驾车 · → 公司', '睡眠 —']);
  // 时间线：受理行和活动段行直接写写法；一集做到头的那条活动段行写的是刚做完的这一集。
  assert.equal(view.summary(1), `已受理 · 使用哔哩哔哩《测试剧集第一集》 · 计划 10分`);
  assert.equal(view.summary(2), '活动段 · 使用哔哩哔哩《测试剧集第一集》 · 进行中 · 已进行 30分');
  assert.equal(view.summary(3), '活动段 · 使用哔哩哔哩《测试剧集第二集》 · 已取消 · 已进行 45分');
  assert.equal(view.summary(4), '已受理 · 在家：做晚饭《备菜做饭》 · 计划 10分');
  assert.equal(view.summary(5), '活动段 · 在家：做晚饭《备菜做饭》 · 进行中 · 已进行 30分');
  assert.equal(view.summary(6), '活动段 · 在家：做晚饭《吃晚饭》 · 已完成 · 已进行 55分');
  assert.equal(view.summary(7), `已受理 · ${phrases[2]} · 计划 10分`);
  // 出行、睡眠行与改动之前逐字相同。
  assert.equal(view.summary(9), '已受理 · 出行 · 驾车 · → 公司 · 计划 10分');
  assert.equal(view.summary(10), '已受理 · 睡眠 · — · 计划 10分');
  assert.deepEqual(view.errors, []);
});

test('result rows name the ordering channel and the payment app', async t => {
  const view = await viewer([event('action_result', {status: 'completed', message: 'Purchase completed', data: {
    orders: [
      {order_id: 'order-noodles', goods_id: 'beef-noodles', app_id: 'meituan', total_price_fen: 2800,
        payment_app_id: 'alipay'},
      {order_id: 'order-tissue', goods_id: 'tissue', app_id: null, total_price_fen: 600, payment_app_id: null},
      {order_id: 'order-notebook', goods_id: 'online-notebook', app_id: 'bilibili', total_price_fen: 1200,
        payment_app_id: 'bilibili'}]}})]);
  t.after(() => view.close());
  // 每条商品履约记录一段：商品、金额、渠道（经某 app 下单或到店），有支付 app 再写它的名称（直付时就是下单的 app）。
  assert.equal(view.summary(1), '结果 已完成 · Purchase completed · 订单 牛肉面 ¥28.00（经美团下单） · 支付 支付宝' +
    ' · 订单 纸巾 ¥6.00（到店） · 订单 平台笔记本 ¥12.00（经哔哩哔哩下单） · 支付 哔哩哔哩');
  assert.deepEqual(view.errors, []);
});

test('phone records name their app and what happened', async t => {
  const view = await viewer([
    ...CASES.phone.map(item => event('environment_event', item.data, {visibility: 'system'})),
    event('environment_event', {kind: 'app_foreground', app_id: 'meituan', step: 'waiting',
      interval_start: epoch, interval_end: epoch + 60}, {visibility: 'system'}),
  ]);
  t.after(() => view.close());
  // 三个步骤、三种订单通知标题与支付成功，app 名按事件的 app_id 取（含功能 app），与共用用例文件逐条相同。
  CASES.phone.forEach((item, index) => assert.equal(view.summary(index + 1), `世界事件 · ${item.text}`, item.name));
  // 查看器会打开别的版本的运行：三个之外的步骤照原值显示。
  assert.equal(view.summary(CASES.phone.length + 1), '世界事件 · app 前台 · 美团 · waiting');
  assert.deepEqual(view.errors, []);
});
