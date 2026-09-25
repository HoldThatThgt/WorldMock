"""时间与能量规格的验收登记表（完整性守卫的数据源）。

@file     time_and_energy_acceptance.py
@brief    把 docs/dev/SPEC-time-and-energy.md「验收台账」的每一行登记到其归属阶段与证据测试，
          并登记各阶段必须消失（REMOVED_SYMBOLS）与必须出现（REQUIRED_SYMBOLS）的代码与文档符号；
          判定由 tests/test_time_and_energy_completeness.py 执行。
@note     `stage` 取「完成后整行才可完整验证」的阶段，即台账行最后一个有贡献的阶段；
          贡献阶段可以并行合入时（如阶段 6 与阶段 1–5），取依赖图中依赖其全部贡献阶段的首个阶段；
          更早的阶段可以预先登记已经存在的测试。只有 `stage` 属于 STAGES_DONE 的行必须列出测试，
          但任何行列出的测试都必须能解析到存在且未被跳过的测试函数。
          符号表语义：REQUIRED 的正则须在每个列出的路径中命中（目录下任一文件命中即算）；
          REMOVED 的正则在列出路径下的任何文件中都不得命中。
"""

from __future__ import annotations

#: 实施计划「实施阶段」中的全部阶段编号。
STAGE_IDS = frozenset(str(n) for n in range(10))

#: 已完成的阶段；每个阶段收尾时把自己的编号加入。
STAGES_DONE = frozenset({"0", "2"})

_GREEN_BASELINE_TESTS = [
    "tests/life/test_dayrun_inventory_regressions.py::"
    "test_meal_partitions_reach_zero_without_losing_quantity_or_energy",
    "tests/integration/test_dayrun_complete_days.py::"
    "test_complete_two_person_day_uses_real_tools_memory_and_restoration",
    "tests/integration/test_agent_branch_coverage.py::test_empty_interactive_layer_binds_no_empty_enum",
    "tests/integration/test_agent_branch_coverage.py::test_empty_interactive_layer_decision_continues_without_failure",
    "tests/life/test_grounded_observation.py::test_real_observation_during_active_activity_keeps_decision_open",
    "tests/life/test_session_state.py::test_world_schema_version_is_7",
    "tests/life/test_session_state.py::test_world_state_schema_version_6_is_rejected",
    "tests/life/test_session_state.py::test_run_with_version_6_world_state_is_rejected_on_open",
    "tests/footprints/test_verified_experiences.py::test_arrival_snapshot_at_origin_still_projects_arrived",
    "tests/life/test_travel_time.py::test_travel_progress_snapshot_keeps_origin_and_arrival_is_verified",
    "tests/life/test_day_plan.py::test_recall_task_summary_by_goal_name_round_trips",
    "tests/life/test_work_review.py::test_submitting_ready_task_while_working_on_another_reviews_and_pays",
    "tests/life/test_work_review.py::test_submitting_task_in_progress_is_rejected_with_work_task_in_progress",
    "tests/life/test_work_review.py::test_incomplete_work_skips_model_review_and_is_business_rejection",
    "tests/integration/test_life_agent_protocol.py::test_incomplete_work_stays_submittable_for_business_rejection",
    "tests/life/test_engine_branch_coverage.py::test_prepare_pending_work_review_during_independent_wait",
    "tests/life/test_engine_branch_coverage.py::test_submit_work_guards_review_presence_and_identity",
]

_DELIVERY_TESTS = [
    "tests/delivery/test_publication.py::"
    "test_delivery_rejects_export_whose_final_checkpoint_has_another_world_schema_version",
    "tests/delivery/test_publication.py::test_delivery_checks_world_schema_before_preparing_windows",
]

