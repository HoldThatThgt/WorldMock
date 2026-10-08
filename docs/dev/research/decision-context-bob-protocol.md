> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/decision-context-bob-protocol.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# Bob report

状态：**COMPLETE**。当前审查范围没有未关闭的测试缺口，也没有发现实现缺失或规范偏离。

调用者仓库：`/Users/atishoo/.codex/worktrees/decision-context/WorldMock`。
基线提交：`f8a55e21887b686b7432093f7e8c950e3f1fdf56`；AS2 gitlink：`e41550957a50398e08570f10ecd9cdb3fc9dd37c`。
权威规范：`docs/dev/SPEC-decision-context.md`，及直接依赖的 `docs/CONTRACTS.md`。

审查条款限定为：请求总回合与重试（规格 28–29、61–64 行），新命名钩子的真实调用（51–57 行），自述/行动上下文与一次观察（69–76 行），只读入口（78–80 行），登记语义、回执双通道清除、提供商身份与即时记忆（84–89 行），各入口提示预算（122–124 行）；合同依据为 197、376–392、514–554、571 行。

基线还运行真实 LifeAgent、LifeEnv 和 SQLite 的跨轮测试。该运行只替换模型传输，未把模拟模型响应当成真实提供商可用性证据。

原始日志和逐次差异保存在本任务工作区 `.work/decision-context/bob-protocol/`；下列证据文件名相对于该目录。

## 计数与结论

| 项目 | 结果 |
| --- | --- |
| 已映射的可观察要求与不同语义破坏点 | 31 |
| 实现缺失 / 实现偏离 | 0 / 0 |
| 不同源码突变 | 31 |
| 突变执行次数 | 35 |
| killed 执行 | 34 |
| survived 执行 | 1；初次窄命令的历史结果，补充审查已关闭 |
| invalid / inconclusive | 0 / 0 |
| 最终仍无检测证据的不同突变 | 0 |

所有 31 个不同突变都有现有测试检测的证据。保留最初的 survived 结果，不把后续选择更完整测试集的结果追溯改写到首次执行。未对整个特性规范的每句话开展穷尽突变，因此不报告规范覆盖百分比或突变得分。

## 隔离和基线

先确认调用者完整 `git status --porcelain=v1 --untracked-files=all` 为空，再以 `mktemp -d /tmp/worldmock-bob-protocol.XXXXXX` 返回的明确路径建立 detached 工作树。所有基线和源码突变只在 `/tmp/worldmock-bob-protocol.j2GLrV` 执行；AS2 从调用者子仓本地克隆并检出固定 gitlink。

导入验证确认 `worldmock`、`custom.agents.life_agent`、`agentsociety2` 的 `__file__` 全部位于该临时工作树。共享的只是调用者虚拟环境中的依赖。

所有命令的工作目录为隔离工作树，解释器为：

```text
/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python
```

所有命令使用下列显式环境覆盖。初始化导入曾提示缺少模型 key，因此在正式基线前添加假 key 和未使用的 loopback 地址；没有真实模型请求。

```text
PYTHONPATH=/tmp/worldmock-bob-protocol.j2GLrV/src:/tmp/worldmock-bob-protocol.j2GLrV:/tmp/worldmock-bob-protocol.j2GLrV/vendor/agentsociety2/packages/agentsociety2
WORKSPACE_PATH=/tmp/worldmock-bob-protocol.j2GLrV
MEM0_TELEMETRY=False
ANONYMIZED_TELEMETRY=False
LITELLM_LOCAL_MODEL_COST_MAP=True
PYTHONDONTWRITEBYTECODE=1
AGENTSOCIETY_LLM_API_KEY=bob-offline-test
AGENTSOCIETY_LLM_API_BASE=http://127.0.0.1:9/v1
```

初始根测试基线：**112 passed**；原生 AS2 钩子基线：**30 passed**。补充的场景命令：**8 passed**；原生明确断言补充命令分别 **1 passed** 和 **1 passed**。各命令均先在未突变源码通过，随后才执行其对应突变。

```sh
/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py tests/integration/test_life_agent_protocol.py tests/integration/test_decision_context_roundtrip.py tests/life/test_narration.py --tb=short
/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q vendor/agentsociety2/packages/agentsociety2/tests/test_worldmock_life_hooks.py --tb=short
```

