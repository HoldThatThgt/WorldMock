<!-- worldmock-doc -->
**Status: FROZEN** | **Owner: worldmock-core**

# WorldMock 验证标准

本文件定义 WorldMock 的测试矩阵、验收门禁和质量标准。

**来源**: 从 `docs/history/agentsociety2-design.md` 提取验证矩阵和交付阶段部分

## 交付阶段与通过条件

| 交付阶段 | 完整结果 | 通过条件 |
| --- | --- | --- |
| 接口验证 | LifeAgent → LifeEnv → 数字足迹 → LabelKit → 一个训练样本与一个规则夹具 | 使用真实 AS2 与 LabelKit 路径；来源关联完整；真实加载器能够读取 |
| 基础生活 | 工作、进食、睡眠及消费，包含拒绝、中断与恢复 | 资源与时间结算正确，人物自主选择，异常不会静默通过 |
| 内容与出行 | 连续节目、游戏、邮件、商品、地点、导航与变化的配送时间 | 内容只在接触后可见，时间依据和缺口明确 |
| 数据生产 | 完整动作、意图和 dreaming 项目，正反规则测试 | 独立语义与结构验收，来源分组、无未来泄漏 |
| 长时间与多人 | 多天、多人物、多活动与故障恢复 | 稳定运行和数据交付均通过；有实际性能与行为分布报告 |

**说明**: 最小端到端阶段包含真实模型选择和真实 LabelKit 加工。手工 JSON、模拟模型响应或仅解析成功不能替代该阶段。每阶段保留可运行路径，随后扩充内容和规模；不为尚未实现的复杂度拆除已验证链路。

**来源**: `docs/history/agentsociety2-design.md:751-760`

## 测试矩阵

### AS2 集成

| 验收事项 | 必须覆盖的案例 | 实际验证入口 |
| --- | --- | --- |
| AS2 接入 | 真实注册、实例化、工具 Schema、人物观察和动作调用 | `tests/integration`、AS2 `tests/test_direct_router.py` 与真实短窗口运行 |

**来源**: `docs/history/agentsociety2-design.md:766`

### 核心生活机制

| 验收事项 | 必须覆盖的案例 | 实际验证入口 |
| --- | --- | --- |
| 时间一致 | 更早结束、多人睡眠、同秒事件、媒体倍速、实验截断 | `test_engine_feedback.py`、`test_activities.py`、AS2 `test_society_decision_round.py` |
| 自主选择 | 拒绝不改写选择、重复观看、反馈后继续或退出 | `test_engine_round.py`、`test_engine_feedback.py`、`test_content_game.py` |
| 资源结算 | 余额、库存、睡眠与食物、部分消费、重复付款 | `test_body.py`、`test_commerce.py`、`test_export_integration.py`、`tests/footprints/test_ledger.py` |
| 工作生产 | 原始材料、独立语义验收、有限协议纠正、返工、套餐、计时与计件 | `test_work_review.py`、`test_work_review_protocol.py`、`test_work_contracts.py` 和真实 DeepSeek 正反工作审阅收据 |
| 内容知识 | 邮件到达、未来字幕隔离、隐藏参考、游戏恢复 | `test_content_game.py`、`tests/corpora/test_corpora.py` |

**来源**: `docs/history/agentsociety2-design.md:767-771`

### 数据完整性

| 验收事项 | 必须覆盖的案例 | 实际验证入口 |
| --- | --- | --- |
| 存储完整 | 素材损坏、事务中断、保存失败、模型未知结果 | `tests/footprints`、`test_life_agent_protocol.py`、AS2 `test_direct_runner_failures.py` |
| 决策协议 | 有限回合错误反馈、原子拒绝、合法 finish、非法引用、完整证据、致命异常不返工 | `test_life_agent_protocol.py`、`test_grounded_evidence.py`、AS2 `test_agent_react_validation.py`、`test_llm_dispatcher_no_retry.py` |
| 共同恢复 | 世界、人物、记忆和游戏一致，坏快照拒绝，父目录不变 | `test_runtime.py`、`test_counterfactual.py`、`test_store.py`、实际 resume/replay |

