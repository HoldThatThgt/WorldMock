> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/day-plan-goals-bob-interface.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 当日目标 Uncle Bob 审查：interface

状态：COMPLETE。接口范围的 8 个独立有效语义突变全部 killed。

## 审查身份与边界

- 调用方仓库：`/private/var/folders/1y/l2txv0cd12n0x_80v_c_st300000gn/T/worldmock-day-goals-review-zit1ffsd/baseline`。
- 固定代码提交：`2d97d3dbe8cd6120e5e3cb707f8387fd22deb601`。
- 唯一行为规范：`docs/dev/SPEC-day-plan-goals.md`，选定「提交与修订」和「人物可见内容与工具」的下列可观察条款。
- 独立 detached worktree：`/tmp/worldmock-bob-interface.HlYQ7h/checkout`，由 `mktemp -d` 返回的独立目录创建，审查后已移除。
- 仅临时修改 `custom/agents/life_agent.py`、`src/worldmock/life/projection.py`、`src/worldmock/life/narration.py`；未修改测试、规范、依赖或调用方文件。
- 获得了本特性确定性本地 integration 测试及临时生产语义突变授权。未调用网络、真实模型或生产运行。
- 仓库及祖先/目标目录未发现额外 AGENTS.md；遵循会话提供的工程约束。

## 基线与预声明命令

创建 worktree 前后调用方 `git status --porcelain=v1 --untracked-files=all` 均为空。先读取规范与测试，再声明以下命令；全部突变均原样使用这一命令，未根据失败结果替换测试。

```sh
cd /tmp/worldmock-bob-interface.HlYQ7h/checkout
export PYTHONPATH=/tmp/worldmock-bob-interface.HlYQ7h/checkout/src:/tmp/worldmock-bob-interface.HlYQ7h/checkout:/Users/atishoo/.codex/worktrees/day-plan-goals/WorldMock/vendor/agentsociety2/packages/agentsociety2:/Users/atishoo/.codex/worktrees/day-plan-goals/WorldMock/vendor/labelkit/src
export LITELLM_LOCAL_MODEL_COST_MAP=True
export AGENTSOCIETY_LLM_API_KEY=test-only
export AGENTSOCIETY_LLM_API_BASE=http://127.0.0.1:1/v1
/Users/atishoo/Project/WorldMock/.venv/bin/python -m pytest -q --tb=short tests/integration/test_observation_agent_seam.py::test_native_goal_tools_persist_empty_revision_progress_and_restore tests/integration/test_grounded_targets.py::test_plan_day_open_without_plan_and_restricted_at_day_start tests/integration/test_grounded_targets.py::test_revise_day_plan_preserves_progressed_intents_and_combined_capacity tests/integration/test_life_agent_protocol.py::test_plan_tool_schemas_describe_ordered_goals_and_empty_revision tests/life/test_grounded_observation.py::test_observation_keeps_current_epoch_without_plan_conversion_anchors tests/life/test_grounded_observation.py::test_observation_keeps_full_goal_text_without_exceeding_scene_budget tests/life/test_grounded_observation.py::test_goal_projection_survives_bedtime_and_expires_at_local_midnight tests/life/test_narration.py::TestRenderScene::test_scene_contains_key_content
```

基线 exit code：`0`。

```text
........                                                                 [100%]
8 passed in 2.80s
```

每个突变先以 Python compile 检查修改后的源文件可解析，再运行上述真实测试入口。被杀死的判据是下文明确的行为断言失败，没有将语法、导入或启动失败计为 killed。

## 条款、实现与突变结果

