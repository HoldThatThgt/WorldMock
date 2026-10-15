'use strict';

/* 查看器从导出建的页内索引与全文参照库给出同样的画面：参照库是运行数据库同形、每行原文的 events 表（不经页面的建索引
 * 代码），候选是页面自己从 events.jsonl 建的、三类大事件已裁剪的索引；两边都经页面的加载入口打开同一份导出。 */

const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const {CASES, RUN_ID, epoch, viewer, event, droppedFile, fileEntry, directoryEntry, frozenCatalog, runManifest,
  tableRows, targets, exportRun, eventsFile, canonicalJson, Database} = require('./run_viewer_harness.cjs');

/** 录制的当前行动请求与续接请求（四条消息）。 */
const CONTEXT = JSON.parse(readFileSync(path.join(__dirname, '../../fixtures/viewer-context.json'), 'utf8'));
/** observation 进索引的四个键（tools/run-viewer-index.js 的 OBSERVATION_DATA_KEEP）。 */
const OBSERVATION_KEEP = ['balance_fen', 'body', 'active_activities', 'calendar_context'];
/** 三类裁剪的事件。 */
const PRUNED = new Set(['observation', 'model_request', 'model_response']);
/** 一条运行输入警告。 */
const warning = (code, agent_id, message) => ({code, agent_id, message, details: {}});

/** @brief 深拷贝一份录制的请求：action 或 continuation。 */
function recorded(kind) {
  return JSON.parse(JSON.stringify(CONTEXT[kind]));
}

/** @brief 不属于人物行动的调用：系统层换成别的角色、不带工具（与 run_viewer.test.cjs 的 otherRequest 相同）。 */
function otherRequest() {
  const request = recorded('action');
  request.messages[0].content = JSON.stringify({layer: '系统层', role: '依据人物记忆回答问题。', rules: []});
  delete request.tools;
  return request;
}

/** @brief 一条录制形状的观察：数据带观察的全部键且都不为空，活动绑定 300 行，使这一行超过 4 KiB。 */
function observation(balance, energy) {
  return {
    active_activities: [{activity_id: 'e-1', agent_id: 1, kind: 'interact', status: 'active'}],
    activity_bindings: Array.from({length: 300}, (_, index) => ({name: `整理第 ${index} 份材料`,
      activity_id: `act-${index}`, title: null, phase: 'active', resumable: true, note_labels: ['纪要']})),
    agent_id: 1, balance_fen: balance,
    body: {levels: {energy: '有点饿', sleep: '还行'}, values: {energy_kcal: energy, sleep_deficit_hours: 1.875,
      relaxation_need: 0.3, social_need: 0.4}},
    calendar_context: {date: '2026-01-05', weekday: '星期一', workday: true},
    catalog_bindings: [{object: '家', kind: 'location', object_id: 'home', actions: [{action: 'sleep'}]}],
    current_time: epoch + 60,
    environment_layer: {layer: '环境层', weather: '小雨'},
    errand_bindings: [{name: '买牛奶', errand_id: 'action-0007:errand:0'}],
    errand_item_bindings: [{name: '牛肉面', product_id: 'beef-noodles'}],
    goal_bindings: [{task_name: '交纪要', intent_kind: '工作'}],
    home_location_id: 'home',
    interaction_bindings: [{object: '哔哩哔哩', kind: 'app', object_id: 'bilibili'}],
    known_activity_actions: ['interact', 'travel', 'sleep', 'purchase', 'query'],
    location_id: 'home',
    locations: [{location_id: 'home', name: '家'}],
    memory_layer: {layer: '记忆层', episodes: ['昨天加班到很晚。']},
    persona_views: [{about: '王磊', text: '王磊很靠谱'}],
    progress_event_ids: ['event-1'],
    progress_layer: {layer: '进度层', events: [{type: 'tool_call', self_note: '先浏览。'}]},
    progress_meta: {window: 'today'},
    same_moment_stage: 'first',
    todo_layer: {layer: '待办层', goals: ['交纪要']},
    tool_layer: {layer: '工具层', groups: [{kind: 'app', action: 'interact'}]},
    verified_experiences: ['昨天去了公司。'],
    wake_reasons: ['session_open'],
  };
}