**来源**: `docs/history/agentsociety2-design.md:772-774`

### 数据生产

| 验收事项 | 必须覆盖的案例 | 实际验证入口 |
| --- | --- | --- |
| LabelKit 输入 | 前置状态、知识截止、活动和自然日边界 | `tests/delivery/test_windows.py`、`test_replay.py`、`tests/footprints/test_validation.py` |
| 语义质量 | 独立 verify、目标盲审、原始事实守恒、失败不发布 | `test_labelkit_process.py`、`test_training.py` 和真实模型 trace |
| 目标数据 | DatasetDict 保存重载、独立规则预期、来源分组 | `test_training.py`、`test_generation.py`、`test_rules.py` 和完整真实 deliver |

**来源**: `docs/history/agentsociety2-design.md:775-777`

### 长期运行

| 验收事项 | 必须覆盖的案例 | 实际验证入口 |
| --- | --- | --- |
| 长期有效性 | 84 天、多人、行为、失败、吞吐、成本和盲评 | `tests/evaluation`、`test_quality.py`、实际十二周 export/quality 与评估报告 |

**来源**: `docs/history/agentsociety2-design.md:778`

### 人物真实性 (来自修订合同)

| 验收事项 | 必须覆盖的案例 | 实际验证入口 |
| --- | --- | --- |
| 决策节律 | 无唤醒条件的边界不调用模型且留痕、每类唤醒条件各触发一次、重估间隔、计划段边界、睡眠不被打断 | `tests/life/test_wake_reasons.py`、`test_engine_feedback.py` |
| 身体语义 | 两过程常数与阈值、睡眠不足账、体感分级以上次决策为参照的滞回、饥饿昼夜因子、社交需要与情绪衰减 | `tests/life/test_sleep_two_process.py`、`test_felt_states.py`、`test_body.py` |
| 观察投影 | 观察不重复序列化、引擎字段不进提示、商品可见期、材料增量、字幕只交付新跨过区间、字符预算超限失败 | `tests/life/test_narration.py`、`tests/integration/test_life_agent_protocol.py` |
| 两阶段决策 | 独白阶段无工具且落库、行动阶段回传 `reasoning_content`、独白进入行动请求、finish 允许空记忆 | `tests/integration/test_life_agent_protocol.py`、AS2 分支 `test_agent_react_validation.py` |
| 社交 | 在场可见、消息送达顺序、联系人回复来源标记、交谈需共同在场、社交需要结算 | `tests/life/test_social.py` |
| 画像编译 | 卡片字段完备、关系与时间锚点、覆盖率不足拒绝、大五与随机浮点不进提示 | `tests/profile/`、`tests/integration/test_persona_profile.py` |
| 行为指标 | 指标可复现、按代码摘要分组、协议门禁阈值、行为分布只报告不裁定 | `tests/evaluation/test_behavior_metrics.py` |

**来源**: `docs/history/agentsociety2-design.md:779-785`

## 验收原则

### 证据层级

1. **短窗口测试**: 与规范双向对应的行为、反例、故障和真实边界测试
2. **测试夹具**: 使用真实场景参数的完整功能测试
3. **语义评审**: 真实模型调用的独立验证
4. **完整十二周交付**: 实际性能与行为分布报告

**说明**: 这些入口规定验收方式；实际通过的源码身份、命令与收据集中记录在 `docs/implementation-status.md`。短窗口、测试夹具、语义评审和完整十二周交付各自记录，不能相互替代。

**来源**: `docs/history/agentsociety2-design.md:787-788`

### 源码质量

生产功能按明确接口与规则规范执行 Uncle Bob 源码突变审查。

审查需要覆盖：
- 改变时钟顺序
- 提前暴露内容
- 重复结算
- 吞掉保存错误
- 丢失来源
- 混合分支

等真实行为破坏。

**来源**: `docs/history/agentsociety2-design.md:790-791`

### 最终报告

最终报告分别列出：
1. 实现证据
2. 结构验证证据
3. 真实模型运行证据
4. 语义质量证据
5. 数据加载证据
6. 规则执行证据

