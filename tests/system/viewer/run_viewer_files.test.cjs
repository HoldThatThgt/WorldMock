'use strict';

/** @brief 通过真实页面拖放回调验证 Chrome 文件句柄获取、目录递归和失败后的页面状态。 */
const assert = require('node:assert/strict');
const test = require('node:test');
const {viewer, event, exportRun, directoryHandle, fileHandle, droppedFile, Database} =
  require('./run_viewer_harness.cjs');

/** @brief 把句柄承诺交给实际绑定的 drop 回调；files 不应再与句柄重复收集。 */
function drop(view, items) {
  return view.get('#drop-zone').dispatch('drop', {
    preventDefault() {}, dataTransfer: {items,
      get files() { throw new Error('Dropped files must be read through their handles'); }},
  });
}

test('drop captures every file handle in the event tick before waiting for the engine', async t => {
  const rows = [event('observation', {})];
  const view = await viewer(rows);
  t.after(() => view.close());
  const sql = view.state.SQL;
  view.state.SQL = null;
  let releaseEngine;
  view.run('window').pendingSQL = new Promise(resolve => { releaseEngine = resolve; });
  view.run('loadSqlJs = () => window.pendingSQL');
  const exported = exportRun(rows);
  let inDrop = true;
  const captured = [];
  const handles = [...exported.directories, fileHandle('unused.txt', 'not read')];
  const items = handles.map(handle => ({kind: 'file', getAsFileSystemHandle() {
    assert.ok(inDrop, 'the drag data store is readable only during the drop event');
    captured.push(handle.name);
    return Promise.resolve(handle);
  }}));
  items.push({kind: 'string', getAsFileSystemHandle() { throw new Error('Text is not a file'); }});
  const pending = drop(view, items);
  inDrop = false;
  assert.deepEqual(captured, ['export', 'unused.txt']);
  assert.equal(view.state.loading, true);
  releaseEngine(sql);
  await pending;
  assert.equal(view.state.fileName, 'export/events.jsonl');
  assert.equal(view.state.events.length, 1);
  assert.equal(view.state.loading, false);
  assert.deepEqual(view.errors, []);
});

test('visible load time includes handle and first-engine waits through overview preparation', async t => {
  const rows=[event('observation',{})];
  const view=await viewer(rows);
  t.after(()=>view.close());
  const window=view.run('window');
  window.clock=100;
  window.sql=view.state.SQL;
  view.state.SQL=null;
  view.run('performance={now:()=>window.clock}; loadSqlJs=async()=>{window.clock+=2000; return window.sql;}');
  view.run('const previousOverview=renderOverview; renderOverview=()=>{previousOverview(); window.clock+=300;}');
  const exported=exportRun(rows);
  await drop(view,exported.directories.map(handle=>({kind:'file',getAsFileSystemHandle(){
    window.clock+=500;
    return Promise.resolve(handle);
  }})));
  assert.match(view.get('#run-meta').textContent,/ · 载入 2\.8 秒$/);
  assert.equal(view.state.view,'overview');
  assert.deepEqual(view.errors,[]);
});

test('dropping separate files and choosing files retain the export and its lazy original event body', async t => {
  const rows = [event('observation', {details: '原文'})];
  const view = await viewer(rows);
  t.after(() => view.close());
  const exported = exportRun(rows, {files: []});
  await drop(view, exported.files.map(file => ({kind: 'file',
    async getAsFileSystemHandle() { return {kind: 'file', name: file.name, async getFile() { return file; }}; }})));
  assert.equal(view.state.fileName, 'events.jsonl');
  assert.equal(await view.run('fetchBody(1)'), exported.lines[0]);
  view.get('#file-input').files = exported.files;
  await view.get('#file-input').onchange();
  assert.equal(await view.run('fetchBody(1)'), exported.lines[0]);
});

