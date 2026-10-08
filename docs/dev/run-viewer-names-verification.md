> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/run-viewer-names-verification.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 查看器对象名称验证

## 范围和源码身份

行为权威为 [查看器对象名称规格](SPEC-run-viewer-names.md)。
功能提交：`d7c7ea1ff78e3a631d3fa51e69b33c43adfb4996`。
补测提交：`036b10128aa127503f4c8572a6467bd831be9df4`，生产源码未变。
`tools/run-viewer.html` SHA-256：`34710e7d61009654e59a9468f2dc2300b9625f34740c3a8bcfd155eabeb06ce1`。

本次只修改查看器、回归测试及其文档，不修改模拟引擎或历史运行数据。

## 回归和相关门禁

```sh
node --test tests/tools/run_viewer.test.cjs
python -m pytest tests/tools/test_run_viewer.py tests/test_style_gate.py tests/test_documentation_gate.py tests/life/test_activity_title_embedding.py -q
git diff --check
```

Node.js v26.0.0 执行页面完整内联脚本，使用 `node:sqlite` 执行实际 SQL，最小 DOM 承接渲染输出。
Node 回归：13 项通过，零跳过。pytest：11 项通过，其中查看器包装执行上述全部 13 项 JavaScript 回归。
风格与文档门禁、活动标题写入回归均通过；`git diff --check` 通过。

| 要求 | 验证 |
| --- | --- |
| 地点名称累计 | 多人物稀疏观察、定制家/公司/市场名称、请求与受理及进展、配送地点 |
| 活动自身标题 | 同目标不同活动、后续标题更新、仅进展记录、无效标题不得覆盖有效标题 |
| 运行内名称 | 商品、邮件、事实任务、电影、短剧、小说、游戏和生活活动浏览结果 |
| 名称缺失 | 未知对象、旧固定信息流、Sintel、空白名称、名称等于编号、英文错误日志 |
| 更换运行 | 名称索引与缺失诊断清空，前次名称不进入后次运行 |
| 纯文本与旧路径删除 | 活动表/时间线/经济流水转义、原事件不变、外部 items.json 不再导入 |
| 分段语义 | 不从旧 content_catalog 或字幕段时长猜测剧集进度 |

## 真实运行记录

使用原页面同版本的 sql.js 1.10.3 JavaScript 与 WebAssembly，引入原数据库的内存副本，
执行实际 `buildIndex()`、`renderActivities()` 和时间线摘要生成；不是替代查询或名称函数。
输入为主工作树 `runs/deepseek-day-openrouter/footprints.sqlite`，1849 个事件、33 个合并活动。
输入 SHA-256：`f5bcf40df19b5920c3152c2b587ff9047fac7d9d9909d7d6983c83c7679e5f05`。

| 时间 | 人物 | 活动 | 验证后的对象 |
| --- | --- | --- | --- |
| 2026-01-05 14:25:01 | 陈曦 | 出行 | → 菜饭骨头汤 |
| 2026-01-05 20:20:03 | 陈曦 | 看剧 | 华语短剧台词选段（源编号 16013） |

原记录中的任务标题等于任务编号，TextWorld 游戏和段子信息流缺少名称；这些对象明确显示名称缺失，
不补造名称。短剧源素材没有真实剧名，保留原始台词选段标题。

浏览器工具的 URL 安全策略拒绝打开本地 `file://`，未进行浏览器目视验证，未绕过该限制。
本次验证覆盖实际查询、索引、标题合并与生成 HTML，不声明完成浏览器布局或拖放交互检查。


## 独立 Uncle Bob 审查

状态：COMPLETE。首轮逐条映射规格的 24 个行为要求组，没有实现缺失或偏离。
32 个有效独立语义突变最终均有 killed 因果证据，零剩余 survived，零 inconclusive；
另有一个未造成跨运行泄漏的无效等价尝试。所有突变仅作用于隔离工作树的查看器源码，
固定命令始终为 `node --test tests/tools/run_viewer.test.cjs`，不修改测试判定条件。