| 规范条款 | 生产责任 | 突变行为 | 结果与因果证据 |
| --- | --- | --- | --- |
| 空清单保持已提交身份；`SPEC-day-plan-goals.md` 提交与修订:37; 人物可见内容与工具:89-90 | `custom/agents/life_agent.py` 的 `_visible_plan_day_contract` | 将已提交空清单误判为尚未提交，使 plan_day 再次出现在工具面 | killed；真实接缝及工具面断言观察到 plan_day 不应重新开放 |
| 已有空清单仍可修订；`SPEC-day-plan-goals.md` 人物可见内容与工具:89-90 | `custom/agents/life_agent.py` 的 `_visible_revise_plan_contract` | 空 goals 时返回空修订合同，隐藏 revise_day_plan | killed；真实接缝及工具面断言观察到 revise_day_plan 消失 |
| 已推进意图不得在人物入口重提；`SPEC-day-plan-goals.md` 提交与修订:40-41; 人物可见内容与工具:89 | `custom/agents/life_agent.py` 的 `_validate_plan_goals` | 移除已推进意图重提的本地拒绝 | killed；预期 cannot resubmit 的非法修订返回空错误字符串 |
| 实际工具 Schema 不含旧时段字段；`SPEC-day-plan-goals.md` 提交与修订:35-36; 人物可见内容与工具:89 | `custom/agents/life_agent.py` 的 `_bind_visible_action_schemas` | 给目标条目实际工具 Schema 增加合法但已退役的必填 start 整数字段 | killed；实际 Schema 字段闭集出现额外 start，被字段相等断言捕获 |
| 移除计划专用时间锚点；`SPEC-day-plan-goals.md` 人物可见内容与工具:87-88 | `src/worldmock/life/projection.py` 的 `_scene_projection` | 在真实场景投影重新输出 day_end_epoch | killed；removed.isdisjoint 对实际观察返回 False |
| 就寝后目标仍可见直到日界；`SPEC-day-plan-goals.md` 提交与修订:44-45; 人物可见内容与工具:84 | `src/worldmock/life/projection.py` 的 `_plan_fact` | 让目标事实在本地日界前一小时消失 | killed；日界前一秒预期空清单对象，实际为 None |
| 完整目标文字保留在事实中；`SPEC-day-plan-goals.md` 人物可见内容与工具:84-85 | `src/worldmock/life/projection.py` 的 `_plan_fact` | 仅从公开 facts 的每个目标删除 goal 字段 | killed；实际观察与完整目标状态相等断言失败；不是仅依靠后续 Pydantic 报错 |
| 场景只声明已推进，不声明目标完成；`SPEC-day-plan-goals.md` 人物可见内容与工具:85-86 | `src/worldmock/life/narration.py` 的 `_plan_sentence` | 将已推进的场景措辞改成已完成 | killed；预期 工作（已推进），实际场景包含 工作（已完成） |

计数：映射可观察要求 8；implementation missing 0；implementation diverged 0；killed 8；survived 0；invalid 0；inconclusive 0。
未发现存活突变，因此没有本范围待补测试观察断言。此结论只涵盖表中所列接口要求，不声称全特性或全部边界均已接受变异检验。

## 逐突变补丁与实际输出

### 空清单保持已提交身份

突变标识：`empty_submission_reopens`。exit code=`1`；分类=`killed`。测试命令与预声明命令完全相同。

```diff
diff --git a/custom/agents/life_agent.py b/custom/agents/life_agent.py
index fd4c722..645667e 100644
--- a/custom/agents/life_agent.py
+++ b/custom/agents/life_agent.py
@@ -1384,7 +1384,7 @@ def _plan_required_before_other_actions(
 
 def _visible_plan_day_contract(data: dict[str, Any]) -> dict[str, Any]:
     """@brief 当日尚未提交时开放目标清单；空清单同样表示已提交。"""
-    if _facts_projection(data).get("day_plan") is not None:
+    if (_facts_projection(data).get("day_plan") or {}).get("goals"):
         return {}
     return {"progressed_intents": ()}
```

