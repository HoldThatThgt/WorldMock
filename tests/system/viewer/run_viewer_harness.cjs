'use strict';

/** @brief 查看器 Node 用例共用的助手：执行页面的真实脚本，用真实 SQLite 实现 sql.js 的查询接口，再用假的文件与目录条目
 *  经页面的同一个加载入口 indexRun 喂入运行目录（属于测试库的运行清单与 assets 里的冻结目录文件）。 */

const {createHash} = require('node:crypto');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {DatabaseSync} = require('node:sqlite');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '../../..');
const html = readFileSync(path.join(ROOT, 'tools/run-viewer.html'), 'utf8');
const scripts = [...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)].map(match => {
  const source = /src="([^"]+)"/.exec(match[1]);
  return source ? readFileSync(path.join(ROOT, 'tools', source[1]), 'utf8') : match[2];
});
/** 两种语言共读的显示写法用例文件；它的 catalog 一组是假运行目录缺省冻结的目录行。 */
const CASES = JSON.parse(readFileSync(path.join(ROOT, 'tests/fixtures/display-wording-cases.json'), 'utf8'));
const epoch = Date.parse('2026-01-05T06:00:00+08:00') / 1000;
const RUN_ID = 'viewer-test-run';
/** 名称表的五组目录行冻结时在运行清单 environment_assets 里的素材名。 */
const CATALOG_ASSETS = {apps: 'catalog/app-catalog.jsonl', contents: 'catalog/content-catalog.jsonl',
  goods: 'catalog/merchant-catalog.jsonl', locations: 'catalog/location.jsonl',
  travel_methods: 'catalog/travel-method.jsonl'};

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