首轮在功能提交上得到 31 killed、1 survived、1 invalid。唯一存活项移除了 `collectNames`
中的 `row.label.trim() &&`，使空名称进入索引。调用者随后追加空名称回归，审查者在补测提交
的新隔离工作树上直接 `git apply` 原补丁，并逐字节确认补丁一致。
变异后只有新增测试失败：第 284 行活动目标列断言检出已有名称被空白覆盖、缺名提示消失；
其余 12 项通过。恢复后 13 项全部通过。首轮其余 31 项未重复执行，生产源码与既有测试未变。

两个隔离工作树均在逐次证明 `git diff --exit-code` 和完整 Git 状态为空后正常移除。
最后调用者 HEAD 为补测提交，diff/status 均为空；查看器 SHA-256 与上文一致。
原始补丁、逐项日志、完整报告及闭合报告保留于本机
`/tmp/worldmock-viewer-bob-report.oCJBgM`，本文件汇总交付证据。

### 首轮变异台账

下表保留首轮历史结果；空名称存活项已按上文原样复验闭合。
每行判定命令均为 `node --test tests/tools/run_viewer.test.cjs`；文件为 `tools/run-viewer.html`。
源码与测试行号对应首轮功能提交，新增测试后的行号以补测提交为准。

| 突变 | 规范行 | 生产行 | 结果/退出码 | 因果证据 |
|---|---|---|---|---|
| `locations_latest_observation_only`：仅保留最后一次观察的地点 | 15-17 | 397-400 | killed / 1 | 活动目标变为名称缺失，未保留前一个人物的菜饭骨头汤等地点。 测试断言行：137。 |
| `fixed_location_labels`：恢复家、公司、市场固定名称 | 16,39 | 320 | killed / 1 | 活动目标退回家/公司/市场，且配送摘要失去张江公寓。 测试断言行：137, 239。 |
| `travel_request_raw_destination`：出行请求直接显示目的地编号 | 16,31 | 546 | killed / 1 | 请求摘要不再包含记录中的地点名称。 测试断言行：138。 |
| `delivery_raw_destination`：配送请求直接显示目的地编号 | 16 | 552 | killed / 1 | 配送摘要不再包含张江公寓。 测试断言行：239。 |
| `accepted_title_not_projected`：受理摘要丢弃 activity.title | 21 | 466 | killed / 1 | 受理事件摘要不再包含其自身初始描述。 测试断言行：210。 |
| `progress_title_not_projected`：进展摘要丢弃 activity.title | 21 | 483 | killed / 1 | 合并活动停在初始描述，未更新为本次选段描述。 测试断言行：209。 |
| `activity_uses_global_target_title`：活动显示仅查全局目标索引 | 21-22 | 881 | killed / 1 | 两个活动均显示另一次选段描述，覆盖了第一个活动的自身标题。 测试断言行：209。 |
| `first_title_wins`：合并活动只接受首个有效标题 | 22 | 627 | killed / 1 | 活动仍为初始描述，未保留最新本次选段描述。 测试断言行：209。 |
| `empty_progress_erases_activity_title`：合并时空标题也写入活动 | 22,35 | 627 | killed / 1 | 空标题抹掉有效标题，显示落到另一个活动的名称。 测试断言行：209。 |
| `id_title_erases_activity_title`：合并时允许编号本身覆盖标题 | 22,35 | 627 | killed / 1 | 无效编号标题抹掉有效标题，显示落到另一个活动名称。 测试断言行：209。 |
| `progress_titles_not_indexed_for_requests`：全局名称索引忽略仅进展标题 | 23 | 416 | killed / 1 | 只有进展事件提供名称时，请求摘要丢失已记录标题。 测试断言行：225。 |
| `request_raw_target`：定向活动请求显示裸目标编号 | 23,31 | 548 | killed / 1 | 浏览目标与仅进展目标的请求摘要均失去名称。 测试断言行：190, 225。 |
| `guess_drama_episode_name`：根据 drama 编号伪造剧集名称 | 24,30,37 | 359 | killed / 1 | 实际显示剧集 #0007，精确目标断言要求原始台词选段描述。 测试断言行：189, 209, 224, 256, 272, 315, 334。 |
| `products_not_indexed`：不收集观察里的商品名称 | 28-30 | 393 | killed / 1 | 进食活动、配送请求和订单流水失去红烧牛肉面/菜饭等名称。 测试断言行：163, 240, 315。 |
| `mail_wrong_path`：从不存在的 mailbox 路径读取邮件 | 28 | 404 | killed / 1 | 邮件活动目标不再包含项目周会通知。 测试断言行：163。 |
| `tasks_old_path`：从 data.tasks 读取任务标题 | 28 | 394 | killed / 1 | 活动目标不再包含 facts.tasks 中的梳理客户访谈记录。 测试断言行：163。 |
| `browse_wrong_path`：浏览条目错误读取 data.items | 28-30 | 412 | killed / 1 | 电影、短剧、小说、游戏和生活活动均变为名称缺失。 测试断言行：189。 |
| `browse_ignores_lifestyle_identity`：忽略浏览条目 activity_id | 29-30 | 409 | killed / 1 | 晚间舒展变为名称缺失，其余作品名称仍正确。 测试断言行：189。 |
| `economy_raw_product`：经济流水改用商品裸编号 | 31 | 1064 | killed / 1 | 流水不再包含购买 红烧牛肉面。 测试断言行：166, 315。 |
| `empty_catalog_names_accepted`：名称索引接受空字符串/空白名称 | 35 | 382 | survived / 0 | 全部12测试仍通过；补充生产函数探针返回 {"has":true,"label":""}，证明空名称被接受。 |
| `id_catalog_names_accepted`：名称索引接受与编号相同的名称 | 35 | 382 | killed / 1 | 商品、邮件、任务、电影及地点均静默显示裸编号。 测试断言行：296。 |
| `missing_name_raw_id`：缺名提示改为裸编号 | 36-37 | 354 | killed / 1 | 缺失对象不再显示名称缺失（对象编号）。 测试断言行：256, 277, 296, 334, 348。 |
| `missing_name_no_error`：移除缺名英文错误日志 | 36 | 351 | killed / 1 | 缺失对象对应错误日志断言失败。 测试断言行：256, 272。 |
| `missing_name_chinese_error`：缺名错误日志改成中文 | 36 | 351 | killed / 1 | 禁止中文的日志断言失败。 测试断言行：262。 |
| `basic_activity_invented_target`：无目标活动伪造目标文字 | 37 | 883 | killed / 1 | 基础睡眠目标不再为空或破折号。 测试断言行：261。 |
| `reload_keeps_missing_diagnostics`：重载不清空缺名去重集合 | 38 | 1467 | killed / 1 | 第二次运行同一缺失对象没有新增错误日志。 测试断言行：278。 |
| `reload_keeps_name_index`：仅移除重载时 nameIdx=null | 38 | 1467 | invalid / 0 | 未达到预定泄漏：buildNameIndex 每次创建全新 Map 并替换 nameIdx。该无效突变不算测试缺口。 |
| `obsolete_catalog_reading_restored`：恢复观察 content_catalog 名称来源 | 39 | 394 | killed / 1 | 未知短剧被过时目录命名为历史外部名称，违反名称缺失断言。 测试断言行：334。 |
| `external_items_import_restored`：恢复目录 items.json 读取及命名 | 39 | 1414 | killed / 1 | items.json 文件读取次数从0变1，外部文件读取禁令的行为断言失败。 测试断言行：347。 |
| `activity_html_unescaped`：活动目标移除 HTML 转义 | 40 | 918 | killed / 1 | 活动目标未包含要求的转义文本。 测试断言行：315。 |
| `timeline_html_unescaped`：时间线摘要正文移除 HTML 转义 | 40 | 859 | killed / 1 | 时间线中出现原始 <img 标记，纯文本断言失败。 测试断言行：316。 |
| `economy_html_unescaped`：经济流水说明移除 HTML 转义 | 40 | 1106 | killed / 1 | 经济流水未包含要求的转义文本。 测试断言行：315。 |
| `reload_reuses_previous_location_map`：地点 Map 改为跨构建共享 | 38 | 390 | killed / 1 | 第二次运行显示前次地点，跨运行隔离断言失败。 测试断言行：276。 |