```text
FF.F....                                                                 [100%]
=================================== FAILURES ===================================
______ test_native_goal_tools_persist_empty_revision_progress_and_restore ______
tests/integration/test_observation_agent_seam.py:166: in test_native_goal_tools_persist_empty_revision_progress_and_restore
    assert "plan_day" not in tools and "revise_day_plan" in tools
E   AssertionError: assert ('plan_day' not in {'start_basic_activity': {'name': 'start_basic_activity', 'description': 'During one decision submit exactly one life ...cursor': {'anyOf': [{...}, {...}], 'title': 'Cursor'}}, 'required': ['category', 'cursor'], ...}, 'strict': True}, ...})
_________ test_plan_day_open_without_plan_and_restricted_at_day_start __________
tests/integration/test_grounded_targets.py:773: in test_plan_day_open_without_plan_and_restricted_at_day_start
    assert "plan_day" not in _functions(planned)
E   AssertionError: assert 'plan_day' not in {'plan_day': {'name': 'plan_day', 'description': 'During one decision submit exactly one life action, and it must be t... 1, 'title': 'Limit', 'type': 'integer'}}, 'required': ['start_step', 'end_step', 'limit'], ...}, 'strict': True}, ...}
E    +  where {'plan_day': {'name': 'plan_day', 'description': 'During one decision submit exactly one life action, and it must be t... 1, 'title': 'Limit', 'type': 'integer'}}, 'required': ['start_step', 'end_step', 'limit'], ...}, 'strict': True}, ...} = _functions(<custom.agents.life_agent.LifeAgent object at 0x116e785c0>)
_______ test_plan_tool_schemas_describe_ordered_goals_and_empty_revision _______
tests/integration/test_life_agent_protocol.py:258: in test_plan_tool_schemas_describe_ordered_goals_and_empty_revision
    assert "revise_day_plan" in revising and "plan_day" not in revising
E   AssertionError: assert ('revise_day_plan' in {'start_basic_activity': {'name': 'start_basic_activity', 'description': 'During one decision submit exactly one life ...ing', 'enum': ['home', 'office']}}, 'required': ['items', 'fulfillment', 'destination_id'], ...}, 'strict': True}, ...} and 'plan_day' not in {'start_basic_activity': {'name': 'start_basic_activity', 'description': 'During one decision submit exactly one life ...ing', 'enum': ['home', 'office']}}, 'required': ['items', 'fulfillment', 'destination_id'], ...}, 'strict': True}, ...})
=========================== short test summary info ============================
FAILED tests/integration/test_observation_agent_seam.py::test_native_goal_tools_persist_empty_revision_progress_and_restore
FAILED tests/integration/test_grounded_targets.py::test_plan_day_open_without_plan_and_restricted_at_day_start
FAILED tests/integration/test_life_agent_protocol.py::test_plan_tool_schemas_describe_ordered_goals_and_empty_revision
3 failed, 5 passed in 2.27s
```

立即恢复证明：`git diff --exit-code` 返回 0；`git status --porcelain=v1 --untracked-files=all` 为空；修改文件逐字节等于突变前内容。

### 已有空清单仍可修订

突变标识：`empty_revision_hidden`。exit code=`1`；分类=`killed`。测试命令与预声明命令完全相同。

```diff
diff --git a/custom/agents/life_agent.py b/custom/agents/life_agent.py
index fd4c722..a59473c 100644
--- a/custom/agents/life_agent.py
+++ b/custom/agents/life_agent.py
@@ -1392,7 +1392,7 @@ def _visible_plan_day_contract(data: dict[str, Any]) -> dict[str, Any]:
 def _visible_revise_plan_contract(data: dict[str, Any]) -> dict[str, Any]:
     """@brief 任何已提交清单都可修订未推进部分，空清单也允许追加目标。"""
     plan = _facts_projection(data).get("day_plan")
-    if plan is None:
+    if plan is None or not plan["goals"]:
         return {}
     try:
         validated = DayPlan.model_validate(plan)
```

```text
FF.F....                                                                 [100%]
=================================== FAILURES ===================================
______ test_native_goal_tools_persist_empty_revision_progress_and_restore ______
tests/integration/test_observation_agent_seam.py:163: in test_native_goal_tools_persist_empty_revision_progress_and_restore
    before_revision, revision, tools = await _agent_action(
tests/integration/test_observation_agent_seam.py:140: in _agent_action
    assert action in functions
E   AssertionError: assert 'revise_day_plan' in {'start_basic_activity': {'name': 'start_basic_activity', 'description': 'During one decision submit exactly one life ...cursor': {'anyOf': [{...}, {...}], 'title': 'Cursor'}}, 'required': ['category', 'cursor'], ...}, 'strict': True}, ...}
_________ test_plan_day_open_without_plan_and_restricted_at_day_start __________
tests/integration/test_grounded_targets.py:774: in test_plan_day_open_without_plan_and_restricted_at_day_start
    assert "revise_day_plan" in _functions(planned)
E   AssertionError: assert 'revise_day_plan' in {'start_basic_activity': {'name': 'start_basic_activity', 'description': 'During one decision submit exactly one life ...ing', 'enum': ['home', 'office']}}, 'required': ['items', 'fulfillment', 'destination_id'], ...}, 'strict': True}, ...}
E    +  where {'start_basic_activity': {'name': 'start_basic_activity', 'description': 'During one decision submit exactly one life ...ing', 'enum': ['home', 'office']}}, 'required': ['items', 'fulfillment', 'destination_id'], ...}, 'strict': True}, ...} = _functions(<custom.agents.life_agent.LifeAgent object at 0x112556840>)
_______ test_plan_tool_schemas_describe_ordered_goals_and_empty_revision _______
tests/integration/test_life_agent_protocol.py:258: in test_plan_tool_schemas_describe_ordered_goals_and_empty_revision
    assert "revise_day_plan" in revising and "plan_day" not in revising
E   AssertionError: assert ('revise_day_plan' in {'start_basic_activity': {'name': 'start_basic_activity', 'description': 'During one decision submit exactly one life ...ing', 'enum': ['home', 'office']}}, 'required': ['items', 'fulfillment', 'destination_id'], ...}, 'strict': True}, ...})
=========================== short test summary info ============================
FAILED tests/integration/test_observation_agent_seam.py::test_native_goal_tools_persist_empty_revision_progress_and_restore
FAILED tests/integration/test_grounded_targets.py::test_plan_day_open_without_plan_and_restricted_at_day_start
FAILED tests/integration/test_life_agent_protocol.py::test_plan_tool_schemas_describe_ordered_goals_and_empty_revision
3 failed, 5 passed in 2.11s
```

