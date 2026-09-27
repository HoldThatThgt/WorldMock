'use strict';

const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.join(__dirname, '../..');
const source = readFileSync(path.join(root, 'tools/run-viewer-context.js'), 'utf8');
const agent = readFileSync(path.join(root, 'custom/agents/life_agent.py'), 'utf8');
const template = /_LIFE_INSTRUCTIONS = """([\s\S]*?)"""\.strip\(\)/.exec(agent)[1].trim();
const life = template.replace('{name}', '林悦');
const prelude = life + '\n\n请用一至三句第一人称中文，说出你此刻的感受和打算，最多二百汉字。';
const action = life + '\n\nAuthoritative ReAct model-turn budget:\n'
  + '{"phase":"before_action","remaining_model_turns_including_current":15}';
const persona = '软件工程师。\n喜欢咖啡☕和散步🚶。\n\n这段空行仍是人物原文。';
const scene = '现在是 2026 年 9 月 28 日星期一 10:35（Unix epoch：当前 1790562900）。你在办公室。';
const experiences = '已核验经历：\n10:20–10:25 等待了五分钟';
const summary = '主观归纳：\n我想吃点热的。\n\n这仍然是同一条归纳。';
const episodes = '最近的人物自述：\n- [subjective] 我答应给同事点饭。\n- [subjective] 先忙完工作。';
const facts = JSON.stringify({current_time: '2026-09-28T10:35:00+08:00', inventory: [{name: '米饭', quantity: 0.25}],
  progress: {results: [{message: 'Order accepted'}]}, experiences: ['10:20–10:25 等待了五分钟']});
const statement = '你刚才心里想的是：给他点饭，再继续干活。';

/** @brief 在独立上下文执行真实解析模块，同时收集解析失败日志。 */
function parser() {
  const errors = [];
  const context = vm.createContext({console: {error: (...args) => errors.push(args.join(' '))}});
  vm.runInContext(source, context, {filename: 'run-viewer-context.js'});
  return {api: vm.runInContext('ViewerContext', context), errors};
}

/** @brief 生成仅含实际现行字段的归档请求事件，省略工具字段和空工具数组可分别测试。 */
function event(stage = 'prelude', sections = [persona, scene], extra = {}) {
  const request = {messages: [{role: 'system', content: stage === 'action' ? action : prelude},
    {role: 'user', content: sections.join('\n\n')}]};
  if (stage === 'action') request.tools = [{type: 'function', function: {name: 'start_activity',
    description: 'Start an activity.', parameters: {type: 'object', properties: {target: {type: 'string'}}}}}];
  return {event_type: 'model_request', data: {request: {...request, ...extra}}};
}

/** @brief 取得某条消息的全部内容块，便于检验原文拼接。 */
function messageBlocks(parsed, index = 1) {
  return parsed.blocks.filter(block => block.messageIndex === index);
}

/** @brief 使用原生对象比较跨上下文返回值，不依赖 VM 原型身份。 */
function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test('current source system classifies both stages without user or full tool bodies', () => {
  const {api} = parser();
  assert.deepEqual(plain(api.classify({messages: [{role: 'system', content: prelude}]})),
    {stage: 'prelude', label: '形成自述'});
  assert.deepEqual(plain(api.classify({messages: [{role: 'system', content: action}], tools: []})),
    {stage: 'action', label: '选择动作'});
  const low = action.replace('\n\nAuthoritative',
    '\n\n剩余回合已经不多：当前阶段是before_action，请尽快提交本阶段需要的动作或结束。\n\nAuthoritative');
  assert.equal(api.classify({messages: [{role: 'system', content: low}], tools: []}).stage, 'action');
});

