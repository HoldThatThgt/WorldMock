> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/dayrun-behavior/bob-experiences/final-document-review.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 一日行为文档双向一致性复核

只读审查对象是 `/Users/atishoo/.codex/worktrees/dayrun-behavior/WorldMock`，复核时 HEAD 为 `aa22dc830d6a66d13f14a22a04babae5cb41ec98`。当时工作树仅 `tests/integration/test_activity_schema.py` 有另一会话的未提交改动；本报告没有修改该树。唯一行为权威是 `docs/dev/SPEC-dayrun-behavior.md`。核对了 `docs/CONTRACTS.md`，`docs/design/01,02,03,04,05,07,08,09`，`docs/manual/resources.md`、`scenarios.md`，`docs/INDEX.md`、`VERIFICATION.md`、`docs/dev/implementation-status.md`、`ANALYSIS-spec-coverage.md` 中与本特性相关的接口、资源、共同身体、记录、餐食、音频、记忆与验收描述，并对照实际源码与测试入口。

## 残余冲突

| 位置 | 具体冲突及证据 | 需要同步的内容 |
| --- | --- | --- |
| `docs/dev/ANALYSIS-spec-coverage.md:591` 的 `RC2` | 该现行追溯行称 `activity_progress` 带 `decision_offered/wake_reasons`。冻结规格 `SPEC-dayrun-behavior.md:121-126` 和记录设计 `docs/design/05-recording-engine.md:153-159` 均将这两项唯一放在共同 `body_settlement` 环境事件，活动进度仅以 `body_settlement_event_id` 引用。实际 `src/worldmock/life/engine.py:1611-1623` 的进度载荷没有这两个字段，所引 `tests/life/test_revision_contract.py:82-109` 也改为检查共同身体事件。`covered` 行描述的证据位置错误。 | 把 `RC2` 的字段归属改为共同身体事件，写明活动进度仅引用；保留其指向的现行测试。 |
| `docs/dev/ANALYSIS-spec-coverage.md:182,604` 的 `E4/RC15` | 两行仍指向不存在的 `tests/life/test_money_settlement.py::test_activity_progress_carries_validated_body_flows`，并在 `RC15` 声称身体数值写入 `activity_progress/observation body`。当前真实测试名是 `test_shared_body_event_carries_validated_body_flows`（该文件 :151），身体账在共同事件，`activity_progress` 仅引用并保留自身效果（`SPEC-dayrun-behavior.md:121-126`，`src/worldmock/life/engine.py:1569-1573,1611-1623`）。这使现行 `covered` 映射不可执行，也把公共身体数值归错了事件。 | 两行换成现行测试名；`RC15` 按共同身体事件与观察数值、提示体感分级重新描述。旧结论是否仍 `covered` 应以实际测试收据判定。 |
| `docs/design/07-evaluation.md:101-103` | 报告项定义写 `wait_minutes`、`unknown_wait_minutes`、`content_contact_minutes` 各自为实际区间“并集分钟”，但 `src/worldmock/evaluation/behavior_metrics.py:349-375,378-393` 对同人 wait/watch 进度直接逐条相加，`unknown_wait_minutes` 直接复用全部 wait 结果。该函数的输入是事件字典列表，代码里没有对这三项做并集。对于同人相交的同类进度，设计结果与函数结果不同。`SPEC-dayrun-behavior.md:41-42` 只明确人的**经过时长**必须取并集，并要求等待、未知等待和真实内容接触分别报告。 | 在确定有效导出是否允许同人同类进度相交后统一口径：若设计要求这些指标在输入事件重叠时也取并集，调整实现与回归；若只要求有效活动的自身时长总和，修改这三个设计条目的“并集”措辞。当前审查没有证明正常 LifeEngine 会产生同人重叠 wait/watch，因此这是报告函数与设计定义的条件性偏差，不冒充已发生的正式运行错误。 |

## 已对齐的关键边界