/** @brief 一条模型响应的 response：思考写在 message 与 provider_specific_fields 两处。 */
function modelResponse(reasoning, message = {},
  usage = {prompt_tokens: 1000, completion_tokens: 20, total_tokens: 1020}) {
  return {model: 'test-model', choices: [{index: 0, finish_reason: message.tool_calls ? 'tool_calls' : 'stop',
    message: {content: '', reasoning_content: reasoning, provider_specific_fields: {reasoning_content: reasoning},
      ...message}}], usage};
}

/** @brief 用于全文与索引对照的一份运行：两个人物、东京时区，26 条事件覆盖每个视图读到的类型；世界状态的 activities
 *  是第二章的数组形状；拖入导出的顶层文件与一个运行目录（运行清单、assets 与人物的 MEMORY.md）。 */
function identityRun() {
  const action = recorded('action');
  const continuation = recorded('continuation');
  const toolCalls = continuation.messages[2].tool_calls;
  const rows = [
    event('decision_started', {round_order: 0, wake_reasons: ['session_open']}, {decision_id: 'decision-a'}),
    event('observation', observation(120000, 1356.25), {decision_id: 'decision-a'}),
    event('model_request', {phase: 'request', call_id: 'call-a', attempt: 1, request: action},
      {event_id: 'request-action', decision_id: 'decision-a'}),
    event('model_response', {phase: 'response', call_id: 'call-a', request: action,
      response: modelResponse('先刷一会儿哔哩哔哩。', {tool_calls: toolCalls}, {prompt_tokens: 900,
        completion_tokens: 80, total_tokens: 980, prompt_cache_hit_tokens: 640,
        completion_tokens_details: {reasoning_tokens: 55}})},
    {caused_by: ['request-action'], decision_id: 'decision-a'}),
    event('action_requested', {tool_name: 'start_side_activity',
      request: {action: 'interact', target: ['哔哩哔哩'], prefer: null}, self_note: '先看会儿。'}),
    ...CASES.events[0].events.map(entry => event(entry.type, entry.data,
      {activity_id: entry.data.activity.activity_id})),
    event('environment_event', {kind: 'app_push', push_id: 'push-1', app: '哔哩哔哩', title: '城市生活短视频',
      summary: '下班路上的城市见闻。', app_id: 'bilibili', content_id: 'c-drama-1', issued_at: epoch},
    {visibility: 'agent'}),
    event('action_result', {status: 'completed', message: 'Purchase completed', data: {orders: [
      {order_id: 'order-1', goods_id: 'beef-noodles', app_id: 'meituan', total_price_fen: 1200,
        payment_app_id: 'alipay'}]}}),
    event('environment_event', {kind: 'purchase_payment'}, {agent_id: null,
      resource_changes: {'account:agent:1': -1200, 'account:external:scenario': 1200}}),
    event('memory_updated', {operation: 'append_episodes', created_episode_ids: ['m-1'], merged_episode_ids: [],
      episodes: [{id: 'm-1', type: 'routine', text: '午饭点了外卖。'}]}),
    event('decision_started', {round_order: 0, wake_reasons: ['planned_end']}, {decision_id: 'decision-b'}),
    event('observation', observation(118800, 1230), {decision_id: 'decision-b'}),
    event('model_request', {phase: 'request', call_id: 'call-b', attempt: 1, request: continuation},
      {event_id: 'request-continuation', decision_id: 'decision-b'}),
    event('model_response', {phase: 'response', call_id: 'call-b', request: null,
      response: modelResponse('看完这一集再说。', {content: '好。'})},
    {caused_by: ['request-continuation'], decision_id: 'decision-b'}),
    event('model_request', {phase: 'request', call_id: 'call-c', attempt: 1, request: otherRequest()},
      {event_id: 'request-other', agent_id: 2}),
    event('model_response', {phase: 'failure', call_id: 'call-c', error: 'Transport failed', request: null,
      response: null}, {caused_by: ['request-other'], agent_id: 2}),
    event('model_request', {phase: 'request', call_id: 'call-d', attempt: 1, purpose: 'plan_day',
      request: {model: 'test-model', messages: [{role: 'system', content: '你替人物安排明天。'},
        {role: 'user', content: '明天要交纪要。'}]}}, {event_id: 'request-plan'}),
    event('model_response', {phase: 'response', call_id: 'call-d', purpose: 'plan_day', request: null,
      response: modelResponse('先交纪要。', {content: '{"goals": []}'})}, {caused_by: ['request-plan']}),
    event('environment_event', {kind: 'day_plan_generated', day_plan: {day_end: epoch + 86400, goals: [
      {intent_kind: '工作', goal: '交纪要', registered_at: epoch, remind_at: epoch + 7200}]}}),
    event('action_rejected', {error_code: 'ACTION_REQUEST_INVALID', same_moment_round: 1,
      max_same_moment_decisions: 3, reason: '所选的对象不在本轮列出的范围内'}),
    event('checkpoint_created', {checkpoint_id: 'cp-1'}, {agent_id: null}),
    event('run_ended', {end_time: epoch + 86400}, {agent_id: null, wall_elapsed_ms: 120000}),
    event('checkpoint_created', {checkpoint_id: 'cp-2'}, {agent_id: null}),
  ];
  const world = {version: 26, agents: {1: {name: '陈曦'}, 2: {name: '李明'}},
    accounts: {'agent:1': 118800, 'external:scenario': 1200},
    activities: [{activity_id: 'e-1', name: '看哔哩哔哩（1月5日 08:00 起）'}],
    sessions: {'session-1-0001': {session_id: 'session-1-0001', agent_id: 1, kind: 'stay', location_id: 'home',
      name: '家（1月5日 06:00 起）', started_at: epoch, ended_at: null, status: 'active', activity_ids: ['e-1']}}};
  const manifest = runManifest({input_warnings: [warning('CONTENT_EMPTY_WITH_MEDIA_TASTES', 1, '陈曦没有媒体口味'),
    warning('LOCATION_NAME_SUSPICIOUS', null, '地点名带坐标')]});
  const run = directoryEntry('run', [fileEntry('manifest.json', manifest),
    directoryEntry('assets', frozenCatalog().files.map(file => fileEntry(file.name, file.text))),
    directoryEntry('as2', [directoryEntry('agents', [directoryEntry('agent_0001', [
      fileEntry('MEMORY.md', '今天把纪要交了。')])])])]);
  return {rows, options: {timezone: 'Asia/Tokyo', world, frozen: manifest, files: [], directories: [run],
    lineEdits: [[15, '"energy_kcal":1230,', '"energy_kcal":1230.0,']]}};
}