原始基线日志：`baseline-1.log`、`baseline-2.log`、`supplement-scene-baseline.log`、`assertions-baseline-native.log`、`assertions-baseline-reserve.log`。

## survived 的原因与关闭证据

`scene-over-by-one-accepted` 把场景上限比较改为允许多一个汉字。最初的 `tests/life/test_narration.py::TestRenderScene` 七项全部通过；同一突变源码的独立诊断接受了 801 汉字。初次命令选集确实不足以检测这项破坏。

继续检索发现调用者原本已有 `tests/life/test_decision_context_projection.py::test_all_render_budget_failures_emit_english_error_logs[scene]`，其输入恰为 801 汉字并断言必须抛错。这说明遗漏发生在本次审查选择的命令，不能据此声称仓库缺少测试。

补充轮独立预声明包含该既有用例的命令，正常源码先取得 8 passed，再应用完全相同的突变，得到 1 failed、7 passed，因果为 `DID NOT RAISE`。没有编辑测试或实现。证据：`scene-over-by-one-accepted.log`、`scene-over-by-one-accepted-diagnostic.log`、`supplement-predeclared-plan.json`、`supplement-scene-mutation.log`。

## 逐次突变台账

下表的 `SPEC` 指上述特性规格，`CONTRACTS` 指接口合同；行号来自固定基线提交。每项只改变一个生产行为，不修改测试、规范或依赖。每项的 Python 语法检查通过；失败由业务观察、提示正文、工具集合、预算或错误日志断言触发。

每行列出完整预声明 pytest 命令。命令中的解释器、工作目录和环境与基线相同。完整替换前后源码和还原凭证保存在 `reviewed-ledger.json`。

