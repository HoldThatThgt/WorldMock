'use strict';

/** @brief 查看器的事件索引：分段读入导出的 events.jsonl，在页面内的 sql.js 库里建与运行数据库同形的 events 表（三类大事件
 *  只留各视图批量读取的字段），记下每行的字节区间供按需读全文，并放入导出清单带的最终世界状态。 */
const ViewerIndex = {
  /** 每次从 events.jsonl 读入的字节数，与 Perfetto 读大文件的分段相同；用例可改小以覆盖跨段的行。 */
  readBytes: 32 * 1048576,
};
{
  /** observation 的 data 里进索引的键：时间线摘要、身体曲线与经济流水按类型批量读取的四个字段（buildIndex 的投影）。 */
  const OBSERVATION_DATA_KEEP = ['balance_fen', 'body', 'active_activities', 'calendar_context'];
  /** model_request 的 data.request.messages 进索引的条数：上下文索引只读第一条的 role 与 content（ContextView.index）。 */
  const REQUEST_MESSAGES_KEEP = 1;
  /** 建索引时连续占用页面的上限（毫秒），超过就让出一次事件循环。 */
  const YIELD_MS = 100;
  /** 索引库的表：events 与 world_state 与运行数据库同形，event_lines 记每行在 events.jsonl 里的字节起点与长度（不含换行）。 */
  const SCHEMA = `
CREATE TABLE events(event_seq INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, body TEXT NOT NULL);
CREATE TABLE event_lines(event_seq INTEGER PRIMARY KEY, offset INTEGER NOT NULL, length INTEGER NOT NULL);
CREATE TABLE world_state(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
version INTEGER NOT NULL, body TEXT NOT NULL);`;
  /** 与各视图按类型读取的 WHERE json_extract(body,'$.event_type')=… ORDER BY event_seq 同一写法的索引。 */
  const TYPE_INDEX = "CREATE INDEX events_type ON events(json_extract(body,'$.event_type'), event_seq)";

  /** @brief 一条事件在索引里的形状：observation 的 data 只留 OBSERVATION_DATA_KEEP，model_request 的
   *  data.request.messages 只留前 REQUEST_MESSAGES_KEEP 条，model_response 去掉 data.request；信封与其余字段原样。
   *  其余类型返回 null，索引写原行。 */
  function prune(event) {
    const data = event.data;
    if (event.event_type === 'observation') {
      return {...event, data: Object.fromEntries(OBSERVATION_DATA_KEEP.filter(key => key in data)
        .map(key => [key, data[key]]))};
    }
    if (event.event_type === 'model_request') {
      return {...event, data: {...data, request: {...data.request,
        messages: data.request.messages.slice(0, REQUEST_MESSAGES_KEEP)}}};
    }
    if (event.event_type === 'model_response') {
      const {request, ...rest} = data;
      return {...event, data: rest};
    }
    return null;
  }

  /** @brief 让出一次事件循环：经一条新的 MessageChannel 投递一条消息，收到后关闭通道再继续。隐藏的标签页里计时器
   *  每秒至多唤醒一次，消息投递不受这一限制，切到别的标签页时建索引不变慢。 */
  function yieldToPage() {
    return new Promise(resolve => {
      const channel = new MessageChannel();
      channel.port1.onmessage = () => { channel.port1.close(); resolve(); };
      channel.port2.postMessage(null);
    });
  }

  /** @brief 把跨段的几块字节拼成一行。 */
  function joined(parts) {
    const out = new Uint8Array(parts.reduce((total, part) => total + part.length, 0));
    let at = 0;
    for (const part of parts) { out.set(part, at); at += part.length; }
    return out;
  }

  /** @brief 按 readBytes 分段读 events.jsonl，以 0x0A 分行，跨段的行拼好后交给 onLine(字节, 行首偏移, 行号)。每占用页面
   *  YIELD_MS 毫秒让出一次事件循环，读过每一成写一行进度；最后一个换行之后还有字节说明文件被截断，报错。返回行数。 */
  async function readLines(file, onLine, log) {
    let lineStart = 0, parts = [], count = 0, tenths = 0, yielded = Date.now();
    for (let position = 0; position < file.size; position += ViewerIndex.readBytes) {
      const value = new Uint8Array(await file.slice(position, position + ViewerIndex.readBytes).arrayBuffer());
      let start = 0;
      for (let end = value.indexOf(10); end !== -1; end = value.indexOf(10, start)) {
        parts.push(value.subarray(start, end));
        onLine(parts.length === 1 ? parts[0] : joined(parts), lineStart, ++count);
        parts = [];
        start = end + 1;
        lineStart = position + start;
        if (Date.now() - yielded >= YIELD_MS) {
          await yieldToPage();
          yielded = Date.now();
        }
      }
      if (start < value.length) parts.push(value.subarray(start));
      const read = Math.min(position + ViewerIndex.readBytes, file.size);
      if (Math.floor(read * 10 / file.size) > tenths) {
        tenths = Math.floor(read * 10 / file.size);
        log(`· 已读 ${(read / 1048576).toFixed(1)} / ${(file.size / 1048576).toFixed(1)} MB（${count} 条事件）`);
      }
    }
    if (parts.length) throw new Error('events.jsonl does not end with a newline: the file is truncated');
    return count;
  }

  /** @brief 一行写进索引：UTF-8 严格解码、解析，按类型裁剪后写 events（不裁剪的类型写原行），字节区间写 event_lines。
   *  任何一步失败都写英文错误日志，并以带行号的错误抛出；sql.js 绑定出错时抛的是字符串，原样作原因。 */
  function indexLine(statements, decoder, bytes, offset, number) {
    try {
      const text = decoder.decode(bytes);
      const event = JSON.parse(text);
      const pruned = prune(event);
      statements.events.run([event.event_seq, event.event_id, pruned === null ? text : JSON.stringify(pruned)]);
      statements.lines.run([event.event_seq, offset, bytes.length]);
    } catch (error) {
      const cause = typeof error === 'string' ? error : error.message;
      console.error('Unindexable events.jsonl line', number, cause);
      throw new Error(`events.jsonl line ${number} could not be indexed: ${cause}`);
    }
  }

  /** @brief 从导出的 events.jsonl 建索引库：逐行写入，行数须等于导出清单的 artifacts.events.rows；建事件类型索引；
   *  导出清单带最终存档时写入 world_state 一行。任何一步失败都先关库再抛出。 */
  async function build(SQL, file, manifest, log) {
    const db = new SQL.Database();
    let built = false;
    try {
      db.exec(SCHEMA);
      db.exec('BEGIN');
      const statements = {events: db.prepare('INSERT INTO events VALUES(?,?,?)'),
        lines: db.prepare('INSERT INTO event_lines VALUES(?,?,?)')};
      const decoder = new TextDecoder('utf-8', {fatal: true});
      const count = await readLines(file, (bytes, offset, number) =>
        indexLine(statements, decoder, bytes, offset, number), log);
      statements.events.free();
      statements.lines.free();
      const rows = manifest.artifacts.events.rows;
      if (count !== rows) throw new Error(`events.jsonl holds ${count} events but the export manifest lists ${rows}`);
      db.exec(TYPE_INDEX);
      if (manifest.checkpoint) {
        db.run('INSERT INTO world_state VALUES(1,?,?)',
          [manifest.checkpoint.state_version, JSON.stringify(manifest.checkpoint.state)]);
      }
      db.exec('COMMIT');
      built = true;
      return db;
    } finally {
      if (!built) db.close();
    }
  }

  /** @brief SQL 控制台的索引说明（HTML）：有哪些表、哪三类事件的 body 裁剪了、各留什么、没有哪些表、完整的行到哪里读。 */
  function pruningNote() {
    return '查询的是页面从导出的 events.jsonl 建的索引，不是运行数据库。表：<code>events(event_seq, event_id, body)</code>' +
      ' · <code>event_lines(event_seq, offset, length)</code> · <code>world_state(singleton, version, body)</code>' +
      '（只有带最终存档的完整导出才有一行）。body 为 JSON，可用 <code>json_extract</code> 取字段。<br>' +
      `三类事件的 body 已裁剪：observation 的 data 只留 ${OBSERVATION_DATA_KEEP.join('、')}；` +
      `model_request 的 data.request.messages 只留前 ${REQUEST_MESSAGES_KEEP} 条；model_response 去掉 data.request。` +
      '其余事件的 body 与 events.jsonl 的原行相同。索引里没有 checkpoints 与 artifacts 表。<br>' +
      '完整的行在 events.jsonl 里：event_lines 是每行的字节起点与长度，时间线展开一行读的就是这一段。' +
      '要对完整事件、存档或素材写 SQL，用 <code>sqlite3</code> 以只读方式打开运行目录的 footprints.sqlite，' +
      '或用 <code>python scripts/inspect_run.py &lt;运行目录&gt; event &lt;序号&gt;</code>。';
  }

  Object.assign(ViewerIndex, {build, pruningNote});
}
