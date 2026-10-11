'use strict';

const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '../../..');
/** @brief 读取仓库文本并统一换行，避免 core.autocrlf 工作副本影响解析。 */
const read = relative => readFileSync(path.join(root, relative), 'utf8').replace(/\r\n/g, '\n');
const source = read('tools/run-viewer-context.js');
const fixture = JSON.parse(read('tests/fixtures/viewer-context.json'));
const LAYER_KEYS = ['tool', 'persona', 'memory', 'todo', 'environment', 'progress'];
const LAYER_NAMES = ['工具层', '人设层', '记忆层', '待办层', '环境层', '进度层'];

/** @brief 在独立上下文执行真实解析模块，同时收集解析失败日志。 */
function parser() {
  const errors = [];
  const context = vm.createContext({console: {error: (...args) => errors.push(args.join(' '))}});
  vm.runInContext(source, context, {filename: 'run-viewer-context.js'});
  return {api: vm.runInContext('ViewerContext', context), errors};
}

/** @brief 取夹具中的一种请求的深拷贝：action、continuation。 */
function request(kind = 'action') {
  return JSON.parse(JSON.stringify(fixture[kind]));
}

/** @brief 构造当前只读调用，保留完整原文并验证不会误判为行动请求。 */
function otherRequest() {
  const value = request();
  value.messages[0].content = JSON.stringify({layer: '系统层', role: '依据人物记忆回答问题。', rules: []});
  delete value.tools;
  return value;
}

/** @brief 把请求包成归档 model_request 事件。 */
function event(body = request(), purpose = undefined) {
  const data = {phase: 'request', request: body};
  if (purpose) data.purpose = purpose;
  return {event_type: 'model_request', data};
}

/** @brief 取得某条消息的全部内容块，便于检验原文拼接。 */
function messageBlocks(parsed, index = 1) {
  return parsed.blocks.filter(block => block.messageIndex === index);
}

/** @brief 使用原生对象比较跨上下文返回值，不依赖 VM 原型身份。 */
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

/** @brief 用给定六层重写首个 user 为紧凑 JSON 数组。 */
function withLayers(layers, base = request()) {
  base.messages[1].content = JSON.stringify(layers);
  return base;
}

/** @brief 解析夹具首个 user 的六层对象。 */
function layers(kind = 'action') {
  return JSON.parse(fixture[kind].messages[1].content);
}

test('genuine current requests have seven layers and eleven offered tools', () => {
  const {api, errors} = parser();
  const parsed = api.parseRequest(event());
  assert.equal(parsed.stage, 'action');
  assert.deepEqual(plain(messageBlocks(parsed).map(block => block.key)), LAYER_KEYS);
  assert.equal(messageBlocks(parsed).map(block => block.text).join(''), request().messages[1].content);
  assert.deepEqual(layers().map(layer => layer.layer), LAYER_NAMES);
  // 现行工具面：六个世界请求（两个开始工具在前）、两个世界查询与三个记忆查询，没有已删除的 start_activity。
  assert.deepEqual(fixture.action.tools.map(tool => tool.function.name), [
    'start_main_activity', 'start_side_activity', 'report_status', 'pause_activity', 'conclude_activity',
    'revise_day_plan', 'recall_task_summary', 'query_world', 'memory_recent', 'memory_search', 'memory_time_range']);
  assert.equal(fixture.action.tools.length, 11);
  assert.deepEqual(fixture.continuation.tools, fixture.action.tools);
  assert.deepEqual(fixture.continuation.messages.slice(0, 2), fixture.action.messages);
  assert.equal(parsed.warnings.length, 0);
  assert.equal(errors.length, 0);
});