立即恢复证明：`git diff --exit-code` 返回 0；`git status --porcelain=v1 --untracked-files=all` 为空；修改文件逐字节等于突变前内容。

### 已推进意图不得在人物入口重提

突变标识：`progressed_intent_local_rejection_removed`。exit code=`1`；分类=`killed`。测试命令与预声明命令完全相同。

```diff
diff --git a/custom/agents/life_agent.py b/custom/agents/life_agent.py
index fd4c722..e0afada 100644
--- a/custom/agents/life_agent.py
+++ b/custom/agents/life_agent.py
@@ -1860,7 +1860,7 @@ def _validate_plan_goals(goals: Any, progressed_intents: tuple[str, ...]) -> str
     if len(goals) + len(progressed_intents) > MAX_PLAN_GOALS:
         logging.error("Day plan exceeds eight goals after preserving progressed goals")
         return "day plan requires at most eight goals including progressed goals"
-    if set(progressed_intents).intersection(goal.intent_kind for goal in goals):
+    if False and set(progressed_intents).intersection(goal.intent_kind for goal in goals):
         logging.error("Day plan revision resubmits a progressed intent")
         return "revise_day_plan cannot resubmit progressed goals"
     return ""
```

```text
..F.....                                                                 [100%]
=================================== FAILURES ===================================
___ test_revise_day_plan_preserves_progressed_intents_and_combined_capacity ____
tests/integration/test_grounded_targets.py:820: in test_revise_day_plan_preserves_progressed_intents_and_combined_capacity
    assert "cannot resubmit" in validate([{"intent_kind": "睡眠", "goal": "换目标"}])
E   AssertionError: assert 'cannot resubmit' in ''
E    +  where '' = <function test_revise_day_plan_preserves_progressed_intents_and_combined_capacity.<locals>.validate at 0x11751f600>([{'intent_kind': '睡眠', 'goal': '换目标'}])
=========================== short test summary info ============================
FAILED tests/integration/test_grounded_targets.py::test_revise_day_plan_preserves_progressed_intents_and_combined_capacity
1 failed, 7 passed in 2.67s
```

立即恢复证明：`git diff --exit-code` 返回 0；`git status --porcelain=v1 --untracked-files=all` 为空；修改文件逐字节等于突变前内容。

### 实际工具 Schema 不含旧时段字段

突变标识：`retired_start_field_reintroduced_in_schema`。exit code=`1`；分类=`killed`。测试命令与预声明命令完全相同。

```diff
diff --git a/custom/agents/life_agent.py b/custom/agents/life_agent.py
index fd4c722..cdf6bbd 100644
--- a/custom/agents/life_agent.py
+++ b/custom/agents/life_agent.py
@@ -1764,6 +1764,8 @@ def _bind_visible_action_schemas(schemas: list[dict[str, Any]], contracts: dict[
         if name in functions:
             properties = functions[name]["parameters"]["properties"]
             progressed = contracts[name]["progressed_intents"]
+            properties["goals"]["items"]["properties"]["start"] = {"type": "integer"}
+            properties["goals"]["items"]["required"].append("start")
             properties["goals"]["items"]["properties"]["intent_kind"]["enum"] = [
                 kind for kind in INTENT_KINDS if kind not in progressed]
     if "send_message" in functions:
```

