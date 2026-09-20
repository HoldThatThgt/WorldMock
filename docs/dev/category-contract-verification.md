# 品类契约实施与验证台账

> 规范：`docs/dev/SPEC-category-contract.md`。本台账记录事实，不变更规范。
> 实施前基线：`426af3bfe3d7e507b749f286e08857e697c2ac2f`；2746 passed、1 failed。
> 失败：`tests/integration/test_life_agent_protocol.py::test_life_tool_schema_fingerprint_pins_registry_digest`。
> 历史特性状态：下述固定源码的全部必需验收项已闭合，功能实现、独立语义突变复验、工程门禁及文档一致性通过；无延期项。本台账不证明后续地图主线组合已通过。

下述 3008 项门禁与 136 项语义突变是合入主分支前的独立特性快照。
合并审查发现并修复了人物模型侧的库存使用合同与份数比较缺口；
该次完整结果以[合并验证记录](category-contract-main-verification.md)的 3166 项门禁及新增独立复验为准；
后续供给组合的新增合同见现行规格，不能沿用这里的历史计数。

| 要求 | 实现位置 | 可执行反例入口 | 状态 |
| --- | --- | --- | --- |
| 品类树与只读语义 | `categories/contract.py`、`common.py` | `test_invalid_category_tree_rejected`、`test_frozen_raw_declarations_are_deep_read_only`、`test_effective_attributes_are_deep_read_only` | 已实现；验证通过 |
| 属性继承与增量 | `categories/contract.py` | `test_relative_child_tracks_parent_value`、`test_proportional_values_multiply_the_inherited_base`、`test_child_schema_keeps_all_ancestor_constraints`、`test_child_cannot_make_an_inherited_required_slot_optional` | 已实现；验证通过 |
| 对象零规则新增 | `catalog/compiler.py`、`builder.py` | `test_add_object_preserves_manifest_identity_and_contract_fields`、`test_invalid_object_does_not_append_or_bump_manifest`、`test_raw_object_rejects_edges_and_formula` | 已实现；验证通过 |
| 来源与模型边界 | `categories/provenance.py`、`common.py` | `test_missing_and_duplicate_numeric_sources_rejected`、`test_model_fact_and_unreviewed_agent_rejected`、`test_source_evidence_shape_is_strict`、`test_every_reviewer_requires_nonempty_authorization` | 已实现；验证通过 |
| 三态和稀疏存储 | `categories/pairing.py` | `test_missing_pairing_is_undefined_without_materializing_matrix`、`test_no_effect_requires_review`、`test_no_effect_loads_but_runtime_use_rejects_without_resource_change`、`test_no_effect_work_loads_and_rejects_settlement_without_payment` | 已实现；验证通过 |
| 唯一最具体配对 | `categories/pairing.py` | `test_direction_is_not_implicitly_reversed`、`test_explicit_undefined_shadows_ancestor_definition`、`test_incomparable_double_specialization_is_ambiguous`、`test_incomparable_pairings_remain_ambiguous_with_unequal_total_depth` | 已实现；验证通过 |
| 场景可触达准入 | `life/category_contract.py` | `test_all_reachable_pairings_reject_undefined_before_artifacts`、`test_initial_inventory_undefined_rejects_before_run_artifacts`、`test_recursive_recipe_output_undefined_rejects_before_run_artifacts` | 已实现；验证通过 |
| 算子及守恒 | `categories/rules.py`、`operators.py` | `test_unknown_operator_and_formula_string_rejected`、`test_every_operator_rejects_the_wrong_resource_shape`、`test_fixed_runtime_resources_reject_same_domain_aliases`、`test_closed_inventory_requires_equal_quantities`、`test_money_operators_preserve_exact_amounts_independently_of_decimal_context` | 已实现；验证通过 |
| 真实结算与系数 | `life/semantics.py`、`work.py`、`engine.py` | `test_purchase_coefficient_changes_real_life_engine_transfer`、`test_food_coefficient_changes_real_life_engine_body_settlement`、`test_travel_coefficient_changes_real_life_engine_payment`、`test_work_coefficient_changes_real_life_engine_payment`、`test_hourly_and_piece_coefficients_change_real_submit_work_payment` | 已实现；验证通过 |
| 库存转换 | `life/inventory_recipe.py` | `test_recipe_completes_once_and_replayed_action_is_idempotent`、`test_recipe_cancel_and_insufficient_inventory_leave_no_conversion`、`test_recipe_ledger_failure_rolls_back_activity_inventory_and_footprint`、`test_recipe_completion_sql_failure_keeps_engine_and_store_unchanged`、`test_recipe_pause_wait_resume_produces_output_only_once_at_completion`、`test_tiny_recipe_quantity_changes_both_real_inventory_balances`、`test_recipe_completion_fixture_settles_both_balances_and_effects`、`test_recipe_completion_rechecks_pairing_before_inventory_or_effects` | 已实现；验证通过 |
| 模型侧库存使用 | `life/observe.py`、仓库根目录下 `custom/agents/life_inventory.py` | `test_inventory_only_recipe_survives_real_model_roundtrip`、`test_insufficient_recipe_inventory_is_rejected_by_real_settlement`、`test_held_food_can_be_eaten_without_a_visible_purchase_offer` | 合并补修；验证通过 |
| 细化影响与触达统计 | `categories/maintenance.py` | `test_refinement_uses_current_attributes_and_reports_unique_ambiguous_and_unmatched`、`test_refinement_reports_rule_and_pairing_changes_and_raw_touch_counts`、`test_refinement_lists_parent_child_and_grandchild_objects_with_their_current_categories`、`test_refinement_reports_removed_rule_and_pairing_with_complete_before_and_after` | 已实现；验证通过 |
| 新版本发布 | `categories/maintenance.py` | `test_publish_runs_actual_scenario_admission_and_preserves_source`、`test_publication_rejects_real_undefined_route_without_destination_or_source_changes`、`test_publication_rejects_invalid_requests`、`test_atomic_publication_preserves_competing_empty_directory`、`test_publication_rejects_nonincreasing_version_when_catalog_and_real_scenario_are_valid` | 已实现；验证通过 |
| 运行绑定与导出 | `life/run_identity.py`、`runtime.py` | `test_open_rejects_same_version_changed_content_without_mutating_run`、`test_resume_and_replay_bind_original_identity_before_publishing_branch`、`test_running_semantics_and_export_keep_original_category_identity` | 已实现；验证通过 |
| 文档及文件一致性 | 规格、接口合同、引擎设计、实验与目录说明 | 文档独立复核、`documentation_gate.py`、目录再生成与最终 Git diff | 已实现；验证通过 |