test('unrelated no-tool and tool calls are other stages even with familiar labels', () => {
  const {api} = parser();
  const other = {messages: [{role: 'system', content: 'Summarize memories.'}]};
  assert.equal(api.classify(other, 'memory_consolidation').stage, 'other');
  assert.match(api.classify(other, 'memory_consolidation').label, /memory_consolidation/);
  assert.equal(api.classify({...other, tools: []}).stage, 'other');
  assert.equal(api.classify({messages: [{role: 'system', content: prelude}], tools: []}).stage, 'other');
  assert.equal(api.classify({messages: [{role: 'system', content: action}]}).stage, 'other');
  assert.equal(api.classify({messages: [{role: 'system', content: prelude.replace('会饿', '不饿')}]}).stage,
    'other');
});

test('prelude segmentation is lossless with optional memories and internal blank lines', () => {
  const {api, errors} = parser();
  const input = event('prelude', [persona, scene, experiences, summary, episodes]);
  const parsed = api.parseRequest(input);
  const user = messageBlocks(parsed);
  assert.deepEqual(plain(user.map(block => block.key)),
    ['persona', 'scene', 'verified_experiences', 'subjective_summary', 'recent_subjective_episodes']);
  assert.equal(user.map(block => block.text).join(''), input.data.request.messages[1].content);
  assert.equal(user[0].text, persona);
  assert.equal(user[1].text, '\n\n' + scene);
  assert.equal(user[3].text, '\n\n' + summary);
  assert.ok(user.every(block => block.interpretation === 'template'));
  assert.equal(parsed.toolChars, 0);
  assert.equal(parsed.blocks.some(block => block.key === 'tools'), false);
  assert.equal(errors.length, 0);
});

test('a title inside scene message text remains a template interpretation without verified provenance', () => {
  const {api} = parser();
  const sceneWithMessage = scene + '收件箱最新消息：\n\n主观归纳：\n先给王磊点饭。有待办任务可以推进。';
  const input = event('prelude', [persona, sceneWithMessage]);
  const parsed = api.parseRequest(input);
  const user = messageBlocks(parsed);
  assert.deepEqual(plain(user.map(block => block.key)), ['persona', 'scene', 'subjective_summary']);
  assert.equal(user.map(block => block.text).join(''), input.data.request.messages[1].content);
  assert.ok(user.every(block => block.interpretation === 'template'));
  assert.equal(user.find(block => block.key === 'subjective_summary').interpretation, 'template');
  assert.equal(parsed.blocks[0].interpretation, 'recorded');
  assert.equal(parsed.blocks.some(block => block.interpretation === 'verified'), false);
});

test('empty optional memory sections are omitted rather than invented', () => {
  const {api} = parser();
  for (const optional of [[], [experiences], [summary], [episodes], [experiences, episodes]]) {
    const input = event('prelude', [persona, scene, ...optional]);
    const parsed = api.parseRequest(input);
    assert.equal(parsed.warnings.length, 0);
    assert.equal(messageBlocks(parsed).length, 2 + optional.length);
    assert.equal(messageBlocks(parsed).map(block => block.text).join(''), input.data.request.messages[1].content);
  }
});

test('action segmentation preserves compact facts, statement and complete tools', () => {
  const {api} = parser();
  const input = event('action', [persona, scene, summary, episodes, facts, statement]);
  const parsed = api.parseRequest(input);
  assert.equal(parsed.stage, 'action');
  assert.deepEqual(plain(messageBlocks(parsed).map(block => block.key)),
    ['persona', 'scene', 'subjective_summary', 'recent_subjective_episodes', 'facts', 'decision_statement']);
  assert.equal(messageBlocks(parsed).map(block => block.text).join(''), input.data.request.messages[1].content);
  const tools = parsed.blocks.at(-1);
  assert.equal(tools.key, 'tools');
  assert.equal(tools.interpretation, 'recorded');
  assert.deepEqual(JSON.parse(tools.text), input.data.request.tools);
  assert.equal(parsed.blocks.find(block => block.key === 'facts').text, '\n\n' + facts);
});