```text
...F....                                                                 [100%]
=================================== FAILURES ===================================
_______ test_plan_tool_schemas_describe_ordered_goals_and_empty_revision _______
tests/integration/test_life_agent_protocol.py:265: in test_plan_tool_schemas_describe_ordered_goals_and_empty_revision
    assert set(fields) == {"intent_kind", "goal"}
E   AssertionError: assert {'goal', 'int...ind', 'start'} == {'goal', 'intent_kind'}
E     
E     Extra items in the left set:
E     'start'
E     Use -v to get more diff
=========================== short test summary info ============================
FAILED tests/integration/test_life_agent_protocol.py::test_plan_tool_schemas_describe_ordered_goals_and_empty_revision
1 failed, 7 passed in 2.71s
```

立即恢复证明：`git diff --exit-code` 返回 0；`git status --porcelain=v1 --untracked-files=all` 为空；修改文件逐字节等于突变前内容。

### 移除计划专用时间锚点

突变标识：`retired_epoch_anchor_reintroduced`。exit code=`1`；分类=`killed`。测试命令与预声明命令完全相同。

```diff
diff --git a/src/worldmock/life/projection.py b/src/worldmock/life/projection.py
index c02327f..24d7aae 100644
--- a/src/worldmock/life/projection.py
+++ b/src/worldmock/life/projection.py
@@ -208,6 +208,7 @@ def _scene_projection(inputs: ProjectionInputs, local: datetime, levels: dict,
     return {
         "current_time": local.isoformat(),
         "current_epoch": now,
+        "day_end_epoch": now,
         "location": location.name if location else agent.location_id,
         "people_here": [item["name"] for item in people],
         "felt": levels,
```

```text
....F...                                                                 [100%]
=================================== FAILURES ===================================
_____ test_observation_keeps_current_epoch_without_plan_conversion_anchors _____
tests/life/test_grounded_observation.py:133: in test_observation_keeps_current_epoch_without_plan_conversion_anchors
    assert removed.isdisjoint(observation[layer])
E   AssertionError: assert False
E    +  where False = <built-in method isdisjoint of set object at 0x11cd80e40>({'current_time': '2026-01-05T00:00:00+08:00', 'current_epoch': 1767542400, 'day_end_epoch': 1767542400, 'location': '张江居所（冻结场景锚点）', ...})
E    +    where <built-in method isdisjoint of set object at 0x11cd80e40> = {'bedtime_epoch', 'day_end_epoch', 'day_start_epoch'}.isdisjoint
=========================== short test summary info ============================
FAILED tests/life/test_grounded_observation.py::test_observation_keeps_current_epoch_without_plan_conversion_anchors
1 failed, 7 passed in 2.75s
```

立即恢复证明：`git diff --exit-code` 返回 0；`git status --porcelain=v1 --untracked-files=all` 为空；修改文件逐字节等于突变前内容。

### 就寝后目标仍可见直到日界

突变标识：`facts_hidden_before_midnight`。exit code=`1`；分类=`killed`。测试命令与预声明命令完全相同。

```diff
diff --git a/src/worldmock/life/projection.py b/src/worldmock/life/projection.py
index c02327f..b3ae4f2 100644
--- a/src/worldmock/life/projection.py
+++ b/src/worldmock/life/projection.py
@@ -305,7 +305,7 @@ def _plan_row(agent: AgentState, now: int) -> dict[str, Any] | None:
 def _plan_fact(agent: AgentState, now: int) -> dict[str, Any] | None:
     """@brief 事实保留当日完整目标与推进证据，空清单保持已提交身份。"""
     plan = agent.day_plan
-    if plan is None or plan.day_end <= now:
+    if plan is None or plan.day_end - 3600 <= now:
         return None
     return plan.model_dump(mode="json")
```

