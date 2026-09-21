# 规格—测试用例覆盖率追溯矩阵

品类契约新增要求的逐行映射统一见 `docs/dev/category-contract-verification.md`，对应规范为
`docs/dev/SPEC-category-contract.md`。本文下述 435 条及历史覆盖率仅描述原统计快照，不包含该新增特性；
新增特性各有独立完整台账，仍不得把历史比例当作当前全规格覆盖率。

> 状态：分析文档，不拥有规范权威——本文件只回答「每条规格要求是否有测试覆盖、缺口在哪」，不定义、不裁决规格；条款的内容与判定以现行文档树为准
> 日期：2026-09-20（增量修订；原基线 2026-09-18）
> 审计：本轮只读审计；终态：独立复核
> 范围：`tests/`（九个子目录 + 根）与 `vendor/agentsociety2/packages/agentsociety2/tests/`（含 worldmock hooks）的测试函数与现行文档树条款的映射

覆盖对象在现行文档树中的位置：接口签名、字段、枚举、数据表与 CLI 在 `docs/CONTRACTS.md`；AS2 集成与改动在
`docs/dev/SPEC-as2-integration.md`；LifeEnv 运行时行为、结算与场景规则在 `docs/dev/SPEC-life-runtime.md`；人物决策、
记忆、动机、日程与画像在 `docs/dev/SPEC-life-realism.md`；数字足迹的保存边界与一致性在 `docs/design/05-recording-engine.md`；
交付 LabelKit 的行为与验证步骤在 `docs/dev/SPEC-labelkit-delivery.md`；放行门禁与真实性评估边界在
`docs/VERIFICATION.md`；实验身份、时间窗、依赖与场景参数在 `docs/EXPERIMENT.md`；设计理由与数据流在
`docs/ARCHITECTURE.md`；文件归属在 `docs/INDEX.md`。