/** 时间线行之外 SQL 控制台跑的两条查询：缺省查询与读信封字段的查询。 */
const CONSOLE_QUERIES = [
  "SELECT event_seq, json_extract(body,'$.event_type') AS type, json_extract(body,'$.occurred_at') AS t\n" +
    'FROM events ORDER BY event_seq DESC LIMIT 20',
  "SELECT event_seq, json_extract(body,'$.agent_id') AS agent, json_extract(body,'$.resource_changes') AS rc " +
    'FROM events ORDER BY event_seq',
];

/** @brief 全部模型请求的序号。 */
const requestSeqs = app => app.state.events.filter(entry => entry.type === 'model_request').map(entry => entry.seq);

/** @brief 一组片段的 HTML：每个片段（元素或文档片段）的子元素依次取 innerHTML。 */
const appended = element => element.children.flatMap(fragment => fragment.children.map(child => child.innerHTML));

/** 快照各项：键、筛选的人物与取值函数，按此顺序依次取。 */
const SNAPSHOT = [
  ['state', '', app => JSON.stringify(app.run(`({events: S.events.map(e => [e.seq, e.type, e.agent, e.t, e.sum]),
    typeCounts: S.typeCounts, t0: S.t0, t1: S.t1, obs: S.obs, acts: S.acts, orders: [...S.orders], runId: S.runId,
    tz: S.tz, agents: S.agents, agentIds: S.agentIds, world: S.world, runManifest: S.runManifest,
    catalogMissing: S.catalogMissing, memFiles: S.memFiles, fileName: S.fileName})`))],
  ['overview', '', app => app.overview()],
  ['overview:1', '1', app => app.overview()],
  ['overview:2', '2', app => app.overview()],
  ['timeline', '', app => {
    app.run('renderTimeline()');
    return JSON.stringify([app.get('#tl-body').innerHTML, app.get('#tl-count').textContent,
      appended(app.get('#tl-list'))]);
  }],
  ['rows', '', app => JSON.stringify(app.run('S.events.map(e => tlRow(e).innerHTML)'))],
  ['details', '', async app => {
    const details = [];
    for (const {seq} of app.state.events) {
      app.run('var snapshotRow = document.createElement("div")');
      await app.run(`toggleDetail(snapshotRow, ${seq})`);
      details.push(app.run('snapshotRow.nextElementSibling.children.map(child => child.textContent)'));
    }
    return JSON.stringify(details);
  }],
  ['activities', '', app => {
    const pages = [app.activities()];
    while (app.run('AT.page < Math.ceil(AT.filtered.length / AT.pageSize) - 1')) {
      app.get('#at-next').onclick();
      pages.push(app.get('#act-body').innerHTML);
    }
    return JSON.stringify(pages);
  }],
  ['body', '', app => app.body()],
  ['economy', '', app => app.economy()],
  ['economy:1', '1', app => app.economy()],
  ['memory', '', app => app.memory()],
  ['reasoning', '', app => {
    const rows = app.run("rsQuery('')");
    app.run('renderReasoning()');
    return JSON.stringify([rows, appended(app.get('#rs-list'))]);
  }],
  ['reasoningSearch', '', app => JSON.stringify(app.run("rsQuery('哔哩')"))],
  ['contextIndex', '', app => JSON.stringify(app.run('ContextView.index()'))],
  ['contextReads', '', async app => {
    const reads = [];
    for (const seq of requestSeqs(app)) {
      const {current, before, previous, response, changes} = await app.run(`ContextView.read(${seq})`);
      reads.push({current, before, previous, response, changes});
    }
    return JSON.stringify(reads);
  }],
  ['contextViews', '', async app => {
    const views = [];
    for (const seq of requestSeqs(app)) {
      await app.run(`ContextView.select(${seq})`);
      views.push(app.get('#ctx-body').innerHTML);
      for (const value of ['growth', 'comparison']) {
        await app.get('#ctx-view').onchange({target: {value}});
        views.push(app.get('#ctx-body').innerHTML);
      }
    }
    return JSON.stringify(views);
  }],
  ['pretty', '', async app => {
    const texts = [];
    for (const {seq} of app.state.events) texts.push(await app.run(`prettyBody(${seq})`));
    return JSON.stringify(texts);
  }],
  ['console', '', app => {
    app.run('renderSqlConsole()');
    const out = [app.get('#sql-body').innerHTML];
    for (const query of CONSOLE_QUERIES) {
      app.get('#sql-input').value = query;
      app.get('#sql-run').onclick();
      out.push(app.get('#sql-out').innerHTML, app.get('#sql-msg').textContent.replace(/ · [\d.]+ ms$/, ''));
    }
    return JSON.stringify(out);
  }],
  ['errors', '', app => JSON.stringify(app.errors)],
];

