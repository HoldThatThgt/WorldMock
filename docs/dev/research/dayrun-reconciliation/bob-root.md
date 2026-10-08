> 历史记录：本文件保存提交 `72f004b0087efa1b6f6d662ac6a143e087e4e21c` 时的过程与判断，不再定义当前系统。
> 当前设计见 [文档入口](../../../INDEX.md)；正文中的路径、规则和结果按当时上下文理解。

> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/dayrun-reconciliation/bob-root.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# Bob report

**Status:** COMPLETE for the selected clauses. Eight independent, valid semantic mutants were killed; zero survived. This is not an exhaustive score for the repository.

**Caller:** `/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock`, `fa150fb2fc874db568f1812b606608217e42e574`. The caller's full porcelain status was empty before worktree registration and at this report's final check. All mutations occurred only in detached `/tmp/worldmock-dayrun-bob-OJ7yF4`; tests and specifications were never edited. The detached worktree was restored to `HEAD` with empty `git diff --exit-code` and porcelain status after every mutation.

**Normative clauses:** `docs/CONTRACTS.md` “活动控制行” lines 533–550 (eight fields, independent `data`, true game session), “下推返回体” lines 635–652 (strict decoded cursor types and action-local rejection), “模型身份” lines 766–789 (transport 3, ReAct 16, manifest and restored workspace strict typing); `docs/dev/SPEC-life-realism.md` “商务与进食” lines 389–400 (nine-place cumulative/actual consumption and conservation). No other document was used as product authority.

**Test environment:** Every command below ran with cwd `/tmp/worldmock-dayrun-bob-OJ7yF4`, interpreter `/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock/.venv/bin/python`, `PYTHONPATH=/tmp/worldmock-dayrun-bob-OJ7yF4/src:/tmp/worldmock-dayrun-bob-OJ7yF4:/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock/vendor/agentsociety2/packages/agentsociety2`, and `MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False LITELLM_LOCAL_MODEL_COST_MAP=True`. Invoke each listed selector with `-m pytest -q`; no network endpoint or model call was used. This is the exact common command prefix:

```sh
env MEM0_TELEMETRY=False ANONYMIZED_TELEMETRY=False LITELLM_LOCAL_MODEL_COST_MAP=True PYTHONPATH=/tmp/worldmock-dayrun-bob-OJ7yF4/src:/tmp/worldmock-dayrun-bob-OJ7yF4:/Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock/vendor/agentsociety2/packages/agentsociety2 /Users/atishoo/.codex/worktrees/dayrun-reconciliation/WorldMock/.venv/bin/python -m pytest -q
```

| Fixed selector appended to common prefix | Baseline |
| --- | --- |
| `tests/integration/test_observation_agent_seam.py::test_real_game_observation_accepts_and_executes_visible_command` | 1 passed |
| `tests/integration/test_observation_agent_seam.py::test_observation_nested_content_data_cannot_change_world_progress` | 1 passed |
| `tests/life/test_dayrun_inventory_regressions.py::test_meal_partitions_reach_zero_without_losing_quantity_or_energy` | 48 passed |
| `tests/life/test_dayrun_cursor_regressions.py::test_cursor_rejects_wrong_field_types_without_coercion tests/life/test_dayrun_cursor_regressions.py::test_malformed_cursor_is_a_business_rejection_and_next_round_survives` | 22 passed |
| `tests/integration/test_runtime.py::test_agent_specs_inject_compiled_persona_card_text` | 1 passed |
| `tests/integration/test_run_manifest.py::test_run_budgets_are_frozen_and_missing_or_changed_values_rejected` | 8 passed |
| `tests/integration/test_host_branch_coverage.py::test_restore_rejects_missing_changed_or_wrong_type_agent_budget` | 7 passed |
| `tests/integration/test_host_branch_coverage.py::test_restored_life_agent_reads_the_frozen_budget_from_its_real_workspace` | 1 passed |

All eight declared selectors passed on the unmodified commit (89 parameterized cases). The final selector was added and baselined before its own mutant; it verifies the actual restored `LifeAgent._max_react_turns`, not only the config factory.

| Clause / production mutation | Fixed selector above | Result and causal evidence |
| --- | --- | --- |
| Activity control / `src/worldmock/life/observe.py:208`: omit `data` | real game observation | **Killed**, 1 failed: `LifeAgent._visible_game_control` rejected missing `game_session_id` as invalid active game session. The mutated engine and agent entrypoints both ran. |
| Activity control / `observe.py:208`: `deepcopy(active.data)` → `dict(active.data)` | nested content observation | **Killed**, 1 failed: clearing the projected nested cues cleared the authoritative list (0 versus original 20). |
| Eating / `src/worldmock/life/basket.py:315`: remove rounding of cumulative `consumed` | meal partitions | **Killed**, 28 failed/20 passed: after seven partitions, inventory was zero but recorded consumption was `0.9999999999999999` rather than `1.0`. The parameterized suite covers 2–49 partitions with JSON rehydration. |
| Cursor / `src/worldmock/life/presentation.py:339–343`: allow finite float offset and return `int(offset)` | both cursor selectors | **Killed**, 1 failed/21 passed: float offset `0.5` was accepted, violating rejection without coercion. A first draft of this mutant also threw on Infinity; it was restored and replaced by the clean finite-float mutation before classification. |
| Actual ReAct budget / `src/worldmock/runtime.py:39`: `MAX_REACT_TURNS=10` | agent specs; independently restored real LifeAgent | **Killed** by both fixed oracles: factory config became 10, and checkpoint-restored `LifeAgent._max_react_turns` was 10 instead of 16. The constant feeds manifest identity and workspace config at `runtime.py:352,888`. One semantic mutant was rerun independently after restoration; counted once. |
| Manifest ReAct type / `src/worldmock/validation.py:102`: remove exact-int guard | run budget manifest | **Killed**, 1 failed/7 passed: `16.0` passed the mutated validator, while the test required `IntegrityError`. |
| Restored workspace type / `src/worldmock/runtime.py:556`: remove exact-int guard | restore budget rejection | **Killed**, 1 failed/6 passed: workspace `max_react_turns: 16.0` reached branch publication instead of rejection. |
| Manifest transport type / `src/worldmock/validation.py:100`: remove exact-int guard | run budget manifest | **Killed**, 1 failed/7 passed: `transport_max_retries: 3.0` passed the mutated validator instead of rejection. |

**Restoration and limits:** After each case, `git restore --source=HEAD -- <mutated production file>`, `git diff --exit-code`, and `git status --porcelain=v1 --untracked-files=all` completed clean. No mutant survived, so no production or test repair is requested from this review. This scope does not establish correctness for other engine paths, live provider behavior, or a full 16-call model session; the ReAct conclusion is tied to the config-to-restored-agent path and the inspected `_react_budget` use of `_max_react_turns` in `custom/agents/life_agent.py:633–655`. No LabelKit or network integration ran.
