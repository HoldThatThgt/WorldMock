'use strict';

/** @brief 查看器的显示写法：src/worldmock/footprints/display_wording.py 的镜像，两份实现由
 *  tests/fixtures/display-wording-cases.json 约束。名称只取本库运行清单登记的冻结目录文件
 *  （docs/design/05-recording-engine.md「显示写法与检查工具」）。查看器是容错的阅读工具，
 *  取不到名称时显示「名称缺失（身份）」并写英文错误，不像 Python 一侧那样报运行故障。 */
const ViewerWording = {};
{
  /* 显示名称读的五份目录：运行清单 environment_assets 里的素材名与名称表的字段；出行方式文件可选。 */
  const CATALOGS = [
    {asset: 'catalog/app-catalog.jsonl', table: 'apps', required: true},
    {asset: 'catalog/content-catalog.jsonl', table: 'contents', required: true},
    {asset: 'catalog/merchant-catalog.jsonl', table: 'goods', required: true},
    {asset: 'catalog/location.jsonl', table: 'locations', required: true},
    {asset: 'catalog/travel-method.jsonl', table: 'travel_methods', required: false},
  ];
  /* 名称种类到名称表字段。 */
  const KIND_TABLES = {app: 'apps', content: 'contents', goods: 'goods', location: 'locations',
    travel_method: 'travel_methods'};
  /* 引擎自带的步行不在目录里，名称固定。 */
  const WALKING = ['walking', '步行'];
  /* 前台区间三个步骤的中文名；查看器会打开别的版本的运行，闭集之外的步骤照原值显示。 */
  const STEP_LABELS = {navigation: '导航', ordering: '下单', payment: '付款'};
  /* 三种手机事件的种类文字。 */
  const PHONE_LABELS = {app_foreground: 'app 前台', order_notification: '订单通知', payment_notification: '消费通知'};
  /* 没有专门写法的种类写的种类名：worldmock.life.readable.ACTIVITY_KIND_LABELS 的镜像。 */
  const ACTIVITY_KIND_LABELS = {sleep: '睡觉', travel: '出行', interact: '互动', purchase: '购买'};

  /** @brief 同一个键只写一次英文错误的报告函数；页面传入它自己的（随换库清空），单独加载时用这一个。 */
  function onceReporter() {
    const seen = new Set();
    return (key, ...message) => {
      if (seen.has(key)) return;
      seen.add(key);
      console.error(...message);
    };
  }

  /** @brief 由五组目录行建名称表：app、商品、地点、出行方式是身份到名称，内容是身份到 {title, unit, series}。
   *  单元名有系列取系列名，否则等于标题；地点有分店时写「名称（分店）」；步行固定写「步行」。行只要求是 JSON 对象，
   *  多出来的字段不管。 */
  function namesFrom(rows) {
    const table = (key, list, label) => new Map((list ?? []).map(row => [row[key], label(row)]));
    return {
      apps: table('app_id', rows.apps, row => row.name),
      contents: table('content_id', rows.contents, row => ({title: row.title,
        unit: row.series == null ? row.title : row.series.title, series: row.series != null})),
      goods: table('goods_id', rows.goods, row => row.name),
      locations: table('location_id', rows.locations,
        row => row.branch_label == null ? row.name : `${row.name}（${row.branch_label}）`),
      travel_methods: new Map([WALKING, ...table('travel_method_id', rows.travel_methods, row => row.name)]),
    };
  }

  /* 当前的名称表与缺名报告函数：读目录文件时建立，换一个数据库时清空。 */
  let current = {names: namesFrom({}), report: onceReporter()};

  /** @brief 清空名称表（只剩步行）并换上缺名报告函数；换一个数据库时调用。 */
  function reset(report = onceReporter()) {
    current = {names: namesFrom({}), report};
  }

  /** @brief 目录文件逐行解析成 JSON 对象，空行跳过；有一行不是 JSON 对象时整份读不了，返回 null。 */
  function parseLines(text) {
    const rows = [];
    for (const line of text.split('\n')) {
      if (!line.trim()) continue;
      let row;
      try {
        row = JSON.parse(line);
      } catch (error) {
        return null;
      }
      if (row === null || typeof row !== 'object' || Array.isArray(row)) return null;
      rows.push(row);
    }
    return rows;
  }

  /** @brief 写一句带素材名的英文错误，返回 null：这一份目录按缺失处理。 */
  function failed(message, asset) {
    console.error(message, asset);
    return null;
  }

  /** @brief 按清单登记的摘要在同一次拖入的素材里找一份目录文件并读成行；清单里没有可选的出行方式文件时是零行。
   *  文件不在、字节数与清单不符、某行不是 JSON 对象时写对应的英文错误并返回 null。不在浏览器里重算摘要。 */
  async function readCatalog(catalog, environment, assets) {
    const reference = environment[catalog.asset];
    if (reference == null && !catalog.required) return [];
    const open = reference == null ? undefined : assets.get(reference.sha256);
    if (!open) return failed('Frozen catalog file is missing:', catalog.asset);
    const file = await open();
    if (file.size !== reference.size_bytes) {
      return failed('Frozen catalog file size disagrees with the run manifest:', catalog.asset);
    }
    return parseLines(await file.text()) ?? failed('Frozen catalog file is unreadable:', catalog.asset);
  }

  /** @brief 读本库运行清单（运行清单本身或导出清单里的 frozen_run_manifest）登记的冻结目录，建好当前的名称表。
   *  assets 是文件名到「取得 File」的函数，记的是同一次拖入的素材；返回没有读到的必需目录的素材名（可选的出行方式文件
   *  读不到时只写英文错误）。没有属于本库的运行清单时写一次英文错误，四份必需目录都算没有读到，全部名称缺失。 */
  async function loadCatalog(manifest, assets, report) {
    reset(report);
    const required = CATALOGS.filter(catalog => catalog.required).map(catalog => catalog.asset);
    if (manifest == null) {
      console.error('Frozen catalog is unavailable: no run manifest for this database');
      return required;
    }
    const rows = {};
    for (const catalog of CATALOGS) {
      rows[catalog.table] = await readCatalog(catalog, manifest.environment_assets ?? {}, assets);
    }
    current.names = namesFrom(rows);
    return CATALOGS.filter(catalog => catalog.required && rows[catalog.table] === null).map(catalog => catalog.asset);
  }

  /** @brief 缺名的显示文字「名称缺失（身份）」，同一个身份只写一次英文错误。 */
  function missing(identity) {
    current.report(String(identity), 'Missing recorded object name:', identity);
    return `名称缺失（${identity}）`;
  }

  /** @brief 按种类（app、content、goods、location、travel_method）查名称：内容返回 {title, unit, series}，其余返回
   *  名称文字；查不到时是缺名的显示文字。 */
  function name(kind, identity) {
    const table = current.names[KIND_TABLES[kind]];
    if (table.has(identity)) return table.get(identity);
    const text = missing(identity);
    return kind === 'content' ? {title: text, unit: text, series: false} : text;
  }

  /** @brief 一条受理或进度事件指向的内容身份：进度事件带 data.content 时取其中的 content_id（这一段真正放过或做过的
   *  那一条），否则取活动快照的 data.content_id。一集做到头的那条进度事件，快照已经换成下一集，所以不能一律读快照。 */
  function eventContentId(played, snapshotContent) {
    return played ?? snapshotContent ?? null;
  }

  /** @brief 去掉相邻的同一条内容（按身份判断，不按标题文字），其余照先后保留。 */
  function distinct(contentIds) {
    return contentIds.filter((key, index) => index === 0 || key !== contentIds[index - 1]);
  }

  /** @brief 各条内容的标题依次各加一个书名号，书名号之间不加字。 */
  function titles(contentIds) {
    return contentIds.map(key => `《${name('content', key).title}》`).join('');
  }

  /** @brief 用 app 写「使用<app 名>《标题》……」；在地点做事写「在<地点>：<单元名>」，系列再列各集。 */
  function interactPhrase(snapshot, contentIds) {
    const appId = snapshot.data?.app_id;
    if (appId != null) {
      // 过渡写法：内容轨道并入之前，没有内容的 app 使用仍会产生，空的内容身份跳过不写（收尾时删去）。
      return `使用${name('app', appId)}${titles(contentIds.filter(key => key != null))}`;
    }
    const place = name('location', snapshot.location_id);
    const first = name('content', contentIds[0]);
    return first.series ? `在${place}：${first.unit}${titles(contentIds)}` : `在${place}：${first.unit}`;
  }

  /** @brief 商品名列表的各项：按商品身份升序，同一商品的几份合写成「名称×份数」。 */
  function goodsItems(goodsIds) {
    const counts = new Map();
    for (const key of goodsIds) counts.set(key, (counts.get(key) ?? 0) + 1);
    return [...counts.keys()].sort().map(key => counts.get(key) === 1
      ? name('goods', key) : `${name('goods', key)}×${counts.get(key)}`);
  }

  /** @brief 一张订单的渠道：带下单 app 写「（经<app 名>下单）」，到店写「（到店）」。 */
  function orderChannel(appId) {
    return appId == null ? '（到店）' : `（经${name('app', appId)}下单）`;
  }

  /** @brief 购买按订单分段：带下单 app 的订单按 app 身份升序，到店订单排最后，段间用「；」；订单记录里找不到的订单
   *  在最后各占一段，显示「名称缺失（订单身份）」。 */
  function purchasePhrase(snapshot, orders) {
    const grouped = new Map(), lost = [];
    for (const orderId of snapshot.data?.order_ids ?? []) {
      const order = orders.get(orderId);
      if (!order) {
        lost.push(missing(orderId));
        continue;
      }
      const key = order.app_id ?? null;
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(order.goods_id);
    }
    const apps = [...grouped.keys()].filter(key => key !== null).sort();
    const channels = grouped.has(null) ? [...apps, null] : apps;
    const segments = channels.map(appId => goodsItems(grouped.get(appId)).join('、') + orderChannel(appId));
    return `购买${[...segments, ...lost].join('；')}`;
  }

  /** @brief 一项活动的写法：互动、购买、出行各有专门的写法，其余种类写中文种类名，种类名表里没有的写「—」。
   *  contentIds 是这一行各条事件指向的内容身份（按事件先后，相邻重复在这里去掉）；orders 是订单身份到商品履约记录
   *  （读 goods_id 与 app_id）的 Map。快照只读 kind、location_id、destination_id、travel_method_id、data.app_id、
   *  data.order_ids。 */
  function activityPhrase(snapshot, contentIds, orders) {
    if (snapshot.kind === 'interact') return interactPhrase(snapshot, distinct(contentIds));
    if (snapshot.kind === 'purchase') return purchasePhrase(snapshot, orders);
    if (snapshot.kind === 'travel') {
      return `${name('travel_method', snapshot.travel_method_id)}去${name('location', snapshot.destination_id)}`;
    }
    return ACTIVITY_KIND_LABELS[snapshot.kind] ?? '—';
  }

  /** @brief 是不是三种手机事件之一（app 前台区间、订单通知、消费通知）。 */
  function isPhone(kind) {
    return Object.hasOwn(PHONE_LABELS, kind);
  }

  /** @brief 一条手机事件的写法「<种类> · <app 名> · <步骤或标题>」；app 名按事件的 app_id 取。 */
  function phonePhrase(data) {
    const detail = data.kind === 'app_foreground' ? (STEP_LABELS[data.step] ?? data.step) : data.title;
    return `${PHONE_LABELS[data.kind]} · ${name('app', data.app_id)} · ${detail}`;
  }

  Object.assign(ViewerWording, {loadCatalog, reset, namesFrom, name, eventContentId, activityPhrase, orderChannel,
    isPhone, phonePhrase});
}
