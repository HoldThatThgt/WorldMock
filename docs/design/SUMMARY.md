<a name="summary-worldmock-设计文档集-创建总结"></a>
# WorldMock 设计文档集 — 创建总结

> **日期**：2026-10-05
> **状态**：设计草稿已创建；资源与身体模型相关章节已修订，文档集整体尚未冻结


<a name="summary-共享规则与可重建环境修订"></a>
## 共享规则与可重建环境修订

按[共享规则与可重建环境规格](../dev/SPEC-shared-rules-and-rebuildable-env.md)修订；验收结果独立记录。

共享规则与可重建环境的设计同步分布在总体架构、交换、资源、模拟、记录、交付、评估、配置与格式章节；当前特性行为由同名冻结规格统一约束。

<a name="summary-工具交互设计同步"></a>
## 工具交互设计同步

[工具交互规格](../dev/SPEC-life-runtime.md#tool-interaction-工具请求)是本次最终特性合同；逐项活动、品类分页查询、完整错误、
类别供给、冻结范围价、通知到达与工作开放已经同步各设计职责。
本次设计冻结不表示生产功能已实现。各章节具体修改和行为/变异验收责任见该规格文件清单。
发布身份目标为场景4、地图3、目录5.0、品类与规则绑定3；世界状态沿用8。


<a name="summary-个人事务与工作安排设计同步"></a>
## 个人事务与工作安排设计同步

[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)是本次特性合同。设计章节不在冻结时预写，随各阶段的代码同步：
阶段 1 运行修复（就寝反思严格工具、只读查询转发、计划重复意图定位、异常日志），阶段 2 个人事务与提示信息分工，
阶段 3 作息补全与工作安排，阶段 4 查询筛选与返回精简；逐章作业见[实施计划](../archive/errands-workday/errands-workday-implementation-plan.md)，
每章改了什么见该章版本表，各章文件头注明该规格修订的状态。
发布身份为世界状态 9、质量报告 4（阶段 2），场景 5、卡片 3 与 `card-v3`（阶段 3）；
当时地图 3、目录 5.3、品类与规则绑定 3、导出清单 2、运行清单 2 不变。
当前目录按[共享规则与可重建环境规格](../dev/SPEC-shared-rules-and-rebuildable-env.md)发布为 5.4；其它上述格式版本不变。
该规格已实现；逐条证据、源码变异审查与门禁结果见[个人事务与工作安排验收记录](../archive/errands-workday/errands-workday-verification.md)，
实现状态见[实现状态](../dev/implementation-status.md#implementation-status-worldmock-实现状态)。证据是离线测试；规格阶段 6 的配对全天复跑是计费的真实运行，
只在用户要求时启动，未执行。


<a name="summary-完成状态"></a>
## 完成状态

<a name="summary-已创建的文档"></a>
### ✅ 已创建的文档

| 文档 | 大小 | 状态 | 主要内容 |
| --- | --- | --- | --- |
| [README.md](README.md#readme-worldmock-设计文档集) | 5.4K | ✅ | 文档集入口、阅读路径、与主文档集的关系 |
| [00-overview.md](00-overview.md#overview-worldmock-产品设计说明书-概述) | 12K | ✅ | 背景目标、术语、需求映射、背书总表、对齐决策 |
| [01-architecture.md](01-architecture.md#architecture-worldmock-产品设计说明书-总体架构) | 14K | ✅ | 五层架构、19 模块清单、数据流、CLI 命令 |
| [02-exchange-engine.md](02-exchange-engine.md#exchange-engine-worldmock-产品设计说明书-交换引擎) | — | DRAFT | 维护交换规则、查询适用规则、校验交换条件、计算投入产出、校验资源分录与返回规则结果 |
| [03-resource-engine.md](03-resource-engine.md#resource-engine-worldmock-产品设计说明书-资源引擎) | — | DRAFT | 资源定义、内容与语料、目录发布、检索和提供 |
| [04-simulation-engine.md](04-simulation-engine.md#simulation-engine-worldmock-产品设计说明书-模拟引擎) | — | DRAFT | 初始化、观察与动作受理、时间与活动推进、结算、唤醒和恢复编排 |
| [05-recording-engine.md](05-recording-engine.md#recording-engine-worldmock-产品设计说明书-记录引擎) | — | 已整合 | 事件、素材、事务、检查点、分支、导出、质量与诊断；承接冻结约束 |
| [06-delivery.md](06-delivery.md#delivery-worldmock-产品设计说明书-数据交付) | 29K | ✅ | LabelKit 接口、证据窗口、多任务编排 |
| [07-evaluation.md](07-evaluation.md#evaluation-worldmock-产品设计说明书-行为评估) | 23K | ✅ | 行为指标、对比协议、盲审流程、结论边界 |
| [08-configuration.md](08-configuration.md#configuration-worldmock-产品设计说明书-配置规格) | 22K | ✅ | 场景文件、目录规则、语料配置、凭证管理 |
| [09-io-formats.md](09-io-formats.md#io-formats-worldmock-产品设计说明书-输入输出格式) | 26K | ✅ | 输入输出格式、Schema 定义、来源标记 |
| **范围** | — | **DRAFT** | **文档创建状态不代表设计冻结或实现验收** |

<a name="summary-资源与身体模型修订"></a>
## 资源与身体模型修订

身体相关资源与金钱统一称为资源；资源的变化、计量与校验规则按类别展开。
详细规则来自 [LifeEnv 运行时规格](../dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则)，
领域接口来自[接口合同](../CONTRACTS.md#contracts-worldmock-接口合同)，记录与证据约束统一见[记录引擎](05-recording-engine.md#recording-engine-worldmock-产品设计说明书-记录引擎)。

```mermaid
flowchart LR
    OVERVIEW[概述：统一资源术语] --> RESOURCE[资源引擎：定义、内容与语料]
    RESOURCE --> EXCHANGE[交换引擎：规则维护、条件、投入产出与资源约束]
    RESOURCE --> CONFIG[配置：版本引用与初始参数]
    RESOURCE --> SIMULATION[模拟引擎：活动、身体变化与实际结算]
    EXCHANGE --> SIMULATION
    SIMULATION --> TRACE[记录引擎：资源变化证据]
    TRACE --> DELIVERY[交付：按可见性保留体感事实]
```

身体模型不再使用四项百分制状态或固定点数恢复，不根据困意直接强制睡眠，也不以能量阻断或停止活动。
金钱双边转账、能量与娱乐需要流水、睡眠模型演化、共享时钟推进分别核验。
食品利用效率与睡眠不足的关联保留为仿真假设。

<a name="summary-文档特点"></a>
## 文档特点

<a name="summary-参考-labelkit-的组织方式"></a>
### 1. 参考 LabelKit 的组织方式

从 `Project/LabelKit/docs/design/` 学习的格式：

<a name="summary-已采用的元素"></a>
#### ✅ 已采用的元素
- 封面与元信息（版本、日期、状态、目标读者）
- 修订历史表
- 术语与缩写表
- 设计原则
- 需求映射表（需求 → 承载章节）
- 算法与工程背书总表（功能 → 学术/工业背书）
- 已对齐的设计决策（多方案对比）
- 模块详细设计（职责、边界、算法、配置）
- 核心数据结构与 API
- 丰富的图表（Mermaid）与表格

<a name="summary-适配-worldmock-的差异"></a>
#### 🔄 适配 WorldMock 的差异
- **格式**：Markdown 而非 HTML（版本控制友好）
- **组织**：模块化多文件而非单一大文件（便于维护）
- **文档分工**：主文档集定义架构与接口，特性规格定义行为，本目录按模块解释
- **引用不重复**：记录引擎合同在本模块设计唯一维护，其余接口引用 CONTRACTS.md

<a name="summary-文档结构"></a>
### 2. 文档结构

```
docs/design/
├── README.md                    # 入口：文档清单与阅读路径
├── 00-overview.md              # 概述：背景、目标、术语、需求映射
├── 01-architecture.md          # 总体架构：系统定位、模块清单、数据流
├── 02-exchange-engine.md           # 交换引擎：规则维护与查询、条件校验、投入产出、分录校验与结果
├── 03-resource-engine.md     # 资源引擎：资源定义、内容、语料与提供
├── 04-simulation-engine.md    # 模拟引擎：初始化、观察、动作、时间、活动、结算、唤醒与恢复
├── 05-recording-engine.md      # 记录引擎：事件、存储、恢复、导出与诊断
├── 06-delivery.md              # 数据交付：LabelKit、证据窗口、编排
├── 07-evaluation.md            # 行为评估：指标、对比、盲审、结论
├── 08-configuration.md         # 配置规格：场景、语料、凭证与规则入口
├── 09-io-formats.md            # 输入输出：Schema、字段、来源标记
└── SUMMARY.md                  # 本文件：创建总结
```

<a name="summary-与主文档集的关系"></a>
### 3. 与主文档集的关系

| 文档集 | 定位 | 关键问题 | 典型文档 |
| --- | --- | --- | --- |
| **主文档集** (`docs/`) | 架构与合同 | 为什么这样设计？接口是什么？ | ARCHITECTURE.md、CONTRACTS.md、EXPERIMENT.md |
| **特性规格** (`docs/dev/`) | 行为与算法 | 如何结算、推进与验证？ | SPEC-life-runtime.md、SPEC-labelkit-delivery.md |
| **设计文档集** (`docs/design/`) | 模块说明 | 如何理解和组合这些规则？ | 02-exchange-engine.md、05-recording-engine.md、08-configuration.md |

**协同关系**：
- 主文档集定义**接口契约**，特性规格定义**行为规则**，设计文档集描述**模块流程**
- 记录引擎集中维护接口与行为，其余设计文档引用对应合同
- 设计文档包含**算法伪代码**、**配置示例**、**数据流图**

<a name="summary-文档内容亮点"></a>
## 文档内容亮点

<a name="summary-overviewmd"></a>
### 00-overview.md
- 问题定义与解决方案
- 10 个核心设计原则（人物自主性、确定性结算、唯一写入者等）
- 15 条需求映射（需求 → 承载章节）
- 10 个功能点的学术/工业背书
- 10 个已对齐的设计决策（多方案对比）

<a name="summary-architecturemd"></a>
### 01-architecture.md
- 五层分层架构图（CLI → Runtime → Agent/Env → Life/Footprints → Storage）
- 19 个模块清单与状态
- 三阶段端到端数据流（准备 → 运行 → 交付）
- 12 个 CLI 命令与执行顺序
- 核心数据结构（WorldState、AgentState、BodyState 等）
- v1.0 → v2.0 → v3.0 演进路线

<a name="summary-exchange-enginemd"></a>
### 02-exchange-engine.md
- 维护注册表中的交换规则，说明资源、对象与语料增删改的衔接
- 查询适用规则，校验当前交换条件
- 计算投入产出，校验资源分录
- 返回规则结果，由模拟引擎落实实际变化
- 当前规则调用入口、实现差距与验收条件

<a name="summary-resource-enginemd"></a>
### 03-resource-engine.md
- 统一可计量资源、内容条目和来源语料的设计归属
- 资源单位与边界、来源分类、目录发布和语料文件身份
- 检索、候选轮转、实际接触区间与素材证据
- 区分当前实现与目标，明确倒排检索尚未接入运行浏览

<a name="summary-simulation-enginemd"></a>
### 04-simulation-engine.md
- 初始化运行世界，绑定人物、场景、资源与规则
- 组织观察与决策机会，收齐并有序受理动作
- 推进共享时钟与同人活动集合，每人共同时间只结算一次；按新事实交付威胁提醒
- 与记录引擎协作保存检查点，编排恢复与实际反事实运行
- 决策不耗时，没有进行中活动或有待收尾活动的人物同一秒再决策，受同刻上限约束
- 场景本地零点由运行时后台任务生成当日计划，目标可带提醒，提醒可结束睡眠
- 人物在播报、暂停、收尾时把以后要买到、拿到或吃掉的具体东西记成个人事务：跨零点留在待办层，办成只由环境按结算证据确认并在进度层留一条记录；清醒时事务提醒精确到点、只给一次决策机会，不结束睡眠
- 地点会话随出发与到达切换，每人两个活动名额
- 人物请求为系统层与六层 JSON，每项信息只出现一次；一次决策就是一个世界请求，同一秒的连续决策续接原生消息，时间推进后思考丢弃
- 场景按人物声明工作安排，环境层每轮写明今天的上班安排 `duty`；今日计划改不了它，环境不因此拒绝请求，班内离岗由行为评估度量
- 班次与休息时段的起止给清醒的人物各一次决策机会，不结束活动、不写事件、不叫醒睡着的人
- 人物算法、资源检索、存储与离线 Dreaming 由所属模块维护


<a name="summary-recording-enginemd"></a>
### 05-recording-engine.md

- 事件、来源与可见性、素材及持久化接口
- 记忆层分列系统写的 `settled` 与人物写的 `self_notes`，从已提交事件与世界状态派生；主观记忆与反思独立留证
- 事务与状态连续性、资金对账、共同检查点与恢复分支
- 反事实来源、固定截止导出、认证与质量报告
- 状态查询、日志、诊断工具、已知差距与验收要求

<a name="summary-deliverymd"></a>
### 06-delivery.md
- LabelKit 接口规范（4 个任务类型）
- 证据窗口（时间窗口、内容窗口、记忆窗口）
- 多任务编排（训练数据 + 规则测试 + 质量报告）
- 反事实分支（replay 认证）

<a name="summary-evaluationmd"></a>
### 07-evaluation.md
- 8 个行为指标（时间分布、消费、社交、地点、身体、资源、长期、异常）
- 对比协议（synthetic vs synthetic，无人类基准）
- 盲审流程（生成 → 盲审 → 解盲 → 结论）
- 结论边界（comparative_plausibility_only）
- 行为评估报告另有工作负荷 `workload` 与作息偏离 `routine_deviation` 两个只报告项，输入是一个可选的打包对象（各人物的班次、任务的预约与要求地点、住处、覆盖后的作息窗口与播报间隔），由 `scripts/inspect_run.py` 的只读子命令 `workday --scenario <场景文件>` 产出；报告结构标识 `behavior-metrics-1` 不变

<a name="summary-configurationmd"></a>
### 08-configuration.md
- 场景配置文件（scenarios/*.json，场景结构 5；地点、商品和供给以身份引用，每个人物显式声明 `work_schedule`）
- 场景绑定目录版本与语料清单哈希，详细设计引用资源引擎
- 交换条件与投入产出引用交换引擎，活动与身体变化引用模拟引擎
- 模型凭证（~/.config/worldmock/deepseek.env，权限 0600）
- 配置验证（validate 命令、9 项校验清单）

<a name="summary-io-formatsmd"></a>
### 09-io-formats.md

- 世界与人物状态的格式说明
- 记录引擎与 LabelKit 数据格式的阅读入口
- 通用编码与验证规则


<a name="summary-地图与门店供给修订"></a>
## 地图与门店供给修订

地图特性设计将地点目录与场景初值分开、商品规格与门店供给分开，并将供给报价、库存和营业例外归入世界状态。
发现查询走既有生活动作及后轮结果，人物用实际返回的道路、营业和价格事实选择，环境在到达与购买时重验。
四个引擎及配置、输入输出、交付、评估设计均承接这些边界；记录合同定义历史比较证据与检查点恢复。

全部十二份设计文档已与 [地图与门店供给规格](../dev/SPEC-map-and-storefront.md#spec-map-and-storefront-地图数据集合与门店供给开发规格) 同步。
原一百零四份步行来源混用响应已更正，全部二百二十条路线通过身份及方式核验。
独立地图分支完整范围的行为测试、完整门禁与独立变异审查见 [验收记录](../dev/research/map-and-storefront-verification.md)。
本次与品类主线组合保留原生品类契约、库存使用与转换、交互菜单，并以供给当前报价进入品类购买算子，
按整行数量与系数取整、按所选卖方结算。组合目录为 `4.0`、品类契约与规则绑定版本为 `2`，旧分支 `3.0` 仅作历史证据。
组合已独立通过离线验收，完整门禁、购买计价审查及状态提交存活项的独立复验见
[主线组合验收](../dev/research/map-and-storefront-main-verification.md)。原分支数字保留历史身份，
不与组合测试相加；各特性通过不改变文档集其他设计的冻结状态，项目级十二周真实运行、三次隔离盲评和完整自然日交付仍未完成。

<a name="summary-时间与能量修订"></a>
## 时间与能量修订

[时间与能量规格](../dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动)冻结时，概述、总体架构、交换引擎、模拟引擎、记录引擎、数据交付、行为评估、
配置、输入输出格式、本目录入口与本文按该规格同步：决策不耗时与同刻再决策、能量不阻断活动、零点计划与计划提醒、
五个动词与并行准入、地点会话与两个活动名额、`finish` 并入 `conclude_activity`、提示不含内部身份。
资源引擎的版本事实，以及由代码或测试互锁的名称，随对应代码同步，
见[实施计划](../archive/time-and-energy/time-and-energy-implementation-plan.md)「文档同步作业」。
文档同步不等于已实现，实测结果见[实现状态](../dev/implementation-status.md#implementation-status-worldmock-实现状态)。

<a name="summary-人物-prompt-七层修订"></a>
## 人物 Prompt 七层修订

[人物 Prompt 规格](../dev/SPEC-life-realism.md#persona-prompt-七层请求)冻结时，概述、总体架构、交换引擎、资源引擎、模拟引擎、记录引擎、数据交付、行为评估、
配置、输入输出格式、本目录入口与本文按该规格同步：人物请求为系统层与工具、人设、记忆、待办、环境、进度六层 JSON，每项信息只出现一次；
删除决策自述阶段，一次决策就是一个世界请求，回复正文可有可无；同一秒的连续决策续接原生消息，时间推进后思考丢弃；
记忆层按 session 派生、零点分区，关系与偏好在人设层原地更新；`obtain`、`consume` 增 `from`、`to`，给予只转移，进食按开吃份量结算，
指名购买不受轮转限制；受限轮用完写跨决策预算耗尽事件；世界结构版本 8。
其中由代码或测试锁定的闭集、事件字段、版本与导出格式属于各代码阶段的同批作业，见[实施计划](../archive/persona-prompt/persona-prompt-implementation-plan.md)「文档同步作业」。
该规格已实现，实现后各文档按最终代码核对并注明修订部分已实现；逐条证据、语义突变审查与门禁结果见[人物 Prompt 验收记录](../archive/persona-prompt/persona-prompt-verification.md)，实现状态见[实现状态](../dev/implementation-status.md#implementation-status-worldmock-实现状态)。

<a name="summary-个人事务与工作安排修订"></a>
## 个人事务与工作安排修订

[个人事务与工作安排规格](../dev/SPEC-as2-integration.md#errands-workday-运行修复入库)分四个代码阶段实现，各设计章节、本目录入口与本文随各阶段的代码同步：
就寝反思经唯一严格工具 `submit_reflection` 提交并核验引用，回看任务经环境 actor 转发，计划重复意图逐条定位，
决策失败与工具分发失败先写带异常链的英文日志再上抛；
个人事务由三个控制请求的 `errands` 新增、更新与取消，随人物状态跨日保留并显示在待办层，办成只由结算证据确认，事务提醒不结束睡眠；
作息补下班与到家两个可空锚点，场景按人物声明 `work_schedule`，环境层 `duty` 每轮写明今天的上班安排；
`query_world` 为六键请求，增购买来源与只看此刻能做的两个筛选，结果不再逐项附候选；
世界结构 9、场景结构 5、卡片结构 3、质量报告结构 4。
该规格已实现；逐条证据、源码变异审查与门禁结果见[个人事务与工作安排验收记录](../archive/errands-workday/errands-workday-verification.md)，
实现状态见[实现状态](../dev/implementation-status.md#implementation-status-worldmock-实现状态)。证据是离线测试；规格阶段 6 的配对全天复跑是计费的真实运行，
只在用户要求时启动，未执行；项目级十二周真实运行、三次隔离盲评和完整自然日交付仍未完成。

<a name="summary-让等待成为例外修订"></a>
## 让「等待」成为例外修订

[让「等待」成为例外规格](../dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)分阶段实现，各设计章节、本目录入口与本文随各阶段的代码原位改写：
班次与休息时段的起止给清醒的人物各一次决策机会，不结束活动、不写事件、不叫醒睡着的人（阶段 1，只变代码身份，世界结构 9、质量报告结构 4、场景结构 5 不变）。
系统层告诉人物何时会再请他决策、哪些事到点会请他决策而手上的活动照常进行，作息与今日计划里的钟点只是大概：差不多到了就做，
非准点不可的事靠目标的提醒时刻（阶段 2，系统层第 7、14、15、16 条原位改写，`prompt_sha256` 与代码身份变化，`tool_schema_sha256` 与各结构版本不变）。


<a name="summary-后续维护建议"></a>
## 后续维护建议

<a name="summary-与代码同步"></a>
### 1. 与代码同步
- 接口变更时更新所属合同，记录引擎统一修改模块设计，并同步导航
- 新增功能时，补充设计文档的相应章节
- 废弃功能时，在文档中标注状态

<a name="summary-版本管理"></a>
### 2. 版本管理
- 每个设计文档携带版本号与修订历史表
- 重大设计变更时，更新版本号
- 保留历史版本记录

<a name="summary-文档评审"></a>
### 3. 文档评审
- 新增设计文档时，评审其完整性与一致性
- 定期检查文档与代码的对齐程度
- 收集开发者反馈，改进文档可读性

<a name="summary-工具支持"></a>
### 4. 工具支持
- 考虑使用 MkDocs 或类似工具生成静态站点
- 添加文档内部链接与交叉引用
- 提供 PDF 导出功能（参考 LabelKit）

<a name="summary-参考资料"></a>
## 参考资料

1. LabelKit 设计文档：`/Users/atishoo/Project/LabelKit/docs/design/`
2. WorldMock 主文档集：`docs/INDEX.md#index-worldmock-文档入口`
3. WorldMock 开发规范：`CLAUDE.md`
