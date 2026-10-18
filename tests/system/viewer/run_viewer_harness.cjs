'use strict';

/** @brief 查看器 Node 用例共用的助手：执行页面的真实脚本，用真实 SQLite 实现 sql.js 的 Database 接口，把用例事件写成一份
 *  导出（events.jsonl、导出清单、冻结目录），经页面的加载入口 openExport 打开。 */

const {createHash} = require('node:crypto');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const {DatabaseSync} = require('node:sqlite');
const vm = require('node:vm');

const ROOT = path.join(__dirname, '../../..');
const html = readFileSync(path.join(ROOT, 'tools/run-viewer.html'), 'utf8');
/** 页面按序执行的脚本：file 是 tools/<名称> 或内联脚本所在的 tools/run-viewer.html，用例可按 file 改写其中一份。 */
const scripts = [...html.matchAll(/<script([^>]*)>([\s\S]*?)<\/script>/g)].map(match => {
  const source = /src="([^"]+)"/.exec(match[1]);
  return source ? {file: `tools/${source[1]}`, source: readFileSync(path.join(ROOT, 'tools', source[1]), 'utf8')}
    : {file: 'tools/run-viewer.html', source: match[2]};
});
/** 两种语言共读的显示写法用例文件；它的 catalog 一组是假导出缺省冻结的目录行。 */
const CASES = JSON.parse(readFileSync(path.join(ROOT, 'tests/fixtures/display-wording-cases.json'), 'utf8'));
const epoch = Date.parse('2026-01-05T06:00:00+08:00') / 1000;
const RUN_ID = 'viewer-test-run';
/** 名称表的五组目录行冻结时在运行清单 environment_assets 里的素材名。 */
const CATALOG_ASSETS = {apps: 'catalog/app-catalog.jsonl', contents: 'catalog/content-catalog.jsonl',
  goods: 'catalog/merchant-catalog.jsonl', locations: 'catalog/location.jsonl',
  travel_methods: 'catalog/travel-method.jsonl'};
/** 参照库的表与类型索引，与 tools/run-viewer-index.js 的同一组语句逐字相同（在这里照写，不读页面代码）。 */
const REFERENCE_SCHEMA = `
CREATE TABLE events(event_seq INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, body TEXT NOT NULL);
CREATE TABLE event_lines(event_seq INTEGER PRIMARY KEY, offset INTEGER NOT NULL, length INTEGER NOT NULL);
CREATE TABLE world_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
version INTEGER NOT NULL, body TEXT NOT NULL);`;
const REFERENCE_TYPE_INDEX = "CREATE INDEX events_type ON events(json_extract(body,'$.event_type'), event_seq)";

/** @brief 提供渲染所需的最小 DOM，不替换页面业务逻辑。 */
function element() {
  const classes = new Set();
  const listeners = new Map();
  return {
    innerHTML: '', textContent: '', value: '', dataset: {}, style: {}, children: [], attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
    classList: {
      add: name => classes.add(name), remove: name => classes.delete(name),
      contains: name => classes.has(name), toggle: name => classes.has(name)
        ? classes.delete(name) : classes.add(name),
    },
    addEventListener(type, callback) { listeners.set(type, callback); },
    dispatch(type, event) { return listeners.get(type)(event); },
    appendChild(child) { this.children.push(child); },
    querySelector() { return element(); }, click() {}, scrollIntoView() {},
    after(child) { this.nextElementSibling = child; },
  };
}

/** @brief sql.js 的最小替身：页面建索引与查询用到的 Database 接口（exec、run、prepare、close），同一 SQL 交给真实的
 *  node:sqlite 内存库执行；close 可重复调用，与 sql.js 相同。Database.live 是还没关闭的库，用例借它核对失败时关了库。 */
