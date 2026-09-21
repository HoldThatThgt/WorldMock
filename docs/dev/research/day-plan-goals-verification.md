# 当日目标实现与验收记录

> 状态：实施中。仅实际执行后的条目才能记为通过。
> 范围：[最终规格](../SPEC-day-plan-goals.md)全部行为，无特性延期项。

## 隔离与基线

工作树：`/Users/atishoo/.codex/worktrees/day-plan-goals/WorldMock`。
分支：`codex/day-plan-goals`。
开发基线：`170e1f0fcd83a745f9989da5dac95eac72ea8647`，冻结主目录当时已有修改和两份未跟踪提案/调研。
开发不写主目录，不合并、不推送。依赖来自隔离快照，Python运行库只读复用既有虚拟环境。

初始定向基线：day_plan、revision_contract、wake_reasons、behavior_metrics、风格和文档测试，
`147 passed in 15.46s`。这只证明改造前可运行，不构成本特性完成证据。

## 已裁决的接口与边界

保留 `plan_day`，允许空目标，家族证据只标记已推进。一次活动当日最多支持一个目标，
修订不能抹掉已推进事实或从报告分母删除丢弃目标。时间与约束属于环境，人物不填时间段。
三层报告进入真实 quality 导出；正确的外部约束拒绝记运行受阻，不称引擎缺陷。

## 验收台账

| 要求 | 生产责任 | 验证入口 | 状态 |
| --- | --- | --- | --- |
| 严格目标请求与修订 | schedule、models、engine | 领域与接口测试 | 待执行 |
| 进展与情绪证据 | engine、rhythm | 正时长、同人同日、一次活动、午夜、恢复 | 待执行 |
| 事件节律 | schedule、rhythm、scenario | 威胁精确候选与常规/空闲/睡眠 | 待执行 |
| 人物实际边界 | LifeAgent、projection、narration | 工具到引擎、SQLite与下一观察 | 待执行 |
| 度量与导出 | goal_metrics、behavior_metrics、quality | 三层手算、修订分母、真实export | 待执行 |
| 文档与工程门禁 | 合同、规格、设计及实现 | 文档、全量、覆盖率与风格 | 待执行 |
| 测试有效性 | 干净提交的独立worktree | Uncle Bob语义突变与恢复 | 待执行 |

## 实际修改清单

实施完成后按开发基线的真实 Git diff 填写，包含设计文档。不得以规格中的预估清单代替。
