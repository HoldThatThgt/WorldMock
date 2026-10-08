> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/decision-context-verification.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 决策上下文特性验收记录

> 日期：2026-09-20。规范：[冻结特性规格](../SPEC-decision-context.md) 与 [接口合同](../../CONTRACTS.md)。
> 验收源码：WorldMock `f8a55e21887b686b7432093f7e8c950e3f1fdf56`；
> AgentSociety2 `e41550957a50398e08570f10ecd9cdb3fc9dd37c`。
> 工作区：`codex/decision-context` 隔离分支。最终文档提交不改变上述生产源码。

## 范围与结论

本次特性覆盖决策自述、冻结上下文、动作登记与结算结果的独立通道、场景信息和全部提示预算。
研究、规格、实现和行为测试使用同一条款；没有延期实现、兼容别名、迁移或新增依赖。
正式提供商请求、十二周实验、三次隔离盲评和完整自然日交付属于项目既有真实运行门禁，
本次没有执行，不用受控模型响应的代码测试替代这些证据。

## 验收结果

| 验收 | 结果 | 原始证据 |
| --- | --- | --- |
| 根全量测试与覆盖率门禁 | **2871 passed，零跳过**；238.94 秒；分支覆盖率 **98.82%**，5872 条已覆盖、70 条未覆盖，阈值 85%；未覆盖公开接口 **0** | `.work/decision-context/coverage-gate-final.log` |
| AS2 全量测试 | **448 passed**；固定于上述 AS2 子模块提交 | `.work/decision-context/as2-tests-configured.log` |
| 源码风格与文档路径测试 | **7 passed** | 同一工作区实际命令 |
| 文档路径门禁 | **41 份文档，0 处失效引用**；最终报告加入后重新执行 | `scripts/documentation_gate.py` |
| 独立文档语义复核 | 初核 15 份文档、124 个相对链接及锚点有效；收口复核 8 份文档、27 个相对链接及锚点有效，测试名均可解析；三处过期测试引用已修正；字段、时序与预算一致 | 当前规范、提案、研究记录及下游说明 |
| 独立源码语义突变 | **60 个不同语义突变最终全部被检出**；64 次执行中 63 killed，初次窄命令 1 survived 已由既有边界测试补充关闭；未关闭缺口 0 | 两份独立 Bob 报告 |

## 条款与可观察证据

| 条款 | 生产路径与验证重点 | 行为测试 |
| --- | --- | --- |
| 唯一命名与自述协议 | 原生 AS2 钩子、明确生成要求、实际预算内重试、事件类型与模型清单 | `test_decision_context_protocol.py`、`test_life_agent_protocol.py`、AS2 `test_worldmock_life_hooks.py`、`test_event_fields.py` |
| 旧运行拒绝 | 新建及 `open_existing`、`restore`、`from_workspace`、`LifeEngine.open` 的真实生命周期；旧阶段、混合阶段、缺失和畸形身份均拒绝 | `test_run_manifest.py`、`test_restored_model_identity.py`、`test_runtime.py` |
| 单次观察 | 原生 `PersonAgent.step` 在正常、自述重试、行动纠错路径均仅观察一次；轮内 user 固定，完整消息历史正常增长 | `test_life_agent_protocol.py::test_person_step_records_native_finish_memory_update`、`test_decision_context_protocol.py` |
| 原生协议与回执隔离 | 实际后续请求不携带宿主回执字段；保留配对身份、推理内容、即时记忆结果与宿主审计证据 | `test_decision_context_protocol.py::test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user`、AS2 钩子测试 |
| 阶段工具与只读 | 动作前无 finish，动作后无生活工具；只读仅人物卡、记忆和问题，禁止世界或记忆写入 | `test_decision_context_protocol.py`、`test_life_agent_protocol.py` |
| 批量结算与下一观察 | 真实 LifeAgent、LifeEnv、SQLite 跨两轮；提交不提前回流，工作评审准备与结果公开分离 | `test_decision_context_roundtrip.py`、`test_engine_round.py`、`test_work_review.py` |
| 全量公开结果 | 七条实际结算仍完整有序、仅回灌一次；原因尾部与公开工作判断完整；拒绝私有字段和超预算结果 | `test_decision_context_projection.py`、`test_work_review.py` |
| 理由文本边界 | 两类理由在真实解析至提示路径不得含内部参考身份或摘要；直接构造和重读亦受约束；合法长理由不截断；纠错与耗尽保留证据 | `test_work_review.py` |
| 场景与收件箱 | 时间及身份稳定排序、最近三条、真正未展示未读提示、完整收件事实；营业信息、暂停进度与独立待办 | `test_decision_context_projection.py`、`test_narration.py`、`test_work_review.py` |
| 所有提示预算 | user、追加预算块后的 system、场景、生活及只读人物卡的边界与超限；失败留英文日志，不截断 | `test_decision_context_protocol.py`、`test_decision_context_projection.py`、`test_narration.py` |
| 下游与证据一致 | 新事件字段类型、足迹质量计数、行为指标和 dreaming 窗口；动作与意图窗口继续排除私有自述 | `tests/footprints/`、`tests/delivery/test_windows.py`、`tests/evaluation/test_behavior_metrics.py` |
| 文档一致 | 提案转为已裁决记录；规范字段、预算、时序、真实运行边界及链接一致 | 文档路径门禁与独立语义复核 |

