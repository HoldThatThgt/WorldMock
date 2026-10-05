<a name="spec-run-viewer-names-运行查看器对象名称"></a>
# 运行查看器对象名称

> 状态：FROZEN；「活动标题」按[一日运行保真规格](../VERIFICATION.md#verification-incremental-evidence)「冻结条款修订」第 21 行修订（一日运行保真 RV1），修订部分已实现并通过离线验收（`tests/life/test_activity_title_embedding.py`、`tests/life/test_timeline_labels.py`、`tests/footprints/test_render_day.py`）；「运行内对象名称」按[个人事务与工作安排规格](../VERIFICATION.md#verification-incremental-evidence)修订（[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md)作业 RV1），「运行内对象名称」与「缺失与重新加载」另按查看器实现的独立核对补充三处（商品名称的补缺来源 `data.errand_item_bindings`、事务变更的逐条读取规则、个人事务的缺失提示），两次修订的条款都已实现并通过离线验收：缺 `errands` 键即报错与六种事务事件、事务提醒的中文说法随该规格阶段 2 的代码实现，请求摘要写事务变更、事务事件与事务提醒写事务名称与商品名称由 `1346e0d` 实现，补充的三处与对 `1346e0d` 独立核对后的修正由 `6c81126` 实现（`tests/tools/run_viewer.test.cjs`，经 `tests/tools/test_run_viewer.py` 执行；逐条证据见「版本表」`2026-10-04-errands-viewer` 行）；修订记录见「版本表」；按[让「等待」成为例外规格](SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)修订「运行内对象名称」（阶段 1）。
> 日期：2026-10-05
> 验收记录：[查看器名称验收](run-viewer-names-verification.md)（修订前的行为）。

<a name="spec-run-viewer-names-范围与依据"></a>
## 范围与依据

修复 `tools/run-viewer.html` 中活动表、时间线和经济流水丢失对象名称的问题。
只使用所选运行数据库内的证据，不修改模拟行为、历史记录或语料，不推测真实剧名。
沿用现有前端页面、sql.js 和 SQLite JSON 投影，不增加生产依赖。
页面与相邻上下文模块一起分发，模块边界见[上下文解析规格](SPEC-run-viewer-context.md#spec-run-viewer-context-运行查看器上下文解析)。

名称与内部身份分开处理，采用 [Activity Streams 的对象名称语义](https://www.w3.org/TR/activitystreams-vocabulary/#dfn-name)
及 [Grafana 的显示名称机制](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-standard-options/)。
名称是记录中的可读文本；对象编号用于关联和审计。

<a name="spec-run-viewer-names-地点名称"></a>
## 地点名称

所有观察事件的 `data.locations` 是地点名称来源，按 `location_id` 关联 `name`。
出行活动、出行请求和配送目的地必须使用实际地点名称，包括自定义名称的家、公司和市场。
最后一个人物的观察不得抹掉此前已记录的名称。

<a name="spec-run-viewer-names-活动标题"></a>
## 活动标题

活动受理和进展中的 `data.activity.title` 必须传到活动表与时间线，且优先于同目标的目录名称。
同一活动合并后保留自身最后记录的有效标题，不得被另一个活动的标题覆盖。
引擎为生活活动、观看、游戏、工作、通话与交谈都生成中文标题（[一日运行保真规格](SPEC-life-runtime.md#dayrun-fidelity-身体与需要) 7.4），
种类的中文说法以 `src/worldmock/life/readable.py` 的中文种类表为单一来源；活动表与时间线不出现 `call`、`work` 等英文种类名，
也不出现 `task-` 一类内部标识形态。
请求中的目标可通过本次运行记录的目标名称关联。
短剧台词选段必须保留原始描述，不猜测剧名，不将字幕段数推断为剧集集数。

<a name="spec-run-viewer-names-运行内对象名称"></a>
## 运行内对象名称

名称索引累计本次运行所有观察中的商品名称、邮件主题、`data.tasks` 中的任务标题，以及浏览受理结果
`data.data.items` 中的内容标题和生活活动名称。电影、短剧、小说、游戏、商品和生活活动
均使用已记录的名称，不依赖编号前缀拼接“剧集 #0007”等替代名称。
`data.interaction_bindings` 中对象身份与可读对象名称的绑定只补充缺失名称，不能覆盖活动标题或目录名称。
`data.errand_item_bindings` 中商品身份与商品名的绑定同样只补充缺失名称，排在 `data.interaction_bindings` 之后：
个人事务可以指目录里的任一商品，`data.products` 只列此刻看得到或持有的，事务快照里的商品名由这份绑定补上。
活动表、时间线请求/受理/进展和经济流水共用这些名称；原始事件与 SQL 数据保持原样。

个人事务的名称取自记录本身：控制请求的 `errands[].name` 与个人事务事件载荷里的事务快照。
时间线的控制请求摘要按现行形状读取：请求须带 `errands` 键，缺键与其余读者一样报错，不留旧形状读取；
`errands` 的每条变更与 `control_errands` 同一规则读取：须是对象且八个键齐全（只查键在不在，不查值的类型），
不符同样报错；种类与名称照请求所写显示。
世界结构 8 的运行（含配对复跑的基线臂）用基线提交的查看器查看。
摘要在活动名与 summary 之后写事务变更的种类与事务名称；六种个人事务事件
（`errand_registered`、`errand_updated`、`errand_cancelled`、`errand_progressed`、`errand_completed`、`errand_reminded`）
与唤醒原因 `errand_reminder_due` 用中文说法显示并写事务名称，快照中的商品按既有名称索引显示，
缺名时照「缺失与重新加载」提示。事务内部身份不进活动表、时间线与请求摘要。
唤醒原因 `shift_start`、`shift_end`、`break_start`、`break_end`（[让「等待」成为例外规格](SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)的班次与休息时段起止）用中文说法「班次开始」「班次结束」「休息时段开始」「休息时段结束」显示；它们没有伴随事件，不带名称，不适用「缺失与重新加载」的缺失提示。

<a name="spec-run-viewer-names-缺失与重新加载"></a>
## 缺失与重新加载

非空且不同于对象编号的名称才有效。有对象编号但运行内没有有效名称时，
显示“名称缺失（对象编号）”，并输出英文错误日志，
不得静默显示裸编号或伪造作品名。没有目标的基础活动仍显示空目标。
个人事务的快照没有有效名称（空白，或与事务内部身份相同）时显示“名称缺失（个人事务）”：事务内部身份不进时间线，
括号里写对象种类而不写编号，英文错误日志写事件序号。唤醒原因 `errand_reminder_due` 找不到同一人物在同一时刻、
排在该决策之前的事务提醒事件时，在原因说法之后显示同一提示，并输出英文错误日志。
更换数据库时清空名称与缺失记录，前一次运行的名称不得进入后一次运行。
删除外部 `items.json` 名称导入、旧 `content_catalog` 名称读取及固定地点名称表。
名称按纯文本展示，含 HTML 的名称不得变成可执行标记。

<a name="spec-run-viewer-names-验证"></a>
## 验证

回归测试执行页面实际 JavaScript 和 SQLite 查询，覆盖上述条款、不同人物的稀疏观察、
同一目标的不同活动标题、仅进展记录、缺失名称、HTML 转义与更换数据库。
真实运行验证重现“陈曦 14:25 出行”和“陈曦 20:20 看剧”的活动行。
提交后在干净隔离工作树执行 Uncle Bob 语义变异审查；临时变异仅限查看器源码，逐次恢复。

<a name="spec-run-viewer-names-版本表"></a>
## 版本表

| 版本 | 日期 | 修订内容 |
| --- | --- | --- |
| `2026-09-29-dayrun-fidelity` | 2026-09-29 | 新增文件头与版本表。按[一日运行保真规格](../VERIFICATION.md#verification-incremental-evidence)「冻结条款修订」第 21 行修订（一日运行保真 RV1，随该规格冻结）：「活动标题」补工作、通话与交谈也有中文标题，种类说法以 `src/worldmock/life/readable.py` 为单一来源，活动表与时间线不出现英文种类名与内部标识形态 |
| `2026-09-29-dayrun-fidelity-implemented` | 2026-09-29 | 一日运行保真规格实现后（按 `34a41ee` 的代码与测试核对）：工作、通话与交谈的中文标题已实现，文件头「定稿不等于已实现」改为修订部分已实现并通过离线验收，列出证据测试 `tests/life/test_activity_title_embedding.py`、`tests/life/test_timeline_labels.py` 与 `tests/footprints/test_render_day.py`；条款正文不变 |
| `2026-10-02-errands-workday` | 2026-10-02 | 按[个人事务与工作安排规格](../VERIFICATION.md#verification-incremental-evidence)修订（[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md)作业 RV1、RV2），修订部分在冻结时未随文实现（该规格阶段 2 实现后的核对见 `2026-10-04-errands-workday-implemented` 行）：「运行内对象名称」补个人事务名称的来源（控制请求与事件快照）、控制请求摘要写事务变更且缺 `errands` 键即报错（世界结构 8 的运行用基线提交的查看器查看）、六种个人事务事件与 `errand_reminder_due` 的中文说法；事务内部身份不进活动表与时间线；缺失提示规则不变 |
| `2026-10-04-errands-workday-implemented` | 2026-10-04 | 个人事务与工作安排规格实现后文档（[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md)作业 RV3，按 `d8a41d3` 的查看器代码与测试核对）：`tools/run-viewer.html` 的 `controlItems` 要求请求级 `errands` 为数组、缺键即报错，`ENVK_LABELS` 增六种个人事务事件的中文说法，`WAKE_LABELS` 增 `errand_reminder_due`。证据是 `tests/tools/run_viewer.test.cjs` 的「session requests display each task with its own progress sentence」（缺 `errands` 键与 `errands` 为 null 都报错）与「decision starts show their wake reasons from the frozen vocabulary」（存储词表的 21 个唤醒原因逐个显示为中文），二者经 `tests/tools/test_run_viewer.py` 执行；六种事务事件的中文说法没有用例逐个断言。代码与条款的出入：「运行内对象名称」中「摘要在活动名与 summary 之后写事务变更的种类与事务名称」与六种事务事件「写事务名称，快照中的商品按既有名称索引显示」两句在该提交没有对应实现，控制请求摘要仍只写各项的活动名与 summary，事务事件只显示种类的中文说法；个人事务与工作安排规格 2.9 对查看器的要求（镜像实现同步为缺键报错，世界结构 8 的运行用基线提交的查看器查看）已满足。文件头据此改写，条款正文不变；`2026-10-02-errands-workday` 行里的冻结期状态注记改为指向本行。本行登记的两句出入其后由 `1346e0d` 与 `6c81126` 补齐，见 `2026-10-04-errands-viewer` 行 |
| `2026-10-04-errands-viewer` | 2026-10-04 | 查看器补齐个人事务条款（`1346e0d`）与对该提交独立核对后的修正（`6c81126`）。**条款补充**（核对时把真实引擎写出的行载入查看器发现，原条款没有覆盖）：①「运行内对象名称」的名称索引增 `data.errand_item_bindings`（商品身份到商品名）为只补缺的来源，排在 `data.interaction_bindings` 之后——引擎的 `data.products` 只列此刻看得到或持有的商品，事务却可以指目录里的任一商品，按原条款列举的来源，人在家里记下的事务，其事件会对运行已记有名称的商品显示缺失提示并输出错误日志；②请求级 `errands` 的每条变更与 `control_errands` 同一规则读取（须是对象、八个键齐全，只查键在不在、不查值的类型），条目级的出入与缺 `errands` 键同等对待，报错、整库不加载，种类与名称照请求所写显示；③「缺失与重新加载」补个人事务的缺失提示“名称缺失（个人事务）”（空白或与事务内部身份相同的名称无效，括号里写对象种类而不写编号，英文错误日志写事件序号），唤醒原因 `errand_reminder_due` 找不到同一人物同一时刻、排在该决策之前的事务提醒事件时在原因说法之后显示同一提示并输出英文错误日志。**实现与证据**（`tools/run-viewer.html`；`tests/tools/run_viewer.test.cjs`，经 `tests/tools/test_run_viewer.py` 执行）：控制请求摘要在活动名、summary 与收尾的记忆条数之后写各条事务变更的种类与名称（「control request summaries write each errand change kind and name after the activity summaries」）；`controlErrands` 的八个键与顺序对照 `src/worldmock/life/control_items.py` 的 `_ERRAND_KEYS`（「errand changes are read by the same rule as control_errands: eight keys present, values as written」）；缺 `errands` 键、`errands` 不是列表与条目级的出入都经建索引这一步（页面加载数据库的入口）报错，三个控制工具逐个断言（「a control request that is not in the current shape fails the timeline index instead of being summarised」）；六种个人事务事件逐个显示中文说法、快照里的事务名称与商品名称（「errand events show their Chinese wording, the errand name and the recorded product, never the errand identity」）；只记在 `data.errand_item_bindings` 的商品名称补进索引、不覆盖其他来源、空白与等同编号的名称无效、更换数据库后清空（「errand products are named from the errand item bindings when the run lists them nowhere else」「errand item bindings only fill names the run records nowhere else and never an empty or identity name」）；商品与事务的缺失提示、事务内部身份只用于判定名称是否有效而不留在页面索引里（「an errand product without a recorded name is reported as missing and an errand is never named by its identity」）；唤醒原因写所提醒事务的名称，没有对应提醒事件或快照缺名时显示缺失提示（「the errand reminder wake reason names the errands reminded for that person at that moment」「decision starts show their wake reasons from the frozen vocabulary」）。`tests/tools/` 两个 Node 文件共 75 个用例通过。`2026-10-04-errands-workday-implemented` 行登记的两句出入至此消除；文件头据此改写 |
| `2026-10-04-errands-viewer-recorded` | 2026-10-04 | 登记查看器个人事务条款的实现提交，条款正文不变：`2026-10-04-errands-viewer` 行里的「本行所在提交」改写为提交号 `6c81126`；文件头写明各条款由哪个提交实现（缺键报错与中文说法随个人事务与工作安排规格阶段 2 的代码，请求摘要与事务事件、事务提醒的名称为 `1346e0d`，补充的三处与修正为 `6c81126`）；`2026-10-04-errands-workday-implemented` 行补一句指向 `2026-10-04-errands-viewer` 行。出入的发现与补齐经过见[个人事务与工作安排验收记录](../archive/errands-workday/errands-workday-verification.md)：三处出入由 `a7e2fdc` 最终全量运行之后的实现后文档核对查出，其后以查看器测试补齐；登记本行时在 `6c81126` 的查看器代码与测试上重新执行，`node --test tests/tools/run_viewer.test.cjs tests/tools/run_viewer_context.test.cjs` 为 75 个用例全部通过（两个文件各 48 个与 27 个），`tests/tools/test_run_viewer.py` 为 1 passed |
| `2026-10-05-idle-fillers-c` | 2026-10-05 | 按[让「等待」成为例外规格](SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)阶段 1 修订「运行内对象名称」：班次与休息时段起止的四个唤醒原因 `shift_start`、`shift_end`、`break_start`、`break_end` 用中文说法「班次开始」「班次结束」「休息时段开始」「休息时段结束」显示，它们没有伴随事件，不带名称，不适用缺失提示；实现为 `tools/run-viewer.html` 的 `WAKE_LABELS` 增四个标签，`tests/tools/run_viewer.test.cjs` 的「decision starts show their wake reasons from the frozen vocabulary」把存储词表的 25 个唤醒原因逐个显示为中文、不输出错误日志（经 `tests/tools/test_run_viewer.py` 执行）；状态行末补修订说明，日期改写 |