test('directory collection retains nested paths and first assets without opening event or asset files', async t => {
  const view = await viewer([event('observation', {})]);
  t.after(() => view.close());
  const sha = 'a'.repeat(64);
  const calls = [];
  const lazy = (name, value) => ({kind: 'file', name, async getFile() { calls.push(name); return value; }});
  const eventFile = {name: 'events.jsonl', size: 1_920_000_000,
    slice() { throw new Error('Collection must not read event bytes'); }};
  const firstAsset = droppedFile(sha, 'first');
  const ignored = Array.from({length: 130}, (_, index) => lazy(`unused-${index}`, null));
  const handles = [directoryHandle('execution', [directoryHandle('export', [
    lazy('events.jsonl', eventFile), directoryHandle('assets', [...ignored, lazy(sha, firstAsset)]),
  ]), directoryHandle('as2', [directoryHandle('agents', [directoryHandle('agent_0002', [
    fileHandle('MEMORY.md', '人物总结'),
  ])])])]), lazy(sha, droppedFile(sha, 'second'))];
  const dropped = await view.run('collectDropped')([], handles);
  assert.deepEqual(calls, []);
  assert.equal(dropped.events[0].path, 'execution/export/events.jsonl');
  assert.equal(dropped.assets.size, 131);
  assert.equal(view.state.memFiles['2'].memoryMd, '人物总结');
  assert.equal(await dropped.events[0].open(), eventFile);
  assert.equal(await dropped.assets.get(sha)(), firstAsset);
  assert.deepEqual(calls, ['events.jsonl', sha]);
});

for (const [name, item, reason] of [
  ['synchronous handle error', {kind: 'file', getAsFileSystemHandle() {
    throw new Error('File handle API unavailable');
  }}, 'File handle API unavailable'],
  ['rejected handle promise', {kind: 'file', getAsFileSystemHandle() {
    return Promise.reject(new Error('File handle access denied'));
  }}, 'File handle access denied'],
  ['missing handle', {kind: 'file', async getAsFileSystemHandle() { return null; }},
    'Could not access a dropped file or directory'],
  ['directory enumeration error', {kind: 'file', async getAsFileSystemHandle() {
    return {kind: 'directory', name: 'export', async *values() { throw new Error('Directory access denied'); }};
  }}, 'Directory access denied'],
]) {
  test(`the drop zone reports ${name} and closes the previous index`, async t => {
    const view = await viewer([event('observation', {})]);
    t.after(() => view.close());
    const previous = view.state.db;
    await drop(view, [item]);
    assert.equal(view.get('#drop-err').textContent, `加载失败：${reason}`);
    assert.ok(view.errors.includes(`Run viewer load failed: ${reason}`));
    assert.equal(view.state.loading, false);
    assert.equal(view.state.db, null);
    assert.equal(Database.live.has(previous), false);
    assert.equal(view.get('#app').classList.contains('on'), false);
    assert.equal(view.get('#drop-zone').classList.contains('hidden'), false);
  });
}

test('an events file getFile failure is reported before indexing starts', async t => {
  const view = await viewer([event('observation', {})]);
  t.after(() => view.close());
  const exported = exportRun([event('observation', {})], {files: []});
  const directory = directoryHandle('export', [fileHandle('manifest.json', exported.manifest),
    {kind: 'file', name: 'events.jsonl', async getFile() { throw new Error('Event file is no longer readable'); }}]);
  await drop(view, [{kind: 'file', async getAsFileSystemHandle() { return directory; }}]);
  assert.equal(view.get('#drop-err').textContent, '加载失败：Event file is no longer readable');
  assert.ok(view.errors.includes('Run viewer load failed: Event file is no longer readable'));
  assert.equal(view.state.db, null);
  assert.equal(view.state.loading, false);
});

test('a refused overlapping drop does not request new handles', async t => {
  const view = await viewer([event('observation', {})]);
  t.after(() => view.close());
  view.state.loading = true;
  await drop(view, [{kind: 'file', getAsFileSystemHandle() { throw new Error('Must not acquire a refused drop'); }}]);
  assert.equal(view.get('#drop-err').textContent, 'Another export is still loading; drop again after it finishes');
  assert.equal(view.state.loading, true);
});