#: 阶段 2 为「候选时刻」行登记的预测部分：困意与饥饿跨档扫描（升档、降档）、餐时窗口起止、100 kcal 保底档；
#: 重估候选与空闲间隔由阶段 1 登记在同一行。
_STAGE2_CANDIDATE_TIME_TESTS = [
    "tests/life/test_sleep_two_process.py::TestSleepinessCrossingPrediction::"
    "test_prediction_from_current_pressure_is_not_later_than_actual",
    "tests/life/test_activity_effects.py::test_hunger_upward_crossing_is_not_later_than_actual",
    "tests/life/test_activity_effects.py::test_hunger_downward_crossing_at_meal_window_end",
    "tests/life/test_activity_effects.py::test_body_boundary_uses_curve_node_and_food_net_energy",
    "tests/life/test_activity_effects.py::test_meal_window_edges_are_boundary_candidates",
    "tests/life/test_activity_effects.py::test_chat_and_call_are_predicted_but_sleep_and_game_are_not",
    "tests/life/test_engine_branch_coverage.py::test_next_body_boundary_skips_zero_rates_when_nothing_due",
]

#: 阶段 2 为「唤醒词表」行登记：activity_infeasible 不再产生，commitment_threatened 只来自临近截止与临近闭店；
#: 新成员与写入校验由阶段 1 登记在同一行。
_STAGE2_WAKE_VOCABULARY_TESTS = [
    "tests/life/test_wake_reasons.py::test_frozen_wake_and_intent_names",
    "tests/life/test_wake_reasons.py::test_activity_infeasible_has_no_source_and_threat_only_from_deadline_or_closing",
    "tests/life/test_wake_reasons.py::test_zero_energy_during_activity_raises_neither_infeasible_nor_threat",
    "tests/life/test_wake_reasons.py::test_engine_schedules_exact_task_warning_and_appends_threat_reason",
    "tests/life/test_wake_reasons.py::test_engine_schedules_closing_warning_only_for_local_task",
]

#: 阶段 2 完成的「能量感知」行：两档低能量饥饿、阈值次序校验、提示不含身体数值、食品热量与路线费力标签。
_STAGE2_ENERGY_PERCEPTION_TESTS = [
    "tests/life/test_felt_states.py::TestLowEnergyAppetite::test_below_emergency_threshold_is_at_least_unbearable",
    "tests/life/test_felt_states.py::TestLowEnergyAppetite::test_below_starving_constant_is_starving",
    "tests/life/test_felt_states.py::TestLowEnergyAppetite::test_emergency_floor_ignores_meal_and_circadian_terms",
    "tests/life/test_felt_states.py::TestLowEnergyAppetite::test_starving_is_not_an_appetite_band",
    "tests/life/test_felt_states.py::TestLowEnergyAppetite::test_summary_applies_the_same_rules",
    "tests/life/test_engine_branch_coverage.py::test_body_projection_renders_five_band_texts_and_numeric_values",
    "tests/life/test_semantics.py::test_energy_thresholds_come_from_catalog_alert_rules",
    "tests/life/test_felt_states.py::test_body_config_rejects_emergency_threshold_not_above_starving",
    "tests/life/test_felt_states.py::test_scenario_with_emergency_threshold_at_starving_constant_fails_to_load",
    "tests/life/test_wake_reasons.py::test_low_energy_bands_match_the_decision_reference",
    "tests/life/test_public_observation_contract.py::test_low_energy_prompt_carries_levels_and_labels_but_no_body_numbers",
    "tests/life/test_public_observation_contract.py::test_food_energy_label_rounds_half_up_to_ten_kcal",
    "tests/life/test_public_observation_contract.py::test_products_and_inventory_show_food_energy_and_elsewhere_holdings",
    "tests/life/test_public_observation_contract.py::test_route_effort_bands_follow_travel_met",
    "tests/life/test_public_observation_contract.py::"
    "test_routes_carry_effort_and_feasible_means_reachable_at_zero_energy",
    "tests/life/test_energy_feasibility.py::test_routes_are_feasible_by_reachability_not_energy",
]