/** @brief 依次取快照各项（keys 给出时只取这几项）；每项之前把人物筛选设成该项的人物，某项抛错时记作 error: <原因>，
 *  对照时报告而不中断。 */
async function snapshot(app, keys = null) {
  const out = {};
  for (const [key, agent, capture] of SNAPSHOT) {
    if (keys && !keys.includes(key)) continue;
    app.get('#agent-filter').value = agent;
    try {
      out[key] = await capture(app);
    } catch (error) {
      out[key] = `error: ${error.message}`;
    }
  }
  return out;
}

/** @brief 逐项对照两份快照，信息写明哪一项不同。 */
function assertSameSnapshot(actual, expected, label) {
  assert.deepEqual(Object.keys(actual), Object.keys(expected), label);
  for (const key of Object.keys(expected)) assert.equal(actual[key], expected[key], `${label}: snapshot key ${key}`);
}

/** @brief 打开一份导出并取快照，取完关库。 */
async function snapshotOf(rows, options, keys = null) {
  const app = await viewer(rows, options);
  try {
    return await snapshot(app, keys);
  } finally { app.close(); }
}

/** @brief 运行输入警告卡片的来源一行（去掉标签）。 */
function warningSource(markup) {
  const card = markup.slice(markup.indexOf('运行输入警告'), markup.indexOf('事件类型分布'));
  return /<span class="k">来源<\/span><span class="v">([^<]*)<\/span>/.exec(card)[1];
}