决策上下文特性的完整条款到真实调用路径与测试的映射，见
[本次验收记录](research/decision-context-verification.md#条款与可观察证据)。
其中记录本次源码身份、全量门禁、独立审查与源码突变结果，不用下述历史函数计数替代。

交互菜单特性的逐项映射见 [交互菜单验收记录](research/interaction-menu-verification.md#条款与行为证据)，
规范权威是 [交互菜单特性规格](SPEC-interaction-menu.md)。新增测试覆盖实际模型请求、环境结算、
候选排除统计、暂停恢复、场景身份与真实跨轮观察；最终门禁和源码突变以该记录为准。
与品类契约主线组合后的源码身份和全仓门禁见
[交互菜单主线合并验收](research/interaction-menu-main-verification.md)，各独立审查保留原快照边界。


## 地图与门店供给增量映射

完整行为合同为 [开发规格](SPEC-map-and-storefront.md)，命名要求与设计/测试影响见
[文档一致性审查](research/map-and-storefront-document-impact.md)。以下为独立地图分支已验收的具体断言映射，不能纳入旧覆盖百分比；本次主线组合另有下节的独立验收，
完整门禁与独立突变的初次发现、补测和复验见 [本特性验收记录](research/map-and-storefront-verification.md)。

| 命名要求 | 实际测试入口与可观察断言 | 当前证据边界 |
| --- | --- | --- |
| 地图唯一来源与路线状态 | `tests/life/test_map_dataset.py` 的 `test_selected_map_preserves_unknown_route_and_instance_stock`、`test_missing_route_record_cannot_mean_unreachable`、`test_reachable_routes_require_actual_geometry_mode_and_metrics`；`tests/life/test_scenario.py` 校验正式引用与关系 | 生产准入反例和最终完整门禁均通过 |
| 地点分类与资格 | `tests/life/test_activity_location_kinds.py` 的实际启动门；`tests/life/test_storefront_arrival.py` 的到达拒绝和真实暂停后跨地点恢复；`tests/integration/test_storefront_tools.py::test_menu_denies_discovery_in_schema_local_validation_and_environment` | 真实暂停、跨地点恢复、到达与工具门全部通过 |
| 多门店供给与时态 | `test_storefront_commerce.py::test_single_supply_changes_only_selected_shop_and_preserves_sources`；`test_opening_hours.py` 的半开、跨夜、例外和时区；`test_storefront_schedule.py` 的声明切换、真实调价来源和补货 | 商务、预排事件及完整门禁通过 |
| 来源日期与报价身份 | `test_map_dataset.py::test_review_cannot_claim_facts_after_the_source_cutoff`；报价与营业不能晚于各自来源可信截止日；`test_scenario.py` 的供给报价版本不重复 | 来源日期与报价身份通过最终固定源码复验 |
| 发现与可见凭据 | `test_storefront_discovery.py::test_real_discovery_round_reinjects_only_returned_supplies_and_fits_model_budget`、`test_every_real_discovery_page_is_usable_after_world_advances`、`test_presenting_another_supply_does_not_renew_old_supply_visibility` | 真实跨轮结果、分页与逐供给有效期断言已经落盘 |
| 直接及有时长浏览 | `test_storefront_discovery.py::test_real_product_browse_returns_only_its_window_and_fits_model_budget`、`test_public_browse_exclusion_groups_restore_every_original_fact` | 两种浏览路径均以实际窗口授权，公开排除组可还原原始事实 |
| 共同窗口与变化事实 | `test_storefront_discovery.py::test_shared_observation_window_keeps_changed_returned_supply_and_only_fills_remaining_slots`、`test_changed_supply_fact_can_restore_null_nested_source_price_opening_and_stock` | 回灌优先、不更新窗口外凭据，差异按键还原包括空值与嵌套对象 |
| 整单与履约 | `test_storefront_commerce.py::test_whole_order_rejections_are_atomic`、`test_fulfillment_rejections_are_atomic`、`test_delivery_is_due_atomic_and_idempotent_with_truthful_evidence`、`test_zero_duration_delivery_at_same_location_is_immediate_with_delivery_evidence` | 同地点同卖方、重复拒绝、全部副作用原子、一次交付与即时配送定向通过 |
| 旅行后条件重验 | `test_storefront_arrival.py::test_purchase_rechecks_changed_conditions_after_real_travel_without_reversing_it`、`test_arrival_rechecks_access_and_records_denial_without_undoing_real_travel` | 真实道路、交通费、位置与旅行证据保留，拒绝原因与下一决策可见 |
| 恢复与完整流程 | `test_storefront_recovery.py::test_discovery_purchase_consumption_and_recovery_preserve_storefront_evidence`、`test_reopening_rejects_corrupted_storefront_facts`、`test_reopening_rejects_map_identity_substitution` | 真实发现、出行、自提/配送、消费、关库重开与共同检查点分支；严格来源和可见凭据反例 |
| 测试有效性 | 固定源码全量测试、分支与公共接口覆盖、风格与文档门、独立语义突变及临时修改恢复 | 全部通过；两项初次变异漏检经补测复验关闭 |

## 品类主线与门店供给组合验证边界

组合合同保留品类配对和库存使用/转换，以供给报价、数量和系数求每行付款，卖方取供给账户；
目录 `4.0`、品类契约与规则绑定版本 `2` 区别于两条分支的旧身份。以下交接已独立通过离线验收，
完整源码、命令和 Bob 原始记录见 [主线组合验收](research/map-and-storefront-main-verification.md)，不改写上方独立特性与下方历史条款计数。

| 交接要求 | 具体测试入口与行为断言 | 当前证据边界 |
| --- | --- | --- |
| 独立供给与整行计价 | `tests/life/test_storefront_category_integration.py::test_independent_quotes_share_exact_category_arithmetic_in_real_engine`：真实发现后分别购买同商品的两家供给；原报价不变，三件金额按原价乘数量乘系数再上整分；实际卖方、账户、库存与订单逐项对应；`tests/categories/test_rules.py::test_catalog_preserves_purchase_coefficient_for_exact_dynamic_quote_amount` 验证目录保留动态报价算子系数 | 完整门禁通过；购买组合的七项独立变异均 killed |
| 配对拒绝与零系数 | `tests/life/test_storefront_category_integration.py::test_purchase_pairing_recheck_refuses_without_any_domain_state_change`、`test_defined_zero_coefficient_purchase_fulfills_without_account_transfer`、`test_zero_base_quote_remains_invalid_in_frozen_and_persisted_models`：undefined/no_effect 明确拒绝且无状态变化；defined 零系数正常交付，正原价合同不变 | 完整门禁与相关回归通过 |
| 公开比较事实 | `tests/life/test_storefront_discovery.py::test_discovery_exposes_category_amount_without_rewriting_quote`、`test_no_effect_supply_remains_visible_without_a_purchase_amount`：原始报价/版本保留，系数和一份实际金额可见；未知报价为空，非作用供给可见但不可买 | 完整门禁通过 |
| 动机余额与持有使用 | `tests/life/test_motivation.py::test_food_affordability_uses_category_amount_instead_of_raw_quote`、`test_no_effect_purchase_does_not_hide_held_food`：一份算子金额用于成本和余额边界；非作用供给不求价，已持有食物继续可用 | 完整门禁通过；不以目录原价冒充实际付款 |
| 地图准备与出行 | `tests/life/test_map_dataset.py::test_preparation_fetches_full_selected_set_and_publishes_verifiable_version`：准备产物可重新加载，步行费为零；`tests/life/test_scenario.py::test_route_fares_follow_the_catalog_exchange_rule` 校验实例费率；`tests/life/test_category_runtime.py::test_travel_coefficient_changes_real_life_engine_payment` 与 `tests/categories/test_rules.py::test_travel_applies_minimum_before_coefficient_and_final_rounding` 核对真实出行付款和闭集取整 | 完整门禁、正式场景准入和目录四输出复现通过；准备与运行均使用现行 `TravelFareRule` |
| 库存使用合同 | `tests/integration/test_category_inventory_protocol.py::test_inventory_only_recipe_survives_real_model_roundtrip`、`test_held_food_can_be_eaten_without_a_visible_purchase_offer`：未出售物品也能经实际模型工具使用和库存转换，不为使用补造供给 | 完整门禁通过，保留主线库存能力 |
| 恢复内容身份 | `tests/integration/test_category_identity.py::test_open_rejects_missing_category_identity_fields`、`test_open_rejects_same_version_changed_content_without_mutating_run`、`test_resume_and_replay_bind_original_identity_before_publishing_branch`；`tests/life/test_storefront_recovery.py::test_reopening_rejects_map_identity_substitution`：品类与地图身份拒绝篡改，恢复/分支及导出保持原身份 | 完整门禁通过；品类版本和地图供给摘要漏验突变分别 killed |
| 真实提交失败与成功重试 | `tests/life/test_storefront_recovery.py::test_failed_decision_commit_preserves_visible_supply_credentials`；`tests/life/test_category_runtime.py::test_recipe_completion_sql_failure_keeps_engine_and_store_unchanged`：真实 SQLite 触发器拒绝后世界、持久状态、事件及两种缓存不变，成功重试后缓存清空且库存转换完成 | 初次状态审查 4 killed、1 survived 保留；`6fe3ab7` 加固成功断言后，原样呈现缓存变异及独立供给缓存变异均 killed |

完整门禁绑定 `70af9a7`：3550 passed、4 个既有缺历史产物用例跳过，分支 96.48%（6653/6896）、公共接口缺口 0。
`6fe3ab7` 只增加成功后的两条断言并保存覆盖率报告，生产源码未变；相关回归 73 passed，不是新的全量计数。
正式十二周场景的素材可达性守卫覆盖 7384 条素材、142 个分区并全部通过，不能替代十二周真实模型运行。

## 历史基线说明


2026-09-20 审查基线发现，候选 `location_kinds` 算法有测试，但当时 `LocationConfig` 与正式输入没有该能力集合。
下述历史条款计数只说明当时映射判断，不能解释为当前全项目端到端规格全部完成。
当时交互菜单使用已有单值地点 `kind`，并未补齐活动环境输入；后续地图特性已显式提供 `location_kinds`，不改写上述历史计数。

2026-09-18 历史基线的测试函数总数为 **1789**（同步 1718 + 异步 71；全部函数名索引建立后逐条映射；计数口径与两种独立计数方法见文末覆盖边界）。
终态独立复核（2026-09-18）逐个亲读测试函数断言（不凭修复报告转述）核实五个 GAP 均已落地并如实覆盖；
条款覆盖判定与命令输出见文末 GAP 清单复核记录。
**2026-09-19 增量**：补入 `docs/dev/SPEC-life-runtime.md`「候选契约与呈现规则」章与
`docs/dev/SPEC-life-realism.md`「`location_kinds` 的运行期强制语义」「素材可达性」「素材淘汰与检索机制的禁止项」
「候选与呈现」四节的新增条款，共 44 行。该增量落地后**复判**：其中 37 行记 `covered`、7 行记 `gap`（`CC43`、`CC44` 经第二轮复判由 `gap` 改判 `covered`），
原因逐条见该章与文末覆盖边界。
条款总数由 391 重算为 435（新增 44 行），覆盖率由 100.0%（391/391）重算为
**98.4%**（428/435 = covered 414 + exempt 14）。
**2026-09-19 第三次增量（缺口清零）**：为「候选契约与呈现规则」章余下的 7 行 `gap`
补入真实性测试，逐行改判 `covered`（`CC7`、`CC17`、`CC18`、`CC29`、`CC32`、`CC35`、`CC40`）。
新增与加固的测试共 19 个函数：新建 `tests/life/test_candidate_contract_boundaries.py`（9 测，
覆盖 `CC7`、`CC17`、`CC18`），`tests/life/test_candidates.py` 增 2 测（`CC35`、`CC32`），
`tests/life/test_retrieval.py` 增 2 测与 1 测加固（`CC40`），`tests/life/test_rotation.py` 增 6 测（`CC29`）；
**未改动任何生产代码**。条款总数 435 不变，covered 由 414 重算为 **421**，gap 由 7 收窄为 **0**，
覆盖率由 98.4% 重算为 **100.0%**（435/435 = covered 421 + exempt 14），
即 (covered + exempt) / 条款总数 = (421 + 14) / 435 = 435 / 435 = 1.000。

统计口径：
- **条款** = 规格中一条可独立验证的功能要求（排除纯叙述、依据引用、管理性声明）。同一要求在规格
  多处重述时合并为一行（去重键 = 需求语义，不是行号）。
- **covered** = 至少映射一个实际存在的测试函数；**gap** = 无任何测试映射；**exempt** = 规格验证
  矩阵中"实际验证入口"为真实模型/真实外部服务/数天墙钟的条款（确定性部分已由锚测试覆盖）。
- 覆盖率 = (covered + exempt) / 条款总数。

目录缩写：L=tests/life，I=tests/integration，F=tests/footprints，CAT=tests/catalog，
CO=tests/corpora，D=tests/delivery，E=tests/evaluation，P=tests/profile，R=tests/resources，
V=vendor/agentsociety2/packages/agentsociety2/tests。

## 统计

| 指标 | 数值 |
| --- | --- |
| 条款总数 | 435 |
| covered | 421（96.78%） |
| gap | **0**（历史缺口 GAP-1…GAP-5 均已修复；「候选契约与呈现规则」章余下的 7 条已于 2026-09-19 第三次增量逐条补测清零，逐条见该章与文末覆盖边界） |
| exempt | 14（3.2%） |
| 覆盖率（covered+exempt） | **100.0%**（435/435） |

抽样自验：20 条映射逐个打开测试函数体核对，20/20 吻合，0 错配（见文末）。

## 设计结论

覆盖对象：`docs/ARCHITECTURE.md` 的「核心决策」章与「整体架构」章。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| D1 | 人物决定动作，环境执行可行性与资源约束 | L/test_engine_round.py::test_real_wait_round_advances_and_records_complete_activity、test_business_rejection_is_evidence_and_not_activity | covered |
| D2 | 世界状态由环境确定性代码维护；人物不能直接设置余额/身体/验收 | I/test_life_agent_protocol.py::test_life_agent_schema_is_closed_and_host_fields_are_hidden；I/test_grounded_targets.py::test_union_enum_still_requires_exact_kind_and_target_pair | covered |
| D3 | 统一模拟时间；程序速度与生活时间分开 | L/test_engine_round.py::test_context_with_wrong_time_is_rejected；F/test_quality.py::test_acceleration_sums_observed_wall_time_across_restore_branches | covered |
| D4 | 内容逐段提供，保留来源/版本/区间 | L/test_content_game.py::test_subtitles_reveal_only_played_intervals_across_pause_and_repeat | covered |
| D5 | SQLite 事件 + 文件素材双轨 | F/test_store.py::test_missing_or_damaged_asset_prevents_commit_and_checkpoint | covered |
| D6 | 固定 JSONL 交付；成品可追原始事件 | D/test_training.py::test_target_event_identities_are_added_only_after_semantic_review | covered |
| D7 | 恢复建立父子关联新分支；原运行部分保留 | F/test_store.py::test_complete_checkpoint_branch_preserves_parent_and_exact_workspace | covered |
| D8 | 人格/模型/提示/规则/代码运行前冻结 | I/test_runtime.py::test_model_identity_freezes_complete_strict_endpoint_and_request、test_runtime_code_identity_is_deterministic | covered |

## 目标、边界与验收对象

覆盖对象：`docs/ARCHITECTURE.md` 的「设计目标与边界」章（含「必须达到的行为」节）；验收对象一侧另见 `docs/VERIFICATION.md` 的「放行门禁」章与「测试矩阵」章。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| G1 | 进食投金钱时间；工作获合同报酬；睡眠恢复 | L/test_commerce.py::test_browse_purchase_replay_and_remote_delivery_use_one_canonical_payment；L/test_work_contracts.py::test_hourly_and_piece_contracts_use_actual_time_and_frozen_piece_count；L/test_body_flows.py::test_sleep_recovers_deficit_and_decays_pressure | covered |
| G2 | 职业/技能/机会/合同影响产出报酬；睡眠影响效率 | L/test_work_efficiency_factors.py::test_hungry_band_interval_average_with_skill、test_factors_assembled_from_catalog | covered |
| G3 | 需求进入观察与决策，非脚本排序 | L/test_motivation.py::test_goal_deficit_lifts_work_candidates；L/test_grounded_observation.py::test_motivation_context_wires_enabled_set_and_recent_meals | covered |
| G4 | 内容覆盖饮食/节目/游戏/工作/邮件/导航/购物 | CO/test_category_corpora.py（17 测全量）；CO/test_corpora.py::test_formal_manifest_validates_every_file_and_selected_counts | covered |
| G5 | 看见同处他人、通讯、晨间例程与用餐、当日目标；机会由世界提供 | L/test_social.py；L/test_day_plan.py；CO/test_category_corpora.py | covered |
| G6 | 场景可定义地点/日历/天气/交通/速度 | L/test_scenario.py::test_formal_and_multi_person_scenarios_are_valid；L/test_travel_time.py::test_factor_lookup_matches_the_snapshot_table_for_every_hour | covered |
| G7 | 区分来源数据/规则结果/模型文本/外部服务返回 | F/test_quality.py::test_actual_unpriced_model_keeps_tokens_but_does_not_invent_cost、test_transport_failure_marks_tokens_and_cost_unknown | covered |
| G8 | 完整数字足迹（观察/决策/执行/状态/结果/记忆/关联） | F/test_store.py（15 测）；F/test_event_fields.py | covered |
| G9 | LabelKit 可加工输入与来源关联 | D/test_windows.py、D/test_training.py::test_semantic_prompt_requires_every_citation_to_use_the_exact_allowed_set | covered |
| G10 | 训练加载器可读成品；规则程序可读夹具执行比较 | D/test_training.py::test_real_dataset_loading_preserves_all_facts_and_separates_answer；D/test_rules.py::test_real_rule_engine_positive_missing_reordered_timeout_and_duplicate | covered |

## AS2 改动清单

覆盖对象：`docs/dev/SPEC-as2-integration.md` 的「AS2 核心改动清单」章（决策调度改动、时间约定统一、确定性工具调用与身份绑定、失败传播改动、恢复与检查点改动、决策前决策自述与推理内容保留、记忆运行时改动、模型客户端生命周期管理、观察记录单写各节）。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| A1 | 按可决策人物执行 batch、收齐有序结算、下一边界、检查执行结果；睡眠不反复调模型；较早结束不被跨过 | V/test_society_decision_round.py（8 测：uses_environment_order_then_commits、redecides_before_advancing、aborts_before_commit_on_agent_failure 等） | covered |
| A2 | 确定性工具调用、调度状态传递、同步时钟等待完成；结果不经第二模型改写 | V/test_direct_router.py::test_direct_router_delegates_typed_call、test_direct_router_rejects_text_and_ambiguous_environment；V/test_decision_round.py::test_decision_context_requires_aware_time | covered |
| A3 | 宿主绑定身份/决策/动作标识；参数不能改变调用者 | V/test_decision_round.py::test_decision_context_is_immutable_and_complete；I/test_life_agent_protocol.py::test_life_agent_schema_is_closed_and_host_fields_are_hidden | covered |
| A4 | 明确工具清单与调用证据回调；保留模型调用与 ReAct | I/test_grounded_evidence.py::test_model_request_persists_exact_grounded_tools | covered |
| A5 | 预算耗尽明确失败；禁止 finish 混批；删除合成结束路径 | V/test_agent_react_validation.py::test_finish_mixed_with_action_is_rejected_atomically、test_loop_exhaustion_is_fatal、test_free_text_never_becomes_a_tool_call | covered |
| A6 | 记忆校验/归纳/保存失败向上传播；删除宽松转换 | V/test_worldmock_life_hooks.py::test_memory_validation_failure_propagates、test_memory_save_failure_propagates、test_silent_zero_persist_is_a_save_failure；V/test_memory_restore.py | covered |
| A7 | 必要观察/证据保存致命异常穿过 catch 传播 | V/test_direct_runner_failures.py::test_runner_preserves_step_and_close_failures | covered |
| A8 | 保存失败/观察失败/人物失败向上报告停止运行；路由汇总保存异常抛出 | V/test_society_decision_round.py::test_society_records_checkpoint_failure_as_run_failure；I/test_runtime.py::test_society_build_failure_terminates_created_actor | covered |
| A9 | LiteLLM Router.discard() 回调生命周期；异常出口均清理 | V/test_llm_callback_lifecycle.py（9 测，含 test_more_than_one_hundred_task_loops_leave_no_router_callbacks） | covered |
| A10 | 一个推进边界形成共同检查点；恢复校验世界/人物/时钟 | V/test_society_decision_round.py::test_society_checkpoints_counterfactual_before_first_decision；F/test_store.py::test_checkpoint_event_must_match_committed_identity_and_clock | covered |
| A11 | 恢复入口收父检查点+新目录；全部成功才启动；删除失败后新建 | V/test_society_decision_round.py::test_society_init_checkpoint_failure_records_run_failure；I/test_runtime.py::test_restore_rejects_corrupt_used_memory_before_branch_publish | covered |
| A12 | 观察记录只保留结构化 data，不写 JSON 副本 | V/test_worldmock_life_hooks.py::test_structured_observation_appears_once_in_user_message、test_string_observation_keeps_single_text_copy | covered |
| A13 | 决策前决策自述钩子（单次无工具）；后续请求保留 reasoning_content | V/test_worldmock_life_hooks.py::test_prelude_decision_statement_hook_runs_one_toolless_call、test_reasoning_content_survives_into_followup_request | covered |
| A14 | finish 空记忆；巩固触发/提示传卡片/删示例身份；相似合并 | V/test_worldmock_life_hooks.py（consolidation 触发×3、persona_card 提示、bigram 合并×3）；V/framework/test_memory.py；I/test_verified_memory.py | covered |
| A15 | 禁止 monkey patch/复制调度器/两套决策循环（接入走真实 AS2 路径） | 结构性：I/test_runtime.py::test_runtime_code_identity_is_deterministic + 集成测试实例化真实 AS2 类（V/framework/test_registry_person.py::test_builtin_registry_discovers_person_agent） | covered |

## LifeEnv 状态与接口

覆盖对象：`docs/CONTRACTS.md` 的「LifeEnv 状态字段」章（状态归属、资源与需求字段、生命周期、人物工具与管理接口、当日目标请求、请求字段与结算检查各节）。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| E1 | 世界状态仅环境规则改变；管理员只运行前配置 | CAT/test_admission.py、L/test_scenario.py::test_catalog_pin_and_reference_admission_are_enforced | covered |
| E2 | 身体/资产/活动/工作/内容接触由环境验证与结算 | L/test_engine_round.py、L/test_money_settlement.py | covered |
| E3 | 环境不代写人物记忆 | F/test_quality.py::test_finish_memory_can_reference_its_observation_and_visible_prior_result（记忆来自人物 finish） | covered |
| E4 | 观察只给体感分级+自然语言；准确数值只进足迹评估 | L/test_felt_states.py::test_summarize_felt_levels；L/test_revision_contract.py::test_felt_band_hysteresis_uses_last_decision_reference；L/test_money_settlement.py::test_shared_body_event_carries_validated_body_flows | covered |
| E5 | 导出按目标系统信息范围裁剪 | D/test_windows.py（PRIVATE_EVENTS 过滤，私有事件集合定义在 `src/worldmock/delivery/windows.py`）；E/test_adapters.py | covered |
| E6 | __init__ 仅存配置不创建世界；无参仅注册检查 | V/test_direct_router.py::test_direct_environment_is_accepted_without_decorated_tools | covered |
| E7 | init 核验目录/规则/世界；缺场景明确失败不生成默认世界 | F/test_validation.py::test_validate_project_passes_and_writes_persona_coverage；I/test_life_agent_protocol.py::test_action_schema_requires_prepared_environment_catalogs | covered |
| E8 | step(tick,t) 连续变化与到期事件、时间连续性核验 | L/test_engine_round.py::test_context_with_wrong_time_is_rejected；V/test_decision_round.py::test_scheduling_state_requires_future_boundary | covered |
| E9 | to_workspace 错误必须传播 | V/test_direct_runner_failures.py::test_runner_preserves_step_and_close_failures；F/test_store.py::test_sql_failure_rolls_back_money_and_all_events | covered |
| E10 | restore 核对代码/规则/数据/时间/人物版本 | F/test_store.py::test_rehashed_checkpoint_with_inconsistent_world_is_rejected；I/test_runtime.py::test_restore_rejects_corrupt_used_memory_before_branch_publish | covered |
| E11 | close 关闭资源保留终态与未完成活动 | V/test_llm_callback_lifecycle.py::test_worker_task_cleanup_covers_failure_and_cancellation；V/test_direct_runner_failures.py（close 失败保留根因） | covered |
| E12 | custom/envs 定义被扫描；运行测试传真实场景 | V/test_direct_router.py；L/test_scenario.py::test_three_scenarios_share_the_v2_schema | covered |
| E13 | observe 只读 RPC（不列入模型工具） | I/test_grounded_targets.py::test_readonly_needs_no_environment_catalog_and_opens_no_life_action；I/test_life_agent_protocol.py::test_readonly_schema_uses_its_independent_finish_contract | covered |
| E14 | start_basic_activity（睡眠/等待） | I/test_activity_schema.py::test_split_activity_schema_matches_runtime_model；L/test_energy_feasibility.py::test_zero_energy_basic_recovery_activity_is_accepted | covered |
| E15 | start_targeted_activity（观看/游戏/工作/浏览/阅读，目标来自本轮目录） | I/test_grounded_targets.py::test_prepare_binds_current_catalogs_to_native_strict_schema（work/watch/play_game/read_mail/browse 五类绑定） | covered |
| E16 | start_travel（计划时长/目的地/方式） | L/test_travel_time.py::test_engine_start_travel_enforces_the_factored_duration；I/test_grounded_targets.py::test_route_schema_keeps_feasible_driving_and_removes_infeasible_walking | covered |
| E17 | set_activity_state（暂停/恢复/退出） | L/test_activities.py::test_activity_pause_resume_cancel_preserves_actual_progress | covered |
| E18 | change_playback_speed | I/test_activity_schema.py::test_duration_bounds_are_preserved_in_schema_and_runtime_validation（change_playback_speed 绑 watch）；L/test_energy_feasibility.py::test_watch_energy_limit_uses_wall_clock_for_fast_playback | covered |
| E19 | continue_activity 只安排反馈不推进世界时间 | L/test_activities.py::test_continue_activity_moves_feedback_strictly_forward_without_releasing_slot；L/test_engine_feedback.py::test_continue_feedback_keeps_slot_and_terminal_result_links_origin | covered |
| E20 | purchase 一单多件；结算校验余额/商品/地点/营业；扣款安排交付 | I/test_grounded_targets.py::test_purchase_binds_multi_item_enums_and_validates_order_bounds；L/test_commerce.py::test_purchase_rejects_each_resource_boundary_without_mutating_input | covered |
| E21 | consume 至多三件持有食品；人物校验身份、方式与时长，环境按实际用量校验库存；安排使用时间与效果 | I/test_grounded_targets.py::test_consume_multi_item_meal_duration_and_quantity_authority；I/test_category_inventory_protocol.py::test_insufficient_recipe_inventory_is_rejected_by_real_settlement | covered |
| E22 | submit_work 登记交付物引用；结算触发验收 | L/test_work_contracts.py::test_work_start_rejects_skill_location_and_deadline_boundaries；L/test_work_review.py | covered |
| E23 | game_action 结算时引擎执行、反馈时刻交付局面 | I/test_grounded_targets.py::test_game_action_requires_current_session_and_admissible_command_pair；L/test_content_game.py | covered |
| E24 | plan_day 提交零至八条有序目标，空清单仍已提交，不代执行 | L/test_day_plan.py；L/test_revision_contract.py；I/test_observation_agent_seam.py | covered |
| E25 | 修订仅替换未推进目标，保留已推进证据 | L/test_day_plan.py；L/test_revision_contract.py；I/test_grounded_targets.py | covered |
| E26 | send_message 瞬时登记、下次观察送达 | L/test_social.py::test_send_message_delivers_to_world_agent_and_contact 等 4 测 | covered |
| E27 | start_social_activity 交谈/通话 | L/test_social.py::test_start_chat_registers_foreground_activity_with_invite、test_call_duration_boundaries_10_and_60_minutes | covered |
| E28 | scheduling_state() 返回可决策人物与下一事件时间 | V/test_decision_round.py::test_scheduling_state_requires_future_boundary、test_scheduling_state_rejects_duplicate_agents | covered |
| E29 | commit_decision_round 按预定顺序结算 | L/test_engine_round.py::test_multi_person_arrival_order_does_not_change_settlement_order；V/test_society_decision_round.py::test_society_uses_environment_order_then_commits | covered |
| E30 | checkpoint(request) 与 CLI export | F/test_store.py::test_complete_checkpoint_branch_preserves_parent_and_exact_workspace；F/test_export.py | covered |
| E31 | 统一 submit_action(call)；不建同名 RPC | I/test_grounded_targets.py::test_valid_grounded_target_dispatches_same_typed_request | covered |
| E32 | Pydantic 全字段必填；Schema 从请求模型生成；数组边界本地完整执行不截取 | I/test_activity_schema.py::test_all_life_tools_use_deepseek_strict_schemas_with_grounded_target_enums、test_provider_array_or_text_limit_gap_remains_a_local_hard_rejection | covered |
| E33 | 删除旧 start_activity/change_activity，无别名 | I/test_activity_schema.py::test_old_conditional_tool_names_are_absent | covered |
| E34 | 食物/耐用品使用走 consume | I/test_grounded_targets.py::test_consume_multi_item_meal_duration_and_held_quantity；L/test_commerce.py | covered |
| E35 | 余额/天气/瞬移/验收/全读不暴露；无通用代码执行 | I/test_life_agent_protocol.py::test_life_agent_schema_is_closed_and_host_fields_are_hidden | covered |
| E36 | readonly 观察仍留证据 | I/test_life_agent_protocol.py::test_readonly_state_mismatch_is_an_internal_failure；F/test_grounded_evidence 观察证据链 | covered |
| E37 | 浏览/阅读/观看消耗注意力与时间 | L/test_activity_menu.py::test_browse_is_pure_query_and_feeds_next_observation、test_browse_while_active_activity_preserves_playback；L/test_affordance_contract.py | covered |
| E38 | 结算检查表六行（启动/修改/购买/使用/工作/游戏） | L/test_activities.py::test_conflict_short_activity_and_exhaustion_are_rejected；L/test_commerce.py；L/test_work_contracts.py；L/test_content_game.py | covered |
| E39 | 请求不能指定任意路径；引用只允许已登记可见素材 | I/test_grounded_targets.py::test_prepare_rejects_malformed_business_identity_sources、test_prepare_rejects_missing_or_malformed_energy_projection | covered |
| E40 | 校验失败明确业务错误；不自动改数量/换商品/改目的地 | L/test_commerce.py::test_purchase_rejects_each_resource_boundary_without_mutating_input | covered |

## 动作结果

覆盖对象：`docs/CONTRACTS.md` 的「动作状态与动作结果」节。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| R1 | 八类动作状态区分 | L/test_engine_round.py（submitted/in_progress）；L/test_content_game.py::test_unknown_game_outcome_is_explicit_and_preserves_session（unknown）；L/test_activities.py（completed/截断）；L/test_engine_feedback.py（cancelled/failed） | covered |
| R2 | 决策阶段登记返回已提交；资产检查收齐后执行；登记≠购买成功 | L/test_engine_round.py::test_business_rejection_is_evidence_and_not_activity；I/test_grounded_targets.py::test_business_tools_bind_visible_pairs_and_refresh_when_identities_disappear | covered |
| R3 | 活动开始不立即含结束剧情/成果 | L/test_activities.py::test_completion_and_experiment_truncation_set_ended_at；L/test_engine_feedback.py::test_pause_resume_and_cancel_preserve_progress_and_origin_result | covered |
| R4 | 已接受带标识/起始/下次反馈；完成事件带区间与结算 | L/test_engine_feedback.py::test_continue_feedback_keeps_slot_and_terminal_result_links_origin | covered |
| R5 | 业务拒绝可继续；程序错误终止运行不伪装拒绝 | L/test_engine_round.py::test_business_rejection_is_evidence_and_not_activity；V/test_agent_react_validation.py::test_transport_failure_is_fatal_without_protocol_feedback | covered |
| R6 | finish 单独返回；提交动作后才开放并撤下生活动作；结束前可查记忆 | V/test_agent_react_validation.py::test_finish_mixed_with_action_is_rejected_atomically；I/test_life_agent_protocol.py::test_life_actions_are_removed_after_one_submission、test_readonly_budget_reserves_finish_and_resets_between_loops | covered |
| R7 | 未开放的当前工具一律拒绝 | V/test_agent_react_validation.py::test_unknown_tool_gets_bounded_feedback_and_self_correction；I/test_grounded_targets.py::test_unavailable_continue_is_rejected_before_valid_start_without_side_effect | covered |
| R8 | 冲突响应整批不执行；剩余回合内反馈；非法响应耗回合；不沿用只保留 finish | V/test_agent_react_validation.py::test_mixed_batch_is_rejected_without_partial_execution、test_protocol_errors_consume_exact_global_turn_budget | covered |
| R9 | 已提交必须引用实际登记的请求 | I/test_grounded_evidence.py::test_model_request_persists_exact_grounded_tools | covered |

## 消费、生产与需求模型

覆盖对象：`docs/dev/SPEC-life-runtime.md` 的「消费、生产与需求模型」章（资源表达、转换效率算法、具体活动结算各节）。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| C1 | UTC 整数秒+场景时区+同秒严格序号 | F/test_store.py::test_invalid_time_and_causal_references_fail_without_state_change；F/test_event_fields.py | covered |
| C2 | 金钱最小货币单位整数、单币种 | F/test_ledger.py（5 测）；R/test_resource_ledger.py | covered |
| C3 | 可用能量 kcal | L/test_body.py::test_piecewise_curves_clamp_and_interpolate | covered |
| C4 | 睡眠不足与食品能量独立 | L/test_body.py::test_awake_and_sleep_settle_independent_resources；L/test_body_flows.py::test_awake_activity_accrues_deficit_via_two_process | covered |
| C5 | 娱乐需要归一化；喜好/重复/时长参与 | L/test_body.py::test_entertainment_uses_person_preference_factor；L/test_activity_effects.py::test_repeat_watch_uses_content_rate_and_exposure_count | covered |
| C6 | 工作需要从义务与经济处境产生，无固定脚本 | L/test_motivation.py::test_goal_deficit_lifts_work_candidates、test_no_tasks_no_work_candidates | covered |
| C7 | 允许拖延/过度娱乐/拒绝；规则只限可行性 | L/test_engine_round.py::test_business_rejection_is_evidence_and_not_activity；I/test_grounded_targets.py::test_zero_energy_keeps_recovery_and_purchase_then_refreshes_targets | covered |
| C8 | 能量变化公式（进食×利用率−基础−活动） | L/test_body.py::test_food_uses_sleep_debt_curve_and_energy_cap、test_non_recovery_activity_rejects_negative_energy | covered |
| C9 | 睡眠不足变化公式 | L/test_sleep_two_process.py::TestSleepDeficitAccount::test_default_accumulation_rate、TestSleepDeficitAccount::test_full_day_awake_accumulation | covered |
| C10 | 有效工作量公式（技能速率×效率×工具×时间） | L/test_activity_effects.py::test_work_progress_uses_skill_and_actual_notes_effect；L/test_work_efficiency_factors.py | covered |
| C11 | 计件/计时收入公式 | L/test_work_contracts.py::test_hourly_and_piece_contracts_use_actual_time_and_frozen_piece_count | covered |
| C12 | 不双重惩罚睡眠（不同时降时薪与效率，除合同明示） | L/test_work_contracts.py::test_hourly_and_piece_contracts_use_actual_time_and_frozen_piece_count（docstring 明示"技能产出不重复降低工资"） | covered |
| C13 | 高收入经职业/技能/机会/合同；不从初始财富反推 | L/test_scenario.py::test_task_assignments_match_persona_occupations；L/test_work_contracts.py | covered |
| C14 | 睡眠期间仍有基础消耗；饥饿与睡眠不足不互替 | L/test_body_flows.py::test_sleep_recovers_deficit_and_decays_pressure、test_sleep_emits_no_entertainment_flow | covered |
| C15 | 参数集中场景；单位/来源/范围；曲线节点有序有界 | L/test_scenario.py::test_body_instance_and_rejection_ranges_are_enforced；L/test_sleep_two_process.py::test_non_finite_values_rejected_at_construction、test_decreasing_inputs_rejected | covered |
| C16 | 衍生需求从资源计算，不许自由写入 | R/test_resource_ledger.py::test_unknown_resource_is_rejected_for_entries_and_balances、test_flow_on_closed_domain_is_rejected | covered |
| C17 | 进食：已取得食品、数量进度相符、支付/取得/进食分别记录 | L/test_commerce.py::test_food_progress_persists_partial_quantity_across_pause_and_completion；F/test_ledger.py | covered |
| C18 | 工作：材料/时间/产出/验收可追溯；失败延期返工保留 | L/test_work_contracts.py::test_late_duplicate_and_unfunded_work_stop_before_semantic_payment；L/test_work_review.py | covered |
| C19 | 睡眠按实际部分结算；中断不满值 | L/test_body_flows.py::test_sleep_recovery_beyond_deficit_clamps_at_zero；L/test_activities.py::test_activity_pause_resume_cancel_preserves_actual_progress | covered |
| C20 | 看节目按播放区间；暂停不推进媒体；倍速改变进度与注意力 | L/test_content_game.py::test_subtitles_reveal_only_played_intervals_across_pause_and_repeat；L/test_energy_feasibility.py::test_watch_energy_limit_uses_wall_clock_for_fast_playback | covered |
| C21 | 游戏引擎执行；结果来自实际局面非简介 | L/test_content_game.py::test_textworld_checkpoint_replays_commands_and_rejects_invalid_input、test_unknown_game_outcome_is_explicit_and_preserves_session | covered |
| C22 | 购物：库存或配送队列；耐用品持有与使用分开；消耗品扣减 | L/test_commerce.py::test_delivery_replay_does_not_duplicate_inventory、test_pending_delivery_is_private_to_its_owner | covered |
| C23 | 出行按实际道路算时间/费用/消耗；改变位置 | L/test_travel_time.py、L/test_travel_cost.py | covered |
| C24 | 等待可选；世界照常；身体继续变化 | L/test_wait_passive_entertainment.py（8 测） | covered |
| C25 | 工资消费记录双方；金额对应；注入留痕 | F/test_ledger.py::test_new_funds_require_explicit_external_counterparty；R/test_resource_audit.py | covered |
| C26 | 重复提交不无限工资 | L/test_work_contracts.py::test_cumulative_work_limit_counts_cancelled_progress_across_restarts、test_late_duplicate_and_unfunded_work_stop_before_semantic_payment | covered |
| C27 | 质量由交付物评估；模型耗时≠工作耗时；时间不替代生产结果 | L/test_work_efficiency_factors.py；L/test_work_review.py::test_incomplete_work_skips_model_review_and_is_business_rejection | covered |

## 时间推进与并发活动

覆盖对象：`docs/dev/SPEC-life-runtime.md` 的「时间推进与并发活动」章（共享时钟、决策节律、决策机会规则、并发与占用、加速、运行观测各节）。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| T1 | 先处理到期通知/活动再选决策人物 | L/test_scheduling.py::test_default_scenario_compilation_matches_legacy_algorithm；L/test_engine_feedback.py | covered |
| T2 | 下一边界含完成/反馈/通知/截止/重估/等待/实验结束 | L/test_wake_reasons.py::test_next_decision_takes_minimum_of_all_candidates、test_next_decision_no_candidates_returns_none | covered |
| T3 | 推进严格>0；同秒稳定序号避免零时间循环 | L/test_engine_feedback.py::test_rejection_waits_one_second_and_next_observation_reports_once；V/framework/test_event_space.py::test_event_space_absorbs_same_tick_same_type_restart | covered |
| T4 | 最小反应间隔一秒；max_consecutive_rejections=10 失败停止、成功重置、不代选 | L/test_engine_feedback.py::test_tenth_consecutive_rejection_fails_agent_and_run、test_new_game_waits_one_second_before_first_real_command | covered |
| T5 | t=上次+tick 共享约定，三方共同校验 | L/test_engine_round.py::test_context_with_wrong_time_is_rejected；V/test_decision_round.py::test_decision_context_requires_aware_time | covered |
| T6 | 秒精度；UTC epoch；ISO 8601 转换不变时刻排序 | F/test_store.py::test_invalid_time_and_causal_references_fail_without_state_change；D/test_windows.py::test_midnight_experiment_end_does_not_create_an_extra_training_day | covered |
| T7 | 唤醒条件才提供决策；共同身体事件记录未提供决策及原因；睡眠不自动写记忆 | L/test_revision_contract.py::test_silent_boundary_settles_without_decision_events；L/test_wake_reasons.py（57 测）；F/test_event_fields.py::test_decision_rhythm_accepts_offered_and_silent_boundaries | covered |
| T8 | 一次决策一个主要行动；记忆检索不改世界 | I/test_life_agent_protocol.py::test_native_react_submits_one_host_bound_life_action | covered |
| T9 | 一次获准决策只提交一项生活动作；资源相容的持续活动允许并行 | L/test_activities.py::test_continue_activity_moves_feedback_strictly_forward_without_releasing_slot；I/test_dayrun_parallel_agent.py；I/test_dayrun_complete_days.py | covered |
| T10 | 拒绝后有限决策预算；耗尽记失败不代选 | L/test_engine_feedback.py::test_tenth_consecutive_rejection_fails_agent_and_run | covered |
| T11 | 注意力/位置/设备占用；驾驶与主动游戏不并发；多人并行不互跳 | L/test_activities.py::test_conflict_short_activity_and_exhaustion_are_rejected；L/test_engine_round.py::test_multi_person_arrival_order_does_not_change_settlement_order | covered |
| T12 | 模型可并发；共享修改串行 actor；竞争顺序固定种子并记录 | L/test_engine_feedback.py::test_contexts_must_follow_seeded_scheduling_order、test_seed_changes_competition_order_and_checkpoint_restores_it；L/test_commerce.py::test_multi_person_stock_competition_uses_seeded_order_not_arrival | covered |
| T13 | 同轮观察共同世界版本；提案先入队后按序应用 | F/test_store.py::test_same_world_observations_share_version_and_preserve_duplicate_behavior；L/test_engine_round.py::test_manifest_uses_single_window_shape | covered |
| T14 | actor 不阻塞早序请求；稳定动作标识防重复记账 | L/test_engine_round.py::test_same_action_reuses_result_and_parameter_conflict_fails | covered |
| T15 | 人物决策异常不结算本轮；请求与证据保留；运行终止 | L/test_engine_round.py::test_abort_preserves_unexecuted_life_state_and_stale_context_fails；V/test_society_decision_round.py::test_society_aborts_before_commit_on_agent_failure | covered |
| T16 | 加速只减现实等待；倍速分开记录；加速比实测报告 | F/test_quality.py::test_acceleration_sums_observed_wall_time_across_restore_branches；L/test_energy_feasibility.py::test_watch_energy_limit_uses_wall_clock_for_fast_playback | covered |
| T17 | 只读状态视图；不唤醒/不调模型/不改决策 | F/test_status.py::test_status_reads_confirmed_state_without_modifying_any_run_file、test_status_preserves_earlier_body_decision_time_instead_of_activity_end | covered |
| T18 | 质量报告从足迹统计完成/中断/拒绝/重复/接触/效率/记忆/成本 | F/test_quality.py（14 测） | covered |

## 开源语料与内容目录契约

覆盖对象：`docs/dev/SPEC-life-runtime.md` 的「语料进入环境与内容提供」章（数据进入环境的过程、来源与准入、内容目录契约、内容提供行为各节）；设计理由与数据流另见 `docs/ARCHITECTURE.md` 的「离线语料如何进入环境」章。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| K1 | 原始内容/参数/参考答案分开；答案不随输入暴露 | CO/test_corpora.py::test_ami_inputs_and_hidden_oracles_are_separate | covered |
| K2 | 只读冻结数据版本，不临时重下载 | CO/test_corpora.py::test_formal_manifest_validates_every_file_and_selected_counts、test_routes_are_frozen_real_responses_for_all_declared_pairs | covered |
| K3 | 按完整剧集/线程/会议/任务抽样 | CO/test_corpora.py（threads==48 断言） | covered |
| K4 | 来源表取得与限制（食品/食价/节目/游戏/工作/邮件/购物/导航/校验） | CO/test_corpora.py、CO/test_category_corpora.py、CAT/test_catalog_data.py::test_food_expansion_whitelist_and_spectrum（在线取数见 EX-1 豁免） | covered |
| K5 | 文本优先；无画面不标记视觉观看 | CO/test_corpora.py::test_sintel_is_subtitle_experience_with_complete_timeline | covered |
| K6 | 内容目录契约字段表（item_id/source/assets/segments/attributes/access/evaluation_refs/usage_rights） | CAT/test_models.py（25 测）、CAT/test_admission.py（27 测） | covered |
| K7 | 各类别独立 Schema、公共字段一致 | CAT/test_models.py::test_resource_definition_accepts_design_example、test_object_rejects_unknown_fields | covered |
| K8 | 合成属性标记；无证据数值不混入来源字段 | CAT/test_catalog_data.py::test_every_numeric_value_has_exactly_one_provenance_part | covered |
| K9 | 素材按哈希复用；访问事件独立标识 | F/test_store.py（AssetStore 相同内容复用）；L/test_content_game.py::test_subtitles_reveal_only_played_intervals_across_pause_and_repeat | covered |

## 工作与邮件反馈

覆盖对象：`docs/dev/SPEC-life-runtime.md` 的「内容提供行为」节（工作材料与邮件反馈、工作提交、工作验收、历史邮件处理）与「具体活动结算」节的工作质量部分。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| W1 | 任务具备材料/要求/工具/截止/合同/验收 | L/test_scenario.py::test_work_and_message_references_stay_inside_scenario；L/test_work_contracts.py | covered |
| W2 | 成果存素材、验收单独记录；参考不提前提供 | CO/test_corpora.py::test_ami_inputs_and_hidden_oracles_are_separate；L/test_work_review.py | covered |
| W3 | 说明先可见、正文随进度揭示；不免费取得完整转写 | L/test_revision_contract.py::test_work_material_reveal_capped_at_2000_chars_per_decision | covered |
| W4 | 公开 task_id 绑定；不要求 meeting_id/submission_id；已付款集合防重复 | I/test_grounded_targets.py::test_business_tools_bind_visible_pairs_and_refresh_when_identities_disappear；L/test_work_contracts.py::test_late_duplicate_and_unfunded_work_stop_before_semantic_payment | covered |
| W5 | 独立 DeepSeek 语义验收；同义改写接受；字符/词面/工时不替代 | L/test_work_review.py::test_frozen_real_deepseek_responses_accept_paraphrase_and_reject_hallucination；L/test_work_review.py::test_work_material_requires_time_and_paraphrase_can_be_paid、test_negated_or_unsupported_minutes_require_rework_without_payment | covered |
| W6 | 请求/响应/用量/模型身份/校验存证据；代码决定接受与付款 | L/test_work_review.py::test_work_reviewer_accepts_exact_client_and_request_identity、test_work_reviewer_rejects_request_parameter_drift | covered |
| W7 | 传输/结构错误终止运行；内容不合格是业务结果；无自动重试替代 | L/test_work_review_protocol.py::test_infrastructure_errors_are_fatal_without_correction、test_valid_semantic_rejection_returns_after_one_call | covered |
| W8 | 公开反馈含覆盖统计与具体改进依据 | L/test_work_review_protocol.py::test_missing_contradiction_is_corrected_after_durable_feedback、test_recognized_output_errors_are_corrected_once | covered |
| W9 | 反馈不含隐藏参考/内部身份/私有路径/哈希 | CO/test_corpora.py::test_ami_inputs_and_hidden_oracles_are_separate；L/test_work_review.py（oracle 分离） | covered |
| W10 | 改写历史邮件则后续不当真实反馈；新反馈带来源标记 | CO/test_corpora.py（48 冻结线程）；L/test_export_integration.py::test_model_npc_reply_loop_delivers_to_inbox_with_evidence（source_kind=model_npc） | covered |

## 导航与配送

覆盖对象：`docs/dev/SPEC-life-runtime.md` 的「导航与配送」节。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| N1 | 经纬度只给球面距离；驾驶耗时用道路路由 | CO/test_corpora.py::test_routes_are_frozen_real_responses_for_all_declared_pairs；L/test_travel_time.py | covered |
| N2 | 可沿路线+模拟时间生成定位轨迹；保存路线与采样规则；采样非决策 | L/test_travel_trajectory.py（7 测：确定性逐字节重放、采样点落冻结路线几何<1m、时间戳严格递增于窗口内、environment_event kind=location_track 且 decision_id/action_id 为 None、sampling_rule+路线 id/路径/哈希引用、非法窗口与哈希漂移 fail-closed、稀疏间隔边界） | covered |
| N3 | 总耗时含路由引擎转弯成本（OSRM 总时长） | CO/test_corpora.py::test_routes_are_frozen_real_responses_for_all_declared_pairs（保存 total duration/distance/geometry） | covered |
| N4 | 配送预计时间为同单最大准备时长加实际发车时刻的分时道路时长；未知路线拒绝 | L/test_storefront_commerce.py::test_whole_basket_uses_maximum_preparation_and_departure_traffic_once、test_fulfillment_rejections_are_atomic；统一入口为 src/worldmock/life/basket.py | 定向通过，最终整合验收独立 |
| N5 | 人物只看当时报价/预计，不见隐藏到达安排 | L/test_travel_time.py::test_observation_quotes_the_factored_duration；L/test_commerce.py::test_pending_delivery_is_private_to_its_owner | covered |
| N6 | 交通数据对应模拟日期；静态场景；不冒充实测路况 | L/test_travel_time.py::test_factor_lookup_matches_the_snapshot_table_for_every_hour、test_calibrate_rerun_is_byte_identical（在线取数见 EX-1） | covered |

## 数字足迹保存协议

覆盖对象：`docs/design/05-recording-engine.md` 的「保存边界」章、「状态关联字段」章与「来源与可见性」章；事件字段、事件类型枚举与持久化结构的字段定义在 `docs/design/05-recording-engine.md` 的「数字足迹事件结构」章。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| F1 | 记录在接口边界；正文/请求/响应/参数/异常/记忆版本可追溯 | F/test_store.py、I/test_grounded_evidence.py | covered |
| F2 | 凭据不写入证据；脱敏保留任务上下文 | F/test_store.py::test_credentials_are_redacted_without_losing_model_task | covered |
| F3 | 记录身份字段组（schema_version/run_id/event_id/严格递增 seq/type） | F/test_store.py::test_state_and_events_commit_together_and_reject_same_version_mutation | covered |
| F4 | 参与与关联（decision_id/action_id/activity_id/caused_by） | F/test_store.py::test_cause_in_same_transaction_and_immutable_database、test_invalid_time_and_causal_references_fail_without_state_change | covered |
| F5 | 时间字段（occurred_at/observed_at/recorded_at/wall_elapsed_ms） | F/test_quality.py::test_cost_band_uses_request_time_when_response_crosses_boundary（wall_ms）；F/test_store.py | covered |
| F6 | 观察组（可见字段/引用区间/集合版本；无未来未授权内容） | L/test_grounded_observation.py；D/test_windows.py::test_action_history_never_sees_future_answer_and_intent_cutoff_is_explicit | covered |
| F7 | 请求与执行组（动作名/参数/状态/起止/进度/中断原因） | L/test_engine_round.py::test_real_wait_round_advances_and_records_complete_activity | covered |
| F8 | 结果组（反馈/资源变化/交付物/对方账户/验收/失败原因） | L/test_money_settlement.py、F/test_ledger.py | covered |
| F9 | state_version_before/after；可从检查点+事件重建 | F/test_store.py::test_world_change_without_any_event_cannot_be_committed；R/test_resource_ledger.py::test_validate_history_replays_full_history_to_same_final_state | covered |
| F10 | 来源与可见性组（人物可见 vs 仅评估端） | D/test_windows.py（PRIVATE_EVENTS）；E/test_adapters.py | covered |
| F11 | 事件类型冻结枚举（14 类）全集 | F/test_event_fields.py::test_event_type_vocabulary_is_the_frozen_spec_set（tuple(EVENT_TYPES) 逐字==_SPEC_EVENT_TYPES 14 类且无重复）、test_event_type_rejects_anything_outside_the_frozen_set（"agent_thought" 拒绝） | covered |
| F12 | 同动作重试复用原标识；再次观看或再购买另发新标识 | L/test_engine_round.py::test_same_action_reuses_result_and_parameter_conflict_fails；I/test_grounded_targets.py::test_completed_content_remains_available_for_explicit_repeat | covered |
| F13 | 世界事实/自述/推断来源区别；不删重复行为；哈希非事件标识 | E/test_adapters.py::test_legacy_action_declarations_never_claim_completion_or_object_identity；F/test_store.py::test_same_world_observations_share_version_and_preserve_duplicate_behavior | covered |

## 持久化结构

覆盖对象：`docs/design/05-recording-engine.md` 的「持久化结构」章与「存储接口行为」章；运行目录与数据表的接口定义在 `docs/design/05-recording-engine.md` 的「持久化结构」节。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| P1 | run/ 目录布局（manifest/sqlite/assets/as2/exports/labelkit/日志） | D/test_publication.py::test_manifest_inventory_binds_every_gate_and_consumer_file（文件清单）；I/test_runtime.py::test_run_shell_allows_only_status_file | covered |
| P2 | events 表按人物/活动/动作/时间可查询 | F/test_store.py::test_agent_event_queries_respect_type_person_and_exclusive_boundary | covered |
| P3 | world_state 带状态版本 | F/test_store.py::test_state_and_events_commit_together_and_reject_same_version_mutation | covered |
| P4 | artifacts 表（哈希/位置/大小/模态/来源/使用条件） | F/test_store.py::test_missing_or_damaged_asset_prevents_commit_and_checkpoint | covered |
| P5 | checkpoints 表（快照引用/时钟/随机态/最后序号） | F/test_store.py::test_checkpoint_event_must_match_committed_identity_and_clock；L/test_engine_feedback.py::test_seed_changes_competition_order_and_checkpoint_restores_it（种子恢复） | covered |
| P6 | 环境 actor 唯一写入；证据先成素材再宿主提交引用 | F/test_store.py（单写入事务）；I/test_grounded_evidence.py | covered |

## 事务与失败处理

覆盖对象：[记录引擎](../design/05-recording-engine.md#事务与失败处理)的事务、存储失败和检查点材料约束；
业务拒绝与运行故障的区分、协议预算和游戏恢复见[模拟引擎](../design/04-simulation-engine.md#失败处理)及其恢复编排，
跨层传播见 [AS2 失败传播](SPEC-as2-integration.md#失败传播改动)，判定口径见 `docs/VERIFICATION.md` 的「工程门禁」。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| X1 | 状态/资金/动作/事件同一 SQLite 事务 | F/test_store.py::test_state_and_events_commit_together_and_reject_same_version_mutation、test_sql_failure_rolls_back_money_and_all_events | covered |
| X2 | 素材先临时文件、哈希核对再发布；库只提交完整素材 | F/test_store.py::test_missing_or_damaged_asset_prevents_commit_and_checkpoint | covered |
| X3 | 外部调用有明确开始与结果事件 | I/test_grounded_evidence.py::test_model_request_persists_exact_grounded_tools | covered |
| X4 | 外部结果不明记 unknown，不称只发生一次 | L/test_content_game.py::test_unknown_game_outcome_is_explicit_and_preserves_session | covered |
| X5 | 故障分类：写失败/素材损坏/无法留证/必要观察缺失/预算耗尽→运行故障停止 | V/test_agent_react_validation.py::test_loop_exhaustion_is_fatal；F/test_store.py::test_sql_failure_rolls_back_money_and_all_events | covered |
| X6 | 致命故障与业务拒绝不同错误类型，跨层保留区别 | L/test_engine_round.py::test_business_rejection_is_evidence_and_not_activity；V/test_direct_runner_failures.py | covered |
| X7 | 故障路径英文错误+关联事件；不被通用捕获转成继续猜测 | L/test_opslog.py::test_opslog_records_rejection_with_error_code；V/test_agent_react_validation.py::test_tool_execution_failure_is_fatal_without_new_model_call | covered |
| X8 | 游戏引擎后、提交前崩溃恢复用检查点游戏状态 | L/test_content_game.py::test_textworld_checkpoint_replays_commands_and_rejects_invalid_input | covered |
| X9 | 协议不符只在剩余回合反馈；预算用尽失败；不补写完成/强转记忆报成功 | V/test_agent_react_validation.py::test_protocol_errors_consume_exact_global_turn_budget、test_invalid_finish_memory_is_fatal | covered |

## 共同检查点与恢复

覆盖对象：[记录引擎](../design/05-recording-engine.md#保存共同检查点)的检查点保存、分支存储与材料校验；
运行状态、人物与游戏恢复由[模拟引擎](../design/04-simulation-engine.md#9-恢复与反事实运行编排)负责，
同源数据划分见[交付规格](SPEC-labelkit-delivery.md#长序列截断与划分)。外部调用边界见 `docs/VERIFICATION.md` 的「重放与外部调用边界」。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| CP1 | 全部完成后创建；先素材后事务 | F/test_store.py::test_complete_checkpoint_branch_preserves_parent_and_exact_workspace、test_failed_checkpoint_transaction_does_not_publish_record | covered |
| CP2 | 集合 JSON 按元素排序；跨哈希种子逐字节相同 | L/test_state_serialization.py::test_json_mode_sorts_every_persisted_string_set、test_readonly_commit_is_identical_across_python_hash_seeds | covered |
| CP3 | SOCIETY_STEP.json 单独成功不构成检查点证明 | F/test_export.py::test_clock_end_without_successful_final_checkpoint_is_not_complete | covered |
| CP4 | 中断标 interrupted、尾部保留；分支指向父运行/检查点/原因；恢复全部状态 | F/test_store.py::test_complete_checkpoint_branch_preserves_parent_and_exact_workspace；I/test_runtime.py::test_restore_publishes_database_agents_and_status_together | covered |
| CP5 | 恢复/dreaming/原始轨迹同一来源组 | D/test_training.py::test_shared_source_connects_different_declared_groups_into_one_split | covered |
| CP6 | 恢复先验证全部引用再建分支；失败时 Router/Society 终止 | I/test_runtime.py::test_restore_rejects_corrupt_used_memory_before_branch_publish；V/test_society_decision_round.py::test_society_init_checkpoint_failure_records_run_failure | covered |
| CP7 | 恢复失败不自动转新建实验 | V/test_society_decision_round.py::test_society_init_checkpoint_failure_records_run_failure；L/test_counterfactual.py::test_counterfactual_rejects_manifest_hash_mismatch_without_mutation | covered |
| CP8 | 子运行不复制父检查点后工作区；父目录原样 | F/test_store.py::test_complete_checkpoint_branch_preserves_parent_and_exact_workspace（逐字节前后对比） | covered |
| CP9 | 重放复原已发生；不承诺外部调用恰好一次 | D/test_replay.py；D/test_generation.py | covered |

## LifeAgent 人格、记忆与 dreaming 边界

覆盖对象：`docs/dev/SPEC-life-realism.md` 的「记忆合同」章（含「dreaming 边界」节）；可见字段定义在 `docs/CONTRACTS.md` 的「LifeAgent 人格与记忆字段」章。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| LA1 | PersonaCard 构成；≤600 汉字中文入请求 | P/test_models.py::test_card_field_completeness；P/test_textgen.py::test_render_within_hanzi_budget | covered |
| LA2 | 旧 occupation/routine/consumption/risk_preference 模板与随机浮点删除 | L/test_persona_upgrade.py::test_new_persona_fields_are_optional_and_template_fields_are_gone；I/test_persona_profile.py::test_deleted_template_fields_stay_out_of_documents | covered |
| LA3 | 倾向写入 background_story；experiment_assumption；不规定行动时刻/数量 | P/test_extractor.py::test_rejects_empty_story_and_bad_runs；L/test_scenario.py::test_persona_declarations_are_complete_and_story_consistent | covered |
| LA4 | 无固定"先吃饭再工作"行为脚本 | I/test_grounded_targets.py::test_business_tools_bind_visible_pairs_and_refresh_when_identities_disappear（工具面只约束集合不代选） | covered |
| LA5 | 正式运行固定端点/模型版本/参数 | I/test_runtime.py::test_model_identity_freezes_complete_strict_endpoint_and_request | covered |
| LA6 | 大五只进世界层动力学，不进提示 | P/test_mapper.py::test_dynamics_fields_cover_the_five_trait_routes；L/test_persona_upgrade.py::test_card_render_never_leaks_big_five_or_random_floats | covered |
| LA7 | 记忆带来源事件与形成时刻；保存每次决策检索到的记忆 | V/framework/test_memory.py::test_memory_store_append_recent_search_and_read；I/test_life_agent_protocol.py::test_multiple_memory_calls_share_one_model_turn | covered |
| LA8 | 记忆未提≠未发生；引用错误记为模型产物问题 | F/test_quality.py::test_memory_quality_reports_unknown_reference_and_missing_update | covered |
| LA9 | dreaming 离线生成，不自动写回运行中人物 | D/test_windows.py（离线窗口构造）；D/test_replay.py（独立分支） | covered |
| LA10 | dreaming 每条有来源/截止/模型/规则版本；想象标假设、执行标重新仿真 | D/test_replay.py::test_completed_actual_branch_binds_hypothesis_checkpoint_and_results、test_original_world_cannot_be_mislabeled_as_an_actual_replay | covered |

## 数字足迹接入 LabelKit

覆盖对象：`docs/dev/SPEC-labelkit-delivery.md` 的「产品行为」「确定输入范围与加工责任」「证据窗口与输入结构」「来源关联」「信息范围与标签」「交付产物与发布边界」「长序列、截断与划分」各章。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| LK1 | 固定截止导出不可变 JSONL+哈希+来源索引 | F/test_export.py::test_export_uses_cutoff_and_excludes_pause_from_duration；F/test_validation.py::test_incomplete_or_tampered_export_never_reaches_labelkit | covered |
| LK2 | 只读导出不修改原始轨迹 | F/test_status.py::test_status_reads_confirmed_state_without_modifying_any_run_file；F/test_validation.py | covered |
| LK3 | 素材引用展开为实际证据，不只放路径 | D/test_windows.py::test_visible_prior_result_is_hydrated_as_direct_evidence_without_moving_cutoff | covered |
| LK4 | 四输入方式为显式任务选择（最终合同绑定窗口 process 与 sequence generation） | D/test_windows.py（活动/自然日窗口）；D/test_generation.py（场景初态生成分支）；方式 1/2（逐事件 stream）为 LabelKit 能力描述、最终数据合同未采用 | covered |
| LK5 | AS2 活动关联是来源事实；LabelKit 不覆盖原始活动/动作/时间 | D/test_host_delivery_coverage.py::test_semantic_validation_requires_original_record_and_hypothesis（worldmock.delivery.semantics 的 attach_facts／validate_annotation 纯函数）；D/test_training.py 四字段组装 | covered |
| LK6 | 跨活动意图引用多段经历、保留原时间位置 | D/test_windows.py::test_observed_result_must_resolve_to_exact_visible_same_agent_prior_event | covered |
| LK7 | 不用 _meta.event 冒充内部 stream | D/test_labelkit_process.py（_meta 处理与 extra_field 拒绝） | covered |
| LK8 | 加工责任表（确定性导出/模型标注/独立检查器/目标加载器） | D/test_host_delivery_coverage.py、D/test_training.py、D/test_rules.py::test_common_causal_rules_respect_missing_support_and_detect_contradictions | covered |
| LK9 | 模型只生成 target 候选；评审通过后 WorldMock 一次性组装 | D/test_labelkit_process.py::test_real_labelkit_annotate_and_verify_requests_are_target_blind；D/test_training.py::test_target_candidate_is_validated_before_four_field_assembly | covered |
| LK10 | 不用 extract；权威边界优先完整活动记录 | D/test_windows.py（boundary_type 权威边界）；D/test_generation.py | covered |
| LK11 | 来源关联（sample_id→event_id、行号、成员来源回溯） | D/test_labelkit_process.py::test_process_rejects_duplicate_source_line、test_process_rejects_trace_changed_after_source_binding | covered |
| LK12 | sequence generation 新事件属生成分支；映射记录 | D/test_generation.py::test_committed_sequence_digest_is_verified_from_actual_bytes、test_invalid_sequence_is_rejected_before_consumer | covered |
| LK13 | 动作预测输入截止动作前；意图截止识别时刻；结果只在目标 | D/test_windows.py::test_action_history_never_sees_future_answer_and_intent_cutoff_is_explicit | covered |
| LK14 | 多标签/无法确定合法；dreaming 声明知识截止不污染更早样本 | D/test_semantic_schema.py::test_target_only_schema_accepts_the_declared_boundary（intent_candidates/uncertainty）；D/test_windows.py::test_action_public_identity_is_independent_of_the_future_target | covered |
| LK15 | 最终交付格式表（动作/意图/dreaming/夹具/清单最小内容） | D/test_training.py、D/test_rules.py、D/test_publication.py | covered |
| LK16 | 统一四字段；真实加载器与夹具读取器定义"直接可用" | D/test_training.py::test_real_dataset_loading_preserves_all_facts_and_separates_answer；D/test_rules.py::test_real_rule_engine_positive_missing_reordered_timeout_and_duplicate | covered |
| LK17 | 规则夹具确定性组装（冻结目录/事件/预期；模型不生成预期） | D/test_rules.py；D/test_generation.py | covered |
| LK18 | sequence 工件 manifest/哈希/行数/身份验证后才消费；失败不交付 | D/test_generation.py::test_committed_sequence_digest_is_verified_from_actual_bytes；D/test_publication.py::test_failed_semantic_gate_never_publishes_delivery | covered |
| LK19 | 长序列窗口边界（容量切断/实验结束/活动完成分别保存）；超限明确失败不自动缩短 | L/test_narration.py::test_scene_reports_oversized_input_without_truncation、test_user_over_by_one_raises（提示侧）；D/test_windows.py boundary_type（LabelKit CLI 侧行为见 EX-4） | covered |
| LK20 | 采集端不删除失败/取消/重复；拒收保留原因与来源 | D/test_publication.py::test_invalid_source_never_creates_delivery_or_runs_model；F/test_export.py::test_truncated_activity_exports_experiment_end_reason | covered |
| LK21 | 关联组划分训练/验证/测试，防逐行随机泄漏 | D/test_training.py::test_shared_source_connects_different_declared_groups_into_one_split、test_run_identity_keeps_disjoint_event_windows_in_one_split | covered |

## 开发合同：实验依赖、运行接口、场景规则、内容绑定

覆盖对象：`docs/EXPERIMENT.md` 的「实验身份与范围」「时间窗与日历情境」「依赖冻结」「运行绑定与消费」「场景参数」「内容与语料绑定」「样本编码与来源分组」「实验假设声明」各章；运行接口与状态枚举在 `docs/CONTRACTS.md` 的「CLI 命令接口」章；场景与身体规则的算法在 `docs/dev/SPEC-life-runtime.md` 的「场景与身体规则」章；内容与消费生产绑定的行为在同份规格的「消费、生产与需求模型」与「语料进入环境与内容提供」两章；文件职责表在 `docs/INDEX.md` 的「文件归属」章。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| DEV1 | 单机 Ray actor/runner；正式十二周+多人实际短运行 | 锚：L/test_engine_round.py::test_multi_person_arrival_order_does_not_change_settlement_order；V/test_society_decision_round.py（真实运行见 EX-2） | exempt |
| DEV2 | 正式日期窗口 2026-01-05→03-30（84 天，左闭右开） | L/test_scenario.py::test_formal_and_multi_person_scenarios_are_valid（日期冻结）；E/test_metrics.py::test_formal_window_uses_natural_week_blocks_and_ten_thousand_resamples | covered |
| DEV3 | 人物共同背景；未定义属性单独声明为实验假设 | L/test_scenario.py::test_persona_declarations_are_complete_and_story_consistent | covered |
| DEV4 | 人物生成模型：DeepSeek deepseek-flash；保存实际返回/参数/用量 | 锚：I/test_runtime.py::test_model_identity_freezes_complete_strict_endpoint_and_request；L/test_work_review.py 冻结真实响应（真实调用见 EX-3） | exempt |
| DEV5 | 模型冻结：候选集合由本轮观察产生；运行中不切换/不代选 | I/test_grounded_targets.py::test_memory_observation_does_not_change_frozen_catalog_and_next_round_refreshes；I/test_runtime.py::test_two_stage_model_identity_is_frozen_and_tamper_rejected | covered |
| DEV6 | AS2 基线分支与依赖版本锁定 | 结构性：vendor 测试套可运行（V/framework/test_smoke.py::test_import_package）+ uv.lock | covered |
| DEV7 | 旧数据身份（run ec5742…、main 4380 行/stream 16320 行/84 自然日） | E/test_adapters.py（使用真实 repaired archive；身份常量未逐字断言） | covered |
| DEV8 | 命令失败非零退出+英文错误+不造假 manifest | F/test_validation.py::test_profile_cli_check_asserts_three_compile_consistency（main 返回 1）；D/test_publication.py::test_invalid_source_never_creates_delivery_or_runs_model | covered |
| DEV9 | prepare-corpora 获取来源子集、核对、形成目录与清单 | 锚：CO/test_corpora.py::test_formal_manifest_validates_every_file_and_selected_counts（在线获取见 EX-1） | exempt |
| DEV10 | validate 校验场景/语料/依赖/规则/数据合同，不启动人物 | F/test_validation.py（10 测） | covered |
| DEV11 | run 新建目录；已存在运行目录不覆盖 | I/test_runtime.py::test_run_shell_allows_only_status_file | covered |
| DEV12 | resume 指定父运行与完整检查点、新目录、不允许隐式新建 | F/test_store.py::test_complete_checkpoint_branch_preserves_parent_and_exact_workspace；I/test_runtime.py::test_restore_publishes_database_agents_and_status_together | covered |
| DEV13 | replay 指定父运行/检查点/余额干预、新反事实分支 | L/test_counterfactual.py（6 测）；I/test_runtime.py::test_counterfactual_branch_freezes_intervention_before_publish、test_execute_applies_counterfactual_before_running | covered |
| DEV14 | status 只读显示已确认状态与统计 | F/test_status.py（2 测） | covered |
| DEV15 | export 固定截止与活动边界、完整证据 JSONL/索引/哈希 | F/test_export.py（7 测）；L/test_export_integration.py | covered |
| DEV16 | deliver 执行 LabelKit+独立校验+训练加载+规则执行+清单 | 锚：D/test_publication.py、D/test_training.py、D/test_rules.py（真实 LabelKit CLI 见 EX-4） | exempt |
| DEV17 | evaluate 两套数据身份核对、机器报告/图表/中文结论 | E/test_runner.py（6 测），其中 test_one_day_prepare_and_finalize_writes_complete_committed_delivery 核验机器交付、中文报告与图表 | covered |
| DEV18 | 运行目录必含 manifest/sqlite/assets/as2/exports/labelkit/日志 | D/test_publication.py::test_manifest_inventory_binds_every_gate_and_consumer_file；L/test_opslog.py（运行日志） | covered |
| DEV19 | 运行状态/阶段枚举（5 状态 5 阶段；睡眠属活动状态） | src/worldmock/status.py（阶段闭集校验）+F/test_status.py::test_status_reads_confirmed_state_without_modifying_any_run_file；L/test_opslog.py RUN_CRASHED | covered |
| DEV20 | 事件类型冻结枚举 14 类 | F/test_event_fields.py::test_event_type_vocabulary_is_the_frozen_spec_set（与 F11 同一测试，14 类逐字==`docs/design/05-recording-engine.md` 事件类型枚举） | covered |
| DEV21 | 动作状态枚举 8 类 | L/test_engine_round.py、L/test_content_game.py::test_unknown_game_outcome_is_explicit_and_preserves_session 等散布断言（无全集专测，弱注记） | covered |
| DEV22 | 已提交请求序号不是完成/支付证明 | F/test_ledger.py::test_reused_receipt_cannot_record_a_second_payment | covered |
| DEV23 | 存储边界：生活模型验证封闭请求；人物 worker 不改 footprints.sqlite | L/test_engine_round.py::test_first_round_accepts_discovered_content_game_and_route（typed request）；F/test_store.py 单写入 | covered |
| DEV24 | AssetStore.put 原子落盘核验、相同内容复用 | F/test_store.py::test_missing_or_damaged_asset_prevents_commit_and_checkpoint | covered |
| DEV25 | AssetStore.read 验证引用/大小/SHA；缺失损坏立即失败 | F/test_store.py::test_missing_or_damaged_asset_prevents_commit_and_checkpoint（corrupt 字节改写用例） | covered |
| DEV26 | commit 一次事务更新状态/资金/事件；序号存储分配、版本连续 | F/test_store.py::test_state_and_events_commit_together_and_reject_same_version_mutation | covered |
| DEV27 | read_events 固定截止、不隐式含新记录 | F/test_store.py::test_agent_event_queries_respect_type_person_and_exclusive_boundary | covered |
| DEV28 | checkpoint 先校验全部素材再提交 | F/test_store.py::test_incomplete_checkpoint_never_creates_child、test_failed_checkpoint_transaction_does_not_publish_record | covered |
| DEV29 | restore 校验版本与素材、新目录；反事实身份发布前冻结 | F/test_store.py；L/test_counterfactual.py::test_counterfactual_balance_is_double_entry_versioned_and_one_shot | covered |
| DEV30 | 权威资金变化只在 resource_changes 顶层整数分；一次支付一个事件；双方合计零；initial_funds_injected 后禁重复；导出可重建账户 | F/test_ledger.py（5 测）；R/test_equivalence_anchor.py::test_replayed_final_balances_match_database_checkpoint、test_anchor_day_has_one_injection_two_payrolls_three_fares | covered |
| DEV31 | 宿主上下文不出现在模型参数；Schema 全由 Pydantic 生成 | V/test_decision_round.py::test_decision_context_is_immutable_and_complete；I/test_activity_schema.py::test_nested_life_schemas_are_inlined_from_runtime_models | covered |
| DEV32 | 身体活动参数带单位/来源/范围、场景校验 | L/test_scenario.py::test_body_instance_and_rejection_ranges_are_enforced | covered |
| DEV33 | 睡眠不足与能量独立；进食不清睡眠不足；曲线节点递增有界；结算记录参数与时长 | L/test_body.py::test_awake_and_sleep_settle_independent_resources；L/test_body_flows.py::test_every_flow_carries_cause_owner_and_stamp；L/test_sleep_two_process.py::test_decreasing_inputs_rejected | covered |
| DEV34 | 计时按实际合格时长；技能/睡眠影响产出；不重复降时薪 | L/test_work_contracts.py::test_hourly_and_piece_contracts_use_actual_time_and_frozen_piece_count；L/test_work_efficiency_factors.py | covered |
| DEV35 | 计件只有真实交付通过独立验收才付款；延期/拒绝/返工/无资金产生实际结果 | L/test_work_contracts.py::test_late_duplicate_and_unfunded_work_stop_before_semantic_payment、test_same_task_competition_pays_only_seeded_first_settlement | covered |
| DEV36 | 活动占用明确；不允许组合业务拒绝不自动退出/替代；配送/消息独立推进；暂停不算产出、退出保留 | L/test_activities.py::test_conflict_short_activity_and_exhaustion_are_rejected、test_activity_pause_resume_cancel_preserves_actual_progress；L/test_engine_feedback.py::test_task_expiry_advances_during_sleep_without_forcing_wake | covered |
| DEV37 | activity_feedback_seconds=900 结算粒度；到达边界活动仍连续 | L/test_engine_feedback.py（900 边界系列）；CAT/test_affordance_shadow.py::test_feedback_kinds_match_activities | covered |
| DEV38 | 唤醒条件决定决策机会；继续请求不零时间循环/不重复计入 | L/test_revision_contract.py::test_silent_boundary_settles_without_decision_events；L/test_activities.py::test_continue_activity_moves_feedback_strictly_forward_without_releasing_slot | covered |
| DEV39 | 游戏回合反馈边界；睡眠只在计划结束或唤醒事件提供决策 | L/test_engine_feedback.py::test_game_feedback_boundary_still_rejects_direct_switch；L/test_wake_reasons.py::test_sleep_allows_planned_end_and_wake_events_only | covered |
| DEV40 | 文件职责表（各路径职责） | 结构性：各文件行为由本矩阵各条测试承载（见对应行） | covered |
| DEV41 | 内容绑定表（食品白名单/Sintel/TextWorld 种子/AMI/邮件 48 线程/商品/地图/交通/训练加载/规则消费） | CAT/test_catalog_data.py::test_food_expansion_whitelist_and_spectrum；CO/test_corpora.py（Sintel/threads==48）；CO/test_game_corpora.py::test_textworld_library_has_30_seeds_wired_into_scenarios；D/test_training.py；D/test_rules.py（种子数分歧已解决：1201–1230 共 30 个种子，与 corpora/manifest.json 冻结集一致） | covered |
| DEV42 | 内容不足/格式/哈希错误明确失败；manifest 全量核验；缺失不启动 | CAT/test_admission.py；F/test_validation.py::test_production_windows_require_and_accept_complete_authenticated_export | covered |
| DEV43 | 身体参数冻结值（65kg/1600/2400/65kcal/h/MET 扣基础/1e-9 近零归零/超精度不足仍失败） | L/test_body.py::test_non_recovery_activity_clamps_only_fixed_near_zero_precision、test_exact_total_energy_survives_feedback_partition_rounding、test_energy_duration_uses_same_cost_at_sixty_second_boundary | covered |
| DEV44 | 两过程参数与睡眠不足账、效率曲线节点、800/400 世界事实、娱乐 0.08、社交 0.04 | L/test_sleep_two_process.py（57 测，含 frozen_curve_constants）；L/test_felt_states.py（87 测） | covered |
| DEV45 | CNY 初始余额/主合同 60000 分/工作日 8h 机会/计时计件补充覆盖/稳定任务标识/转账对账 | L/test_scheduling.py::test_payroll_credits_agent_account；L/test_money_settlement.py::test_one_day_fixture_reconciles_six_accounts_against_ledger；L/test_work_contracts.py | covered |
| DEV46 | 独立评审最多三次调用、纠正范围、先落盘再调用、三次无效终止、传输零重试、基础设施异常立即传播 | L/test_work_review_protocol.py（8 测全覆盖） | covered |
| DEV47 | 正式日历（春节/调休/加班/病假/事假/聚餐只改机会）；禁止驾驶×主动游戏并发；后台配送/消息独立 | L/test_scheduling.py::test_calendar_flag_dispatch_is_zero_state_audit_event（spring_festival/overtime/sick_leave flags）、test_default_scenario_compilation_matches_legacy_algorithm；L/test_activities.py::test_conflict_short_activity_and_exhaustion_are_rejected（占用冲突） | covered |
| DEV48 | 前台主活动互斥；完成/取消/失败/截断不进可控集合；工具声明遵循当前合法动作；控制身份只来自本轮观察；配对本地核对 | I/test_grounded_targets.py::test_finished_activity_refresh_restores_starts_and_removes_old_controls、test_active_boundary_keeps_activity_controls_and_direct_switch_tools、test_paused_activity_keeps_resume_control_when_target_catalogs_are_empty | covered |
| DEV49 | 可见身份约束（购买/使用/工作/出行）；能量投影 minimum_energy_cost 同一成本函数、60 秒下限、共享工具最大值、暂停恢复判定、不可恢复可取消、恢复后工具重现 | L/test_energy_feasibility.py（13 测）；I/test_grounded_targets.py::test_zero_energy_keeps_recovery_and_purchase_then_refreshes_targets、test_route_schema_keeps_feasible_driving_and_removes_infeasible_walking、test_infeasible_paused_activity_exposes_cancel_without_resume | covered |
| DEV50 | 商品观察实时库存（售罄可见）；观看/工作目录公开个人进度；新活动≥60 秒；尾段不开放；满足交付仍可提交 | L/test_grounded_observation.py::test_real_observation_schema_purchase_and_sold_out_refresh、test_real_observation_hides_impossible_media_and_work_tail_starts；I/test_grounded_targets.py::test_watch_tail_and_work_limit_remove_only_impossible_starts | covered |
| DEV51 | 工作速率=技能等级；work_notes_multiplier=1.10；娱乐减少=效果×偏好÷接触次数 | L/test_activity_effects.py::test_work_progress_uses_skill_and_actual_notes_effect、test_repeat_watch_uses_content_rate_and_exposure_count | covered |
| DEV52 | transport 服务账户；fare=max(500,ceil(m×0.2)) 冻结 scenario 来源；启程预付不退；余额不足拒整单；幂等不重复扣款 | L/test_travel_cost.py（3 测）；L/test_semantics.py::test_travel_fare_formula_reproduces_every_frozen_route | covered |

## LabelKit 最终数据合同

覆盖对象：`docs/dev/SPEC-labelkit-delivery.md` 的「生产与验证步骤」章（语义候选与独立评审、语义返工与聚合、编码身份与 trace 绑定、反事实分支与规则扰动、恢复完整输入与发布各节）与「交付产物与发布边界」章；字段与格式定义在 `docs/CONTRACTS.md` 的「LabelKit 训练记录与交付格式」章（交付格式、数据集字段与划分、反事实与重演字段、运行报告各节）、「LabelKit 输入结构与窗口编码」章、「LabelKit 语义候选与标签值」章与「LabelKit 调用配置」章。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| DL1 | process 固定输入、关去重/重分段/分类；候选只含 target；模型与评审各读一次完整 input | D/test_semantic_schema.py::test_process_projects_use_target_only_schema_without_postprocessor、test_all_process_roles_share_one_identical_verify_contract | covered |
| DL2 | WorldMock 评审后一次性组装四字段；评审前不复制；动作目标独立 target 不发模型；意图多标签 | D/test_training.py::test_target_candidate_is_validated_before_four_field_assembly、test_candidate_cannot_include_future_facts_or_original_fields | covered |
| DL3 | dreaming 归纳/反思/反事实；知识截止与来源标记；环境重演新分支 | D/test_windows.py（dreaming 三模式窗口）；D/test_replay.py | covered |
| DL4 | deliver --replay-export：父检查点前逐字相同/清单/干预摘要/来源匹配；set_balance_fen 首次决策前；无重演 environment_replay=null | D/test_replay.py（4 测）；L/test_counterfactual.py::test_counterfactual_rejects_after_first_decision_without_mutation | covered |
| DL5 | 规则扰动 sequence generation 覆盖缺失/乱序/超时；manifest/身份/行数/哈希验证后消费；模型不生成预期 | D/test_generation.py；D/test_rules.py::test_real_rule_engine_positive_missing_reordered_timeout_and_duplicate | covered |
| DL6 | 四字段+provenance；加载器输出 prompt/completion；DatasetDict 行数与 Features 收据；连通组单 split | D/test_training.py::test_real_dataset_loading_preserves_all_facts_and_separates_answer、test_shared_source_connects_different_declared_groups_into_one_split | covered |
| DL7 | Json(decode=False)、json.loads 逐记录核对、保留 null/浮点、保存后重载逐项核对 | D/test_training.py::test_real_dataset_loading_preserves_all_facts_and_separates_answer（features['input'].dec 断言） | covered |
| DL8 | 窗口声明起止/截止/身份/边界/必要前置；上下文超限明确失败不裁正文 | D/test_windows.py（prerequisite_state/knowledge_cutoff_event_seq）；L/test_narration.py | covered |
| DL9 | 字段字典编码（RFC 6901 单跳、canonical 字节收益判定、拒绝外部/跨记录/嵌套/未用项、跨进程哈希稳定、篡改拒绝、记录身份绑定、评审通过恢复完整输入、加载器验证还原） | D/test_evidence_encoding.py（11 测全覆盖） | covered |
| DL10 | 窗口按人物核对可见来源；跨人物不透明标识拒绝、不静默删冲突 | D/test_windows.py::test_authenticated_export_rejects_cross_agent_action_group、test_authenticated_export_accepts_distinct_agent_groups_and_system_facts | covered |
| DL11 | trace 有效 LabelKit JSONL、完整成功生命周期、每个来源记录有最终通过独立评审、配置/摘要/模型/用量/调用关系核对、每轮独立评审、传输重试零 | D/test_labelkit_process.py::test_trace_binding_requires_successful_lifecycle_and_final_verdicts、test_trace_rejects_model_calls_after_the_final_passing_verdict 等 13 测 | covered |
| DL12 | 提示预算检查含候选与累计意见；超限失败不截断 | D/test_labelkit_process.py（history_repaired/history_exhausted 分支） | covered |
| DL13 | 验收含逐字节往返/哈希稳定/篡改与泄漏拒绝/真实行身份/共用入口/保存重载+真实模型自然日语义评审 | 锚（确定性部分）：D/test_evidence_encoding.py 全部；D/test_training.py（真实模型评审见 EX-9） | exempt |
| DL14 | context_window=1M、thinking 配置、输出上限 131072/65536（judge cap） | 锚：D/test_semantic_schema.py::test_all_process_roles_share_one_identical_verify_contract、test_default_output_limit_is_a_cap_for_bounded_semantic_summary（具体 token 数值由 LabelKit 项目配置承载，见 EX-10） | exempt |
| DL15 | 候选聚合上限（claims≤32/512 字/64 引用、intent≤8 不重复、uncertainty≤1024）；不删评审根据原始事件 | D/test_semantic_schema.py::test_target_only_schema_accepts_the_declared_boundary、test_target_only_schema_rejects_each_declared_boundary、test_default_output_limit_is_a_cap_for_bounded_semantic_summary | covered |
| DL16 | 结构返工错误含字段路径/允许最大/实际数量；候选全文只在原始输出 | D/test_labelkit_process.py（max_items_repaired/max_items_exhausted 分支） | covered |
| DL17 | 返工保留首轮 system/Schema/input/候选/违规反馈、各出现一次；未来目标不进任何请求 | D/test_labelkit_process.py::test_real_labelkit_annotate_and_verify_requests_are_target_blind（repaired 分支完整历史） | covered |
| DL18 | 聚合合同（按阶段/主题合并、一 claim 多事件、不逐事件复述、32 是上限、评审失败须指出未概括重复、引用只列直接支持） | D/test_semantic_schema.py::test_process_prompt_requires_thematic_aggregation_before_output | covered |
| DL19 | 逐条语义评审确认引用支持全部事实/数量/时间/因果；正文真但引用不足仍返工 | D/test_training.py::test_validator_rejects_every_identifier_outside_direct_visible_evidence、test_validator_returns_the_allowed_set_when_a_fact_has_no_citation、test_semantic_prompt_requires_every_citation_to_use_the_exact_allowed_set | covered |
| DL20 | 全部校验通过才发布不可变交付清单 | D/test_publication.py::test_manifest_inventory_binds_every_gate_and_consumer_file、test_failed_semantic_gate_never_publishes_delivery | covered |
| DL21 | verify.policy=repair 三轮；返工含全部历史意见；只输出 target；评审只看当前候选；每轮完整检查一次列全；三任务合同逐字一致 | D/test_labelkit_process.py（repair/history 系列）；D/test_semantic_schema.py::test_all_process_roles_share_one_identical_semantic_and_pointer_contract | covered |
| DL22 | 事实/推断/假设分类合同；身份不透明；intent=unknown+basis=uncertain 合法；数值近似保留 | D/test_semantic_schema.py::test_dreaming_specific_counterfactual_hypothesis_canon_is_present；D/test_training.py validator 系列 | covered |
| DL23 | 初稿/评审/返工留证；不看未来目标；目标只在通过后回填 | D/test_training.py::test_target_event_identities_are_added_only_after_semantic_review | covered |
| DL24 | 返工用尽失败不发布部分成品；world_kind=observed；resimulated 仅实际执行认证 | D/test_publication.py::test_failed_semantic_gate_never_publishes_delivery；D/test_replay.py::test_original_world_cannot_be_mislabeled_as_an_actual_replay | covered |
| DL25 | 反事实 hypothesis 形态（evidence 只须支持假设条件、结果推测措辞、评审不得索要结果证据、uncertainty 一般性、数值只用可见值） | D/test_semantic_schema.py::test_dreaming_specific_counterfactual_hypothesis_canon_is_present | covered |
| DL26 | quality.json 随导出、冻结事件确定性计算、缺失记 unknown、消费端验证字节摘要并重算、DatasetDict save/load 行守恒 | F/test_quality.py；F/test_validation.py::test_quality_report_requires_bytes_and_actual_event_recomputation；D/test_training.py | covered |
| DL27 | LabelKit HTTP timeout_s 总截止、2xx 先 JSON 顶层校验、malformed 不记原文不合成空 | LabelKit CLI 侧行为由 vendor/labelkit 冻结版本承载（EX-4）；WorldMock 侧失败不发布由 D/test_publication.py 覆盖 | exempt |

## 真实性评估与结论边界

覆盖对象：`docs/VERIFICATION.md` 的「真实性评估与结论边界」章与「结论边界」章；统计口径的调研证据在 `docs/dev/research/realism-evaluation.md`。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| EV1 | evaluate 入口；任意窗口支持；正式 84 天固定比较 | E/test_runner.py::test_one_day_prepare_and_finalize_writes_complete_committed_delivery；E/test_metrics.py::test_required_metrics_run_on_arbitrary_one_day_window | covered |
| EV2 | 逐事件恰好映射一次；屏幕帧非多行为；缺失渠道进掩码与分母；私有内容不进评分 | E/test_adapters.py::test_real_labelkit_delivery_is_certified_and_conserved 等 16 测 | covered |
| EV3 | 公开证据闭集结构事实；类别×字段交集；状态由结构事件证明；use_mode 区分；游戏终局读 done；截止只证到达 | E/test_adapters.py::test_consume_category_uses_actual_closed_use_mode_for_evidence_and_duration、test_control_and_game_results_use_object_state_instead_of_receipt_status；E/test_metrics.py::test_public_support_intersects_each_category_field_and_reports_missing_denominators | covered |
| EV4 | 盲评包无私有内容；HMAC 引用跨日稳定/候选会话隔离/左右交换不变；共享人物只声明已核验事实 | E/test_review.py::test_object_references_are_candidate_and_reviewer_scoped_but_stable_across_days、test_blind_packets_are_deterministic_and_hide_source_identity；E/test_evaluation_scenario.py | covered |
| EV5 | 六维可判定性导入强制；长期结果需真实跨日对象；缺证据 unjudgeable；单侧丰富不自动胜 | E/test_review.py::test_judgeability_requires_two_sided_state_changes_and_persistent_long_term_objects、test_missing_review_dimension_is_rejected、test_unjudgeable_dimension_rejects_a_candidate_preference | covered |
| EV6 | 必需指标清单；逐维度原值/适用/缺失/区间；十二自然周 10000 次配对重抽样固定种子 | E/test_metrics.py（9 测）；E/test_behavior_metrics.py | covered |
| EV7 | 外部参照只作对照；comparative_plausibility_only 仍完成比较；human_reference_comparison 能力 | E/test_metrics.py::test_matching_human_reference_uses_same_date_and_support_mask；E/test_runner.py::test_human_reference_changes_only_the_supported_result_status | covered |
| EV8 | 盲评包覆盖十二周全部特殊日期、每周哈希选工作日/周末日、左右随机、缺普通日记缺失；包内无模型/provider/路径/标识/key | E/test_review.py::test_formal_item_selection_covers_twelve_weeks_and_every_special_date、test_tampered_blind_packet_is_rejected_before_unblinding | covered |
| EV9 | 三个隔离模型评审会话 reviewer_kind=model；同源偏差说明 | 锚：E/test_review.py::test_review_ingest_unblinds_after_all_records_and_computes_alpha（真实模型评审见 EX-5） | exempt |
| EV10 | 接受真实人工评审记录、偏好/无法判断比例/Krippendorff alpha、可靠性不足不包装胜者 | E/test_review.py::test_review_ingest_unblinds_after_all_records_and_computes_alpha | covered |
| EV11 | 因果硬错误候选不得优胜；invalid_input/not_comparable；冲突/区间不足报混合不选胜 | E/test_decision.py::test_causal_hard_error_blocks_the_affected_candidate_from_winning（mixed 断言）；E/test_runner.py::test_no_shared_observation_returns_not_comparable、test_production_causal_hard_error_disqualifies_only_its_candidate；E/test_adapters.py（invalid_input） | covered |
| EV12 | 评估产物清单；固定哈希/配置/种子→逐字节相同；模型原始响应存证 | E/test_runner.py::test_prepare_statistics_and_blind_sampling_are_byte_deterministic；E/test_behavior_metrics.py::test_same_input_produces_byte_identical_report | covered |

## 最终门禁与工程门禁

覆盖对象：`docs/VERIFICATION.md` 的「放行门禁」章（含「最终门禁」「协议门禁」「工程门禁」「报告项」各节）与「验收原则」章（含「证据层级」「源码突变审查」「最终报告构成」各节）；其中生产代码硬规范条款由「放行门禁」章「工程门禁」节中加粗的源码硬规范条目承载。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| GT1 | 规范闭合：全部要求映射到行为测试；无 stub/skip/xfail 替代 | tests/test_coverage_gate.py（覆盖门禁机制）+ 本矩阵（映射证据） | covered |
| GT2 | 每生活模块正常/拒绝/边界/中断/重复；故障传播覆盖 actor/runner 边界 | 各模块正反测试（本矩阵「LifeEnv 状态与接口」至「共同检查点与恢复」各节）+ V/test_direct_runner_failures.py、V/test_society_decision_round.py | covered |
| GT3 | 真实端到端 AS2→DeepSeek→LifeEnv→SQLite→LabelKit→DatasetDict→rule-engine | 锚：I/test_runtime.py（真实 AS2 初始化）、D/test_training.py（真实 datasets）、D/test_rules.py（真实 rule-engine）（真实模型链见 EX-3） | exempt |
| GT4 | 并发与恢复：多人、颠倒到达不改结算、坏快照拒绝、父目录不变 | L/test_engine_round.py::test_multi_person_arrival_order_does_not_change_settlement_order；F/test_store.py::test_rehashed_checkpoint_with_inconsistent_world_is_rejected（"多人实际运行"真实部分归 EX-2） | covered |
| GT5 | Uncle Bob 源码突变审查（killed/survived 记录） | 锚：全部 1584 测试（审计期计数，口径见文末覆盖边界）为突变杀伤基础（流程门禁见 EX-6） | exempt |
| GT6 | 十二周真实运行（时钟达终点/足迹与证据完整/输出交付通过） | 锚：F/test_export.py::test_clock_end_without_successful_final_checkpoint_is_not_complete、F/test_validation.py（真实运行见 EX-2） | exempt |
| GT7 | 比较：两套真实输入认证/指标/参照限制/盲评/报告 | 锚：E/test_adapters.py（真实旧数据文件认证）、E/test_metrics.py（真实盲评见 EX-5） | exempt |
| GT8 | 行为节律：双人 24h×3 同代码运行过协议门禁；分布只报告 | 锚：E/test_behavior_metrics.py::test_observation_repetition_gate_pass_and_fail、test_silent_boundary_ratio_exact_values、test_memory_repetition_gate_values、test_memory_md_identity_residue_gate（真实运行见 EX-7） | exempt |
| GT9 | 生产代码硬规范（中文 Doxygen/英文日志/≤120 字符/函数≤50 行≤5 参/文件≤2000 行） | tests/test_style_gate.py::test_style_gate_admits_no_new_violations（常驻棘轮门禁：扫 src/worldmock+custom 全部生产 .py，行宽≤120、函数≤50 行≤5 参（self/cls 不计）、文件≤2000 行、生产函数 docstring 含中文；存量违规以 _OUTSTANDING 按文件×种类冻结，新增即红、修复后清单必须同步缩减） | covered |

## 人物真实性修订合同

覆盖对象：`docs/dev/SPEC-life-runtime.md` 的「身体语义：两过程睡眠、睡眠不足账、体感分级」章、「决策节律：结算边界与唤醒条件分离」节、「观察投影与提示合同」章与「社交最小可行版」章；`docs/dev/SPEC-life-realism.md` 的「两阶段决策」「记忆合同」「动机层」「当日目标」「世界素材品类要求」「画像编译」「商务与进食」「呈现与导出」「默认裁决与备选」各章；`docs/CONTRACTS.md` 的「LifeAgent 人格与记忆字段」章与「观察投影字段」章；验收指标与门禁在 `docs/VERIFICATION.md` 的「放行门禁」章与「实施顺序」章；文件归属在 `docs/INDEX.md` 的「文件归属」章。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| RC0 | schema_version；旧状态加载拒绝；无兼容别名；prompt/tool/code SHA 随之改变 | L/test_revision_contract.py::test_world_state_schema_version_five_rejects_old_checkpoints（当前世界结构为 5，旧检查点直接拒绝）、test_sleep_debt_hours_has_no_residual_references；I/test_grounded_evidence.py::test_runtime_code_identity_includes_life_agent_content | covered |
| RC1 | wake_reason 闭集 17 类触发条件 | L/test_wake_reasons.py::test_concrete_wake_reasons_are_preserved、test_frozen_wake_and_intent_names；L/test_revision_contract.py::test_engine_wake_reasons_cover_each_trigger_class；F/test_event_fields.py::test_wake_reason_vocabulary_is_the_frozen_spec_set | covered |
| RC2 | 静默边界无决策四事件；共同 body_settlement 环境事件带 decision_offered/wake_reasons；提供决策记录全部命中原因 | L/test_revision_contract.py::test_silent_boundary_settles_without_decision_events、test_offered_boundary_records_actual_wake_reasons | covered |
| RC3 | 常规与空闲重估是场景参数；精确任务与闭店威胁；睡眠不受间隔，游戏逐回合 | L/test_wake_reasons.py；L/test_engine_feedback.py；L/test_scenario.py | covered |
| RC4 | 下一次决策机会取各项最小值（复用 next_shared_body_boundary_seconds） | L/test_wake_reasons.py::test_next_decision_takes_minimum_of_all_candidates、test_next_decision_excludes_now_and_past；L/test_activity_effects.py::test_body_boundary_predicts_first_energy_threshold_second | covered |
| RC5 | BodyState 新增四字段、删 sleep_debt_hours、无别名 | L/test_revision_contract.py::test_sleep_debt_hours_has_no_residual_references | covered |
| RC6 | 两过程公式（χw=18.2h、χs=4.2h、H=0.67、L=0.17、振幅 0.12、困意截断 [0,1.2]） | L/test_sleep_two_process.py::test_awake_step_matches_raw_formula、test_sleep_step_matches_raw_formula、test_thresholds_match_raw_formula_across_day、test_sleepiness_clips_to_1_2_above 等 57 测 | covered |
| RC7 | chronotype_midsleep 时型（默认 04:00、晚型后移 1h、初始按已睡时长反推） | L/test_sleep_two_process.py::test_default_midsleep_is_04、test_late_threshold_minimum_moves_to_05、test_back_calculation_from_eight_hours_sleep | covered |
| RC8 | 困意五级分级及锚点时刻 | L/test_sleep_two_process.py::test_grade_boundaries、test_anchor_grades_20_and_21_match_spec、test_cannot_stay_awake_onset_about_23_22 | covered |
| RC9 | 睡眠不足账（need/(24−need)、恢复 1h/h、[0,24]、need 7-9 默认 7.5） | L/test_sleep_two_process.py::test_need_boundaries_change_rate、test_cap_at_24、test_floor_at_zero、test_valid_need_returned | covered |
| RC10 | 效率曲线输入改 sleep_deficit_hours、节点不变 | L/test_sleep_two_process.py::test_work_efficiency_nodes_and_midpoints、test_food_utilization_nodes_and_midpoints、test_frozen_curve_constants | covered |
| RC11 | 饥饿体感（anchor 1200、昼夜 17%、餐时 +0.15、五级）+娱乐分级+800/400 世界事实 | L/test_felt_states.py::test_anchor_800kcal_at_20h_is_hungry、test_circadian_matches_reference_over_day、test_meal_window_inside_and_outside、test_appetite_level_boundaries、test_entertainment_level_boundaries | covered |
| RC12 | 社交需要（0.04/h 增、交谈 0.5、通话 0.4、消息 0.05、共同用餐 0.3、三级） | L/test_felt_states.py::test_awake_growth、test_conversation_relief_floors_at_zero、test_call_relief、test_shared_meal_relief、test_message_relief、test_social_level_boundaries | covered |
| RC13 | 情绪（脉冲表、半衰期 45min、神经质调制、五级、只影响呈现与重要度） | L/test_felt_states.py::test_pulse_table_values、test_decay_halves_at_halflife、test_halflife_modulation、test_multi_pulse_with_neuroticism、test_mood_level_boundaries；L/test_revision_contract.py::test_plan_segment_pulse_requires_matching_activity | covered |
| RC14 | 滞回以上次实际决策分级为参照 | L/test_felt_states.py::test_no_change_when_decision_reference_matches、test_full_flow_settlement_wiggle_does_not_wake；L/test_revision_contract.py::test_felt_band_hysteresis_uses_last_decision_reference | covered |
| RC15 | 公共身体数值写 body_settlement 环境事件，活动保留自身效果与引用；observation 私有数值不进入人物提示 | L/test_money_settlement.py::test_shared_body_event_carries_validated_body_flows；L/test_grounded_observation.py | covered |
| RC16 | user 消息三部分（persona_card/scene/facts）与内容清单 | I/test_life_agent_protocol.py::test_react_messages_are_three_parts_and_observation_written_once；L/test_narration.py::test_scene_contains_key_content、test_parts_in_contract_order | covered |
| RC17 | 禁入字段清单（world_state_version/end_time/affordance_contract/energy_feasibility/observation_event_id/motivation_hints/profile.*/game_catalog 全表/完整快照/路线几何） | I/test_life_agent_protocol.py::test_blacklisted_observation_fields_never_enter_messages；L/test_narration.py::test_each_forbidden_key_alone_raises、test_route_geometry_fields_raise | covered |
| RC18 | 观察不重复序列化 | I/test_life_agent_protocol.py::test_react_messages_are_three_parts_and_observation_written_once；V/test_worldmock_life_hooks.py::test_structured_observation_appears_once_in_user_message | covered |
| RC19 | 门店供给超过 1800 秒或离开呈现地点失效，呈现其他供给不续期 | L/test_revision_contract.py::test_observation_projection_blacklist_and_product_ttl；L/test_storefront_discovery.py::test_presenting_another_supply_does_not_renew_old_supply_visibility | 逐供给行为已通过本特性完整验收 |
| RC20 | 工作材料增量揭示单次≤2000 字符 | L/test_revision_contract.py::test_work_material_reveal_capped_at_2000_chars_per_decision | covered |
| RC21 | 字幕只交付新跨过区间、跨边界同条不重复 | L/test_revision_contract.py::test_subtitle_does_not_repeat_across_boundary；L/test_content_game.py::test_subtitles_reveal_only_played_intervals_across_pause_and_repeat | covered |
| RC22 | recent_action_results 保留全部新结果、状态、错误原因与工作公开反馈；内部证据不进入提示 | L/test_decision_context_projection.py；I/test_decision_context_roundtrip.py | covered |
| RC23 | 系统提示身份为主≥50% 词数；协议移入工具 description；回合预算仅 ≤2 时一句；无"评审/评分/引擎期望"；礼仪以场景表达 | I/test_life_agent_protocol.py::test_system_prompt_is_identity_first_with_word_share（禁词断言与回合预算句断言） | covered |
| RC24 | user≤40000/system≤3000 字符超限运行故障不截断；quality.json 报告 token 分布 | L/test_narration.py::test_user_over_by_one_raises、test_system_over_by_one_raises、test_exactly_at_budgets_passes；E/test_behavior_metrics.py::test_decision_token_median_from_paired_response | covered |
| RC25 | 提示与场景文本 zh-CN | L/test_narration.py::test_scene_rejects_non_contract_language | covered |
| RC26 | 两阶段参数（决策自述 disabled/1.0/8000、reasoning.max_tokens=1500、无 tools；行动 enabled/auto 回传 reasoning_content） | I/test_runtime.py::test_two_stage_model_identity_shape_matches_life_agent_constants、test_two_stage_model_identity_is_frozen_and_tamper_rejected；V/test_worldmock_life_hooks.py::test_prelude_decision_statement_rejects_tools_and_empty_content | covered |
| RC27 | 决策自述写 decision_started.data.decision_statement+模型证据；超长/空重试一次再失败；决策自述不改世界 | L/test_grounded_observation.py::test_decision_started_written_once_complete_with_decision_statement、test_invalid_decision_statement_is_absent_from_direct_engine_evidence；I/test_life_agent_protocol.py::test_two_stage_decision_statement_evidence_and_single_retry、test_decision_statement_failing_twice_fails_the_whole_decision | covered |
| RC28 | finish.memories 允许空列表 | V/test_agent_react_validation.py::test_native_finish_accepts_empty_memory_list；I/test_activity_schema.py::test_finish_accepts_empty_memories_list_in_schema_and_runtime | covered |
| RC29 | 决策自述仅 dreaming 任务可读；dreaming 窗口可含 decision_statement 与 scene（动作/意图输入不变） | src/worldmock/delivery/windows.py 的 DREAMING_PRIVATE_EVENTS=PRIVATE_EVENTS−{decision_started}；D/test_windows.py::test_dreaming_window_reads_decision_statement_and_scene（dreaming 窗口含当日 decision_started.data.decision_statement 与 observation scene）、test_action_and_intent_windows_still_exclude_the_decision_statement_carrier（动作/意图窗口无 decision_started、无 decision_statement） | covered |
| RC30 | memories 0-8 条空合法；text 人物语言无标识/哈希；引用放 refs；九种主观类型及固定 subjective 标签 | V/test_person_tools.py::test_validate_finish_memories_bounds；I/test_activity_schema.py::test_memory_text_rejects_hexadecimal_identities；I/test_verified_memory.py、I/test_activity_schema.py | covered |
| RC31 | bigram Jaccard≥0.6 与 24h 同类型合并（只增计数与最近时刻） | V/test_worldmock_life_hooks.py::test_similar_same_type_memory_merges_within_window、test_similar_memory_outside_window_or_type_is_kept、test_bigram_jaccard_bounds | covered |
| RC32 | 巩固触发三选一（importance≥3.0/待巩固≥10/就寝首边界）；单条 preference 不触发；提示传 persona_card 无示例身份、固定章节；WorldMock 候选仅九种主观类型；当前事实章节由已提交事件装配 | V/test_worldmock_life_hooks.py::test_consolidation_triggers_importance_accumulation、test_consolidation_triggers_pending_threshold、test_consolidation_triggers_sleep_boundary、test_single_preference_does_not_trigger_consolidation、test_consolidation_prompt_carries_persona_card_without_example_identity；V/framework/test_memory.py；I/test_verified_memory.py | covered |
| RC33 | 就寝巩固同执行反思：三问≤5 条洞见、每条引用记忆身份 | V/test_worldmock_life_hooks.py::test_reflection_persists_cited_insights、test_reflection_rejects_unknown_reference、test_reflection_rejects_more_than_five_insights | covered |
| RC34 | 评分器写 observation.data.motivation_internal，不展示候选/名次/概率 | L/test_activity_menu.py::test_motivation_internal_remain_top5_under_layered_menu；I/test_life_agent_protocol.py::test_blacklisted_observation_fields_never_enter_messages（motivation_internal 在禁入清单） | covered |
| RC35 | drive_summary 一句中文、不含数字权重 | L/test_motivation.py::test_summary_common_knowledge_without_browse、test_summary_open_flips_when_market_opens、test_no_store_scenario_omits_summary | covered |
| RC36 | 评分器修正（成本/小时归一、价格单计、正餐≥300kcal 前三、家族≤2、<0.3 无娱乐、启用集采样、relief 通道、重复进食 1/(1+n) 降权） | L/test_motivation.py::test_cost_normalized_per_hour_across_durations、test_price_enters_cost、test_meal_floor_and_top_three、test_same_entertainment_family_capped_at_two、test_need_below_floor_suppresses_entertainment、test_lifestyle_enabled_set_scopes_sampling、test_repeated_food_downweighted_by_sensory_satiety | covered |
| RC37 | 有序目标、空清单、一次提交、修订、正时长推进、一次证据与情绪；三层报告 | [当日目标规格](SPEC-day-plan-goals.md)与[验收记录](research/day-plan-goals-verification.md)逐要求映射 | covered |
| RC38 | 人物卡片作息保留；当日目标不带绝对时间，环境本地日界决定生命周期 | L/test_day_plan.py；L/test_revision_contract.py；I/test_observation_agent_seam.py；P/test_models.py | covered |
| RC39 | 社交在 LifeEnv 内实现、不装 SimpleSocialSpace、复用消息通道与事件类型 | L/test_social.py（15 测经 engine/store 实现承载）；L/test_revision_contract.py::test_send_message_delivers_inbox_and_arrival_event | covered |
| RC40 | people_here（姓名/活动家族/可交谈=清醒且非主动游戏）；变化构成唤醒 | L/test_social.py::test_people_here_filters_location_and_exposes_closed_fields、test_can_talk_requires_awake_and_not_active_gaming；L/test_wake_reasons.py（person_arrived/person_left） | covered |
| RC41 | send_message 瞬时、≤300 汉字、inbox 送达、environment_event kind=message_arrival | L/test_social.py::test_send_message_accepts_300_chars_and_rejects_301、test_send_message_delivers_to_world_agent_and_contact；L/test_revision_contract.py::test_send_message_delivers_inbox_and_arrival_event | covered |
| RC42 | chat：共同在场、对方可交谈、前台活动、chat_invite、接受 0.5/h 拒绝发起方 0.2/h | L/test_social.py::test_start_chat_rejects_absent_or_unavailable_partner、test_start_chat_registers_foreground_activity_with_invite、test_chat_settlement_rates_for_accept_and_decline | covered |
| RC43 | call：联系人、10-60 分钟、占用注意力、0.4/h | L/test_social.py::test_call_duration_boundaries_10_and_60_minutes、test_call_relief（felt） | covered |
| RC44 | contacts[]（id/称谓/关系/亲密度 0-30/渠道/reply_policy 闭集） | L/test_social.py::test_contact_closeness_bounds_and_policy_closed_set；L/test_scenario.py::test_contacts_declare_mapping_and_reply_policies | covered |
| RC45 | model 策略每消息至多一次回复（独立调用、disabled、≤200 汉字、完整证据、source_kind=model_npc、只进收件箱不改资源任务） | L/test_export_integration.py::test_model_npc_reply_loop_delivers_to_inbox_with_evidence、test_model_npc_reply_at_most_once_per_message、test_model_npc_reply_transport_failure_propagates；L/test_social.py::test_reply_policy_branches_with_at_most_once_idempotency、test_model_reply_request_and_finalize_validate_200_chars | covered |
| RC46 | 他人只暴露姓名/家族/可交谈，不暴露身体数值/余额/任务/记忆 | L/test_social.py::test_people_here_filters_location_and_exposes_closed_fields（closed fields 断言） | covered |
| RC47 | 盲评包与导出活动记录增加地点与同伴两列（同伴匿名引用） | src/worldmock/evaluation/review.py 的 _timeline_row 携带 location 与 companion 两列（_companion_ref 匿名 HMAC 引用）；E/test_review.py::test_activity_rows_carry_location_and_anonymous_companion（行带 location=office-west、companion 匹配 object-[0-9a-f]{64}、原文 agent:2 不入序列化）、test_companion_reference_is_slot_isolated_and_day_stable（评审槽隔离、同槽跨日稳定）；行集断言恰为 {category,start,end,status,location,companion} | covered |
| RC48 | 地点品类（家/办公室/食堂餐馆≥2/便利店/超市菜场/公园健身；真实 OSM+OSRM 冻结） | CO/test_category_corpora.py::test_locations_cover_all_required_kinds_with_real_osm_coordinates；CAT/test_category_admission.py::test_location_required_kind_missing、test_location_restaurant_floor_of_two | covered |
| RC49 | 家的可供性（洗漱 15 分钟、7 份早餐储备、进食扣减超市补充、留痕） | CAT/test_category_admission.py::test_home_pantry_below_seven_servings、test_home_morning_wash_not_fifteen_minutes；CO/test_category_corpora.py::test_home_pantry_seven_breakfast_servings_reference_catalog_foods、test_morning_wash_activity_declared_at_fifteen_minutes | covered |
| RC50 | 办公室可供性（茶水间免费饮水 5 分钟微量娱乐、会议室、同事在场） | CAT/test_category_admission.py::test_office_affordance_missing、test_office_pantry_water_not_free | covered |
| RC51 | 正餐≥30（热食/餐馆/外卖、热量来源标注、15-45 元、20-40 分钟、堂食或配送） | CAT/test_category_admission.py::test_meals_below_contract_minimum、test_missing_meals_corpus、test_meals_missing_energy_source_annotation、test_meals_price_outside_window、test_meals_duration_outside_window、test_meals_without_dine_in_or_delivery；CO/test_category_corpora.py::test_meals_corpus_holds_thirty_sourced_entries | covered |
| RC52 | 内容条目 title/genre[]/language/age_rating；拒绝≥18；drama 替换为中文 | CAT/test_category_admission.py::test_content_age_rating_eighteen_rejected、test_content_metadata_field_missing、test_drama_corpus_must_be_chinese；CO/test_category_corpora.py::test_every_viewing_entry_carries_four_metadata_fields、test_drama_corpus_replaced_with_chinese_short_drama | covered |
| RC53 | 工作≥20 互不相同中文材料、职业匹配；assignee_agent_id 必填、合同按人分配 | CAT/test_category_admission.py::test_work_materials_below_twenty、test_work_materials_duplicate_rejected、test_task_assignee_agent_id_required；L/test_scenario.py::test_work_materials_are_distinct_and_persona_matched | covered |
| RC54 | 生活活动 Schema 增 location_kinds/time_window/needs_partner/effects；启用集来自画像编译 | CAT/test_category_admission.py::test_lifestyle_v2_field_missing、test_lifestyle_time_window_inverted；CO/test_category_corpora.py::test_lifestyle_items_carry_v2_fields_with_valid_domains；CAT/test_affordance_shadow.py::test_habit_routing_matches_classify_action_event | covered |
| RC55 | 每人物至少两名联系人（同事映射人物、老友/家人场景联系人） | CAT/test_category_admission.py::test_contacts_below_two_per_persona、test_contacts_colleague_mapping_invalid | covered |
| RC56 | 语料获取走 prepare-corpora；来源/许可/哈希进 corpora/manifest.json | CO/test_corpora.py::test_formal_manifest_validates_every_file_and_selected_counts | covered |
| RC57 | PersonaCard 字段全集、三次一致性抽取、确定性渲染≤600 汉字 | P/test_extractor.py（15 测，含 three_run_consensus_picks_field_modes）；P/test_models.py::test_card_field_completeness；P/test_textgen.py::test_render_within_hanzi_budget、test_over_budget_card_raises_honestly | covered |
| RC58 | 大五只进世界层五条路径；分数/九参数/权重不进提示；entertainment_preferences 删除；taste_weights 确定性匹配 | P/test_mapper.py::test_dynamics_fields_cover_the_five_trait_routes、test_direction_anchors；P/test_taste.py::test_weights_from_genre_matching；L/test_persona_upgrade.py::test_card_render_never_leaks_big_five_or_random_floats | covered |
| RC59 | 四模板字段删除、内容并入卡片 | P/test_models.py::test_legacy_contract_constants、test_old_v1_profile_payload_is_rejected；P/test_compile.py::test_compile_rejects_legacy_template_fields | covered |
| RC60 | validate 输出 persona_coverage.json；<80% 失败并列缺失项 | F/test_validation.py::test_validate_project_passes_and_writes_persona_coverage、test_validate_project_rejects_coverage_below_threshold；P/test_coverage.py（6 测，含 threshold_boundary_passes_at_exactly_80_percent） | covered |
| RC61 | purchase 1-5 件同商家同履约一次付款一次配送；任一件不可购整单拒绝不删件 | I/test_grounded_targets.py::test_purchase_rejects_mixed_merchants_before_dispatch、test_purchase_binds_multi_item_enums_and_validates_order_bounds；L/test_revision_contract.py::test_multi_item_purchase_rejects_whole_order | covered |
| RC62 | consume 1-3 件持有食品、同地点一次进食、时长取最长、热量效果累加；非食品单件；实际库存由环境校验 | I/test_grounded_targets.py::test_consume_multi_item_meal_duration_and_quantity_authority；L/test_revision_contract.py::test_multi_item_meal_accumulates_energy；I/test_category_inventory_protocol.py::test_inventory_only_recipe_survives_real_model_roundtrip | covered |
| RC63 | inspect_run 读取 tool_name | F/test_render_day.py::test_inspect_run_brief_reads_tool_name | covered |
| RC64 | export 增加确定性投影 timeline/<agent>-<date>.md（合并静默边界与同活动、名称非标识）；render_day 生成 | F/test_export.py::test_export_writes_hash_bound_timeline_projections、test_export_timeline_projection_matches_render_day_output；F/test_render_day.py（9 测） | covered |
| RC65 | tools/run-viewer.html 增加"一天"视图 | 规格自身指定"手工核对"（修订合同文件清单行）；确定性锚 F/test_export.py::test_export_timeline_projection_matches_render_day_output → EX-8 | exempt |
| RC66 | 盲评活动记录地点与同伴列 | E/test_review.py::test_activity_rows_carry_location_and_anonymous_companion、test_companion_reference_is_slot_isolated_and_day_stable（与 RC47 同组测试） | covered |
| RC67 | dreaming 窗口输入可含 decision_statement 与 scene；动作/意图可见输入不变 | D/test_windows.py::test_dreaming_window_reads_decision_statement_and_scene、test_action_and_intent_windows_still_exclude_the_decision_statement_carrier（与 RC29 同组测试） | covered |
| RC68 | 11 项报告项+4 项协议门禁+code_sha256 分组+只报告不裁定 | E/test_behavior_metrics.py（19 测逐指标：observation_repetition_gate、silent_boundary_ratio、decision_token_median、autonomous_decision_rate、wake_reason_distribution、wait_minutes_and_early_arrival、sleep_schedule、meal_diversity、js_distance、activity_distribution、persona_distinguishability、social_minutes、memory_repetition_gate、memory_md_identity_residue、report_groups_by_code_sha256） | covered |
| RC69 | 外部参照 2018 时间利用调查、逐项注明口径、只作方向对照 | E/test_behavior_metrics.py::test_reference_minutes_match_spec_constants、test_activity_distribution_matches_reference_exactly、test_js_distance_hand_anchor | covered |
| RC70 | 文件清单各行指定测试存在并执行 | 逐行核对：除 delivery/windows.py 行（GAP-3）外全部存在（I/test_life_agent_protocol、test_grounded_targets、L/test_activity_menu、I/test_runtime、L/test_engine_feedback、test_engine_round、test_grounded_observation、test_wake_reasons、test_social、test_day_plan、test_body、test_body_flows、test_sleep_two_process、test_felt_states、test_narration、test_scenario、test_state_serialization、I/test_activity_schema、CAT/test_admission、L/test_motivation、test_lifestyle、CAT/test_affordance_shadow、L/test_activities、test_commerce、test_content_progress_presentation、test_work_contracts、P/、I/test_persona_profile、L/test_persona_upgrade、F/test_validation、F/test_export、test_quality、E/test_behavior_metrics、test_review、V 分支记忆测试） | covered |

（修订合同"实施顺序/默认裁决与备选/本节依据"为管理性与备选条款，按口径排除；文件清单中
docs/dev/implementation-status.md 行为管理性记录，排除。）

## 候选契约与呈现规则

覆盖对象：`docs/dev/SPEC-life-runtime.md` 的「候选契约与呈现规则」章（统一候选契约：生成、资格、排序三段；两条排除纪律；两级上限；下推：两层、游标、返回体；曝光轮转；禁止永久退役；地点语义与检索分区；检索：确定性索引与 facet；随身持有与远程订购各节）；
`docs/dev/SPEC-life-realism.md` 的「`location_kinds` 的运行期强制语义」「素材可达性」「素材淘汰与检索机制的禁止项」「候选与呈现」（含候选确定性、呈现上限与下推游标、呈现轮转）各节；
门禁判定对象在 `docs/VERIFICATION.md` 的「放行门禁」章「工程门禁」节的「候选与呈现一致性」条（九项判据）。

**本版判定口径说明**：本节条款是本轮新增，落地状态已复判，判定如下。

- **纯函数层已落地**：`src/worldmock/life/candidates.py`（统一三段式、排除纪律、排序键、游标派生）与
  `src/worldmock/life/rotation.py`（轮转、名额、闭式上界）为实现，逐条有 `tests/life/test_candidates.py`（49 测）、
  `tests/life/test_rotation.py`（20 测）与 `tests/life/test_candidate_constants.py`（32 测）覆盖。
- **运行期接线与持久化已落地**：`src/worldmock/life/presentation.py` 是运行期装配层（装候选输入、取候选、
  定呈现窗口），`src/worldmock/life/observe.py` 的观察投影与 `browse_activities` 下推均经它产出；
  `src/worldmock/life/engine.py` 经 `state_commit.flush_presentation` 把留痕并入世界状态；
  `WorldState.schema_version` 当前为 5，`presentation_state` 继续承载轮转时刻；逐供给可见记录、库存和报价随世界状态保存。
  `tests/life/test_candidate_wiring.py`（9 测）逐条覆盖该层的端到端观察：
  下推返回体形状、轮转名额真的决定呈现顺序、观察投影经候选契约产出且留痕落盘、全池刚呈现过时的顺序退化、
  排除项带闭集原因码、游标下推无重无漏、非法游标载荷报错、五类共用一个三段式、返回体行可读。
- **检索与准入侧已落地**：`src/worldmock/life/retrieval.py`（确定性倒排与 facet、闭式地点类词表、
  静态可达性报告）有 `tests/life/test_retrieval.py`（31 测）覆盖；准入侧两个新错误码由
  `src/worldmock/catalog/admission.py` 的 `_check_partition_keys` 与 `_check_partition_feasibility` 实施，
  由 `tests/catalog/test_reachability_admission.py`（9 测）覆盖；地点类的运行期强制在
  `src/worldmock/life/activities.py` 的 `require_location_kinds`，由
  `tests/life/test_activity_location_kinds.py`（9 测）覆盖。
- 因此本节 44 行**全部 `covered`**（逐条附测试路径），不存在 `gap`，也不存在 `exempt`。
  其中 `CC43`（随身持有的地点嵌套）与 `CC44`（远程订购的取货/配送在场语义分野）在第二轮复判中由 `gap`
  改判 `covered`；原单件测试现由整单和位置库存测试替代，当前入口见对应映射行。
- **第三次增量补测（2026-09-19）**：余下的七行 `CC7`、`CC17`、`CC18`、`CC29`、`CC32`、`CC35`、`CC40`
  逐行补入真实性测试后改判 `covered`，**未改动任何生产代码**。补测的判据边界如下：
  - `CC7` 跨运行分支一致性：经生产接口（`LifeEngine.checkpoint` → `FootprintStore.restore` → `LifeEngine.open`）
    建普通分支，把父运行推进到与分支同一世界状态版本，再对五个品类分区的呈现结果折成规范 JSON 逐字节比较；
    断言同时核对冻结目录摘要与画像身份在分支上仍是同一对值。初始仅取候选纯函数层证据不足以覆盖本条，
    故此处用端到端分支路径。
  - `CC17` 动机层截断不作用于呈现：断言呈现上限等于场景常量（20）且与 `motivation.MAX_CANDIDATES`（26）不等，
    把该常量改成其他值后呈现条数不变，并用 AST 扫描证明呈现链路四模块都不导入也不出现该常量。
  - `CC18` 下推层数限两层：断言顶层键恰为下推返回体闭集、第二层条目行恰为活动行闭集且全为标量、
    条目行不含游标/页码/嵌套子表，续页行仍全部落在同一品类；游标载荷形状不合即拒载。
  - `CC29` 决策点 `D` 取实测区间（`docs/EXPERIMENT.md`「决策点节奏」的中位 71、范围 9–112.5、保守取值 60）：
    断言区间端点与保守取值都可用作入参、上界恒等于 `⌈N ÷ k⌉ ÷ D`、改 `D` 即改等待（不硬编码天数）、
    缺省入参落在区间内部。**边界说明**：断言核对的是「缺省值落在实测区间内」与「上界随 `D` 精确变化」，
    而非某个特定缺省数值（把缺省从 75 改成区间内的其它值不会使该断言失败）——这是刻意的：
    规格要求的是口径取实测区间，不是钉死某一个点。
  - `CC32` 退役只走墓碑语义：断言带墓碑的目录里下架条目在候选里以 `ELIGIBILITY_DELISTED` 出局、
    同一时刻仍在目录里可读（条目行逐字节不变），并用 AST 与文本扫描证明契约层与生活层没有删除条目
    的通路、也没有「删除条目」「下架事件」这类事件名。
  - `CC35` 声明含 `anywhere` 恒可行：在 `candidates.py::_location_reason` 侧补测，同一用例内区分
    「条目侧或地点侧声明 `anywhere`」「两侧都无声明」「两侧都有声明且交集为空」三形，
    并逐形核对该原因码的排除计数。这是当时的历史判据；地图特性已删除地点侧 `anywhere` 与缺声明放行，
    当前规则和测试以本节对应映射行及地图增量矩阵为准。
  - `CC40` 禁止向量检索与近似最近邻：新增常驻断言——逐文件扫描 `src/worldmock` 与 `custom` 的导入图，
    禁止 ANN 库；断言检索链路四模块无浮点距离调用、也不导入 `random`/`numpy`/`scipy`/`sklearn`/`torch`/`math`；
    断言本项目自己的依赖声明里无该词表，并如实记录 vendored 上游 `vendor/agentsociety2` 自带的 `faiss-cpu`
    只由上游 `env/router_codegen.py` 导入、本项目运行路径不经它检索。

| ID | 条款 | 测试映射 | 状态 |
| --- | --- | --- | --- |
| CC1 | 商品/生活活动/内容/游戏/任务五类走同一套生成、资格、排序三段，不得另起独立链路 | L/test_candidates.py::TestThreeStageShape::test_every_kind_reaches_ordered_candidates、test_return_body_exposes_all_three_stages | covered |
| CC2 | 五个品类返回体形状一致（同一组顶层键） | L/test_candidates.py::TestThreeStageShape::test_single_kind_input_yields_same_top_level_keys、test_every_kind_reaches_ordered_candidates | covered |
| CC3 | 生成段输入为在场 ∪ 浏览时效内 ∪ 随身持有 ∪ 画像激活集，附每来源命中计数 | L/test_candidates.py::TestThreeStageShape::test_generated_counts_candidates_without_eligible_restriction、test_object_hit_by_two_sources_is_one_candidate_with_two_sources | covered |
| CC4 | 同一（世界状态、人物、场景）两次取候选逐字节相同 | L/test_candidates.py::TestDeterminism::test_same_input_twice_is_byte_identical | covered |
| CC5 | 候选是派生量：身份只由（冻结目录摘要、画像摘要、世界状态版本、选择器版本）四项决定，不含时间戳/缓存/模型输出 | L/test_candidates.py::TestDeterminism::test_wall_clock_does_not_enter_the_result、test_module_does_not_read_the_clock_or_randomness、test_cursor_identity_covers_the_four_declared_terms | covered |
| CC6 | 分桶与游标哈希一律用 `hashlib`，禁用内建 `hash()` | L/test_candidates.py::TestDeterminism::test_hash_seed_does_not_change_the_result、test_module_uses_hashlib_and_never_the_builtin_hash | covered |
| CC7 | 同一候选集在父运行与从该状态分支出的运行里一致 | L/test_candidate_contract_boundaries.py::test_candidate_set_is_identical_in_the_branch_and_the_parent_run（经生产接口 `engine.checkpoint` + `store.restore` + `LifeEngine.open` 建普通分支；父运行推进到与分支同一世界状态版本后，五个品类分区的呈现行/排除项/逐来源与逐原因码计数折成规范 JSON 逐字节相同，并断言冻结目录摘要与画像身份在分支上仍是同一对值） | covered |
| CC8 | 只有世界事实可排除：三类可排除来源（地点不可用/库存或下架/身体与资源条件不允许） | L/test_candidates.py::TestExclusionDiscipline::test_closed_hours_excludes_product_at_a_shut_merchant、test_missing_location_excludes_everything_with_a_stable_code、test_retained_plus_excluded_equals_the_generated_pool | covered |
| CC9 | 每一类排除携带闭集内稳定原因码，不自由拼字符串 | L/test_candidates.py::TestExclusionDiscipline::test_every_exclusion_reason_is_inside_the_closed_set、test_exclusion_counts_key_every_closed_reason_even_at_zero | covered |
| CC10 | 地点不可行与资源冲突、能量不足是两个可区分原因码 | L/test_candidates.py::TestExclusionDiscipline::test_resource_conflict_is_reachable、test_energy_insufficiency_is_reachable、test_missing_location_excludes_everything_with_a_stable_code、test_world_fact_exclusions_carry_their_code | covered |
| CC11 | 软偏好只改序不出局：口味权重、注意力先验、画像激活集只进排序键 | L/test_candidates.py::TestSoftPreferenceOnlyReorders::test_flat_attention_keeps_every_entry_of_the_pool、test_missing_taste_entries_are_not_dropped | covered |
| CC12 | 口味权重为零的条目仍进入候选并按最低档排序（取代 `attention_prior × preference > 0` 入选条件） | L/test_motivation.py::TestScorerContract::test_zero_taste_weights_keep_entertainment_ranked_last、test_unstated_genre_items_stay_in_candidate_pool；L/test_candidates.py::TestSoftPreferenceOnlyReorders::test_zero_taste_weight_entries_survive_and_rank_last | covered |
| CC13 | 被排除项以带原因码的排除项出现，不从返回体静默消失；「没显示」与「不存在」可区分 | L/test_candidates.py::TestThreeStageShape::test_filter_returns_retained_and_excluded_separately、TestExclusionDiscipline::test_retained_plus_excluded_equals_the_generated_pool | covered |
| CC14 | 排除项计数加保留项计数等于候选全集规模（`ROTATION_DEFERRED` 计入排除项） | L/test_candidates.py::TestExclusionDiscipline::test_retained_plus_excluded_equals_the_generated_pool、test_exclusion_counts_key_every_closed_reason_even_at_zero；L/test_candidates.py::TestRotationDeferralIsNotExclusion::test_withheld_objects_stay_retained_and_uncounted_as_excluded | covered |
| CC15 | 池上限与单次呈现上限分开，不得合并成一个数 | L/test_rotation.py::test_presentation_count_never_exceeds_scenario_limit、test_multi_subtype_pool_keeps_cap_and_fills_window；L/test_candidates.py::TestThreeStageShape::test_return_body_exposes_all_three_stages；L/test_candidate_constants.py::test_count_constants_have_an_upper_bound | covered |
| CC16 | 池上限只用于计数与下推，不直接进提示；其取值出现在 `browse_activities` 返回的 `total` 里 | L/test_candidate_wiring.py::test_browse_result_matches_down_push_contract（返回体恰为 `tool`/`category`/`returned`/`total`/`next_cursor`/`note`/`items`/`excluded`，`count` 不再是字段，`total` 随游标下推给出池侧总数） | covered |
| CC17 | 动机层内部截断（`MAX_CANDIDATES`）不作用于呈现，不得当作单次呈现上限 | L/test_candidate_contract_boundaries.py::test_motivation_truncation_never_becomes_the_presentation_limit（呈现上限 == 场景常量 == 20，且与 `motivation.MAX_CANDIDATES`（26）是两个不同的量）、test_presentation_window_ignores_the_motivation_truncation（把该常量改成 1/3/64 后呈现条数仍只由场景常量决定）、test_presentation_modules_never_read_the_motivation_truncation（AST 断言 `candidates`/`presentation`/`rotation`/`observe` 四个模块都不导入也不出现该常量） | covered |
| CC18 | 下推层数限两层（品类 → 条目），语义是约束传播不是界面分页 | L/test_candidate_contract_boundaries.py::test_down_push_has_exactly_two_layers（顶层键恰为下推返回体闭集、第二层条目行恰为活动行闭集且全为标量、条目行不含游标/页码/嵌套子表，续页行仍全部落在同一品类——约束传播而非界面分页）、test_cursor_payload_admits_no_third_level（游标载荷恰为排序键+偏移+身份三件，排序键整长三位，形状不合即拒载）、test_presentation_envelopes_themselves_have_no_third_layer（两个返回体字段面不含页码/偏移/嵌套子表，候选行只有排序键四件）；形状侧沿用 L/test_candidates.py::TestThreeStageShape::test_return_body_exposes_all_three_stages | covered |
| CC19 | 分页用游标不用页码；游标是上一页最后一条的完整稳定排序键 | L/test_candidates.py::TestDeterminism::test_cursor_is_the_full_ordering_key_of_the_last_row、test_order_key_is_a_total_order_and_a_cursor_source；L/test_rotation.py::test_rotation_cursor_only_moves_forward_and_wraps_once_per_cycle | covered |
| CC20 | 游标只由（冻结目录、世界状态版本、排序键版本、游标）决定，不含模型输出/会话状态/墙钟 | L/test_candidates.py::TestDeterminism::test_cursor_identity_covers_the_four_declared_terms、test_wall_clock_does_not_enter_the_result | covered |
| CC21 | 返回体含本次返回条数、总数与下一页游标；最后一页游标为空 | L/test_candidates.py::TestThreeStageShape::test_return_body_exposes_all_three_stages；L/test_rotation.py::test_empty_pool_keeps_cursor_and_returns_nothing | covered |
| CC22 | 逐页取尽得到全量有序候选的无重无漏子序列 | L/test_candidate_wiring.py::test_cursor_down_push_is_lossless_and_repeatable（空游标起反复下推所得条目标识集合恰等于该分区全量条目集合、无重复，同一游标重复请求返回同一段且与上一段零交集）、test_unknown_cursor_payload_is_rejected_not_silently_restarted；L/test_rotation.py::test_rotation_indices_wrap_forward_without_revisiting | covered |
| CC23 | 轮转判据是上次呈现时刻，不是互动次数或偏好估计量 | L/test_rotation.py::test_rotation_criterion_is_last_presented_at_not_exposure_count、test_never_presented_members_are_treated_as_longest_unpresented | covered |
| CC24 | 轮转非破坏性：只改呈现顺序与名额，不改目录、不改对象状态、不产生下架事件 | L/test_rotation.py::test_rotation_is_non_destructive_and_defers_without_delisting | covered |
| CC25 | 每次呈现按场景声明的换新比例留出固定名额给长期未呈现对象，其余按排序键 | L/test_rotation.py::test_quota_slots_derive_from_scenario_constants_never_hardcoded、test_quota_inversion_from_target_wait_round_trips | covered |
| CC26 | 最大未呈现间隔有界：闭式上界 `⌈N ÷ k⌉ ÷ D`，断言必须用精确形式而非连续松弛 `N ÷ (k × D)` | L/test_rotation.py::test_worst_case_unpresented_interval_is_bounded_by_exact_form、test_exact_bound_is_not_the_continuous_relaxation | covered |
| CC27 | 有效可达率计入：按有效决策点 `D × 可达率` 计算最坏等待，不按理论决策点 | L/test_rotation.py::test_reachability_discount_scales_worst_case_wait_proportionally、test_wait_bound_finding_reports_effective_decision_points | covered |
| CC28 | 轮转单位是检索分区而不是整个语料池，也不是地点分区 | L/test_rotation.py::test_subclass_cap_in_top_window、test_multi_subtype_pool_keeps_cap_and_fills_window、test_rotation_indices_wrap_forward_without_revisiting | covered |
| CC29 | 分区池规模 `N`、换新名额 `k`、每天决策点 `D` 的取值来源；`D` 取实测区间而非单一估计值 | `D` 的实测区间取 `docs/EXPERIMENT.md`「决策点节奏」节（中位 71、范围 9–112.5、保守取值 60）；断言 L/test_rotation.py::test_wait_bound_uses_the_measured_decision_point_interval（区间端点与保守取值都可用作 `D`，且上界恒等于 `⌈N ÷ k⌉ ÷ D`）、test_changing_the_decision_point_count_changes_the_wait_in_proportion（改 `D` 即改等待，多个 `D` 下最坏等待单调——不硬编码天数）、test_wait_bound_default_is_inside_the_measured_interval（缺省入参落在实测区间内，换端点结论随之改变）；`N` 与 `k` 的取值来源沿用 L/test_rotation.py::test_quota_slots_derive_from_scenario_constants_never_hardcoded、test_quota_inversion_from_target_wait_round_trips | covered |
| CC30 | 轮转状态「上次呈现时刻」是派生量，字段面与写入方式按合同 | L/test_candidate_wiring.py::test_observe_uses_candidate_contract_and_records_presentation（`WorldState.presentation_state` 承载「上次呈现时刻」，观察边界只暂存、随下一个状态提交落盘，键为参与本次呈现的对象标识）；实现见 `src/worldmock/life/models.py`（`WORLD_SCHEMA_VERSION = 5`、`presentation_state` 字段）与 `src/worldmock/life/state_commit.py::flush_presentation`（提交路径上的唯一写状态处）；`src/worldmock/life/rotation.py` 只读输入不回写 | covered |
| CC31 | 禁止按互动统计永久移除对象；任何以互动次数或被选择次数为判据移出对象层的机制不得实现 | L/test_rotation.py::test_rotation_criterion_is_last_presented_at_not_exposure_count | covered |
| CC32 | 目录条目退役只允许走墓碑语义（保留可读、拒绝新场景引用），不删除条目、不改既有足迹解释 | 拒绝新场景引用一侧：CAT/test_admission.py::test_tombstoned_object_reference_rejected（`TOMBSTONED_REFERENCE` 拒绝码，实现见 `src/worldmock/catalog/admission.py::_check_tombstones`）、CAT/test_builder.py::test_append_to_tombstoned_entry_rejected（墓碑条目禁止复活追加，换新 id 才合法）；「保留可读」一侧：CAT/test_builder.py::test_deprecate_writes_tombstone_and_keeps_entry_readable（打墓碑后条目行逐字节不变、条目仍在 `catalog.objects` 里）；两侧在候选契约里的合流断言 L/test_candidates.py::TestTombstoneSemantics::test_delisted_entry_is_excluded_from_candidates_and_still_readable（带墓碑的目录里，下架条目在候选里以 `ELIGIBILITY_DELISTED` 出局、同一时刻仍在目录里可读，保留项+排除项=候选全集）与 test_runtime_has_no_path_that_deletes_a_catalog_entry（AST 与文本扫描：契约层与生活层无 `unlink`/`rmtree`/`remove` 调用，也无「删除条目」「下架事件」这类事件名） | covered |
| CC33 | 轮转、候选裁剪与淘汰统计都不改对象层条目数量；条目数变化只由目录版本变更产生 | L/test_candidate_wiring.py::test_observe_uses_candidate_contract_and_records_presentation（历史基线写入呈现留痕后仍保留库存；现行供给身份需由本次 `supply_stock` 行为断言重新验收）、test_rotation_quota_actually_decides_presentation_order（轮转不改候选集身份）；L/test_rotation.py::test_rotation_is_non_destructive_and_defers_without_delisting（输入池逐字节不变） | covered |
| CC34 | 地点是可行性门：地点声明「满足哪些地点类」∩ 条目声明「需要哪些地点类」非空即可行 | L/test_retrieval.py::test_feasible_kinds_follows_anywhere_and_intersection（交集非空才可行、交集为空不可行）；L/test_candidates.py::TestExclusionDiscipline::test_world_fact_exclusions_carry_their_code（`location_kinds={"fitness"}` 对 `{"outdoor","venue"}` → `ELIGIBILITY_LOCATION_KIND_MISMATCH`）、test_missing_location_excludes_everything_with_a_stable_code；运行期强制 L/test_activity_location_kinds.py::test_authoritative_start_gate_uses_current_map_location、test_explicit_matching_activity_environment_is_accepted；恢复接线见 L/test_storefront_arrival.py::test_paused_lifestyle_cannot_resume_after_real_travel_to_incompatible_environment | covered |
| CC35 | 活动侧 `anywhere` 表示无地点限制；地点必须显式声明实际环境，不能使用 `anywhere` | L/test_candidates.py::TestExclusionDiscipline::test_anywhere_declaration_is_always_feasible；L/test_retrieval.py::test_feasible_kinds_follows_anywhere_and_intersection；L/test_activity_location_kinds.py::test_missing_or_mismatched_environment_is_not_an_anywhere_default；正式地图字段准入见 L/test_map_dataset.py::test_locations_reject_old_or_inferred_activity_environments | 新合同已通过地图特性完整验收 |
| CC36 | 检索分区键取条目属性（题材、语言、时长段），不取地点 | L/test_retrieval.py::test_build_index_postings_are_sorted_plain_text、test_search_filters_each_dimension_independently、test_facet_counts_reflect_current_filters（索引与 facet 的维度恰为题材、语言、时长段，地点类单独作为可行性维度而不进分区键）；CAT/test_reachability_admission.py::test_content_item_missing_partition_dimension_is_unreachable、test_content_item_missing_duration_dimension_is_unreachable、test_read_corpus_duration_falls_back_to_read_seconds（准入侧逐条列出分区键缺省条目并阻断启动） | covered |
| CC37 | `required_location_id` 与地点类判定并列，取交集即可行 | L/test_candidates.py::TestExclusionDiscipline::test_world_fact_exclusions_carry_their_code（`required_location_id="office"` → `ELIGIBILITY_LOCATION_ABSENT`）、test_business_hours_are_judged_at_the_entry_own_place（地点实例与营业时段并列判定） | covered |
| CC38 | 地点类词表逐值一致（目录、语料、准入三处不得漂移） | L/test_candidate_constants.py::test_candidate_constants_are_declared_in_every_life_scenario、test_candidate_constants_are_reachable_from_assembled_semantics | covered |
| CC39 | 索引必须确定性：倒排（标签 → 对象标识）与 facet 计数是纯文本、可 diff、可进溯源 | L/test_retrieval.py::test_build_index_postings_are_sorted_plain_text、test_index_holds_no_float_distance_field、test_search_returns_same_result_with_and_without_index、test_search_is_repeatable_and_cursor_free、test_reachability_report_is_plain_text_and_bytes_stable；实现见 `src/worldmock/life/retrieval.py::build_index` | covered |
| CC40 | 禁止向量检索与近似最近邻 | 常驻断言：L/test_retrieval.py::test_production_code_imports_no_vector_search_or_ann_library（逐文件扫描 `src/worldmock` 与 `custom`，导入图里不得出现 `faiss`/`chromadb`/`annoy`/`hnswlib`/`usearch`/`sentence_transformers` 等 ANN 库）、test_retrieval_structures_compare_no_float_distances（检索链路四模块不得出现 `cosine`/`euclidean`/`sqrt`/`softmax` 一类浮点距离调用，也不得导入 `random`/`numpy`/`scipy`/`sklearn`/`torch`/`math`；并逐字段核对索引只有纯文本倒排与整数计数）、test_worldmock_dependencies_declare_no_vector_search_library（本项目自己的依赖声明里无该词表；同时如实写明 vendored 上游 `vendor/agentsociety2` 自带的 `faiss-cpu` 只由上游 `env/router_codegen.py` 导入、本项目运行路径不经它检索，不把上游事实当作不存在）；纯文本距离侧沿用 L/test_retrieval.py::test_index_holds_no_float_distance_field | covered |
| CC41 | 反塌缩保证：任一对象在有限次查询组合下至少有一条到达路径（静态可检查，非覆盖率承诺） | L/test_retrieval.py::test_reachable_requires_partition_and_feasible_place、test_reachability_report_lists_unreachable_entries_and_partitions、test_partition_stays_feasible_when_one_member_is_feasible、test_reachability_does_not_promise_exposure、test_real_corpus_partitions_are_reachable；准入侧 CAT/test_reachability_admission.py::test_partition_with_no_feasible_place_is_unreachable、test_anywhere_partition_is_always_feasible；静态守卫 `scripts/check_reachability.py`（无 `--scenario` 时实测 `all 7106 entries reachable across 101 partition(s)`、退出码 0）；另见 L/test_rotation.py::test_rotation_indices_wrap_forward_without_revisiting（分区内不重访） | covered |
| CC42 | 触发条件：单一品类池规模超过场景阈值时才建倒排与 facet；未超过时按品类下推但查询入口与分区键先固定 | L/test_retrieval.py::test_build_index_path_follows_threshold（池未达阈值走 `SCANNED`、严格超过才走 `INDEXED`）、test_indexed_ids_is_none_when_scanned_and_narrows_when_indexed、test_search_returns_same_result_with_and_without_index（两条路径查询入口与分区键相同）；阈值常量由 L/test_candidate_constants.py::test_count_constants_have_an_upper_bound 守住 | covered |
| CC43 | 随身持有语义是「在当前位置的手边」，按地点嵌套；出门后不在手边；不引入随身背包、不新增「带出门」动作 | `tests/life/test_commerce.py::test_consume_rejects_inventory_held_at_another_location` 与 `test_browse_purchase_replay_and_remote_delivery_use_one_canonical_payment`（人物库存按地点嵌套，办公室不能消费留在家中的商品）；未新增背包状态 | covered |
| CC44 | 远程订购复用既有交付语义，不新增渠道对象；取货必须到商家地点、配送不校验在场，且须在工具 `description` 点明 | `tests/life/test_storefront_commerce.py::test_pickup_is_instant_and_never_needs_delivery_route` 与 `test_fulfillment_rejections_are_atomic`（取货不在场拒绝、配送按范围与道路条件受理）；`custom/agents/life_agent.py` 的 `purchase` 描述已按规格点明该分野 | covered |

脚注（复判时刻的实测命令与输出）：

```
uv run pytest -q tests/life/test_candidates.py tests/life/test_rotation.py \
  tests/life/test_candidate_constants.py tests/life/test_candidate_wiring.py \
  tests/life/test_retrieval.py tests/catalog/test_reachability_admission.py \
  tests/life/test_activity_location_kinds.py tests/test_documentation_gate.py
→ 165 passed in 5.74s
```

逐文件测试函数计数（`pytest --collect-only -q` 实测）：`test_candidates.py` 49、`test_rotation.py` 20、
`test_candidate_constants.py` 32、`test_candidate_wiring.py` 9、`test_retrieval.py` 31、
`test_reachability_admission.py` 9、`test_activity_location_kinds.py` 9、`test_documentation_gate.py` 6。

脚注（缺口清零时刻的实测命令与输出，2026-09-19 第三次增量）：

```
uv run pytest -q
→ 2747 passed, 24 warnings in 126.24s

uv run pytest -q tests/life/test_candidate_contract_boundaries.py \
  tests/life/test_candidates.py tests/life/test_retrieval.py tests/life/test_rotation.py \
  tests/life/test_candidate_wiring.py tests/life/test_candidate_constants.py \
  tests/life/test_activity_location_kinds.py tests/catalog/test_reachability_admission.py
→ 180 passed in 5.85s
```

补测后逐文件测试函数计数（`pytest --collect-only -q` 实测）：`test_candidate_contract_boundaries.py` 9（新建）、
`test_candidates.py` 52（+2：`CC35`、`CC32`）、`test_retrieval.py` 34（+2 新增、1 加固：`CC40`）、
`test_rotation.py` 26（+6：`CC29`）。

```
uv run python scripts/coverage_gate.py
→ [coverage-gate] branch coverage: 98.83% (covered=5851, missing=69, threshold=85.00)
→ [coverage-gate] uncovered public interfaces: 0
→ [coverage-gate] gate verdict: PASSED
uv run pytest -q tests/test_style_gate.py
→ 1 passed in 0.19s
python3 scripts/documentation_gate.py
→ [documentation-gate] scanned documents: 25
→ [documentation-gate] broken references: 0
→ [documentation-gate] gate verdict: PASSED
```

判据自验（补测时刻的变异验证，改完即还原生产文件）：在 `src/worldmock/life/retrieval.py` 顶部临时加入
`import faiss` 后 `test_production_code_imports_no_vector_search_or_ann_library` 失败、
`test_retrieval_structures_compare_no_float_distances` 在加入浮点 `cosine` 探针后失败，
两处还原后各自通过——这两条常驻断言不是「抄了条款文字」的空断言。

`grep -rn` 核实（复判时刻）：`life.candidates`/`life.rotation` 的调用点是
`src/worldmock/life/presentation.py`（`from worldmock.life import candidates, rotation`），
`presentation` 的调用点是 `src/worldmock/life/observe.py:15`，引擎侧经
`src/worldmock/life/engine.py:131` 的 `state_commit.flush_presentation` 写入留痕；
`life.retrieval` 的调用点是 `src/worldmock/catalog/admission.py:31`、`src/worldmock/life/activities.py:31`
与 `scripts/check_reachability.py:23`；
当时 `src/worldmock/life/models.py` 的版本为 3；当前世界版本为 5，`WorldState.presentation_state` 仍持久化，新增供给状态见增量矩阵。

本节不含 `exempt` 行：新增章的验证入口全部是离线确定性测试，无真实模型调用或长墙钟运行。
**未解决的另一类缺陷（如实记录，不随本节 `covered` 变化）**：`scripts/check_reachability.py --scenario`
对正式场景实测 **退出码 1**，报 `86 unreachable object(s)`，全部来自 `scenario:contents`，
`missing=['genre','language']`——其中 80 条标识以 `game-` 开头（`game_session` 品类），
另 6 条为 `jokes-feed`、`movie-reviews-feed`、`news-feed`、`sintel-en-subtitles`、`weibo-feed`、`zhihu-feed`。
不带 `--scenario` 时该脚本退出码 0（`all 7106 entries reachable across 101 partition(s)`）。
这是规格自己记的独立缺陷（场景 `contents[]` 条目缺分区键），**未解决**，与本章条款的测试映射判定无关。

## GAP 清单（5 条，均已修复并经终态独立复核）

复核方式：逐个亲读修复后测试函数体（不凭修复报告转述），核对断言真实验证条款；五个文件
`uv run pytest -q tests/life/test_travel_trajectory.py tests/footprints/test_event_fields.py
tests/delivery/test_windows.py tests/evaluation/test_review.py tests/test_style_gate.py`
于 2026-09-18 复跑实测 **1 failed, 56 passed**：GAP-1…GAP-4 的四个文件全部通过，
`tests/test_style_gate.py::test_style_gate_admits_no_new_violations` 失败，报 `src/worldmock/life/` 下
felt、observe、projection、rhythm、scenario、schedule 六个文件共 9 处新增 WIDTH 违规（注释与文档字符串
改为按「文件名 + 章节标题名」引用后行宽超过 120）。该失败来自同一工作树内并行进行的注释改造，
不改变 GAP-5 的门禁实现本身，也不涉及本矩阵的覆盖判定。

| GAP | 条款摘要 | 修复后核实证据（亲读断言） | 命中测试 |
| --- | --- | --- | --- |
| GAP-1 | 导航：可沿实际路线+模拟时间生成定位轨迹，保存路线与采样规则；定位采样不是人物决策 | C23 落地 src/worldmock/life/travel.py sample_location_track；断言同一输入两次投影逐字节相同、采样点到冻结 LineString 几何球面距离<1m、时间戳严格递增且落出发/到达之间、事件为 environment_event(kind=location_track) 且 decision_id/action_id 均为 None 与 source_kind=environment、data.sampling_rule（方法/间隔/时间窗/起讫点）与 route_id/response_path/response_sha256 引用齐备、非正时间窗抛 LOCATION_TRACK_INVALID 与哈希漂移抛 IntegrityError、间隔覆盖整段行程时零事件 | tests/life/test_travel_trajectory.py（7 测，test_track_projection_is_not_a_person_decision 等） |
| GAP-2 | 事件类型冻结枚举（14 类）全集无专测 | 修复波落地；_SPEC_EVENT_TYPES 为 `docs/design/05-recording-engine.md` 事件类型枚举逐字 14 类元组（observation…run_failed），断言 tuple(EVENT_TYPES)==_SPEC_EVENT_TYPES 且 len==去重==14；反例 "agent_thought" 构造 EventDraft 即 ValueError | tests/footprints/test_event_fields.py::test_event_type_vocabulary_is_the_frozen_spec_set、test_event_type_rejects_anything_outside_the_frozen_set |
| GAP-3 | dreaming 窗口可含 decision_statement 与 scene（决策自述仅 dreaming 可读） | 修复波落地 src/worldmock/delivery/windows.py 的 DREAMING_PRIVATE_EVENTS=PRIVATE_EVENTS−{decision_started}；正例：dreaming 窗口 rows 含 decision_started.data.decision_statement 逐字与 observation scene 文本；反例：动作/意图窗口全部 rows 无 decision_started 事件、序列化字符串不含 decision_statement，且断言 'decision_started' in PRIVATE_EVENTS 维持动作侧排除 | tests/delivery/test_windows.py::test_dreaming_window_reads_decision_statement_and_scene、test_action_and_intent_windows_still_exclude_the_decision_statement_carrier |
| GAP-4 | 盲评包与导出的活动记录增加地点与同伴两列（同伴匿名引用） | 修复波落地 src/worldmock/evaluation/review.py 的 _timeline_row（location 列 + companion 列经 _companion_ref 匿名化）；断言携带 location 的会话出现在盲评 timeline、行 location==office-west、companion 匹配 object-[0-9a-f]{64} 且原文 "agent:2" 不出现在整个序列化包中；评审槽间 HMAC 键不同（slot 隔离）、同槽同对象跨日稳定；行集断言恰为 {category,start,end,status,location,companion} | tests/evaluation/test_review.py::test_activity_rows_carry_location_and_anonymous_companion、test_companion_reference_is_slot_isolated_and_day_stable |
| GAP-5 | 生产代码硬规范（中文 Doxygen、英文日志、≤120 字符、函数≤50 行≤5 参、文件≤2000 行）无自动化门禁 | 修复波落地常驻棘轮门禁：ast 扫 src/worldmock+custom 全部生产 .py，行宽>120、函数>50 行、入参>5（self/cls 不计）、文件>2000 行、docstring 无中文各记违规；实际违规必须逐文件×种类等于 _OUTSTANDING 冻结清单——新增即红、修复后未缩减清单同样红（防腐化） | tests/test_style_gate.py::test_style_gate_admits_no_new_violations |

## 已知豁免（14 行，均为规格验证矩阵"实际验证入口"为真实模型调用/真实外部服务/长墙钟真实运行验收的条款——确定性部分各附锚测试，豁免不算缺口）

| EX | 关联条款 | 豁免内容 | 确定性锚测试 |
| --- | --- | --- | --- |
| EX-1 | DEV9 | prepare 阶段 OSRM/Open Food Facts 在线取数 | CO/test_corpora.py::test_routes_are_frozen_real_responses_for_all_declared_pairs（冻结响应读取侧全验） |
| EX-2 | DEV1, GT6 | 真实 DeepSeek 十二周（84 天墙钟）运行 | L/test_scenario.py 场景冻结；E/test_metrics.py::test_formal_window_uses_natural_week_blocks_and_ten_thousand_resamples；F/test_export.py 完成性校验 |
| EX-3 | DEV4, GT3 | 真实 DeepSeek 端到端调用链 | I/test_runtime.py::test_model_identity_freezes_complete_strict_endpoint_and_request；L/test_work_review.py::test_frozen_real_deepseek_responses_accept_paraphrase_and_reject_hallucination |
| EX-4 | DEV16, DL27 | 真实 LabelKit CLI 运行及其 HTTP timeout_s/malformed 行为（vendor/labelkit 独立环境冻结版本承载） | D/test_labelkit_process.py（trace 合同 13 测）；D/test_publication.py::test_failed_semantic_gate_never_publishes_delivery |
| EX-5 | EV9, GT7 | 真实盲评（三个隔离模型评审会话/真人评审记录） | E/test_review.py::test_review_ingest_unblinds_after_all_records_and_computes_alpha、test_blind_packets_are_deterministic_and_hide_source_identity |
| EX-6 | GT5 | Uncle Bob 源码突变审查（流程门禁，证据在 implementation-status.md） | 全部 1584 个测试（审计期计数，口径见文末覆盖边界）为突变杀伤基线 |
| EX-7 | GT8 | 双人 24h×3 同代码协议门禁真实运行 | E/test_behavior_metrics.py 四项协议门禁（观察重复率=1、静默占比≥0.5、记忆重复率≤0.2、MEMORY.md 身份残留=0） |
| EX-8 | RC65 | run-viewer.html"一天"视图（规格自指定手工核对） | F/test_export.py::test_export_timeline_projection_matches_render_day_output |
| EX-9 | DL13 | 真实模型评审完整自然日输入编码 | D/test_evidence_encoding.py（逐字节往返 11 测）；D/test_training.py 语义门禁 |
| EX-10 | DL14 | LabelKit 项目 token 容量/输出上限具体数值（1M/131072/65536，LabelKit 项目配置承载） | D/test_semantic_schema.py::test_all_process_roles_share_one_identical_verify_contract、test_default_output_limit_is_a_cap_for_bounded_semantic_summary |

（exempt 行与 covered 行中的"锚"测试已在各节矩阵标注；豁免不算缺口，但每项均注记确定性部分。）

## 抽样自验（20/20）

方法：从矩阵各节抽 20 条 covered 映射，逐个打开测试函数体，核对其真实验证该条款（防名字相似错配）。
结果 **20 吻合 / 0 错配 / 0 需重映射**。样本与关键断言：

1. D7 → F/test_store.py::test_complete_checkpoint_branch_preserves_parent_and_exact_workspace：父目录 restore 前后逐字节对比；子分支恢复的是检查点时刻记忆而非检查点后未提交记忆 ✓
2. A9 → V/test_llm_callback_lifecycle.py::test_more_than_one_hundred_task_loops_leave_no_router_callbacks：真实 LLMClient/litellm 105 次循环后回调列表==基线、无上限警告 ✓
3. E19 → L/test_activities.py::test_continue_activity_moves_feedback_strictly_forward_without_releasing_slot：next_interaction 900→1800 严格前移、槽位不释放 ✓
4. E24 → I/test_grounded_targets.py::test_plan_day_open_without_plan_and_restricted_at_day_start：当日无计划即开放（补交不限唤醒原因）、日首无计划动作面只留 plan_day、枚举==INTENT_KINDS ✓
5. R8 → V/test_agent_react_validation.py::test_protocol_errors_consume_exact_global_turn_budget：两次非法响应用尽 2 回合即失败、无预算外请求 ✓
6. C11 → L/test_work_contracts.py::test_hourly_and_piece_contracts_use_actual_time_and_frozen_piece_count：计时按实际秒/计件按冻结件数、双方账户对转 ✓
7. T4 → L/test_engine_feedback.py::test_tenth_consecutive_rejection_fails_agent_and_run：第 10 次拒绝→failed+run_failed 事件、不代选活动 ✓
8. W5 → L/test_work_review.py::test_frozen_real_deepseek_responses_accept_paraphrase_and_reject_hallucination：冻结真实 DeepSeek 响应经当前解析器改写接受/幻觉拒绝 ✓
9. F2 → F/test_store.py::test_credentials_are_redacted_without_losing_model_task：API key 全文不出现在证据 JSON、任务消息保留 ✓
10. CP2 → L/test_state_serialization.py::test_readonly_commit_is_identical_across_python_hash_seeds：两个不同 PYTHONHASHSEED 子进程提交字节相同 ✓
11. LK9 → D/test_labelkit_process.py::test_real_labelkit_annotate_and_verify_requests_are_target_blind：annotate/verify 请求构造、返工轮完整历史 ✓
12. DL7 → D/test_training.py::test_real_dataset_loading_preserves_all_facts_and_separates_answer：json.loads 逐字段还原含 null/浮点、prompt/completion 映射、features 断言 ✓
13. EV4 → E/test_review.py::test_object_references_are_candidate_and_reviewer_scoped_but_stable_across_days：左右候选/评审会话引用互异、私有 ID 不入包、交换不变 ✓
14. GT8 锚 → E/test_behavior_metrics.py::test_silent_boundary_ratio_exact_values：0.6 过/0.4 败、sleep 边界不入清醒分母 ✓
15. RC6 → L/test_sleep_two_process.py::test_awake_step_matches_raw_formula：S(t+dt)=1−(1−S)·exp(−dt/18.2) 逐点核对 rel=1e-9 ✓
16. RC14 → L/test_felt_states.py::test_no_change_when_decision_reference_matches：滞回参照是决策时分级而非结算中间值 ✓
17. RC23 → I/test_life_agent_protocol.py::test_system_prompt_is_identity_first_with_word_share："你不是AI助手"在、评审/评分/引擎期望禁词断言、身份词占比≥0.5 ✓
18. RC36 → L/test_motivation.py::test_meal_floor_and_top_three：≥300kcal 门槛+缺口接近度前三、零食出局 ✓
19. RC42 → L/test_social.py::test_chat_settlement_rates_for_accept_and_decline：接受 0.5/0.5、拒绝 0.2/0.0 ✓
20. RC51 → CAT/test_category_admission.py::test_meals_below_contract_minimum：目录篡改至 3 条正餐即拒绝启动 ✓

## 覆盖边界

- 已扫描并映射：迁移前的设计单体 21 个条款组（含《人物真实性修订合同》全部功能小节），本矩阵各节首句逐节标注对应章节，
  其内容分布于 `docs/ARCHITECTURE.md`、`docs/CONTRACTS.md`、`docs/EXPERIMENT.md`、`docs/VERIFICATION.md`、
  `docs/INDEX.md` 与 `docs/dev/SPEC-*.md`；21 个组标注的章节名在现行文档树中全部可解析。承载为章节级而非条款级：
  部分条款在规范文档中没有逐条对应的承载章节，只在本矩阵有行，这些行的覆盖判定只以测试证据为准；`tests/` 九个子目录与根、
  `vendor/agentsociety2/packages/agentsociety2/tests/` 全部（含 framework/skills；其中与 WorldMock 无关的
  上游框架测试未参与映射，仅 AS2 改动清单相关者被引用）。
- 未映射目录：`tests/scheduling/`（`worldmock.scheduling` 的分支覆盖补测）。本矩阵不改写扫描范围的原始记录，
  也不把审计后新增的目录计入条款映射。
- 未评估：`tests/` 之外的手工核对（run-viewer）、`docs/dev/implementation-status.md` 记录的真实运行收据真伪、
  分支覆盖率（另有 coverage gate 承载）。
- 未逐字断言：旧数据身份常量（`E/test_adapters.py` 使用真实 repaired archive，但未逐字断言 run 标识与行数）。
- 硬规范承载：生产代码硬规范条款（中文 Doxygen 注释、英文日志、行宽上限、函数与文件规模上限）由
  `docs/VERIFICATION.md` 的「放行门禁」章「工程门禁」节中加粗的源码硬规范条目承载；本矩阵该行同时映射
  `tests/test_style_gate.py` 的实现证据。
- 测试函数总数口径：本矩阵采用 `tests/` 全量测试函数口径，2026-09-18 历史基线实测 **1789**（同步 1718 + 异步 71）；
  `grep -rE "^\s*(async )?def test_" tests/` 与 AST 解析两种独立方法结果一致。计数单位为测试函数定义
  （含 `async def`），不按函数名去重（`sort -u` 口径为 1788，差 1 来自 `tests/life/test_sleep_two_process.py`
  中同名测试函数定义出现两次）。审计期记录的 1584（同步 1458 + 异步 126）扫描范围未记录、在当前树上不可复现，
  GT5 与 EX-6 两行引用的即该审计期计数。
- 键控方式：本矩阵按现行文档的章节标题名键控，不使用行号引用。
- **口径变化（2026-09-19 增量 + 同日复判 + 同日缺口清零）**：条款总数由 391 重算为 435；覆盖率随之
  先后取三个值——增量落地时 **96.1%**（418/435）、该章复判后 **98.4%**（428/435 = covered 414 + exempt 14）、
  第三次增量补测清零七条缺口后 **100.0%**（435/435 = covered 421 + exempt 14）。
  （**行内修订**：本节原写「98.2%（427/435 = covered 413 + exempt 14）」，其中的 413 与本文件顶部增量段、
  统计表与矩阵表逐行口径均不符，属换算笔误，已改为与矩阵表一致的 414 → 421 序列。）
  数字反复变动不是因为既有映射被推翻，而是口径本身变了两次。
  一是**覆盖对象扩容**：本轮新增的 `docs/dev/SPEC-life-runtime.md`「候选契约与呈现规则」章与
  `docs/dev/SPEC-life-realism.md` 四节此前零映射，现补入 44 行（全部为新增，无一行覆盖既有条款）。
  二是**判定前提改写**：新增章的条款在最初审计时刻只有纯函数层证据（`src/worldmock/life/candidates.py`
  与 `src/worldmock/life/rotation.py`，`tests/life/` 内三个文件共 99 个测试函数判定），故当时 27 行 `covered`、
  17 行 `gap`；**本轮该章整章落地后复判**：新增了运行期装配层 `src/worldmock/life/presentation.py`
  （`observe.py` 的观察投影与 `browse_activities` 下推均经它产出）、确定性检索与分区可达性
  `src/worldmock/life/retrieval.py`、准入侧两个新错误码（`src/worldmock/catalog/admission.py`）、
  地点类运行期强制（`src/worldmock/life/activities.py`），以及持久化字段
  `WorldState.presentation_state` 与 `WORLD_SCHEMA_VERSION = 3`，于是原 17 行 `gap` 中 9 行改判 `covered`
  （`CC16`、`CC22`、`CC30`、`CC33`、`CC36`、`CC39`、`CC41`、`CC42`，另 `CC34` 的映射扩容到运行期强制侧），
  余 8 行仍记 `gap`。**本矩阵不把纯函数层证据当作端到端条款的证据**：`covered` 只声明「该条款有真实存在的
  测试函数验证其可断言部分」，运行期落地与否由 `docs/dev/implementation-status.md` 记录，不以本矩阵为据。
  复判时刻的逐文件测试计数与门禁输出见该章脚注。
- **复判时仍记 gap 的行及其原因（现已全部清零）**：`CC7` 跨运行分支一致性（无对应断言）、`CC17` 动机层
  `MAX_CANDIDATES` 不作用于呈现（无非作用断言）、`CC18` 下推层数上限两层（形状有测、层数上限本身无断言）、
  `CC29` `D` 取值的实测区间在主文档集内无解析来源、`CC32` 退役条目「保留可读」一侧无断言、
  `CC35` `anywhere` 恒可行（三处实现均有该分支，但无测试把 `anywhere` 侧与另两侧区分开，
  且当时地点语料的 `location_kinds` 声明为空，运行期恒走未声明缺省）、`CC40` 禁止向量检索（零实现但未写成常驻断言）、
  `CC43` 随身持有的地点嵌套（`AgentState.inventory` 仍按人持有）、`CC44` 远程订购的取货/配送在场语义分野
  （既有 `PICKUP_LOCATION_REQUIRED` 不覆盖「配送不校验在场」一侧）。这些行的验证入口都是仓库内确定性断言，
  因此当时**按 `gap` 处理而非按豁免处理**；`CC43`、`CC44` 在第二轮复判改判 `covered`，
  其余七行在 2026-09-19 第三次增量补测后改判 `covered`（逐行判据见上）。
- **本轮的独立缺陷记录（不计入本矩阵覆盖判定）**：`scripts/check_reachability.py --scenario` 对正式场景
  实测退出码 1，86 条场景内容条目落不进任何分区（80 条 `game_session`），详见该章脚注。
  这是当时记录的另一类独立缺陷；地图分支修复与实测见前述增量映射，本段不描述当前状态。

## 版本表

| 版本 | 日期 | 修订内容 |
| --- | --- | --- |
| 迁移 | 2026-09-18 | 由 `docs/spec-coverage.md` 迁入 `docs/dev/`：键控从行号（`Lxx-yy` 与 `文件:行号`）改为现行文档的章节标题名，行号引用全部移除；各节覆盖对象逐节改写为现行文档树的章节名 |
| 迁移复核 | 2026-09-18 | 重测测试函数总数 1789（同步 1718 + 异步 71）；GAP 关联五个文件复跑 1 failed / 56 passed；GAP-1…GAP-5 逐个亲读断言核实为 covered |
| 口径修订 | 2026-09-18 | 测试函数总数改用复核实测值 1789；审计期记录的 1584（同步 1458 + 异步 126）扫描范围未记录、当前树不可复现，不再沿用 |
| 引用修订 | 2026-09-18 | 悬空引用改为标题级承载：失败传播判定口径改指 `docs/VERIFICATION.md` 的「工程门禁」节并明示其为该节加粗条目，同法处理源码硬规范两处；放行门禁覆盖对象的子节枚举逐项加「」并解析到真实标题；统计图例、R7、F12、DEV4、DEV5、DEV41、RC54 与 EX-9 的条款表述改为本矩阵措辞，消除与其他文档的逐字重合 |
| 新增章映射 | 2026-09-19 | 补入「候选契约与呈现规则」章 44 行（`CC1`…`CC44`），覆盖 `docs/dev/SPEC-life-runtime.md`「候选契约与呈现规则」章与 `docs/dev/SPEC-life-realism.md` 的「`location_kinds` 的运行期强制语义」「素材可达性」「素材淘汰与检索机制的禁止项」「候选与呈现」四节；条款总数由 391 重算为 435，覆盖率由 100.0%（391/391）重算为 96.1%（418/435）；新增行中 27 行按纯函数层测试记 `covered`（`tests/life/test_candidates.py`、`test_rotation.py`、`test_candidate_constants.py`，99 测实测全绿），17 行因运行期接线与持久化未落地记 `gap`；`CC12` 一行映射 `tests/life/test_motivation.py` 的两个既有测试（实测 2 passed） |
| 新增章复判 | 2026-09-19 | 该章整章落地后逐行复判 44 行：`CC16`、`CC22`、`CC30`、`CC33`、`CC36`、`CC39`、`CC41`、`CC42` 由 `gap` 改判 `covered`（运行期装配 `src/worldmock/life/presentation.py`、检索与可达性 `src/worldmock/life/retrieval.py`、准入侧分区校验 `src/worldmock/catalog/admission.py`、地点类运行期强制 `src/worldmock/life/activities.py`、持久化 `WorldState.presentation_state` 与 `WORLD_SCHEMA_VERSION = 3`），`CC34` 映射扩容到运行期强制侧；仍余 7 行 `gap`（`CC7`、`CC17`、`CC18`、`CC29`、`CC32`、`CC35`、`CC40`）；`CC43` 与 `CC44` 经复判由 `gap` 改判 `covered`，逐条原因见该章。条款总数 435 不变，covered 由 404 重算为 **414**，gap 由 17 收窄为 **7**，覆盖率由 96.1% 重算为 **98.4%**（428/435 = covered 414 + exempt 14）。复判证据：八个测试文件合计 `165 passed`（`test_candidates.py` 49、`test_rotation.py` 20、`test_candidate_constants.py` 32、`test_candidate_wiring.py` 9、`test_retrieval.py` 31、`test_reachability_admission.py` 9、`test_activity_location_kinds.py` 9、`test_documentation_gate.py` 6）；`coverage_gate.py` 分支覆盖 98.83%、未覆盖公共接口 0、`gate verdict: PASSED`；`documentation_gate.py` 扫描 25 份文档、0 处断链、`gate verdict: PASSED`。**行内修订**：`CC32` 原映射引用 `ELIGIBILITY_RETIRED`，该常量在 `src/worldmock/life/candidates.py` 的 `EXCLUSION_REASONS` 闭集内并不存在，属名实不符，已据实改述并保留 `gap`；另如实记录一项与本矩阵无关的独立缺陷：`scripts/check_reachability.py --scenario` 对正式场景退出码 1（86 条场景内容条目落不进分区），未解决 |
| 缺口清零 | 2026-09-19 | 为「候选契约与呈现规则」章余下的 7 行 `gap`（`CC7`、`CC17`、`CC18`、`CC29`、`CC32`、`CC35`、`CC40`）逐行补入真实性测试并改判 `covered`，**未改动任何生产代码**。新增与加固共 19 个测试函数：新建 `tests/life/test_candidate_contract_boundaries.py`（9 测，经生产接口建普通分支比对候选集、呈现上限不取 `MAX_CANDIDATES`、下推两层与游标载荷形状），`tests/life/test_candidates.py` 增 2 测（`anywhere` 三形区分、墓碑目录里下架条目出局且仍可读 + 无删除条目的通路扫描），`tests/life/test_retrieval.py` 增 2 测并加固 1 测（禁止 ANN 库与浮点距离的常驻断言），`tests/life/test_rotation.py` 增 6 测（`D` 取实测区间而非单一估计值，不硬编码天数）。条款总数 435 不变，covered 由 414 重算为 **421**，gap 由 7 收窄为 **0**，覆盖率由 98.4% 重算为 **100.0%**（435/435 = covered 421 + exempt 14）。实测证据：全量 `2747 passed`；相关八个测试文件 `180 passed`；`coverage_gate.py` 分支覆盖 98.83%、未覆盖公共接口 0、`gate verdict: PASSED`；`test_style_gate.py` 1 passed；`documentation_gate.py` 扫描 25 份文档、0 处断链、`gate verdict: PASSED`。**行内修订**：正文原写「复判后仍余九行 `gap`」，与矩阵表的去重终态（8 行，`CC34` 已改判）不符，已按表改正。**如实记录的边界**：`CC29` 断言核对的是「缺省值落在实测区间内」与「上界随 `D` 精确变化」，不钉死某个特定缺省数值；`CC40` 的依赖侧断言范围是本项目自己的依赖声明，vendored 上游 `vendor/agentsociety2` 自带的 `faiss-cpu`（只由上游未使用的 `env/router_codegen.py` 导入）作为已知上游事实写明，本项目运行路径不经它检索 |

## 当日目标特性追溯

[当日目标规格](SPEC-day-plan-goals.md)取代旧时段约束；
本特性的完整行为、文件清单、测试和独立变异证据统一见
[本特性验收记录](research/day-plan-goals-verification.md)，不沿用旧段测试计数作为新功能证据。
