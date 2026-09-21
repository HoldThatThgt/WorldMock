# 一日行为设计的业界参考详表

这份细节文档只支持主报告的机制判断，不以论文或产品文档替代 WorldMock 的领域合同。
一手来源于 2026-09-21 核对。机制名称固定为**事件驱动的持续活动**、
**层次与并行状态**、**事件投影**、**观察与主观记忆分离**。
`pyproject.toml:10-31`、`uv.lock` 和 AS2 的 `pyproject.toml` 均未列 SimPy 或 XState；
这里借用已验证的机制，不主张引入这两个框架重写现有模拟引擎。

| 机制 | 对应资料 | 支持的 WorldMock 差距 |
| --- | --- | --- |
| 事件驱动的持续活动 | SimPy；Sutton、Precup、Singh | 等待缺少可核验行为；唤醒与活动持续性 |
| 层次与并行状态 | Harel；Stately | 出行中的并行活动；餐馆进食路径 |
| 事件投影 | Microsoft Azure Architecture Center；Martin Fowler | 等待缺少可核验行为；餐馆进食路径；记忆事实约束 |
| 观察与主观记忆分离 | Park 等人的观察、规划、反思架构，加 WorldMock 自身的事件权威约束 | 记忆事实约束 |

## SimPy：事件驱动的持续活动