- `docs/CONTRACTS.md:286,537-566`、`docs/design/08-configuration.md:135`、`docs/design/09-io-formats.md:75` 与新规格的活动集合和世界版本 5 一致。先前 `CONTRACTS.md:383`、`docs/design/05-recording-engine.md:549` 的版本 4 现已改成 5。`CONTRACTS.md:1271` 的单活动控制行有明确历史合同说明，不作为当前冲突。
- `docs/design/04-simulation-engine.md:402-410`、`docs/design/05-recording-engine.md:153-162,244-249,699-705` 将共同身体事件、活动自身进度、食品效率和人物时间并集分开，与新规格和实际生活引擎/导出质量报告对应。上述追溯矩阵两处仍须同步。
- `docs/CONTRACTS.md:568-579,976-1009`、`docs/design/05-recording-engine.md:258-295` 把本人最近五条已提交经历、完整截止导出、九种主观记忆类型和四个哈希字段与新规格分开表达；没有发现这组合同之间的残余语义冲突。
- `docs/design/03-resource-engine.md:473-474`、`docs/design/08-configuration.md:170-173,236-237`、`docs/manual/resources.md:205-214,347-355` 与新规格的三家正式餐馆供给、通用空菜单、NASA 786 秒选段、官方锚点完成后揭示及来源条件一致。
- `docs/INDEX.md:57` 登记冻结规格，`docs/VERIFICATION.md:31-38` 设置全部条款同批验收；旧历史快照在 `docs/dev/implementation-status.md:3-6,27-43` 有身份说明。父代理正在填写最终门禁状态，现有“实施中／待验收”仅为待刷新记录，不在本次列为语义冲突。

本报告是内容和可执行映射复核，不声称全仓门禁、真实供应商运行或完整十二周验收完成。

## 最终只读复核（父开发树后续未提交修订）

上方“残余冲突”表保留初次快照的发现，**不再代表当前结论**。对同一父开发树的后续未提交修订只读复核后，三项均已消除；本次未修改父开发树，亦未借绿色测试推断未运行的全仓门禁。

- `docs/dev/ANALYSIS-spec-coverage.md:182,591,604`：E4/RC15 已引用真实存在的 `test_shared_body_event_carries_validated_body_flows`（`tests/life/test_money_settlement.py:151`）；RC2 把 `decision_offered/wake_reasons` 归于共同 `body_settlement` 环境事件；RC15 把公共身体数值归于该事件，活动进度仅保留自身效果及引用。`tests/life/test_revision_contract.py:100-109` 核验提供决策时的共同事件字段。先前两处事件归属错误和失效测试名已修复。
- `docs/design/07-evaluation.md:92,101-103`：现将等待、未知等待、内容接触描述为各活动真实执行区间的**累计**分钟；只有人物经过时间明确取区间并集。这与 `src/worldmock/evaluation/behavior_metrics.py:349-416` 的三项逐事件累计和 `person_elapsed_minutes` 并集实现一致。初次指出的条件性定义偏差已修复。
- 同轮核对 `ANALYSIS-spec-coverage.md:590,593,615`：RC1 现为 17 类，`src/worldmock/life/schedule.py:32-50` 的 17 个 `WakeReason` 与 `tests/life/test_wake_reasons.py:25-39` 冻结列表相同，所引 `test_concrete_wake_reasons_are_preserved` 也存在；RC4 引用现行 `next_shared_body_boundary_seconds`；RC26 的决策自述 disabled/1.0/8000、`reasoning.max_tokens=1500`、无 tools，以及行动 enabled/auto，与 `custom/agents/life_agent.py:44-59,612-637,752-783`、`src/worldmock/life/decision_start.py:26-28` 和 `tests/integration/test_life_agent_protocol.py:591-633` 对齐。RC26 的模型思考内容回传由该集成测试后续断言覆盖。

最终复核范围内没有新的文档语义冲突。父树仍有其他会话的未提交文档与测试改动；门禁状态入口待父代理以其最终全仓收据刷新。本结论仅覆盖上述静态双向一致性，不替代全仓或真实模型验收。
