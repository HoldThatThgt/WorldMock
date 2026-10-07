<a name="index-worldmock-文档入口"></a>
# WorldMock 文档入口

> 按[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订「特性规格与提案、按任务读、模块测试归属、场景手册入口」（阶段 8）；此前阶段的修订并入同一现行合同。




> 状态：文档集整体处于实现中——此前确定性基线已有验收，新特性以各自实施台账为准，三项真实验收（正式十二周运行、三次隔离盲评、LabelKit 完整自然日交付）未完成。
> 日期：2026-10-07
> 范围：文档地图、按任务阅读路径、权威层级与文件归属。本文件不定义任何接口与行为，只回答「该读哪一份」。

<a name="index-共享规则与可重建环境"></a>
## 共享规则与可重建环境

[最终开发规格](dev/SPEC-shared-rules-and-rebuildable-env.md)是本特性的唯一实施权威，
[原提案](dev/PROPOSAL_2026-10-04_SHARED_RULES_AND_REBUILDABLE_ENV.md)保留为历史分析。
[验收记录](dev/shared-rules-and-rebuildable-env-verification.md)集中登记实现、测试与变异证据。

<a name="index-环境数据归属"></a>
## 环境数据归属

[环境数据归属规格](dev/SPEC-environment-data-ownership.md)定义双仓职责；实施与最终验收仍须核对对应收据。
WorldMock-Dataset 在 `environment/shanghai-simplified/` 发布语料、目录和结构 7 场景，并维护数据制作工具。
WorldMock 安装包提供引擎和固定通用规则，通过显式场景路径加载环境；引擎仓只保留人工最小测试环境。
正式环境已发布；当前 macOS 与 Linux 正式回归均在 standard 参考层失败，正式 full 主体尚未执行，
未产生新的覆盖率测量。平台与完整日证据按[实施状态](dev/implementation-status.md#implementation-status-资源与规则引擎简化开发状态)分别核对。

```mermaid
flowchart LR
    D[WorldMock-Dataset：来源与制作工具] --> E[冻结环境：corpora、catalog、scenarios]
    E -->|显式场景路径| W[WorldMock：加载与模拟]
    P[WorldMock 安装包：固定通用规则] --> W
    W --> R[运行记录、检查点与导出]
```

<a name="index-数据加工归属"></a>
## 数据加工归属

WorldMock 负责模拟、记录、完整认证导出和真实性评估；LabelKit-DataSet 负责输入整理、LabelKit 模型任务、
训练数据加载和规则测试数据。环境素材与制作工具继续归 WorldMock-Dataset。
WorldMock 不再安装或调用 LabelKit，不提供数据加工命令或详细任务合同。

本次来源快照已交接并核对摘要，整合后的新消费验收尚未取得；状态见[实现状态](dev/implementation-status.md#implementation-status-worldmock-实现状态)。

<a name="index-这是什么"></a>
## 这是什么

WorldMock 用 AgentSociety 2（下称 AS2）驱动数字人的日常生活，把生活过程写成可追溯的数字足迹，完整导出再交给 LabelKit-DataSet 组织数据加工与数据集发布。正式实验是上海张江白领的单人物十二周（2026-01-05 至 2026-03-30）。**数字足迹**指一次生活过程留下的、可追溯的观察、决策、执行、状态变化、结果、记忆与相互关系。

文档集按职责分工：`ARCHITECTURE.md` 写分层理由，`CONTRACTS.md` 提供跨模块接口定义与导航，
`docs/dev/SPEC-*.md` 写对应特性的行为，`VERIFICATION.md` 写通过条件，`implementation-status.md` 写当前状态。
[用户配置手册](manual/README.md#readme-用户配置手册)面向使用者，说明如何编写、修改和校验当前支持的配置。
记录引擎的接口、行为与格式统一维护在[记录引擎设计](design/05-recording-engine.md#recording-engine-worldmock-产品设计说明书-记录引擎)，
其他文档引用所属合同，不再重复定义。

<a name="index-文档地图"></a>
## 文档地图

<a name="index-主文档"></a>
### 主文档

| 文档 | 职责 | 状态 |
| --- | --- | --- |
| `docs/INDEX.md#index-worldmock-文档入口` | 本文件：文档地图、阅读路径、权威层级与文件归属 | 入口 |
| `docs/ARCHITECTURE.md#architecture-worldmock-架构设计` | 系统分层、职责边界、数据流与核心决策的理由 | FROZEN |
| `docs/CONTRACTS.md#contracts-worldmock-接口合同` | 跨模块接口合同与导航；记录引擎定义引用所属设计，其余接口在本文件维护；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步；阶段 7 的导出清单 3、app 使用事实 与诊断组件正文已同步 | FROZEN |
| `docs/EXPERIMENT.md#experiment-worldmock-实验设置` | 实验身份：时间窗与日历情境、依赖冻结、运行绑定、场景参数、内容与语料绑定与外部参照；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | FROZEN |
| `docs/VERIFICATION.md#verification-worldmock-验收标准` | 放行门禁（最终门禁、协议门禁、工程门禁、报告项）、交付阶段与通过条件、测试矩阵、验收原则与结论边界；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步；阶段 7 的内容、出行、质量与足迹导出通过条件已同步，执行状态按实际回执登记 | NOT-COMPLETE |
| `docs/dev/implementation-status.md#implementation-status-worldmock-实现状态` | 当前实现状态：确定性范围与测试基线实测、覆盖率口径、规格追溯与突变守卫、未完成项与阻塞 | NOT-COMPLETE |
| `docs/dev/coverage-baseline.md#coverage-baseline-覆盖率门禁口径与基线` | 覆盖率门禁的判定口径、测量与排除范围、失败关闭约定、历史基线及本次正式门禁阻断 | 现行口径 |
| `docs/dev/coverage-report.json` | 门禁生成物：分支覆盖率、未覆盖公共接口与逐文件分项，由 `scripts/coverage_gate.py` 写入 | 生成物 |
| `docs/dev/test-durations.json` | 统一测试入口生成的节点、层次、阶段与文件耗时，标准墙钟取相同内容和执行条件的实际标准层回执 | 生成物 |

<a name="index-用户配置手册"></a>
### 用户配置手册

从[创建自己的第一个场景](manual/README.md#readme-创建自己的第一个场景)开始，再按修改目标阅读对应页面。
手册提供使用步骤和示例，接口与行为定义仍引用所属合同和规格。

| 页面 | 使用目标 |
| --- | --- |
| [手册入口](manual/README.md#readme-用户配置手册) | 文件分类、一天自定义场景、加载检查与运行 |
| [人物画像](manual/personas.md#personas-人物画像) | 编写背景故事、作息、关系与人物初值，预览和校验卡片 |
| [生活场景](manual/scenarios.md#scenarios-生活场景) | 自定义日期、人数、账户、菜单、工作主题分配、消息及预排事件 |
| [世界素材](manual/resources.md#resources-定义地点门店供给与世界素材) | 准备地点、图、店铺、食品、app 与内容及生活活动、联系人邮件语料 |
| [规则配置](manual/rules.md#rules-调整世界规则与人物动机参数) | 调整人物身体初值和画像映射，理解引擎固定类型、身体及交换规则 |
| [运行配置](manual/runtime.md#runtime-运行配置) | 模型凭据、新建运行、恢复和反事实干预文件 |
| [真实性评估](manual/evaluation.md#evaluation-自定义评估与盲评输入) | 评估配置、人工参照和评审输入 |

<a name="index-特性规格与提案"></a>
### 特性规格与提案

工具交互相关规格已同步2026-10-01目标设计，新增条款已实现，使用本次独立验收证据；下表旧特性的“已实现”只描述此前源码身份。

分层与测试层次的当前开发规格是 [冻结规格](dev/SPEC-layering-and-test-tiers.md#layering-outcome)，全部十五个契约主题由
`tests/fixtures/layering-acceptance.json` 登记到实际测试。本次全量门禁与独立源码变异仍在执行，原有增量归档不替代本次验收。


| 文档 | 职责 | 状态 |
| --- | --- | --- |
| [工具交互规格](archive/tool-interaction/SPEC-tool-interaction.md) | 历史增量规格；现行行为正文见 `docs/dev/SPEC-life-runtime.md#tool-interaction-工具请求`，提示与工具说明见 `docs/CONTRACTS.md#contracts-worldmock-接口合同`，验收登记由 `tests/fixtures/acceptance_requirements.json` 与相应 Python 登记维护；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 归档 |
| [工具交互验收记录](archive/tool-interaction/tool-interaction-verification.md) | 工具交互规格的发布身份、实际生产实现、行为测试与独立源码变异结果 | 归档 |
| [未完成任务、人物行为约束与运行修复提案](archive/errands-workday/PROPOSAL_2026-10-02_TASK_MEMORY_AND_RUNTIME_FIXES.md) | 白领与商务两个人物全天运行证据（计数见 `docs/archive/errands-workday/2026-10-02-dayrun-review-evidence.json`）、业界研究、待办与作息方案、运行期间修复的入库建议及过时预检审查；已收敛为冻结规格 `docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库`，本文只保留设计背景、运行证据与取舍依据 | 归档 |
| [代码分层、文档测试解耦与测试分层提案](dev/PROPOSAL_2026-10-04_LAYERING_AND_TEST_TIERS.md) | 2026-10-04 可维护性评估第 1–3 项（按 `94595ca` 实测）：完整导入图（含函数内导入）的 17 处回边、无环守卫与 `LifeEngine` 拆分；现行文档的行号引用改为符号引用、突变锚点改为符号作用域、源码文本钉改为结构检查、已验收特性的增量规格合并后归档；按实测耗时把测试分成快速、标准与全量三层；不改变行为 | 未冻结提案 |
| [共享规则单一归宿与可重建环境提案](dev/PROPOSAL_2026-10-04_SHARED_RULES_AND_REBUILDABLE_ENV.md) | 2026-10-04 历史分析（按 `94595ca` 实测）；已收敛为共享规则与可重建环境冻结规格，原机器状态不作为当前实现事实 | 未冻结提案 |
| [让「等待」成为例外提案](dev/PROPOSAL_2026-10-05_IDLE_FILLERS.md) | 2026-10-04 五人共享世界运行的 43 次等待（按 `9cca694` 核对）：多数等的是到点没有决策机会的钟点，其余等的事到点会请人物决策而人物不知道；A 系统层第 7、14、15、16 条原位改写，B 零点计划模板第三、五、六条改写，C 班次与休息时段起止的四个唤醒原因；列出定稿文本、被改写的现行条款、指纹与完整日基线的影响、验收台账与变异清单、配对复跑指标，已收敛为冻结规格[让「等待」成为例外规格](dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)，本文只保留运行证据、原因链与取舍依据 | 已裁决 |
| [让「等待」成为例外规格](dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格) | 系统层第 7、14、15、16 条原位改写、零点计划模板第三、五、六条改写与班次和休息时段起止的四个唤醒原因 `shift_start`、`shift_end`、`break_start`、`break_end`；裁决记录、各阶段的现行文档改写、测试与夹具、验收台账与变异登记（`tests/fixtures/idle-fillers-acceptance.json`、`tests/idle_fillers_acceptance.py`）、门禁与平台、配对复跑指标；阶段 1（班次与休息时段起止的唤醒）、阶段 2（系统层第 7、14、15、16 条）与阶段 3（零点计划模板第三、五、六条）已实现；旧第三份完整日夹具计划未执行，由现行结构 6 工件核验替代；门禁与配对复跑按各自实际收据判定；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；阶段 5 的最终 22 项唤醒原因与 7 类预排事件正文已同步；系统层第 7 条与提示指纹仍归阶段 6，不用阶段 5 收据提前证明；阶段 7 的当前唤醒与真实工作累计验收登记已同步；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 1、3、4、6、7 提交实现 | FROZEN |
| [完整日行为基线跨平台一致提案](dev/PROPOSAL_2026-10-05_CROSS_PLATFORM_DAY_BASELINE.md) | 完整日用例在 Windows 上对 macOS 生成的现行夹具不符（按 `0184c97` 在 Windows 上实测，按 `b83292f` 与 macOS 导出逐条比较）：四处原因已全部定位并经确认实验复现——工作区快照归档次序、工作区文本文件换行、`AGENT.json` 路径分隔符，以及 macOS 数学库在一个难舍入参数上不正确舍入的 `exp`（进入 `sleep_pressure` 与 `sleepiness` 的末位）；前三处的修复在 POSIX 上是空操作，数学库须改用标准库 `decimal` 的正确舍入实现，提议在 Windows 与 macOS 上用同一份当前结构 6 外部输入生成新证明，现存两份历史夹具与签名实现保持原字节；方案未实施 | 未冻结提案 |
| [环境数据归属规格](dev/SPEC-environment-data-ownership.md) | 正式环境与制作工具归数据仓；通用规则归引擎包；场景结构 6、环境标题与活动效果、显式环境加载及人工测试夹具；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 1、7 提交实现 | FROZEN |
| [以 app 为入口的资源与规则引擎简化提案](dev/PROPOSAL_2026-10-06_RESOURCE_AND_RULE_SIMPLIFICATION.md) | 用户需求、运行证据与设计背景；全部推荐及最终裁决已收敛为资源与规则引擎简化冻结规格 | 已裁决 |
| [以动作和内容组织资源与固定规则的简化提案](dev/PROPOSAL_2026-10-07_APP_ACTION_RESOURCE_AND_RULE_SIMPLIFICATION.md) | 用户原话、心智模型与设计背景；六个补充选择与全部补定已收敛为动作与内容简化冻结规格 | 已裁决 |
| [动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格) | 系统动作只留出行、睡眠、购买，等待是时间原语；库存与进食删除，收货即结算；其余行为由数据集以 app、动作、内容登记，一个执行器按内容时长执行并按历史选内容；导航、购物、支付 app 由人物点名；app 使用足迹合同（六种事实、收货行与七条不变量）；目录格式 2、场景结构 8、世界结构 11；实施计划为阶段 0 冻结、阶段 1–6 一个集成波次、阶段 7 数据仓交接，各阶段的文件清单与设计文档改写清单已对照代码基线核定；条款随集成波次合入生效，此前现行行为仍以所属合同为准 | FROZEN |
| [资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格) | 固定规则、目录、图、食品、app 内容推送、主题工作、当前工具和认证足迹；阶段 1–8 修订条款已实现，macOS 人工完整日双跑一致并冻结基线；阶段 9 正式环境已发布，macOS 与 Linux 首次正式回归失败保留，来源职责与依赖探针修复及新门禁按实际收据登记，完整发布与平台结论按实际收据判断；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 1、2、3、4、5、6、7 提交实现 | FROZEN |
| [正式环境验收记录](dev/resource-rule-formal-verification.md#resource-rule-formal-verification-正式环境验收记录) | 已发布 Dataset 与实际引擎身份、确定性完整日和来源投影、首次正式失败、回归修复与新收据及逐平台边界 | 验收记录 |
| [正式回归修复验收](dev/resource-rule-formal-repair.md#resource-rule-formal-repair-正式回归修复验收) | 测试来源、保存恢复的同源身份、实际 SQLite 与字体能力，以及最新正式 macOS/Linux 全量收据 | 现行记录 |
| [同时做两件事规格](dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格) | GitHub issue #4 的正式修复：暂停的活动不占两个并行名额、恢复要有空名额、同刻受限轮不再按名额分流（甲）；只有同一种动作互斥，占用名改为每种动作自己的名字，改的是引擎包资源 `src/worldmock/data/affordances.toml`，仍带旧占用名的已发布环境须按新占用名重新发布（乙）；系统层第 8 条原位改写（丙）；裁决记录、各阶段的现行文档改写、随实现一起改的测试、验收、门禁与平台、配对复跑指标与不在本规格内的待裁决事项；条款随阶段 1–3 提交逐阶段实现，完整日基线在 POSIX 平台另行生成；阶段 1（暂停的活动不占名额）、阶段 2（只有同一种动作互斥）与阶段 3（系统层第 8 条）已实现；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；阶段 4 的八项占用与驾车仅可并行交谈正文已同步；阶段 5 的恢复检查改为地点、主题上限、名额、占用；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 1、3、4、6 提交实现 | FROZEN |
| [共享规则与可重建环境规格](dev/SPEC-shared-rules-and-rebuildable-env.md#shared-rules-特性规格共享规则单一归宿与可重建环境) | 共享规则、固定源码、引导和平台要求；与分层与测试层次规格共同使用唯一 full runner；「安全文件」一行 2026-10-06 补 Windows 路径加固；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；阶段 4 的 app 固定时长、五类固定缓解率与三类候选正文已同步；阶段 5 的规范摘要与安全路径正文已改为工作主题累计；当前 macOS 证据与尚未执行的 Windows 回执分开登记；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 1、3、4、7 提交实现 | FROZEN |
| [共享规则与可重建环境验收记录](dev/shared-rules-and-rebuildable-env-verification.md) | 合并前具体被测身份的实现、完整门禁、平台重建和独立源码变异证据；不证明新主线合并后的代码 | 验收记录 |
| [个人事务与工作安排规格](archive/errands-workday/SPEC-errands-workday.md) | 历史增量规格；现行行为正文见 `docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库`，提示与工具说明见 `docs/CONTRACTS.md#contracts-worldmock-接口合同`，验收登记由 `tests/fixtures/acceptance_requirements.json` 与相应 Python 登记维护；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 归档 |
| [个人事务与工作安排验收记录](archive/errands-workday/errands-workday-verification.md) | 源码身份与发布身份（各阶段提交、结构版本与两个指纹）、实施过程与各阶段测试结果、评审与修复、两轮独立源码变异审查、全量门禁与覆盖率（`a7e2fdc` 上的运行，以及含两个查看器提交的分支头 `964de50` 上的最终运行）、验收台账逐行登记（证据节点来自 `tests/errands_workday_acceptance.py`）、代码与规格的出入与裁定（含 `a7e2fdc` 的全量门禁之后由 `1346e0d`、`6c81126` 补齐的查看器事务显示）、文档一致性、环境敏感项；证据层级为离线测试，配对全天复跑（阶段 6，只报告）与 strict 端点探针未执行 | 归档 |
| [个人事务与工作安排实施计划](archive/errands-workday/errands-workday-implementation-plan.md) | 个人事务与工作安排实施计划：实施阶段与门禁之外的逐文档逐条款的文档同步作业、逐阶段的文件修改清单与被钉点；随规格冻结后作为实施台账维护 | 归档 |
| [个人事务与工作安排提案设计评审记录](archive/errands-workday/2026-10-02-errands-workday-design-review.md) | 七路源码映射、十路现行规格与设计文档扫描、六个视角评审与逐条复核的结论，以及草稿规格对每条结论的处置 | 归档 |
| [工具交互定稿方案](archive/tool-interaction/PROPOSAL_2026-09-30_TOOL_INTERACTION.md) | 并发源码核对与最终取舍；行为引用工具交互规格 | 归档 |
| `docs/dev/SPEC-as2-integration.md#spec-as2-integration-特性规格agentsociety-2-集成与改动` | 选用 AS2 的理由与复用边界、分层职责，以及 AS2 核心链路为本次场景必须发生的确定性改动；「AS2 基线与分支」补登一日运行保真规格的三个 LifeAgent 覆写点，修订条款已实现；覆写点清单补登 `LifeAgent.step` 的异常日志、记忆运行时的严格反思与 `LifeEnvActor`、`LifeEnvProxy` 的只读查询转发，控制请求的请求级 `errands` 与 `query_world` 六键的覆写边界由个人事务与工作安排规格修订，修订条款已实现；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款；阶段 5 正文已同步为 app 推送职责、工作主题累计与无独立工作评审，实际通过以阶段 5 收据为准；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 2 提交实现 | FROZEN |
| `docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则` | LifeEnv 的状态归属与生命周期、共享时钟与并发活动、身体语义与体感分级、食品消费与需求模型、语料准入与内容提供、场景与身体规则、观察投影与提示合同、候选契约与呈现规则、社交最小可行版；困意档界、社交与娱乐口径、睡眠中不读消息、名称类拒绝、按名调用、候选资格与轮转、可调用索引与购买渠道由一日运行保真规格修订，修订条款已实现；唤醒原因增 `errand_reminder_due`、待办层与环境层投影、场景的 `work_schedule`、决策异常日志、任务回看的执行位置与 `query_world` 六键由个人事务与工作安排规格修订，修订条款已实现；唤醒闭集追加班次与休息时段起止的四个成员、下一边界与下一次决策机会增这些时刻（阶段 1）由[让「等待」成为例外规格](dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)修订，修订条款已实现；零点计划修订一节的写法（阶段 3）由同一规格修订，修订条款已实现；活动名额只算进行中与待收尾、恢复要有空名额、受限轮不再按名额分流（阶段 1）与占用声明改为只有同一种动作互斥（阶段 2）由[同时做两件事规格](dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)修订，修订条款已实现；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；食品与画像的阶段三正文已同步，执行结果按实际验收登记；阶段 4 的 app 选择、全局内容进度、会话推送与八项占用正文已同步；阶段 5 的工作主题累计、最终 22 项唤醒与 7 类预排事件正文已同步，提示规则第 7、8、12、13、17 条归阶段 6；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 1、2、3、4、7 提交实现 | FROZEN |
| [查看器对象名称](dev/SPEC-run-viewer-names.md#spec-run-viewer-names-运行查看器对象名称) | 地点、活动及内容名称的运行内关联、缺失提示与验证；工作、通话与交谈的活动标题由一日运行保真规格补充，修订条款已实现；个人事务名称的来源、控制请求须带 `errands` 的现行形状、请求摘要写事务变更、事务事件与事务提醒写事务名称与商品名称由个人事务与工作安排规格修订，商品名称的补缺来源（`errand_item_bindings`）、事务变更的逐条读取规则与个人事务的缺失提示按查看器实现的独立核对补充，修订条款已实现（查看器的请求摘要与事务名称由 `1346e0d`、`6c81126` 实现）；四个新唤醒原因的中文说法由[让「等待」成为例外规格](dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)修订（阶段 1），修订条款已实现；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；阶段 4 的 app 使用标题与「app 推送」显示正文已同步；阶段 7 的当前 app、内容、工作主题、出行名称与最终导出验证正文已同步；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 5 提交实现 | FROZEN |
| [查看器名称验收](dev/run-viewer-names-verification.md) | 真实运行、回归测试与独立语义变异闭合 | 验收记录 |
| [查看器上下文解析](dev/SPEC-run-viewer-context.md#spec-run-viewer-context-运行查看器上下文解析) | 模型请求的选择与关联、输入结构与原文、相邻调用对比与验证边界；按 `layer` 分段与续接识别由人物 Prompt 七层规格修订，修订条款已实现；待办层的个人事务、进度层的两种事务记录与环境层 `duty` 的增长与退出由个人事务与工作安排规格修订，查看器的待办层、环境层、进度层规则文字与工具定义条目已同步（进度层规则文字与工具定义条目由 `1346e0d` 补齐，夹具与补测为 `6c81126`），修订条款已实现；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；阶段 7 的 22 项唤醒、11 项原生工具和最终标签验证正文已同步；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 5 提交实现 | FROZEN |
| [查看器上下文验收](dev/run-viewer-context-verification.md) | 要求到证据、实际运行验证、测试命令与语义变异审查 | 验收记录 |
| [一日运行行为设计](dev/PROPOSAL-dayrun-behavior.md) | 等待、通勤、进食、记忆和持续活动的事实核查与采纳依据；其采纳的「不预设等待越少越好」已由[让「等待」成为例外规格](dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)取代 | 已裁决 |
| [一日行为特性规格](dev/SPEC-dayrun-behavior.md#spec-dayrun-behavior-一日行为可信性特性开发规格) | 并行生命周期、共同身体区间、餐食、app 内容接触、已核验经历与主观记忆、预警交付；到店指名购买的口径由一日运行保真规格修订，修订条款已实现；就寝反思的严格工具、主观文字不改变世界事实一句、控制请求携带的个人事务变更、事务提醒与 `query_world` 六键由个人事务与工作安排规格修订，修订条款已实现；下一边界与必要唤醒增班次与休息时段起止（阶段 1）由[让「等待」成为例外规格](dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)修订，修订条款已实现；等待与内容接触补系统层写给人物的过法（阶段 2）由同一规格修订，修订条款已实现；占用名与拒绝的组合（阶段 2）由[同时做两件事规格](dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)修订，修订条款已实现；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；食品与画像的阶段三正文已同步，执行结果按实际验收登记；阶段 4 的 app 内抽取、八项占用与内容完成边界正文已同步，旧真实音频来源正文已删除；阶段 7 的已核验 app 内容经历和 app 使用五类离线事实正文已同步；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 2、3、5 提交实现 | FROZEN |
| [一日行为实施台账](dev/research/dayrun-behavior/verification.md) | 文件修改责任、逐条生产与测试映射、完整自然日及独立审查证据 | 实施台账 |
| [一日运行修复核查与验收](dev/research/dayrun-reconciliation/verification.md) | dayrun 清单到主线修复、回归与独立突变证据 | 验收记录 |
| [一日运行保真修复方案](archive/dayrun-fidelity/PROPOSAL-dayrun-fidelity.md) | `4a5b0e6` 白领与商务全天运行暴露的系统问题：睡眠计费、下限缓解与注入警告、困意档界、守作息规则、卡片作息、按名调用与指名购买、App 与门店、错误合同、呈现排序、控制工具改形、中文摘录、输出上限、联系人回复、strict 端点，另含不适脉冲与睡眠中读消息、浏览与提示的可达性、英文与内部名称外泄、当日目标推进、场景加载校验、工作材料正文（范围外），共 G1–G22；数据集侧见 WorldMock-Dataset 的 `PROPOSAL_2026-09-29_DATASET_FIXES.md`；已收敛为冻结规格 `docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要`，本文只保留设计背景、运行证据与取舍依据；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 归档 |
| [一日运行保真特性规格](archive/dayrun-fidelity/SPEC-dayrun-fidelity.md) | 历史增量规格；现行行为正文见 `docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要`，提示与工具说明见 `docs/CONTRACTS.md#contracts-worldmock-接口合同`，验收登记由 `tests/fixtures/acceptance_requirements.json` 与相应 Python 登记维护；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 归档 |
| [一日运行保真验收记录](archive/dayrun-fidelity/dayrun-fidelity-verification.md) | 一日运行保真验收记录：源码身份与提交、评审与修复、全量门禁与覆盖率、验收台账逐行登记（证据节点来自 `tests/dayrun_fidelity_acceptance.py`）、三路独立语义突变审查、strict 端点探针结论、代码与规格的出入、文档一致性与锚点核对；真实复验（阶段 7）未执行 | 归档 |
| `docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像` | 人物的两阶段决策调用、记忆写入与巩固、动机层、当日目标、世界素材品类准入、素材可达性与呈现轮转、候选与呈现、画像编译、商品数量合同、运行呈现与导出投影；两阶段决策改为单一行动阶段，卡片与记忆的提示呈现改为人设层与记忆层，由人物 Prompt 七层规格修订，修订条款已实现；候选决胜键与轮转、按名调用、画像编译（场景作息优先、覆盖率运行只警告）与时间线标题由一日运行保真规格修订，修订条款已实现；就寝反思改经严格工具 `submit_reflection`、个人事务与控制请求的请求级 `errands`、画像作息增下班与到家锚点、`query_world` 六键由个人事务与工作安排规格修订，修订条款已实现；工作安排效力的唤醒一条（阶段 1）由[让「等待」成为例外规格](dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)修订，修订条款已实现；系统层的作息、饥饿、困意与决策机会（阶段 2）由同一规格修订，修订条款已实现；零点计划的写法与提醒（阶段 3）由同一规格修订，修订条款已实现；待办层恢复的不可用原因增名额占满（阶段 1）由[同时做两件事规格](dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)修订，修订条款已实现；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；食品与画像的阶段三正文已同步，执行结果按实际验收登记；阶段 4 的 app 工具、全局内容进度与按 app 绑定的内容可达性正文已同步；阶段 5 的工作主题、目标工时、实际累计与工作完成进度正文已同步；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步；阶段 7 的时间线、app 内容状态与工作主题累计导出正文已同步；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 1、2、3、4、5、7 提交实现 | FROZEN |
| `docs/dev/SPEC-day-plan-goals.md#spec-day-plan-goals-当日目标特性开发规格` | 当日目标、实际推进证据、修订、环境威胁、三层质量报告与完整验收；生成方式、提醒与部分节律由时间与能量规格修订；目标大致时刻 `around_time`、推进口径与例行意图由一日运行保真规格修订，修订条款已实现；重复意图的逐条定位、零点计划输入与质量报告结构 4 由个人事务与工作安排规格修订，修订条款已实现；事件驱动的决策节律增班次与休息时段起止（阶段 1）由[让「等待」成为例外规格](dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)修订，修订条款已实现；钟点只是大概（阶段 2）由同一规格修订，修订条款已实现；零点计划的写法与提醒（阶段 3）由同一规格修订，修订条款已实现；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；阶段 4 的 `use_app → entertainment` 度量映射正文已同步；阶段 5 的零点计划工作主题输入、`{action}` 阶段身份与无关门威胁正文已同步；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 1、2、3、5 提交实现 | FROZEN |
| `docs/dev/PROPOSAL-day-plan-goals.md` | 目标与时间职责分离的裁决依据，现行行为引用最终规格 | 已裁决 |
| `docs/dev/research/day-plan-semantics.md` | 当日目标学科调研与核实限制 | 调研证据 |
| `docs/dev/research/day-plan-goals-verification.md` | 独立 worktree 基线、逐要求验证、实际修改清单与独立变异证据 | 验收记录 |
| `docs/dev/research/day-plan-goals-main-verification.md` | 当日目标与 main 新增手册的组合验收、依赖发布与源码身份 | 验收记录 |
| `docs/dev/research/day-plan-goals-bob-domain.md` | 当日目标领域、节律与午夜结算的独立源码突变证据 | 审查记录 |
| `docs/dev/research/day-plan-goals-bob-interface.md` | 当日目标工具、观察与叙述的独立源码突变证据 | 审查记录 |
| `docs/dev/research/day-plan-goals-bob-metrics.md` | 三层统计及导出首次审查，保留三个初次存活结论 | 审查记录 |
| `docs/dev/research/day-plan-goals-bob-metrics-recheck.md` | 三个存活补丁在新增行为断言后的原样复验 | 审查记录 |
| `docs/dev/SPEC-decision-context.md#spec-decision-context-特性规格决策上下文与工具结果` | 决策自述、冻结上下文、动作登记与结果通道、场景信息、预算、修改清单与验收台账；决策自述与场景信息由人物 Prompt 七层规格取代（决策就是所选世界请求，场景信息改为七层归属），冻结上下文、预算、修改清单与验收台账由其修订，登记与结果通道约束保留，修订条款已实现；按名调用、指名购买口径与公开结果键由一日运行保真规格修订，修订条款已实现；任务回看的执行位置、同秒续接 `changes` 与 `events` 的口径、公开结果 `details.errands` 与 `query_world` 六键由个人事务与工作安排规格修订，修订条款已实现；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；食品与画像的阶段三正文已同步，阶段 5 的结果通道已删除 `work_review` 与 `task_deadline` 特例，执行结果按实际验收登记；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 2、4 提交实现 | FROZEN |
| `docs/dev/SPEC-interaction-menu.md#spec-interaction-menu-特性规格交互菜单` | 地点类与人物交互菜单、工具和活动类型准入、冻结观察、模型与环境共同门控、运行身份及验收台账；按名调用与控制工具的参数形状由一日运行保真规格修订，修订条款已实现；控制工具的请求级 `errands` 与 `query_world` 六键由个人事务与工作安排规格修订，修订条款已实现；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；食品与画像的阶段三正文已同步，执行结果按实际验收登记；阶段 4 的 `use_app` 与店铺 app 来源正文已同步；阶段 5 的工作主题上限准入正文已同步；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 1、3、4、7 提交实现 | FROZEN |
| `docs/dev/PROPOSAL-agent-interaction.md#proposal-agent-interaction-数字人交互方案` | **已裁决并冻结**：数字人交互的原始问题、心智模型逐条对账与取舍记录；行为定义已由 `docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-特性规格数字人交互会话层上下文分层与稳定工具面` 承载 | FROZEN |
| `docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-特性规格数字人交互会话层上下文分层与稳定工具面` | **FROZEN**：三级结构（当日目标、地点会话、活动）、七层上下文与进度层、稳定工具面、决策轮循环与播报节律、前置条件前置、活动名额、午夜浓缩；显式修订七条既有冻结条款并随冻结生效；工具面、会话与记忆写入点由时间与能量规格修订；七层上下文与进度层由人物 Prompt 七层规格取代（进度层只含当前 session），修订条款已实现；控制工具的参数形状、按名调用、名称类拒绝与公开结果键由一日运行保真规格修订，修订条款已实现；控制工具的请求级 `errands`、待办层的个人事务与新增唤醒成员 `errand_reminder_due` 的同步、环境层 `duty` 与 `query_world` 六键由个人事务与工作安排规格修订，修订条款已实现；触发表增四个班次边界成员、新增唤醒成员的同步点清单改以冻结条款修订第 1 行为准由[让「等待」成为例外规格](dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)修订（阶段 1），修订条款已实现；名额只算进行中与待收尾、暂停让出名额与受限轮清单（阶段 1）由[同时做两件事规格](dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)修订，修订条款已实现；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；食品与画像的阶段三正文已同步，执行结果按实际验收登记；阶段 4 的 app 目标、内容、推送与全局内容进度正文已同步；阶段 5 的十一项原生工具、八项稳定面、工作主题四键合同与 `last_report` 正文已同步；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 3、4 提交实现 | FROZEN |
| `docs/dev/research/agent-interaction-document-impact.md` | 文档影响与文件修改清单：爆炸半径实测、五处词表同步点、导出重建约束、冻结规格与设计文档修订清单 | 调研证据 |
| `docs/archive/time-and-energy/SPEC-time-and-energy.md` | 历史增量规格；现行行为正文见 `docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动`，提示与工具说明见 `docs/CONTRACTS.md#contracts-worldmock-接口合同`，验收登记由 `tests/fixtures/acceptance_requirements.json` 与相应 Python 登记维护；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 归档 |
| `docs/archive/time-and-energy/time-and-energy-implementation-plan.md` | 时间与能量实施计划：实施阶段与门禁、逐文档逐条款的文档同步作业、文件修改清单与工程约束；随规格冻结后作为实施台账维护 | 归档 |
| `docs/archive/time-and-energy/time-and-energy-verification.md` | 时间与能量验收记录：源码身份与各阶段评审修复、验收台账逐行登记、全量门禁与覆盖率、VF4 语义突变、无新增故障、文档一致性与锚点核对；真实复验（阶段 8）本机未执行，记录执行步骤与对照基线 | 归档 |
| `docs/archive/persona-prompt/PROPOSAL-decision-context.md` | 人物 Prompt 七层与交互修复方案：七层 JSON 与唯一出现原则、已确认的交互语义、统一修复的程序错误、需修订的现行条款；已收敛为冻结规格 `docs/dev/SPEC-life-realism.md#persona-prompt-七层请求`，本文只保留设计背景与取舍依据；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 归档 |
| `docs/archive/persona-prompt/SPEC-persona-prompt.md` | 历史增量规格；现行行为正文见 `docs/dev/SPEC-life-realism.md#persona-prompt-七层请求`，提示与工具说明见 `docs/CONTRACTS.md#contracts-worldmock-接口合同`，验收登记由 `tests/fixtures/acceptance_requirements.json` 与相应 Python 登记维护；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 归档 |
| `docs/archive/persona-prompt/persona-prompt-implementation-plan.md` | 人物 Prompt 七层实施计划：实施阶段与门禁、逐文档逐条款的文档同步作业、文件修改清单与工程约束；规格冻结后作为实施台账维护 | 归档 |
| `docs/archive/persona-prompt/persona-prompt-verification.md` | 人物 Prompt 七层验收记录：源码身份与提交、评审与修复、验收台账逐行登记（证据节点来自 `tests/persona_prompt_acceptance.py`）、独立语义突变审查、全量门禁与覆盖率、与规格的出入、文档一致性与锚点核对；真实复验（阶段 7）未执行 | 归档 |
| `docs/archive/persona-prompt/persona-prompt-seven-layers.md` | 七层字段、差距的代码依据与字段取值依据；配套七份 Schema、完整示例与交互图示 | 归档 |
| `docs/dev/research/white-collar-dayrun-root-cause-2026-09-28.md` | 白领全天模拟重复下单、给予、进食与强制等待的运行证据与根因分析 | 调研证据 |
| `docs/dev/research/decision-context-proposal-2026-09-20.md` | 决策上下文 2026-09-20 已裁决的心智模型与取舍记录原文；行为与开发验收由 `docs/dev/SPEC-decision-context.md#spec-decision-context-特性规格决策上下文与工具结果` 承载 | 归档 |
| `docs/dev/PROPOSAL-interaction-menu.md` | 交互菜单的原始问题、方案比较与取舍记录；行为与验收由 `docs/dev/SPEC-interaction-menu.md#spec-interaction-menu-特性规格交互菜单` 承载 | 调研证据 |
| `docs/archive/category-contract/SPEC-category-contract.md#spec-category-contract-品类契约与交换规则开发规格` | 已退役的历史品类树、配对与环境规则合同；原文解释历史证明，现行规则由资源与规则引擎简化取代 | 归档 |
| `docs/archive/category-contract/category-contract-verification.md` | 品类契约逐要求实施、测试与独立突变证据 | 归档 |
| `docs/archive/category-contract/category-contract-main-verification.md` | 合入决策上下文主分支、模型库存路径补修、全仓门禁与独立复验 | 归档 |
| `docs/archive/category-contract/category-contract-files.md` | 对照开发基线逐文件核对真实 Git diff 与功能责任 | 归档 |
| `docs/archive/category-contract/category-contract-bob-review.md` | 独立语义突变、原始证据及修复复审结果 | 归档 |
| `docs/archive/category-contract/PROPOSAL-category-contract.md` | 品类契约提案裁决记录；规范已收敛到对应 SPEC | 归档 |
| `docs/dev/SPEC-map-and-storefront.md#spec-map-and-storefront-地图数据集合与门店供给开发规格` | 发布图唯一来源、地点发现、同类型食品菜单、固定价格、店铺 app 来源、单订单付款与配送、食品持有、恢复和完整验收；挑店可交给系统（`prefer`）、`branch_label`、配送路线只警告与浏览结果完整标签由一日运行保真规格修订，修订条款已实现；`query_world` 六键、来源与可执行筛选由个人事务与工作安排规格修订，修订条款已实现；冻结输入与身份里仍带旧占用名的已发布环境在加载时被拒（阶段 2）由[同时做两件事规格](dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)修订，修订条款已实现；由[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)修订对应条款，修订条款已实现；食品与画像的阶段三正文已同步，执行结果按实际验收登记；由[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)修订对应条款，随阶段 1、2、4、7 提交实现 | FROZEN |
| `docs/dev/PROPOSAL-map-and-storefront.md` | 已裁决的地图与门店供给心智模型、采纳机制和审查基线差距；行为以开发规格为准 | 调研证据 |
| `docs/dev/ANALYSIS-map-model.md` | 原始问题与已纠正的历史判断，连接现行开发规格和研究证据 | 调研证据 |
| `docs/dev/research/map-and-storefront-feasibility.md` | 商品与供给、路线方式、营业、AS2 复用和许可的一手资料核验 | 调研证据 |
| `docs/dev/research/map-and-storefront-document-impact.md` | 地图与门店供给的要求、文档、测试影响及审查基线 | 调研证据 |
| `docs/dev/research/map-and-storefront-verification.md` | 独立地图分支源码身份、要求到行为测试、全量门禁、独立变异和补测复验；保留原始收据 | 验收记录 |
| `docs/dev/research/map-and-storefront-main-verification.md` | 地图与品类主线组合的固定源码、完整工程门禁、计价及状态提交审查、原存活突变闭合和数据准入 | 验收记录 |
| `docs/dev/ANALYSIS-spec-coverage.md#analysis-spec-coverage-规格测试用例覆盖率追溯矩阵` | 规格条款与测试用例的覆盖率追溯矩阵 | 现行追溯矩阵 |
| `docs/dev/research/decision-context-verification.md` | 决策上下文完整修改清单、条款到测试、独立审查与工程门禁证据 | 验收记录 |
| `docs/dev/research/decision-context-bob-protocol.md` | 决策协议与预算的隔离源码突变、补充验证与恢复证据 | 验收记录 |
| `docs/dev/research/decision-context-bob-projection.md` | 结果投影、场景、事件与恢复入口的隔离源码突变及恢复证据 | 验收记录 |
| `docs/dev/research/interaction-menu-feasibility.md` | 动态工具、场景可供性、服务端重验与运行身份的业界机制核查及采纳边界 | 调研证据 |
| `docs/dev/research/interaction-menu-verification.md` | 交互菜单修改清单、条款到测试、固定源码门禁与 Uncle Bob 突变证据 | 验收记录 |
| `docs/dev/research/interaction-menu-main-verification.md` | 交互菜单与品类、库存及决策上下文的主线合并、冲突裁决和完整工程门禁 | 验收记录 |
| `docs/dev/research/interaction-menu-bob-environment.md` | 菜单声明、交集、观察与环境执行的隔离源码突变、补强复验及恢复证据 | 验收记录 |
| `docs/dev/research/interaction-menu-bob-host.md` | 人物工具与参数、恢复身份和提示字段的隔离源码突变、补强复验及恢复证据 | 验收记录 |

<a name="index-汇编版设计说明书"></a>
### 汇编版设计说明书

[汇编版设计说明书](design/worldmock-design.html)是 `docs/design/` 十章（概述、总体架构、交换引擎、资源引擎、模拟引擎、记录引擎、数据交付、
行为评估、配置规格、输入输出格式）加附录 A（从 `docs/CONTRACTS.md#contracts-worldmock-接口合同` 逐字摘录的人物工具、请求与拒绝码、七层输入、请求组成与生成参数、模型身份、命令行十五节）
汇编成的单文件，由 `scripts/build_design_doc.py` 生成，状态为 `生成物`：想一次看完整个系统的现行设计与工具接口先读它；
它不另立权威，接口与字段仍看 `docs/CONTRACTS.md#contracts-worldmock-接口合同`，行为规则仍看对应 `docs/dev/SPEC-*.md`。章节改动后必须重新汇编并一并提交，
`scripts/build_design_doc.py --check` 是工程门禁的一部分。

<a name="index-资源引擎设计"></a>
### 资源引擎设计

[资源引擎设计](design/03-resource-engine.md#resource-engine-worldmock-产品设计说明书-资源引擎)统一承载资源定义、内容与语料、目录发布、检索和提供，
状态为 DRAFT；交换条件与投入产出引用交换引擎，实际变化与结算引用模拟引擎，接口和行为仍遵循冻结合同及规格。

<a name="index-模拟引擎设计"></a>
### 模拟引擎设计

[模拟引擎设计](design/04-simulation-engine.md#simulation-engine-worldmock-产品设计说明书-模拟引擎)承载运行初始化、观察与决策机会、动作受理、共享时钟、
活动推进、结算、唤醒和恢复编排，状态为 DRAFT；人物算法、资源提供、记录保存和离线交付引用所属模块。

<a name="index-交换引擎设计"></a>
### 交换引擎设计

[交换引擎设计](design/02-exchange-engine.md#exchange-engine-worldmock-产品设计说明书-交换引擎)统一承载维护交换规则、查询适用规则、校验交换条件、计算投入产出、
校验资源分录与返回规则结果，状态为 DRAFT。接口与行为仍以对应冻结合同和规格为准.

<a name="index-记录引擎设计"></a>
### 记录引擎设计

[记录引擎设计](design/05-recording-engine.md#recording-engine-worldmock-产品设计说明书-记录引擎)统一承载事件、素材、事务、检查点、恢复与反事实分支、
导出、质量报告和运行诊断，承接原已冻结的接口与行为约束；待实现设计与已知差距在文内单列。

<a name="index-调研证据"></a>
### 调研证据

| 文档 | 职责 | 状态 |
| --- | --- | --- |
| `docs/dev/research/category-contract-feasibility.md` | 品类契约的第一方业界依据、排除理由与实施前代码差距及验证目标 | 调研证据 |
| `docs/dev/research/as2-feasibility.md` | AS2 接入可行性与核心改动独立审查 | 调研证据 |
| `docs/dev/research/decision-context-feasibility.md` | 原生工具配对、动态工具、批量同步结算与记忆查询的官方依据和采纳边界 | 调研证据 |
| `docs/dev/research/bob-mutation-plan.md` | 核心合同语义突变计划与历史突变台账 | 调研证据 |
| `docs/dev/research/code-refute-2026-09-17.md` | 编码模块对抗审计：确认的缺陷、被驳倒的发现与未完成复核的缺口 | 调研证据 |
| `docs/dev/research/delivery-final-audit.md` | 足迹与数据交付的最终独立审查与缺口修复复核 | 调研证据 |
| `docs/dev/research/life-data-feasibility.md` | LifeEnv、真实内容与 LabelKit 交付可行性审查 | 调研证据 |
| `docs/dev/research/life-final-audit.md` | LifeEnv 最终规格审计（修复前快照，不描述当前状态） | 调研证据 |
| `docs/dev/research/realism-evaluation.md` | 十二周数字足迹真实性评估合同：指标、可比性与结论边界 | 调研证据 |
| `docs/dev/research/realism-root-cause-2026-09-17.md` | 人物行为「不像人」的根因分析，七视角综合报告 | 调研证据 |
| `docs/dev/research/exogenous-incidents.md` | 非脚本化外生事件的生成机制与可复现随机性：三种中介方式、十个系统的选择器与复现手段、可迁移结论与引证核实记录 | 调研证据 |
| `docs/dev/research/dayrun-toolbox-design-audit.md` | 一日双人运行的表达层审计：结算侧自洽、表达侧把内部记账口径直接端给模型；含对前份评估五处判断的修正 | 调研证据 |
| `docs/dev/research/dayrun-two-agent-realism-and-friction.md` | 一日双人运行的真实性与交互摩擦评估：独白与事件脱钩、交互面未建立、工具返回错误率的原始结论 | 调研证据 |

<a name="index-按任务读"></a>
## 按任务读

| 你想做的事 | 从这里读 | 接着读 |
| --- | --- | --- |
| 一份文件看完整个系统的现行设计与工具接口 | [汇编版设计说明书](design/worldmock-design.html)（`scripts/build_design_doc.py` 生成；附录 A 为接口合同摘录）；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 合同其余各节在 `docs/CONTRACTS.md#contracts-worldmock-接口合同`，行为细节跳到对应 `docs/dev/SPEC-*.md`，章节源在 `docs/design/`；阶段 7 的记录、交付和评估章节已同步 |
| 编写自己的配置、自定义人物与生活环境 | [用户配置手册](manual/README.md#readme-用户配置手册) | 对应手册页的示例、字段说明与校验命令 |
| 了解系统怎么分层、为什么这样设计 | `docs/ARCHITECTURE.md#architecture-worldmock-架构设计` | 对应 `docs/dev/SPEC-*.md` 看具体行为 |
| 实现或修改某个特性的行为 | `docs/dev/SPEC-*.md` 中对应那一份；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | `docs/CONTRACTS.md#contracts-worldmock-接口合同` 取接口，`docs/VERIFICATION.md#verification-worldmock-验收标准` 取门禁 |
| 找某个接口、字段、枚举、目录结构、命令行参数或提示词模板 | `docs/CONTRACTS.md#contracts-worldmock-接口合同`；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 设计理由在 `docs/ARCHITECTURE.md#architecture-worldmock-架构设计` 与对应 SPEC |
| 知道现在跑到哪一步、已有哪些实测证据 | `docs/dev/implementation-status.md#implementation-status-worldmock-实现状态` | `docs/dev/coverage-baseline.md#coverage-baseline-覆盖率门禁口径与基线` 与 `docs/dev/coverage-report.json` |
| 判断能不能放行、本次要交哪些证据 | `docs/VERIFICATION.md#verification-worldmock-验收标准`；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | `docs/dev/implementation-status.md#implementation-status-worldmock-实现状态` 取源码身份与命令收据 |
| 复现实验：依赖版本、场景参数与时间窗 | `docs/EXPERIMENT.md#experiment-worldmock-实验设置`；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | `docs/CONTRACTS.md#contracts-worldmock-接口合同` 取运行接口与目录结构 |
| 理解地图、门店供给与交易 | `docs/dev/SPEC-map-and-storefront.md#spec-map-and-storefront-地图数据集合与门店供给开发规格`；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | `docs/CONTRACTS.md#contracts-worldmock-接口合同` 取字段，各引擎设计取定义、判断、结算和记录；研究核验说明路线与许可边界 |
| 配置或修改地点类与人物交互菜单 | `docs/dev/SPEC-interaction-menu.md#spec-interaction-menu-特性规格交互菜单`；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | `docs/design/08-configuration.md#configuration-worldmock-产品设计说明书-配置规格` 取配置，`docs/design/04-simulation-engine.md#simulation-engine-worldmock-产品设计说明书-模拟引擎` 取执行链路 |
| 查历史调研、审计与根因结论 | `docs/dev/research/` | 对应 SPEC 看结论如何落地为条款 |
| 了解数字人交互的设计依据与取舍 | `docs/dev/PROPOSAL-agent-interaction.md#proposal-agent-interaction-数字人交互方案`；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 已裁决：行为定义看 `docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-特性规格数字人交互会话层上下文分层与稳定工具面`；差距审计引用 `docs/CONTRACTS.md#contracts-worldmock-接口合同` 与各 SPEC 取证 |
| 审查时间与能量（已实现）：同刻决策、能量不阻断、零点计划与提醒、五个动词、地点会话与活动名额 | `docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动`；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 「冻结条款修订」取被改写的既有冻结条款，`docs/archive/time-and-energy/time-and-energy-implementation-plan.md` 取实施阶段、文档同步作业与文件修改清单，`docs/archive/time-and-energy/time-and-energy-verification.md` 取验收证据 |
| 实现或审查数字人交互（会话、上下文分层、稳定工具面） | `docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-特性规格数字人交互会话层上下文分层与稳定工具面`；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | `docs/dev/research/agent-interaction-document-impact.md` 取文件清单与爆炸半径，`docs/CONTRACTS.md#contracts-worldmock-接口合同` 取字段与 Schema；「上下文分层」已被人物 Prompt 七层规格取代，见 `docs/dev/SPEC-life-realism.md#persona-prompt-七层请求` |
| 实现或审查人物 Prompt 七层（已实现）：七层请求、同秒续接、记忆层、来源与去向、指名购买 | `docs/dev/SPEC-life-realism.md#persona-prompt-七层请求`；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 「裁决记录」取已定裁决，「冻结条款修订」取被改写的冻结条款，`docs/archive/persona-prompt/persona-prompt-implementation-plan.md`（实施台账）取阶段、文档同步作业与文件修改清单，`docs/VERIFICATION.md#verification-worldmock-验收标准`「人物 Prompt 七层特性门禁」取门禁，`docs/archive/persona-prompt/persona-prompt-verification.md` 取验收证据；设计依据见已裁决的 `docs/archive/persona-prompt/PROPOSAL-decision-context.md` |
| 设计突发事件类世界事件（事故、疾病、升迁、丧亲） | `docs/dev/research/exogenous-incidents.md`；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | `docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像` 取现有行为边界，`docs/design/05-recording-engine.md#recording-engine-worldmock-产品设计说明书-记录引擎` 取事件闭集，`docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则` 取唤醒闭集 |
| 查看资源、内容与语料的统一设计 | `docs/design/03-resource-engine.md#resource-engine-worldmock-产品设计说明书-资源引擎` | 资源定义、来源、版本、内容提供与实现差距 |
| 查看模拟引擎如何组织并推进实际运行 | `docs/design/04-simulation-engine.md#simulation-engine-worldmock-产品设计说明书-模拟引擎`；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 初始化、观察与动作受理、时钟与活动、结算、唤醒和恢复编排 |
| 查看交换引擎设计与尚未闭合的实现差距 | `docs/design/02-exchange-engine.md#exchange-engine-worldmock-产品设计说明书-交换引擎` | 当前实现与验收 |
| 查看记录引擎的接口、行为与实现差距 | `docs/design/05-recording-engine.md#recording-engine-worldmock-产品设计说明书-记录引擎` | 同一文档内的存储、恢复、导出、诊断与验收章节 |
| 实现或审查一日运行保真（已冻结、已实现）：睡眠与社交账、下限缓解与输入警告、困意档界、系统层守作息、按名调用与指名购买（含请求级 `prefer` 与方式「购买」）、错误合同、呈现与可调用索引、控制工具改形、截断与联系人守卫、加载校验与目标大致时刻 | `docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要`；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 「裁决记录」取已定裁决，3.5「收敛」取购买请求的定稿，「冻结条款修订」取被改写的冻结条款，「文档同步作业」「文件修改清单」（实施台账）取各阶段作业与影响面，`docs/VERIFICATION.md#verification-worldmock-验收标准`「一日运行保真特性门禁」取门禁，`docs/dev/ANALYSIS-spec-coverage.md#analysis-spec-coverage-规格测试用例覆盖率追溯矩阵`「一日运行保真增量映射」取验收台账逐行到测试的追溯，`docs/archive/dayrun-fidelity/dayrun-fidelity-verification.md` 取验收证据（源码身份、门禁、突变审查与 strict 端点探针）；设计依据与运行证据见已裁决的 `docs/archive/dayrun-fidelity/PROPOSAL-dayrun-fidelity.md`，数据集侧见 WorldMock-Dataset 的 `PROPOSAL_2026-09-29_DATASET_FIXES.md` 与 `RUN_REPORT_2026-09-29.md` |
| 实现或审查个人事务与工作安排（已冻结、已实现）：运行修复入库、个人事务、提示信息分工、下班与到家作息、场景工作安排、查询筛选 | `docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库`；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 「裁决记录」取已定裁决，「冻结条款修订」取被改写的冻结条款，`docs/archive/errands-workday/errands-workday-implementation-plan.md`（实施台账）取逐文档的文档同步作业、逐阶段的文件修改清单与被钉点，规格「实施计划」「验收台账」取阶段、身份与验收，`docs/VERIFICATION.md#verification-worldmock-验收标准`「个人事务与工作安排特性门禁」取门禁，`docs/dev/ANALYSIS-spec-coverage.md#analysis-spec-coverage-规格测试用例覆盖率追溯矩阵`「个人事务与工作安排增量映射」取验收台账逐行到测试的追溯，`docs/archive/errands-workday/errands-workday-verification.md` 取验收证据（源码身份、门禁与突变审查）；评审逐条结论见 `docs/archive/errands-workday/2026-10-02-errands-workday-design-review.md`，设计背景与运行证据见已裁决的提案 |
| 实现或审查让「等待」成为例外（已冻结）：系统层第 7、14、15、16 条、零点计划模板第三、五、六条、班次与休息时段起止的唤醒 | [让「等待」成为例外规格](dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格) | 「裁决记录」取已定裁决，「现行文档原位改写」取各阶段的文档改写，「验收台账」与 `tests/fixtures/idle-fillers-acceptance.json` 取验收与变异登记，「门禁与平台」取各门禁的执行平台；运行证据与取舍依据见已裁决的提案 |
| 实现或审查同时做两件事（已冻结）：暂停的活动不占名额、只有同一种动作互斥、系统层第 8 条 | [同时做两件事规格](dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 「随实现一起改的测试」「验收」与 `docs/CONTRACTS.md#contracts-model-request` |
| 实现或审查资源与规则简化：系统固定规则、随机地图、食品、app 与主题工作 | [资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格) | 「裁决记录」「实施阶段」与「验收台账」；阶段 1–8 修订条款已实现，人工完整日已双跑一致并冻结；工程门禁结果按报告身份判断；阶段 9 正式环境已发布，首次正式回归失败保留，本轮修复与最新全量及覆盖率按实际收据判定；跨平台结论分别核对，见[实施状态](dev/implementation-status.md#implementation-status-资源与规则引擎简化开发状态) |
| 实现或审查动作与内容简化（已冻结；条款随集成波次生效）：三种系统动作与等待原语、收货即结算、数据集动作与内容、app 使用足迹合同 | [动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格) | 「裁决记录」取定稿与同提案文字的出入，「实施阶段与提交」取阶段 0–7 与各阶段拥有的文件，「现行文档原位改写」取设计文档与各合同的逐阶段改写清单，「验收台账」取契约点与行为用例；波次合入之前现行行为仍以所属合同为准 |
| 找某个文件归谁管、代码在哪 | 本文件「文件归属」 | `docs/ARCHITECTURE.md#architecture-worldmock-架构设计` 看该模块的职责边界 |

<a name="index-权威层级"></a>
## 权威层级

同一件事出现两份说法时，按下面的归属判定，不按文档新旧、不按篇幅长短。

- **用户配置步骤与示例**看 `docs/manual/README.md#readme-用户配置手册` 及其分册；手册不新增配置能力或改变所属合同。
- **记录引擎的接口、行为与格式**看 `docs/design/05-recording-engine.md#recording-engine-worldmock-产品设计说明书-记录引擎`，原冻结约束继续有效，待实现项单列。
- **其他接口、字段、枚举、Schema、目录结构、命令行参数与提示词模板**看 `docs/CONTRACTS.md#contracts-worldmock-接口合同`；代码变更先更新所属合同，再改代码。
- **其他行为规则与算法**（怎么结算、怎么推进、怎么决策）看 `docs/dev/SPEC-*.md`。
- **设计理由与职责边界**看 `docs/ARCHITECTURE.md#architecture-worldmock-架构设计`。
- **实验身份**（依赖、场景、时间窗、样本、外部参照）看 `docs/EXPERIMENT.md#experiment-worldmock-实验设置`。
- **验收**（什么算通过、门禁键、测试矩阵、证据层级、report-only 边界）看 `docs/VERIFICATION.md#verification-worldmock-验收标准`。
- **实现状态**（现在到哪一步、已有哪些证据、还剩哪些阻塞）看 `docs/dev/implementation-status.md#implementation-status-worldmock-实现状态`；它只写现状，变更过程由 Git 承载。
- **覆盖率口径与基线**看 `docs/dev/coverage-baseline.md#coverage-baseline-覆盖率门禁口径与基线` 与它写出的 `docs/dev/coverage-report.json`。
- **汇编版设计说明书**（`docs/design/worldmock-design.html`）是设计章节的生成物，不构成新的权威；与章节源不一致时以源为准并重新汇编。
- **未裁决方向**看状态为未冻结提案的文件；未冻结内容不得成为代码、测试或验收结论的依据。
- **当前行为**看基础规格所属正文。时间与能量、人物 Prompt、一日运行保真、工具交互、个人事务与工作安排的已验收行为已并回基础规格；提示与工具说明仅在 `docs/CONTRACTS.md#contracts-worldmock-接口合同` 维护权威文本。阶段、行为要求与突变语义登记由 `tests/fixtures/acceptance_requirements.json` 及对应 Python 登记维护，测试不再解析历史增量散文。
- **资源与规则简化的被修订条款**以[资源与规则引擎简化规格](dev/SPEC-resource-and-rule-simplification.md#spec-rrs-资源与规则引擎简化开发规格)的裁决和分阶段合同为准；旧规格中未被修订的约束继续有效，现行合同与设计正文随对应实现阶段同步改写。
- **动作与内容简化的被修订条款**以[动作与内容简化规格](dev/SPEC-action-and-content-simplification.md#spec-acs-动作与内容简化开发规格)的裁决与分阶段合同为准；条款随阶段 1–6 的集成波次一次生效，波次合入之前现行行为、接口与设计正文仍以所属合同与资源与规则引擎简化规格为准。
- **历史证据**看 `docs/archive/`、`docs/dev/research/` 和地图中明确登记为历史状态的记录。原文须声明完整提交：可重建执行记录使用实际源码基线；无法重建的未提交工作树保留事实，固定归档提交仅定位原文快照，不是审查执行证明。历史不成为当前行为权威。
- **当前文档范围**包含设计十章、手册、当前追溯矩阵和根 README，是否在三列表格登记不改变检查范围；DRAFT 只表示设计状态。

```mermaid
flowchart TB
    README["README.md<br/>项目自述"]
    IDX["docs/INDEX.md<br/>唯一入口"]
    MANUAL["manual/README.md<br/>用户配置手册"]
    ARCH["ARCHITECTURE.md<br/>WHY 与分层"]
    CON["CONTRACTS.md<br/>跨模块接口与导航"]
    RECORD["design/05-recording-engine.md<br/>记录引擎合同与设计"]
    EXP["EXPERIMENT.md<br/>实验身份"]
    VER["VERIFICATION.md<br/>验收与门禁"]
    IMPL["implementation-status.md<br/>当前实现状态"]
    COV["coverage-baseline.md<br/>覆盖率口径"]
    REP["coverage-report.json<br/>门禁生成物"]
    SPEC["dev/SPEC-*.md<br/>特性行为规格"]
    PROP["dev/PROPOSAL-*.md<br/>提案与裁决记录"]
    RES["dev/research/*.md<br/>调研证据"]
    ANA["dev/ANALYSIS-spec-coverage.md<br/>追溯矩阵"]

    README --> IDX
    IDX --> MANUAL
    MANUAL -->|"字段定义"| CON
    MANUAL -->|"行为约束"| SPEC
    IDX --> ARCH
    IDX --> RECORD
    CON -->|"记录引擎"| RECORD
    IDX --> CON
    IDX --> EXP
    IDX --> VER
    IDX --> IMPL
    IDX --> COV
    IDX --> SPEC
    IDX --> PROP
    IDX --> RES
    IDX --> ANA
    ARCH -->|"接口与数据表"| CON
    ARCH -->|"实现进度"| IMPL
    CON -.->|"理由与算法"| ARCH
    CON -.->|"行为规则"| SPEC
    EXP -->|"接口与目录结构"| CON
    EXP -->|"行为规则"| SPEC
    EXP -->|"验收门禁"| VER
    VER -->|"源码身份与命令收据"| IMPL
    IMPL -->|"接口口径"| CON
    IMPL -->|"验收口径"| VER
    IMPL -->|"覆盖率口径"| COV
    COV -->|"追溯矩阵"| ANA
    COV -->|"写入"| REP
    SPEC -->|"接口与字段"| CON
    SPEC -->|"证据与状态"| IMPL
    SPEC -->|"根因与依据"| RES
```

图中实线是从引用方指向被引用方，虚线是从实现指向它所依据的规范。未冻结提案刻意不画指向任何文档的边：它不构成规范。

<a name="index-文件归属"></a>
## 文件归属

<a name="index-核心系统"></a>
### 核心系统

| 路径 | 职责 |
| --- | --- |
| `custom/envs/life_env.py` | AS2 环境适配层（LifeEnv） |
| `custom/agents/life_agent.py` | AS2 人物适配层（LifeAgent） |
| `src/worldmock/life/` | 生活规则、身体、活动、社交、叙述 |
| `src/worldmock/footprints/` | 事件存储、检查点与导出 |
| `src/worldmock/profile/` | 人物卡片编译 |
| `src/worldmock/corpora/` | 生活活动、场景联系人与场景邮件语料加载校验 |
| `src/worldmock/data/` | 固定活动闭集、动机算法参数和人物映射包资源 |
| `src/worldmock/evaluation/` | 行为指标与协议门禁 |
| `src/worldmock/exchange/` | 系统固定类型、常数和食品、出行、配送、资金交换算子 |
| `src/worldmock/catalog/` | 目录声明、严格登记、加载、身份与发布图查询 |

<a name="index-测试"></a>
### 测试

| 路径 | 职责 |
| --- | --- |
| `tests/life/` | 生活规则与运行时行为 |
| `tests/footprints/` | 事件存储、检查点与恢复 |
| `tests/integration/` | 跨模块集成与协议 |
| `tests/evaluation/` | 行为指标与协议门禁 |
| `tests/exchange/` | 固定规则、常数与纯交换结果 |
| `tests/catalog/` | 人工目录登记、身份、地图生成与查询 |
| `tests/fixtures/environment/` | 引擎最小端到端的人工环境，不复制正式世界 |

<a name="index-场景与配置"></a>
### 场景与配置

| 路径 | 职责 |
| --- | --- |
| `WorldMock-Dataset/environment/shanghai-simplified/scenarios/` | 已发布结构 7 生活场景；其中 evaluation.json 仍是生活运行输入 |
| `docs/manual/` | 当前可用配置的用户编写手册与校验示例 |
| `WorldMock-Dataset/environment/shanghai-simplified/corpora/` | 已发布生活活动、联系人和邮件语料 |
| `WorldMock-Dataset/environment/shanghai-simplified/catalog/` | 地点、食品、app、内容、主题和图；规则包副本须与引擎字节一致 |

<a name="index-状态与变更约定"></a>
## 状态与变更约定

文档地图「状态」列只用下表取值，表外取值不得出现在该列：

| 状态词 | 含义 |
| --- | --- |
| `入口` | 本文件：文档集的唯一入口 |
| `FROZEN` | 条款已裁决并冻结，是当前实现合同，改动必须走版本表并说明理由 |
| `NOT-COMPLETE` | 实现或验收尚未收口，任何场合都不得把它当作完成态引用 |
| `现行口径` | 该文件是当前生效的判定口径 |
| `生成物` | 由脚本覆盖重写的产物，内容随每次运行变化 |
| `未冻结提案` | 方向或条款尚未冻结（含草稿规格及其实施计划），使用边界见「权威层级」节 |
| `已裁决` | 提案已收敛为冻结规格，只保留决策背景，不再承载实现要求 |
| `实施台账` | 逐项记录当前实现、对应测试与验收结论 |
| `审查记录` | 保留独立审查、问题修复与复审证据，不替代冻结规格 |
| `现行追溯矩阵` | 随规格条款与测试同步维护的当前映射 |
| `调研证据` | 当时快照下的裁决依据，不单独构成当前行为合同 |
| `验收记录` | 固定源码身份下的实际命令、测试结果和审查证据，不定义新行为 |
| `归档` | 原始条款与证据的固定历史快照；不作为当前行为权威 |

接口或行为变更先更新所属合同再改代码；记录引擎统一修改模块设计，其他特性修改对应合同与 SPEC，并在本文件登记阅读入口。
设计章节（`docs/design/0*.md`）或接口合同改动后运行 `scripts/build_design_doc.py` 重新汇编 `docs/design/worldmock-design.html`，与源同一提交。

<a name="index-版本表"></a>
## 版本表

| 版本 | 日期 | 修订内容 |
| --- | --- | --- |
| 1.0 | 2026-09-18 | 文档入口重写：文档地图覆盖全部文档，给出按任务阅读路径、权威层级、引用关系图与文件归属 |
| 1.1 | 2026-09-18 | 状态列词表补全为文档地图实际使用的八个取值并逐条定义，地图取值与词表对齐；位置指代改为章节标题名 |
| 1.2 | 2026-09-19；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 文档地图职责列补登两份规格新增的章：`docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则` 补「候选契约与呈现规则」，`docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像` 补「素材可达性与呈现轮转」与「候选与呈现」 |
| 1.3 | 2026-09-20 | 记录引擎的接口与行为集中维护，更新文档地图、权威归属和引用关系 |
| `2026-09-20-decision-context` | 2026-09-20 | 对齐决策上下文特性规格：统一决策自述命名，明确登记与结算结果的通道、场景信息和预算边界 |
| `2026-09-20-interaction-menu` | 2026-09-20 | 登记交互菜单冻结规格、研究与验收入口，将原提案状态改为裁决记录并补实现阅读路径 |
| `2026-09-21-exogenous-incidents` | 2026-09-21 | 登记突发事件机制调研：外生事件的选择与结算分层、可复现随机性手段及引证核实结果，供后续设计裁决引用 |
| `2026-09-22-agent-interaction` | 2026-09-22 | 登记数字人交互未冻结提案：上下文分层与稳定工具面心智模型、骨架/血肉边界，以及十七类表达层失配与门禁缺口审计 |
| `2026-09-22-agent-interaction-spec` | 2026-09-22 | 登记数字人交互特性规格（2026-09-22 提交冻结）：会话层、七层上下文与进度层、稳定工具面（7 个工具，`action` 按对象绑定）、播报节律、前置条件前置、并发上限、午夜浓缩；显式修订四条既有冻结条款；登记文档影响与文件修改清单 |
| `2026-09-24-time-and-energy` | 2026-09-24 | 登记时间与能量特性规格（未冻结）：同刻再决策、能量不阻断、跨日当日计划与提醒、五个动词、地点会话与两个活动名额、`finish` 并入 `conclude_activity`；列出对既有冻结条款的修订，随冻结生效；补实现阅读路径；「未冻结提案」同时适用于草稿规格 |
| `2026-09-24-time-and-energy-plan` | 2026-09-24 | 规格修订至 0.12 并登记实施计划（未冻结）：计划列出实施阶段与门禁、文档同步作业与文件修改清单；登记行改称零点计划；阅读路径改为审查入口并指向实施计划；「未冻结提案」同时适用于草稿规格的实施计划 |
| `2026-09-24-time-and-energy-freeze` | 2026-09-24 | 冻结时间与能量特性规格：用户指示按规格实现，裁决 #19–#23、#25 按推荐定稿，规格状态改 `FROZEN`，实施计划改 `实施台账`；数字人交互规格一行不写工具个数，改为修订七条冻结条款、三级结构与活动名额，并注明由时间与能量规格修订；当日目标规格一行注明生成方式、提醒与部分节律被修订、待实现；文档地图中词表外的状态值改为词表取值（`FROZEN`、`验收记录`、`审查记录`）；突发事件阅读入口的事件闭集改指记录引擎设计，唤醒闭集改指 life-runtime 规格 |
| `2026-09-26-time-and-energy-verification` | 2026-09-26 | 时间与能量实现后文档（IX5）：文档地图登记本特性验收记录（`验收记录`）；「按任务读」时间与能量一行改为已实现并补验收记录入口；当日目标规格一行删去冻结时加入的「，修订条款待实现」 |
| `2026-09-27-persona-prompt-spec` | 2026-09-27 | 登记人物 Prompt 七层特性规格与实施计划（均为未冻结提案）并补审查阅读路径；提案一行的状态由词表外的「审阅稿」改为 `未冻结提案`，并指向草稿规格；「权威层级」注明草稿规格路径 |
| `2026-09-27-persona-prompt-freeze` | 2026-09-27；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 冻结人物 Prompt 七层特性规格：用户指示实施本方案、不延后任何待实现项，裁决 #21–#38 按推荐定稿，规格状态改 `FROZEN`，实施计划改 `实施台账`，提案改 `已裁决`；`docs/dev/SPEC-life-realism.md#spec-life-realism-特性规格人物决策与画像`、`docs/dev/SPEC-decision-context.md#spec-decision-context-特性规格决策上下文与工具结果`、`docs/dev/SPEC-agent-interaction.md#spec-agent-interaction-特性规格数字人交互会话层上下文分层与稳定工具面` 与 `docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动` 各行注明被该规格取代或修订的部分、修订条款待实现；「按任务读」数字人交互一行注明「上下文分层」被取代，人物 Prompt 一行改为实现与审查入口并指向实施台账与验收门禁；「权威层级」改为提案已裁决、实施依据为该规格；补登既有漂移：查看器上下文解析规格（`FROZEN`）及其验收记录（`验收记录`） |
| `2026-09-28-persona-prompt-verification` | 2026-09-28 | 人物 Prompt 七层实现后文档（人物 Prompt 台账 IX8）：文档地图登记本特性验收记录 `docs/archive/persona-prompt/persona-prompt-verification.md`（`验收记录`）；查看器上下文解析、人物规格、决策上下文规格、数字人交互规格、时间与能量规格与人物 Prompt 规格六行的「修订条款待实现」改为已实现；「按任务读」人物 Prompt 一行改为已实现并补验收记录入口；文件头日期改写 |
| `2026-09-29-dayrun-fidelity-spec` | 2026-09-29；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 登记一日运行保真特性规格 `docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要`（未冻结提案）并补审查阅读路径；提案一行的状态由词表外的「提案，待裁决」改为 `未冻结提案`，职责列补齐 G1–G22；「权威层级」注明草稿规格路径；文件头日期改写 |
| `2026-09-29-design-aio` | 2026-09-29 | 登记汇编版设计说明书 `docs/design/worldmock-design.html`（`scripts/build_design_doc.py` 生成，状态 `生成物`）：文档地图新增小节、「按任务读」首行、「权威层级」注明生成物不另立权威、「状态与变更约定」补重新汇编的要求；附录 A 从接口合同摘录人物工具、模型请求与命令行 |
| `2026-09-29-dayrun-fidelity-freeze` | 2026-09-29 | 冻结一日运行保真特性规格（IX1 的冻结部分）：用户指示实施该规格、不延后任何尚未完成的工作并采纳全部推荐，「裁决记录」D2–D7、D9–D11、D14–D17、D19、S1、S3–S10 按推荐定稿（S10 采纳），规格状态改 `FROZEN`，文档同步作业与文件修改清单留在规格内作实施台账；提案改 `已裁决` 并注明已收敛为冻结规格；AS2 集成、life-runtime、查看器对象名称、一日行为、人物规格、当日目标、决策上下文、交互菜单、数字人交互、时间与能量、人物 Prompt、地图与门店十二行注明被该规格修订的部分，并注明这些修订当时尚未落地；「按任务读」一日运行保真一行改为实现与审查入口并指向 3.5「收敛」、实施台账与验收门禁；「权威层级」改为提案已裁决、实施依据为该规格，【同】作业落地前接口合同与设计文档的对应条款仍为 `4a5b0e6` 文本 |
| `2026-09-29-dayrun-fidelity-implemented` | 2026-09-29；阶段 6 的剩余系统规则、最终提示身份与查看器正文已同步 | 一日运行保真实现后文档（IX1 的【后】部分，按 `e377669` 核对）：文档地图中 AS2 集成、life-runtime、查看器对象名称、一日行为、人物规格、当日目标、决策上下文、交互菜单、数字人交互、时间与能量、人物 Prompt、地图与门店十二行冻结时注明的修订改为已实现，规格一行改为条款已全部实现；「按任务读」一日运行保真一行改为已实现并补 `docs/dev/ANALYSIS-spec-coverage.md#analysis-spec-coverage-规格测试用例覆盖率追溯矩阵`「一日运行保真增量映射」；「权威层级」改为【同】作业已随代码落地、`docs/CONTRACTS.md#contracts-worldmock-接口合同` 与 `docs/design/` 描述现行实现 |
| `2026-09-29-dayrun-fidelity-verification` | 2026-09-29 | 一日运行保真实现后文档（IX1 的验收记录入口）：文档地图登记本特性验收记录 `docs/archive/dayrun-fidelity/dayrun-fidelity-verification.md`（`验收记录`）；「按任务读」一日运行保真一行补验收记录入口 |
| 工具交互定稿 | 2026-10-01 | 登记最终规格、定稿提案与设计同步；目标功能已实现 |
| `2026-10-02-errands-workday-spec` | 2026-10-02 | 登记 2026-10-02 提案（状态由词表外的「待裁决」改为 `未冻结提案`，职责列注明已收敛为草稿规格）、草稿规格 `docs/dev/SPEC-as2-integration.md#errands-workday-运行修复入库` 及其实施计划 `docs/archive/errands-workday/errands-workday-implementation-plan.md`（均为 `未冻结提案`）与设计评审记录（`调研证据`），补审查阅读路径；「权威层级」的未裁决方向写明按日期命名的提案路径与当前未冻结的规格，并注明工作树里的运行修复代码在冻结前不得入库；顺带更正既有漂移：工具交互规格一行的状态由词表外的「FROZEN，已实现」改为 `FROZEN`（已实现写入职责列），补登此前未登记的工具交互验收记录 `docs/archive/tool-interaction/tool-interaction-verification.md`（`验收记录`）；文件头日期改写 |
| `2026-10-02-errands-workday-freeze` | 2026-10-02 | 冻结个人事务与工作安排规格（IX1 的冻结部分）：用户指示实施该规格、不延后任何尚未完成的工作并采纳全部推荐，「裁决记录」二十二项全部按推荐定稿（含标 † 的 R1、E1、E4、W1、Q2），规格状态改 `FROZEN`，配套实施计划改 `实施台账`（逐文档同步作业与文件修改清单在实施计划内）；提案改 `已裁决` 并注明已收敛为冻结规格；工具交互、AS2 集成、life-runtime、查看器对象名称、查看器上下文、一日行为、一日运行保真、人物规格、当日目标、决策上下文、交互菜单、LabelKit 交付、数字人交互、时间与能量、人物 Prompt、地图与门店供给十六行注明被该规格修订的部分与对应阶段；「按任务读」该行改为实现与审查入口并指向实施台账与验收门禁；「权威层级」改为提案已裁决、实施依据为该规格与其实施台账，被测试锁定的文本、设计章节与接口合同字段条款落地前仍为 `3b4afe5` 文本 |
| `2026-10-04-errands-workday-implemented` | 2026-10-04 | 个人事务与工作安排实现后文档（IX1 的【后】部分，按 `d8a41d3` 核对）：文档地图中冻结时注明被该规格修订的工具交互、AS2 集成、life-runtime、查看器对象名称、查看器上下文、一日行为、一日运行保真、人物规格、当日目标、决策上下文、交互菜单、LabelKit 交付、数字人交互、时间与能量、人物 Prompt、地图与门店供给十六行改为已实现（人物 Prompt 一行同批删去括注里冻结时的说法；查看器对象名称、查看器上下文两行另写明条款与查看器代码的出入），规格一行改为条款已全部实现；「按任务读」该行改为已实现并补 `docs/dev/ANALYSIS-spec-coverage.md#analysis-spec-coverage-规格测试用例覆盖率追溯矩阵`「个人事务与工作安排增量映射」；「权威层级」改为【同】作业与被测试锁定的文本已随代码落地 |
| `2026-10-04-errands-workday-verification` | 2026-10-04 | 个人事务与工作安排实现后文档（IX1 的验收记录入口）：文档地图登记本特性验收记录 `docs/archive/errands-workday/errands-workday-verification.md`（`验收记录`），该行写明配对全天复跑（阶段 6，只报告）未执行；「按任务读」该行补验收记录入口；文件头日期改写 |
| `2026-10-04-errands-viewer` | 2026-10-04 | 运行查看器补齐个人事务条款之后（`1346e0d`、`6c81126`）：文档地图中查看器对象名称、查看器上下文两行由「另写明条款与查看器代码的出入」改为修订条款已实现（两行的改写随 `6c81126` 提交，本行补登并写明实现提交）；个人事务与工作安排验收记录一行补「全量门禁之后补齐的查看器事务显示」。查看器的三处出入由最终全量运行（`a7e2fdc`）之后的实现后文档核对查出，其后随查看器测试补齐（`tests/tools/` 的 Node 用例由 62 个增至 75 个，全部通过），经过见该验收记录 |
| `2026-10-04-errands-final-gate` | 2026-10-04 | 个人事务与工作安排的最终门禁在分支头上重跑之后：上一行所写的「最终全量运行（`a7e2fdc`）」是登记该行时的状态，其后全量测试与覆盖率门禁在分支头 `964de50`（含 `1346e0d`、`6c81126` 与全部实现后文档）的干净检出上重跑通过（5851 passed、7 skipped、0 failed，分支覆盖率 95.15%，未覆盖公共接口 0），`docs/dev/coverage-report.json` 随之重新生成；文档地图中个人事务与工作安排验收记录一行写明两次全量运行各自的提交，其余各行不变。经过见该验收记录「全量门禁与覆盖率」，数字与报告身份见 `docs/dev/coverage-baseline.md#coverage-baseline-覆盖率门禁口径与基线`「基线记录」首节 |
| `2026-10-04-maintainability-proposals` | 2026-10-04 | 文档地图「特性规格与提案」登记两份未冻结提案：代码分层、文档测试解耦与测试分层提案，以及共享规则单一归宿与可重建环境提案。两者都不改变行为；按「权威层级」，裁决之前任何代码、测试与验收结论不得依赖其内容 |
| `2026-10-05-layering-and-test-tiers` | 2026-10-05 | 五项增量正文并回、历史原文归档、单一合同提示、机器验收台账及显式稳定标识；门禁覆盖全部现行文档和固定历史快照 |
| `2026-10-05-idle-fillers-proposal` | 2026-10-05 | 文档地图「特性规格与提案」登记未冻结提案：让「等待」成为例外（系统层措辞、零点计划模板与班次边界唤醒）。它不改变行为；按「权威层级」，裁决之前任何代码、测试与验收结论不得依赖其内容 |
| `2026-10-05-idle-fillers-freeze` | 2026-10-05 | 冻结让「等待」成为例外规格：用户指示实施该规格、不延后任何尚未完成的工作并采纳提案全部推荐（D12 随文更正、D13 重新引导本机环境），提案改 `已裁决` 并注明已收敛为冻结规格；登记规格（`FROZEN`）；生活运行时、查看器对象名称、一日行为、人物规格、当日目标、LabelKit 交付、数字人交互七行注明被该规格修订的部分与阶段，写随对应阶段提交实现；一日运行行为设计一行注明其等待立场已被取代；「按任务读」加实现与审查入口；版本表并回此前被空行隔开的两行 |
| `2026-10-05-idle-fillers-c` | 2026-10-05 | [让「等待」成为例外规格](dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)阶段 1（班次与休息时段起止的唤醒）实现后：文档地图里生活运行时、查看器对象名称、一日行为、人物规格、当日目标、LabelKit 交付、数字人交互七行中由该规格阶段 1 修订的部分改为修订条款已实现，各行由阶段 2、3 修订的部分仍写随对应阶段提交实现；该规格一行注明阶段 1 已实现 |
| `2026-10-05-idle-fillers-a` | 2026-10-05 | [让「等待」成为例外规格](dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)阶段 2（系统层第 7、14、15、16 条）实现后：文档地图里一日行为、人物规格、当日目标、LabelKit 交付四行中由该规格阶段 2 修订的部分（等待与内容接触补系统层写给人物的过法；系统层的作息、饥饿、困意与决策机会；钟点只是大概；系统层改写的指纹叙述）改为修订条款已实现，各行由阶段 3 修订的部分仍写随阶段 3 提交实现；该规格一行注明阶段 2 已实现 |
| `2026-10-05-idle-fillers-b` | 2026-10-05 | [让「等待」成为例外规格](dev/SPEC-idle-fillers.md#spec-idle-fillers-让等待成为例外开发规格)阶段 3（零点计划模板第三、五、六条）实现后：文档地图里生活运行时、人物规格、当日目标、LabelKit 交付四行中由该规格阶段 3 修订的部分（零点计划修订一节的写法；零点计划的写法与提醒；零点计划模板改写的指纹叙述）改为修订条款已实现；该规格一行注明阶段 3 已实现 |
| `2026-10-05-cross-platform-baseline-proposal` | 2026-10-05 | 文档地图「特性规格与提案」登记未冻结提案：完整日行为基线跨平台一致（快照归档次序、工作区文本换行、工作区路径分隔符与平台数学库四处原因；2026-10-06 按 macOS 导出修订）。它不改变行为；按「权威层级」，裁决之前任何代码、测试与验收结论不得依赖其内容 |
| 环境归属与等待行为整合 | 2026-10-06 | 保留已实施的班次唤醒、系统层与零点计划文本；现行场景结构 6 与显式数据仓输入，完整日核验实际工件，历史两份证明不改；跨平台方案仍未实施 |
| `2026-10-06-windows-path-safety` | 2026-10-06 | 文档地图共享规则与可重建环境规格一行注明「安全文件」2026-10-06 补 Windows 路径加固（无盘符的根路径算绝对路径，反斜杠、目录联接与任一平台不能原样保存的名字一并拒绝，工作区快照与恢复的成员名使用同一声明规则） |
| `2026-10-06-parallel-activities-freeze` | 2026-10-06 | 文档地图「特性规格与提案」登记冻结规格[同时做两件事规格](dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)（`FROZEN`），「按任务读」加实现与审查入口；被它修订的现行文档在各阶段提交时注明 |
| `2026-10-06-parallel-activities-record` | 2026-10-06 | [同时做两件事规格](dev/SPEC-parallel-activities.md#spec-parallel-activities-同时做两件事开发规格)阶段 1–3 实现后：文档地图里生活运行时、数字人交互、人物规格、一日行为、地图与门店供给、LabelKit 交付六行注明被该规格修订的部分与阶段，写修订条款已实现；该规格一行注明阶段 1–3 已实现 |
| `2026-10-06-rrs-freeze` | 2026-10-07 | 登记资源与规则引擎简化规格为 FROZEN、提案为已裁决，注明被修订规格的真实实施阶段并增加阅读与权威入口；全部阻断关闭，实现和平台通过状态仍由实际台账与回执证明 |
| `2026-10-06-rrs-rules` | 2026-10-07 | 阶段 1：固定规则与 RULES_VERSION、七份环境目录、真实环境命令和五字段运行身份；旧品类合同归档，后续消费者按阶段闭合 |
| `2026-10-06-rrs-store` | 2026-10-07 | 食品、店铺 app 来源、固定价格、消费与六警告合同按冻结阶段三同步；历史裁决保留，完整引擎执行结果按实际验收登记 |
| `2026-10-06-rrs-app` | 2026-10-07 | 阶段 4：app 目录、五类内容、推送、全局内容进度、固定时长与缓解率、八项占用及现行 25/9 阶段边界按冻结规格同步；工作阶段仍保留至阶段 5，完整引擎与跨平台回执未在本阶段登记。 |
| `2026-10-06-rrs-work` | 2026-10-07 | 登记阶段 5 工作主题现行文档与真实收据边界 |
| `2026-10-06-rrs-prompt` | 2026-10-07 | 剩余系统规则与查看器同步，最终工具身份保持 |
| `2026-10-06-rrs-record` | 2026-10-07 | 阶段 7：记录、导出、交付、评估与最终查看器合同的文档入口；执行结果仍按实际回执登记 |
| `2026-10-06-rrs-docs` | 2026-10-07 | 当前规格、提案和实施阶段，exchange/catalog/corpora 归属与手册入口；正式发布及平台未执行 |
| `2026-10-07-rrs-formal-docs` | 2026-10-07 | 正式环境路径、纯事实手机投影与评估输入入口同步；正式回归、覆盖率与平台结论仍按实际回执分别判定 |
| `2026-10-07-rrs-formal-repair` | 2026-10-07 | 明确人工行为测试与正式验收来源，保存恢复使用同一来源；依赖验证执行真实 SQLite 与生产字体选择器；原失败与最新正式门禁各保留实际身份 |
| `2026-10-07-repository-ownership` | 2026-10-07 | 按用户裁决收敛目录与职责：WorldMock 保留模拟、记录、完整认证足迹与评估；下游加工、模型任务、训练和规则数据归 LabelKit-DataSet，源码快照已接收，新消费验收另记实际证据 |
| `2026-10-07-acs-freeze` | 2026-10-07 | 登记动作与内容简化规格为 FROZEN、提案为已裁决；注明被修订规格及其实施阶段，增加阅读与权威入口；实施计划、各阶段文件清单与设计文档改写清单已对照代码基线核定，实现与平台状态由后续台账与回执证明 |