/** @brief 警告表逐行的归属、警告码与说明（去掉标签）。 */
function warningRows(markup) {
  return tableRows(markup, 'warning-table').map(cells => cells.map(cell => cell.replace(/<[^>]*>/g, '').trim()));
}

/** @brief 时间线各行的时刻一栏。 */
function timeColumn(rowsJson) {
  return JSON.parse(rowsJson).map(row => /<span class="tl-time">([^<]*)<\/span>/.exec(row)[1]);
}

test('every view shows the same output from the index as from full bodies on a complete export', async () => {
  const {rows, options} = identityRun();
  const reference = await snapshotOf(rows, {...options, reference: true});
  const app = await viewer(rows, options);
  try {
    const candidate = await snapshot(app);
    assertSameSnapshot(candidate, reference, 'complete export');
    for (const {seq} of app.state.events) {
      assert.equal(await app.run(`fetchBody(${seq})`), app.exported.lines[seq - 1], `byte range of event ${seq}`);
    }
    // 覆盖面：第 16 条是续接，来源决策按 tool_call 身份找到；请求与响应按 caused_by 配对。
    const continued = await app.run('ContextView.read(16)');
    assert.equal(continued.current.continuation, true);
    assert.ok(continued.current.blocks.some(block => block.label.includes('decision-a')));
    assert.equal((await app.run('ContextView.read(3)')).response.seq, 4);
    assert.equal((await app.run('ContextView.read(16)')).response.seq, 17);
    assert.equal(JSON.parse(candidate.details)[2][0], '查看上下文');
    assert.ok(candidate.memory.includes('今天把纪要交了。'));
    assert.equal(warningSource(candidate.overview), 'run/manifest.json');
    assert.equal(app.state.world.version, 26);
  } finally { app.close(); }
});

