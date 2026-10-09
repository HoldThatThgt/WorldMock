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

test('genuine current requests have seven layers and ten offered tools', () => {
  const {api, errors} = parser();
  const parsed = api.parseRequest(event());
  assert.equal(parsed.stage, 'action');
  assert.deepEqual(plain(messageBlocks(parsed).map(block => block.key)), LAYER_KEYS);
  assert.equal(messageBlocks(parsed).map(block => block.text).join(''), request().messages[1].content);
  assert.deepEqual(layers().map(layer => layer.layer), LAYER_NAMES);
  assert.equal(fixture.action.tools.length, 10);
  assert.deepEqual(fixture.continuation.tools, fixture.action.tools);
  assert.deepEqual(fixture.continuation.messages.slice(0, 2), fixture.action.messages);
  assert.equal(parsed.warnings.length, 0);
  assert.equal(errors.length, 0);
});

test('the current tool layer contains only the seven actual actions with lossless target drills', () => {
  const {api} = parser();
  const parsed = api.parseRequest(event());
  const tool = parsed.blocks.find(block => block.key === 'tool');
  const groups = tool.value.groups;
  assert.deepEqual([...new Set(groups.map(group => group.action).filter(Boolean))].sort(),
    ['drive', 'interact', 'purchase', 'sleep', 'take_bus', 'take_taxi', 'walk']);
  const items = plain(api.drillItems(tool));
  for (const group of groups) for (const entry of group.targets) {
    const merged = {...group.shared, ...entry, kind:group.kind};
    assert.ok(items.some(item => item.text === JSON.stringify(merged, null, 2)));
    if (group.action !== null) assert.ok(items.some(item => item.label.startsWith(
      `${entry.name} · ${group.action} · `)));
    assert.equal(Object.hasOwn(group, 'way'), false);
  }
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