test('the current tool layer contains only the four actual actions with lossless target drills', () => {
  const {api} = parser();
  const parsed = api.parseRequest(event());
  const tool = parsed.blocks.find(block => block.key === 'tool');
  const groups = tool.value.groups;
  assert.deepEqual([...new Set(groups.map(group => group.action).filter(Boolean))].sort(),
    ['interact', 'purchase', 'sleep', 'travel']);
  // 出行只有 travel 一个动作：同一目的地每种出行方式一行，靠 detail 区分，每个目的地的方式集合相同。
  const methods = new Map();
  for (const group of groups.filter(group => group.action === 'travel')) for (const entry of group.targets) {
    methods.set(entry.name, [...(methods.get(entry.name) ?? []), entry.detail]);
  }
  assert.ok(methods.has('公司'));
  for (const details of methods.values()) assert.deepEqual([...details].sort(), ['公共交通', '打车', '步行', '驾车']);
  const items = plain(api.drillItems(tool));
  for (const group of groups) for (const entry of group.targets) {
    const merged = {...group.shared, ...entry, kind:group.kind};
    assert.ok(items.some(item => item.text === JSON.stringify(merged, null, 2)));
    // 带 detail 的行（出行、在地点做事）标签写「目标 · 动作 · detail · 可用性」，没有 detail 的行不留空位。
    const named = entry.detail == null ? group.action : `${group.action} · ${entry.detail}`;
    const state = merged.available ? '可用' : `不可用：${merged.reason}`;
    if (group.action !== null) assert.ok(items.some(item => item.label === `${entry.name} · ${named} · ${state}`),
      `${entry.name} · ${named} · ${state}`);
    assert.equal(Object.hasOwn(group, 'way'), false);
  }
});

/** @brief 生活决策系统层的人物角色原文，取自提示的唯一来源 LIFE_SYSTEM_LAYER，不抄查看器里的那一份。 */
const LIFE_ROLE = /LIFE_SYSTEM_LAYER[^=]*= \{\s*"layer": "系统层",\s*"role": "([^"]+)"/.exec(
  read('src/worldmock/agents/life_prompt.py'))[1];

/** @brief 现行工具层在提示里的紧凑分组（由真实引擎的观察照抄）：出行每个目的地每种方式一行、在地点做事每个内容单元
 * 一行，都带 detail；睡眠、购买与用内容 app 的动作行没有 detail。驾车一行按余额不足写成不可用。 */
const START_TOOL_LAYER = {layer: '工具层', groups: [
  {kind: 'location', action: 'travel', shared: {state: '全天营业'}, targets: [
    {name: '公司', detail: '步行', available: true, price: '0 元', duration: '约 32 分钟', note: '费力'},
    {name: '公司', detail: '驾车', available: false, reason: "The caller's available balance is below the route fare.",
      price: '5 元', duration: '约 5 分钟', note: '轻松'},
    {name: '公司', detail: '打车', available: true, price: '14 元', duration: '约 5 分钟', note: '轻松'},
    {name: '公司', detail: '公共交通', available: true, price: '2 元', duration: '约 8 分钟', note: '轻松'}]},
  {kind: 'location', action: 'interact', shared: {state: '全天营业', available: true}, targets: [
    {name: '家', detail: '测试闲聊', duration: '约 10 分钟'},
    {name: '家', detail: '测试休息', duration: '约 15 分钟'}]},
  {kind: 'location', action: 'sleep', shared: {state: '全天营业', available: true, note: '现在不欠觉'},
    targets: [{name: '家'}]},
  {kind: 'product', action: 'purchase', shared: {duration: '1–5 分钟', quantity: '1 件'}, targets: [
    {name: '柠檬茶', available: true, price: '12 元', note: '运费5 元，预计01月05日 08:18到达'}]},
  {kind: 'app', action: 'interact', shared: {available: true}, targets: [
    {name: '哔哩哔哩', duration: '约 5 分钟–约 40 分钟'}]},
], callable_index: {}};

/** @brief 不读录制夹具，按七层合同内联构造一个现行行动请求：系统层、六个用户层与含两个开始工具的工具数组。 */
function inlineRequest(toolLayer) {
  const userLayers = [toolLayer, ...LAYER_NAMES.slice(1).map(layer => ({layer}))];
  return {model: 'test-model', messages: [
    {role: 'system', content: JSON.stringify({layer: '系统层', role: LIFE_ROLE, rules: []})},
    {role: 'user', content: JSON.stringify(userLayers)}],
  tools: ['start_main_activity', 'start_side_activity', 'conclude_activity'].map(
    name => ({type: 'function', function: {name}}))};
}

