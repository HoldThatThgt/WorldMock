'use strict';

const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {DatabaseSync} = require('node:sqlite');
const test = require('node:test');
const vm = require('node:vm');

const html = readFileSync(path.join(__dirname, '../../tools/run-viewer.html'), 'utf8');
const scripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(match => match[1]);
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
  };
}

/** @brief 用真实 SQLite 实现 sql.js 查询接口，SQL 原样执行。 */
function database(events) {
  const db = new DatabaseSync(':memory:');
  db.exec('CREATE TABLE events(event_seq INTEGER PRIMARY KEY, body TEXT NOT NULL)');
  db.exec('CREATE TABLE world_state(body TEXT NOT NULL)');
  const insert = db.prepare('INSERT INTO events(event_seq, body) VALUES (?, ?)');
  events.forEach((event, index) => insert.run(index + 1, JSON.stringify({
    run_id: 'viewer-test-run', occurred_at: epoch + index * 60, agent_id: 1, ...event,
  })));
  db.prepare('INSERT INTO world_state(body) VALUES (?)').run(JSON.stringify({
    timezone: 'Asia/Shanghai', agents: {1: {name: '陈曦'}, 2: {name: '李明'}},
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
function viewer(events) {
  const nodes = new Map();
  const errors = [];
  const get = selector => {
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  };
  const context = vm.createContext({
    document: {
      querySelector: get, querySelectorAll: () => [],
      createElement: element, createDocumentFragment: element,
    },
    window: {addEventListener() {}},
    console: {error: (...args) => errors.push(args.map(String).join(' '))},
    setTimeout, clearTimeout, requestAnimationFrame() {},
  });
  for (const script of scripts) vm.runInContext(script, context, {filename: 'run-viewer.html'});
  const run = source => vm.runInContext(source, context);
  const state = run('S');
  state.db = database(events);
  run('buildIndex()');
  return {
    state, errors, run, get,
    activities() { run('renderActivities()'); return get('#act-body').innerHTML; },
    body() { run('renderBodyCurves()'); return get('#bd-body').innerHTML; },
    economy() { run('renderEconomy()'); return get('#eco-body').innerHTML; },
    memory() { run('renderMemory()'); return get('#mem-body').innerHTML; },
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

/** @brief 提取活动表的目标列，防止页面其他文字造成假阳性。 */
function targets(markup) {
  return [...markup.matchAll(/<tbody>([\s\S]*?)<\/tbody>/g)].flatMap(body =>
    [...body[1].matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map(row =>
      [...row[1].matchAll(/<td(?:\s[^>]*)?>([\s\S]*?)<\/td>/g)][3][1]));
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
    events.push(event('action_requested', {tool_name: 'start_activity', request: {
      targets: [place.name], actions: ['步行前往'],
    }}));
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
    {item_id: 'movie-0001', title: '海边的清晨', kind: 'feature_film'},
    {item_id: 'drama-0007', title: '华语短剧台词选段（源编号 16013）', kind: 'short_drama'},
    {item_id: 'novel-0003', title: '老城故事', kind: 'online_novel'},
    {item_id: 'textworld-0004', title: '森林迷宫', kind: 'text_game'},
    {activity_id: 'stretch-evening', name: '晚间舒展', kind: 'exercise'},
  ];
  const events = [browse(rows)];
  for (const row of rows) {
    const target_id = row.item_id || row.activity_id;
    events.push(event('action_requested', {tool_name: 'start_activity', request: {
      targets: [row.title || row.name], actions: ['开始'],
    }}));
    events.push(activity('action_accepted', `do-${target_id}`, {kind: row.kind, target_id}));
  }
  const view = viewer(events);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), rows.map(row => row.title || row.name));
  rows.forEach((row, index) => assert.ok(view.summary(index * 2 + 2).includes(row.title || row.name)));
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
    event('action_requested', {tool_name: 'start_activity', request: {
      targets: [title], actions: ['观看'],
    }}),
    activity('activity_progress', 'only-progress', {kind: 'watch', target_id: 'drama-0007', title}),
  ]);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), [title]);
  assert.ok(view.summary(1).includes(title));
  assert.ok(view.summary(2).includes(title));
  assert.deepEqual(view.errors, []);
});

test('start activity request displays semantic objects and actions', t => {
  const view = viewer([
    event('observation', {locations: [{location_id: 'home', name: '张江公寓'}],
      products: [{product_id: 'food-rice', name: '菜饭'}]}),
    event('action_requested', {tool_name: 'start_activity', request: {
      targets: ['菜饭'], actions: ['外送到张江公寓'],
    }}),
  ]);
  t.after(() => view.close());
  assert.ok(view.summary(2).includes('张江公寓'));
  assert.ok(view.summary(2).includes('菜饭'));
  assert.ok(!view.summary(2).includes('food-rice'));
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
  const view = viewer([
    event('action_requested', {tool_name: 'report_status', request: {
      task: ['整理访谈', '准备周会'], summary: ['完成提纲', '列出议题'],
    }}),
    event('action_requested', {tool_name: 'conclude_activity', request: {
      task: ['整理访谈'], summary: ['已经提交纪要'],
    }}),
  ]);
  t.after(() => view.close());
  assert.ok(view.summary(1).includes('整理访谈：完成提纲；准备周会：列出议题'));
  assert.ok(view.summary(2).includes('结束活动 · 整理访谈：已经提交纪要'));
  assert.ok(!view.summary(1).includes('task='));
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