test('an incomplete export keeps catalog names, input warnings, run identity and local times', async () => {
  const {rows, options} = identityRun();
  const cut = rows.slice(0, 23);
  const incomplete = {...options, checkpoint: false};
  const reference = await snapshotOf(cut, {...incomplete, reference: true});
  const app = await viewer(cut, incomplete);
  try {
    const candidate = await snapshot(app);
    assertSameSnapshot(candidate, reference, 'incomplete export');
    // 同样 23 条事件、带最终存档打开（截到这里的运行数据库）：世界状态之外的一切相同。
    const complete = await viewer(cut, {...options, reference: true});
    try {
      const full = await snapshot(complete, ['state', 'overview', 'rows']);
      const fullState = JSON.parse(full.state), cutState = JSON.parse(candidate.state);
      for (const key of ['events', 'runManifest', 'catalogMissing']) {
        assert.deepEqual(cutState[key], fullState[key], key);
      }
      assert.deepEqual(targets(app.activities()), targets(complete.activities()));
      assert.equal(warningSource(candidate.overview), warningSource(full.overview));
      const people = warningRows(candidate.overview), named = warningRows(full.overview);
      assert.deepEqual(people.map(row => row.slice(1)), named.map(row => row.slice(1)));
      assert.deepEqual(people.map(row => row[0]), ['agent 1', '场景级']);
      assert.deepEqual(timeColumn(candidate.rows), timeColumn(full.rows));
    } finally { complete.close(); }
    assert.equal(app.state.runId, RUN_ID);
    assert.equal(app.state.tz, 'Asia/Tokyo');
    assert.equal(app.state.world, null);
    assert.ok(candidate.overview.includes('无 world_state'));
    assert.ok(candidate.overview.includes('agent 1'));
  } finally { app.close(); }
});

/** @brief 在运行末尾加一条 app 推送，摘要用 ASCII 的 x 补齐，使「中」（3 字节）从模 7 余 6 的字节起、「😀」（4 字节）从
 *  模 7 余 5 的字节起，两个字都跨过 7 字节一段的读入边界。 */
function straddlingRows(base) {
  const make = (before, between) => [...base.rows, event('environment_event', {kind: 'app_push', push_id: 'push-2',
    app: '哔哩哔哩', title: '跨段', app_id: 'bilibili', content_id: 'c-drama-1',
    summary: `${'x'.repeat(before)}中${'x'.repeat(between)}😀`})];
  const offset = (rows, mark) => {
    const text = exportRun(rows, base.options).text;
    return Buffer.byteLength(text.slice(0, text.lastIndexOf(mark)), 'utf8');
  };
  const before = (6 - offset(make(0, 0), '中') % 7 + 7) % 7;
  const between = (5 - offset(make(before, 0), '😀') % 7 + 7) % 7;
  const rows = make(before, between);
  assert.equal(offset(rows, '中') % 7, 6);
  assert.equal(offset(rows, '😀') % 7, 5);
  return rows;
}

test('lines split across reads at any read size give the same index and byte ranges', async () => {
  const base = identityRun();
  const expected = await snapshotOf(base.rows, base.options);
  for (const readBytes of [7, 4096]) {
    const prepare = run => { run('ViewerIndex').readBytes = readBytes; };
    assertSameSnapshot(await snapshotOf(base.rows, {...base.options, prepare}), expected, `read size ${readBytes}`);
  }
  // 三字节与四字节的字都跨过读入边界。
  const rows = straddlingRows(base);
  const prepare = run => { run('ViewerIndex').readBytes = 7; };
  const reference = await snapshotOf(rows, {...base.options, prepare, reference: true});
  const candidate = await snapshotOf(rows, {...base.options, prepare});
  assertSameSnapshot(candidate, reference, 'characters across reads');
  assert.ok(candidate.rows.includes('中') && candidate.rows.includes('😀'));
});