| 突变名称 / 规范条款 | 生产位置与变化 | 预声明命令 | 结果 / 因果证据 |
| --- | --- | --- | --- |
| `generation-instruction-removed`<br>SPEC:61-64 | `custom/agents/life_agent.py:504`<br>`请用一至三句第一人称中文，说出你此刻的感受和打算，最多二百汉字。` → `请说明当前情况。` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_decision_statement_request_explicitly_describes_generation_contract --tb=short` | **killed**, exit 1<br>真实自述请求的第一人称中文、句数、感受打算、汉字上限短语断言失败。 `generation-instruction-removed.log` |
| `statement-thinking-enabled`<br>SPEC:63; CONTRACTS:550 | `custom/agents/life_agent.py:734`<br>`"extra_body": {"thinking": {"type": "disabled"}},` → `"extra_body": {"thinking": {"type": "enabled"}},` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_decision_statement_request_explicitly_describes_generation_contract --tb=short` | **killed**, exit 1<br>请求 extra_body 的 thinking.type 实际 enabled，期望 disabled。 `statement-thinking-enabled.log` |
| `statement-token-budget-changed`<br>SPEC:63; CONTRACTS:550 | `custom/agents/life_agent.py:39`<br>`DECISION_STATEMENT_MAX_TOKENS = 400` → `DECISION_STATEMENT_MAX_TOKENS = 401` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_decision_statement_request_explicitly_describes_generation_contract --tb=short` | **killed**, exit 1<br>请求 max_tokens 实际 401，期望 400。 `statement-token-budget-changed.log` |
| `empty-statement-accepted`<br>SPEC:61-64 | `src/worldmock/life/narration.py:353`<br>`if not text or not text.strip():` → `if False:` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/life/test_narration.py::TestDecisionStatement::test_empty_string_raises tests/life/test_narration.py::TestDecisionStatement::test_whitespace_only_raises --tb=short` | **killed**, exit 1<br>空串与纯空白均未抛 DecisionStatementValidationError，两个 raises 断言失败。 `empty-statement-accepted.log` |
| `statement-over-by-one-accepted`<br>SPEC:61-64 | `src/worldmock/life/narration.py:357`<br>`if hanzi > DECISION_STATEMENT_HANZI_BUDGET:` → `if hanzi > DECISION_STATEMENT_HANZI_BUDGET + 1:` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/life/test_narration.py::TestDecisionStatement::test_201_hanzi_raises --tb=short` | **killed**, exit 1<br>201 汉字未抛协议错误，raises 断言失败。 `statement-over-by-one-accepted.log` |
| `statement-at-limit-rejected`<br>SPEC:61-64 | `src/worldmock/life/narration.py:357`<br>`if hanzi > DECISION_STATEMENT_HANZI_BUDGET:` → `if hanzi >= DECISION_STATEMENT_HANZI_BUDGET:` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/life/test_narration.py::TestDecisionStatement::test_exactly_200_hanzi_passes --tb=short` | **killed**, exit 1<br>本应接受的 200 汉字被拒绝，原文返回断言无法通过。 `statement-at-limit-rejected.log` |
| `statement-retry-removed`<br>SPEC:42,64 | `custom/agents/life_agent.py:755`<br>`for _attempt in (1, 2):` → `for _attempt in (1,):` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_invalid_decision_statement_retries_once_with_same_context --tb=short` | **killed**, exit 1<br>空或超长自述首次失败后提前终止，本应第二次成功并提交动作的三个用例失败。 `statement-retry-removed.log` |
| `statement-retry-reserve-weakened`<br>SPEC:28-29,64 | `custom/agents/life_agent.py:756`<br>`self._life_require_turns("decision_statement", 3)` → `self._life_require_turns("decision_statement", 2)` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_decision_statement_retry_requires_remaining_action_and_finish_turns --tb=short` | **killed**, exit 1<br>本应在剩余两回合时拒绝重试，却额外访问脚本模型并耗尽响应；补充轮另有明确预算异常断言。 `statement-retry-reserve-weakened.log` |
| `renamed-statement-hook-skipped`<br>SPEC:51-57,63 | `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/base/agent_runtime.py:525`<br>`await self._run_prelude_decision_statement(request)` → `pass` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q vendor/agentsociety2/packages/agentsociety2/tests/test_worldmock_life_hooks.py::test_prelude_decision_statement_hook_runs_one_toolless_call --tb=short` | **killed**, exit 1<br>原生循环仍可运行到 finish，但 receive 钩子记录为空，期望接收到中文自述。 `renamed-statement-hook-skipped.log` |
| `environment-observed-twice`<br>SPEC:71-75,148 | `custom/agents/life_agent.py:542`<br>`return await self._env.observe(self.require_decision_context())` → `await self._env.observe(self.require_decision_context()) ⏎         return await self._env.observe(self.require_decision_context())` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_life_agent_protocol.py::test_person_step_records_native_finish_memory_update --tb=short` | **killed**, exit 1<br>原生 PersonAgent.step 的正常、自述重试、行动纠错路径均观察两次，精确上下文列表断言失败。 `environment-observed-twice.log` |
| `initial-user-changes-between-turns`<br>SPEC:71-72; CONTRACTS:527 | `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/base/agent_runtime.py:832`<br>`return list(self._react_messages)` → `self._react_messages[1]["content"] += " " ⏎         return list(self._react_messages)` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user --tb=short` | **killed**, exit 1<br>真实后续请求的初始 user 编码字节不再全部相等。 `initial-user-changes-between-turns.log` |
| `statement-exposes-facts`<br>SPEC:69-72 | `custom/agents/life_agent.py:722`<br>`user = self._life_user_sections(request, with_facts=False)` → `user = self._life_user_sections(request, with_facts=True)` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user --tb=short` | **killed**, exit 1<br>真实自述 user 出现 tasks 事实字段，禁止事实进入自述的断言失败。 `statement-exposes-facts.log` |
| `action-omits-statement`<br>SPEC:70-72; CONTRACTS:523 | `custom/agents/life_agent.py:685`<br>`if decision_statement: ⏎             parts.append(f"你刚才心里想的是：{decision_statement}")` → `if False: ⏎             parts.append(f"你刚才心里想的是：{decision_statement}")` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_decision_statement_request_explicitly_describes_generation_contract --tb=short` | **killed**, exit 1<br>行动 user 缺少已经成功生成的 200 汉字自述，正文包含断言失败。 `action-omits-statement.log` |
| `finish-exposed-before-action`<br>SPEC:43,69,149 | `custom/agents/life_agent.py:643`<br>`return {**self._life_action_models, **memory}` → `return {**self._life_action_models, **memory, "finish": FinishRequest}` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user --tb=short` | **killed**, exit 1<br>动作前请求 tools 含 finish，与阶段布尔断言冲突。 `finish-exposed-before-action.log` |
| `life-actions-retained-after-action`<br>SPEC:43,69,149 | `custom/agents/life_agent.py:640`<br>`return {**memory, "finish": FinishRequest}` → `return {**self._life_action_models, **memory, "finish": FinishRequest}` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user --tb=short` | **killed**, exit 1<br>动作后请求 tools 仍与生活动作集合相交。 `life-actions-retained-after-action.log` |
| `scene-over-by-one-accepted`<br>SPEC:122-124; CONTRACTS:386 | `src/worldmock/life/narration.py:276`<br>`if hanzi > SCENE_HANZI_BUDGET:` → `if hanzi > SCENE_HANZI_BUDGET + 1:` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/life/test_narration.py::TestRenderScene --tb=short` | **survived**, exit 0<br>最初所选 TestRenderScene 的七个用例全过；同一源码诊断证实接受 801 汉字。此结果仅描述该命令选集。 `scene-over-by-one-accepted.log` |
| `receipt-observation-not-cleared`<br>SPEC:84-88 | `custom/agents/life_agent.py:958`<br>`result = ReactToolResult(True, _ACTION_REGISTRATION_CONFIRMATION, {})` → `result = ReactToolResult(True, result.observation, {})` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user --tb=short` | **killed**, exit 1<br>实际 tool 正文 observation 带内部身份与摘要，完整固定确认结构断言失败。 `receipt-observation-not-cleared.log` |
| `receipt-data-not-cleared`<br>SPEC:84-88 | `custom/agents/life_agent.py:958`<br>`result = ReactToolResult(True, _ACTION_REGISTRATION_CONFIRMATION, {})` → `result = ReactToolResult(True, _ACTION_REGISTRATION_CONFIRMATION, result.data)` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user --tb=short` | **killed**, exit 1<br>实际 tool 正文 data 保留内部回执字典，期望空字典的结构断言失败。 `receipt-data-not-cleared.log` |
| `provider-tool-call-id-changed`<br>SPEC:87; CONTRACTS:540 | `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/base/agent_runtime.py:703`<br>`"tool_call_id": decision.tool_call_id,` → `"tool_call_id": "changed-provider-identity",` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user --tb=short` | **killed**, exit 1<br>后续消息无法找到原生 provider-action-call；补充轮直接比较实际 tool_call_id。 `provider-tool-call-id-changed.log` |
| `provider-reasoning-dropped`<br>SPEC:53,146; CONTRACTS:571 | `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/base/agent_runtime.py:876`<br>`assistant["reasoning_content"] = reasoning` → `assistant["reasoning_content"] = ""` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q vendor/agentsociety2/packages/agentsociety2/tests/test_worldmock_life_hooks.py::test_reasoning_content_survives_into_followup_request vendor/agentsociety2/packages/agentsociety2/tests/test_worldmock_life_hooks.py::test_reasoning_content_survives_protocol_feedback_request --tb=short` | **killed**, exit 1<br>正常后续与纠错后续两条路径的 reasoning_content 均变为空串，精确正文断言失败。 `provider-reasoning-dropped.log` |
| `immediate-memory-result-cleared`<br>SPEC:89,147 | `custom/agents/life_agent.py:957`<br>`if decision.action in REQUEST_MODELS: ⏎             result = ReactToolResult` → `if decision.action in REQUEST_MODELS or decision.action.startswith("memory_"): ⏎             result = ReactToolResult` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user --tb=short` | **killed**, exit 1<br>记忆返回的 episodes 消失，现有读取断言失败；补充轮直接断言后续请求中的记忆身份。 `immediate-memory-result-cleared.log` |
| `readonly-question-omitted`<br>SPEC:78-80 | `custom/agents/life_agent.py:698`<br>`if request.question is not None:` → `if False:` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_readonly_loop_uses_only_card_memory_and_question --tb=short` | **killed**, exit 1<br>只读初始 user 缺少外部问题，与完整文字期望不一致。 `readonly-question-omitted.log` |
| `readonly-finish-creates-memory`<br>SPEC:78-80 | `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/base/agent_runtime.py:719`<br>`if readonly: ⏎             return decision.final` → `if readonly: ⏎             self._last_finish_memories = [] ⏎             return decision.final` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_readonly_loop_uses_only_card_memory_and_question --tb=short` | **killed**, exit 1<br>只读 finish 把 _last_finish_memories 设成列表，与必须为 None 的断言冲突。 `readonly-finish-creates-memory.log` |
| `readonly-exposes-life-action`<br>SPEC:78-80 | `custom/agents/life_agent.py:638`<br>`return {**memory, "finish": ReadonlyFinishRequest}` → `return {**memory, "start_basic_activity": REQUEST_MODELS["start_basic_activity"], "finish": ReadonlyFinishRequest}` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_life_agent_protocol.py::test_readonly_schema_uses_its_independent_finish_contract --tb=short` | **killed**, exit 1<br>只读工具集合多出 start_basic_activity，严格闭集断言失败。 `readonly-exposes-life-action.log` |
| `readonly-card-budget-bypassed`<br>SPEC:80,122-124 | `custom/agents/life_agent.py:696`<br>`sections = [compose_user_message( ⏎                 self._persona_card_text(), "", self._memory_prompt_section())]` → `sections = ["\n\n".join((self._persona_card_text(), self._memory_prompt_section()))]` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_persona_card_budget_applies_to_life_and_readonly_requests -k True --tb=short` | **killed**, exit 1<br>只读 601 汉字人物卡未抛 RenderBudgetExceededError；600 汉字用例仍通过。 `readonly-card-budget-bypassed.log` |
| `persona-over-by-one-accepted`<br>SPEC:80,122-124 | `src/worldmock/life/narration.py:456`<br>`if hanzi > PERSONA_CARD_HANZI_BUDGET:` → `if hanzi > PERSONA_CARD_HANZI_BUDGET + 1:` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_persona_card_budget_applies_to_life_and_readonly_requests --tb=short` | **killed**, exit 1<br>生活和只读两个入口的 601 汉字均未抛异常；600 汉字边界仍通过。 `persona-over-by-one-accepted.log` |
| `user-over-by-one-accepted`<br>SPEC:122-124; CONTRACTS:384 | `src/worldmock/life/narration.py:329`<br>`if user_message_len > USER_MESSAGE_CHAR_BUDGET:` → `if user_message_len > USER_MESSAGE_CHAR_BUDGET + 1:` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_user_budget_is_enforced_at_every_model_entry --tb=short` | **killed**, exit 1<br>自述把超限请求发出，行动和只读入口也未拒绝 40001 字符；三个对应断言失败。 `user-over-by-one-accepted.log` |
| `system-over-by-one-accepted`<br>SPEC:122-124; CONTRACTS:385 | `src/worldmock/life/narration.py:335`<br>`if system_message_len > SYSTEM_MESSAGE_CHAR_BUDGET:` → `if system_message_len > SYSTEM_MESSAGE_CHAR_BUDGET + 1:` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_system_budget_includes_appended_generation_and_turn_requirements --tb=short` | **killed**, exit 1<br>自述把超限请求发出，行动和只读入口也未拒绝 3001 字符；三个对应断言失败。 `system-over-by-one-accepted.log` |
| `final-system-budget-check-skipped`<br>SPEC:122-124 | `custom/agents/life_agent.py:597`<br>`enforce_budgets(0, len(first["content"]))` → `enforce_budgets(0, 0)` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_system_budget_includes_appended_generation_and_turn_requirements --tb=short` | **killed**, exit 1<br>行动/只读追加预算块后的超限 system 未拒绝；自述边界引出的后续行动超限也未拒绝。 `final-system-budget-check-skipped.log` |
| `transport-failure-error-log-removed`<br>SPEC:64 | `custom/agents/life_agent.py:781`<br>`self._logger.error("Decision statement transport failed: %s", exc)` → `self._logger.info("Decision statement transport failed: %s", exc)` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_decision_context_protocol.py::test_decision_statement_transport_and_evidence_failures_are_logged_without_retry -k transport --tb=short` | **killed**, exit 1<br>传输故障仍抛出且未重试，但错误日志列表为空，日志存在断言失败。 `transport-failure-error-log-removed.log` |
| `scene-over-by-one-existing-projection-test`<br>SPEC:122-124; CONTRACTS:386 | `src/worldmock/life/narration.py:276`<br>`if hanzi > SCENE_HANZI_BUDGET:` → `if hanzi > SCENE_HANZI_BUDGET + 1:` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/life/test_narration.py::TestRenderScene 'tests/life/test_decision_context_projection.py::test_all_render_budget_failures_emit_english_error_logs[scene]' --tb=short` | **killed**, exit 1<br>既有 projection 测试的 801 汉字输入未抛 RenderBudgetExceededError，明确 raises 断言杀死同一突变。 `supplement-scene-mutation.log` |
| `provider-tool-call-id-changed-explicit-assertion`<br>SPEC:87; CONTRACTS:540 | `vendor/agentsociety2/packages/agentsociety2/agentsociety2/agent/base/agent_runtime.py:703`<br>`"tool_call_id": decision.tool_call_id,` → `"tool_call_id": "changed-provider-identity",` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_life_agent_protocol.py::test_native_react_submits_one_host_bound_life_action --tb=short` | **killed**, exit 1<br>实际 changed-provider-identity 不等于 call-action，原生后续请求配对身份断言失败。 `provider-tool-call-id-changed-explicit-assertion.log` |
| `immediate-memory-result-cleared-explicit-assertion`<br>SPEC:89,147 | `custom/agents/life_agent.py:957`<br>`if decision.action in REQUEST_MODELS: ⏎             result = ReactToolResult` → `if decision.action in REQUEST_MODELS or decision.action.startswith("memory_"): ⏎             result = ReactToolResult` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_life_agent_protocol.py::test_native_react_submits_one_host_bound_life_action --tb=short` | **killed**, exit 1<br>实际后续 tool 正文不含 m_prior，真实记忆内容包含断言失败。 `immediate-memory-result-cleared-explicit-assertion.log` |
| `statement-retry-reserve-weakened-explicit-assertion`<br>SPEC:28-29,64 | `custom/agents/life_agent.py:756`<br>`self._life_require_turns("decision_statement", 3)` → `self._life_require_turns("decision_statement", 2)` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_life_agent_protocol.py::test_native_react_rejects_impossible_one_turn_life_decision --tb=short` | **killed**, exit 1<br>必须在剩余两回合时拒绝；实际先调用模型再报只剩一回合，pytest.raises 的预算原因断言失败。 `statement-retry-reserve-weakened-explicit-assertion.log` |
| `registration-claims-completed`<br>SPEC:84-86; CONTRACTS:535 | `custom/agents/life_agent.py:508`<br>`Your action has been registered for later settlement. Its outcome will appear in your next observation.` → `Your action has been successfully completed.` | `/Users/atishoo/.codex/worktrees/decision-context/WorldMock/.venv/bin/python -m pytest -q tests/integration/test_life_agent_protocol.py::test_native_react_submits_one_host_bound_life_action --tb=short` | **killed**, exit 1<br>实际正文声称 successfully completed，缺少 registered for later settlement，字面语义断言失败。 `registration-claims-completed.log` |