**来源**: `docs/history/agentsociety2-design.md:792`

## 四项协议门禁 (质量标准)

### 1. 接口完整性门禁

**要求**:
- 所有 LifeEnv 工具必须有对应的 Pydantic 请求模型
- 工具 Schema 从实际请求模型直接生成
- 模型只生成业务参数，宿主附加身份、决策与动作标识
- 数组数量边界由同一模型的本地校验器完整执行，不能静默截取

**验证**: `tests/integration` 全部通过

**来源**: `docs/history/agentsociety2-design.md:195-197`

### 2. 数据一致性门禁

**要求**:
- 一次生活状态变化、资金结算、动作状态与对应结果事件在同一个 SQLite 事务提交
- 素材先写临时文件、完成持久化和哈希核对，再发布为稳定引用
- 数据库只能提交指向完整素材的记录
- 权威资金变化只位于 `Event.data.resource_changes` 顶层，全部以整数分表示
- 完整导出必须能够仅从初始注资与后续权威资金事件重建最终共同检查点账户

**验证**: `tests/footprints/test_ledger.py` 全部通过，实际 export 可对账

**来源**: `docs/history/agentsociety2-design.md:533-547, 885-888`

### 3. 失败传播门禁

**要求**:
- 数据库写失败、来源素材损坏、模型响应无法可靠留证、必要观察缺失，或 ReAct 预算耗尽仍未形成合法结束结果，都是运行故障
- 这类故障停止运行并保存已确认部分
- 致命运行故障与普通业务拒绝采用不同的错误类型
- 故障路径记录英文错误与关联事件，不允许被通用异常捕获转换成"工具失败后让模型继续猜测"
- 必要记忆归纳或写盘失败属于运行故障
- 不能由框架补写一个"完成"结果或强制转换错误记忆，再让 runner 报告成功

**验证**: AS2 `test_direct_runner_failures.py` 全部通过，`test_life_agent_protocol.py` 覆盖致命异常不返工

**来源**: `docs/history/agentsociety2-design.md:539-547`

### 4. 来源可追溯门禁

**要求**:
- 每个导出保存 `sample_id → 原始 event_id`
- 逐事件导出同时保存输入文件、行号、人物和出现位置
- 后处理只复制已存在的事实并检查关联，不通过模型生成新的事件标识
- sequence generation 的新事件属于生成分支，不能直接继承为已经发生的原始事件
- 相同原始内容、人物运行、恢复分支和 dreaming 派生数据按关联组划分训练、验证和测试

**验证**: `tests/delivery/test_windows.py`、`test_replay.py`、`tests/footprints/test_validation.py` 全部通过，完整真实 deliver 清单包含来源分组

**来源**: `docs/history/agentsociety2-design.md:667-714`

## 行为质量标准 (Report-Only)

以下指标用于运行后评估，不作为强制门禁：

| 指标 | 说明 | 验证入口 |
| --- | --- | --- |
| 自主决策率 | 人物实际做出选择的比例 vs 环境代为续行的比例 | `tests/evaluation/test_behavior_metrics.py` |
| 唤醒原因分布 | 各类唤醒条件触发次数 | `tests/life/test_wake_reasons.py` |
| 等待与就寝 | 人物选择等待和就寝的频率与时长 | `test_body.py` |
| 进食多样性 | 不同食品的选择分布 | `test_commerce.py` |
| 活动分布距离 | 与外部参照（ATUS）的距离 | `tests/evaluation/` |
| 人格可区分性 | 不同人格的人物行为是否可区分 | `tests/profile/` |
| 记忆重复率 | 记忆中重复内容的比例 | AS2 记忆测试 |

**说明**: 这些统计用于运行后评估；正式实验期间不根据报告暗中修改提示或替人物选择行动。行为分布只报告不裁定。

**来源**: `docs/history/agentsociety2-design.md:374-375, 926`

## 验收边界

数据来源真实不代表数字人行为已经符合人类分布。行为真实性需要独立样本、参数校准与人工复核。

本设计不以程序运行时长或 JSON 能被解析作为最终成功条件。

**来源**: `docs/history/agentsociety2-design.md:49-50`