test('the index prunes exactly the three listed types and the console says so', async () => {
  const {rows, options} = identityRun();
  const app = await viewer(rows, options);
  /** @brief 在页面的索引库上执行一条查询。 */
  const query = sql => app.run(`q(${JSON.stringify(sql)})`);
  try {
    const indexed = query('SELECT event_seq AS seq, body FROM events ORDER BY event_seq');
    assert.equal(indexed.length, rows.length);
    for (const {seq, body} of indexed) {
      const line = JSON.parse(app.exported.lines[seq - 1]);
      const pruned = JSON.parse(body);
      if (line.event_type === 'observation') {
        const keys = Object.keys(pruned.data);
        assert.ok(keys.every(key => OBSERVATION_KEEP.includes(key)), `observation ${seq}: ${keys}`);
        assert.deepEqual([...keys].sort(), [...OBSERVATION_KEEP].sort(), `observation ${seq}`);
        assert.deepEqual({...pruned, data: null}, {...line, data: null}, `observation ${seq} envelope`);
      } else if (line.event_type === 'model_request') {
        const [{n}] = query(`SELECT json_array_length(body,'$.data.request.messages') AS n FROM events
          WHERE event_seq=${seq}`);
        assert.equal(n, 1, `request ${seq}`);
        line.data.request.messages = line.data.request.messages.slice(0, 1);
        assert.deepEqual(pruned, line, `request ${seq}`);
      } else if (line.event_type === 'model_response') {
        const [{t}] = query(`SELECT json_type(body,'$.data.request') AS t FROM events WHERE event_seq=${seq}`);
        assert.equal(t, null, `response ${seq}`);
        delete line.data.request;
        assert.deepEqual(pruned, line, `response ${seq}`);
      } else {
        assert.equal(body, app.exported.lines[seq - 1], `event ${seq}`);
      }
    }
    assert.equal(query('SELECT COUNT(*) AS n FROM world_state')[0].n, 1);
    const plan = JSON.stringify(query(`EXPLAIN QUERY PLAN SELECT event_seq FROM events
      WHERE json_extract(body,'$.event_type')='observation' ORDER BY event_seq`));
    assert.ok(plan.includes('INDEX events_type'), plan);
    const tables = query("SELECT name FROM sqlite_master WHERE type='table'").map(row => row.name);
    assert.ok(!tables.includes('checkpoints') && !tables.includes('artifacts'), tables.join());
    app.run('renderSqlConsole()');
    const help = app.get('#sql-body').innerHTML;
    for (const text of [...OBSERVATION_KEEP, '只留前 1 条', '去掉 data.request', 'event_lines(event_seq, offset, length)',
      '没有 checkpoints 与 artifacts 表', 'sqlite3', 'scripts/inspect_run.py']) {
      assert.ok(help.includes(text), text);
    }
  } finally { app.close(); }
});

/** 页面源码的变异：名称、文件、原文、替换文字，以及快照里必须因此不同的项。 */
const MUTATIONS = [
  ['observation_keep_drops_balance', 'tools/run-viewer-index.js', "'balance_fen', ", '', ['state', 'rows']],
  ['request_keeps_no_message', 'tools/run-viewer-index.js', 'const REQUEST_MESSAGES_KEEP = 1;',
    'const REQUEST_MESSAGES_KEEP = 0;', ['contextIndex']],
  ['response_drops_the_response', 'tools/run-viewer-index.js', 'const {request, ...rest} = data;',
    'const {request, response, ...rest} = data;', ['overview', 'reasoning']],
  ['byte_range_off_by_one', 'tools/run-viewer-index.js', 'lineStart = position + start;',
    'lineStart = position + start + 1;', ['pretty', 'details']],
  ['timezone_not_from_the_manifest', 'tools/run-viewer.html', 'S.tz = exported.value.window.timezone;',
    "S.tz = 'Asia/Shanghai';", ['rows', 'state']],
];

test('the identity comparison notices a keep-list or byte range that loses what a view reads', async () => {
  const {rows, options} = identityRun();
  const reference = await snapshotOf(rows, {...options, reference: true});
  for (const [name, file, original, replacement, keys] of MUTATIONS) {
    const mutated = await snapshotOf(rows, {...options, mutation: {file, original, replacement}});
    for (const key of keys) assert.notEqual(mutated[key], reference[key], `${name} must change ${key}`);
  }
});

