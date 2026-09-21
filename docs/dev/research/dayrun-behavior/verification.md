# 一日行为实施与验收台账

> 实施中。基线 `74edeaddccf1e051a0f7c98024ec95eeb6c48514`，分支 `codex/dayrun-behavior`。
> 工作树 `/Users/atishoo/.codex/worktrees/dayrun-behavior/WorldMock`；不修改并发主工作树。
> 行为权威为 [特性规格](../../SPEC-dayrun-behavior.md)，接口归 [CONTRACTS](../../../CONTRACTS.md)，
> 记录格式归 [记录引擎设计](../../../design/05-recording-engine.md)。本表不能替代生产验证。完整逐文件清单见[文件清单](files.md)。

## 独立方案核查

[活动与身体](concurrency-design-review.md)、[记忆与记录](memory-design-review.md)、
[餐食与音频](dining-design-review.md)保存实施前核查与来源。报告中的旧代码差距属于基线证据，
最终裁决以冻结规格为准。

## 文件修改责任与验收映射

| 要求 | 生产责任 | 设计与接口 | 验证方法 | 状态 |
| --- | --- | --- | --- | --- |
| 活动集合与资源准入 | life/models、activities、engine、catalog/affordances | 交换设计、模拟设计、生命周期规格、CONTRACTS | 真实dispatch、拒绝原子性、恢复校验 | 实施中 |
| 真实人物组合入口与独立控制 | LifeAgent、life_menu、interaction_menu、observe/projection/narration | 生命周期规格、交互菜单规格、输入输出设计 | 原生工具schema与完整人物链 | 实施中 |
| 共同身体与全部活动边界 | body、engine、rhythm、schedule | 模拟设计、记录设计、当日目标规格 | 区间守恒、顺序无关、预测同源、多日恢复 | 实施中 |
| 等待无虚构内容 | body、motivation、footprints/evaluation、render_day | 特性规格、记录设计、真实性规格 | 实际等待区间与真实播放分别断言 | 实施中 |
| 餐馆实际购买进食 | 场景、供给语料、dining、观察投影 | 资源设计、配置设计、地图规格、用户手册 | 首次到店、窗口授权、消费增量、恢复 | 实施中 |
| 官方音频与时轴 | 语料manifest、content、类别声明 | 资源设计、素材手册、来源核验 | 官方锚点、完整段揭示、倍速与独立到达 | 实施中 |
| 已核验经历 | footprints/experiences、store、quality、export | 记录设计、CONTRACTS | 截止、来源、多人物、幂等、畸形拒绝 | 实施中 |
| 主观记忆与确定性视图 | life_memory、LifeAgent、AS2 memory runtime/store/factory | 记录设计、真实性规格、总体架构 | 标签、引用、反思、合并、空输入、写盘失败、恢复 | 实施中 |
| 持续威胁交付去重 | rhythm、schedule、models | 模拟设计、当日目标规格 | 新事实提醒、持续条件静默、睡眠过滤、检查点 | 实施中 |
| 所有条款完整集成 | 真LifeAgent→LifeEnv→LifeEngine→SQLite→观察/记忆→导出 | SPEC、VERIFICATION、implementation-status | 多完整自然日、双人、不同种子、非法输出扰动 | 待验收 |
| 独立审查与文档一致性 | 全部实际diff | 全部修改设计/规格/手册及引用 | 全仓覆盖、构建身份、链接、Bob语义突变 | 待验收 |

## 集成中发现并关闭的问题

- 真实控制行、记忆字段与旧测试夹具不一致：按冻结合同更新，保留非法输入拒绝和动态工具身份断言。
- 跨结算区间的官方播客段落被通用字幕去重误删：按段落完成锚点交付，真实暂停、倍速与末锚点回归通过。
- 多个食物事实共享一个消费事件：记忆证据按首次出现去重来源，完整事实仍保留各食物增量。
- 到达效果应用后的路线里程未进入终态动作快照：终态统一取世界表最终活动，原到店强断言通过。
- 活动快照能篡改资源、目标和时间后恢复：结构与冻结目录共同校验，实际持久化后重新打开拒绝。
- 无活动且零能量的瞬时动作后无法推进：空白时间基础代谢到零后仍演化睡压与需要，不创建假等待。
- 其他地点任务或没有地点的自由目标会触发本店闭门提醒：只据明确地点约束构成威胁。

这些修复对应规格已有的事实、恢复及共同时间要求，不新增替人物行动或自由文字推断功能。

## 当前证据

独立工作树初始化正确的可编辑依赖；核心回归基线：
`tests/life/test_engine_round.py tests/life/test_wait_passive_entertainment.py
 tests/integration/test_observation_agent_seam.py tests/integration/test_run_manifest.py`：
41 passed，10.52 秒。该基线不证明新增特性已完成。

多个完整日和中断恢复已实跑：`tests/integration/test_dayrun_complete_days.py` 两种子、双人、
每人 86400 秒共同身体区间，2 passed；`tests/integration/test_meal_checkpoint.py` 实际自提进食中断、
检查点恢复、剩余量/库存/金额/食品热量/导出守恒，1 passed。

首次整合覆盖门禁是在并发实现尚未冻结时运行，75 failed、3709 passed、4 个既有历史产物缺失跳过；
分支 92.26%，公共接口有 33 项未完全执行。此轮是定位回归和边界测试缺口的失败证据，
不是放行结果；缺口正在按独占文件修复，最终必须以固定源码重新执行。

目录四项逐字节复现、NASA 派生文件检查通过；正式十二周场景 7386 条素材、143 个分区全部可达。
素材可达不证明真实十二周模型运行完成。
