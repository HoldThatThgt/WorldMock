'use strict';

const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '../..');
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

/** @brief 取夹具中的一种请求的深拷贝：action、continuation、legacy_prelude、legacy_action。 */
function request(kind = 'action') {
  return JSON.parse(JSON.stringify(fixture[kind]));
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

test('the fixture holds a seven-layer action, a continuation-shaped request and legacy two-stage requests', () => {
  assert.deepEqual(Object.keys(fixture), ['action', 'continuation', 'legacy_prelude', 'legacy_action']);
  const system = JSON.parse(fixture.action.messages[0].content);
  assert.equal(system.layer, '系统层');
  assert.equal(system.rules.length, 17);
  assert.deepEqual(layers().map(layer => layer.layer), LAYER_NAMES);
  assert.equal(fixture.action.messages.length, 2);
  assert.ok(fixture.continuation.messages.length > 2);
  assert.deepEqual(fixture.continuation.messages.slice(0, 2), fixture.action.messages);
  assert.ok(fixture.legacy_prelude.messages[0].content.includes('你不是AI助手'));
  assert.equal(Object.hasOwn(fixture.legacy_prelude, 'tools'), false);
});

test('action requests are recognised only by the life system layer JSON and an array of tools', () => {
  const {api, errors} = parser();
  assert.deepEqual(plain(api.classify(request())), {stage: 'action', label: '选择动作'});
  assert.equal(api.classify({...request(), tools: []}).stage, 'action');
  const missingTools = request();
  delete missingTools.tools;
  assert.equal(api.classify(missingTools).stage, 'other');
  assert.equal(api.classify({...request(), tools: null}).stage, 'other');
  const readonly = request();
  readonly.messages[0].content = JSON.stringify({layer: '系统层', role: '你是模拟世界中的一个人物，依据人设层与记忆层回答外部问题。',
    rules: []});
  assert.equal(api.classify(readonly).stage, 'other');
  const wrongLayer = request();
  wrongLayer.messages[0].content = wrongLayer.messages[0].content.replace('"系统层"', '"工具层"');
  assert.equal(api.classify(wrongLayer).stage, 'other');
  const brokenJson = request();
  brokenJson.messages[0].content = brokenJson.messages[0].content.slice(0, -1);
  assert.equal(api.classify(brokenJson).stage, 'other');
  const userFirst = request();
  userFirst.messages[0].role = 'user';
  assert.equal(api.classify(userFirst).stage, 'other');
  assert.equal(errors.length, 0);
});

test('legacy prelude and legacy action requests are other calls shown as unparsed', () => {
  const {api, errors} = parser();
  assert.deepEqual(plain(api.classify(request('legacy_prelude'))), {stage: 'other', label: '其他调用 · 未解析'});
  assert.deepEqual(plain(api.classify(request('legacy_action'))), {stage: 'other', label: '其他调用 · 未解析'});
  assert.match(api.classify(request('legacy_prelude'), 'memory_consolidation').label, /其他调用 · memory_consolidation/);
  for (const kind of ['legacy_prelude', 'legacy_action']) {
    const input = event(request(kind));
    const parsed = api.parseRequest(input);
    assert.equal(parsed.stage, 'other');
    assert.equal(parsed.stageLabel, '其他调用 · 未解析');
    assert.equal(messageBlocks(parsed).length, 1);
    assert.equal(messageBlocks(parsed)[0].text, input.data.request.messages[1].content);
    assert.match(messageBlocks(parsed)[0].label, /未解析/);
    assert.ok(parsed.blocks.every(block => block.rule === null));
    assert.ok(parsed.blocks.every(block => block.interpretation === 'recorded'));
    assert.equal(parsed.blocks.some(block => LAYER_KEYS.includes(block.key)), false);
  }
  assert.equal(errors.length, 2);
});

test('the first user message is sliced losslessly into the six top-level layers in contract order', () => {
  const {api, errors} = parser();
  const input = event();
  const parsed = api.parseRequest(input);
  const user = messageBlocks(parsed);
  assert.equal(parsed.stage, 'action');
  assert.deepEqual(plain(user.map(block => block.key)), LAYER_KEYS);
  assert.deepEqual(plain(user.map(block => block.label)), LAYER_NAMES);
  assert.equal(user.map(block => block.text).join(''), input.data.request.messages[1].content);
  assert.ok(user[0].text.startsWith('[{"layer":"工具层"'));
  assert.ok(user.slice(1).every(block => block.text.startsWith(',{"layer":')));
  assert.ok(user.at(-1).text.endsWith('}]'));
  user.forEach((block, index) => assert.deepEqual(plain(block.value), layers()[index]));
  assert.ok(user.every(block => block.interpretation === 'layer' && block.role === 'user'));
  assert.equal(parsed.blocks[0].key, 'system');
  assert.equal(parsed.blocks[0].text, input.data.request.messages[0].content);
  assert.deepEqual(JSON.parse(parsed.blocks.at(-1).text), input.data.request.tools);
  assert.equal(parsed.warnings.length, 0);
  assert.equal(errors.length, 0);
});

test('brackets, commas and escaped quotes inside layer strings never create extra slices', () => {
  const {api} = parser();
  const tricky = layers();
  tricky[5].events[1].text = '好的，],{"layer":"工具层"}, 还有 \\"引号\\" 和 [括号] 🚶';
  tricky[1].identity.name = '林,悦]';
  const input = event(withLayers(tricky));
  const parsed = api.parseRequest(input);
  const user = messageBlocks(parsed);
  assert.deepEqual(plain(user.map(block => block.key)), LAYER_KEYS);
  assert.equal(user.map(block => block.text).join(''), input.data.request.messages[1].content);
  assert.equal(user[5].value.events[1].text, tricky[5].events[1].text);
});

test('wrong layer order, missing layers, unparsable slices and whitespace framing stay unparsed', () => {
  const {api, errors} = parser();
  const base = layers();
  const swapped = [base[1], base[0], ...base.slice(2)];
  const texts = [JSON.stringify(swapped), JSON.stringify(base.slice(0, 5)), JSON.stringify([...base, base[5]]),
    JSON.stringify(base).slice(0, -1), ' ' + JSON.stringify(base), JSON.stringify(base) + '\n',
    '[]', JSON.stringify(base.map(layer => ({...layer, layer: undefined})))];
  for (const text of texts) {
    const input = event();
    input.data.request.messages[1].content = text;
    const parsed = api.parseRequest(input);
    assert.equal(parsed.stage, 'action');
    assert.equal(messageBlocks(parsed).length, 1, text.slice(0, 40));
    assert.equal(messageBlocks(parsed)[0].text, text);
    assert.match(messageBlocks(parsed)[0].label, /未解析/);
    assert.equal(parsed.warnings.length, 1);
  }
  assert.equal(errors.length, texts.length);
});

test('structured first user content and extra message fields stay as complete unparsed JSON', () => {
  const {api, errors} = parser();
  const text = fixture.action.messages[1].content;
  for (const message of [{role: 'user', content: [{type: 'text', text}]}, {role: 'user', name: 'Lin', content: text}]) {
    const input = event();
    input.data.request.messages[1] = message;
    const parsed = api.parseRequest(input);
    assert.equal(messageBlocks(parsed).length, 1);
    assert.equal(messageBlocks(parsed)[0].text, JSON.stringify(message));
    assert.match(parsed.warnings[0], /未解析/);
  }
  assert.equal(errors.length, 2);
});

test('tool layer drill-down lists every target with its actions and tools and their availability', () => {
  const {api} = parser();
  const parsed = api.parseRequest(event());
  const tool = parsed.blocks.find(block => block.key === 'tool');
  const items = plain(api.drillItems(tool));
  const targets = layers()[0].targets;
  const expected = targets.reduce((sum, target) => sum + 1 + target.actions.length + target.tools.length, 0);
  assert.equal(items.length, expected);
  assert.equal(items[0].label, `${targets[0].name}（activity）`);
  assert.deepEqual(JSON.parse(items[0].text), targets[0]);
  assert.ok(items.some(item => item.label === '工作：季度报告 · submit_work · 不可用：先结束正在做的工作'));
  assert.ok(items.some(item => item.label === '星巴克出售的拿铁 · obtain：远程下单 · 可用'));
  assert.ok(items.some(item => item.label === '星巴克出售的拿铁 · obtain：到店 · 不可用：不在这家店'));
  const contract = items.find(item => item.label === '工作：季度报告（task）');
  assert.deepEqual(JSON.parse(contract.text).contract, targets[1].contract);
});

test('progress drill-down follows real record fields and labels self_note as unverified', () => {
  const {api} = parser();
  const parsed = api.parseRequest(event());
  const progress = parsed.blocks.find(block => block.key === 'progress');
  const items = plain(api.drillItems(progress));
  const records = layers()[5].events;
  assert.equal(items.length, records.length);
  assert.equal(items[0].label, '2026-09-28T09:05:00+08:00 · tool_call · start_activity');
  assert.equal(items[1].label, '2026-09-28T10:20:00+08:00 · message_arrival · 王磊');
  assert.equal(items[2].label, '2026-09-28T10:31:00+08:00 · tool_call · start_activity · self_note（未经核验）');
  assert.ok(items[2].text.startsWith('self_note（未经核验）：先给王磊点上，他说想吃牛肉面。\n\n'));
  assert.deepEqual(JSON.parse(items[2].text.split('\n\n').slice(1).join('\n\n')), records[2]);
  assert.equal(items[0].text.includes('未经核验'), false);
  assert.deepEqual(plain(api.drillItems(parsed.blocks.find(block => block.key === 'persona'))), []);
  assert.deepEqual(plain(api.drillItems(parsed.blocks.find(block => block.key === 'system'))), []);
});

test('a continuation-shaped first request keeps its six layers and shows the extra messages as full blocks', () => {
  const {api, errors} = parser();
  const input = event(request('continuation'));
  const parsed = api.parseRequest(input);
  assert.equal(parsed.stage, 'action');
  assert.deepEqual(plain(messageBlocks(parsed).map(block => block.key)), LAYER_KEYS);
  const extra = parsed.blocks.filter(block => block.messageIndex > 1);
  assert.deepEqual(plain(extra.map(block => block.role)), ['assistant', 'tool']);
  input.data.request.messages.slice(2).forEach((message, offset) => {
    assert.equal(extra[offset].text, JSON.stringify(message));
    assert.equal(extra[offset].interpretation, 'recorded');
    assert.equal(extra[offset].rule, 'message');
  });
  assert.ok(extra[0].text.includes('reasoning_content'));
  assert.equal(new Set(parsed.blocks.map(block => block.id)).size, parsed.blocks.length);
  assert.equal(errors.length, 0);
});

test('semantic parsing locates the first user rather than assuming message index one', () => {
  const {api} = parser();
  const input = event();
  input.data.request.messages.splice(1, 0, {role: 'system', content: 'Additional system information.'});
  const parsed = api.parseRequest(input);
  assert.equal(messageBlocks(parsed, 1)[0].text, 'Additional system information.');
  assert.equal(messageBlocks(parsed, 1)[0].label, '系统消息');
  assert.deepEqual(plain(messageBlocks(parsed, 2).map(block => block.key)), LAYER_KEYS);
});

test('explicit empty tools remain visible while missing tools contribute no placeholder', () => {
  const {api} = parser();
  const parsed = api.parseRequest(event({...request(), tools: []}));
  assert.equal(parsed.stage, 'action');
  assert.equal(parsed.blocks.at(-1).text, '[]');
  assert.equal(parsed.toolChars, 2);
  const missing = request();
  delete missing.tools;
  assert.equal(api.parseRequest(event(missing)).toolChars, 0);
});

test('Unicode character totals count every message once including separators', () => {
  const {api} = parser();
  const tricky = layers();
  tricky[1].identity.name = '林悦🚶';
  const input = event(withLayers(tricky));
  const parsed = api.parseRequest(input);
  const expected = input.data.request.messages.reduce((sum, message) => sum + Array.from(message.content).length, 0);
  assert.equal(parsed.messageChars, expected);
  const persona = parsed.blocks.find(block => block.key === 'persona');
  assert.equal(persona.chars, Array.from(persona.text).length);
  assert.notEqual(persona.chars, persona.text.length);
  assert.equal(parsed.toolChars, Array.from(JSON.stringify(input.data.request.tools)).length);
});

test('malformed request envelope preserves the archived payload and does not fabricate messages', () => {
  const {api, errors} = parser();
  const input = {event_type: 'model_request', data: {request: {messages: null, tools: []}}};
  const parsed = api.parseRequest(input);
  assert.equal(parsed.stage, 'other');
  assert.equal(parsed.blocks[0].text, JSON.stringify(input));
  assert.equal(parsed.blocks[1].key, 'tools');
  assert.equal(errors.length, 1);
});

test('comparison detects same-length layer edits and preserves both complete versions', () => {
  const {api} = parser();
  const edited = layers();
  edited[4].body.appetite = '有点撑';
  const before = api.parseRequest(event());
  const after = api.parseRequest(event(withLayers(edited)));
  const changes = api.compare(after.blocks, before.blocks);
  const changed = changes.find(block => block.key === 'environment');
  assert.equal(changed.status, 'changed');
  assert.equal(changed.delta, 0);
  assert.equal(changed.before.text, before.blocks.find(block => block.key === 'environment').text);
  assert.ok(changed.after.text.includes('有点撑'));
  assert.equal(changes.find(block => block.key === 'tool').status, 'unchanged');
  assert.equal(changes.find(block => block.key === 'system').status, 'unchanged');
});

test('comparison distinguishes additions and exits using stable identities', () => {
  const {api} = parser();
  const before = api.parseRequest(event());
  const after = api.parseRequest(event(request('continuation')));
  const changes = api.compare(after.blocks, before.blocks);
  const added = changes.filter(block => block.status === 'added');
  assert.deepEqual(plain(added.map(block => block.id)), ['message:2:message', 'message:3:message']);
  assert.ok(added.every(block => block.before === null && block.delta === block.after.chars));
  const exited = api.compare(before.blocks, after.blocks).filter(block => block.status === 'removed');
  assert.equal(exited.length, 2);
  assert.ok(exited.every(block => block.after === null && block.delta === -block.before.chars));
});

test('all parsed block rules exist and every growth view retains explicit source and steps', () => {
  const {api} = parser();
  const parsed = api.parseRequest(event(request('continuation')));
  assert.ok(parsed.blocks.every(block => api.rules[block.rule]));
  for (const key of ['system', ...LAYER_KEYS, 'tools', 'message', 'capacity']) assert.ok(api.rules[key], key);
  for (const rule of Object.values(api.rules)) {
    assert.ok(rule.label && rule.source && rule.fill);
    assert.ok(rule.steps.length >= 3);
    assert.ok(rule.steps.every(step => step.length === 2 && step.every(value => typeof value === 'string')));
    assert.equal(rule.source.startsWith('/'), false);
  }
  assert.match(api.rules.progress.steps.map(step => step[1]).join(''), /self_note.*未经核验/);
  assert.match(api.rules.capacity.fill, /不是 token/);
  const retired = /自述|scene|facts|decision_statement|render_scene|两阶段/;
  assert.equal(retired.test(JSON.stringify(plain(api.rules))), false);
});