test('loading refuses inputs that are not one complete export, with the exact message', async () => {
  const rows = identityRun().rows.slice(0, 3);
  const ex = (options = {}) => exportRun(rows, {files: [], ...options});
  const M = canonicalJson(ex().manifest);
  const rowsOf = count => ex({exportFields: {artifacts: {events: {path: 'events.jsonl', rows: count}}}}).files[1];
  const app = await viewer(rows);
  try {
    const cases = [
      ['lone database', [droppedFile('footprints.sqlite', '')], [], {message: 'footprints.sqlite is not opened ' +
        'directly: export the run with "worldmock export --run-dir <run directory> --output <new directory>" ' +
        '(--cutoff <event_seq> exports part of it) and drop the export directory'}],
      ['nothing', [], [], {message: 'No export manifest was dropped: drop the directory written by ' +
        '"worldmock export", or select its manifest.json and events.jsonl'}],
      ['two exports', [], [directoryEntry('a', [fileEntry('manifest.json', M)]),
        directoryEntry('b', [fileEntry('manifest.json', M)])],
      {message: 'More than one export manifest was dropped: a/manifest.json, b/manifest.json; ' +
        'drop one export directory'}],
      ['no time zone', ex({exportFields: {window: null}}).files, [],
        {message: 'Export manifest lacks run_id, window.timezone or artifacts.events'}],
      ["another run's frozen manifest", ex({frozen: runManifest({}, 'another-run')}).files, [],
        {message: 'Export manifest frozen_run_manifest is not the run manifest of run viewer-test-run'}],
      ['no events file', [ex().files[1]], [], {message: "The export's events file was not dropped: events.jsonl"}],
      ['one row too many', ex({exportFields: {artifacts: {events: {path: 'events.jsonl', rows: 4}}}}).files, [],
        {message: 'events.jsonl holds 3 events but the export manifest lists 4'}],
      ['truncated', [eventsFile(ex().text.slice(0, -1)), ex().files[1]], [],
        {message: 'events.jsonl does not end with a newline: the file is truncated'}],
      ['line 2 not JSON', [eventsFile(`${ex().lines[0]}\nnot json\n`), rowsOf(2)], [],
        {message: /^events\.jsonl line 2 could not be indexed: /}],
      ['invalid UTF-8', [eventsFile(Buffer.from([0xff, 0x0a])), rowsOf(1)], [],
        {message: /^events\.jsonl line 1 could not be indexed: /}],
    ];
    for (const [name, files, directories, expected] of cases) {
      const before = new Set(Database.live);
      const errorsBefore = app.errors.length;
      await assert.rejects(app.run('openExport')(files, directories, () => {}), expected, name);
      assert.equal(app.state.db, null, name);
      assert.ok([...Database.live].every(database => before.has(database)), `${name} left an index open`);
      if (name === 'line 2 not JSON') {
        assert.ok(app.errors.slice(errorsBefore).some(error => error.startsWith('Unindexable events.jsonl line 2 ')));
      }
    }
  } finally { app.close(); }
});

test('the index build yields to the page through messages and reports progress', async () => {
  const {rows, options} = identityRun();
  const expected = (await snapshotOf(rows, options, ['state'])).state;
  for (const [clock, yields] of [['advancing', rows.length], ['frozen', 0]]) {
    let channels = 0, now = 0;
    const logs = [];
    const prepare = run => {
      run('ViewerIndex').readBytes = 7;
      const Base = run('MessageChannel');
      run('globalThis').MessageChannel = class extends Base {
        constructor() { super(); channels += 1; }
      };
      run('Date').now = clock === 'advancing' ? () => (now += 1000) : () => 0;
    };
    const app = await viewer(rows, {...options, prepare, log: line => logs.push(line)});
    try {
      assert.equal(channels, yields, clock);
      assert.equal((await snapshot(app, ['state'])).state, expected, clock);
      const progress = logs.filter(line => /^· 已读 [\d.]+ \/ [\d.]+ MB（\d+ 条事件）$/.test(line));
      assert.ok(progress.length > 0, clock);
      assert.ok(progress.at(-1).endsWith(`（${rows.length} 条事件）`), progress.at(-1));
      assert.equal(logs[logs.indexOf(progress.at(-1)) + 1], '· 事件索引已建好');
    } finally { app.close(); }
  }
});