#: 阶段 2 对「能量不拦人」行（阶段 4 收尾：目录删 energy_projection）的预先登记。
_STAGE2_ENERGY_NEVER_BLOCKS_TESTS = [
    "tests/life/test_energy_feasibility.py::test_zero_energy_start_is_accepted",
    "tests/life/test_energy_feasibility.py::test_derived_duration_beyond_energy_is_accepted_and_settles_at_zero",
    "tests/life/test_energy_feasibility.py::test_zero_energy_travel_and_work_are_accepted",
    "tests/life/test_energy_feasibility.py::test_zero_energy_resume_is_accepted",
    "tests/life/test_energy_feasibility.py::test_checkpoint_with_overdrawn_activity_restores",
    "tests/life/test_energy_feasibility.py::test_hunger_band_crossing_wakes_during_activity",
    "tests/life/test_energy_feasibility.py::test_hunger_band_crossing_wakes_during_chat",
    "tests/life/test_energy_feasibility.py::test_energy_gate_symbols_are_gone_from_production_code",
    "tests/life/test_energy_feasibility.py::test_motivation_internal_still_weighs_energy",
    "tests/life/test_body.py::test_every_activity_truncates_at_zero",
    "tests/life/test_dayrun_restaurants.py::test_food_path_takes_the_first_path_that_holds",
    "tests/life/test_dayrun_restaurants.py::test_late_delivery_adds_the_first_nearer_alternative",
    "tests/life/test_dayrun_restaurants.py::test_below_hungry_keeps_the_local_meal_sentence",
    "tests/life/test_dayrun_restaurants.py::test_scene_food_path_quotes_only_this_observation",
    "tests/life/test_affordance_contract.py::test_contract_shape_matches_registry_one_to_one",
]

#: 阶段 2 对「本机运行修复」行（阶段 7 收尾）的预先登记：00:00 起点的困意与人读配送时刻。
_STAGE2_LOCAL_RUN_FIX_TESTS = [
    "tests/life/test_sleep_two_process.py::TestInitialBodyFromUsualWake::"
    "test_lianqiu_midnight_start_first_observation_is_at_least_sleepy",
    "tests/life/test_sleep_two_process.py::TestInitialBodyFromUsualWake::"
    "test_pressure_counts_awake_hours_since_the_most_recent_usual_wake",
    "tests/life/test_sleep_two_process.py::TestInitialBodyFromUsualWake::test_usual_wake_hour_wraps_modulo_24",
    "tests/life/test_dayrun_restaurants.py::test_delivery_eta_is_readable_and_matches_actual_delivery",
    "tests/life/test_dayrun_restaurants.py::test_delivery_is_unavailable_here_without_destination_or_route",
]

#: 阶段 2 对「无新增故障」行（阶段 7 收尾）的预先登记：能量为 0 与路线费力键渲染。
_STAGE2_NO_NEW_FAULT_TESTS = [
    "tests/life/test_energy_feasibility.py::test_zero_energy_observation_offers_every_action_without_energy_projection",
    "tests/life/test_narration.py::TestFieldBlacklist::test_route_whitelist_includes_effort_label",
]

#: 验收台账行（首列原文）→ 归属阶段与证据测试节点。
ACCEPTANCE: dict[str, dict[str, object]] = {
    "绿色基线": {"stage": "0", "tests": _GREEN_BASELINE_TESTS},
    "同刻决策": {"stage": "4", "tests": []},
    "候选时刻": {"stage": "2", "tests": _STAGE2_CANDIDATE_TIME_TESTS},
    "唤醒词表": {"stage": "2", "tests": _STAGE2_WAKE_VOCABULARY_TESTS},
    "能量感知": {"stage": "2", "tests": _STAGE2_ENERGY_PERCEPTION_TESTS},
    "能量不拦人": {"stage": "4", "tests": _STAGE2_ENERGY_NEVER_BLOCKS_TESTS},
    "零点计划与提醒": {"stage": "5", "tests": []},
    "五个动词与并行": {"stage": "4", "tests": []},
    "常驻提示": {"stage": "5", "tests": []},
    "会话、活动与记忆": {"stage": "4", "tests": []},
    "本机运行修复": {"stage": "7", "tests": _STAGE2_LOCAL_RUN_FIX_TESTS},
    "交付": {"stage": "3", "tests": _DELIVERY_TESTS},
    "无新增故障": {"stage": "7", "tests": _STAGE2_NO_NEW_FAULT_TESTS},
    "真实复验": {"stage": "8", "tests": []},
    "文档一致性": {"stage": "7", "tests": []},
    "工程门禁": {"stage": "7", "tests": []},
}