/** @brief 用真实 SQLite 实现 sql.js 查询接口，SQL 原样执行；世界状态带测试运行的 run_id，world 覆盖其字段。 */
function database(events, world = {}) {
  const db = new DatabaseSync(':memory:');
  db.exec('CREATE TABLE events(event_seq INTEGER PRIMARY KEY, body TEXT NOT NULL)');
  db.exec('CREATE TABLE world_state(body TEXT NOT NULL)');
  const insert = db.prepare('INSERT INTO events(event_seq, body) VALUES (?, ?)');
  events.forEach((event, index) => insert.run(index + 1, JSON.stringify({
    run_id: RUN_ID, occurred_at: epoch + index * 60, agent_id: 1, ...event,
  })));
  db.prepare('INSERT INTO world_state(body) VALUES (?)').run(JSON.stringify({
    run_id: RUN_ID, timezone: 'Asia/Shanghai', agents: {1: {name: '陈曦'}, 2: {name: '李明'}}, ...world,
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

/** @brief 拖入或选择的顶层文件：名称、UTF-8 字节数与文本；content 不是字符串时按 JSON 写出。 */
function droppedFile(name, content) {
  const text = typeof content === 'string' ? content : JSON.stringify(content);
  return {name, size: Buffer.byteLength(text, 'utf8'), text: async () => text};
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

/** @brief 冻结的目录：catalog 每组目录行写成 JSONL 文本、以 SHA-256 摘要为文件名；返回运行清单 environment_assets 的
 *  条目与素材文件。catalog 里没有的组不冻结（出行方式文件可选）。 */
function frozenCatalog(catalog = CASES.catalog) {
  const assets = {}, files = [];
  for (const [table, asset] of Object.entries(CATALOG_ASSETS)) {
    if (!catalog[table]) continue;
    const text = catalog[table].map(row => JSON.stringify(row)).join('\n') + '\n';
    const sha256 = createHash('sha256').update(text, 'utf8').digest('hex');
    assets[asset] = {sha256, size_bytes: Buffer.byteLength(text, 'utf8'), media_type: 'text/plain',
      source: {kind: 'environment_input', path: asset}};
    files.push({name: sha256, text});
  }
  return {assets, files};
}

/** @brief 共用用例文件的目录行再加几行：extra 的每组行追加在同组之后。 */
function withCatalog(extra) {
  const catalog = {...CASES.catalog};
  for (const [table, rows] of Object.entries(extra)) catalog[table] = [...(catalog[table] ?? []), ...rows];
  return catalog;
}

/** @brief 运行清单：与测试世界状态同一 run_id、带 agent_ids，登记 catalog 冻结的目录素材；fields 覆盖 input_warnings
 *  等键（缺省不带该键）。 */
function runManifest(fields = {}, runId = RUN_ID, catalog = CASES.catalog) {
  return {schema_version: 3, run_id: runId, source_group_id: runId, agent_ids: [1, 2],
    environment_assets: frozenCatalog(catalog).assets, ...fields};
}

/** @brief 假的运行目录：属于测试库的运行清单，assets 目录里放 catalog 冻结的目录文件。 */
function runDirectory(catalog = CASES.catalog) {
  return directoryEntry('run', [fileEntry('manifest.json', runManifest({}, RUN_ID, catalog)),
    directoryEntry('assets', frozenCatalog(catalog).files.map(file => fileEntry(file.name, file.text)))]);
}

/** @brief catalog 冻结的目录文件，作为顶层选中的素材文件（文件名就是摘要）。 */
function assetFiles(catalog = CASES.catalog) {
  return frozenCatalog(catalog).files.map(file => droppedFile(file.name, file.text));
}

/** @brief 执行整段页面脚本，经页面的加载入口 indexRun 建索引、读运行文件、生成摘要，暴露真实索引和渲染结果。
 *  缺省带一个假的运行目录（用例文件的目录行）；给出 files 或 directories 时只用给出的。加载报错时关库后原样抛出。 */
async function viewer(events, {world = {}, catalog = CASES.catalog, files, directories} = {}) {
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
  /** @brief 打开一份测试库并走页面的加载入口。 */
  const open = async (rows, options) => {
    state.db = database(rows, options.world ?? {});
    const supplied = options.files || options.directories;
    const directoryList = supplied ? options.directories ?? [] : [runDirectory(options.catalog ?? CASES.catalog)];
    try {
      await run('indexRun')(options.files ?? [], directoryList);
    } catch (error) {
      state.db.close();
      throw error;
    }
  };
  await open(events, {world, catalog, files, directories});
  return {
    state, errors, run, get,
    activities() { run('renderActivities()'); return get('#act-body').innerHTML; },
    body() { run('renderBodyCurves()'); return get('#bd-body').innerHTML; },
    economy() { run('renderEconomy()'); return get('#eco-body').innerHTML; },
    memory() { run('renderMemory()'); return get('#mem-body').innerHTML; },
    overview() { run('renderOverview()'); return get('#ov-body').innerHTML; },
    context() { run('ContextView.render()'); return get('#ctx-body').innerHTML; },
    summary(sequence) { return state.events.find(event => event.seq === sequence).sum; },
    /** @brief 换一个数据库：点「换一个文件」后经同一个加载入口打开下一份库（选项同 viewer）。 */
    async reload(nextEvents, options = {}) {
      get('#btn-reload').onclick();
      await open(nextEvents, options);
    },
    close() { state.db.close(); },
  };
}

/** @brief 保持事件级 data 和 activity_id 的现行存储形状。 */
function event(type, data, extra = {}) {
  return {event_type: type, data, ...extra};
}

/** @brief 按表格类名提取表体各行的单元格文字，防止页面其他文字造成假阳性。 */
function tableRows(markup, className) {
  const table = new RegExp(`<table class="${className}">[\\s\\S]*?<tbody>([\\s\\S]*?)<\\/tbody>`, 'g');
  return [...markup.matchAll(table)].flatMap(body =>
    [...body[1].matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map(row =>
      [...row[1].matchAll(/<td(?:\s[^>]*)?>([\s\S]*?)<\/td>/g)].map(cell => cell[1])));
}

/** @brief 提取活动表的目标列（第四格）。 */
function targets(markup) {
  return tableRows(markup, 'act-table').map(cells => cells[3]);
}

module.exports = {CASES, ROOT, RUN_ID, epoch, viewer, database, event, droppedFile, fileEntry, directoryEntry,
  frozenCatalog, withCatalog, runManifest, runDirectory, assetFiles, tableRows, targets};
