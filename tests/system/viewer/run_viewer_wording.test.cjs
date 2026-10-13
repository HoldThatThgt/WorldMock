'use strict';

const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const {CASES, ROOT, viewer, event, droppedFile, frozenCatalog, runManifest, targets} =
  require('./run_viewer_harness.cjs');

/** @brief 在独立上下文执行并列脚本 run-viewer-wording.js，同时收集它写的英文错误。 */
function wording() {
  const errors = [];
  const context = vm.createContext({console: {error: (...args) => errors.push(args.map(String).join(' '))}});
  vm.runInContext(readFileSync(path.join(ROOT, 'tools/run-viewer-wording.js'), 'utf8'), context,
    {filename: 'run-viewer-wording.js'});
  return {api: vm.runInContext('ViewerWording', context), errors};
}

/** @brief 名称表写成用例文件 names 一组的形状。 */
function plain(names) {
  const entries = map => Object.fromEntries(map);
  return {apps: entries(names.apps), goods: entries(names.goods), locations: entries(names.locations),
    travel_methods: entries(names.travel_methods),
    contents: Object.fromEntries([...names.contents].map(([key, {title, unit}]) => [key, {title, unit}]))};
}

/** @brief 按运行清单读用例文件 catalog 冻结的目录文件：素材按摘要取用，与页面传入的同一种「取得 File」函数。 */
async function loadCaseCatalog(api) {
  const assets = new Map(frozenCatalog().files.map(file => [file.name, async () => droppedFile(file.name, file.text)]));
  return api.loadCatalog(runManifest(), assets);
}

test('shared cases give the same names and wording as the Python implementation', async () => {
  const {api, errors} = wording();
  assert.deepEqual(plain(api.namesFrom(CASES.catalog)), CASES.names);
  assert.deepEqual([...await loadCaseCatalog(api)], []);
  for (const item of CASES.activities) {
    const orders = new Map(Object.entries(item.orders));
    assert.equal(api.activityPhrase(item.snapshot, item.content_ids, orders), item.text, item.name);
  }
  for (const item of CASES.phone) assert.equal(api.phonePhrase(item.data), item.text, item.name);
  assert.deepEqual(errors, []);
  // 目录里没有的身份：Python 一侧是带种类与身份的故障，查看器显示「名称缺失（身份）」并恰好写一次英文错误。
  for (const item of CASES.missing) {
    const before = errors.length;
    const orders = new Map(Object.entries(item.orders));
    assert.equal(api.activityPhrase(item.snapshot, item.content_ids, orders), item.viewer_text, item.name);
    assert.deepEqual(errors.slice(before), [`Missing recorded object name: ${item.identity}`], item.name);
  }
});

test('the events of one activity written into SQLite give the activity table target the same wording', async t => {
  // 每组事件照原样写进测试用的 SQLite（信封带活动身份），经页面的加载入口取各条事件指向的内容后读活动表的「目标」。
  const rows = CASES.events.flatMap(item => item.events.map(entry =>
    event(entry.type, entry.data, {activity_id: entry.data.activity.activity_id})));
  const view = await viewer(rows);
  t.after(() => view.close());
  assert.deepEqual(targets(view.activities()), CASES.events.map(item => item.text));
  assert.deepEqual(view.errors, []);
});

test('app use without content has no wording', async () => {
  // 带 app 而内容身份为空的互动：空的内容身份与别的身份一样去查名称，显示「名称缺失（null）」并恰好写一次英文错误，
  // 不写成只有 app 名的「使用美团」。
  const {api, errors} = wording();
  assert.deepEqual([...await loadCaseCatalog(api)], []);
  const snapshot = {kind: 'interact', location_id: 'home',
    data: {action: 'interact', app_id: 'meituan', content_id: null}};
  assert.equal(api.activityPhrase(snapshot, [api.eventContentId(undefined, snapshot.data.content_id)], new Map()),
    '使用美团《名称缺失（null）》');
  assert.deepEqual(errors, ['Missing recorded object name: null']);
});