测试文件的完整路径列在下方修改清单；AS2 路径属于对应子模块。

## 独立审查修复

| 审查反例 | 复现 | 最终修复 |
| --- | --- | --- |
| 自由文本绕过键名黑名单 | 合法评审理由含内部参考身份或摘要，解析后进入事实提示 | 解析及公开投影共用确定性验证，沿用已有纠错和证据保存路径 |
| 恢复入口接受旧阶段 | 三个正式环境生命周期及直接引擎打开的反例修复前均失败 | 统一打开入口验证当前阶段身份，明确缺失身份不能当作直驱 null |
| 重复环境观察未被测试检测 | 运行期连续观察两次时原生步骤旧测试仍通过 | 三种真实步骤路径统计观察上下文并精确断言一次 |

源码突变审查结果见 [人物协议与预算报告](decision-context-bob-protocol.md) 和 [投影、证据与恢复报告](decision-context-bob-projection.md)。

## 并发隔离与产物

原工作区保持 `main` 的 `426af3bfe3d7e507b749f286e08857e697c2ac2f` 与原有三个文档改动，
本次没有覆盖其源码、工作区环境或未提交提案。AS2 改动独立提交并通过根仓 gitlink 固定。
原 AS2 工作区仅增加本地提交引用供分支检出，原 HEAD 和文件未变化。
历史锚定数据库通过 SQLite 只读连接复制一致快照，用于执行原先缺少产物的四项测试；没有修改原运行数据。

`PROPOSAL-category-contract.md` 是原工作区未跟踪输入的原样保留，不属于此次功能开发或通过声明。
实际功能改动相对于起点的文件如下；最终验收文档另列在文末。

```text
custom/agents/life_agent.py
custom/agents/life_context.py
docs/CONTRACTS.md
docs/INDEX.md
docs/VERIFICATION.md
docs/design/04-simulation-engine.md
docs/design/05-recording-engine.md
docs/design/06-delivery.md
docs/dev/ANALYSIS-spec-coverage.md
docs/dev/PROPOSAL-category-contract.md
docs/dev/PROPOSAL-decision-context.md
docs/dev/SPEC-as2-integration.md
docs/dev/SPEC-decision-context.md
docs/dev/SPEC-labelkit-delivery.md
docs/dev/SPEC-life-realism.md
docs/dev/SPEC-life-runtime.md
docs/dev/implementation-status.md
docs/dev/research/decision-context-feasibility.md
src/worldmock/delivery/windows.py
src/worldmock/evaluation/behavior_metrics.py
src/worldmock/footprints/models.py
src/worldmock/footprints/quality.py
src/worldmock/life/decision_start.py
src/worldmock/life/engine.py
src/worldmock/life/models.py
src/worldmock/life/narration.py
src/worldmock/life/observe.py
src/worldmock/life/projection.py
src/worldmock/life/result_projection.py
src/worldmock/life/work_review.py
src/worldmock/runtime.py
src/worldmock/validation.py
tests/delivery/test_windows.py
tests/footprints/test_event_fields.py
tests/footprints/test_quality.py
tests/footprints/test_validation.py
tests/integration/test_agent_branch_coverage.py
tests/integration/test_decision_context_protocol.py
tests/integration/test_decision_context_roundtrip.py
tests/integration/test_grounded_targets.py
tests/integration/test_host_branch_coverage.py
tests/integration/test_life_agent_protocol.py
tests/integration/test_run_manifest.py
tests/integration/test_runtime.py
tests/life/test_decision_context_projection.py
tests/life/test_engine_branch_coverage.py
tests/life/test_engine_feedback.py
tests/life/test_engine_round.py
tests/life/test_grounded_observation.py
tests/life/test_narration.py
tests/life/test_restored_model_identity.py
tests/life/test_revision_contract.py
tests/life/test_work_review.py
tests/test_style_gate.py
vendor/agentsociety2
```

AS2 子模块实际修改：

```text
packages/agentsociety2/agentsociety2/agent/base/agent_runtime.py
packages/agentsociety2/tests/test_worldmock_life_hooks.py
```

最终新增验收文档：

```text
docs/dev/research/decision-context-verification.md
docs/dev/research/decision-context-bob-protocol.md
docs/dev/research/decision-context-bob-projection.md
```

索引、实现状态和追溯矩阵同步指向这些证据。

## 可复跑命令

从本工作区执行，根和 LabelKit 各自使用 `uv sync --frozen --offline` 安装的独立环境。
关闭遥测与在线模型价格表查询；所有测试使用受控模型响应，没有发送真实模型请求。

```sh
MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False LITELLM_LOCAL_MODEL_COST_MAP=True \
  uv run --no-sync python scripts/coverage_gate.py
uv run --no-sync pytest -q tests/test_style_gate.py tests/test_documentation_gate.py
uv run --no-sync python scripts/documentation_gate.py
WORKSPACE_PATH="$PWD" MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False \
  LITELLM_LOCAL_MODEL_COST_MAP=True uv run --no-sync pytest -q \
  vendor/agentsociety2/packages/agentsociety2/tests
```

原始门禁日志位于任务工作区 `.work/decision-context/`；Bob 报告逐项列明隔离基线、命令、反例与恢复证据。