_AGENT = "custom/agents/life_agent.py"
_ENGINE = "src/worldmock/life/engine.py"
_MODELS = "src/worldmock/life/models.py"
_SESSION_ACTIONS = "src/worldmock/life/session_actions.py"
_CONTRACTS = "docs/CONTRACTS.md"
_RECORDING = "docs/design/05-recording-engine.md"
_BODY = "src/worldmock/life/body.py"
_RHYTHM = "src/worldmock/life/rhythm.py"
_PROJECTION = "src/worldmock/life/projection.py"
_LIFE_RUNTIME = "docs/dev/SPEC-life-runtime.md"

#: 阶段 → 完成后必须从列出路径中消失的符号（正则, 路径或目录列表）。
REMOVED_SYMBOLS: dict[str, list[tuple[str, list[str]]]] = {
    "0": [
        (r"not isinstance\(rows, list\) or not rows", [_AGENT]),
        (r"interactive objects layer is unavailable", [_AGENT]),
        (r'row\["submission_ready"\]', [_AGENT]),
        (r'data\["activity"\]\["location_id"\] =', [_ENGINE]),
        (r"if any\(activity\.kind == ActivityKind\.WORK", [_ENGINE]),
        (r'activity\.get\("location_id"\) != location_id', ["src/worldmock/footprints/experiences.py"]),
        (r"\bg\.kind\b", [_SESSION_ACTIONS]),
        (r"WORLD_SCHEMA_VERSION = 6\b", [_MODELS]),
        (r"世界状态结构版本 `WorldState\.schema_version` \| 5 \|", [_CONTRACTS]),
        (r"场景结构 3、世界状态结构 5", [_CONTRACTS]),
        (r"有效工作量达到合同要求后开放；任务名称", [_CONTRACTS]),
        (r"世界结构版本为 `5`", [_CONTRACTS, _RECORDING]),
        (r"Literal\[5\]", ["docs/design/09-io-formats.md"]),
        (r"世界状态 `schema_version` \| 5 \|", ["docs/design/08-configuration.md"]),
        (r"世界状态结构为 5", ["docs/design/03-resource-engine.md"]),
        (r"世界结构 `4` 独立冻结", ["docs/EXPERIMENT.md"]),
        (r"`schema_version=5`", ["docs/VERIFICATION.md"]),
    ],
    "2": [
        (r"INSUFFICIENT_ENERGY", ["src", "custom"]),
        (r"energy_feasibility", ["src", "custom"]),
        (r"ELIGIBILITY_ENERGY_INSUFFICIENT", ["src", "custom"]),
        (r'"ELIGIBILITY_ENERGY_INSUFFICIENT",\s+# 能量不允许', [_CONTRACTS]),
        (r"`interaction_menus_by_location_kind`、`energy_feasibility`", [_CONTRACTS]),
        (r"minimum_energy_cost_kcal、resume_energy_feasible", [_CONTRACTS]),
        (r"_require_energy_plan|energy_check|_remaining_activity_energy", [_ENGINE]),
        (r"validate_activity_energy", ["src"]),
        (r"activity would exhaust available energy", ["src"]),
        (r"active_energy_entries|combined_energy_cost|maximum_combined_duration", ["src"]),
        (r"shared_energy_cost|maximum_shared_energy_duration", ["src"]),
        (r"\brecovery_kinds\b", [_ENGINE, _RHYTHM]),
        (r"_activity_infeasible_signatures|activity_infeasible=", [_RHYTHM]),
        (r"activity_infeasible: bool|snap\.activity_infeasible", ["src/worldmock/life/schedule.py"]),
        (r"(?m)^def (body_observation|felt_levels|felt_values)\(", [_BODY]),
        (r"alert_codes", ["src"]),
        (r"energy_projection", [_ENGINE]),
        (r"energy_feasible|minimum_energy_cost_kcal|resume_energy_feasible", ["src", "custom"]),
        (r"delivery_delay_seconds", [_PROJECTION]),
        (r"\| `commitment_threatened` \|[^|\n]*或当前活动不可行|能量或位置条件已不允许当前活动继续", [_LIFE_RUNTIME]),
    ],
}