class Database {
  static live = new Set();
  constructor() { this.db = new DatabaseSync(':memory:'); this.open = true; Database.live.add(this); }
  exec(sql) { this.db.exec(sql); return []; }
  run(sql, params) { if (params) this.db.prepare(sql).run(...params); else this.db.exec(sql); return this; }
  prepare(sql) {
    const statement = this.db.prepare(sql);
    let rows = [], index = -1;
    return {
      bind(parameters) { rows = statement.all(...(parameters || [])); index = -1; },
      step() { return ++index < rows.length; },
      getAsObject() { return rows[index]; },
      run(values) { statement.run(...(values || [])); },
      free() {},
    };
  }
  close() { if (this.open) { this.db.close(); this.open = false; Database.live.delete(this); } }
}

/** @brief 与 Python canonical_json 同序的 JSON 文本：对象键按字符串排序、无空白、非 ASCII 原样，值为 undefined 的键
 *  不写；数组逐项（undefined 写 null），其余值照 JSON.stringify。 */
function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(item => canonicalJson(item ?? null)).join(',')}]`;
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  const keys = Object.keys(value).filter(key => value[key] !== undefined).sort();
  return `{${keys.map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}

/** @brief 导出的 events.jsonl：size 与 slice(起, 止)；slice 给出的 arrayBuffer() 与 text() 只经微任务完成，
 *  形状同浏览器的 File。content 是字符串（按 UTF-8 编码）或 Buffer（原样字节，用于非法 UTF-8 的用例）。 */
function eventsFile(content) {
  const bytes = Buffer.isBuffer(content) ? content : Buffer.from(content, 'utf8');
  return {name: 'events.jsonl', size: bytes.length, slice(start, end) {
    const part = bytes.subarray(start, end);
    return {arrayBuffer: async () => part.buffer.slice(part.byteOffset, part.byteOffset + part.length),
      text: async () => part.toString('utf8')};
  }};
}

/** @brief 拖入或选择的顶层文件：名称、UTF-8 字节数与文本；content 不是字符串时按 JSON 写出。 */
function droppedFile(name, content) {
  const text = typeof content === 'string' ? content : JSON.stringify(content);
  return {name, size: Buffer.byteLength(text, 'utf8'), text: async () => text};
}

/** @brief 拖入的文件句柄（FileSystemFileHandle 的最小形状）；content 是带 slice 与数字 size 的文件对象时原样交出，
 *  否则照 droppedFile 写成文件。 */
function fileHandle(name, content) {
  const fileLike = content !== null && typeof content === 'object' && typeof content.slice === 'function' &&
    typeof content.size === 'number';
  const file = fileLike ? content : droppedFile(name, content);
  return {kind: 'file', name, async getFile() { return file; }};
}

/** @brief 拖入的目录句柄：values 异步交出各个文件或子目录句柄。 */
function directoryHandle(name, children) {
  return {kind: 'directory', name, async *values() { yield* children; }};
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

/** @brief 运行清单：与测试导出同一 run_id、带 agent_ids，登记 catalog 冻结的目录素材；fields 覆盖 input_warnings
 *  等键（缺省不带该键）。 */
function runManifest(fields = {}, runId = RUN_ID, catalog = CASES.catalog) {
  return {schema_version: 3, run_id: runId, source_group_id: runId, agent_ids: [1, 2],
    environment_assets: frozenCatalog(catalog).assets, ...fields};
}

/** @brief 假的运行目录：属于测试运行的运行清单，assets 目录里放 catalog 冻结的目录文件。 */
function runDirectory(catalog = CASES.catalog) {
  return directoryHandle('run', [fileHandle('manifest.json', runManifest({}, RUN_ID, catalog)),
    directoryHandle('assets', frozenCatalog(catalog).files.map(file => fileHandle(file.name, file.text)))]);
}

/** @brief catalog 冻结的目录文件，作为顶层选中的素材文件（文件名就是摘要）。 */
function assetFiles(catalog = CASES.catalog) {
  return frozenCatalog(catalog).files.map(file => droppedFile(file.name, file.text));
}

/** @brief 把用例事件写成一份导出并给出拖入的文件。每行是带测试信封缺省值（run_id、按分钟递增的 occurred_at、人物 1、
 *  event-<序号>）的规范 JSON，lineEdits 逐条替换某行里唯一的一段原文；导出清单照 export_run 的字段写成规范 JSON，
 *  缺省带最终存档（checkpoint 为 false 时不带），exportFields 最后覆盖。不给 files 与 directories 时拖入一个 export
 *  目录（events.jsonl、manifest.json 与冻结目录的 assets）；给出任一项时顶层是 events.jsonl、导出清单与 files，
 *  目录只有 directories，不带导出的 assets。 */
function exportRun(rows, options = {}) {
  const timezone = options.timezone ?? 'Asia/Shanghai';
  const catalog = options.catalog ?? CASES.catalog;
  const lines = rows.map((row, index) => canonicalJson({run_id: RUN_ID, occurred_at: epoch + index * 60, agent_id: 1,
    event_id: `event-${index + 1}`, ...row, event_seq: index + 1}));
  for (const [seq, original, replacement] of options.lineEdits ?? []) {
    if (lines[seq - 1].split(original).length !== 2) throw new Error('Line edit anchor is not unique');
    lines[seq - 1] = lines[seq - 1].replace(original, () => replacement);
  }
  const text = lines.map(line => `${line}\n`).join('');
  const world = {run_id: RUN_ID, timezone, agents: {1: {name: '陈曦'}, 2: {name: '李明'}}, ...options.world};
  const end = lines.length ? JSON.parse(lines.at(-1)).occurred_at : epoch;
  const iso = seconds => new Date(seconds * 1000).toISOString();
  const manifest = {schema_version: 8, committed: true, run_id: RUN_ID, source_group_id: RUN_ID,
    cutoff_event_seq: rows.length, experiment_completed: options.checkpoint !== false,
    window: {start: iso(epoch), end: iso(end), timezone},
    artifacts: {events: {path: 'events.jsonl', rows: rows.length,
      sha256: createHash('sha256').update(text, 'utf8').digest('hex')}},
    frozen_run_manifest: options.frozen ?? runManifest({}, RUN_ID, catalog)};
  if (options.checkpoint !== false) manifest.checkpoint = {state_version: world.version ?? 0, state: world};
  Object.assign(manifest, options.exportFields);
  const file = eventsFile(text);
  const manifestText = canonicalJson(manifest);
  if (!options.files && !options.directories) {
    const assets = frozenCatalog(catalog).files.map(asset => fileHandle(asset.name, asset.text));
    return {lines, text, manifest, files: [], directories: [directoryHandle('export', [fileHandle('events.jsonl', file),
      fileHandle('manifest.json', manifestText), directoryHandle('assets', assets)])]};
  }
  return {lines, text, manifest, files: [file, droppedFile('manifest.json', manifestText), ...(options.files ?? [])],
    directories: options.directories ?? []};
}

/** @brief 参照库：运行数据库同形、每行原文的全文 events 表，用例自己按字节算的 event_lines，同一个 events_type 索引；
 *  导出带最终存档时 world_state 一行（规范 JSON）。不经页面的建索引代码。 */
function referenceIndex(lines, manifest) {
  const db = new Database();
  db.exec(REFERENCE_SCHEMA);
  let offset = 0;
  for (const line of lines) {
    const event = JSON.parse(line);
    const length = Buffer.byteLength(line, 'utf8');
    db.run('INSERT INTO events VALUES(?,?,?)', [event.event_seq, event.event_id, line]);
    db.run('INSERT INTO event_lines VALUES(?,?,?)', [event.event_seq, offset, length]);
    offset += length + 1;
  }
  db.exec(REFERENCE_TYPE_INDEX);
  if (manifest.checkpoint) {
    db.run('INSERT INTO world_state VALUES(1,?,?)',
      [manifest.checkpoint.state_version, canonicalJson(manifest.checkpoint.state)]);
  }
  return db;
}

/** @brief 页面脚本的源码；mutation 给出时把其中 file 那一份里恰好出现一次的 original 换成 replacement。 */
function pageScripts(mutation) {
  if (!mutation) return scripts;
  const target = scripts.filter(script => script.file === mutation.file);
  if (target.length !== 1 || target[0].source.split(mutation.original).length !== 2) {
    throw new Error(`Mutation anchor is not unique: ${mutation.file}`);
  }
  return scripts.map(script => script.file !== mutation.file ? script
    : {...script, source: script.source.replace(mutation.original, () => mutation.replacement)});
}

/** @brief 执行整段页面脚本，把用例事件写成导出后经页面的加载入口 openExport 打开，暴露真实索引和渲染结果。导出的选项见
 *  exportRun；reference 让页面用参照库代替自己建的索引，mutation 改写一份页面脚本，prepare(run) 在打开之前执行（改小
 *  读入字节数、替换计时等）。加载报错时关库后原样抛出。 */
async function viewer(events, options = {}) {
  const nodes = new Map();
  const errors = [];
  const get = selector => {
    if (!nodes.has(selector)) nodes.set(selector, element());
    return nodes.get(selector);
  };
  /** @brief 上下文内容块按钮取自渲染结果中的 data-context-block 属性，其余选择器没有元素。 */
  const selectAll = selector => {
    if (selector === '#act-body [data-calendar-row]') {
      return [...get('#act-body').innerHTML.matchAll(/data-calendar-row="(\d+)"[^>]*aria-pressed="([^"]+)"/g)]
        .map((match,index) => {
          const button = get(`calendar-bar:${index}`);
          button.dataset.calendarRow = match[1];
          button.setAttribute('aria-pressed',match[2]);
          return button;
        });
    }
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
    IntersectionObserver: class { observe() {} disconnect() {} },
    setTimeout, clearTimeout, requestAnimationFrame() {},
    TextDecoder, MessageChannel, performance,
  });
  for (const script of pageScripts(options.mutation)) {
    vm.runInContext(script.source, context, {filename: script.file});
  }
  const run = source => vm.runInContext(source, context);
  const state = run('S');
  state.SQL = {Database};
  options.prepare?.(run);
  let exported = null;
  /** @brief 写出一份导出并走页面的加载入口；reference 时页面的建索引换成参照库。 */
  const open = async (rows, opened) => {
    const next = exportRun(rows, opened);
    if (options.reference) run('ViewerIndex').build = async () => referenceIndex(next.lines, next.manifest);
    try {
      await run('openExport')(next.files, next.directories, opened.log ?? (() => {}));
    } catch (error) {
      if (state.db) { state.db.close(); state.db = null; }
      throw error;
    }
    exported = next;
  };
  await open(events, options);
  return {
    state, errors, run, get,
    /** 最后一次打开的导出（exportRun 的返回值）。 */
    get exported() { return exported; },
    activities() { run('renderActivities()'); return get('#act-body').innerHTML; },
    body() { run('renderBodyCurves()'); return get('#bd-body').innerHTML; },
    economy() { run('renderEconomy()'); return get('#eco-body').innerHTML; },
    memory() { run('renderMemory()'); return get('#mem-body').innerHTML; },
    overview() { run('renderOverview()'); return get('#ov-body').innerHTML; },
    async context() { await run('ContextView.render()'); return get('#ctx-body').innerHTML; },
    summary(sequence) { return state.events.find(event => event.seq === sequence).sum; },
    /** @brief 换一份导出：点「换一个文件」后经同一个加载入口打开下一份（选项同 exportRun，reference 照旧）。 */
    async reload(nextEvents, nextOptions = {}) {
      get('#btn-reload').onclick();
      await open(nextEvents, nextOptions);
    },
    close() { if (state.db) { state.db.close(); state.db = null; } },
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

module.exports = {CASES, ROOT, RUN_ID, epoch, viewer, event, droppedFile, fileHandle, directoryHandle,
  frozenCatalog, withCatalog, runManifest, runDirectory, assetFiles, tableRows, targets, exportRun, eventsFile,
  referenceIndex, canonicalJson, Database};