test('explicit empty tools remain visible while missing tools contribute no placeholder', () => {
  const {api} = parser();
  const parsed = api.parseRequest(event('action', [persona, scene, facts], {tools: []}));
  assert.equal(parsed.blocks.at(-1).text, '[]');
  assert.equal(parsed.blocks.at(-1).chars, 2);
  assert.equal(parsed.toolChars, 2);
  assert.equal(api.parseRequest(event()).toolChars, 0);
});

test('Unicode character totals include separators exactly once', () => {
  const {api} = parser();
  const input = event('action', [persona, scene, facts, statement]);
  const parsed = api.parseRequest(input);
  const expected = input.data.request.messages.reduce((sum, message) => sum + Array.from(message.content).length, 0);
  assert.equal(parsed.messageChars, expected);
  assert.equal(parsed.blocks.find(block => block.key === 'persona').chars, Array.from(persona).length);
  assert.notEqual(parsed.blocks.find(block => block.key === 'persona').chars, persona.length);
  assert.equal(parsed.toolChars, Array.from(JSON.stringify(input.data.request.tools)).length);
});

test('appended assistant queries, tool results and correction messages retain order and metadata', () => {
  const {api} = parser();
  const input = event('action', [persona, scene, facts, statement]);
  const appended = [
    {role: 'assistant', content: '', tool_calls: [{id: 'query-1', type: 'function',
      function: {name: 'memory_recent', arguments: '{"limit":8}'}}]},
    {role: 'tool', tool_call_id: 'query-1', content: '{"episodes":[{"text":"先前的承诺"}]}'},
    {role: 'user', content: 'Please use native tool calls.'},
    {role: 'assistant', content: [{type: 'text', text: 'Structured content 🚶'}]},
  ];
  input.data.request.messages.push(...appended);
  const parsed = api.parseRequest(input);
  assert.deepEqual(plain(parsed.blocks.filter(block => block.messageIndex > 1).map(block => block.role)),
    ['assistant', 'tool', 'user', 'assistant']);
  appended.forEach((message, offset) => {
    const block = messageBlocks(parsed, offset + 2)[0];
    assert.equal(block.text, offset === 2 ? message.content : JSON.stringify(message));
    assert.equal(block.interpretation, 'recorded');
  });
  assert.equal(new Set(parsed.blocks.map(block => block.id)).size, parsed.blocks.length);
});

test('semantic parsing locates the first user rather than assuming message index one', () => {
  const {api} = parser();
  const input = event('prelude', [persona, scene, summary]);
  input.data.request.messages.splice(1, 0, {role: 'system', content: 'Additional system information.'});
  const parsed = api.parseRequest(input);
  assert.equal(messageBlocks(parsed, 1)[0].text, 'Additional system information.');
  assert.deepEqual(plain(messageBlocks(parsed, 2).map(block => block.key)), ['persona', 'scene', 'subjective_summary']);
});

test('structured first user and extra fields stay as complete unparsed JSON', () => {
  const {api, errors} = parser();
  for (const message of [{role: 'user', content: [{type: 'text', text: persona}]},
    {role: 'user', name: 'Lin', content: [persona, scene].join('\n\n')}]) {
    const input = event();
    input.data.request.messages[1] = message;
    const parsed = api.parseRequest(input);
    assert.equal(messageBlocks(parsed).length, 1);
    assert.equal(messageBlocks(parsed)[0].text, JSON.stringify(message));
    assert.match(parsed.warnings[0], /未解析/);
  }
  assert.equal(errors.length, 2);
});

test('unrecognized template preserves the entire first user and reports unparsed state', () => {
  const {api, errors} = parser();
  const input = event();
  input.data.request.messages[0].content = 'Different agent rules.';
  const parsed = api.parseRequest(input);
  assert.equal(parsed.stage, 'other');
  assert.equal(messageBlocks(parsed).length, 1);
  assert.equal(messageBlocks(parsed)[0].text, input.data.request.messages[1].content);
  assert.match(messageBlocks(parsed)[0].label, /未解析/);
  assert.ok(parsed.blocks.every(block => block.rule === null));
  assert.ok(parsed.blocks.every(block => block.interpretation === 'recorded'));
  assert.equal(errors.length, 1);
});

