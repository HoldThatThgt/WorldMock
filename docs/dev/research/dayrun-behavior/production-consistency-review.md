# 一日行为生产边界与文档复审

> 2026-09-21，在未提交的并发工作树审查；基线提交 `74edead`。本页只记录亲自核验的生产路径和现时文档状态，不替代固定源码验收。

## 持久状态恢复

真实 `start_parallel_activities` 的观看加等待状态经 SQLite 提交后，分别篡改观看项的资源声明和目标身份，再经 `LifeEngine.open` 恢复。资源声明篡改被 `WorldState` 的注册表核对拒绝，目标身份篡改被 `LifeEngine._validate_runtime` 拒绝，后者抛出 `IntegrityError: Persisted activity target is invalid`。将 `planned_end_at` 改到 `started_at` 之前也在世界状态校验被拒绝。`WorldState.model_validate` 单独接受任意目标字符串，因为它不持有冻结场景和内容目录；真实恢复路径在 `src/worldmock/life/engine.py:716-734` 引入 `src/worldmock/life/activity_validation.py` 完成该身份核对。审查时曾发现资源声明和目标缺口，核心实现随后补齐，以上为补齐后的真实开门复验。

这次只对观看目标和资源声明做了实际篡改开门；其它活动类型的目标由同一 `validate_activity_targets` 分支处理，但不能把代码分支审阅充当逐类篡改试验。

## 动态动作与共同身体

`custom/agents/life_agent.py` 先绑定单项动作、目标和控制 schema，再把已绑定的子 schema 组成 `start_parallel_activities`；本地递归核对子项，菜单层核对可见性，环境 `src/worldmock/life/engine.py` 最终再次核对菜单与资源占用。审阅 `custom/agents/life_activity_controls.py`、`custom/agents/life_menu.py` 及真实组合工具测试，未找到可复现的人物层与环境层契约分歧。人物观察只作严格结构验证；资源授权以环境为最终防线，符合冻结规格的分层。

使用真实引擎启动观看加步行，以相同两个活动的剩余秒数及活动代谢输入调用 `shared_energy_cost`，再推进到两项结束。预测与人物身体实际扣减均为 `108.568055556` 千卡，差值 `0.0`。`src/worldmock/life/observe.py` 的能量可行性调用同一计算函数，`src/worldmock/life/engine.py` 对共同身体区间只结算一次。该实验核对一个真实并行组合；其它组合由定向测试覆盖。

## 到店终态证据缺陷与复现

在适配旧门店测试时发现一处真实缺陷：`LifeEngine._advance_one_activity` 在 `_apply_activity_effect` 更新 `state.activities` 后，向 `_terminal_for_progress` 传入 effects 前的 `advanced.activity`。真实驾驶到店的终局状态和 `activity_progress` 记录均为 `route_progress_meters=1080.4`，但原始动作的 `completed` 类型 `action_result` 内，顶层和嵌套的活动快照都为 `0.0`。受控复现从正式场景启动、发现门店、真实出行并读取 SQLite 事件，排除了测试只比较不同进度节点的可能。核心实现负责者已改为传递 `state.activities[activity.activity_id]`；`tests/life/test_storefront_arrival.py` 保留终态证据必须与权威终态一致的断言，修复后同目录、菜单和到店三组回归共 `86 passed`。

## 已核验经历与主观记忆

`src/worldmock/life/observe.py` 从已提交事件投影最近五条已核验经历，`custom/agents/life_agent.py` 在人物步骤内冻结这一窗口。`custom/agents/life_memory.py` 仅把主观摘要和主观片段写入可持久化记忆视图，主观片段带 `[subjective]` 标签；环境事实窗口不写入 `MEMORY.md`。`src/worldmock/life/memory_evidence.py` 对更新事件要求主观认知状态。审阅上述生产路径和定向回归后，未发现事实窗口与主观记忆身份混用。

## 文档复审剩余项

此前在 `document-consistency-review.md` 指出的世界状态版本、共同身体记录、记忆证据、提案历史措辞、日运行验收入口与单活动旧测试名，已在当前工作树同步。`docs/INDEX.md` 当前状态栏均使用该页状态词表中的取值；`docs/dev/ANALYSIS-spec-coverage.md:215` 已改成现存测试 `test_browse_while_active_activity_preserves_playback`。新设计、合同和手册的关键名称未发现新的直接冲突。

此复审不证明完整自然日、全仓测试和独立语义变异已通过；对应结果必须以固定源码的验收台账为准。

## 定向核验

```text
AGENTSOCIETY_LLM_API_KEY=test-only MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False LITELLM_LOCAL_MODEL_COST_MAP=True \
  .venv/bin/python -m pytest -q tests/life/test_shared_body.py \
  tests/integration/test_dayrun_parallel_agent.py tests/integration/test_verified_memory.py \
  tests/life/test_parallel_acceptance.py
34 passed in 7.50s

AGENTSOCIETY_LLM_API_KEY=test-only MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False LITELLM_LOCAL_MODEL_COST_MAP=True \
  .venv/bin/python -m pytest -q tests/test_documentation_gate.py
6 passed in 0.03s
```

两个恢复篡改实验和能量对账实验使用临时目录调用真实引擎入口，未写入仓库文件。

```text
AGENTSOCIETY_LLM_API_KEY=test-only MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False LITELLM_LOCAL_MODEL_COST_MAP=True \
  .venv/bin/python -m pytest -q tests/catalog/test_affordance_shadow.py \
  tests/life/test_interaction_menu_runtime.py tests/life/test_storefront_arrival.py
86 passed in 11.90s
```
