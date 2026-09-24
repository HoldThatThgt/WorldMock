# WorldMock 文档入口

> 状态：文档集整体处于实现中——此前确定性基线已有验收，新特性以各自实施台账为准，三项真实验收（正式十二周运行、三次隔离盲评、LabelKit 完整自然日交付）未完成。
> 日期：2026-09-24
> 范围：文档地图、按任务阅读路径、权威层级与文件归属。本文件不定义任何接口与行为，只回答「该读哪一份」。

## 这是什么

WorldMock 用 AgentSociety 2（下称 AS2）驱动数字人的日常生活，把生活过程写成可追溯的数字足迹，再交给 LabelKit 生产模型训练数据与规则引擎测试数据。正式实验是上海张江白领的单人物十二周（2026-01-05 至 2026-03-30）。**数字足迹**指一次生活过程留下的、可追溯的观察、决策、执行、状态变化、结果、记忆与相互关系。

文档集按职责分工：`ARCHITECTURE.md` 写分层理由，`CONTRACTS.md` 提供跨模块接口定义与导航，
`docs/dev/SPEC-*.md` 写对应特性的行为，`VERIFICATION.md` 写通过条件，`implementation-status.md` 写当前状态。
[用户配置手册](manual/README.md)面向使用者，说明如何编写、修改和校验当前支持的配置。
记录引擎的接口、行为与格式统一维护在[记录引擎设计](design/05-recording-engine.md)，
其他文档引用所属合同，不再重复定义。

## 文档地图

### 主文档

| 文档 | 职责 | 状态 |
| --- | --- | --- |
| `docs/INDEX.md` | 本文件：文档地图、阅读路径、权威层级与文件归属 | 入口 |
| `docs/ARCHITECTURE.md` | 系统分层、职责边界、数据流与核心决策的理由 | FROZEN |
| `docs/CONTRACTS.md` | 跨模块接口合同与导航；记录引擎定义引用所属设计，其余接口在本文件维护 | FROZEN |
| `docs/EXPERIMENT.md` | 实验身份：时间窗与日历情境、依赖冻结、运行绑定、场景参数、内容与语料绑定、样本划分与外部参照 | FROZEN |
| `docs/VERIFICATION.md` | 放行门禁（最终门禁、协议门禁、工程门禁、报告项）、交付阶段与通过条件、测试矩阵、验收原则与结论边界 | NOT-COMPLETE |
| `docs/dev/implementation-status.md` | 当前实现状态：确定性范围与测试基线实测、覆盖率口径、规格追溯与突变守卫、未完成项与阻塞 | NOT-COMPLETE |
| `docs/dev/coverage-baseline.md` | 覆盖率门禁的判定口径、测量与排除范围、失败关闭约定与基线记录（最近一次完整门禁通过） | 现行口径 |
| `docs/dev/coverage-report.json` | 门禁生成物：分支覆盖率、未覆盖公共接口与逐文件分项，由 `scripts/coverage_gate.py` 写入 | 生成物 |

### 用户配置手册

