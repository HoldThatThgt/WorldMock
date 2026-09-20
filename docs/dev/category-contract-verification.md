# 品类契约实施与验证台账

> 规范：`docs/dev/SPEC-category-contract.md`。本台账记录事实，不变更规范。
> 实施前基线：`426af3bfe3d7e507b749f286e08857e697c2ac2f`；2746 passed、1 failed。
> 失败：`tests/integration/test_life_agent_protocol.py::test_life_tool_schema_fingerprint_pins_registry_digest`。
> 当前状态：实现与专项验证已推进；等待稳定快照的全量门禁与独立 Bob 审查。

| 要求 | 实现位置 | 可执行反例入口 | 状态 |
| --- | --- | --- | --- |
| 品类树与只读语义 | `categories/contract.py`、`common.py` | `test_invalid_category_tree_rejected`、`test_frozen_raw_declarations_are_deep_read_only`、`test_effective_attributes_are_deep_read_only` | implemented；最终门禁进行中 |
| 属性继承与增量 | `categories/contract.py` | `test_relative_child_tracks_parent_value`、`test_candidate_category_values_fill_new_slots_without_overwriting_current_values`、类型与越界参数化反例 | implemented；最终门禁进行中 |
| 对象零规则新增 | `catalog/compiler.py`、`builder.py` | `test_add_object_preserves_manifest_identity_and_contract_fields`、`test_invalid_object_does_not_append_or_bump_manifest`、`test_raw_object_rejects_edges_and_formula` | implemented；最终门禁进行中 |
| 来源与模型边界 | `categories/provenance.py`、`common.py` | `test_missing_and_duplicate_numeric_sources_rejected`、`test_model_fact_and_unreviewed_agent_rejected`、`test_source_evidence_shape_is_strict` | implemented；最终门禁进行中 |
| 三态和稀疏存储 | `categories/pairing.py` | `test_missing_pairing_is_undefined_without_materializing_matrix`、`test_no_effect_requires_review`、`test_no_effect_loads_but_runtime_use_rejects_without_resource_change` | implemented；最终门禁进行中 |
| 唯一最具体配对 | `categories/pairing.py` | `test_direction_is_not_implicitly_reversed`、`test_explicit_undefined_shadows_ancestor_definition`、`test_incomparable_double_specialization_is_ambiguous` | implemented；最终门禁进行中 |
| 场景可触达准入 | `life/category_contract.py` | `test_all_reachable_pairings_reject_undefined_before_artifacts`、`test_initial_inventory_undefined_rejects_before_run_artifacts`、`test_recursive_recipe_output_undefined_rejects_before_run_artifacts` | implemented；最终门禁进行中 |
| 算子及守恒 | `categories/rules.py`、`operators.py` | `test_unknown_operator_and_formula_string_rejected`、`test_every_operator_rejects_the_wrong_resource_shape`、`test_fixed_runtime_resources_reject_same_domain_aliases`、`test_closed_inventory_requires_equal_quantities` | implemented；最终门禁进行中 |
| 真实结算与系数 | `life/semantics.py`、`work.py`、`engine.py` | `test_purchase_coefficient_changes_real_life_engine_transfer`、`test_food_coefficient_changes_real_life_engine_body_settlement`、`test_travel_coefficient_changes_real_life_engine_payment`、`test_work_coefficient_changes_real_life_engine_payment` | implemented；最终门禁进行中 |
| 库存转换 | `life/inventory_recipe.py` | `test_recipe_completes_once_and_replayed_action_is_idempotent`、`test_recipe_cancel_and_insufficient_inventory_leave_no_conversion`、`test_recipe_ledger_failure_rolls_back_activity_inventory_and_footprint` | implemented；最终门禁进行中 |
| 细化影响与触达统计 | `categories/maintenance.py` | `test_refinement_uses_current_attributes_and_reports_unique_ambiguous_and_unmatched`、`test_refinement_reports_rule_and_pairing_changes_and_raw_touch_counts` | implemented；最终门禁进行中 |
| 新版本发布 | `categories/maintenance.py` | `test_publish_runs_actual_scenario_admission_and_preserves_source`、`test_publication_failure_has_no_destination_or_source_changes`、`test_publication_rejects_invalid_requests`、`test_destination_created_during_validation_is_not_overwritten` | implemented；最终门禁进行中 |
| 运行绑定与导出 | `life/run_identity.py`、`runtime.py` | `test_open_rejects_same_version_changed_content_without_mutating_run`、`test_resume_and_replay_bind_original_identity_before_publishing_branch`、`test_running_semantics_and_export_keep_original_category_identity` | implemented；最终门禁进行中 |
| 文档及文件一致性 | 规格、接口合同、引擎设计、实验与目录说明 | 文档独立复核、`documentation_gate.py`、目录再生成与最终 Git diff | implemented；最终门禁进行中 |

实现路径以 `src/worldmock/` 为根。品类测试位于 `tests/categories/`，对象维护测试位于
`tests/catalog/`，真实动作测试位于 `tests/life/test_category_runtime.py`，身份测试位于
`tests/integration/test_category_identity.py`。矩阵按行为组合，不以函数数量充当规格覆盖比例。

## 命令与证据

独立文档复核完成，生成目录保持 163 个对象与 5 个资源；现有对象没有虚构库存转换用途。
库存转换使用隔离场景验证真实动作、世界状态和足迹事务，正式场景的输出绑定保持为空。

实施前唯一失败是工具 Schema 摘要断言与现行请求模型文档字符串不一致。
核对实际生成 Schema 后重钉摘要，并保留注册表变化必须改变指纹的独立断言；未修改人物工具协议。

全量门禁、稳定提交身份、Bob 变异结果和最终文件修改清单在最终验收后登记。