```text
......F.                                                                 [100%]
=================================== FAILURES ===================================
_____ test_goal_projection_survives_bedtime_and_expires_at_local_midnight ______
tests/life/test_grounded_observation.py:170: in test_goal_projection_survives_bedtime_and_expires_at_local_midnight
    assert _plan_fact(agent, day_end - 1) == {"day_end": day_end, "goals": []}
E   AssertionError: assert None == {'day_end': 1767628800, 'goals': []}
E    +  where None = <function _plan_fact at 0x11603ac00>(AgentState(agent_id=1, name='陈曦', location_id='home', balance_account_id='agent:1', skill_levels={'meeting_analysis': ...ort_started_at=None, discomfort_pulse_hours=0.0, supply_browsed_at={}, supply_browsed_location={}, material_reveals=[]), (1767628800 - 1))
=========================== short test summary info ============================
FAILED tests/life/test_grounded_observation.py::test_goal_projection_survives_bedtime_and_expires_at_local_midnight
1 failed, 7 passed in 2.63s
```

立即恢复证明：`git diff --exit-code` 返回 0；`git status --porcelain=v1 --untracked-files=all` 为空；修改文件逐字节等于突变前内容。

### 完整目标文字保留在事实中

突变标识：`full_goal_text_removed_from_facts`。exit code=`1`；分类=`killed`。测试命令与预声明命令完全相同。

```diff
diff --git a/src/worldmock/life/projection.py b/src/worldmock/life/projection.py
index c02327f..d974b7c 100644
--- a/src/worldmock/life/projection.py
+++ b/src/worldmock/life/projection.py
@@ -307,7 +307,7 @@ def _plan_fact(agent: AgentState, now: int) -> dict[str, Any] | None:
     plan = agent.day_plan
     if plan is None or plan.day_end <= now:
         return None
-    return plan.model_dump(mode="json")
+    return plan.model_dump(mode="json", exclude={"goals": {"__all__": {"goal"}}})
 
 
 def _task_fact_rows(inputs: ProjectionInputs) -> list[dict[str, Any]]:
```

```text
F....F..                                                                 [100%]
=================================== FAILURES ===================================
______ test_native_goal_tools_persist_empty_revision_progress_and_restore ______
custom/agents/life_agent.py:1398: in _visible_revise_plan_contract
    validated = DayPlan.model_validate(plan)
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   pydantic_core._pydantic_core.ValidationError: 1 validation error for DayPlan
E   goals.0.goal
E     Field required [type=missing, input_value={'intent_kind': '休息',...ress_activity_id': None}, input_type=dict]
E       For further information visit https://errors.pydantic.dev/2.12/v/missing

The above exception was the direct cause of the following exception:
tests/integration/test_observation_agent_seam.py:171: in test_native_goal_tools_persist_empty_revision_progress_and_restore
    _, started, _ = await _agent_action(
tests/integration/test_observation_agent_seam.py:137: in _agent_action
    agent.prepare_react_loop(ReactLoopRequest(0, ctx.observed_at, _observation_records(observation)))
custom/agents/life_agent.py:869: in prepare_react_loop
    self._prepare_life_action_state(data)
custom/agents/life_agent.py:897: in _prepare_life_action_state
    menu, _visible_action_contracts(data))
          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
custom/agents/life_agent.py:1364: in _visible_action_contracts
    "revise_day_plan": _visible_revise_plan_contract(data),
                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
custom/agents/life_agent.py:1401: in _visible_revise_plan_contract
    raise RuntimeError("LifeAgent day plan goals are invalid") from exc
E   RuntimeError: LifeAgent day plan goals are invalid
------------------------------ Captured log call -------------------------------
ERROR    root:life_agent.py:1400 LifeAgent day plan goals are invalid: 1 validation error for DayPlan
goals.0.goal
  Field required [type=missing, input_value={'intent_kind': '休息',...ress_activity_id': None}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.12/v/missing
_____ test_observation_keeps_full_goal_text_without_exceeding_scene_budget _____
tests/life/test_grounded_observation.py:153: in test_observation_keeps_full_goal_text_without_exceeding_scene_budget
    assert observation["facts"]["day_plan"] == state.agents[1].day_plan.model_dump(mode="json")
E   AssertionError: assert {'day_end': 1...: None}, ...]} == {'day_end': 1...e, ...}, ...]}
E     
E     Omitting 1 identical items, use -vv to show
E     Differing items:
E     {'goals': [{'intent_kind': '睡眠', 'registered_at': 1767542400, 'progressed_at': None, 'progress_activity_id': None}, {'...: None}, {'intent_kind': '休息', 'registered_at': 1767542400, 'progressed_at': None, 'progress_activity_id': None}, ...]} != {'goals': [{'intent_kind': '睡眠', 'goal': '目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目...目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目目', 'registered_at': 1767542400, 'progressed_at': None, ...}, ...]}
E     Use -v to get more diff
=========================== short test summary info ============================
FAILED tests/integration/test_observation_agent_seam.py::test_native_goal_tools_persist_empty_revision_progress_and_restore
FAILED tests/life/test_grounded_observation.py::test_observation_keeps_full_goal_text_without_exceeding_scene_budget
2 failed, 6 passed in 2.39s
```

