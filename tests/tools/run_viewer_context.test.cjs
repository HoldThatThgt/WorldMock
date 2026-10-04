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
  assert.ok(system.rules.length > 0);
  assert.deepEqual(layers().map(layer => layer.layer), LAYER_NAMES);
  assert.equal(fixture.action.messages.length, 2);
  assert.ok(fixture.continuation.messages.length > 2);
  assert.deepEqual(fixture.continuation.messages.slice(0, 2), fixture.action.messages);
  assert.ok(fixture.legacy_prelude.messages[0].content.includes('你不是AI助手'));
  assert.equal(Object.hasOwn(fixture.legacy_prelude, 'tools'), false);
});

test('the viewer life role and the fixture system layer match the host life system layer', () => {
  const role = /const LIFE_ROLE = '([^']+)';/.exec(source)[1];
  // 合并 Python 源码中跨行隐式拼接的字符串字面量，再按原文比较。
  const host = read('custom/agents/life_prompt.py').replace(/(["'])\s*\n\s*\1/g, '');
  assert.ok(host.includes(role), 'custom/agents/life_prompt.py must define the same life role text');
  const system = JSON.parse(fixture.action.messages[0].content);
  assert.equal(system.role, role);
  for (const rule of system.rules) assert.ok(host.includes(rule), rule);
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

test('legacy decision statement requests stay other calls shown as unparsed', () => {
  const {api, errors} = parser();
  assert.deepEqual(plain(api.classify(request('legacy_prelude'))), {stage: 'other', label: '其他调用·未解析'});
  assert.match(api.classify(request('legacy_prelude'), 'memory_consolidation').label, /其他调用·memory_consolidation/);
  const input = event(request('legacy_prelude'));
  const parsed = api.parseRequest(input);
  assert.equal(parsed.stage, 'other');
  assert.equal(parsed.stageLabel, '其他调用·未解析');
  assert.equal(messageBlocks(parsed).length, 1);
  assert.equal(messageBlocks(parsed)[0].text, input.data.request.messages[1].content);
  assert.match(messageBlocks(parsed)[0].label, /未解析/);
  assert.ok(parsed.blocks.every(block => block.rule === null));
  assert.ok(parsed.blocks.every(block => block.interpretation === 'recorded'));
  assert.equal(parsed.blocks.some(block => LAYER_KEYS.includes(block.key)), false);
  assert.equal(errors.length, 1);
});

test('legacy action requests are recognised by the old template and segmented as unverified inferences', () => {
  const {api, errors} = parser();
  assert.deepEqual(plain(api.classify(request('legacy_action'))), {stage: 'legacy', label: '选择动作·旧模板'});
  const input = event(request('legacy_action'));
  const parsed = api.parseRequest(input);
  assert.equal(parsed.stage, 'legacy');
  assert.equal(parsed.continuation, false);
  const user = messageBlocks(parsed);
  assert.deepEqual(plain(user.map(block => block.key)),
    ['persona', 'scene', 'subjective_summary', 'recent_subjective_episodes', 'facts', 'decision_statement']);
  assert.equal(user.map(block => block.text).join(''), input.data.request.messages[1].content);
  assert.ok(user.every(block => block.interpretation === 'template' && block.label.endsWith(' · 来源未核验')));
  assert.equal(user[0].label, '人物卡片 · 来源未核验');
  const system = parsed.blocks.find(block => block.messageIndex === 0);
  assert.equal(system.label, '身份规则');
  assert.equal(system.text, input.data.request.messages[0].content);
  assert.ok(parsed.blocks.every(block => block.rule === null), 'current source rules do not describe old templates');
  assert.equal(parsed.blocks.find(block => block.key === 'tools').text, JSON.stringify(input.data.request.tools));
  assert.equal(errors.length, 0);
});

test('a legacy action body that happens to contain a template heading stays an inference or falls back to unparsed', () => {
  const {api, errors} = parser();
  const once = request('legacy_action');
  const [persona, ...rest] = once.messages[1].content.split('\n\n');
  const scene = rest.shift();
  once.messages[1].content = [persona, scene, '主观归纳：\n正文里恰好写了这个标题。', ...rest.slice(2)].join('\n\n');
  const inferred = messageBlocks(api.parseRequest(event(once)));
  const summary = inferred.find(block => block.key === 'subjective_summary');
  assert.equal(summary.label, '主观归纳 · 来源未核验');
  assert.equal(summary.interpretation, 'template');
  assert.equal(inferred.map(block => block.text).join(''), once.messages[1].content);
  const twice = request('legacy_action');
  twice.messages[1].content = twice.messages[1].content.replace('\n\n最近的人物自述：\n', '\n\n主观归纳：\n再次出现。\n\n最近的人物自述：\n');
  const parsed = api.parseRequest(event(twice));
  assert.equal(parsed.stage, 'legacy');
  assert.equal(messageBlocks(parsed).length, 1);
  assert.equal(messageBlocks(parsed)[0].text, twice.messages[1].content);
  assert.match(messageBlocks(parsed)[0].label, /未解析/);
  assert.equal(parsed.warnings.length, 1);
  const budgetless = request('legacy_action');
  budgetless.messages[0].content = budgetless.messages[0].content.split('\n\nAuthoritative ReAct')[0];
  assert.equal(api.classify(budgetless).stage, 'other');
  assert.equal(errors.length, 1);
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
  const groups = layers()[0].groups;
  const targets = groups.flatMap(group => group.targets.map(row => ({...group.shared, ...row, kind:group.kind})));
  const expected = groups.reduce((sum, group) => sum + group.targets.reduce((count, row) =>
    count + 1 + (group.action === null ? 0 : 1) + (row.tools || group.shared.tools || []).length, 0), 0);
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

test('a decision first request with sources is a continuation: source decisions and returned thinking listed once', () => {
  const {api, errors} = parser();
  const input = event(request('continuation'));
  const [assistantMessage] = input.data.request.messages.slice(2);
  const parsed = api.parseRequest(input, {sources: {call_7f2c: '10:43:00 · decision-k'}});
  assert.equal(parsed.continuation, true);
  assert.deepEqual(plain(messageBlocks(parsed).map(block => block.key)), LAYER_KEYS);
  const assistant = parsed.blocks.find(block => block.messageIndex === 2 && block.key === 'message');
  assert.equal(assistant.label, '续接 · assistant · 来源决策 10:43:00 · decision-k');
  assert.equal(assistant.rule, 'continuation');
  assert.equal(assistant.text, JSON.stringify(assistantMessage));
  const reasoning = parsed.blocks.find(block => block.key === 'reasoning');
  assert.equal(reasoning.text, assistantMessage.reasoning_content);
  assert.equal(reasoning.label, '回传思考 · 来源决策 10:43:00 · decision-k');
  assert.equal(reasoning.interpretation, 'reasoning');
  assert.equal(parsed.blocks.find(block => block.messageIndex === 3).label, '续接 · tool · 完整消息');
  assert.equal(parsed.messageChars, api.parseRequest(input).messageChars, 'returned thinking is not counted twice');
  assert.equal(new Set(parsed.blocks.map(block => block.id)).size, parsed.blocks.length);
  assert.ok(parsed.blocks.every(block => api.rules[block.rule]));
  const unmatched = api.parseRequest(input, {sources: {}});
  assert.equal(unmatched.blocks.find(block => block.key === 'reasoning').label, '回传思考 · 来源决策 未匹配');
  assert.equal(api.parseRequest(event(), {sources: {}}).continuation, false);
  assert.equal(api.parseRequest(input).continuation, false);
  assert.deepEqual(plain(api.prefixCallIds(input.data.request)), ['call_7f2c']);
  assert.deepEqual(plain(api.prefixCallIds(request())), []);
  assert.equal(errors.length, 0);
});

test('a continuation prefix reply body written by an earlier decision is labelled unverified', () => {
  const {api} = parser();
  const input = event(request('continuation'));
  const sources = {sources: {call_7f2c: '10:43:00 · decision-k'}};
  const silent = api.parseRequest(input, sources).blocks.find(block => block.messageIndex === 2 && block.key === 'message');
  assert.equal(silent.label.includes('未经核验'), false, 'no reply body, no label');
  input.data.request.messages[2].content = '先把王磊的面点上，报告回头再核对。';
  const replied = api.parseRequest(input, sources).blocks.find(block => block.messageIndex === 2 && block.key === 'message');
  assert.equal(replied.label, '续接 · assistant · 来源决策 10:43:00 · decision-k · 回复正文（未经核验）');
  assert.equal(replied.text, JSON.stringify(input.data.request.messages[2]), 'the recorded message stays verbatim');
  input.data.request.messages[2].content = '   ';
  const blank = api.parseRequest(input, sources).blocks.find(block => block.messageIndex === 2 && block.key === 'message');
  assert.equal(blank.label.includes('未经核验'), false);
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
  const capacity = api.rules.capacity.steps.map(step => step[1]);
  const endChain = capacity.findIndex(text => text.includes('先提前结束本秒续接'));
  assert.ok(endChain >= 0, 'budget step 0 ends the same-second continuation first');
  assert.ok(endChain < capacity.findIndex(text => text.includes('condensed')), 'step 0 precedes progress condensing');
  assert.equal(api.rules.memory.source, 'src/worldmock/life/memory_layer.py · memory_view');
  assert.match(api.rules.memory.steps.map(step => step[1]).join(''), /8000 字符/);
  assert.match(api.rules.persona.steps.map(step => step[1]).join(''), /按名称合并/);
  assert.equal(/当前阶段|尚未派生/.test(JSON.stringify(plain([api.rules.memory, api.rules.persona]))), false);
  const retired = /自述|scene|facts|decision_statement|render_scene|两阶段/;
  assert.equal(retired.test(JSON.stringify(plain(api.rules))), false);
});

/** @brief 一条规则的全部说明文字：填充说明与各步的说明相连。 */
function ruleText(rule) {
  return [rule.fill, ...plain(rule.steps).map(step => step[1])].join('');
}

test('the progress rule holds the two name-only errand records and keeps them out of the memory layer', () => {
  const {api} = parser();
  const progress = ruleText(api.rules.progress);
  // 两种记录的类型名取自进度层的构造源码，规则文字须逐个点名。
  const builder = read('src/worldmock/life/progress_layer.py');
  const records = /_ERRAND_RECORDS = \{REMINDED_KIND: "([a-z_]+)", COMPLETED_KIND: "([a-z_]+)"\}/.exec(builder).slice(1);
  assert.deepEqual(records, ['errand_reminder', 'errand_completed']);
  for (const record of records) assert.ok(progress.includes(record), record);
  assert.match(api.rules.progress.fill, /事务提醒与办成两种只写事务名称的记录/);
  assert.match(progress, /事务提醒到点（errand_reminder）/);
  assert.match(progress, /环境确认办成（errand_completed）/);
  assert.match(progress, /只写事务名称/);
  assert.match(progress, /二者不迁入记忆层/);
  assert.match(progress, /self_note.*未经核验/, 'the earlier progress rules stay');
});

test('the todo and environment rules state where open errands and duty come from as the growth clause does', () => {
  const {api} = parser();
  const todo = ruleText(api.rules.todo);
  assert.match(todo, /未完成的个人事务取自人物状态，跨零点保留/);
  assert.match(todo, /办成或取消即移出/);
  const environment = ruleText(api.rules.environment);
  assert.match(environment, /duty 由场景工作安排与世界时钟求值，今日计划改不了它/);
  assert.match(environment, /没有工作安排为 null/);
  const clause = read('docs/dev/SPEC-run-viewer-context.md');
  for (const phrase of ['待办层的未完成个人事务取自人物状态，跨零点保留，办成或取消后退出',
    '环境层的 `duty` 由场景工作安排与世界时钟求值，今日计划改不了它',
    '含事务提醒与办成两种只写名称的记录，二者不迁入记忆层']) assert.ok(clause.includes(phrase), phrase);
});

/** @brief Python 字典字面量里逐个写出的字符串键（按出现顺序）。 */
function literalKeys(block) {
  return [...block.matchAll(/"([a-z_]+)":/g)].map(match => match[1]);
}

/** @brief 宿主源码登记的工具面：世界请求工具、世界查询工具与记忆查询工具，各按宿主发给模型的顺序。 */
function hostToolSurface() {
  const agent = read('custom/agents/life_agent.py');
  const models = read('src/worldmock/life/models.py');
  const requests = literalKeys(/^REQUEST_MODELS: [^\n]*\{\n([\s\S]*?)^\}/m.exec(models)[1]);
  const memory = literalKeys(/^_MEMORY_TOOL_MODELS: [^\n]*\{\n([\s\S]*?)^\}/m.exec(agent)[1]);
  const worldQueries = literalKeys(/^_QUERY_TOOL_MODELS = \{([\s\S]*?)\}/m.exec(agent)[1]);
  return {worldRequests: requests.filter(name => !worldQueries.includes(name)), worldQueries, memory};
}

test('the tool definition rule counts the tool surface the host offers and when query tools are withheld', () => {
  const {api} = parser();
  const {worldRequests, worldQueries, memory} = hostToolSurface();
  assert.deepEqual(worldQueries, ['recall_task_summary', 'query_world']);
  assert.deepEqual([worldRequests.length, worldQueries.length, memory.length], [7, 2, 3]);
  const numerals = {2: '两', 3: '三', 7: '七'};
  const fill = api.rules.tools.fill;
  assert.ok(fill.includes(`${numerals[worldRequests.length]}个世界请求工具`), fill);
  assert.ok(fill.includes(`${numerals[worldQueries.length]}个世界查询工具`), fill);
  assert.ok(fill.includes(`${numerals[memory.length]}个记忆查询工具`), fill);
  for (const name of worldQueries) assert.ok(fill.includes(name), name);
  assert.equal(fill.includes('八个世界请求工具'), false);
  // 最后一个回合与截断重试去掉的是全部查询工具（世界查询与记忆查询），规则文字不得只说记忆查询。
  const last = plain(api.rules.tools.steps).find(step => step[0] === '最后回合')[1];
  assert.match(last, /只剩最后一个回合时与输出截断后的那次重试时去掉全部查询工具/);
  assert.match(last, /世界查询与记忆查询/);
});

test('progress drill-down labels the two errand records with the errand name they carry', () => {
  const {api} = parser();
  const records = layers()[5].events.filter(record => record.type.startsWith('errand_'));
  assert.deepEqual(records, [{occurred_at: '2026-09-28T10:35:00+08:00', type: 'errand_completed', errand: '囤咖啡豆'},
    {occurred_at: '2026-09-28T10:40:00+08:00', type: 'errand_reminder', errand: '买牛奶'}]);
  const parsed = api.parseRequest(event());
  const items = plain(api.drillItems(parsed.blocks.find(block => block.key === 'progress')));
  assert.deepEqual(items.slice(-2).map(item => item.label), [
    '2026-09-28T10:35:00+08:00 · errand_completed · 囤咖啡豆', '2026-09-28T10:40:00+08:00 · errand_reminder · 买牛奶']);
  assert.deepEqual(items.slice(-2).map(item => JSON.parse(item.text)), records);
});

test('the fixture action request is in the layer shape of the errands and workday feature and parses unchanged', () => {
  const {api, errors} = parser();
  const [, , , todo, environment, progress] = layers();
  // 键序与行的键取自各层的构造源码：待办层增 errands（六键一行），环境层增 duty，进度层增两种事务记录。
  const todoBuilder = read('src/worldmock/life/todo_layer.py');
  assert.deepEqual(Object.keys(todo), literalKeys(/return \{"layer": "待办层"[\s\S]*?\}\n/.exec(todoBuilder)[0]));
  assert.deepEqual(Object.keys(todo), ['layer', 'paused_activities', 'errands', 'day_plan']);
  const rowBuilder = /def _row\([\s\S]*?return (\{[\s\S]*?\})\n\n/.exec(read('src/worldmock/life/errands.py'))[1];
  assert.deepEqual(literalKeys(rowBuilder), ['name', 'done_when', 'progress', 'memo', 'remind_time', 'noted_on']);
  assert.ok(todo.errands.length >= 2);
  for (const row of todo.errands) assert.deepEqual(Object.keys(row), literalKeys(rowBuilder));
  assert.deepEqual(todo.errands.map(row => row.remind_time), [null, '18:30'], 'a reminded errand and a pending one');
  const environmentBuilder = read('src/worldmock/life/environment_layer.py');
  assert.deepEqual(Object.keys(environment),
    literalKeys(/return \{"layer": "环境层"[\s\S]*?"duty": _duty\(context\)\}/.exec(environmentBuilder)[0]));
  assert.equal(Object.keys(environment).at(-1), 'duty');
  const schedule = read('src/worldmock/life/work_schedule.py');
  assert.deepEqual(Object.keys(environment.duty),
    [...literalKeys(/return \{"day": DAY_WORK[\s\S]*?\}\n/.exec(schedule)[0]), 'status']);
  assert.ok(/return \{\*\*arrangement, "status": /.test(schedule), 'duty adds status to the day arrangement');
  assert.deepEqual(progress.events.map(record => record.type),
    ['tool_call', 'message_arrival', 'tool_call', 'errand_completed', 'errand_reminder']);
  // 原生工具是宿主此刻发给模型的十二个：七个世界请求、两个世界查询（含 query_world）、三个记忆查询，顺序相同。
  const {worldRequests, worldQueries, memory} = hostToolSurface();
  const names = fixture.action.tools.map(tool => tool.function.name);
  assert.deepEqual(names, [...worldRequests, ...worldQueries, ...memory]);
  assert.ok(names.includes('query_world'));
  for (const name of ['report_status', 'pause_activity', 'conclude_activity']) {
    const parameters = fixture.action.tools.find(tool => tool.function.name === name).function.parameters;
    assert.deepEqual(parameters.required, ['items', 'errands'], name);
  }
  assert.deepEqual(fixture.continuation.tools, fixture.action.tools);
  // 行动请求识别、六层切片与层序不随这些键改变：切片不校验层内的键。
  const input = event();
  assert.deepEqual(plain(api.classify(input.data.request)), {stage: 'action', label: '选择动作'});
  const parsed = api.parseRequest(input);
  const user = messageBlocks(parsed);
  assert.deepEqual(plain(user.map(block => block.key)), LAYER_KEYS);
  assert.equal(user.map(block => block.text).join(''), input.data.request.messages[1].content);
  assert.deepEqual(plain(user[3].value.errands), todo.errands);
  assert.deepEqual(plain(user[4].value.duty), environment.duty);
  assert.deepEqual(JSON.parse(parsed.blocks.at(-1).text).map(tool => tool.function.name), names);
  assert.equal(parsed.warnings.length, 0);
  // 没有 errands 与 duty 的既有七层请求照旧识别与切片，与现行请求对照时只有这两层算内容变化。
  const earlier = layers();
  delete earlier[3].errands;
  delete earlier[4].duty;
  const before = api.parseRequest(event(withLayers(earlier)));
  assert.equal(before.stage, 'action');
  assert.deepEqual(plain(messageBlocks(before).map(block => block.key)), LAYER_KEYS);
  const changed = plain(api.compare(parsed.blocks, before.blocks)).filter(row => row.status !== 'unchanged');
  assert.deepEqual(changed.map(row => [row.key, row.status]), [['todo', 'changed'], ['environment', 'changed']]);
  assert.equal(errors.length, 0);
});