**出处。** SimPy 项目，[Process Interaction，官方文档](https://simpy.readthedocs.io/en/latest/simpy_intro/process_interaction.html)；
网页没有可核实的独立发表年份或 DOI，按官方产品文档处理，**DOI：无**。

**解决的问题。** 长时过程不必在每个内部时间片重新请求决策。该文档的
“Waiting for a Process”把进程当作可等待的事件；“Interrupting Another Process”
让外部过程在特定时刻中断它，受中断过程明确处理后续行为。
**可借机制。** 人物选择一次有持续时间的活动后，环境推进其结算和进度；
活动结束或具体外部事件才形成新的决策机会。中断保留已发生的区间与原因。
**不可照抄。** WorldMock 已有 `src/worldmock/life/engine.py` 的时间推进、
`rhythm.py` 的唤醒判定、SQLite 事件与共同检查点；整体改用 SimPy 的生成器过程
会形成第二套调度与恢复权威。SimPy 示例也没有人物模型、门店库存与记忆事实合同。
**可能落点。** `life/engine.py`、`life/rhythm.py`、`life/activities.py` 的持续性
不变量和事件驱动唤醒；记录仍由既有 `footprints/store.py` 承担。

## Sutton、Precup、Singh：事件驱动的持续活动

**出处。** Richard S. Sutton、Doina Precup、Satinder Singh，1999，
“Between MDPs and semi-MDPs: A framework for temporal abstraction in reinforcement learning”，
*Artificial Intelligence* 112(1–2):181–211，
DOI：[10.1016/S0004-3702(99)00052-1](https://doi.org/10.1016/S0004-3702(99)00052-1)；
[论文全文](https://people.cs.umass.edu/~barto/courses/cs687/Sutton-Precup-Singh-AIJ99.pdf)。

**解决的问题。** 论文把跨时间动作定义为可启动条件、执行策略和终止条件组成的
“option”，并允许在执行中因条件变化而中断；因此“活动持续”和“有理由才重新选择”
可以分别建模，见论文第 2 节的形式定义与第 4 节的中断讨论。
**可借机制。** 以现有活动的启动资格、当前进度、完成与不可行条件作为可审计合同；
内部结算不天然等于一次新选择，真正的完成或外部事件才触发人物重新决策。
**不可照抄。** 这是一篇强化学习形式化论文，不证明需要增加奖励函数、训练策略、
概率终止器或自主低层策略。WorldMock 的选择权属于人物模型，世界状态和结算权属于环境。
**可能落点。** `life/models.py` 的活动状态，`life/schedule.py` 与 `life/rhythm.py`
的终止和唤醒条件，以及长时活动跨多次结算的测试。

## Harel：层次与并行状态

**出处。** David Harel，1987，“Statecharts: A visual formalism for complex systems”，
*Science of Computer Programming* 8(3):231–274，
DOI：[10.1016/0167-6423(87)90035-9](https://doi.org/10.1016/0167-6423(87)90035-9)；
[出版记录与摘要](https://www.sciencedirect.com/science/article/pii/0167642387900359)。

**解决的问题。** Harel 在原论文摘要中明确把层次、并发和通信列为相对平面状态图的
扩展。复杂行为因此可以同时说明“大活动”和其内部阶段，也能表达彼此独立的状态区域。
**可借机制。** 把“去餐馆吃饭”理解为有发现、出行、取得餐食、实际进食等阶段的
持续意图，并为合法的随行活动留独立状态，而不是将每个阶段误写成已完成的进食事实。
**不可照抄。** 论文给出状态建模语言，不给 WorldMock 的购买、物流、身体和内容算子。
不能因图上有并行区域就绕过资源占用、地点和单写入环境 actor 的规则。
**可能落点。** `life/models.py` 的活动与状态表示、`life/engine.py` 的阶段转换、
`life/activities.py` 的资源占用；现有门店购买规则仍归 `life/basket.py`。

## Stately：层次与并行状态

**出处。** Stately，[Parallel states，官方文档](https://stately.ai/docs/parallel-states)；
网页没有可核实的独立发表年份或 DOI，按官方产品文档处理，**DOI：无**。

**解决的问题。** 官方文档把并行状态定义为同一父状态下多个同时活跃的区域；
一个事件能送达所有活跃区域，并要求相关区域各自维持状态。
**可借机制。** 同一人物的“步行出行”和“听节目”可以作为两个有各自进度的相关状态，
共享时间边界但分别留证；驾驶占用注意力时则由 WorldMock 资源规则拒绝冲突组合。
**不可照抄。** 文档示例是 XState 的程序接口，不是 Python/AS2 运行时合同；
引入 XState 会新增语言边界与重复状态权威。其并行状态本身也不解决人物注意力、
车辆、手部、内容许可和结算次序。
**可能落点。** `life/models.py` 的单人活动状态、`life/engine.py` 的共同时间推进、
`life/activities.py` 的互斥检查、`life/observe.py` 的多活动观察投影。

## Microsoft Azure Architecture Center：事件投影

**出处。** Microsoft，[Event Sourcing pattern，Azure Architecture Center 官方文档](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing)；
网页没有可核实的独立发表年份或 DOI，按官方产品文档处理，**DOI：无**。

**解决的问题。** 官方文档以追加事件作事实记录，从事件生成按用途优化的只读投影；
它同时强调事件溯源的成本、投影延迟和幂等要求，反对为了简单状态查询全系统改造。
**可借机制。** 从已确认的 `action_result`、`activity_progress`、`environment_event`
生成一天的行为时间线与可核验事实。配送到达、持有物增加和实际进食应在投影中保持
各自含义，不由叙述推断或合并成“吃过”。
**不可照抄。** WorldMock 已有 SQLite 事件库、单写入 actor、检查点和导出投影；
没有依据增加队列、异步订阅、另一事件库或把当前原子结算改成最终一致的读模型。
**可能落点。** `footprints/export.py`、`scripts/render_day.py`、
`evaluation/behavior_metrics.py` 与 `life/observe.py` 的事件消费侧；事件写入仍归
`life/engine.py` 和 `footprints/store.py`。

## Martin Fowler：事件投影

**出处。** Martin Fowler，2005-12-12，[Event Sourcing，Further Enterprise Application Architecture 草稿](https://martinfowler.com/eaaDev/EventSourcing.html)；
非学术网页，**DOI：无**。网页自述为草稿，不当作 WorldMock 的规范权威。

**解决的问题。** Fowler 区分“当前在何处”和“如何到这里”；持久化有顺序的领域事件
可重建过去状态、查询历史和派生后续视图。
**可借机制。** 以已结算的区间和结果核对“在餐馆吃饭”“旅行时听播客”这类叙述：
没有对应事件时只保留意图或主观说法，不把它们写进客观足迹。
**不可照抄。** 不能按文章演示重写 WorldMock 的状态存储，不能改写历史事件使其
迎合记忆，也不能让事件投影直接替人物作新决定。
**可能落点。** `footprints/quality.py`、`footprints/export.py`、
`evaluation/behavior_metrics.py` 与记忆事实对账入口。

## Park 等人：观察与主观记忆分离

**出处。** Joon Sung Park、Joseph C. O’Brien、Carrie J. Cai、Meredith Ringel Morris、
Percy Liang、Michael S. Bernstein，2023，
“Generative Agents: Interactive Simulacra of Human Behavior”，arXiv 预印本
[arXiv:2304.03442](https://arxiv.org/abs/2304.03442)，
DOI：[10.48550/arXiv.2304.03442](https://doi.org/10.48550/arXiv.2304.03442)。

**解决的问题。** 论文摘要明确描述经验记录、上层反思、按需检索与规划的架构，
并报告观察、规划、反思组件对行为可信度的消融结果。
**可借机制。** 保留人物可检索的主观经验与反思，同时把环境已结算事件和人物
自述作为不同来源；长期记忆的“已发生”说法必须能回指支持该命题的环境事件。
后一句是 WorldMock 从旧运行“记忆说吃了、事件只记等待”提出的约束，**不是**论文
已验证的抗幻觉机制。
**不可照抄。** 论文不能证明自由文本反思会阻止虚构经历；不能仅凭 `refs` 指向
一条观察或旧记忆就把“想吃饭”提升为“已吃过饭”。
**可能落点。** `custom/agents/life_agent.py` 的 `finish` 记忆输入、
AS2 `agent/memory_runtime.py` 的写入/巩固、`life/engine.py` 的环境事件与
`footprints/quality.py` 的命题支持校验；客观事实权威仍由环境维护。

## 对主报告的更正与核对

已读取 `docs/dev/PROPOSAL-dayrun-behavior.md` 初稿（156 行）。主报告的四个机制名称
与本表逐字一致；五个差距名称与 `behavior-evidence.md` 的五个同名章节逐字一致；
没有“结算必然产生决策”“全系统没有并行”或“Park 证明记忆不会幻觉”的误称。
主报告没有源码行号，细节文档提及的模块路径均在当前 `20dedcd` 工作树存在。

| 更正位置 | 初稿问题 | 应有表述与依据 |
| --- | --- | --- |
| 第一章“用户原话” | “这次明确要求同时完成现状核查、适用修复整合和行为设计方案”是对用户选择的转述，不是逐字原话 | 移到“转述确认”，或仅保留实际逐字引文；依据 `design-writer/SKILL.md` 的用户原话规则 |
| 第二章“记忆有事实来源，也有人物视角” | “巩固只压缩已核验事实”可能把感受和意图排除在长期记忆外，与同段的人物视角及本表的观察与主观记忆分离机制不一致 | 分别保留有事件支持的完成事实与标明主观来源的感受、意图；后者不可升级为世界事实。Park 等人支持经验与反思架构，但不证明抗幻觉 |

主报告若随后改动，应重查这两处文字以及四个机制、五个差距的同名关系。

复核更正：父任务已将转述替换为用户实际回复，并明确事实与主观内容分别保留。