test('tool layer drill-down names the detail of travel and location rows between the action and the availability', () => {
  const {api, errors} = parser();
  const original = inlineRequest(START_TOOL_LAYER);
  const parsed = api.parseRequest(event(original));
  assert.equal(parsed.stage, 'action');
  assert.equal(parsed.warnings.length, 0);
  assert.deepEqual(plain(messageBlocks(parsed).map(block => block.key)), LAYER_KEYS);
  assert.equal(messageBlocks(parsed).map(block => block.text).join(''), original.messages[1].content);
  const tool = parsed.blocks.find(block => block.key === 'tool');
  const items = plain(api.drillItems(tool));
  // 每个分组行先给目标一项，再给它的动作一项：带 detail 的行写「目标 · 动作 · detail · 可用性」，没有 detail 的行不留空位。
  assert.deepEqual(items.map(item => item.label), [
    '公司（location）', '公司 · travel · 步行 · 可用',
    '公司（location）', "公司 · travel · 驾车 · 不可用：The caller's available balance is below the route fare.",
    '公司（location）', '公司 · travel · 打车 · 可用',
    '公司（location）', '公司 · travel · 公共交通 · 可用',
    '家（location）', '家 · interact · 测试闲聊 · 可用',
    '家（location）', '家 · interact · 测试休息 · 可用',
    '家（location）', '家 · sleep · 可用',
    '柠檬茶（product）', '柠檬茶 · purchase · 可用',
    '哔哩哔哩（app）', '哔哩哔哩 · interact · 可用',
  ]);
  // 同一目的地的四行出行只靠 detail 区分；下钻正文无损：动作在前，其后是分组共有字段、本行字段与类别。
  const text = label => JSON.parse(items.find(item => item.label === label).text);
  assert.deepEqual(text('公司 · travel · 公共交通 · 可用'), {action: 'travel', state: '全天营业', name: '公司',
    detail: '公共交通', available: true, price: '2 元', duration: '约 8 分钟', note: '轻松', kind: 'location'});
  assert.deepEqual(text('家 · interact · 测试休息 · 可用'), {action: 'interact', state: '全天营业', available: true,
    name: '家', detail: '测试休息', duration: '约 15 分钟', kind: 'location'});
  assert.deepEqual(text('家 · sleep · 可用'), {action: 'sleep', state: '全天营业', available: true,
    note: '现在不欠觉', name: '家', kind: 'location'});
  for (const item of items) assert.doesNotMatch(item.label, /undefined|null| ·  · /);
  assert.equal(errors.length, 0);
});

test('current progress records keep source facts and mark self notes unverified', () => {
  const {api} = parser();
  const values = layers();
  values[5].events = [
    {occurred_at:'2026-01-05T08:00:00+08:00', type:'tool_call', tool:'revise_day_plan', self_note:'先列计划。'},
    {occurred_at:'2026-01-05T08:05:00+08:00', type:'app_push', app:'抖音', title:'测试实况', summary:'测试摘要。'},
    {occurred_at:'2026-01-05T08:10:00+08:00', type:'errand_completed', errand:'买牛奶'},
  ];
  const block = api.parseRequest(event(withLayers(values))).blocks.find(row => row.key === 'progress');
  const items = plain(api.drillItems(block));
  assert.equal(items.length, 3);
  assert.match(items[0].label, /self_note（未经核验）/);
  assert.ok(items[0].text.endsWith(JSON.stringify(values[5].events[0], null, 2)));
  assert.deepEqual(JSON.parse(items[1].text), values[5].events[1]);
  assert.match(items[2].label, /买牛奶/);
});

