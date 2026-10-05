> 归档基线：2cf230115f4084aef2376e9d77bc976efbed43ea
> 原始路径：`docs/dev/research/dayrun-behavior/memory-verification.md`
> 归档说明：此提交仅定位归档原文快照，不是审查执行证明；原始执行记录与未执行事实按原文保留。

# 已核验经历、主观记忆与记录验收证据

> 工作树：`/Users/atishoo/.codex/worktrees/dayrun-behavior/WorldMock`。检查时主仓 HEAD 为
> `74edeaddccf1e051a0f7c98024ec95eeb6c48514`，并发实现尚未在主仓提交。
> AS2 子模块记忆钩子单独提交为 `8768e14df5c887c710fe96bf24981a2d8508346e`。
> 权威要求见[一日行为规格](../../SPEC-dayrun-behavior.md#已核验经历与人物自述)、
> [人物记忆合同](../../../CONTRACTS.md#记忆字段)及[记录引擎设计](../../../design/05-recording-engine.md#已核验经历与主观记忆)。

## 要求到生产入口与测试

| 冻结要求 | 生产入口 | 可执行验收 |
| --- | --- | --- |
| 本人已提交事件、明确截止、最近五条；完整历史只在离线导出 | `footprints/experiences.py` 的投影与近期读取、`store.py` 的倒序索引、`export.py` 的全前缀导出 | `test_recent_window_only_projects_own_committed_progress_before_observation`、`test_export_writes_full_committed_experience_prefix` |
| 等待仅证明实际正区间；食用按本区间正增量；路线终态与媒体位置才证明到达、播放 | `footprints/experiences.py` 的四种封闭投影 | `test_consumed_intervals_are_increments_and_projection_is_idempotent`、`test_missing_settlement_evidence_is_an_error_not_a_guessed_fact`；完整日测试检查四种事实和进食份额 |
| 事实来源、人物、时间和种类载荷不得伪造 | `validate_verified_experiences`、`footprints/quality.py` 的源事件独立重算、`footprints/validation.py` 的导出复算 | `test_reference_window_rejects_other_person_and_future_time`、`test_valid_looking_observed_fact_must_equal_its_committed_source`、导出篡改测试 |
| 自由文字一律主观；长期视图由身份、当前事实窗口、主观归纳确定性组装 | `custom/agents/life_memory.py` 的 `WorldMockMemoryRuntime`、AS2 `AgentMemoryStore` 与 PersonAgent factory hook | `test_fact_view_only_uses_current_observation_and_does_not_reset_subjective_counter`、`test_memory_tool_returns_explicit_subjective_label_even_when_empty` |
| 就寝反思明确为主观类型，新建与合并身份留证；空输入合法 | `life_memory.py`、AS2 `MemoryStepOutcome`/`MemoryUpdateEvidence`、`engine.record_memory_update` | `test_sleep_reflection_is_subjective_and_its_id_is_reported`、`test_merged_memory_identity_is_not_reported_as_a_duplicate`、完整日记忆更新断言 |
| 同一食用结算可投影多条事实，但记忆来源事件身份唯一 | `life_memory.py` 的首次出现顺序去重、`engine._memory_evidence_data` 的严格拒绝 | `test_multi_item_meal_keeps_both_facts_but_references_source_once` 用真实 FootprintStore 生成两条同源事实并通过记忆载荷校验 |
| 恢复保留主观身份，错误认识不升格；记忆检索结果不可修改持久数据 | `AgentMemoryStore` 严格状态与 episode schema、深副本返回 | `test_restore_preserves_subjective_identity_and_rejects_status_tampering`、`test_memory_store_results_cannot_mutate_persisted_nested_episode_data` |
| 等待、真实播放与人物经过时间分开；进食多样性只读实际 eat 进度 | `evaluation/behavior_metrics.py`、`footprints/quality.py` | `test_parallel_activity_time_does_not_double_person_elapsed_or_fake_content`、`test_meal_diversity_simpson_and_repetition_anchors` |
| 主观重复指标只取规范条目快照，缺失或畸形时未知且门禁失败 | `evaluation/behavior_metrics.py` 的 `_episode_pairs` 和门禁 | `test_memory_repetition_gate_values`、`test_missing_metric_evidence_is_reported_unknown_and_gates_fail_closed` |

`memory_updated.data` 的字段与证据含义在记录设计中逐项列明：操作类型、新建/合并身份、
`{id,type,text,epistemic_status}` 主观快照、观察身份、去重后的事实来源身份、
`MEMORY.md` 写入前后哈希、条目文件与状态文件写入后哈希，以及实际巩固标志。
质量重算要求来源身份集合与本轮观察窗口严格相等。该事件不生成客观经历。

## 已执行命令与边界

下列命令在该工作树运行；AS2 子模块测试另设 `WORKSPACE_PATH=当前工作树`。
测试环境设置 `MEM0_TELEMETRY=False`、`ANONYMIZED_TELEMETRY=False`、
`LITELLM_LOCAL_MODEL_COST_MAP=True`、
`PYTHONPATH=src:.:vendor/agentsociety2/packages/agentsociety2`，使用本地 `.venv/bin/python`。
测试不调用真实模型。各行属不同时间和选择集，测试有重叠，**通过数不能相加**。

| 命令中的测试选择集 | 结果 | 解释 |
| --- | --- | --- |
| `pytest -q vendor/agentsociety2/packages/agentsociety2/tests` | 480 passed，2 项依赖弃用提示 | AS2 全部本地测试；曾因未设 `WORKSPACE_PATH` 和过严标题校验失败，修正后原样复跑通过 |
| `pytest -q tests/footprints tests/evaluation tests/integration/test_verified_memory.py tests/life/test_domain_branch_coverage.py` | 659 passed，14 项字体提示 | 记录、导出、评估、WorldMock 记忆和领域测试；结果早于随后日志与旧测试夹具适配 |
| `pytest -q tests/integration/test_verified_memory.py tests/life/test_domain_branch_coverage.py tests/life/test_money_settlement.py tests/life/test_narration.py tests/life/test_social.py` | 206 passed | 后续日志、冗余接口删除及旧单槽/共同身体测试适配后的窄域复验 |
| `pytest -q tests/footprints tests/integration/test_verified_memory.py tests/life/test_export_integration.py tests/life/test_work_review_protocol.py` | 230 passed | 新增非法来源、乱序、重复、截止边界、零播放及冷启动记忆测试；导出夹具与协议复验 |
| `coverage run -m pytest -q tests/footprints/test_verified_experiences.py tests/integration/test_verified_memory.py` | 17 passed | 使用独立 `/tmp/worldmock-behavior-evidence-narrow.coverage`；对应 JSON 中新增经历、倒序读取、记忆运行时公共接口均无未执行行，不代表全仓门禁 |
| `pytest -q tests/test_style_gate.py` | 1 passed | 源码样式门禁 |
| `python scripts/documentation_gate.py` | 扫描 106 篇，零断链 | 本工作树文档引用门禁 |

上述收据不代表全仓覆盖门禁、独立 Uncle Bob 突变审查或真实供应商长跑已通过。
完整自然日本地集成、最终全仓门禁和独立审查由总台账统一归档。