## 还原与清理证据

每次突变后都立即 `git restore -- <唯一目标文件>`，并分别在临时根仓和 AS2 子仓运行 `git diff --exit-code` 与完整 `git status --porcelain=v1 --untracked-files=all`；所有 35 次记录均为 diff exit 0、空 status。不会把不同突变叠在同一次运行里。

最终临时根仓 HEAD 与 AS2 HEAD 分别仍为上述固定提交，两仓 diff/status 仍干净。为避免嵌套仓影响 worktree 删除，把仅本次创建的 AS2 clone 临时移出后，再次核验根仓干净，再运行：

```sh
git worktree remove /tmp/worldmock-bob-protocol.j2GLrV
```

命令返回 0；工作树路径已不存在，Git 工作树登记已不存在，临时移出的 AS2 clone 也已删除。未使用强制工作树移除、未 stash、未提交、未推送。

清理完成时，调用者根仓和 AS2 子仓 HEAD 与初始完全一致，二者 `git diff --exit-code` 返回 0，完整 status 为空。只有被忽略的 `.work/decision-context/bob-protocol/` 中保存报告和证据。完整证明见 `restoration.json`。

## 未验证边界

未调用真实模型提供商，未执行真实网络、长周期模拟或十二周实验；未声称那些验证通过。投影的业务隐私、运行恢复入口、证据下游与文档一致性属于其他独立审查范围。本报告只报告上述协议条款、对应源代码和固定测试命令观察到的结果。

初始计划：`predeclared-plan.json`。额外明确断言计划：`assertions-predeclared-plan.json`。预检和导入路径：`preflight.json`。

报告中的调用者清理状态以 restoration.json 保存的 postflight 为准；父代理随后开展的文档收口不属于本次审查改动，生产源码和测试仍以审查基线为准。