test('other requests retain every complete message', () => {
  const {api} = parser();
  const original = otherRequest();
  const parsed = api.parseRequest(event(original));
  assert.equal(parsed.stage, 'other');
  assert.ok(parsed.blocks.some(block => block.text === original.messages[1].content));
  assert.equal(parsed.toolChars, 0);
});

test('nested brackets and escaped quotes never change lossless layer boundaries', () => {
  const {api} = parser();
  const values = layers();
  values[2].earlier_today = [{settled:'名字 [a,b] 与 \"引号\"', nested:{value:['[', ']']}}];
  const original = withLayers(values);
  const parsed = api.parseRequest(event(original));
  assert.deepEqual(plain(messageBlocks(parsed).map(block => block.key)), LAYER_KEYS);
  assert.equal(messageBlocks(parsed).map(block => block.text).join(''), original.messages[1].content);
});

test('malformed layers stay complete with an explicit parse warning', () => {
  for (const content of ['broken JSON', '[{"layer":"工具层"}]', ' '+request().messages[1].content]) {
    const {api, errors} = parser();
    const original = request();
    original.messages[1].content = content;
    const parsed = api.parseRequest(event(original));
    assert.ok(parsed.blocks.some(block => block.text === content));
    assert.ok(parsed.warnings.length > 0);
    assert.ok(errors.length > 0);
  }
});

test('the first user may follow an additional assistant message', () => {
  const {api} = parser();
  const original = request();
  original.messages.splice(1, 0, {role:'assistant',content:'追加原文'});
  const parsed = api.parseRequest(event(original));
  assert.deepEqual(plain(messageBlocks(parsed, 2).map(block => block.key)), LAYER_KEYS);
  assert.ok(parsed.blocks.some(block => block.text.includes('追加原文')));
});

test('empty tools are visible while absent tools produce no placeholder', () => {
  const {api} = parser();
  const empty = api.parseRequest(event({...request(), tools:[]}));
  assert.equal(empty.toolChars, 2);
  assert.equal(empty.blocks.at(-1).text, '[]');
  const absent = request();
  delete absent.tools;
  assert.equal(api.parseRequest(event(absent)).toolChars, 0);
});

test('archived messages with invalid envelopes are displayed with an error', () => {
  const {api, errors} = parser();
  const original = event({messages:null, tools:[]});
  const parsed = api.parseRequest(original);
  assert.equal(parsed.blocks[0].text, JSON.stringify(original));
  assert.equal(parsed.warnings.length, 1);
  assert.equal(errors.length, 1);
});

test('continuations retain the exact archived assistant and tool messages', () => {
  const {api} = parser();
  const original = request('continuation');
  const parsed = api.parseRequest(event(original));
  const extras = parsed.blocks.filter(block => block.messageIndex >= 2);
  assert.deepEqual(plain(extras.map(block => block.role)), ['assistant', 'tool']);
  for (const block of extras) assert.deepEqual(JSON.parse(block.text), original.messages[block.messageIndex]);
});

test('Unicode character accounting includes each whole message and the tools once', () => {
  const {api} = parser();
  const original = request();
  const parsed = api.parseRequest(event(original));
  const counted = parsed.blocks.filter(block => block.key !== 'tools').reduce(
    (sum,block) => sum + Array.from(block.text).length, 0);
  assert.equal(parsed.messageChars, counted);
  assert.equal(parsed.toolChars, Array.from(JSON.stringify(original.tools)).length);
});

test('comparison finds equal length edits, additions and removed blocks', () => {
  const {api} = parser();
  const before = api.parseRequest(event());
  const values = layers();
  values[4].weather = '晴天';
  const after = api.parseRequest(event(withLayers(values)));
  const compared = plain(api.compare(after.blocks, before.blocks));
  assert.equal(compared.find(block => block.key === 'environment').status, 'changed');
  assert.equal(compared.find(block => block.key === 'tool').status, 'unchanged');
  const removed = plain(api.compare(after.blocks.slice(1), before.blocks));
  assert.ok(removed.some(block => block.status === 'removed'));
  for (const block of after.blocks) if (block.rule) assert.ok(api.rules[block.rule]);
});
