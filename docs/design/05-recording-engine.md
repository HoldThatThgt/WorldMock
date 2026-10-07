<a name="recording-engine-worldmock-产品设计说明书-记录引擎"></a>
# WorldMock 产品设计说明书 — 记录引擎

> **状态**：已整合；原已冻结的接口与行为约束继续有效，待实现设计单独列出；按人物 Prompt 规格修订，修订部分已实现（证据见[人物 Prompt 验收记录](../archive/persona-prompt/persona-prompt-verification.md)）；按一日运行保真规格同步（一日运行保真 RE1–RE6），已实现；按[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)分阶段同步（[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md) D5 作业），已实现（离线验收，证据见[个人事务与工作安排验收记录](../archive/errands-workday/errands-workday-verification.md)）<br>
> **日期**：2026-10-08
> **版本**：v1.6
> **范围**：记录生活全过程、保存与关联证据、一致性保存与校验、保存共同检查点、恢复与反事实分支、固定截止导出、运行查询与诊断
> **动作与内容简化修订**：现行设计按[动作与内容简化规格](../dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)使用统一动作、无分类内容、真实收货与 app 足迹合同；实施与验收状态见[实现状态](../dev/implementation-status.md#implementation-status-动作与内容简化开发状态)。


> 2026-10-07：按[资源与规则引擎简化规格](../dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)同步阶段 7 的记录、导出、交付与评估合同。

> 2026-10-07：按[资源与规则引擎简化规格](../dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)同步阶段 5 的分配内容证据与质量报告。

> 按[资源与规则引擎简化规格](../dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订「归档引用」（阶段 1）；「地图与门店供给证据」、「地图与供给的恢复身份」、「检查点创建」、「持久化结构」、「必须保存的内容」（阶段 2）；「事件字段、事件类型、事件正文、活动进度、已核验经历、共同身体结算与决策证据」（阶段 4，app 使用与推送）。

> 2026-10-01 按[工具交互规格](../dev/SPEC-life-runtime.md#tool-interaction-工具请求)修订当前工具请求、查询与错误合同；本次设计已冻结，生产代码已实现，旧验收证据不覆盖新增条款。

> 本特性首次受影响的发布升级为场景 4、地图 3、目录 5.0、品类与规则绑定 3，世界结构仍为 8。各阶段独立发布；后续格式或内容再次变化时另升相应版本，禁止同目录版本覆盖内容。

> 个人事务与工作安排规格首次受影响的发布：阶段 2 世界结构 9、质量报告结构 4，阶段 3 场景结构 5、卡片结构 3（抽取提示 `card-v3`）；地图 3、目录 5.3、品类与规则绑定 3、导出清单 2、运行清单 2 不变。上一条所说「世界结构仍为 8」只描述工具交互特性发布时的身份。

> 按[让「等待」成为例外规格](../dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)修订「共同身体结算与决策证据」「事件正文与资源证据」「调试工具」（阶段 1）；该阶段只改变代码身份，现行发布身份见正文。


<a name="design-05-recording-engine-共享规则与可重建环境修订"></a>
## 共享规则与可重建环境修订

按[共享规则与可重建环境规格](../dev/SPEC-shared-rules-and-rebuildable-env.md)修订；验收结果独立记录。

规范 JSON 摘要只有一个实现并拒绝非有限数值。原子发布写字节、同步文件后替换；POSIX 同步父目录，Windows 按平台能力发布。有效结算区间使用非布尔整数秒且起点小于终点；时间线和逐日导出均读取运行清单时区。主观记忆快照共用校验，带反思来源的合法快照不算畸形经历。代码身份继续哈希实际字节，依赖源码由固定补丁重建。

<a name="recording-engine-记录引擎负责什么"></a>
## 记录引擎负责什么

记录引擎让一段数字人的生活能够被查证：他实际看到了什么、作出了什么选择、环境如何执行、
状态为何变化、结果是什么，以及这些经历怎样进入记忆。
它接收模拟引擎提交的事实与快照，负责保存、关联和完整性校验；
从共同检查点构建新的分支存储，提供已核验的恢复材料，原分支记录始终保留。
数字人负责行动选择与记忆，交换引擎负责规则计算，模拟引擎负责运行编排与实际执行。

本文统一维护记录引擎的设计、接口和行为约束。
[接口合同](../CONTRACTS.md#contracts-worldmock-接口合同)保留跨模块接口导航；
[交换引擎](02-exchange-engine.md#exchange-engine-worldmock-产品设计说明书-交换引擎)提供交换条件与投入产出规则，[模拟引擎](04-simulation-engine.md#simulation-engine-worldmock-产品设计说明书-模拟引擎)推进活动并落实结算；
LabelKit-DataSet 定义认证导出接收后的数据加工；
[验收门禁](../VERIFICATION.md#verification-worldmock-验收标准)和[实现状态](../dev/implementation-status.md#implementation-status-worldmock-实现状态)分别记录放行条件与实测证据。
文档整合不构成实现完成或真实运行验收。

<a name="recording-engine-项目框架映射"></a>
### 项目框架映射

沿用[概述的解决方案](00-overview.md#overview-解决方案)：资源引擎提供资源与内容，交换引擎提供规则，
模拟引擎组织数字人的行动与世界变化。记录引擎在交互边界保存全过程，形成可追溯、可恢复、可交付的数字足迹。

```mermaid
flowchart TB
    RESOURCE["资源引擎<br/>资源、对象、内容及其定义"] --> SIMULATION
    EXCHANGE["交换引擎<br/>投入、产出、条件与转换规则"] --> SIMULATION
    PERSON["数字人<br/>按自身需求选择行动"] --> SIMULATION
    SIMULATION["模拟引擎<br/>推进时间、处理动作、调用规则并更新世界状态"]

    subgraph RECORDING["记录引擎"]
        direction TB
        PROCESS["记录生活全过程<br/>观察、决策、执行、状态变化、结果与记忆"]
        EVIDENCE["保存与关联证据<br/>实际正文、模型请求与响应、工具参数与返回<br/>关联人物、动作、来源与可见性"]
        STORE["一致性保存与校验<br/>素材完整性、状态与事件共同提交<br/>事件不可覆盖、版本连续、资金可对账"]
        CHECKPOINT["保存共同检查点<br/>完整世界、数字人工作区、内容进度与随机状态"]
        BRANCH["恢复与反事实分支<br/>构建分支存储并提供恢复材料<br/>保留原分支、父检查点与干预来源"]
        EXPORT["固定截止导出<br/>冻结事件、素材与对应检查点<br/>核验完整性并生成质量报告"]
        DIAGNOSIS["运行查询与诊断<br/>查询已保存事实与客观统计<br/>定位记录缺失、不一致与因果关系"]

        PROCESS --> EVIDENCE --> STORE
        STORE --> CHECKPOINT --> BRANCH
        STORE --> EXPORT
        CHECKPOINT --> EXPORT
        STORE --> DIAGNOSIS
    end

    SIMULATION -->|交互边界的全过程| PROCESS
    SIMULATION -->|收齐本轮状态与参与者快照| CHECKPOINT
    BRANCH -->|已核验的分支存储与恢复材料| SIMULATION
    EXPORT -->|通过认证的数字足迹| LABELKIT["LabelKit-DataSet<br/>证据整理与数据加工"]
    LABELKIT --> TRAINING["模型训练数据"]
    LABELKIT --> TESTING["规则引擎测试数据"]

    style RECORDING fill:#eff6ff,stroke:#2563eb,stroke-width:2px
    classDef engine fill:#f2f2f2,stroke:#888,color:#222
    class PROCESS,EVIDENCE,STORE,CHECKPOINT,BRANCH,EXPORT,DIAGNOSIS engine
```

<a name="recording-engine-职责与模块边界"></a>
### 职责与模块边界

责任以谁产生决定、谁保存结果划分，不因代码位于同一目录就归入同一引擎。

| 协作边界 | 上游或下游负责 | 记录引擎负责 |
| --- | --- | --- |
| 行动与结算 | 模拟引擎组织决策、唤醒与活动推进，计算身体变化，并调用交换引擎校验条件、计算投入产出 | 保存实际提交的事件与状态，核对版本、资金差额、引用和历史是否一致 |
| 内容与可见性 | 资源引擎提供内容及来源；模拟引擎决定本次观察实际交付什么 | 保存实际交付字节、来源与可见性标记，保持事件关联 |
| 记忆与共同检查点 | 数字人保存记忆和工作区；模拟引擎完成世界推进，收齐参与者快照并确定共同边界 | 校验快照齐备、字节完整、状态与截止一致，原子提交检查点 |
| 恢复与反事实执行 | 模拟引擎恢复人物与外部进程、施加干预并启动新分支 | 核验检查点，构建分支存储，保存父检查点、干预证据与来源关系 |
| 运行生命周期与故障处置 | 模拟引擎维护运行状态，决定终止、重试与继续执行 | 返回持久化结果或明确失败，提供已保存记录的只读查询和完整性诊断 |
| 数据加工与行为评估 | 数据交付组装样本、裁剪窗口和划分数据；行为评估判断真实性与实验效果 | 提供固定截止的认证足迹和可重算的事实统计 |

本文的七个功能主题均限于右栏；协作方的规则只在交接处引用所属文档，不作为记录引擎的功能或实现差距。

记录引擎使用追加事件、状态快照和内容寻址素材，复用 SQLite 与现有存储模块。
数据库事务的依据是 [SQLite 原子提交](https://www.sqlite.org/atomiccommit.html)；
不可变事件与快照的组织方式参考 [Microsoft Event Sourcing](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing)。
这些工程机制不意味着重新调用模型会重现相同决策。

<a name="recording-engine-主题阅读路径"></a>
### 主题阅读路径

后续正文按项目框架映射中记录引擎内部的功能框展开，章节名称与图中功能名称一致。
接口、数据结构、处理规则、失败边界、实现差距和验收要求分别放入所属主题。

- [记录生活全过程](05-recording-engine.md#recording-engine-记录生活全过程)
- [保存与关联证据](05-recording-engine.md#recording-engine-保存与关联证据)
- [一致性保存与校验](05-recording-engine.md#recording-engine-一致性保存与校验)
- [保存共同检查点](05-recording-engine.md#recording-engine-保存共同检查点)
- [恢复与反事实分支](05-recording-engine.md#recording-engine-恢复与反事实分支)
- [固定截止导出](05-recording-engine.md#recording-engine-固定截止导出)
- [运行查询与诊断](05-recording-engine.md#recording-engine-运行查询与诊断)

实现差距沿用已有源码核查结论，按主题分列；本次调整不修改生产代码；跨引擎约束在其所属文档维护。
各主题保留已冻结规范的验收要求与现有测试入口；测试存在不等于本次重新执行通过。
本次文档整理的验证结果记录在交付说明中，正式运行与跨系统验收仍见[实现状态](../dev/implementation-status.md#implementation-status-worldmock-实现状态)。

<a name="recording-engine-记录生活全过程"></a>
## 记录生活全过程

围绕数字人实际经历的顺序，明确每个环节必须留下什么，以及怎样形成结构化事件。

<a name="recording-engine-保存边界"></a>
### 保存边界

<a name="recording-engine-必须保存的内容"></a>
#### 必须保存的内容

证据由运行环境在模型与环境的接口边界写入，不能依靠数字人自己维护日志。

| 生活过程 | 必须可追溯的证据 |
| --- | --- |
| 观察交付 | 数字人实际收到的正文、可见字段、内容片段范围、观察版本，以及宿主实际使用的有效交互菜单 |
| 人物决策 | 决策身份、同一秒内的轮序、实际上下文、模型请求（同秒续接时含本秒前序决策的 assistant 与 tool 消息）与响应（含可选正文与 `reasoning_content`）和所选的世界请求 |
| 后台任务 | 零点计划 `plan_day` 的请求与响应（`purpose="plan_day"`）、生成的计划与提醒及其来源；须能与人物决策区分 |
| 记忆阶段调用 | 就寝反思的模型请求与响应：请求不带 `purpose`、思考由 AS2 记忆调度关闭，`tools` 恰为一个 `strict` 的 `submit_reflection`，`tool_choice="required"`；响应恰好一次该工具调用，`message.content` 不读取。本地校验不通过时在原始消息之后附回一条 user 消息（上一次不合法的工具调用、英文校验错误与重新提交的指令；总是基于原始消息，不逐次累积）重新请求，首次之外至多三次（连同首次至多四次模型调用）；每次尝试是 AS2 记忆调度下的一次独立模型调用，请求与响应照常留证、各自计入模型用量，校验错误随下一次请求的附回消息留证，耗尽时最后一次错误写进运行故障原因；没有可反思的主观记忆时不调用模型。`submit_reflection` 不属于人物工具面，不写 `action_requested`，须能与行动请求区分 |
| 动作执行 | 工具名、参数、返回、接受或拒绝、实际进度、中断与最终结果 |
| 付款与收货 | 一次付款、待收货订单、收货时刻与一次结算的 provided、applied、efficiency、本次资源分录及真实 caused_by |
| 图上出行 | `trip_id`、起点、终点、`path`、`distance_meters`、图上进度、车费付款与到达；不生成坐标或轨迹 |
| 恢复输入 | 世界结构 11 的完整快照，以及冻结清单五字段 `environment_identity` 和对应清单摘要 |
| 状态变化 | 变化前后的状态版本、发生原因、资金变化、身体结算与环境自主事件 |
| 记忆更新 | 实际写入的记忆、记忆版本、来源事件与工作区快照；提示中的记忆层条目是构建提示时从已提交事件与世界状态派生的视图，不新增持久状态；构建结果随观察事件保存（`memory_layer`），并写进人物工作区的只读问答快照 `life_prompt_snapshot.json` |
| 个人事务 | 人物在 `report_status`、`pause_activity`、`conclude_activity` 的请求级 `errands` 里新增、更新、取消的事务，环境按结算证据累计与确认办成的事实，以及实际交付的提醒：各写一条 `environment_event`（六个 `data.kind` 见「事件正文与资源证据」）；未完成事务随世界状态与共同检查点保存，终态、原因与证据只在事件里。事务是人物记下的打算，不是世界事实，不改变余额、身体、订单与验收，也不是记忆写入 |
| 故障与结束 | 异常类型、英文错误、关联事件、运行终态与最终共同检查点 |

凭据不写入证据。模型完整请求保留必要的任务上下文，脱敏规则带版本与处理记录。
记录数字人实际输出的内容，不把额外生成的解释冒充当时的决定或事实。

现行身份是目录格式 2、场景结构 8、世界结构 11、运行清单 4。运行清单不含语料钉，五字段
`environment_identity` 不变；恢复输入与检查点核对世界结构 11。

<a name="recording-engine-数字足迹事件结构"></a>
### 数字足迹事件结构

<a name="recording-engine-事件字段"></a>
#### 事件字段

事件保存 event_id、event_seq、run_id、event_type、occurred_at、agent_id、activity_id、source_kind、visibility、状态版本与 caused_by。观察只保存当前七层；开始和恢复结果公开 `details.content{action, app, title, summary, duration, pushed}`，app 可空。

action 活动每次结算带 `content{content_id, app_id, position_before_seconds, position_after_seconds, completed, completed_rounds_after}`，app_id 可空。区间、活动快照、资源变化和出行 arrival 同一次提交。实际快照另带动作名 title 与当前内容标题 content_title；完成证据归真实消费方。


<a name="recording-engine-共同身体结算与决策证据"></a>
#### 共同身体结算与决策证据

同一人物同一真实区间只产生一次 `environment_event.data.kind=body_settlement`，事件顶层
`activity_id` 为空。正文保存 `interval_start`、`interval_end`、按身份排序的 `activity_ids`、
`body_settlement`、`resource_flows`、`decision_offered` 和 `wake_reasons`。同一边界的各
`activity_progress.data.body_settlement_event_id` 引用这条已提交事件；各自只保存本活动的
进度、效率、消费和内容接触，不复制共同身体变化或将联合代谢成本分摊给某条活动。
`activity_ids` 同时是该区间社交账的唯一口径来源：节律结算按它判定这段区间是否在睡、是否由交谈或通话记账，
不按推进后的活动状态重新推断，所以在边界结束的睡眠整段按睡眠计；事件结构不因此增加字段。

场景本地零点（不是运行终点）的边界分两次提交：推进先提交旧日的结算，即带旧计划快照 `data.day_plan` 的
`activity_progress` 与终态结果，随后由零点计划为人物登记新计划（`day_plan_generated`）；该边界的共同身体结算
连同其 `decision_offered` 与 `wake_reasons` 留给零点节律入口，在 `day_plan_generated` 之后与该秒的唤醒判定一起提交。
所以零点边界的各 `activity_progress` 仍以 `data.body_settlement_event_id` 指向这条结算，但不把它列入 `caused_by`
（直接原因只能是已提交事件）。零点节律入口提交之前，该秒不提供决策，也不形成共同检查点。

记录引擎校验引用身份、人物、区间与事件顺序；具体身体演化和唤醒条件仍由模拟引擎计算。
静默边界也写共同身体证据，不能因没有人物决策而漏记身体时间。
同刻轮次（同一模拟秒内的再决策轮）没有区间，不写共同身体结算；其唤醒原因只记于
`decision_started.data.wake_reasons` 与观察。同秒续接的各轮仍逐轮写观察与 `decision_started`。
边界兜底的 `no_active_activity` 属于边界判定，照常写入结算。
唤醒原因闭集、睡眠允许的原因与触发条件统一见
[生活运行时规格的决策节律](../dev/SPEC-life-runtime.md#spec-life-runtime-决策节律结算边界与唤醒条件分离)。
`plan_reminder_due` 之后另有 `errand_reminder_due`（[个人事务与工作安排规格](../dev/SPEC-life-runtime.md#errands-workday-个人事务-提醒时刻) 2.5）：
清醒人物有已到点未提醒的个人事务、本边界为他提供决策机会时记入，与其他成员一样写进共同身体结算的 `wake_reasons`、
`decision_started.data.wake_reasons` 并经同一闭集校验；同一边界到期的多条事务合并为一次决策机会、该原因只记一次，与
`plan_reminder_due` 同刻到期时按枚举顺序并列。它不在睡眠允许的原因之内：清醒人物的未提醒时刻进入下一边界与下一次决策机会的
候选、精确到点；睡眠中的人物不计入候选、不形成静默边界、不交付也不消耗，睡眠期间错过的提醒在人物醒来的那个边界
（与 `wake_from_sleep` 并列）一并交付，不另起边界。提醒事件追加到该边界的事件草稿，平常边界随推进提交，零点边界随零点节律入口提交。

`errand_reminder_due` 之后依次是班次与休息时段起止的四个成员 `shift_start`、`shift_end`、`break_start`、`break_end`
（[让「等待」成为例外规格](../dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)）：某个结算边界的那一秒恰是人物当日班次的开始或结束
（含前一日写到 24:00、落在当地零点的结束）或该班次某个休息时段的开始或结束，而人物此刻清醒、本边界为他提供决策机会时记入对应成员，
与其他成员一样写进共同身体结算的 `wake_reasons`、`decision_started.data.wake_reasons` 并经同一闭集（足迹侧词表 `src/worldmock/footprints/models.py::WAKE_REASON_NAMES`，新名字缺登即落不了库）校验；
同一秒重合的多个起止各记一次，按枚举顺序与同一秒的其他原因并列。四个成员都不写事件，也不另存状态：起止由场景工作安排与世界时钟
重新求值，检查点、恢复与反事实分支不为它保存任何字段，已经判定而尚未决策的原因照常留在人物状态的 `pending_wake_reasons` 里随检查点恢复，
不重复、不补发。它们不在睡眠允许的原因之内：睡眠中的人物不计入候选、不形成静默边界、不被叫醒，睡过的起止醒来后不补；
运行第一秒恰在起止上时只记 `run_start`。

`app_push` 排在 `break_end` 之后，共同身体结算与 `decision_started` 使用同一最终 23 项有序闭集；
它不进入四项睡眠允许集合。

收货变化不属于区间身体结算；goods_received 保存自身资源分录，不能流入下一身体事件或另一人物事件。

<a name="recording-engine-事件类型枚举"></a>
#### 事件类型枚举

事件类型遵循生产 Event 模型。动作登记、结果、活动推进、身体结算、观察、决策和环境事件分别保存；`work_content_completed`、`goods_received`、`delivery_arrival`、`alarm_ring` 和 `app_push` 是 environment_event 的真实 kind。


<a name="recording-engine-事件正文与资源证据"></a>
#### 事件正文与资源证据

action 活动每次结算带 `content{content_id, app_id, position_before_seconds, position_after_seconds, completed, completed_rounds_after}`，app_id 可空。区间、活动快照、资源变化和出行 arrival 同一次提交。实际快照另带动作名 title 与当前内容标题 content_title；完成证据归真实消费方。

购买受理当秒付款，每个请求只作一次完整运输报价，不产生提前的商品效果。到店和直送经唯一收货入口结算；完整运输时长严格大于一天时，订单按实际模拟收货人的家庭地址选择图上最近且场景选中的驿站，恰一天仍直送。到站只写 `arrived_at`、进入 `ready_for_pickup` 并通知实际收货人，没有 `receipt`、`goods_received` 或商品效果。本人在站执行零秒领取后，才写 `received_at`、`delivered`、商品效果与收货证据。系统没有库存、持有、食用或给予；完整结算合同见[供给结算与事件载荷](../CONTRACTS.md#contracts-供给结算与事件载荷)。

推送人物可见载荷恰含 kind、push_id、app_id、app、action_id、action、content_id、title、summary、session_id、issued_at。未找到合格 app 的结算只对系统可见，不写本人推送记录。


<a name="recording-engine-地图与门店供给证据"></a>
##### 地图与门店供给证据

订单 `DeliveryState` 保留 `goods_id`、`category`、购物与支付 app、付款人与实际收货人、原收货地址 `delivery_address_id`、实际目的地 `destination_id`、路径、一次报价得到的 `expected_at`，以及 `arrived_at`、`received_at`。履约方式为 `pickup`、`delivery`、`station_pickup`；状态为 `pending`、`ready_for_pickup`、`delivered`。到店的到站与收货时刻等于创建时刻；驿站到站时只有 `arrived_at`，领取后才有 `received_at`。订单数量恒为一，没有模拟人物或联系人的持有状态。

模拟收货人每次真实收货恰有一条人物可见的 `goods_received`，人物所有者是实际收货人，载荷含订单、付款人、商品、类别、数量、目的地、当时地点与睡眠事实、`provided`、`applied`、`efficiency` 和本次资源分录。`caused_by` 引用同次提交中真实的购买、直送或领取结果。直送 `delivery_arrival` 归付款人；驿站到站事件归实际模拟收货人，只携完整订单快照与可取通知。联系人直送只更新订单和 `received_at`，不写 `goods_received` 或身体效果。

<a name="recording-engine-activity_progress-事件"></a>
##### activity_progress 事件

action 活动每次结算带 `content{content_id, app_id, position_before_seconds, position_after_seconds, completed, completed_rounds_after}`，app_id 可空。区间、活动快照、资源变化和出行 arrival 同一次提交。实际快照另带动作名 title 与当前内容标题 content_title；完成证据归真实消费方。

每个人的 `content_progress` 以内容为键，跨 app 共享。记录恰含 `content_id`、`position_seconds`、`completed_rounds`、必填的 `last_consumed_at`；只有正秒消费才创建或更新，推送、展示和零秒暂停不算看过。完成时位置归零、轮数加一，活动 `played` 追加实际消费区间，并释放当前 session 指向该内容的全部推送。


<a name="recording-engine-已核验经历与主观记忆"></a>
#### 已核验经历与主观记忆

已核验经历只有 waited、arrived、played、received。played 来自任一 action 真实位置前进，可无 app；received 来自本人 goods_received，并含 order_id、goods_id、category、quantity，activity_id 为空。模型注记仅是主观文字，不能更改真实位置、轮数、收货、发送或通话。

记忆活动句写活动名、起止、暂停和实际终局，随后写完成内容标题与确有消费的未完内容；静默抽出的下一条不算经历。订单句写购买、商品、付款、真实购物与支付 app；完成句写真实内容标题。


<a name="recording-engine-工具纠错只读查询与当天操作备忘的证据"></a>
### 工具纠错、只读查询与当天操作备忘的证据

宿主保存实际模型回复、结构化 `protocol_feedback`、整批拒绝与最终受理结果；当天操作备忘只从这些已提交证据派生。`query_world` 的证据保存 `name`、`way`、`category`、`app`、`available_only`、`offset` 六个必填键。只读查询只进入真实模型与 tool 交换证据，不登记世界请求，不推进时钟，也不改世界或人物状态。

任一 `action` 活动只有在内容位置实际正向推进时才写 `played`，记录真实 `content_id`、可空 `app_id` 与前后位置；目录展示、零秒选择和模型文字都不能伪造内容消费。场景 `work_content_ids` 决定本人分配的工作内容，进度来自同一内容结算，首次做满写 `work_content_completed`；播报只作注记，不改变进度或完成状态。



<a name="recording-engine-记录生活全过程的实现差距"></a>
### 记录生活全过程的实现差距

时间与能量、七层人物提示、工具交互的既有记录差距已由各自验收覆盖。现行记录入口读取 `wait`、`sleep`、`travel`、`action` 四种活动、五字段开始项、六键世界查询、当前订单状态、`goods_received`、闹钟、分配内容与无分类内容进度；退役的活动、库存、食用和旧版工作载荷直接拒绝。动作与内容简化的新增证据必须由本次验收证明，不能沿用更早的绿色结果代替。

<a name="recording-engine-记录生活全过程的验收"></a>
### 记录生活全过程的验收

| 要求 | 必须通过的观察 | 证据位置 | 本次核验状态 |
| --- | --- | --- | --- |
| 同刻决策证据 | 同刻轮次逐轮写 `decision_started`，唤醒原因只含本轮原因，写入时经唤醒闭集校验；同刻轮数按同一模拟秒内的 `decision_started` 计 | [时间与能量规格「验收台账」](../VERIFICATION.md#verification-incremental-evidence)的「同刻决策」「唤醒词表」行 | 已实现，证据见[时间与能量验收记录](../archive/time-and-energy/time-and-energy-verification.md) |
| 零点计划留证 | 零点计划的模型请求与响应按 `purpose` 与人物决策区分；计划、提醒、来源与模型证据同一次提交；同一人物同一本地日只生成一次，恢复与分支不重复生成 | [时间与能量规格「验收台账」](../VERIFICATION.md#verification-incremental-evidence)的「零点计划与提醒」行 | 已实现，证据见[时间与能量验收记录](../archive/time-and-energy/time-and-energy-verification.md) |
| 记忆来源 | 人物决策中的记忆只经 `conclude_activity` 写入，此外只有就寝反思与阈值巩固；来源由系统登记，模型不填身份 | [时间与能量规格「验收台账」](../VERIFICATION.md#verification-incremental-evidence)的「会话、活动与记忆」行 | 已实现，证据见[时间与能量验收记录](../archive/time-and-energy/time-and-energy-verification.md) |
| 记忆层 `settled` | 发给模型的已结算事实只在记忆层 `settled` 与进度层：`settled` 是由共享句式从人物可见、成功且不是重放的结果与世界事件生成的确定性句子，不含内部身份；私有观察仍保存 `verified_experiences` 结构行，它与由它生成的句子都不进提示 | [人物 Prompt 规格「验收台账」](../VERIFICATION.md#verification-incremental-evidence)的「记忆层」行 | 已实现，证据见[人物 Prompt 验收记录](../archive/persona-prompt/persona-prompt-verification.md) |
| 续接证据 | 同秒续接的各轮仍逐轮写观察与 `decision_started`；续接请求的 `model_request` 原样保存本秒前序决策的原生 assistant（含 `reasoning_content`）与 tool 消息，续接缓冲由环境从这些模型证据重建、不另存；观察重复门禁对链内复用链首 user 合法、跨秒重复仍失败 | [人物 Prompt 规格「验收台账」](../VERIFICATION.md#verification-incremental-evidence)的「同秒续接」「下游」行 | 已实现，证据见[人物 Prompt 验收记录](../archive/persona-prompt/persona-prompt-verification.md) |
| 记忆层 | 记忆层按 session 由已提交事件与世界状态派生，构建结果随观察事件保存，导出的就是模型所见；四种 session 结束方式与同秒归属（按边界提交）、零点分区、未了结事项、反思归属日期与 8000 字符窗口正确；检查点恢复前后逐字节相同 | [人物 Prompt 规格「验收台账」](../VERIFICATION.md#verification-incremental-evidence)的「记忆层」「人设层」行 | 已实现，证据见[人物 Prompt 验收记录](../archive/persona-prompt/persona-prompt-verification.md) |
| 订单收货人 | 送达即结算给收货人，provided/applied 和资源分录归收货人；联系人只有订单 delivered 和 received_at，没有 goods_received | [收货结算](../dev/SPEC-action-and-content-simplification.md#spec-acs-收货结算) | 以当前测试与真实运行回执判定 |
| 个人事务留证 | 三个控制请求里每条被受理的事务变更恰好写一条事务事件；六种事务事件与所记录的事务变化、触发它的事件同一提交，都不带决策与动作身份；请求里无法自报完成；办成与进展事件的 `caused_by` 指向记下之后的结算证据，重放的动作结果与重复到期的配送不重复累计；提醒只在实际交付决策机会时写事件 | [个人事务与工作安排规格「验收台账」](../VERIFICATION.md#verification-incremental-evidence)的「事务变更与原子受理」「完成依据」「结算幂等」「事务提醒」行 | 已实现，证据见[个人事务与工作安排验收记录](../archive/errands-workday/errands-workday-verification.md) |
| 个人事务检查点 | 共同检查点恢复后待办层逐字节相同，不重复记下、完成与提醒；世界结构版本 8 的检查点与导出按版本拒绝 | [个人事务与工作安排规格「验收台账」](../VERIFICATION.md#verification-incremental-evidence)的「检查点」行 | 已实现，证据见[个人事务与工作安排验收记录](../archive/errands-workday/errands-workday-verification.md) |
| 严格反思留证 | 就寝反思恰一次 `submit_reflection`；输出不合合同时附回英文校验错误重新请求（首次之外至多三次），每次尝试的请求与响应各自留证；耗尽即运行故障，本轮中止、不提交，故障时无 `memory_updated` | [个人事务与工作安排规格「验收台账」](../VERIFICATION.md#verification-incremental-evidence)的「严格反思」行 | 已实现，证据见[个人事务与工作安排验收记录](../archive/errands-workday/errands-workday-verification.md) |

<a name="recording-engine-保存与关联证据"></a>
## 保存与关联证据

保存当时实际使用和产生的内容，让每条经历能回到原始证据，并明确它来自哪里、谁当时能够看到。

<a name="recording-engine-事件身份与因果关联"></a>
### 事件身份与因果关联

同一动作的重试沿用动作标识；真实再次观看、再次购买或再次接触内容产生新的动作标识和事件。
素材哈希只标识内容，不能作为事件身份，也不能用来删除重复行为。
`caused_by`、`decision_id`、`action_id` 与 `activity_id` 连接观察、决策、执行和结果；同秒事件按序号排序。
已经受理的请求序号不能证明活动完成或资金已支付。

<a name="recording-engine-来源与可见性"></a>
### 来源与可见性

原始记录保留世界事实、数字人自述与推断的来源区别，按提交内容保存来源和可见性标记。
模拟引擎负责观察投影与访问范围；记录引擎保留实际交付证据，不能把事后完整状态冒充当时的观察。
下游的字段裁剪与证据窗口由 LabelKit-DataSet 负责，原始事件保持不变。

<a name="recording-engine-素材存储行为"></a>
### 素材存储行为

```mermaid
flowchart LR
    INPUT[原始字节与来源] --> HASH[计算 SHA-256 与字节数]
    HASH --> TEMP[写入临时文件并持久化]
    TEMP --> VERIFY[核对字节数与摘要]
    VERIFY --> PUBLISH[原子发布 assets 下的素材]
    PUBLISH --> REF[返回不可变 AssetRef]
    REF --> COMMIT[数据库提交引用]
```

相同内容复用已有文件，读取仍须检查引用、大小和摘要。
不存在或损坏的素材立即失败，不能返回空内容或临时替代物。
文件系统不参加 SQLite 事务，因此只有完整发布的素材才允许被事件或检查点引用；
数据库提交失败时，不得发布看似成功的记录。

`AssetRef` 拒绝未知字段，创建后不可变；`sha256` 是 64 位小写十六进制字符串，
`size_bytes` 是非负整数，`media_type` 是字符串，`source` 是来源信息对象。
`EventDraft`、`Event` 与 `Checkpoint` 的模型同样拒绝未声明字段；
具体事件正文的领域约束由对应领域模型验证。

<a name="recording-engine-素材存储接口"></a>
### 素材存储接口

| 调用 | 签名 | 合同 |
| --- | --- | --- |
| `AssetStore.put` | `(content: bytes, metadata: dict) -> AssetRef` | 内容先原子落盘并核验，返回不可变引用；相同内容复用文件，接触事件仍分别生成 |
| `AssetStore.read` | `(reference: AssetRef) -> bytes` | 验证引用、大小与 SHA-256；缺失或损坏立即失败，不返回空内容 |
| `AssetStore.put_json` | `(value: object, source: dict) -> AssetRef` | 把结构化值落成素材，复用同一哈希与引用规则 |


<a name="recording-engine-保存与关联证据的验收"></a>
### 保存与关联证据的验收

| 要求 | 必须通过的观察 | 证据位置 | 本次核验状态 |
| --- | --- | --- | --- |
| 素材原子写入与不可变引用 | 正文以原子方式落盘并通过核验后才发布为稳定引用；同一份素材被复用，每次接触仍各自生成事件；引用缺失、字节数或摘要对不上时当即失败，绝不返回空内容 | `tests/footprints/test_store.py` 的 `test_missing_or_damaged_asset_prevents_commit_and_checkpoint`；`tests/footprints/test_export.py` 的 `test_export_corrupt_asset_never_publishes_manifest` | 保留要求，本次未复跑 |

<a name="recording-engine-一致性保存与校验"></a>
## 一致性保存与校验

把世界变化和对应事件作为同一笔事实保存，保证中断或错误不会留下部分成功的结果。

<a name="recording-engine-持久化结构"></a>
### 持久化结构

```
run/
  manifest.json                 # 运行身份与代码（含实际导入的 AS2）、模型、提示、规则、数据版本及 Git 状态
  footprints.sqlite             # 事件、当前结算状态、素材索引与检查点
  assets/<sha256>               # 实际内容、模型证据、人物状态快照与交付物
  exports/<export-id>/          # 固定输入、来源索引和导出清单
```

图中只列记录引擎使用的持久化材料；冻结运行清单由运行宿主提供。
数字人维护自身记忆与可变工作区，模拟引擎协调其保存和恢复；交付目录由数据交付管理。
交互菜单声明随场景原始字节进入场景身份，静态工具描述进入工具模板身份；运行时有效菜单留在对应
观察事件里，不新增一份与场景身份重复的菜单版本清单。
记录引擎仅持有检查点封存的不可变快照。

运行清单结构 `3` 冻结五字段 `environment_identity`：环境身份、环境版本、规则版本、目录内容摘要与图摘要。创建、打开、恢复或重放在副作用前核对同一身份，同版本异内容拒绝执行。
检查点已有 `manifest_sha256` 覆盖这些字段，足迹导出的 `frozen_run_manifest` 原样保留实际身份，不从导出时的目录重建。字段见[环境身份合同](../CONTRACTS.md#contracts-model-identity)。

运行清单冻结的 `code_sha256` 覆盖 `src`、`custom`、`pyproject.toml`、`uv.lock` 与实际导入的 AS2 包
（包目录内除 `__pycache__` 外的全部文件，标签与安装位置无关）。AS2 包目录缺失、为空或与固定补丁源码摘要
的源码摘要不符时，运行宿主拒绝启动，不写运行清单。清单另记 `git_commit` 与 `git_dirty`（`git status --porcelain`
是否非空，含未跟踪文件、不含忽略文件；非 Git 检出时二者为 null），二者只记录，不参与恢复比对。
规则见[时间与能量规格](../dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动)「运行身份」。

维护工具按这些记录统计触达次数；活动完成和幂等重试不重复登记命中。

| 数据表 | 责任 |
| --- | --- |
| `events` | 不覆盖的事件序列；可按人物、活动、动作和时间查询 |
| `world_state` | 当前已确认世界、人物资源、活动与待发生事件；带状态版本 |
| `artifacts` | 哈希、文件位置、大小、模态、来源和使用条件 |
| `checkpoints` | 完整世界快照与 AS2 人物快照引用、时钟、随机状态和最后事件序号 |

数据库只接受运行环境持有的唯一存储句柄写入；上游先保存证据素材，再通过该句柄提交引用。

`events` 由数据库触发器限制为只追加，并限制持久序号连续；历史事件不能更新或删除；已提交的检查点记录同样禁止更新或删除。
`world_state` 保存当前已确认状态，完整历史恢复依靠共同检查点及其关联事件，不能把当前状态误当作任意历史截点的状态。
素材按内容哈希共享字节，事件和引用中的来源语义仍各自保留。

<a name="recording-engine-数字足迹存储接口"></a>
### 数字足迹存储接口

状态提交接口在本主题定义；素材、检查点、恢复、固定截止读取与运行查询接口分别见对应主题。

<a name="recording-engine-存储调用"></a>
### 存储调用

| 调用 | 签名 | 合同 |
| --- | --- | --- |
| `FootprintStore.commit` | `(state: dict, events: List[EventDraft]) -> List[Event]` | 一次事务更新状态、资金与事件；事件序号由存储分配，状态版本由环境递增并验证连续性；返回本次落库的持久事件 |


生活模型负责验证世界状态与封闭请求类型；存储接收已验证的 JSON 状态与事件，不反向决定活动或资源规则。素材引用包含 SHA-256、字节数、模态与来源。所有业务模块只通过同一个环境实例的存储句柄写入，人物 worker 不能自行修改 `footprints.sqlite`。

<a name="recording-engine-存储接口行为"></a>
### 存储接口行为

| 操作 | 行为边界 |
| --- | --- |
| 新建运行 | 明确创建新的运行身份与目录；目标存在时拒绝覆盖 |
| 打开运行 | 只打开已有的有效数据库与冻结清单；缺失数据不能隐式新建 |
| 提交状态与事件 | 校验状态连续性、资金差额、因果引用和素材后，在同一事务提交全部状态与事件 |


<a name="recording-engine-事务与失败处理"></a>
### 事务与失败处理

<a name="recording-engine-事务边界"></a>
#### 事务边界

生活状态、资金结算、动作状态与对应结果事件在同一笔 SQLite 事务提交。
领域规则由生活模型验证；记录引擎验证持久化一致性，不能反向决定活动规则。

```mermaid
flowchart TD
    DRAFT[环境生成已验证状态与事件] --> VALIDATE[校验版本、资金与引用]
    VALIDATE --> TX[开始数据库事务]
    TX --> EVENTS[分配序号并插入全部事件与素材索引]
    EVENTS --> STATE[更新世界状态]
    STATE --> DONE[提交并返回持久事件]
    TX & EVENTS & STATE -->|失败| ROLLBACK[回滚本次状态与全部事件]
    ROLLBACK --> STOP[传播运行故障]
```

<a name="recording-engine-事务范围"></a>
#### 事务范围

外部模型调用、游戏进程和文件系统整体不在数据库事务内。
外部调用的开始、结果或未知状态由调用方提交；记录引擎按提交内容保存，不执行或重试外部调用。
同一次结算的事件批量提交，不逐事件拆散原子边界。

<a name="recording-engine-失败分类"></a>
#### 失败分类

| 情况 | 记录引擎的处理 |
| --- | --- |
| 状态版本、事件结构、资金差额或因果引用不一致 | 拒绝提交，返回明确的校验错误，已确认数据不变 |
| 素材缺失、损坏或引用不匹配 | 拒绝读取、提交、检查点、分支或导出，不返回空内容代替证据 |
| 数据库事务失败 | 回滚本次状态与全部事件，向调用方传播失败 |
| 检查点、分支存储或导出构建失败 | 不发布成功记录或清单，不修改父运行 |
| 故障事件本身也无法保存 | 输出带可用关联身份的英文错误，不伪造已经持久化的 `run_failed` |

保存成功只代表对应记录已提交。业务拒绝、协议重试、运行终止与恢复启动由
[模拟引擎的失败处理](04-simulation-engine.md#simulation-engine-失败处理)和[AS2 失败传播](../dev/SPEC-as2-integration.md#spec-as2-integration-失败传播改动)约束；
记录引擎不得把持久化故障包装为成功返回。

<a name="recording-engine-状态版本与连续性"></a>
### 状态版本与连续性

<a name="recording-engine-状态版本递增"></a>
#### 状态版本递增

状态由环境递增版本，存储检查连续性。实际改变世界时，版本只能增加一；
仅追加观察等证据、世界内容完全相同时，可以保持同一版本。
同版本必须具有相同的确定性编码字节，不能隐藏状态修改；时钟不能倒退。

<a name="recording-engine-事件序号严格递增"></a>
#### 事件序号严格递增

序号由存储分配，按提交顺序严格连续递增，同秒事件也有明确次序。
运行内的截止序号确定固定历史范围，不依赖墙钟时间排序。

<a name="recording-engine-状态版本变化必须有事实事件"></a>
#### 状态版本变化必须有事实事件

版本变化至少对应一个事实事件，禁止静默改变时钟、资源或活动。
`state_version_before` 与 `state_version_after` 把事件绑定到状态变化，供因果追溯、连续性检查和检查点恢复核对。

<a name="recording-engine-状态关联字段"></a>
#### 状态关联字段

世界快照持久化采用确定性 JSON：键排序、集合按元素排序、编码规则固定。
新进程和不同 Python 哈希种子必须得到逐字节相同的状态。
只有事件和状态版本一致仍不足以证明完整恢复，还需检查参与者工作区、内容进度、待发生事件和随机数状态。

<a name="recording-engine-资金守恒验证"></a>
#### 资金守恒验证

权威资金变化只位于 `Event.data.resource_changes` 顶层，全部使用整数分。

| 约束 | 核验方式 |
| --- | --- |
| 支付只记一次 | 只在实际结算的结果事件声明；受理、再次观察与幂等回放不得再记支付 |
| 转账双方明确 | 使用 `account:` 账户标识描述双方，两端金额合计为零 |
| 事务差额一致 | 本次所有账户余额差额必须与同批权威资金事件逐项一致 |
| 初始注资完整且唯一 | 首条 `initial_funds_injected` 一次列出全部初始账户和外部对手方，之后禁止再次声明注资 |
| 完整历史可对账 | 仅凭初始注资与后续资金事件，就能重建最终共同检查点的全部账户余额 |

只检查单条事件收支平衡不够，必须核对余额差额与完整历史。
这里验证的是已提交余额与资金证据是否一致，不计算价格、报酬或支付资格；
金额由交换引擎计算，实际状态由模拟引擎提交。

<a name="recording-engine-一致性保存与校验的实现差距"></a>
### 一致性保存与校验的实现差距

| 设计要求 | 当前实现与差距 | 核查位置 |
| --- | --- | --- |
| 事件、状态与检查点共同提交 | 已有 SQLite 事务、素材校验、参与者检查与恢复路径；真实验收另见运行证据 | `src/worldmock/footprints/store.py`、`src/worldmock/footprints/checkpoint.py`、`src/worldmock/runtime.py` |


<a name="recording-engine-一致性保存与校验的验收"></a>
### 一致性保存与校验的验收

| 要求 | 必须通过的观察 | 证据位置 | 本次核验状态 |
| --- | --- | --- | --- |
| 事务原子性与失败回滚 | 状态、资金、动作与结果事件在同一次事务内一并落库；SQL 失败时资金与全部事件一起回滚，不留部分状态 | `tests/footprints/test_store.py` 的 `test_sql_failure_rolls_back_money_and_all_events`、`test_state_and_events_commit_together_and_reject_same_version_mutation` | 保留要求，本次未复跑 |
| 状态版本与事件序号连续性 | 时钟回退与版本跳变被拒；状态版本变化必须有至少一个事实事件；同秒事件按严格递增序号排序而不依赖时间戳 | `tests/footprints/test_branch_coverage.py` 的 `test_store_commit_rejects_clock_regression_and_version_jumps`；`tests/footprints/test_store.py` 的 `test_world_change_without_any_event_cannot_be_committed` | 保留要求，本次未复跑 |
| 资金守恒与对账 | 资金变化的唯一位置是 `Event.data.resource_changes` 顶层，单位为整数分；每条转账两端写清、相加为零；受理与幂等回放不再添支付；只看首笔注资与此后各笔权威资金事件，就能算出最终共同检查点的各账户余额 | `tests/footprints/test_ledger.py` 的 `test_final_checkpoint_balance_must_equal_complete_event_history`、`test_reused_receipt_cannot_record_a_second_payment`、`test_balanced_transfer_reconstructs_actual_final_accounts` | 保留要求，本次未复跑 |
| 真实存储介质口径 | 所有持久化行为通过真实 SQLite 与文件系统的集成测试，不以内存模拟替代 | `tests/footprints/` 全目录 | 保留要求，本次未复跑 |

<a name="recording-engine-保存共同检查点"></a>
## 保存共同检查点

在一轮交互完整结束后，把世界与所有参与者的状态保存为同一个可验证的恢复起点。建立分支的流程见[恢复与反事实分支](05-recording-engine.md#recording-engine-恢复与反事实分支)。

<a name="recording-engine-检查点创建"></a>
### 检查点创建

检查点封存同一已提交状态与日志边界：账户、身体、四类活动、当前已选内容、全局进度及 last_consumed_at、session 推送、实际订单、驾驶天数与最后驾驶日期、闹钟和剩余预排事件，以及人物运行与记忆状态。检查点创建不执行世界动作，不重抽内容，不重复付款、收货、领取或响铃。


<a name="recording-engine-共同检查点接口"></a>
### 共同检查点接口

| 调用 | 签名 | 合同 |
| --- | --- | --- |
| `FootprintStore.checkpoint` | `(state: dict, snapshots: dict[str, AssetRef]) -> Checkpoint` | 先校验全部素材，再提交共同检查点；返回检查点记录，其身份与截止事件序号在记录内 |


| 操作 | 行为边界 |
| --- | --- |
| 创建检查点 | 校验世界状态和全部参与者素材后提交记录，返回数据库确认的检查点 |


<a name="recording-engine-地图与供给的恢复身份"></a>
### 地图与供给的恢复身份

共同检查点封存世界结构 `11` 的订单、待收货订单、呈现状态、内容进度与会话推送。图与目录定义只读，恢复不从初始目录覆盖已提交状态，也不重新生成图。
运行清单结构 `4` 保存 `environment_identity{environment_id, environment_version, rules_version, catalog_content_sha256, map_sha256}`，检查点的 `manifest_sha256` 钉同一清单。打开、恢复和反事实分支逐字段核对当前身份，差异即拒绝，分支不改父运行。
旧状态和旧清单直接拒绝，世界不再保存供给库存、报价、营业例外、资格或可见供给字段；实际恢复消费者在共同集成波次验收。

<a name="recording-engine-保存共同检查点的验收"></a>
### 保存共同检查点的验收

| 要求 | 必须通过的观察 | 证据位置 | 本次核验状态 |
| --- | --- | --- | --- |
| 共同检查点的完整性与参与者齐备 | 检查点先校验全部人物、游戏与环境素材再提交；缺少任一参与者的检查点不完整；最终共同检查点缺失或其之后仍有事件时运行不算完成 | `tests/footprints/test_export.py` 的 `test_checkpoint_requires_every_participant`、`test_clock_end_without_successful_final_checkpoint_is_not_complete`、`test_event_after_final_checkpoint_invalidates_completion` | 保留要求，本次未复跑 |

<a name="recording-engine-恢复与反事实分支"></a>
## 恢复与反事实分支

从共同检查点构建新的分支存储，提供已核验的世界与参与者快照，保留原运行及分支来源关系。
将这些材料恢复为可运行的世界、人物与外部进程，由模拟引擎完成。

<a name="recording-engine-恢复行为"></a>
### 恢复行为

```mermaid
flowchart TD
    INPUT[父运行与指定共同检查点] --> CHECK[核验记录、冻结清单、截止事件与全部素材]
    CHECK -->|失败| FAIL[返回失败，保留父运行]
    CHECK --> STAGE[在父运行外准备新的分支目录]
    STAGE --> IDENTITY[冻结新运行、父检查点、分支原因与来源组]
    IDENTITY --> COPY[复制截止内的历史事件与封存快照]
    COPY --> PUBLISH[原子发布完整分支存储]
    PUBLISH --> MATERIAL[返回分支存储与已核验的恢复材料]
    MATERIAL --> SIMULATION[模拟引擎恢复参与者、施加干预并决定启动]
```

目标目录必须全新，并且不能位于父运行目录内。
创建分支不覆盖父运行或检查点后的已提交事件；复制的历史事件保留原始身份和字节，新增事件属于新运行。
分支只使用检查点封存的工作区，不复制父运行检查点之后变动过的可变文件。
人物工作区快照只收普通文件，自上而下遍历，遇到符号链接或目录联接即拒绝，不进入它指向的目录；快照与恢复对成员用同一组检查，保存不了恢复不了的快照。恢复先核对全部成员再建目标目录：成员须是普通文件（稀疏等变体不算），名字是只用 `/` 分隔的根内相对路径（声明判定与安全文件共用 `corpora.manifest`），每一级在任何平台上都能原样保存（不以点或空格结尾、不是 Windows 设备名、不含 Windows 禁用字符、控制字符或孤立代理码点、UTF-8 不超过 255 字节），忽略大小写与 Unicode 规范形式后互不重名，且没有文件同时是另一成员的上级目录；写入时目标已存在只能是先前成员的别名（Windows 8.3 短名、大小写或 Unicode 规范化），同样拒绝。任一不符或归档损坏都以 `IntegrityError` 拒绝，不发布分支。

<a name="recording-engine-恢复入口边界"></a>
### 恢复入口边界

记录引擎的返回值只确认分支存储与恢复材料有效，不表示人物、游戏进程或模型服务已经就绪。
运行启动与失败传播由[模拟引擎的恢复编排](04-simulation-engine.md#simulation-engine-恢复与反事实运行编排)和
[AS2 恢复入口](../dev/SPEC-as2-integration.md#spec-as2-integration-恢复与检查点改动)负责。

<a name="recording-engine-反事实分支与来源关系"></a>
### 反事实分支与来源关系

分支获得新的 `run_id`，记录 `parent_run_id`、`parent_checkpoint_id` 与分支原因，沿用 `source_group_id`。
记录引擎保存调用方提供的干预描述和 `intervention_sha256`，保持父检查点、原始事件与新分支证据可追溯。
干预是否受支持、何时执行及如何改变世界，由模拟引擎决定；实际执行结果仍通过事件和状态提交。
下游样本划分与反事实标签由 LabelKit-DataSet 定义；原始来源身份随完整导出提供。

<a name="recording-engine-重放与重新调用的区别"></a>
### 重放与重新调用的区别

读取和核验历史只能还原已经保存的事实。重新调用模型与外部服务属于模拟执行，结果不保证相同。
记录引擎不承诺外部服务严格只调用一次，也不能把新的调用结果冒充原始历史。

<a name="recording-engine-分支状态与发布"></a>
### 分支状态与发布

记录引擎核对检查点格式、冻结清单、状态、参与者快照与素材之间的一致性；不一致即拒绝构建分支。
运行宿主核验当前代码（代码身份覆盖实际导入的 AS2）、依赖、模型配置、场景与静态工具模板是否满足冻结身份，决定是否允许恢复执行。
菜单声明、源码或工具模板漂移均不得迁移、补写或按旧检查点继续。
分支存储建立时世界版本增加一，追加 `environment_event` 类型的 `branch_created` 事件。
存储发布与运行就绪分别确认；模拟引擎完成参与者恢复后才允许开始决策。

<a name="recording-engine-崩溃恢复"></a>
### 崩溃恢复

记录引擎只提供完整共同检查点及其封存材料；未确认尾部不能拼接为检查点内的历史。
人物和游戏进程的重建见[模拟引擎的恢复编排](04-simulation-engine.md#simulation-engine-恢复与反事实运行编排)。

<a name="recording-engine-分支恢复接口"></a>
### 分支恢复接口

| 调用 | 签名 | 合同 |
| --- | --- | --- |
| `src/worldmock/footprints/checkpoint.py::restore_branch` | `(store: FootprintStore, checkpoint_id: str, destination: Path, branch_metadata: dict \| None = None) -> FootprintStore` | 核对检查点与冻结清单、格式版本和全部素材后，在新目录建立分支并返回绑定该分支的存储；反事实身份发布前冻结，父目录不变 |


| 操作 | 行为边界 |
| --- | --- |
| 恢复分支 | 校验指定检查点、冻结清单与全部素材；在新目录建立并发布分支存储，父目录保持不变 |


<a name="recording-engine-恢复与反事实分支的验收"></a>
### 恢复与反事实分支的验收

| 要求 | 必须通过的观察 | 证据位置 | 本次核验状态 |
| --- | --- | --- | --- |
| 恢复材料完整且一致 | 未通过素材校验的检查点事务不发布记录；重算摘要与状态不一致的检查点被拒 | `tests/footprints/test_store.py` 的 `test_failed_checkpoint_transaction_does_not_publish_record`、`test_rehashed_checkpoint_with_inconsistent_world_is_rejected` | 保留要求，本次未复跑 |
| 恢复分支隔离与父目录不变 | 恢复在新目录创建分支存储并提供封存工作区；父运行目录与尾部事件不被覆盖，分叉之后父运行改动过的可变工作区也不复制过来；分支目录不得落在父运行内部 | `tests/footprints/test_store.py` 的 `test_complete_checkpoint_branch_preserves_parent_and_exact_workspace`；`tests/footprints/test_branch_coverage.py` 的 `test_restore_branch_rejects_destinations_inside_parent_run` | 保留要求，本次未复跑 |

<a name="recording-engine-固定截止导出"></a>
## 固定截止导出

把明确历史范围内的足迹冻结成可交付快照，让后续模拟和分支追加不改变这次交付的输入。

<a name="recording-engine-导出行为"></a>
### 导出行为

导出以冻结的截止事件和对应状态为输入，原始运行保持只读。`export_run` 先在临时目录写出、重算并校验，
最后写清单并原子发布；失败不留下成功清单。导出清单 `schema_version` 为 4，`artifacts` 恰好包含五项：

| 工件 | 文件与事实 |
| --- | --- |
| `events` | `events.jsonl`，截止前缀内的持久事件，保留原始身份、顺序与因果引用 |
| `activities` | `activities.jsonl`，从活动快照重建的冻结活动；出行保留起点、终点、`path`、距离、`route_progress_meters`、`destination_id` 与 `travel_mode`，app 使用保留 `media_position_seconds`；不含 `effective_work_seconds` 或 `playback_speed` |
| `verified_experiences` | `verified-experiences.jsonl`，等待、收货、到达与实际动作内容消费经历，由事件独立重算 |
| `purchases` | `purchases.jsonl`，`fact_kind` 为 `purchase`、`order_attempt`、`pickup_ready` 或 `receipt`；没有目录浏览事实 |
| `app_usage` | `app-usage.jsonl`，`fact_kind` 为 `app_use`、`app_touch`、`content_consumed`、`push_delivered`、`push_opened` 或 `push_released` |

购买投影保留当前四种事实的真实所有者与来源：

| `fact_kind` | 当前字段与来源 |
| --- | --- |
| `purchase` | 付款人的 `agent_id`，以及 `order_id`、`goods_id`、`category`、`store_id`、`app_id`、`payment_app_id`、价格与配送费、数量、总价、`fulfillment`、`recipient_id`、`request_message_id`、`created_at`、`action_id`、首次订单结果的 `source_event_id` |
| `order_attempt` | 请求人物的 `agent_id`、`action_id`、`request_event_id`、规范化后的 `way`、首个结果的 `status` 与 `error_code`、`occurred_at` |
| `pickup_ready` | 实际模拟收货人的 `agent_id`、`order_id`、购物 `app_id`、`pickup_location_id`、`arrived_at` 与到站事件 `source_event_id` |
| `receipt` | `order_id`、`recipient_id`、`received_at`、`goods_id`、`category`、`quantity`、`provided`、`applied` 与 `source_event_id`；模拟人物的 `agent_id` 是实际收货人，来源是本人的 `goods_received`；联系人的 `agent_id` 是实际交付来源事件的付款人，来源是该订单结果或 `delivery_arrival` |

六种 app 事实共用 `fact_kind`、`agent_id`、可空 `activity_id`、`app_id`、可空 `action_id`、`manner`、`device`、`source_event_id`。`app_use` 另带活动 `status` 与真实 `segments[{started_at, ended_at}]`；`app_touch` 另带 `edge`、`occurred_at`；`content_consumed` 另带 `content_id`、实际区间、消费前后位置、`completed` 与可空 `push_id`，不含内容分类。三种推送事实另带 `push_id`、`content_id`、`session_id`、`occurred_at`。

清单逐项写文件路径、行数与摘要，`frozen_run_manifest` 保留五字段 `environment_identity`；完整实验导出另带
最终共同检查点。素材索引、素材字节、`quality.json` 与按人物日期生成的时间线是清单认证的辅助产物，
不扩充五项 `artifacts` 闭集。JSONL 每行是独立完整 JSON 对象，使用 UTF-8、排序键和固定分隔符，
禁止 NaN 与无穷值。消费端从 `events.jsonl` 与认证检查点独立重算购买、app 使用、推送状态和质量报告，
任一不一致都拒绝。

<a name="recording-engine-完整性验证"></a>
### 完整性验证

正式交付前，记录引擎校验完成标志、运行与覆盖身份、全部主文件哈希和行数、事件全序与因果引用、
唯一正常结束、最终共同检查点、参与者快照及所有素材。
只达到场景终点不代表完整；缺少最终共同检查点，或最终检查点之后仍有事件，都不能认定为完成。
不完整导出只用于诊断，不能作为下游正式加工的认证输入。
`validate_export` 在重算并逐行比对五个工件后，还逐条检查[足迹不变量](../dev/SPEC-action-and-content-simplification.md#spec-acs-足迹不变量)的七项约束；任一项不成立都以 `IntegrityError` 拒绝导出。


<a name="recording-engine-导出认证"></a>
### 导出认证

导出保留原始 `event_id`、`run_id`、序号、输入文件和行位置，素材索引绑定对应字节及摘要。
恢复与反事实分支保留父检查点和来源身份，使下游能够追溯材料来自哪段原始历史。
认证只证明导出与固定截止的持久事实一致；样本标识、窗口正文展开与数据划分由 LabelKit-DataSet 定义。
素材哈希与事件身份分别标识内容和发生记录，不能互相代替。

<a name="recording-engine-质量报告"></a>
### 质量报告

当日目标的登记、修订和实际推进由模拟引擎裁决。记录引擎在零点计划的 `day_plan_generated`、修订的动作结果和结算事件中
保存完整目标清单（含各目标的 `remind_at`、`reminded_at` 与大致时刻 `around_at`），与世界状态原子提交；目标的活动证据、登记时刻与推进时刻进入共同检查点，
恢复不得重复生成零点计划，也不得重复发放推进脉冲。
`quality.json.day_plan_metrics` 保存按代码摘要分组的习惯、外部约束、刻意目标三层度量及受阻门禁，
只读取本批已认证截止事件；刻意目标层的 `family_progress_rate` 只计非例行意图的目标，例行意图另计 `routine_goals`，
推进与引擎同一判定（带大致时刻的目标只认其前后 90 分钟内进行的活动）；字段与判据见 [当日目标规格](../dev/SPEC-day-plan-goals.md#spec-day-plan-goals-度量输出合同)。


`quality.json` 是同一批冻结事实的只读统计：动作结果计数、已记录活动与内容接触区间汇总（重复观看只计从头重看）、
食品利用效率、分配内容消费与完成统计、记忆来源检查、实际模型用量（含零点计划，按 `purpose` 分列：`model_usage.by_purpose` 逐项给出
`purpose`、`request_events`、`provider_responses` 与 `token_usage`；人物决策中发出的请求不带 `purpose`，记为 `decision`，
后台用途只有 `plan_day`）、冻结价格估算和实测加速比。
`activities.known_elapsed_seconds` 是各活动自身时间之和；同人并行时另以共同身体结算区间并集报告
`activities.person_elapsed_seconds_by_agent`，不能将前者冒充人物一天的经过时长。决策节律中，
边界计数与边界的唤醒原因只从每人每区间唯一的共同身体结算读取，决策轮的唤醒原因从 `decision_started` 读取，
两份唤醒原因分列：`decision_rhythm` 另按人物与模拟秒统计 `decision_started` 的同刻轮数分布
（`same_moment_round_counts`，键为同一人物同一秒的轮数，值为出现次数），并按 `decision_started.data.wake_reasons`
统计轮次原因（`decision_started_wake_reason_counts`，闭集外成员计入
`unrecognized_decision_started_wake_reason_counts`）；食品利用效率读取实际 `goods_received` 效果；`work_topics` 读取分配内容的实际消费与完成证据。
报告 `schema_version` 为 6。`conversion_efficiency.food` 按食品类 `goods_received` 的 `applied / provided` 加权，没有食品收货时原因为 `no_food_receipt`。`content_contact.by_app` 只计 `content_consumed`，`by_action` 计全部内容结算，不含 `by_kind`；`repeat_watch` 同时报告 `repeat_seconds_share`。现行 `work_topics{topics, completed, worked_seconds_by_topic, reports}` 读取分配内容，
删除 `conversion_efficiency.work`，`model_usage.by_purpose` 不再有 `work_review`；`errand_metrics` 的提醒后行动只认受理的购买，完成证据只认 `goods_received`。此前 `schema_version` 4（版本 2 起 `decision_rhythm` 不再统计决策自述，删去 `decision_started_with_decision_statement` 与
`decision_started_missing_decision_statement`，另以 `decision_budget_exhausted_events` 计 `decision_budget_exhausted` 事件数；
版本 3 是[一日运行保真规格](../dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要)的新节，全部只报告不放行；版本 4 是
[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)的两个新节 `errand_metrics` 与 `conclusion_memories`，同样只报告）。
`input_warnings` 一节统计运行清单冻结的运行输入警告（警告码闭集与条目形状见[接口合同「运行输入警告」](../CONTRACTS.md#contracts-运行输入警告)）：
`recorded` 表示清单是否记录了警告；记录时给出 `warning_count`、按全部警告码列出的 `code_counts`（未出现的码为 0）、
闭集外的 `unrecognized_code_counts`、按人物编号的 `agent_counts` 与场景级（`agent_id` 为 `null`）的 `scenario_warning_count`；
清单没有记录（引擎直驱建立的运行为 `null`，旧清单没有这个键）时各计数为 `null`，不记为零。
`truncations` 一节统计输出截断（[一日运行保真规格 7.2](../dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-输出截断与思考螺旋)），只读已记录的
`model_response`，不需要新证据：每个调用身份取一条终态（与 `model_usage` 同一去重），提供商回复 `finish_reason` 为 `length` 的计为
一次截断；给出 `truncated_responses`、按用途分列的 `purpose_counts`（人物决策记为 `decision`）、这些回复实报的
`usage.completion_tokens` 之和 `wasted_output_tokens`（截断回合整段丢弃，输出全部浪费）与其中
`usage.completion_tokens_details.reasoning_tokens` 之和 `reasoning_tokens`；某次截断缺少对应计数时该合计为 `null`，
缺失次数记于 `responses_missing_output_tokens`、`responses_missing_reasoning_tokens`，不记为零，没有截断时两项合计为 0。
宿主判定的截断回合另在该响应的 `error` 记协议错误串「OUTPUT_TRUNCATED: <英文说明>」。
正餐多样性只取本人实际正餐收货，按真实订单收货证据去重；付款、在途与到站待领不算收到或吃过。
记录引擎不重算身体或资源效果，不推断人物意图，不设置行为真实性或训练样本合格阈值；
这些判断由对应规则、行为评估与数据交付负责。
测不到的指标标记未知，不能填成零；价格估算不能称为供应商账单。
当前报告按 `event_range`、`run_outcome`、`actions`、`activities`、`content_contact`、
`conversion_efficiency`、`decision_rhythm`、`memory_references`、`model_usage`、`acceleration`、`day_plan_metrics`、
`errand_metrics`、`conclusion_memories`、`input_warnings`、`timeline_labels`、`contact_reply_guard`、`name_rejections`、
`truncations` 分组，
统计从事实事件计算，不能简单把受理次数当作完成次数。
`memory_references` 分组保留名称，内容按 `memory_updated.data.trigger` 中的每个写入点核对系统登记的来源（收尾与反思
同在一步时两项都核对；`write_points_checked` 为核对的写入点总数）：`activity_conclusion` 对应同一决策已登记的
`conclude_activity`，`reflection` 对应醒来那一轮的观察。问题码按写入点：`conclusion_without_memory_update` 指经 `control_items` 读出
带记忆或看法的 `conclude_activity` 请求（`action_requested`；登记后结算被拒时记忆照写）在同一 `decision_id` 下没有
`trigger` 含 `activity_conclusion` 的 `memory_updated`；`memory_update_without_source` 指 `memory_updated` 的某个写入点
找不到对应登记（写入了条目却没有写入点同样计入）；观察的进度层来源 `progress_event_ids` 中任一身份不能解析到本人更早提交的事件时记
`invalid_observed_result_reference`。记忆不再带模型填写的引用，不核对条目身份引用。
`timeline_labels` 检查投影出的时间线只出现中文名称（[一日运行保真规格](../dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-时间线只出现中文名称)）：
用唯一的时间线渲染器从同批已认证事件逐人逐日（运行窗口内）投影时间线并逐行检查活动标签，书名号与引号内的作品标题、邮件主题不查；
其余由英文与数字连成的片段恰为 `readable.py` 中文种类表的英文键时计一项 `english_kind_names`，形如内部标识（小写片段以
`-`、`_`、`:` 相连，十二位以上十六进制串，`#` 加六位十六进制）时计一项 `internal_ids`；另给 `rows_checked` 与逐项 `findings`
（`agent_id`、`date`、`interval`、`label`、`item`、`text`）。`contact_reply_guard` 统计联系人回复的后验守卫
（[同一规格 7.3](../dev/SPEC-as2-integration.md#dayrun-fidelity-评审模型调用与叙述-联系人回复不虚构到场)）：`replies`、没有 `attempts` 证据的
`replies_without_guard_evidence`、`attempts`，按尝试计的 `hits` 与 `hits_by_category`（四个类别码全列，没有命中记 0），
按回复计的 `retries` 与 `fallbacks`。两组都只报告，不放行。
`name_rejections` 把名称类拒绝与其余协议错误分开计数（[同一规格 3.4](../dev/SPEC-map-and-storefront.md#dayrun-fidelity-按名调用与指名购买-名称类拒绝不计同刻轮数)）：
宿主一侧取人物决策（没有 `purpose` 的调用，按调用身份去重后的终态响应）已落库的结构化纠错 `data.protocol_feedback`，按其 `error_code` 归类，
名称类纠错码逐码计 `host_codes`（全部码列出，没有记 0；合计 `host_protocol_errors`），其余协议错误计 `other_protocol_errors` 与按码的 `other_protocol_codes`（落库的失败阶段码也计在这里，不是名称类纠错：现行实现中只有模型服务失败的 `MODEL_SERVICE_FAILED` 写进模型证据）；
环境一侧取不含重放的 `action_rejected`，名称类拒绝码逐码计 `environment_codes`（全部码列出，没有记 0；合计 `environment_rejections`），
并按同一人物同一模拟秒的结算先后用同刻规则分出免计（不计同刻轮数、不施加脉冲）的 `environment_free_rejections`。只报告，不放行。
`errand_metrics` 统计个人事务（[个人事务与工作安排规格](../VERIFICATION.md#errands-workday-度量与下游-质量报告) 6.1，由
`src/worldmock/evaluation/errand_metrics.py` 计算），不使用任何当日目标指标的键名，不以目标数为分母，不写进 `day_plan_metrics`：
事务身份取快照的 `errand_id`，所有人取事件的人物字段，终态是同一事务的 `errand_completed` 或 `errand_cancelled`，截止是导出的末一个事件；
`registered_count`（有 `errand_registered` 事件的不同事务数，是办成、取消、截止时未完成与跨夜四项的分母）、`completed_count`、
`cancelled_count`、`open_at_cutoff_count`（三者之和恒等于 `registered_count`）、`carried_overnight_count`（记下后跨过至少一个本地零点
仍未完成：终态恰在零点不算，截止恰在零点算）、`visible_observation_count` 与 `expected_observation_count`（记下事件之后、终态事件之前
——没有终态到截止——本人各次观察中待办层含该事务名称的次数与同期观察数，二者应相等）、`reminded_errand_count`（至少有一条
`errand_reminded` 的事务数）、`acted_after_reminder_count`（其中某次提醒之后、下一个本地零点与终态事件之前，本人有不是重放的
`action_accepted` 且指向同一商品的事务数：只读取受理结果顶层订单的商品，在途订单也算）、
`completed_without_evidence_count`（`errand_completed` 事件缺证据引用，或引用的都不是本导出里更早、经 `errands.evidence_rows`
给出同一所有人、结果种类与商品证据行的事件的条数；完整性计数，应恒为 0）。没有任何 `errand_registered` 事件时整组为 `null`，
有事务时各计数没有命中记 0。`conclusion_memories` 统计收尾记忆：不是重放的 `conclude_activity` 请求（`action_requested`，不论之后
是否被受理：记忆在登记时写入）经 `control_items` 读出的 `items[].memories`，看法与偏好 `views` 不计；`total_count`、`routine_count`
（类型为 `routine` 的条数）、`errand_overlap_count`（文字包含同决策观察待办层里某条事务的名称，或经同一观察私有绑定
`errand_bindings`、`errand_item_bindings` 取得的其商品名称的条数；纯子串判定，不做相似度）；没有收尾记忆时整组为 `null`。
两组都只读同批已认证事件与冻结清单（本地零点按清单的 `window.timezone`）、可逐值重算；「自述完成而没有证据」没有可判定字段，
不计量；只报告，不放行。
消费端先核对报告字节摘要，再从同批事件独立重算；报告存在或摘要正确本身不足以证明统计正确。
性能分析使用可测量的模型用量、墙钟耗时、模拟时长、事件和素材统计，不把假设阈值当成实测结果。


<a name="recording-engine-固定截止读取接口"></a>
### 固定截止读取接口

| 调用 | 签名 | 合同 |
| --- | --- | --- |
| `FootprintStore.read_events` | `(cutoff: int) -> List[Event]` | 按确定事件序号读取固定截止范围，不隐式包含持续追加的新记录 |


| 操作 | 行为边界 |
| --- | --- |
| 固定截止读取 | `read_events(cutoff)` 只返回截止序号以内的事件，后续追加不改变该范围 |


<a name="recording-engine-固定截止导出的实现差距"></a>
### 固定截止导出的实现差距

| 设计要求 | 当前实现与差距 | 核查位置 |
| --- | --- | --- |
| 所有导出视图受同一截止约束 | 事件和活动按截止读取；时间线仍把完整数据库连接交给渲染器，可能读到截止后的同日事件 | `src/worldmock/footprints/export.py`、`scripts/render_day.py` |
| 消费端验证全部导出文件 | 时间线在清单中有摘要，但当前认证流程未验证 `manifest.timeline` 的文件 | `src/worldmock/footprints/validation.py` |


时间线缺口需要以后续实现和反例验证闭合，不能以现有完整性测试通过替代。
最小验证场景是保持同一截止，追加一条同日后续事件，确认导出时间线不变；
再修改时间线文件，确认消费端拒收。

<a name="recording-engine-固定截止导出的验收"></a>
### 固定截止导出的验收

| 要求 | 必须通过的观察 | 证据位置 | 本次核验状态 |
| --- | --- | --- | --- |
| 导出完整性与导出认证 | 导出以固定截止事件及该事件当时的状态为唯一输入，产物按 本文「持久化结构」落定；完成标志、运行与覆盖身份、主文件哈希与行数、事件全序与因果引用、唯一结束、最终共同检查点全数通过后才可进入正式交付；导出保留原始事件身份与来源位置 | `tests/footprints/test_validation.py` 的 `test_production_windows_require_and_accept_complete_authenticated_export`、`test_incomplete_or_tampered_export_never_reaches_labelkit`；`tests/footprints/test_export.py` 的 `test_export_writes_hash_bound_timeline_projections` | 保留要求，本次未复跑 |
| 质量报告的确定性与未知口径 | 同一份认证输入逐字节确定；测不到的指标如实留作未知，不落成零；估算价格不得称为供应商账单；消费端先核对报告字节摘要，再顺同一批事件独立重算 | `tests/footprints/test_quality.py` 的 `test_quality_report_is_deterministic_for_the_same_authenticated_input`、`test_missing_measurements_remain_unknown_instead_of_becoming_zero`；`tests/footprints/test_validation.py` 的 `test_quality_report_requires_bytes_and_actual_event_recomputation` | 保留要求，本次未复跑 |
| 足迹不变量 | app 行只来自带 app 的活动；同一人物手机区间不重叠；收货、账户、内容、推送与来源所有人满足七项约束，重签全部投影也不能掩盖伪造事实 | `tests/footprints/test_acs_export.py::test_real_export_rejects_corrupted_events_after_all_five_projections_and_hashes_agree`、`tests/footprints/test_acs_export.py::test_real_manners_pauses_navigation_and_no_app_export_pass_all_invariants` | 结论取本阶段回执 |
| 收货行 | 每张已收货订单恰有一行 `receipt`；待领取没有收货行，模拟收货人和联系人各按真实来源归属 | `tests/footprints/test_acs_export.py::test_real_engine_current_five_artifacts_and_receipt_owner`、`tests/footprints/test_acs_export.py::test_real_station_export_keeps_purchase_payer_and_actual_recipient_notifications_and_receipt` | 结论取本阶段回执 |
| 活动快照与开始地点 | 主动结束与暂停写活动快照，导出的活动与最终共同检查点逐项一致；出行活动的 `location_id` 为出发地，结算不改写，到达经历照常产生 | [时间与能量规格「验收台账」](../VERIFICATION.md#verification-incremental-evidence)的「绿色基线」「本机运行修复」行 | 已实现，证据见[时间与能量验收记录](../archive/time-and-energy/time-and-energy-verification.md) |


<a name="recording-engine-运行查询与诊断"></a>
## 运行查询与诊断

以已经确认的记录回答运行到哪一步、发生了什么、哪里出错；查询与诊断不推进世界，也不替数字人作决定。

<a name="recording-engine-查询边界"></a>
### 查询边界

查询只呈现已提交世界、持久事件、检查点和运行宿主已确认的状态；不根据“暂时没有新事件”推断运行失败。
运行状态与阶段由[公共运行状态合同](../CONTRACTS.md#contracts-运行状态枚举)维护，
全局日志格式与埋点见[运行诊断输出](../CONTRACTS.md#contracts-运行诊断输出)。

记录引擎提供事实查询、引用追踪与完整性检查，自身错误使用上述公共日志合同。
持久事件与操作日志不能互相充当完成证据；已提交请求也不能证明活动完成或支付发生。

<a name="recording-engine-调试工具"></a>
### 调试工具

```sh
worldmock status --run-dir runs/white-collar
python3 scripts/inspect_run.py runs/white-collar summary
python3 scripts/inspect_run.py runs/white-collar events --type action_result --agent 0 --tail 20
python3 scripts/inspect_run.py runs/white-collar event 100
python3 scripts/inspect_run.py runs/white-collar money
python3 scripts/inspect_run.py runs/white-collar body --agent 0
python3 scripts/inspect_run.py runs/white-collar flows --tail 20
python3 scripts/inspect_run.py runs/white-collar rerun
python3 scripts/inspect_run.py runs/white-collar continuation --topic 咖啡 --baseline runs/white-collar-off
python3 scripts/inspect_run.py runs/white-collar queries
python3 scripts/render_day.py runs/white-collar/footprints.sqlite --agent 0 --date 2026-01-05 --out runs/white-collar
```

示例运行目录、人物和日期需替换为实际值。浏览工具只读打开 SQLite，不推进世界或修改事件。
`events --full` 显示完整事件；`body --agent <id>` 显示观察事件中的 `body.values` 与 `body.levels`，
逐行打印能量 `energy_kcal`、睡眠不足 `sleep_deficit_hours` 与饥饿、困意分级（`appetite`、`sleepiness`），与上一行相同或缺能量值的观察不打印。
`rerun` 只读计算[时间与能量规格「验收台账」](../VERIFICATION.md#verification-incremental-evidence)「真实复验」行的八项指标
（无进行中活动总时长、同刻轮数分布、播报唤醒占比、最低饥饿分级、首次进食前最低能量、进食次数、步行占比、
零点计划是否含早于 08:30 的提醒），逐项打印中文报告，缺少可计算记录的项标「不适用」；只报告，不作门禁。
`continuation [--topic 话题 ...] [--baseline <run_dir>]` 只读给出当前完整模型证据中的续接对比指标，以 JSON 打印 `{run_id, same_second_continuation, decisions, summary}`：`decisions` 逐决策（按首个行动请求的事件序号）
给 `decision_id`、`agent_id`、`occurred_at`、`same_second_follow_up`、`chain_position`（链首为 0，断链为 `null`）、`chain_intact`、
`tools_same_as_head`、`requests`、`reasoning_tokens` 与 `reasoning_chars`（每个响应都报告思考 token 时给 token 数，否则只给思考原文字符数，
两者不混算）、`protocol_errors`、`transport_failures`、`bad_request_400`（400 单列，不计入传输失败）、`cache_hit_tokens`、`prompt_tokens`、
`cost_usd`（与质量报告同一冻结价目）、`unpriced_responses`、`topics`（话题是否出现在思考中，缺省话题为「咖啡」）与 `repeat_orders`
（按订单去重后同一人物同一本地日再次购买同一商品的订单数）；`summary` 给链与跟进计数、同秒跟进与其余决策分列的思考量、错误、缓存、
已计价成本、话题命中、工具与链首相同的续接数与重复下单合计。行动请求按「不带 `purpose`、thinking 开启、`tools` 含 `start_activity`」识别。
开关取清单 `stage_model_identity.stages.action.same_second_continuation`；清单没有阶段身份（特性之前的运行）时为 `null`，不能作任何一臂。
`--baseline` 先核对两份清单：去掉运行身份键（`run_id`、`source_group_id`、`source_run_ids`）与续接开关后必须完全相同，且两臂开关不同，
否则以英文错误拒绝比较；可比时打印 `{arm, baseline}` 两份报告。只报告，不作门禁。
`queries` 只读按运行给出[个人事务与工作安排规格](../VERIFICATION.md#errands-workday-度量与下游-查询成本只报告) 6.3 的查询成本，以 JSON 打印
`{run_id, calls, failures, returned_chars, filtered_calls, empty_after_filter, model_calls_to_purchase, reflection_retries}`：查询调用数、
失败数、返回字符数、带筛选（`app` 或 `available_only`）的调用数、筛选后为空的次数、从首次查询到购买结算的模型调用数，以及反思校验
重试次数（工具只有 `submit_reflection` 且带附回消息的模型请求数）。口径取已落库的模型工具交换证据（`model_request`、`model_response`）：
调用是 `phase=response` 的 `model_response` 里名为 `query_world` 的工具调用；同一决策其后最先带同一调用身份的 tool 消息是解析为含
`matches` 的 JSON 对象的结果页时算成功，整批被拒（tool 消息是协议纠错）或从未得到回答算失败；返回字符数只计成功调用结果页的正文；
带筛选指 `app` 不为 null 或 `available_only` 为 true（旧四键调用不算），筛选后为空指其中成功且 `total_count` 为 0 的；
`model_calls_to_purchase` 取全运行第一个查询调用的人物与产生它的请求，计该人物从这一请求（含）到其后第一笔购买结算的
`action_result`（不含）之间开放 `start_activity` 的模型请求（含协议纠错与截断重试），没有查询或其后没有该人物的购买时为 `null`；
反思校验重试按消息多于原始两条识别。决策 token 用量取质量报告既有的模型用量分组，协议错误取 `continuation` 报告，对照运行同时报告，
本子命令不另设这些键；降低返回量不能以购买失败为代价。只报告，不作门禁。
日程工具读取已发生事件，`--out DIR` 将投影写入 `DIR/timeline/<agent>-<date>.md`，不把输出目录再写成 `DIR/timeline`。
时间线名称索引取实际商品、动作、地点、消息、app、内容与人物；
内容行写真实动作、所选 app 和标题，无 app 不写用手机，工作行写实际内容标题，出行行显示实际起点、终点和路径名称。缺名时只写中文活动种类，
不写英文种类名、内部标识或其片段。`name_index` 与 `day_rows` 接受已排序事件，质量报告使用同一投影检查标签。
中文时间线用于阅读，英文日志用于故障定位；投影不能充当人物当时的观察。
运行查看器 `tools/run-viewer.html` 在浏览器本地只读解析拖入或选择的 `footprints.sqlite`；同一 run 目录的 `manifest.json`（运行清单，或
导出清单中的 `frozen_run_manifest`，以库中世界状态的 `run_id` 认定）一起拖入或选择时，总览列出清单冻结的运行输入警告 `input_warnings`
（人物的警告按人物编号在前、场景级在后，逐条给警告码与 `message`；为 `null` 或清单没有这个键时写明未计算，空列表写明没有警告），
口径见[接口合同「运行输入警告」](../CONTRACTS.md#contracts-运行输入警告)。
查看器经自己的 `controlItems` 镜像（`tools/run-viewer.html`）读取三个控制工具的请求：请求级 `errands` 不是数组即报错
（`Activity control request requires errands`），不留旧形状分支，所以世界结构 8 的运行（含配对复跑的基线臂）用产出它的提交上的
查看器查看。事件种类与唤醒原因的中文标签表同批补六种事务事件与 `errand_reminder_due`；唤醒原因标签表另有班次与休息时段起止的四个标签
（`shift_start`「班次开始」、`shift_end`「班次结束」、`break_start`「休息时段开始」、`break_end`「休息时段结束」），这四个原因没有伴随事件，
只显示中文说法。查看器里 `query_world` 请求的显示分支在
真实运行下不可达（只读查询不登记为世界动作），不计入本特性的验收。

最终标签表包含 `action`“动作”、方式“进行”及“领取”、`app_push`“app 推送”、
`work_content_completed`“工作内容完成”和 `alarm_ring`“闹钟响铃”。唤醒与原生工具标签与当前生产闭集一致，
查看器只投影认证事实，实际 Node 与导出重算分别验收。


<a name="recording-engine-完整性诊断边界"></a>
### 完整性诊断边界

```mermaid
flowchart LR
    STATUS[查询已确认状态与最近事件] --> LOCATE[按运行、决策、动作和活动定位]
    LOCATE --> ASSETS[核验引用与素材]
    ASSETS --> CHECK[核对状态版本、资金差额与共同检查点]
    CHECK --> RESULT[返回证据链与具体不一致项]
    RESULT --> OWNER[运行宿主或领域模块决定如何处置]
```

诊断报告记录缺失、素材损坏、引用断开、版本不一致或资金无法对账等事实。
停滞判定、重试、重启和监控告警属于运行管理，领域状态是否合理属于对应规则与行为评估。

<a name="recording-engine-人物事件查询接口"></a>
### 人物事件查询接口

| 调用 | 签名 | 合同 |
| --- | --- | --- |
| `FootprintStore.read_agent_events` | `(event_type: EventType, agent_id: int, after: int = 0) -> List[Event]` | 按事件类型与人物读取指定序号之后的记录 |


| 操作 | 行为边界 |
| --- | --- |
| 查询当前状态 | 返回已提交的世界副本与最后事件序号；不能将内存中尚未提交的状态对外宣称为事实 |


<a name="recording-engine-运行查询与诊断的实现差距"></a>
### 运行查询与诊断的实现差距

本主题当前没有未闭合的实现差距：身体浏览已按当前身体模型读取 `body.values` 与 `body.levels`，见[调试工具](05-recording-engine.md#recording-engine-调试工具)。

<a name="recording-engine-运行查询与诊断的验收"></a>
### 运行查询与诊断的验收

| 要求 | 必须通过的观察 | 证据位置 | 本次核验状态 |
| --- | --- | --- | --- |
| 只读状态查询 | 只读呈现已确认的模拟时间、运行阶段、人物活动与模型调用统计，且不向运行目录落任何写操作 | `tests/footprints/test_status.py` 的 `test_status_reads_confirmed_state_without_modifying_any_run_file` | 保留要求，本次未复跑 |


<a name="design-recording-engine-current-responsibility"></a>
## 恢复与时间线的实际归属

```mermaid
flowchart LR
    RESTORE[checkpoint.restore_branch] --> STORE[FootprintStore 读取与写入]
    RESTORE --> CERT[历史及来源认证]
    EXPORT[时间线导出编排] --> LABELS[timeline_labels 时间线加载与呈现]
```
分支恢复由 checkpoint 编排，store 只承担存取；时间线渲染加载由 timeline_labels 承担，不回调上层导出入口。事件、检查点、运行身份、排序、提交边界和恢复失败语义保持本章冻结合同。完整自然日基线比较包含因果引用与导出正文，代码身份和壁钟差异只按验收规格明列的范围规范化。

<a name="recording-engine-版本表"></a>
## 版本表

以下记录保留当时的变更与验证证据；当前接口约束以正文和各特性规格为准。

| 版本 | 日期 | 修订内容 |
| --- | --- | --- |
| v1.0 | 2026-09-20 | 将记录引擎的事件与存储合同、足迹行为规格、格式、导出和诊断设计集中维护；删除重复文档和过时示例，保留现行约束、验收要求与待实现边界 |
| v1.1 | 2026-09-20 | 正文按项目框架映射中的功能框展开，接口、规则、实现差距与验收要求归入各自主题 |
| v1.2 | 2026-09-20 | 明确记录引擎仅负责事实持久化、完整性、检查点材料、分支存储与认证输出；调度、身体结算、恢复编排、公共运行合同和数据加工回归各自职责 |
| `2026-09-20-decision-context` | 2026-09-20 | 对齐决策上下文特性规格：统一决策自述命名，明确登记与结算结果的通道、场景信息和预算边界 |
| `2026-09-20-interaction-menu` | 2026-09-20 | 明确观察事件保存本轮有效菜单、菜单拒绝错误及场景与工具模板身份的恢复边界 |
| `2026-09-24-time-and-energy` | 2026-09-24 | 对齐时间与能量特性规格：必须保存的内容增零点计划后台任务与同一秒内的轮序；列出 `decision_started` 的字段与同刻轮数口径；结束、暂停与恢复逐活动写带快照的 `environment_event`，主动结束按自然结束的路径写终态；会话切换与提醒到期不另写事件；提示中的供给行写完整行；活动快照的 `location_id` 为开始地点，结算不改写；零点计划不产生事实，模型只见经历句子；记忆写入点为 `conclude_activity` 与就寝反思，来源由系统登记；代码身份覆盖实际导入的 AS2；导出与质量报告按零点计划、记忆来源与两份唤醒原因改写；登记本规格的实现差距。世界结构版本 5→6 漏登，随绿色基线直接改为 7 |
| `2026-09-25-time-and-energy-stage-0` | 2026-09-25 | 时间与能量规格阶段 0（绿色基线，issue #1）：世界结构版本句移到「检查点创建」并改为 7，版本不符的旧状态（含版本 6）直接拒绝、不迁移；「地图与供给的恢复身份」改为引用该句 |
| `2026-09-25-time-and-energy-stage-1` | 2026-09-25 | 时间与能量规格阶段 1：质量报告 `decision_rhythm` 增同刻轮次统计，按人物与模拟秒计 `decision_started` 的轮数分布，并按 `decision_started.data.wake_reasons` 统计轮次原因（E5-18）；同刻轮次不写共同身体结算，唤醒原因只记于 `decision_started` 与观察，边界兜底的 `no_active_activity` 照常写入结算（E5-4）；受限轮越界请求保留 `SAME_MOMENT_ACTION_RESTRICTED` 并计入同刻轮数（E5-6）；检查点封存 `same_moment_rounds`、`same_moment_stage` 与按人物暂存的本秒呈现留痕 `same_moment_presentation`（E5-13）；观察私有证据另记 `same_moment_stage`，与实际 Schema、环境派发核对，不进 `scene` 与 `facts`（E5-3） |
| `2026-09-25-time-and-energy-stage-3` | 2026-09-25 | 时间与能量规格阶段 3（活动、会话与记忆）：事件类型闭集不变，活动的结束、暂停与恢复复用 `environment_event`（`data.kind` 取 `activity_concluded`、`activity_paused`、`activity_resumed`，带 `activity_id`、活动快照 `data.activity` 与含本次请求事件的 `caused_by`）；实验截止时每项被截断的活动（进行中或暂停）另写一条 `activity_truncated`（带截断后的活动快照，`caused_by` 为开始该活动的请求事件），同源两项的聚合终态不带快照，`activities.jsonl` 据逐活动快照重建、与最终检查点逐项一致；`memory_updated.data` 增写入点 `trigger`（`activity_conclusion`、`reflection` 的有序列表，由引擎推导）与 `decision_id`，结束活动的来源另记于 `action_result.data.memory_sources`，按每个写入点分别核对来源；检查点封存地点会话与活动的收尾、播报锚点、会话归属与名称；质量报告 `memory_references` 按写入点核对，问题码改为 `conclusion_without_memory_update`、`memory_update_without_source`，删除条目引用核对 |
| `2026-09-25-time-and-energy-stage-5` | 2026-09-25 | 时间与能量规格阶段 5（零点计划与计划提醒）：事件类型闭集不变，零点计划的计划登记复用 `environment_event`（`data.kind="day_plan_generated"`，`visibility=system`，`data.day_plan` 与修订结果同形、目标的 `registered_at` 为该事件时刻，`data.source_event_ids` 为输入来源事件，经系统登记入口与模型证据、计划和 `plan_submitted_dates` 同一次提交，不写 `action_requested`）（E5-5）；零点计划的 `model_request`、`model_response` 带 `data.purpose="plan_day"`，不带 `decision_id` 与 `action_id`，按 `call_id`、`attempt`、`phase` 留证，与人物决策请求区分（E5-6）；检查点封存计划与提醒状态（目标的 `remind_at`、`reminded_at`）与既有字段 `plan_submitted_dates`，恢复、重放与分支据此不重复生成（E5-13）；「共同身体结算与决策证据」补零点边界的两次提交：旧日结算（带旧计划快照的 `activity_progress` 与终态结果）先于零点计划提交，该边界的共同身体结算连同唤醒字段在 `day_plan_generated` 之后由零点节律入口提交，所以零点的 `activity_progress` 以 `data.body_settlement_event_id` 指向它而不列入 `caused_by`；质量报告的 `model_usage.by_purpose` 写明分列字段 |
| `2026-09-25-time-and-energy-stage-6` | 2026-09-25 | 时间与能量规格阶段 6（运行身份）：「持久化结构」写明运行清单冻结的 `code_sha256` 覆盖实际导入的 AS2，AS2 缺失、为空或与锁定源码摘要不符即拒绝启动；清单另记 `git_commit` 与 `git_dirty`，非 Git 检出时为 null，二者不参与恢复比对 |
| `2026-09-26-time-and-energy-stage-7` | 2026-09-26 | 时间与能量规格文档一致性核对：「质量报告」中零点计划、修订结果与结算事件保存的目标清单写明提醒字段 `remind_at` 与 `reminded_at`（原写请求参数名 `remind_time`，与已保存的 `day_plan` 不符）（E5-16） |
| `2026-09-26-time-and-energy-stage-9` | 2026-09-26 | 时间与能量规格实现后文档（阶段 9）：按最终代码与测试核对差距已闭合后，删去本规格的实现差距行：「固定截止导出的实现差距」中冻结时补的结束与暂停无快照、结算改写出行 `location_id` 两行，「记录生活全过程的实现差距」中同刻决策证据、零点计划后台任务与系统登记入口、记忆来源、经历句子四行，该节改为一句「本主题当前没有未闭合的实现差距」；「记录生活全过程的验收」四行与「固定截止导出的验收」的「活动快照与开始地点」行由「待实现」改为「已实现」并链接时间与能量验收记录（E5-19）；「调试工具」写明 `body --agent <id>` 显示观察事件中的 `body.values` 与 `body.levels`（能量、睡眠不足与饥饿、困意分级），补示例与只读子命令 `rerun`（计算时间与能量规格验收台账「真实复验」行的八项指标，只报告，不作门禁），删「运行查询与诊断的实现差距」中已兑现的身体浏览行，该节同样改为一句（E5-20） |
| `2026-09-27-persona-prompt` | 2026-09-27 | 对齐人物 Prompt 七层规格（人物 Prompt 台账 E5-1、E5-2、E5-8、E5-9、E5-13 的冻结部分）：文件头注明修订部分待实现；必须保存的内容中人物决策改为续接时含本秒前序 assistant 与 tool 消息的请求、含可选正文与 `reasoning_content` 的响应和所选的世界请求，记忆层条目是派生视图；地图与门店供给证据中「登记确认」改为登记不回显，订单记收货人与所回应的消息、送达只入收货人持有，指名购买不查可见凭据且不把未呈现供给写进人物窗口，给予只转移不计摄入，实际呈现的供给改为工具层商品目标，浏览结果只列名称；已核验经历的五条窗口只作私有证据、不进提示，记忆层 `settled` 另有来源（已提交结果与世界事件的确定性句子），`MEMORY.md` 与主观归纳不再装配进提示，巩固摘要不进提示；「记录生活全过程的实现差距」登记本规格的差距 |
| `2026-09-27-persona-prompt-stage-1` | 2026-09-27 | 人物 Prompt 规格阶段 1 同批作业：观察事件保存层数据、看法偏好与私有证据，进度层只存 `progress_event_ids`，完整进度层 JSON 在实际请求中，`same_moment_stage` 不进七层（E5-3）；`decision_started.data` 为闭集，带 `decision_statement` 即拒绝，在首个行动请求时同批写入；`action_requested.data` 增 `self_note`（E5-5）；续接请求与响应正文的留证，`reasoning_content` 只取响应 `message` 中的一份统计（E5-6）；世界结构版本 8，封存会话 `opened_by_action_id` 与活动 `paused_at`（E5-11）；质量报告 `schema_version` 为 2，删两项决策自述计数（E5-12） |
| `2026-09-27-persona-prompt-stage-2` | 2026-09-27 | 人物 Prompt 规格阶段 2 同批作业：同秒续接的各轮仍逐轮写观察与 `decision_started`（E5-4）；续接缓冲不另存，恢复后从模型证据重建，普通恢复续接、反事实断链（E5-11） |
| `2026-09-27-persona-prompt-stage-3` | 2026-09-27 | 人物 Prompt 规格阶段 3 同批作业：就寝反思的快照条目带来源标记，relationship、preference 的 `subject` 只随请求原文保存在 `action_requested`（E5-7）；`memory_updated` 的来源核对改为等于该轮观察的私有证据窗口 `verified_experiences`（E5-10） |
| `2026-09-27-persona-prompt-stage-4` | 2026-09-27 | 人物 Prompt 规格阶段 4 同批作业：`action_result` 的订单记 `recipient_id` 与 `request_message_id`，结束进食写已吃、丢弃与剩余份量（E5-7）；检查点封存进食活动数据的 `opened`、订单的收货人与请求关联、`contact_holdings` 与 `outbox`（E5-11） |
| `2026-09-27-persona-prompt-stage-5` | 2026-09-27 | 人物 Prompt 规格阶段 5 同批作业：`environment_event` 增 `data.kind="decision_budget_exhausted"`（`visibility=system`，`data` 为 `kind`、`error_code`、`next_decision_at`）（E5-7）；受限轮阶段不再有 `wait_only`（E5-11）；导出新增 `purchases.jsonl`，导出清单 `schema_version` 为 2，质量报告增 `decision_budget_exhausted_events`（E5-12） |
| `2026-09-28-persona-prompt-implemented` | 2026-09-28 | 人物 Prompt 规格实现后文档（人物 Prompt 台账 E5-13、E5-14 与阶段 8 核对）：文件头注明修订部分已实现；「记录生活全过程的实现差距」删去冻结时登记的本规格八项差距，改为已闭合；「记录生活全过程的验收」的「经历句子」行改为记忆层 `settled`，增续接证据、记忆层、订单收货人三行，证据指向人物 Prompt 验收记录；按最终代码核对：必须保存的内容写明记忆层构建结果随观察事件与只读问答快照保存，`progress_event_ids` 写明两类来源，`action_result` 补给予 `data.given`、下单的 `answering`、进食的 `discarded` 与收尾 `data.outcome`，`decision_budget_exhausted` 补待收尾情形与可为 null 的 `error_code`，导出文件表补 `verified-experiences.jsonl`、`purchases.jsonl` 的逐行字段，导出清单 `artifacts` 恰好四键，质量报告补 `invalid_observed_result_reference`，调试工具补只读子命令 `continuation` 及其输出字段；版本号不变 |
| `2026-09-29-dayrun-fidelity` | 2026-09-29 | 按[一日运行保真规格](../dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要)同步（一日运行保真 RE1–RE6，随阶段 1–5 的代码落地，按 `e377669` 核对）：「共同身体结算与决策证据」`activity_ids` 是社交账与共同用餐的口径来源；「事件正文与资源证据」`action_requested.request` 为控制工具的 `items` 形状（旧形状经 `control_items` 读出），`action_rejected` 与同一拒绝的 `action_result` 按共用错误结构带 `error_code`、`reason`、`fix`、`next_step` 与逐项 `items[]`，受理结果的 `resolved` 记名称归一与指名购买解析出的完整标签，`notes` 另记按 `prefer` 选定的门店与「购买」定下的方式，`action_result.data.orders[]` 的每个订单增 `channel_id`（不进 `DeliveryState`），`model_npc_reply.data` 增 `attempts[]` 与 `guard_applied`，`decision_budget_exhausted` 的 `error_code` 取最近一次计入同刻轮数的拒绝，`decision_started` 一行注明引擎的同刻计数不计免计的名称类拒绝；三处目标字段列表增 `around_at`；「地图与门店供给证据」浏览结果列完整标签，指名购买的 `from` 约束、渠道解析与按 `prefer` 选定；「已核验经历与主观记忆」`views`→`subject` 由宿主转换、存储形状不变；「导出行为」`purchases.jsonl` 增 `channel_id` 一列，`order_attempt` 的 `way` 照请求所写的方式（同义写法按方式同义表归一），时间线标签取 `readable.py` 中文种类表，工作、通话、交谈有标题；「调试工具」运行查看器总览列出清单冻结的 `input_warnings`；「质量报告」新节 `input_warnings`、`truncations`、`timeline_labels`、`contact_reply_guard`、`name_rejections`，`schema_version` 3，分组列表补齐（含此前漏列的 `day_plan_metrics`），`host_codes` 全部码列出，刻意目标的推进比例只计非例行意图；文件头补修订说明，日期改写 |
| v1.3 | 2026-10-01 | 按工具交互规格直接修订当前条款：逐项对象请求、方式推导、严格中文购买枚举与完整错误定位、即时只读查询、当天操作备忘及紧凑呈现；合同已实现，验收证据另列，历史验收不证明新增功能。 |
| `2026-10-01-tool-interaction-review` | 2026-10-01 | 按附件复核定稿：开始活动改逐项对象、购买偏好用精确中文枚举且非购买为 null、to=null 统一本人、查询四键与二十项分页；合同已实现，验收证据另列，错误语言仍待澄清且暂维持英文。 |
| v1.4 | 2026-10-03 | 按[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)同步（实施计划 D5 作业，随阶段 1 的代码落地，按 `a1f680b` 核对）：「必须保存的内容」增记忆阶段调用行，`decision_started` 行注明就寝反思请求不是行动请求，`model_request`、`model_response` 行写明反思的每次尝试是独立调用、校验错误随下一次请求的附回消息留证，并登记四个失败阶段码及其去向，「已核验经历与主观记忆」写明反思输出不合合同的有界校验重试与耗尽即运行故障，「质量报告」`name_rejections` 注明落库的失败阶段码计入其余协议错误，宿主一侧的口径照现行实现改写为读取已落库的 `data.protocol_feedback.error_code`（原写按协议错误串判定）；顺带更正「工具纠错、只读查询与当天操作备忘的证据」末句与「记录生活全过程的实现差距」末句两处把已实现的工具交互写成未实现的句子；版本表前的说明句改为以正文和各特性规格为准；文件头补同步说明，日期改写 |
| `2026-10-03-errands-workday-stage-2` | 2026-10-03 | 按[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)阶段 2 同步（实施计划 D5 作业，随阶段 2 的代码落地，按 `d09d574` 核对）：「必须保存的内容」增个人事务行；`action_requested` 请求形状增请求级 `errands`（八键、取消、更新、新增的求值次序、`control_items` 与 `control_errands` 是唯一读取入口、缺键按完整性故障拒绝），受理与拒绝行写明事务变更与活动转换同一事务提交、错误明细的路径与名称载体，`action_result` 行增回执 `details.errands`；`environment_event` 登记六种事务事件的 `data.kind`、载荷与可见性（办成与提醒为人物可见，六种都不带决策与动作身份，只经 `caused_by` 关联），当日目标提醒与事务提醒分开写；表后增完成证据一段（读取一处、按事件顺序、被取消的事务不再接受同一提交的证据、结算每次提交一次）；唤醒原因闭集补 `errand_reminder_due`（不进睡眠允许集合，睡眠中错过的在醒来的那个边界交付）；观察段补待办层 `errands`、进度层两种记录与两类私有绑定，同秒续接补 `changes.errands`；记忆层 `settled` 排除六种事务事件，浓缩句只在进度层；事务变更不是记忆写入点；检查点封存版本 9 的 `errands`，世界结构 8→9；质量报告结构 4 与 `errand_metrics`、`conclusion_memories` 两节的定义；调试工具补查看器缺 `errands` 键报错与新标签；文件头的阶段号改为 2，补本规格首次受影响的发布身份。与实施计划作业原文不一致处按规格写：三种请求触发的事务事件不带动作身份（规格 2.7），每条被受理的变更各写一条而不按状态差分（2.2），完成证据不限于到店成交的订单并补同一提交内取消的规则（2.4），提醒在醒来的那个边界交付（2.5），事务指标无事务时整组为 null、有事务时没有命中记 0（6.1） |
| `2026-10-03-errands-workday-stage-4` | 2026-10-03 | 按[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)阶段 4（查询筛选与返回精简）同步（实施计划 D5-24、D5-25、D5-30，随阶段 4 的代码落地，阶段 4 与阶段 2 并行开发，在阶段 2 之后并入）：「工具纠错、只读查询与当天操作备忘的证据」`query_world` 改六键（增 `from`、`available_only`，旧四键请求按缺字段拒绝），筛选先于切页，返回不再附候选，两种查询参数错误，与 09 同句逐字一致；「数字足迹事件结构」观察私有事实增渠道索引 `world_query_channels`，供给条目所属门店记在已禁的 `location_id`；指名购买 `from` 来源写法由「门店名」更正为地点显示名（含「家」「住处」）；「调试工具」增 `queries` 子命令（八个键与各自口径，只报告）；文件头状态改为已落地阶段 1、2 与阶段 4；版本号不变 |
| `2026-10-03-errands-workday-stage-3` | 2026-10-03 | 按[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)阶段 3 同步（实施计划 D5 作业，随阶段 3 的代码落地，按 `56451b4` 核对）：「事件字段」观察段登记环境层在 `location` 之后的必填可空键 `duty`（今天的上班安排 `day`、`hours`、`place`、`breaks`、`status`，没有工作安排为 `null`），班内离岗照常受理、不另写事件；「调试工具」增只读子命令 `inspect_run.py workday --scenario <场景文件>` 及示例（工作负荷与作息偏离，场景文件摘要须等于运行清单的 `scenario_sha256`，作息窗口取自人物工作区的结构化卡片，只报告）；文件头状态改为已落地阶段 1–4（阶段 3 在自己的分支上开发，在阶段 2、4 之后并入），本规格首次受影响发布的引用块在阶段 2 之后补阶段 3 的场景结构 5、卡片结构 3 与抽取提示 `card-v3`；版本号不变 |
| `2026-10-04-errands-workday-implemented` | 2026-10-04 | [个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)实现后文档（实施计划 D5-26、D5-27，按 `d8a41d3` 的代码与验收登记表核对）：「记录生活全过程的验收」增个人事务留证、个人事务检查点、严格反思留证三行，证据位置指向该规格验收台账的对应行（首行所引的台账行照规格现名写「事务变更与原子受理」，实施计划作业原文写作「事务登记与原子受理」），状态链接个人事务与工作安排验收记录；文件头状态行注明已实现并链接该验收记录（证据为离线测试，规格阶段 6 的配对全天复跑未执行）；日期改写，版本号不变 |
| 环境数据归属 | 2026-10-05 | 按环境数据归属规格同步双仓职责、包内通用规则与显式环境路径；场景结构 6、必填标题和冻结活动效果按正文说明，最终验收另记实际收据 |
| `2026-10-05-layering-and-test-tiers` | 2026-10-05 | 当前职责对象与依赖方向、五项行为正文归属、固定历史来源和稳定引用；业务与持久合同保持不变 |
| 共享规则与可重建环境 | 2026-10-05 | 同步唯一规则归宿、固定源码补丁和平台验收合同 |
| `2026-10-05-idle-fillers-c` | 2026-10-05 | 按[让「等待」成为例外规格](../dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)阶段 1 同步：「共同身体结算与决策证据」登记 `errand_reminder_due` 之后的四个成员 `shift_start`、`shift_end`、`break_start`、`break_end`（清醒人物恰在班次或休息时段起止那一秒得到决策机会时记入，写进共同身体结算与 `decision_started` 并经同一闭集校验，同秒重合的各记一次、按枚举顺序并列；不写事件、不另存状态，按场景与时钟重新求值，已判定未决策的原因随人物状态恢复；不在睡眠允许的原因之内，睡过的不补），`errand_reminder_due` 的位置改写为排在 `plan_reminder_due` 之后；「事件正文与资源证据」`environment_event` 行补起止不写事件、只记于唤醒原因；「调试工具」补四个唤醒原因的中文标签；文件头增该规格修订说明；版本表去掉隔开末两行的空行；日期改写，版本号不变 |
| 环境归属与等待行为整合 | 2026-10-06 | 保留已实施的班次唤醒、系统层与零点计划文本；现行场景结构 6 与显式数据仓输入，完整日核验实际工件，历史两份证明不改；跨平台方案仍未实施 |
| `2026-10-06-windows-path-safety` | 2026-10-06 | 「恢复行为」补人物工作区快照与恢复的成员规则：快照不进入符号链接与目录联接，快照与恢复按同一组检查核对成员；恢复先核对全部成员再展开，名字须能在任何平台上原样保存、互不重名，写入时遇到别名拒绝，归档损坏同样拒绝 |
| `2026-10-06-rrs-rules` | 2026-10-07 | 阶段 1：固定规则与 RULES_VERSION、七份环境目录、真实环境命令和五字段运行身份；旧品类合同归档，后续消费者按阶段闭合 |
| `2026-10-06-rrs-map` | 2026-10-07 | 随机连通加权图、最短距离出行与环境恢复身份；对应正文按资源与规则引擎简化规格地图阶段修订 |
| `2026-10-06-rrs-app` | 2026-10-07 | app 内容接触的 `data.content` 与 `played[]`、`app_push` 环境事件、公开 `app_content` 结果；阶段 4 唤醒词表仍为 25 项，工作事件留到阶段 5；版本保持 v1.6 |
| `2026-10-06-rrs-work` | 2026-10-07 | 同步工作主题证据与质量报告，保留特性版本 v1.6 |
| `2026-10-06-rrs-record` | 2026-10-07 | 阶段 7：导出清单 3、五个工件、app 使用事实与最终查看器标签；版本保持 v1.6 |
| `2026-10-07-repository-ownership` | 2026-10-07 | 按用户裁决收敛目录与职责：WorldMock 保留模拟、记录、完整认证足迹与评估；下游加工、模型任务、训练和规则数据归 LabelKit-DataSet，源码快照已接收，新消费验收另记实际证据 |
| `2026-10-07-acs-rules` | 2026-10-07 | 固定规则版本 2；目录格式 2、场景结构 8、世界结构 11、运行清单 4；环境仅发布目录与场景，规则只从包读取。 |
| `2026-10-07-acs-receipt` | 2026-10-07 | 付款与收货分别留证，收货由唯一函数恰好结算一次，效果与证据归收货人；个人事务七键，用餐目标按本人 meal 收货推进。 |
| `2026-10-08-acs-action` | 2026-10-08 | 统一动作执行、内容历史、推送、社交及用户补订的现行合同 |
| `2026-10-07-acs-close` | 2026-10-07 | 动作与内容简化收口：场景 8、世界 11、运行清单 4、导出 4、质量 6；统一动作、内容、收货与 app 足迹合同。 |
