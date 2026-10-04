# 运行查看器对象名称

> 状态：FROZEN；「活动标题」按[一日运行保真规格](SPEC-dayrun-fidelity.md#冻结条款修订)「冻结条款修订」第 21 行修订（一日运行保真 RV1），修订部分已实现并通过离线验收（`tests/life/test_activity_title_embedding.py`、`tests/life/test_timeline_labels.py`、`tests/footprints/test_render_day.py`）；「运行内对象名称」按[个人事务与工作安排规格](SPEC-errands-workday.md#冻结条款修订)修订（[实施计划](research/errands-workday-implementation-plan.md)作业 RV1），修订部分已实现并通过离线验收的是控制请求摘要缺 `errands` 键即报错与唤醒原因 `errand_reminder_due` 的中文说法（`tests/tools/run_viewer.test.cjs` 的「session requests display each task with its own progress sentence」「decision starts show their wake reasons from the frozen vocabulary」，经 `tests/tools/test_run_viewer.py` 执行）；六种个人事务事件的中文说法已写进查看器的标签表，没有用例逐个断言；摘要写事务变更的种类与名称、事务事件写事务名称与商品名称两句在 `d8a41d3` 的查看器里没有对应实现，出入见「版本表」`2026-10-04-errands-workday-implemented` 行；修订记录见「版本表」。
> 日期：2026-10-04
> 验收记录：[查看器名称验收](run-viewer-names-verification.md)（修订前的行为）。

## 范围与依据

修复 `tools/run-viewer.html` 中活动表、时间线和经济流水丢失对象名称的问题。
只使用所选运行数据库内的证据，不修改模拟行为、历史记录或语料，不推测真实剧名。
沿用现有前端页面、sql.js 和 SQLite JSON 投影，不增加生产依赖。
页面与相邻上下文模块一起分发，模块边界见[上下文解析规格](SPEC-run-viewer-context.md)。

名称与内部身份分开处理，采用 [Activity Streams 的对象名称语义](https://www.w3.org/TR/activitystreams-vocabulary/#dfn-name)
及 [Grafana 的显示名称机制](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-standard-options/)。
名称是记录中的可读文本；对象编号用于关联和审计。

## 地点名称

所有观察事件的 `data.locations` 是地点名称来源，按 `location_id` 关联 `name`。
出行活动、出行请求和配送目的地必须使用实际地点名称，包括自定义名称的家、公司和市场。
最后一个人物的观察不得抹掉此前已记录的名称。

## 活动标题

活动受理和进展中的 `data.activity.title` 必须传到活动表与时间线，且优先于同目标的目录名称。
同一活动合并后保留自身最后记录的有效标题，不得被另一个活动的标题覆盖。
引擎为生活活动、观看、游戏、工作、通话与交谈都生成中文标题（[一日运行保真规格](SPEC-dayrun-fidelity.md) 7.4），
种类的中文说法以 `src/worldmock/life/readable.py` 的中文种类表为单一来源；活动表与时间线不出现 `call`、`work` 等英文种类名，
也不出现 `task-` 一类内部标识形态。
请求中的目标可通过本次运行记录的目标名称关联。
短剧台词选段必须保留原始描述，不猜测剧名，不将字幕段数推断为剧集集数。

## 运行内对象名称

名称索引累计本次运行所有观察中的商品名称、邮件主题、`data.tasks` 中的任务标题，以及浏览受理结果
`data.data.items` 中的内容标题和生活活动名称。电影、短剧、小说、游戏、商品和生活活动
均使用已记录的名称，不依赖编号前缀拼接“剧集 #0007”等替代名称。
`data.interaction_bindings` 中对象身份与可读对象名称的绑定只补充缺失名称，不能覆盖活动标题或目录名称。
活动表、时间线请求/受理/进展和经济流水共用这些名称；原始事件与 SQL 数据保持原样。

个人事务的名称取自记录本身：控制请求的 `errands[].name` 与个人事务事件载荷里的事务快照。
时间线的控制请求摘要按现行形状读取：请求须带 `errands` 键，缺键与其余读者一样报错，不留旧形状读取；
世界结构 8 的运行（含配对复跑的基线臂）用基线提交的查看器查看。
摘要在活动名与 summary 之后写事务变更的种类与事务名称；六种个人事务事件
（`errand_registered`、`errand_updated`、`errand_cancelled`、`errand_progressed`、`errand_completed`、`errand_reminded`）
与唤醒原因 `errand_reminder_due` 用中文说法显示并写事务名称，快照中的商品按既有名称索引显示，
缺名时照「缺失与重新加载」提示。事务内部身份不进活动表、时间线与请求摘要。

## 缺失与重新加载

非空且不同于对象编号的名称才有效。有对象编号但运行内没有有效名称时，
显示“名称缺失（对象编号）”，并输出英文错误日志，
不得静默显示裸编号或伪造作品名。没有目标的基础活动仍显示空目标。
更换数据库时清空名称与缺失记录，前一次运行的名称不得进入后一次运行。
删除外部 `items.json` 名称导入、旧 `content_catalog` 名称读取及固定地点名称表。
名称按纯文本展示，含 HTML 的名称不得变成可执行标记。

## 验证

回归测试执行页面实际 JavaScript 和 SQLite 查询，覆盖上述条款、不同人物的稀疏观察、
同一目标的不同活动标题、仅进展记录、缺失名称、HTML 转义与更换数据库。
真实运行验证重现“陈曦 14:25 出行”和“陈曦 20:20 看剧”的活动行。
提交后在干净隔离工作树执行 Uncle Bob 语义变异审查；临时变异仅限查看器源码，逐次恢复。

## 版本表

| 版本 | 日期 | 修订内容 |
| --- | --- | --- |
| `2026-09-29-dayrun-fidelity` | 2026-09-29 | 新增文件头与版本表。按[一日运行保真规格](SPEC-dayrun-fidelity.md#冻结条款修订)「冻结条款修订」第 21 行修订（一日运行保真 RV1，随该规格冻结）：「活动标题」补工作、通话与交谈也有中文标题，种类说法以 `src/worldmock/life/readable.py` 为单一来源，活动表与时间线不出现英文种类名与内部标识形态 |
| `2026-09-29-dayrun-fidelity-implemented` | 2026-09-29 | 一日运行保真规格实现后（按 `34a41ee` 的代码与测试核对）：工作、通话与交谈的中文标题已实现，文件头「定稿不等于已实现」改为修订部分已实现并通过离线验收，列出证据测试 `tests/life/test_activity_title_embedding.py`、`tests/life/test_timeline_labels.py` 与 `tests/footprints/test_render_day.py`；条款正文不变 |
| `2026-10-02-errands-workday` | 2026-10-02 | 按[个人事务与工作安排规格](SPEC-errands-workday.md#冻结条款修订)修订（[实施计划](research/errands-workday-implementation-plan.md)作业 RV1、RV2），修订部分在冻结时未随文实现（该规格阶段 2 实现后的核对见 `2026-10-04-errands-workday-implemented` 行）：「运行内对象名称」补个人事务名称的来源（控制请求与事件快照）、控制请求摘要写事务变更且缺 `errands` 键即报错（世界结构 8 的运行用基线提交的查看器查看）、六种个人事务事件与 `errand_reminder_due` 的中文说法；事务内部身份不进活动表与时间线；缺失提示规则不变 |
| `2026-10-04-errands-workday-implemented` | 2026-10-04 | 个人事务与工作安排规格实现后文档（[实施计划](research/errands-workday-implementation-plan.md)作业 RV3，按 `d8a41d3` 的查看器代码与测试核对）：`tools/run-viewer.html` 的 `controlItems` 要求请求级 `errands` 为数组、缺键即报错，`ENVK_LABELS` 增六种个人事务事件的中文说法，`WAKE_LABELS` 增 `errand_reminder_due`。证据是 `tests/tools/run_viewer.test.cjs` 的「session requests display each task with its own progress sentence」（缺 `errands` 键与 `errands` 为 null 都报错）与「decision starts show their wake reasons from the frozen vocabulary」（存储词表的 21 个唤醒原因逐个显示为中文），二者经 `tests/tools/test_run_viewer.py` 执行；六种事务事件的中文说法没有用例逐个断言。代码与条款的出入：「运行内对象名称」中「摘要在活动名与 summary 之后写事务变更的种类与事务名称」与六种事务事件「写事务名称，快照中的商品按既有名称索引显示」两句在该提交没有对应实现，控制请求摘要仍只写各项的活动名与 summary，事务事件只显示种类的中文说法；个人事务与工作安排规格 2.9 对查看器的要求（镜像实现同步为缺键报错，世界结构 8 的运行用基线提交的查看器查看）已满足。文件头据此改写，条款正文不变；`2026-10-02-errands-workday` 行里的冻结期状态注记改为指向本行 |