#: 阶段 → 完成后必须在每个列出路径中出现的符号（正则, 路径或目录列表）。
REQUIRED_SYMBOLS: dict[str, list[tuple[str, list[str]]]] = {
    "0": [
        (r"WORLD_SCHEMA_VERSION = 7\b", [_MODELS]),
        (r"WORK_TASK_IN_PROGRESS", ["src/worldmock/life/work.py", _CONTRACTS]),
        (r"#### 活动受理拒绝码", [_CONTRACTS]),
        (r"WORLD_SCHEMA_VERSION", ["src/worldmock/delivery/pipeline.py"]),
        (r'g\.intent_kind == goals\[0\]\["intent_kind"\]', [_SESSION_ACTIONS]),
        (r"Literal\[7\]", ["docs/design/09-io-formats.md"]),
        (r"state\.schema_version", ["docs/design/06-delivery.md"]),
        (r"世界结构版本为 `7`", [_CONTRACTS, _RECORDING]),
    ],
    "2": [
        (r"APPETITE_STARVING_KCAL = 100\.0", ["src/worldmock/life/felt.py"]),
        (r"def appetite_level\(appetite: float, energy_kcal: float, emergency_kcal: float\)",
         ["src/worldmock/life/felt.py"]),
        (r"APPETITE_STARVING_KCAL < self\.emergency_threshold_kcal", ["src/worldmock/life/scenario.py"]),
        (r"FELT_SCAN_STEP_SECONDS = 900", [_BODY]),
        (r"FELT_SCAN_HORIZON_SECONDS = 86_400", [_BODY]),
        (r"def _meal_window_boundaries", [_BODY]),
        (r"wake_hour = \(chronotype_midsleep \+ sleep_need_hours / 2\) % 24", [_BODY]),
        (r"def route_effort|def food_energy_label", [_PROJECTION]),
        (r'"delivery_eta"', [_PROJECTION]),
        (r'"effort"', ["src/worldmock/life/narration.py", _PROJECTION]),
        (r"def plan_delivery", ["src/worldmock/life/basket.py"]),
        (r"def local_food_summary\(hunger: str, facts: FoodPathFacts\)", ["src/worldmock/life/dining.py"]),
        (r"LATE_DELIVERY_SECONDS = 3600", ["src/worldmock/life/dining.py"]),
        (r"def shared_activity_factors", ["src/worldmock/life/activities.py"]),
        (r"delivery_eta", [_CONTRACTS, "docs/design/09-io-formats.md"]),
        (r"`effort`", [_CONTRACTS]),
        (r"2026-09-25-time-and-energy-stage-2", [_CONTRACTS, _LIFE_RUNTIME]),
        (r"\| `activity_infeasible` \| 保留名称，当前无触发来源 \|", [_LIFE_RUNTIME]),
        (r"APPETITE_STARVING_KCAL", ["docs/manual/rules.md", "docs/manual/scenarios.md"]),
        (r"chronotype_midsleep \+ sleep_need_hours / 2", ["docs/manual/personas.md"]),
        (r"人读送达时刻", ["docs/manual/resources.md"]),
    ],
}