从[创建自己的第一个场景](manual/README.md#创建自己的第一个场景)开始，再按修改目标阅读对应页面。
手册提供使用步骤和示例，接口与行为定义仍引用所属合同和规格。

| 页面 | 使用目标 |
| --- | --- |
| [手册入口](manual/README.md) | 文件分类、一天自定义场景、加载检查与运行 |
| [人物画像](manual/personas.md) | 编写背景故事、作息、关系与人物初值，预览和校验卡片 |
| [生活场景](manual/scenarios.md) | 自定义日期、人数、账户、菜单、任务、消息及预排事件 |
| [世界素材](manual/resources.md) | 准备地图、门店供给、商品、内容、语料和目录输入 |
| [规则配置](manual/rules.md) | 编写品类与交换规则，调整身体、动机和画像映射 |
| [运行配置](manual/runtime.md) | 模型凭据、新建运行、恢复和反事实干预文件 |
| [数据生成](manual/delivery.md) | LabelKit 项目、生成设置与规则测试数据 |
| [真实性评估](manual/evaluation.md) | 评估配置、人工参照和评审输入 |

### 特性规格与提案

| 文档 | 职责 | 状态 |
| --- | --- | --- |
| `docs/dev/SPEC-as2-integration.md` | 选用 AS2 的理由与复用边界、分层职责，以及 AS2 核心链路为本次场景必须发生的确定性改动 | FROZEN |
| `docs/dev/SPEC-life-runtime.md` | LifeEnv 的状态归属与生命周期、共享时钟与并发活动、身体语义与体感分级、消费生产与需求模型、语料准入与内容提供、场景与身体规则、观察投影与提示合同、候选契约与呈现规则、社交最小可行版 | FROZEN |
| [查看器对象名称](dev/SPEC-run-viewer-names.md) | 地点、活动及内容名称的运行内关联、缺失提示与验证 | FROZEN |
| [查看器名称验收](dev/run-viewer-names-verification.md) | 真实运行、回归测试与独立语义变异闭合 | 验收记录 |
| [一日运行行为设计](dev/PROPOSAL-dayrun-behavior.md) | 等待、通勤、进食、记忆和持续活动的事实核查与采纳依据 | 已裁决 |
| [一日行为特性规格](dev/SPEC-dayrun-behavior.md) | 并行生命周期、共同身体区间、餐食、音频、已核验经历与主观记忆、预警交付 | FROZEN |
| [一日行为实施台账](dev/research/dayrun-behavior/verification.md) | 文件修改责任、逐条生产与测试映射、完整自然日及独立审查证据 | 实施台账 |
| [一日运行修复核查与验收](dev/research/dayrun-reconciliation/verification.md) | dayrun 清单到主线修复、回归与独立突变证据 | 验收记录 |
| `docs/dev/SPEC-life-realism.md` | 人物的两阶段决策调用、记忆写入与巩固、动机层、当日目标、世界素材品类准入、素材可达性与呈现轮转、候选与呈现、画像编译、商品数量合同、运行呈现与导出投影 | FROZEN |
| `docs/dev/SPEC-day-plan-goals.md` | 当日目标、实际推进证据、修订、环境威胁、三层质量报告与完整验收 | 已实现，确定性验收通过 |
| `docs/dev/PROPOSAL-day-plan-goals.md` | 目标与时间职责分离的裁决依据，现行行为引用最终规格 | 已裁决 |
| `docs/dev/research/day-plan-semantics.md` | 当日目标学科调研与核实限制 | 调研证据 |
| `docs/dev/research/day-plan-goals-verification.md` | 独立 worktree 基线、逐要求验证、实际修改清单与独立变异证据 | 验收通过 |
| `docs/dev/research/day-plan-goals-main-verification.md` | 当日目标与 main 新增手册的组合验收、依赖发布与源码身份 | 组合验收通过 |
| `docs/dev/research/day-plan-goals-bob-domain.md` | 当日目标领域、节律与午夜结算的独立源码突变证据 | 独立审查 |
| `docs/dev/research/day-plan-goals-bob-interface.md` | 当日目标工具、观察与叙述的独立源码突变证据 | 独立审查 |
| `docs/dev/research/day-plan-goals-bob-metrics.md` | 三层统计及导出首次审查，保留三个初次存活结论 | 独立审查 |
| `docs/dev/research/day-plan-goals-bob-metrics-recheck.md` | 三个存活补丁在新增行为断言后的原样复验 | 独立复验 |
| `docs/dev/SPEC-decision-context.md` | 决策自述、冻结上下文、动作登记与结果通道、场景信息、预算、修改清单与验收台账 | FROZEN |
| `docs/dev/SPEC-interaction-menu.md` | 地点类与人物交互菜单、工具和活动类型准入、冻结观察、模型与环境共同门控、运行身份及验收台账 | FROZEN |
| `docs/dev/SPEC-labelkit-delivery.md` | 数字足迹接入 LabelKit 的输入范围与加工责任、证据窗口与来源关联、交付产物与发布边界、长序列与数据划分、真实调用的生产与验证步骤 | FROZEN |
| `docs/dev/PROPOSAL-agent-interaction.md` | **已裁决并冻结**：数字人交互的原始问题、心智模型逐条对账与取舍记录；行为定义已由 `SPEC-agent-interaction.md` 承载 | FROZEN |
| `docs/dev/SPEC-agent-interaction.md` | **FROZEN**：会话层与生命周期、七层上下文与进度层、稳定工具面（7 个工具）、决策轮循环与播报节律、前置条件前置、并发上限、午夜浓缩；显式修订四条既有冻结条款并随冻结生效；遗留 2 处实现落地缺口，不改变条款语义 | FROZEN |
| `docs/dev/research/agent-interaction-document-impact.md` | 文档影响与文件修改清单：爆炸半径实测、五处词表同步点、导出重建约束、冻结规格与设计文档修订清单 | 调研证据 |
| `docs/dev/SPEC-time-and-energy.md` | 时间与能量特性规格：决策不耗时与同刻再决策、饥饿低能量显示与商品热量及路线费力、能量不阻断而让人物趁早吃、跨日 Dreaming 与计划提醒、五个动词与并行准入、动作照常列出并带条件说明、派生时长上限、常驻「怎样过一天」提示、地点会话与两个活动名额、`finish` 并入 `conclude_activity`、提示不含内部身份、本机运行修复与 issue #1 复核；列出对既有冻结条款的修订，随冻结生效 | 未冻结提案 |
| `docs/dev/PROPOSAL-decision-context.md` | 已裁决的心智模型与取舍记录；行为与开发验收由 `SPEC-decision-context.md` 承载 | 调研证据 |
| `docs/dev/PROPOSAL-interaction-menu.md` | 交互菜单的原始问题、方案比较与取舍记录；行为与验收由 `SPEC-interaction-menu.md` 承载 | 调研证据 |
| `docs/dev/SPEC-category-contract.md` | 品类树、属性增量、三态配对、闭集交换、库存转换、细化发布和运行内容身份 | FROZEN |
| `docs/dev/category-contract-verification.md` | 品类契约逐要求实施、测试与独立突变证据 | 实施台账 |
| `docs/dev/category-contract-main-verification.md` | 合入决策上下文主分支、模型库存路径补修、全仓门禁与独立复验 | 验收记录 |
| `docs/dev/category-contract-files.md` | 对照开发基线逐文件核对真实 Git diff 与功能责任 | 实施台账 |
| `docs/dev/category-contract-bob-review.md` | 独立语义突变、原始证据及修复复审结果 | 审查记录 |
| `docs/dev/PROPOSAL-category-contract.md` | 品类契约提案裁决记录；规范已收敛到对应 SPEC | 已裁决 |
| `docs/dev/SPEC-map-and-storefront.md` | 地图唯一来源、地点发现、独立门店供给、营业与报价时态、原子购买、自提配送、恢复和完整验收 | FROZEN |
| `docs/dev/PROPOSAL-map-and-storefront.md` | 已裁决的地图与门店供给心智模型、采纳机制和审查基线差距；行为以开发规格为准 | 调研证据 |
| `docs/dev/ANALYSIS-map-model.md` | 原始问题与已纠正的历史判断，连接现行开发规格和研究证据 | 调研证据 |
| `docs/dev/research/map-and-storefront-feasibility.md` | 商品与供给、路线方式、营业、AS2 复用和许可的一手资料核验 | 调研证据 |
| `docs/dev/research/map-and-storefront-document-impact.md` | 地图与门店供给的要求、文档、测试影响及审查基线 | 调研证据 |
| `docs/dev/research/map-and-storefront-verification.md` | 独立地图分支源码身份、要求到行为测试、全量门禁、独立变异和补测复验；保留原始收据 | 历史验收记录 |
| `docs/dev/research/map-and-storefront-main-verification.md` | 地图与品类主线组合的固定源码、完整工程门禁、计价及状态提交审查、原存活突变闭合和数据准入 | 验收记录 |
| `docs/dev/ANALYSIS-spec-coverage.md` | 规格条款与测试用例的覆盖率追溯矩阵 | 现行追溯矩阵 |
| `docs/dev/research/decision-context-verification.md` | 决策上下文完整修改清单、条款到测试、独立审查与工程门禁证据 | 验收记录 |
| `docs/dev/research/decision-context-bob-protocol.md` | 决策协议与预算的隔离源码突变、补充验证与恢复证据 | 验收记录 |
| `docs/dev/research/decision-context-bob-projection.md` | 结果投影、场景、事件与恢复入口的隔离源码突变及恢复证据 | 验收记录 |
| `docs/dev/research/interaction-menu-feasibility.md` | 动态工具、场景可供性、服务端重验与运行身份的业界机制核查及采纳边界 | 调研证据 |
| `docs/dev/research/interaction-menu-verification.md` | 交互菜单修改清单、条款到测试、固定源码门禁与 Uncle Bob 突变证据 | 验收记录 |
| `docs/dev/research/interaction-menu-main-verification.md` | 交互菜单与品类、库存及决策上下文的主线合并、冲突裁决和完整工程门禁 | 验收记录 |
| `docs/dev/research/interaction-menu-bob-environment.md` | 菜单声明、交集、观察与环境执行的隔离源码突变、补强复验及恢复证据 | 验收记录 |
| `docs/dev/research/interaction-menu-bob-host.md` | 人物工具与参数、恢复身份和提示字段的隔离源码突变、补强复验及恢复证据 | 验收记录 |

### 资源引擎设计

[资源引擎设计](design/03-resource-engine.md)统一承载资源定义、内容与语料、目录发布、检索和提供，
状态为 DRAFT；交换条件与投入产出引用交换引擎，实际变化与结算引用模拟引擎，接口和行为仍遵循冻结合同及规格。

### 模拟引擎设计

[模拟引擎设计](design/04-simulation-engine.md)承载运行初始化、观察与决策机会、动作受理、共享时钟、
活动推进、结算、唤醒和恢复编排，状态为 DRAFT；人物算法、资源提供、记录保存和离线交付引用所属模块。

### 交换引擎设计

[交换引擎设计](design/02-exchange-engine.md)统一承载维护交换规则、查询适用规则、校验交换条件、计算投入产出、
校验资源分录与返回规则结果，状态为 DRAFT。接口与行为仍以对应冻结合同和规格为准.

### 记录引擎设计

[记录引擎设计](design/05-recording-engine.md)统一承载事件、素材、事务、检查点、恢复与反事实分支、
导出、质量报告和运行诊断，承接原已冻结的接口与行为约束；待实现设计与已知差距在文内单列。

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

## 按任务读

| 你想做的事 | 从这里读 | 接着读 |
| --- | --- | --- |
| 编写自己的配置、自定义人物与生活环境 | [用户配置手册](manual/README.md) | 对应手册页的示例、字段说明与校验命令 |
| 了解系统怎么分层、为什么这样设计 | `docs/ARCHITECTURE.md` | 对应 `docs/dev/SPEC-*.md` 看具体行为 |
| 实现或修改某个特性的行为 | `docs/dev/SPEC-*.md` 中对应那一份 | `docs/CONTRACTS.md` 取接口，`docs/VERIFICATION.md` 取门禁 |
| 找某个接口、字段、枚举、目录结构、命令行参数或提示词模板 | `docs/CONTRACTS.md` | 设计理由在 `docs/ARCHITECTURE.md` 与对应 SPEC |
| 知道现在跑到哪一步、已有哪些实测证据 | `docs/dev/implementation-status.md` | `docs/dev/coverage-baseline.md` 与 `docs/dev/coverage-report.json` |
| 判断能不能放行、本次要交哪些证据 | `docs/VERIFICATION.md` | `docs/dev/implementation-status.md` 取源码身份与命令收据 |
| 复现实验：依赖版本、场景参数、时间窗、样本划分 | `docs/EXPERIMENT.md` | `docs/CONTRACTS.md` 取运行接口与目录结构 |
| 理解地图、门店供给与交易 | `docs/dev/SPEC-map-and-storefront.md` | `docs/CONTRACTS.md` 取字段，各引擎设计取定义、判断、结算和记录；研究核验说明路线与许可边界 |
| 配置或修改地点类与人物交互菜单 | `docs/dev/SPEC-interaction-menu.md` | `docs/design/08-configuration.md` 取配置，`docs/design/04-simulation-engine.md` 取执行链路 |
| 查历史调研、审计与根因结论 | `docs/dev/research/` | 对应 SPEC 看结论如何落地为条款 |
| 了解数字人交互的设计依据与取舍 | `docs/dev/PROPOSAL-agent-interaction.md` | 已裁决：行为定义看 `docs/dev/SPEC-agent-interaction.md`；差距审计引用 `docs/CONTRACTS.md` 与各 SPEC 取证 |
| 实现或审查时间与能量（同刻决策、能量不阻断、Dreaming 与提醒、五个动词、地点会话与活动名额） | `docs/dev/SPEC-time-and-energy.md` | 「冻结条款修订」取被改写的既有冻结条款，「文件修改清单」与「验收台账」取实施范围 |
| 实现或审查数字人交互（会话、上下文分层、稳定工具面） | `docs/dev/SPEC-agent-interaction.md` | `docs/dev/research/agent-interaction-document-impact.md` 取文件清单与爆炸半径，`docs/CONTRACTS.md` 取字段与 Schema |
| 设计突发事件类世界事件（事故、疾病、升迁、丧亲） | `docs/dev/research/exogenous-incidents.md` | `docs/dev/SPEC-life-realism.md` 取现有行为边界，`docs/CONTRACTS.md` 取事件与唤醒闭集 |
| 查看资源、内容与语料的统一设计 | `docs/design/03-resource-engine.md` | 资源定义、来源、版本、内容提供与实现差距 |
| 查看模拟引擎如何组织并推进实际运行 | `docs/design/04-simulation-engine.md` | 初始化、观察与动作受理、时钟与活动、结算、唤醒和恢复编排 |
| 查看交换引擎设计与尚未闭合的实现差距 | `docs/design/02-exchange-engine.md` | 当前实现与验收 |
| 查看记录引擎的接口、行为与实现差距 | `docs/design/05-recording-engine.md` | 同一文档内的存储、恢复、导出、诊断与验收章节 |
| 找某个文件归谁管、代码在哪 | 本文件「文件归属」 | `docs/ARCHITECTURE.md` 看该模块的职责边界 |

## 权威层级

同一件事出现两份说法时，按下面的归属判定，不按文档新旧、不按篇幅长短。

- **用户配置步骤与示例**看 `docs/manual/README.md` 及其分册；手册不新增配置能力或改变所属合同。
- **记录引擎的接口、行为与格式**看 `docs/design/05-recording-engine.md`，原冻结约束继续有效，待实现项单列。
- **其他接口、字段、枚举、Schema、目录结构、命令行参数与提示词模板**看 `docs/CONTRACTS.md`；代码变更先更新所属合同，再改代码。
- **其他行为规则与算法**（怎么结算、怎么推进、怎么决策）看 `docs/dev/SPEC-*.md`。
- **设计理由与职责边界**看 `docs/ARCHITECTURE.md`。
- **实验身份**（依赖、场景、时间窗、样本、外部参照）看 `docs/EXPERIMENT.md`。
- **验收**（什么算通过、门禁键、测试矩阵、证据层级、report-only 边界）看 `docs/VERIFICATION.md`。
- **实现状态**（现在到哪一步、已有哪些证据、还剩哪些阻塞）看 `docs/dev/implementation-status.md`；它只写现状，变更过程由 Git 承载。
- **覆盖率口径与基线**看 `docs/dev/coverage-baseline.md` 与它写出的 `docs/dev/coverage-report.json`。
- **未裁决方向**看状态仍为未冻结提案的 `docs/dev/PROPOSAL-*.md` 与草稿规格。二者在被单独裁决为冻结以前，任何 `CONTRACTS`、代码、测试与验收结论不得依赖其未冻结内容。已裁决的决策上下文、交互菜单、品类契约及地图与门店供给提案仅保留设计记录，实施依据为对应冻结规格。
- **历史证据**看 `docs/dev/research/`。这些文件是当时快照下的依据，不构成当前规范。

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

## 文件归属

### 核心系统

| 路径 | 职责 |
| --- | --- |
| `custom/envs/life_env.py` | AS2 环境适配层（LifeEnv） |
| `custom/agents/life_agent.py` | AS2 人物适配层（LifeAgent） |
| `src/worldmock/life/` | 生活规则、身体、活动、社交、叙述 |
| `src/worldmock/footprints/` | 事件存储、检查点与导出 |
| `src/worldmock/profile/` | 人物卡片编译 |
| `src/worldmock/corpora/` | 语料整理 |
| `src/worldmock/delivery/` | LabelKit 交付 |
| `src/worldmock/evaluation/` | 行为指标与协议门禁 |
| `src/worldmock/catalog/` | 可供性注册表 |

### 测试

| 路径 | 职责 |
| --- | --- |
| `tests/life/` | 生活规则与运行时行为 |
| `tests/footprints/` | 事件存储、检查点与恢复 |
| `tests/integration/` | 跨模块集成与协议 |
| `tests/delivery/` | LabelKit 交付行为 |
| `tests/evaluation/` | 行为指标与协议门禁 |
| `tests/catalog/` | 可供性注册表与目录数据 |

### 场景与配置

| 路径 | 职责 |
| --- | --- |
| `scenarios/` | 实验场景定义 |
| `docs/manual/` | 当前可用配置的用户编写手册与校验示例 |
| `corpora/` | 固化语料素材 |
| `catalog/` | 目录注册表 |
| `labelkit-projects/` | LabelKit 项目配置 |

## 状态与变更约定

文档地图「状态」列只用下表取值，表外取值不得出现在该列：

| 状态词 | 含义 |
| --- | --- |
| `入口` | 本文件：文档集的唯一入口 |
| `FROZEN` | 条款已裁决并冻结，是当前实现合同，改动必须走版本表并说明理由 |
| `NOT-COMPLETE` | 实现或验收尚未收口，任何场合都不得把它当作完成态引用 |
| `现行口径` | 该文件是当前生效的判定口径 |
| `生成物` | 由脚本覆盖重写的产物，内容随每次运行变化 |
| `未冻结提案` | 方向或条款尚未冻结（含草稿规格），使用边界见「权威层级」节 |
| `已裁决` | 提案已收敛为冻结规格，只保留决策背景，不再承载实现要求 |
| `实施台账` | 逐项记录当前实现、对应测试与验收结论 |
| `审查记录` | 保留独立审查、问题修复与复审证据，不替代冻结规格 |
| `现行追溯矩阵` | 随规格条款与测试同步维护的当前映射 |
| `调研证据` | 当时快照下的裁决依据，不单独构成当前行为合同 |
| `验收记录` | 固定源码身份下的实际命令、测试结果和审查证据，不定义新行为 |

接口或行为变更先更新所属合同再改代码；记录引擎统一修改模块设计，其他特性修改对应合同与 SPEC，并在本文件登记阅读入口。

## 版本表

| 版本 | 日期 | 修订内容 |
| --- | --- | --- |
| 1.0 | 2026-09-18 | 文档入口重写：文档地图覆盖全部文档，给出按任务阅读路径、权威层级、引用关系图与文件归属 |
| 1.1 | 2026-09-18 | 状态列词表补全为文档地图实际使用的八个取值并逐条定义，地图取值与词表对齐；位置指代改为章节标题名 |
| 1.2 | 2026-09-19 | 文档地图职责列补登两份规格新增的章：`SPEC-life-runtime.md` 补「候选契约与呈现规则」，`SPEC-life-realism.md` 补「素材可达性与呈现轮转」与「候选与呈现」 |
| 1.3 | 2026-09-20 | 记录引擎的接口与行为集中维护，更新文档地图、权威归属和引用关系 |
| `2026-09-20-decision-context` | 2026-09-20 | 对齐决策上下文特性规格：统一决策自述命名，明确登记与结算结果的通道、场景信息和预算边界 |
| `2026-09-20-interaction-menu` | 2026-09-20 | 登记交互菜单冻结规格、研究与验收入口，将原提案状态改为裁决记录并补实现阅读路径 |
| `2026-09-21-exogenous-incidents` | 2026-09-21 | 登记突发事件机制调研：外生事件的选择与结算分层、可复现随机性手段及引证核实结果，供后续设计裁决引用 |
| `2026-09-22-agent-interaction` | 2026-09-22 | 登记数字人交互未冻结提案：上下文分层与稳定工具面心智模型、骨架/血肉边界，以及十七类表达层失配与门禁缺口审计 |
| `2026-09-22-agent-interaction-spec` | 2026-09-22 | 登记数字人交互特性规格（2026-09-22 提交冻结）：会话层、七层上下文与进度层、稳定工具面（7 个工具，`action` 按对象绑定）、播报节律、前置条件前置、并发上限、午夜浓缩；显式修订四条既有冻结条款；登记文档影响与文件修改清单 |
| `2026-09-24-time-and-energy` | 2026-09-24 | 登记时间与能量特性规格（未冻结）：同刻再决策、能量不阻断、跨日 Dreaming 与提醒、五个动词、地点会话与两个活动名额、`finish` 并入 `conclude_activity`；列出对既有冻结条款的修订，随冻结生效；补实现阅读路径；「未冻结提案」同时适用于草稿规格 |