立即恢复证明：`git diff --exit-code` 返回 0；`git status --porcelain=v1 --untracked-files=all` 为空；修改文件逐字节等于突变前内容。

### 场景只声明已推进，不声明目标完成

突变标识：`progress_claimed_as_completion`。exit code=`1`；分类=`killed`。测试命令与预声明命令完全相同。

```diff
diff --git a/src/worldmock/life/narration.py b/src/worldmock/life/narration.py
index b9afa7c..65e26d1 100644
--- a/src/worldmock/life/narration.py
+++ b/src/worldmock/life/narration.py
@@ -208,7 +208,7 @@ def _plan_sentence(observation: dict) -> str:
     goals = day_plan["goals"]
     if not goals:
         return "今天没有刻意目标。"
-    labels = [f"{goal['intent_kind']}（{'已推进' if goal['progressed'] else '未推进'}）"
+    labels = [f"{goal['intent_kind']}（{'已完成' if goal['progressed'] else '未推进'}）"
               for goal in goals]
     return _close_sentence(f"今天想推进：{'、'.join(labels)}")
```

```text
.......F                                                                 [100%]
=================================== FAILURES ===================================
_______________ TestRenderScene.test_scene_contains_key_content ________________
tests/life/test_narration.py:77: in test_scene_contains_key_content
    assert "今天想推进：工作（已推进）、运动（未推进）" in scene
E   AssertionError: assert '今天想推进：工作（已推进）、运动（未推进）' in '现在是 2026 年 9 月 18 日星期五 08:30。你在公司工位，李雷、韩梅梅也在。你有点饿、精力尚可。你正在进行《写周报》，已写将近一半。之前做到一半的《整理会议纪要》（已做二十分钟）还没收尾。今天想推进：工作（已完成）、运动（未推进）。收件箱最新消息：张三：周报请在今天下班前提交；人事部：下午茶活动报名。更早还有未读消息。还有两件待办：提交周报和预约体检。可达地点有公司食堂（营业中）、楼下便利店（未营业）。你手边有手机、工卡、钱包。你完成了通勤，准时到达公司。'
=========================== short test summary info ============================
FAILED tests/life/test_narration.py::TestRenderScene::test_scene_contains_key_content
1 failed, 7 passed in 4.36s
```

立即恢复证明：`git diff --exit-code` 返回 0；`git status --porcelain=v1 --untracked-files=all` 为空；修改文件逐字节等于突变前内容。

## 恢复与清理证据

全部突变串行执行；开始下一突变前均证明恢复干净。末次执行后 detached worktree 的 HEAD 仍为固定提交，diff 和完整 status 均为空。随后 `git worktree remove /tmp/worldmock-bob-interface.HlYQ7h/checkout` 返回 0，注册列表不再包含该路径。

```json
{
  "temporary_head": {
    "returncode": 0,
    "output": "2d97d3dbe8cd6120e5e3cb707f8387fd22deb601\n"
  },
  "temporary_diff": {
    "returncode": 0,
    "output": ""
  },
  "temporary_status": {
    "returncode": 0,
    "output": ""
  },
  "remove_worktree": {
    "returncode": 0,
    "output": ""
  },
  "caller_head": {
    "returncode": 0,
    "output": "2d97d3dbe8cd6120e5e3cb707f8387fd22deb601\n"
  },
  "caller_diff": {
    "returncode": 0,
    "output": ""
  },
  "caller_status": {
    "returncode": 0,
    "output": ""
  },
  "registered_after_removal": false
}
```

调用方 HEAD 未变，diff 返回 0，完整 status 为空；没有覆盖其他会话工作。没有提交、推送、stash 或保留生产修复。

## 未验证内容

未审查目标结算、情绪脉冲、事件时钟及三层统计模块的变异有效性；这些属于其他独立审查范围。未运行网络测试、真实模型、完整自然日或项目整体放行门禁。此报告不替代父代理的最终集成与验收台账。
