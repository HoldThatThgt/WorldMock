<a name="spec-run-viewer-context-运行查看器上下文解析"></a>
# 运行查看器上下文解析

> 状态：FROZEN；按[人物 Prompt 规格](SPEC-life-realism.md#persona-prompt-七层请求)修订，修订部分已实现并通过离线验收（证据见[人物 Prompt 验收记录](../archive/persona-prompt/persona-prompt-verification.md)）；「增长与退出」按[个人事务与工作安排规格](../VERIFICATION.md#verification-incremental-evidence)修订（[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md)作业 RV5、RV6），修订部分已实现并通过离线验收：查看器内置的待办层、环境层、进度层规则文字与工具定义条目都已同步（待办层、环境层的说明随该规格阶段 2、3 的代码写入，进度层的两种事务记录、工具定义条目与待办层、环境层的来源说明由 `1346e0d` 补齐），`tests/tools/run_viewer_context.test.cjs` 逐句断言这些规则文字，夹具的七层行动请求已带 `errands`、`duty`、两种事务记录与现行工具面（夹具与补测为 `6c81126`；经 `tests/tools/test_run_viewer.py` 执行；逐条证据见「版本表」`2026-10-04-errands-viewer` 行）；修订记录见「版本表」。
> 日期：2026-10-04
> 验收记录：[运行查看器上下文解析验证](run-viewer-context-verification.md)（修订前的行为，另有「人物 Prompt 七层实现后复验」一节）。

<a name="spec-run-viewer-context-范围与入口"></a>
## 范围与入口

在 `tools/run-viewer.html` 增加“上下文”页面，读取用户已加载的 `footprints.sqlite`。
沿用原生 JavaScript、sql.js、全局人物筛选和事件全文按需读取，不增加服务、依赖或数据导入格式。
页面与相邻的解析、渲染和样式文件一起分发，使用相对路径，可直接从本地打开。
本功能只解析证据，不修改模拟、提示、历史记录或已登记的程序错误。

交互采用成熟运行追踪工具的请求详情与原文分层方式，参考
[Langfuse 的运行追踪结构](https://langfuse.com/docs/observability/best-practices)。
用户先选实际调用，再查看输入结构、完整原文和相邻调用变化。

<a name="spec-run-viewer-context-请求选择与关联"></a>
## 请求选择与关联

索引列出全部模型请求，按事件顺序区分人物、时刻、用途和行动／其他调用；支持阶段筛选。
system 为 JSON、`layer` 为「系统层」且 `role` 为生活决策的角色句、并带工具数组时判为行动请求（只读问答的系统层角色句不同，显示为其他调用）；
历史运行仍按原模板识别行动请求（阶段标「选择动作·旧模板」），其决策自述请求显示为「其他调用·未解析」。
请求全文只在选择后读取。时间线中的模型请求可直接进入对应上下文。
响应只通过 `caused_by` 中的请求事件身份关联，不按时间接近、数组相邻或复用的 call_id 猜测。
没有响应时明确显示尚无响应；失败响应显示已记录的错误。
更换人物后不得保留不属于该人物的请求；更换数据库后清空请求索引和选中状态。

<a name="spec-run-viewer-context-结构与原文"></a>
## 结构与原文

按实际顺序展示 system、user、assistant、tool 等消息和独立 tools 字段。
行动请求的 system 为系统层 JSON；首个 user 按顶层数组无损切片为工具层、人设层、记忆层、待办层、环境层、进度层，
每片可解析且层序正确才分段，否则明确显示未解析状态及完整消息，不强行分段。
工具层与进度层按真实字段下钻；进度层记录中的 `self_note` 标「未经核验」。
同一决策内追加的查询、工具结果和纠错消息保持原始顺序。
一次决策的首个请求多于两条消息时判为同秒续接：前序 assistant 按 `tool_call` 身份匹配来源决策，回传的思考单列；
时间推进后请求不再带前序原生消息，上一秒的调用与结果已并入进度层记录，按进度层字段展示。

历史运行的旧模板行动请求仍按原模板识别身份规则、人物卡片、场景、主观归纳、最近人物自述、事实清单和刚形成的自述（带已核验经历段的只是旧自述请求，归入其他调用）。
历史请求未保存各段填充来源，正文也可能包含与模板相同的标题；仅靠拼接文本不能证明来源。
符合旧模板的首条人物 user 消息可按模板推断分段，界面必须明确显示“来源未核验”，
不能把标题匹配当作已证实的记忆或场景来源。无法识别、重复边界或顺序冲突时，
明确显示未解析状态及完整消息，不强行分段。所有原文必须完整保留，分段拼接与输入逐字一致。
非字符串消息内容和 assistant 工具调用也必须可查看，工具定义完整显示。
旧模板的事实清单作为完整一段保留，不再逐字段下钻（下钻只针对七层请求的工具层与进度层）。
未提供 tools 时不把占位的空数组计为请求内容；显式提供的空数组则保留原始含义。
数量条使用 Unicode 字符计数，说明分隔符计入随后内容块、对象按 JSON 计量，不把字符当成 token。
响应的 token 用量只显示归档记录值；所有记录内容按文本转义，不执行其中的 HTML。

<a name="spec-run-viewer-context-增长与退出"></a>
## 增长与退出

展示每层内容的来源和当前实现的增长、替换、退出规则，并注明规则来自当前源码，
记忆层 8000 字符窗口等数值不代表所选运行保存了这些配置值。
系统层与原生 tools 整次运行不变、人设层的卡片复用与看法偏好原地更新、记忆层随 session 结束与零点分区变化、
待办层与环境层每个时间点重建（待办层的未完成个人事务取自人物状态，跨零点保留，办成或取消后退出；
环境层的 `duty` 由场景工作安排与世界时钟求值，今日计划改不了它）、
进度层在本 session 内累积（含事务提醒与办成两种只写名称的记录，二者不迁入记忆层）并在 session 结束后迁入记忆层、
同秒续接追加原生消息而时间推进后断链、容量达到阈值时先提前结束续接再浓缩进度层的规则均可查看。
将当前请求与同人物、同阶段的前一请求对照，分别显示新增、内容变化、未变和退出的层或块。
内容相等才算未变；相同长度不能当作相同内容。没有可比前一请求时明确说明。
对照展示前后完整文本；退出当前提示不代表底层事实或记忆被删除。

<a name="spec-run-viewer-context-验证与边界"></a>
## 验证与边界

用页面真实 JavaScript 和 SQLite 测试请求选择、严格响应关联、人物筛选、更换数据库、
七层切片无损与层序校验、层 JSON 不可解析或层序错误时显示未解析、续接识别与前序 assistant 的来源决策匹配、
回传思考单列、`self_note` 标「未经核验」、追加消息、工具、非字符串内容、歧义输入、同长不同内容及 HTML 转义。
旧模板请求覆盖消息正文恰好含有主观归纳标题的情况，保证这种分段明确标为推断而非已核验来源；
旧运行的决策自述请求显示为「其他调用·未解析」。
使用真实日运行的请求验证七层解析、续接、原文、工具和变化，并检查桌面与窄屏交互。
在包含候选实现与测试的干净提交上执行 Uncle Bob 语义变异审查，临时变异仅限查看器生产源码。

<a name="spec-run-viewer-context-版本表"></a>
## 版本表

| 版本 | 日期 | 修订内容 |
| --- | --- | --- |
| `2026-09-27-persona-prompt` | 2026-09-27 | 新增文件头与版本表。按[人物 Prompt 规格](SPEC-life-realism.md#persona-prompt-七层请求)「冻结条款修订」第 28 行修订（人物 Prompt 台账 RV1），修订部分待实现：删决策自述阶段，system 为 JSON 且 `layer` 为「系统层」时判为行动请求；首个 user 按顶层数组无损切片为六层，每片可解析且层序正确才分段，否则显示未解析；工具层与进度层按真实字段下钻，`self_note` 标「未经核验」；首个请求多于两条消息时判为续接，前序 assistant 按 `tool_call` 身份匹配来源决策，回传思考单列，时间推进后的调用按进度层记录展示；旧模板解析保留给历史运行，旧运行的自述请求显示为「其他调用·未解析」；增长与退出规则改按七层；验证与边界相应改写 |
| `2026-09-28-persona-prompt-implemented` | 2026-09-28 | 人物 Prompt 七层规格实现后文档（人物 Prompt 台账阶段 8，按最终代码 `f798ea7` 核对）：状态头改为修订部分已实现并通过离线验收，验收记录注明实现后复验一节；按查看器代码核对：行动请求另要求系统层角色句为生活决策角色句且带工具数组（只读问答显示为其他调用），旧模板行动请求的阶段标「选择动作·旧模板」、不含已核验经历段，旧模板事实清单整段保留、不再下钻 |
| `2026-10-02-errands-workday` | 2026-10-02 | 按[个人事务与工作安排规格](../VERIFICATION.md#verification-incremental-evidence)修订（[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md)作业 RV5–RV7），修订部分在冻结时未随文实现（该规格阶段 2、3 实现后的核对见 `2026-10-04-errands-workday-implemented` 行）：「增长与退出」写明待办层的未完成个人事务取自人物状态并跨零点保留、进度层增事务提醒与办成两种只写名称的记录（不迁入记忆层）、环境层的 `duty` 由场景工作安排与时钟求值；行动请求识别、六层切片与层序、旧模板解析不变；文件头日期改写 |
| `2026-10-04-errands-workday-implemented` | 2026-10-04 | 个人事务与工作安排规格实现后文档（[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md)作业 RV8，按 `d8a41d3` 的查看器代码 `tools/run-viewer-context.js` 核对规则文字）：待办层的说明已写还没办完的个人事务（按记下的时刻排列，写明要办成什么、已办到哪、备注与尚未提醒的钟点；人物在播报、暂停或收尾时记下，办没办成由环境按实际结算确认，办成或取消即移出），没有单写跨零点保留；环境层的说明已含今天的上班安排（没有工作安排为 null），没有写它由场景工作安排与世界时钟求值、今日计划改不了它；进度层的说明仍是「调用与结果、消息全文、配送、截止与提醒」，没有写事务提醒与办成两种只写名称的记录及其不迁入记忆层（实施计划安排随阶段 2 代码同步的一处，没有做）；工具定义条目仍写「八个世界请求工具与三个记忆查询工具」，没有随本特性改动（实际工具面是七个世界请求工具、`query_world` 与 `recall_task_summary` 两个只读查询工具、三个记忆查询工具）；睡醒反思条目已改为经唯一严格工具 `submit_reflection` 提交一至五条，并补校验重试一步。`tests/tools/` 的 62 个 Node 用例在该提交通过（经 `tests/tools/test_run_viewer.py` 执行），其中「all parsed block rules exist and every growth view retains explicit source and steps」只核对各层规则条目齐全，没有用例逐字断言本次新增的规则文字。文件头改为修订部分已实现并通过离线验收并注明上述出入；条款正文不变；`2026-10-02-errands-workday` 行里的冻结期状态注记改为指向本行。本行登记的出入其后由 `1346e0d` 与 `6c81126` 补齐，见 `2026-10-04-errands-viewer` 行 |
| `2026-10-04-errands-viewer` | 2026-10-04 | 查看器规则文字补齐（`1346e0d`）与对该提交独立核对后的补测（`6c81126`），条款正文不变。`tools/run-viewer-context.js`：进度层规则写事务提醒与办成两种只写事务名称的记录及其不迁入记忆层，进度层下钻的标签写记录所带的事务名称；待办层规则写未完成的个人事务取自人物状态、跨零点保留；环境层规则写 `duty` 由场景工作安排与世界时钟求值、今日计划改不了它；工具定义条目改为七个世界请求工具、两个世界查询工具（`query_world`、`recall_task_summary`）与三个记忆查询工具，最后回合与截断重试去掉全部查询工具。证据（`tests/tools/run_viewer_context.test.cjs`，经 `tests/tools/test_run_viewer.py` 执行）：「the progress rule holds the two name-only errand records and keeps them out of the memory layer」「the todo and environment rules state where open errands and duty come from as the growth clause does」「the tool definition rule counts the tool surface the host offers and when query tools are withheld」「progress drill-down labels the two errand records with the errand name they carry」；`tests/tools/run_viewer.test.cjs` 的「context navigation, stage filter and all rule paths are available through the actual view」在真实页面的「增长与退出」视图里逐条查看这四处规则文字。夹具 `tests/fixtures/viewer-context.json` 的七层行动请求与续接请求改为本特性的层形状：待办层 `errands` 两行（已提醒与尚未提醒各一）、环境层 `duty`、进度层的事务办成与事务提醒记录各一条，原生工具为宿主现行的十二个工具定义（含 `query_world`，控制工具带请求级 `errands`）；「the fixture action request is in the layer shape of the errands and workday feature and parses unchanged」对照各层构造源码与宿主工具表核对键序与工具面，并断言行动请求识别、六层切片与层序不变，没有这两个键的既有七层请求照旧切片、对照时只有待办层与环境层算内容变化。夹具其余部分仍是人物 Prompt 七层实现时的样本（工具层的可调用索引与任务合同、今日计划的意图种类、体感用词、进度层的 `rejected_today` 与现行层 Schema 有出入）；切片不校验层内的键，这些出入不影响本规格的条款，本次没有改动。`tests/tools/` 两个 Node 文件共 75 个用例通过。`2026-10-04-errands-workday-implemented` 行登记的出入（进度层规则文字、工具定义条目、没有用例逐字断言新增的规则文字）至此消除；文件头据此改写 |
| `2026-10-04-errands-viewer-recorded` | 2026-10-04 | 登记查看器规则文字的实现提交，条款正文不变：`2026-10-04-errands-viewer` 行里的「本行所在提交」改写为提交号 `6c81126`；文件头写明规则文字由哪个提交写入（待办层、环境层的说明随个人事务与工作安排规格阶段 2、3 的代码，进度层的两种事务记录、工具定义条目与两层的来源说明为 `1346e0d`，夹具与补测为 `6c81126`）；`2026-10-04-errands-workday-implemented` 行补一句指向 `2026-10-04-errands-viewer` 行。出入的发现与补齐经过见[个人事务与工作安排验收记录](../archive/errands-workday/errands-workday-verification.md)：出入由 `a7e2fdc` 最终全量运行之后的实现后文档核对查出，其后以查看器测试补齐；登记本行时在 `6c81126` 的查看器代码与测试上重新执行，`node --test tests/tools/run_viewer.test.cjs tests/tools/run_viewer_context.test.cjs` 为 75 个用例全部通过（两个文件各 48 个与 27 个），`tests/tools/test_run_viewer.py` 为 1 passed |
