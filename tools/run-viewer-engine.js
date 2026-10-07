'use strict';

/* ================================================================
 * 运行查看器的 SQLite 引擎：官方 SQLite WASM 构建 + 分块只读文件。
 * 浏览器里单个 ArrayBuffer 不到 2 GiB，足迹库整块读不进一个数组；这里把文件按块读进内存，经一个只读 VFS 交给
 * SQLite，库的大小只受可用内存限制。页面其余部分只通过 prepare/bind/step/getAsObject/free 与 close 这组语句接口
 * 取数，不直接接触引擎。
 * ================================================================ */
const ViewerEngine = {};
{
  const VERSION = '3.50.4-build1';
  const SCRIPTS = [
    `https://cdn.jsdelivr.net/npm/@sqlite.org/sqlite-wasm@${VERSION}/sqlite-wasm/jswasm/sqlite3.js`,
    `https://unpkg.com/@sqlite.org/sqlite-wasm@${VERSION}/sqlite-wasm/jswasm/sqlite3.js`,
  ];
  const CHUNK_BYTES = 64 * 1048576;
  const VFS_NAME = 'viewer-chunks';
  /* 已登记的分块文件：VFS 路径 -> {size, chunkBytes, chunks}；同一页面先后打开的库各用一个路径 */
  const files = new Map();
  let opened = 0;

  /** @brief 插入一个脚本标签并等它执行完；引擎的 sqlite3.wasm 按脚本自身的地址就近取得。 */
  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = src;
      script.onload = resolve;
      script.onerror = () => reject(new Error('script load fail: ' + src));
      document.head.appendChild(script);
    });
  }

  /** @brief 依次尝试各 CDN 加载并初始化引擎，装上分块文件 VFS；都失败时报出各自的原因。
   *  引擎在页面主线程里装不上 OPFS 持久化 VFS，每次初始化都为此告警；查看器只读内存里的分块文件，用不到它，
   *  这一条不转给控制台，其余告警照常输出。 */
  async function load() {
    const errors = [];
    for (const src of SCRIPTS) {
      try {
        await loadScript(src);
        globalThis.sqlite3ApiConfig = {warn: (...args) => {
          if (!String(args[0]).includes('OPFS')) console.warn(...args);
        }};
        const sqlite3 = await globalThis.sqlite3InitModule({print: () => {}, printErr: text => console.error(text)});
        installVfs(sqlite3);
        return sqlite3;
      } catch (error) { errors.push(error.message); }
    }
    throw new Error('无法加载 SQLite 引擎（需联网访问 jsDelivr 或 unpkg 之一）。\n' + errors.join('\n'));
  }

  /** @brief 把文件按块读进内存：每块一个 Uint8Array，读完一块报告一次进度 progress(已读字节, 总字节)。
   *  读到的块比应有的短，说明文件在读取期间变了，不拿残缺的内容去建库。 */
  async function readChunks(file, progress, chunkBytes = CHUNK_BYTES) {
    const chunks = [];
    for (let offset = 0; offset < file.size; offset += chunkBytes) {
      const expected = Math.min(chunkBytes, file.size - offset);
      const chunk = new Uint8Array(await file.slice(offset, offset + expected).arrayBuffer());
      if (chunk.length !== expected) throw new Error('File changed while it was being read');
      chunks.push(chunk);
      progress(offset + expected, file.size);
    }
    return {size: file.size, chunkBytes, chunks};
  }

  /** @brief 从分块文件的 offset 处起拷贝到 dest，跨块的读取逐块接上；返回拷贝的字节数，读到文件末尾就停。 */
  function copyFromChunks(store, dest, offset) {
    const end = Math.min(store.size, offset + dest.length);
    let copied = 0;
    for (let position = offset; position < end;) {
      const chunk = store.chunks[Math.floor(position / store.chunkBytes)];
      const start = position % store.chunkBytes;
      const count = Math.min(chunk.length - start, end - position);
      dest.set(chunk.subarray(start, start + count), copied);
      copied += count;
      position += count;
    }
    return copied;
  }

  /** @brief 分块文件的读写方法：只读，读取从内存块拷进 SQLite 的缓冲区，读过文件末尾的部分补零并报短读；
   *  文件不会变（IMMUTABLE），锁与同步都是空操作。open 记着每个已打开的文件句柄对应哪个分块文件。 */
  function ioMethods(sqlite3, open) {
    const {capi, wasm} = sqlite3;
    return {
      xCheckReservedLock: (pFile, pOut) => { wasm.poke32(pOut, 0); return 0; },
      xClose: pFile => { open.delete(pFile); return 0; },
      xDeviceCharacteristics: () => capi.SQLITE_IOCAP_IMMUTABLE,
      xFileControl: () => capi.SQLITE_NOTFOUND,
      xFileSize: (pFile, pSize) => { wasm.poke64(pSize, BigInt(open.get(pFile).size)); return 0; },
      xLock: () => 0,
      xRead: (pFile, pDest, count, offset) => {
        const dest = wasm.heap8u().subarray(pDest, pDest + count);
        const copied = copyFromChunks(open.get(pFile), dest, Number(offset));
        if (copied === count) return 0;
        dest.fill(0, copied);
        return capi.SQLITE_IOERR_SHORT_READ;
      },
      xSectorSize: () => 4096,
      xSync: () => 0,
      xTruncate: () => capi.SQLITE_READONLY,
      xUnlock: () => 0,
      xWrite: () => capi.SQLITE_READONLY,
    };
  }

  /** @brief 分块文件的 VFS 方法：只认得已登记的路径，打开时把文件句柄接到上面的读写方法；不建文件、不删文件。 */
  function vfsMethods(sqlite3, open, io) {
    const {capi, wasm} = sqlite3;
    return {
      xAccess: (pVfs, zName, flags, pOut) => { wasm.poke32(pOut, files.has(wasm.cstrToJs(zName)) ? 1 : 0); return 0; },
      xCurrentTime: (pVfs, pOut) => { wasm.poke(pOut, 2440587.5 + Date.now() / 86400000, 'double'); return 0; },
      xCurrentTimeInt64: (pVfs, pOut) => { wasm.poke(pOut, 2440587.5 * 86400000 + Date.now(), 'i64'); return 0; },
      xDelete: () => capi.SQLITE_IOERR_DELETE,
      xFullPathname: (pVfs, zName, nOut, pOut) => wasm.cstrncpy(pOut, zName, nOut) < nOut ? 0 : capi.SQLITE_CANTOPEN,
      xGetLastError: () => 0,
      xOpen: (pVfs, zName, pFile, flags, pOutFlags) => {
        const store = zName ? files.get(wasm.cstrToJs(zName)) : null;
        if (!store) return capi.SQLITE_CANTOPEN;
        open.set(pFile, store);
        const handle = new capi.sqlite3_file(pFile);
        handle.$pMethods = io.pointer;
        handle.dispose();
        wasm.poke32(pOutFlags, capi.SQLITE_OPEN_READONLY);
        return 0;
      },
    };
  }

  /** @brief 向引擎登记分块文件 VFS；随机数与休眠沿用引擎默认 VFS 的实现。 */
  function installVfs(sqlite3) {
    const {capi, wasm} = sqlite3, open = new Map();
    const io = new capi.sqlite3_io_methods();
    io.$iVersion = 1;
    sqlite3.vfs.installVfs({io: {struct: io, methods: ioMethods(sqlite3, open)}});
    const vfs = new capi.sqlite3_vfs(), base = new capi.sqlite3_vfs(capi.sqlite3_vfs_find(null));
    vfs.$iVersion = 2;
    vfs.$szOsFile = capi.sqlite3_file.structInfo.sizeof;
    vfs.$mxPathname = 512;
    vfs.$zName = wasm.allocCString(VFS_NAME);
    vfs.$xRandomness = base.$xRandomness;
    vfs.$xSleep = base.$xSleep;
    base.dispose();
    sqlite3.vfs.installVfs({vfs: {struct: vfs, methods: vfsMethods(sqlite3, open, io)}});
  }

  /** @brief 把引擎的数据库包成页面使用的语句接口；close 关库并执行 release（放掉分块文件占的内存）。 */
  function wrap(db, release) {
    return {
      prepare(sql) {
        const statement = db.prepare(sql);
        return {
          bind(values) { if (values.length) statement.bind(values); },
          step() { return statement.step(); },
          getAsObject() { return statement.get({}); },
          free() { statement.finalize(); },
        };
      },
      close() { db.close(); release(); },
    };
  }

  /** @brief 以只读、不变（immutable：不加锁、不找日志文件）的方式打开一个分块文件；临时表与排序都放内存。 */
  function open(sqlite3, store) {
    const path = `/run-${++opened}.sqlite`;
    files.set(path, store);
    try {
      const db = new sqlite3.oo1.DB({filename: `file:${path}?immutable=1`, flags: 'r', vfs: VFS_NAME});
      db.exec('PRAGMA temp_store=MEMORY');
      return wrap(db, () => files.delete(path));
    } catch (error) {
      files.delete(path);
      throw error;
    }
  }

  Object.assign(ViewerEngine, {load, readChunks, copyFromChunks, wrap, open, installVfs});
}