实现路径以 `src/worldmock/` 为根。品类测试位于 `tests/categories/`，对象维护测试位于
`tests/catalog/`，真实动作测试位于 `tests/life/test_category_runtime.py`，身份测试位于
`tests/integration/test_category_identity.py`。矩阵按行为组合，不以函数数量充当规格覆盖比例。
模型到结算再到下一轮观察的用例在 `tests/integration/test_category_inventory_protocol.py`。

## 命令与证据

独立文档复核完成，生成目录保持 163 个对象与 5 个资源；现有对象没有虚构库存转换用途。
库存转换使用隔离场景验证真实动作、世界状态和足迹事务，正式场景的输出绑定保持为空。

实施前唯一失败是工具 Schema 摘要断言与现行请求模型文档字符串不一致。
核对实际生成 Schema 后重钉摘要，并保留注册表变化必须改变指纹的独立断言；未修改人物工具协议。

首轮审查前，固定提交 `c582330140dd80f43dd4d67853e0eb27e83e9476` 上完整执行覆盖率门禁：
`2781 passed`、`0 skipped`、分支覆盖率 `96.52%`、未覆盖公共接口 `0`，门禁通过。
目录生成检查四个产物逐字节一致；文档门禁检查 38 份文档、零失效引用。
本机历史资金锚定数据库以只读链接接入隔离工作树，没有伪造锚定数据。

原始对象不再接受旧规则字段，旧 `Edge` 类型别名、文本票价规则和场景迁移脚本已删除。
首轮 Bob 找出的实现问题与测试判据缺口均已修复，并在新干净提交上重放原语义。
[独立审查记录](category-contract-bob-review.md)保留首轮失败、修复复审、准确补丁与恢复证据。
三个审查范围共 136 项有效语义突变验证中，被破坏的行为均被测试拦住，没有存活突变；冗余守卫的等价删除不计有效数量。

## 最终工程门禁

被测提交：`5bd851186e8124b19d5a1786ea4c858be338ef4e`。
覆盖率快照：`93195358-a595-4f68-b4bf-b9bfa80e6d86`，生成于 `2026-09-20T09:11:41+00:00`。

| 观察项 | 实测结果 |
| --- | --- |
| 全仓 pytest | 3008 passed、0 failed、0 skipped；24 条既有依赖/序列化警告 |
| 分支覆盖率 | 96.66%，6155 / 6368，阈值 85.00% |
| 公共接口缺行 | 0 |
| 顶层门禁判定 | PASSED，退出码 0 |
| 目录再生成 | resources、objects、tombstones、manifest 四个产物逐字节一致 |
| 文档链接 | 40 份文档，0 失效引用 |
| 新增代码与测试的占位或跳过标记 | 未发现 TODO、FIXME、NotImplementedError、skip 或 xfail |

覆盖率运行与独立 Bob 并发，使用既有 `coverage_gate.main()`，只将 `REPORT_PATH` 临时指向仓库外。
没有修改判定、测试、覆盖率配置或源代码，报告生成后原样写回 `docs/dev/coverage-report.json`。
精确环境、命令与全量原始输出见[工程门禁证据包](evidence/category-contract/final-gates.json.gz)。

特性提交 `02a54ddba5625b50284519f2da1333bc461656c2` 的修改范围见[逐文件清单](category-contract-files.md)，
与实施前基线到该特性提交的实际 Git 差异核对；此处的门禁是合入主分支前的独立特性快照。
本次规格的功能要求无延期项；正式十二周模型运行、隔离盲评与真实 LabelKit 自然日交付仍按项目级验收独立判定。
Linux 排他重命名已验证调用参数，未在 Linux 文件系统实测；macOS 发布竞争已用真实目录和原生系统调用验证。