test('ambiguous scenes, duplicate titles and reversed sections are never guessed', () => {
  const {api} = parser();
  const cases = [[persona, scene, scene], [persona, scene, summary, summary],
    [persona, scene, episodes, summary], [persona, summary], [scene, summary],
    [persona, scene, '主观归纳：\n'], [persona, scene, '最近的人物自述：\nmissing marker']];
  for (const sections of cases) {
    const input = event('prelude', sections);
    const parsed = api.parseRequest(input);
    assert.equal(messageBlocks(parsed).length, 1, sections.join(' | '));
    assert.equal(messageBlocks(parsed)[0].text, input.data.request.messages[1].content);
    assert.equal(messageBlocks(parsed)[0].interpretation, 'recorded');
    assert.equal(parsed.warnings.length, 1);
  }
});

test('action requires a single valid one-line facts object in its actual stage position', () => {
  const {api} = parser();
  const cases = [[persona, scene], [persona, scene, '{broken'], [persona, scene, '{\n"x":1\n}'],
    [persona, scene, facts, facts], [persona, scene, statement, facts],
    [persona, scene, experiences, facts], [persona, scene, facts, 'Unmarked trailing paragraph']];
  for (const sections of cases) {
    const input = event('action', sections);
    const parsed = api.parseRequest(input);
    assert.equal(messageBlocks(parsed).length, 1, sections.join(' | '));
    assert.equal(messageBlocks(parsed)[0].text, input.data.request.messages[1].content);
    assert.equal(parsed.warnings.length, 1);
  }
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

test('comparison detects same-length changes and preserves both complete versions', () => {
  const {api} = parser();
  const before = api.parseRequest(event('prelude', [persona, scene, summary]));
  const after = api.parseRequest(event('prelude', [persona, scene, summary.replace('热的', '冷的')]));
  const changes = api.compare(after.blocks, before.blocks);
  const changed = changes.find(block => block.key === 'subjective_summary');
  assert.equal(changed.status, 'changed');
  assert.equal(changed.delta, 0);
  assert.equal(changed.before.text, '\n\n' + summary);
  assert.equal(changed.after.text, '\n\n' + summary.replace('热的', '冷的'));
  assert.equal(changes.find(block => block.key === 'persona').status, 'unchanged');
});

test('comparison distinguishes additions and exits using stable identities', () => {
  const {api} = parser();
  const before = api.parseRequest(event('prelude', [persona, scene, summary]));
  const after = api.parseRequest(event('prelude', [persona, scene, episodes]));
  const changes = api.compare(after.blocks, before.blocks);
  const added = changes.find(block => block.key === 'recent_subjective_episodes');
  const removed = changes.find(block => block.key === 'subjective_summary');
  assert.equal(added.status, 'added');
  assert.equal(added.before, null);
  assert.equal(added.delta, Array.from('\n\n' + episodes).length);
  assert.equal(removed.status, 'removed');
  assert.equal(removed.after, null);
  assert.equal(removed.delta, -Array.from('\n\n' + summary).length);
  assert.equal(changes.at(-1).id, removed.id);
});

test('all parsed block rules exist and all growth views retain explicit source and steps', () => {
  const {api} = parser();
  const parsed = api.parseRequest(event('action', [persona, scene, summary, episodes, facts, statement]));
  assert.ok(parsed.blocks.every(block => api.rules[block.rule]));
  for (const rule of Object.values(api.rules)) {
    assert.ok(rule.label && rule.source && rule.fill);
    assert.ok(rule.steps.length >= 3);
    assert.ok(rule.steps.every(step => step.length === 2 && step.every(value => typeof value === 'string')));
    assert.equal(rule.source.startsWith('/'), false);
  }
  assert.match(api.rules.reflection.fill, /不含人物卡片/);
  assert.match(api.rules.capacity.fill, /不是 token/);
});
