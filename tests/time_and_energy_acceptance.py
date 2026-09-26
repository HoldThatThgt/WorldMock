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
STAGES_DONE = frozenset({"0", "1", "2", "6"})

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

_SAME_MOMENT = "tests/life/test_same_moment_decisions.py::"

#: 阶段 1 预先登记：同刻决策行由阶段 4 完成（「刚才」、交谈对方结束与名额、待收尾各支在阶段 3–4）。
_SAME_MOMENT_TESTS = [
    _SAME_MOMENT + name for name in (
        "test_run_start_and_instant_action_redecide_in_same_second",
        "test_activity_end_boundary_offers_no_active_activity_and_redecides",
        "test_business_rejection_redecides_in_same_second_with_rejection_feedback",
        "test_rejection_with_active_activity_retries_until_limit_then_follows_rhythm",
        "test_restricted_rounds_open_start_then_wait_only",
        "test_out_of_bounds_request_is_rejected_and_counted",
        "test_start_or_resume_accepts_start_and_resume_but_not_other_reports",
        "test_replay_round_counts_toward_same_moment_rounds",
        "test_clock_advances_only_when_everyone_has_an_active_activity",
        "test_report_with_active_activity_does_not_redecide_in_same_second",
        "test_wake_reasons_replaced_per_round_single_bedtime_reflection",
        "test_sleep_does_not_wake_for_no_active_activity",
        "test_service_refused_pulse_at_most_once_per_second",
        "test_motivation_internal_survives_parameter_swap",
        "test_scenario_with_max_consecutive_rejections_is_rejected",
        "test_restricted_stage_check_and_ladder_units",
        "test_restricted_observation_carries_stage_and_scene_note",
        "test_start_or_resume_schema_matches_engine_dispatch",
        "test_awake_recipient_gets_same_second_opportunity",
        "test_recipient_in_same_round_is_not_overwritten_by_its_own_success",
        "test_recipient_at_limit_gets_message_at_next_boundary",
        "test_restricted_round_does_not_use_up_message_opportunity",
        "test_sleeping_recipient_is_not_woken_by_person_message",
        "test_rejected_paired_start_leaves_no_chat_invite",
        "test_contact_reply_arrival_filtered_during_sleep",
        "test_same_second_rounds_keep_presentation_window_and_rotation",
        "test_browse_results_stay_pinned_in_later_same_second_rounds",
        "test_two_agents_same_round_keep_their_presentation_stamps",
    )
] + [
    "tests/life/test_scenario.py::test_body_instance_and_rejection_ranges_are_enforced",
    "tests/life/test_engine_feedback.py::test_business_rejection_is_reported_in_progress_and_run_continues",
    "tests/life/test_engine_branch_coverage.py::test_rework_rejection_at_limit_keeps_review_feedback",
    "tests/life/test_motivation.py::TestEmotion::test_frustration_lifts_comfort_candidates",
    "tests/integration/test_agent_branch_coverage.py::test_corrupt_observation_fails_before_any_model_tool",
    "tests/integration/test_agent_branch_coverage.py::test_start_or_resume_stage_narrows_tools_and_enums",
    "tests/integration/test_agent_branch_coverage.py::test_wait_only_stage_offers_only_wait",
    "tests/life/test_export_integration.py::test_model_npc_reply_loop_delivers_to_inbox_with_evidence",
    "tests/life/test_export_integration.py::test_model_npc_reply_at_most_once_per_message",
    "tests/integration/test_runtime.py::test_life_env_commit_settles_model_reply_into_inbox",
    "tests/life/test_wake_reasons.py::test_unread_inbox_message_yields_message_arrival_then_is_marked_read",
    # 整日运行的不变式：同一秒的轮数不超过上限加三，每个结算区间都有进行中活动。
    "tests/integration/test_dayrun_complete_days.py::"
    "test_complete_two_person_day_uses_real_tools_memory_and_restoration",
]

#: 阶段 1 为「候选时刻」行登记的重估候选与空闲间隔部分；预测部分（困意、饥饿与餐时窗口）由阶段 2 登记在同一行。
_CANDIDATE_TIME_TESTS = [
    _SAME_MOMENT + "test_first_decision_includes_reconsideration_candidate",
    _SAME_MOMENT + "test_idle_interval_only_while_waiting_without_commitments",
    _SAME_MOMENT + "test_game_start_follows_decision_rhythm",
    "tests/life/test_wake_reasons.py::test_commitments_exclude_messages_history_and_completed_activities",
]

#: 阶段 1 为「唤醒词表」行登记的新成员与写入校验部分；activity_infeasible 不再产生、commitment_threatened 的来源
#: 由阶段 2 登记在同一行。
_WAKE_VOCABULARY_TESTS = [
    "tests/footprints/test_event_fields.py::test_wake_reason_vocabulary_is_the_frozen_spec_set",
    "tests/footprints/test_event_fields.py::test_wake_reason_vocabulary_matches_the_engine_side_enum",
    "tests/footprints/test_event_fields.py::test_unknown_wake_reason_is_rejected_on_decision_started_write",
    "tests/footprints/test_event_fields.py::test_unknown_wake_reason_is_rejected_on_body_settlement_write",
    "tests/footprints/test_event_fields.py::test_new_wake_reasons_are_accepted_by_validation",
    "tests/life/test_wake_reasons.py::test_frozen_wake_and_intent_names",
    "tests/life/test_wake_reasons.py::test_sleep_allowed_reasons_add_only_plan_reminder_due",
    "tests/footprints/test_quality.py::test_decision_rhythm_counts_same_moment_rounds",
    "tests/evaluation/test_behavior_metrics.py::test_wake_reason_distribution_counts_same_moment_round_reasons",
]

#: 阶段 1 预先登记：无新增故障行由阶段 7 完成。
_NO_NEW_FAULT_TESTS = [
    _SAME_MOMENT + "test_feedback_interval_60_bounds_same_second_rounds",
    _SAME_MOMENT + "test_same_moment_state_survives_reopen",
    _SAME_MOMENT + "test_presentation_staging_survives_reopen",
]

#: 「本机运行修复」行中阶段 6（运行身份）的部分：AS2 缺失、为空或摘要不符即拒绝启动，清单含 git_commit 与 git_dirty。
_RUN_IDENTITY_TESTS = [
    "tests/integration/test_runtime.py::test_imported_as2_is_the_locked_8768e14_source",
    "tests/integration/test_runtime.py::test_code_identity_covers_every_imported_as2_file_except_pycache",
    "tests/integration/test_runtime.py::test_as2_identity_is_independent_of_install_location",
    "tests/integration/test_runtime.py::test_unlocked_as2_refuses_run_resume_replay_and_validate",
    "tests/integration/test_runtime.py::test_as2_lookup_failures_are_identity_refusals",
    "tests/integration/test_runtime.py::test_as2_file_read_failure_is_an_identity_refusal",
    "tests/integration/test_runtime.py::test_experiment_registers_the_locked_as2_source_digest_verbatim",
    "tests/integration/test_runtime.py::test_git_state_records_head_and_dirty_flag_of_the_checkout",
    "tests/integration/test_runtime.py::test_git_state_is_null_when_git_is_unavailable_or_status_fails",
    "tests/integration/test_runtime.py::test_run_manifest_records_git_commit_and_dirty_flag",
    "tests/integration/test_run_manifest.py::test_run_identity_records_git_state_as_a_pair",
    "tests/integration/test_host_branch_coverage.py::test_require_runtime_identity_rejects_diverged_manifest",
]

#: 阶段 2 为「候选时刻」行登记的预测部分：困意与饥饿跨档扫描（升档、降档）、餐时窗口起止、100 kcal 保底档；
#: 重估候选与空闲间隔由阶段 1 登记在同一行。
_STAGE2_CANDIDATE_TIME_TESTS = [
    "tests/life/test_sleep_two_process.py::TestSleepinessCrossingPrediction::"
    "test_prediction_from_current_pressure_is_not_later_than_actual",
    "tests/life/test_activity_effects.py::test_hunger_upward_crossing_is_not_later_than_actual",
    "tests/life/test_activity_effects.py::test_hunger_downward_crossing_at_meal_window_end",
    "tests/life/test_activity_effects.py::test_hunger_crossing_reverted_by_meal_window_end_is_not_skipped",
    "tests/life/test_energy_feasibility.py::test_hunger_crossing_just_before_lunch_end_wakes_during_wait",
    "tests/life/test_activity_effects.py::test_body_boundary_uses_curve_node_and_food_net_energy",
    "tests/life/test_activity_effects.py::test_meal_window_edges_are_boundary_candidates",
    "tests/life/test_activity_effects.py::test_chat_and_call_are_predicted_but_sleep_and_game_are_not",
    "tests/life/test_engine_branch_coverage.py::test_next_body_boundary_skips_zero_rates_when_nothing_due",
]

#: 阶段 2 为「唤醒词表」行登记：activity_infeasible 不再产生，commitment_threatened 只来自临近截止与临近闭店；
#: 新成员、写入校验与冻结名称由阶段 1 登记在同一行。
_STAGE2_WAKE_VOCABULARY_TESTS = [
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
    "tests/life/test_dayrun_restaurants.py::test_scene_food_path_names_the_fastest_delivery_to_here",
    "tests/life/test_dayrun_restaurants.py::test_scene_food_path_names_the_nearest_store_when_nothing_delivers_here",
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
    "同刻决策": {"stage": "4", "tests": _SAME_MOMENT_TESTS},
    "候选时刻": {"stage": "2", "tests": _CANDIDATE_TIME_TESTS + _STAGE2_CANDIDATE_TIME_TESTS},
    "唤醒词表": {"stage": "2", "tests": _WAKE_VOCABULARY_TESTS + _STAGE2_WAKE_VOCABULARY_TESTS},
    "能量感知": {"stage": "2", "tests": _STAGE2_ENERGY_PERCEPTION_TESTS},
    "能量不拦人": {"stage": "4", "tests": _STAGE2_ENERGY_NEVER_BLOCKS_TESTS},
    "零点计划与提醒": {"stage": "5", "tests": []},
    "五个动词与并行": {"stage": "4", "tests": []},
    "常驻提示": {"stage": "5", "tests": []},
    "会话、活动与记忆": {"stage": "4", "tests": []},
    "本机运行修复": {"stage": "7", "tests": _RUN_IDENTITY_TESTS + _STAGE2_LOCAL_RUN_FIX_TESTS},
    "交付": {"stage": "3", "tests": _DELIVERY_TESTS},
    "无新增故障": {"stage": "7", "tests": _NO_NEW_FAULT_TESTS + _STAGE2_NO_NEW_FAULT_TESTS},
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
_SCHEDULE = "src/worldmock/life/schedule.py"
_FOOTPRINT_MODELS = "src/worldmock/footprints/models.py"
_IO_FORMATS = "docs/design/09-io-formats.md"
_RUNTIME = "src/worldmock/runtime.py"
_EXPERIMENT = "docs/EXPERIMENT.md"
_MANUAL_RUNTIME = "docs/manual/runtime.md"
_AS2_LOCKED_DIGEST = "65a902ec66df42296d4e4c85e424bce3512e437133fc6be9fa597766fc30a334"
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
    "1": [
        (r"max_consecutive_rejections", ["src", "custom", "scenarios"]),
        (r"maximum_rejections", ["src", "custom"]),
        (r"def _after_unknown", [_ENGINE]),
        (r'str\(error\) == "RESOURCE_CONFLICT" and running', [_ENGINE]),
        (r'"next_decision_at": state\.current_time \+ 1\b', [_ENGINE]),
        (r"_put_agent_and_defer_feedback", ["src"]),
        (r"consecutive_rejections", [_SCHEDULE, "src/worldmock/life/rhythm.py"]),
        (r"成员(?:集)?不可增删", [_SCHEDULE, _FOOTPRINT_MODELS]),
        (r"self\._pending_presentation = _presentation_stamps", ["src/worldmock/life/observe.py"]),
        (r"inbox\[reply\.message\.message_id\] = reply\.message", ["src/worldmock/runtime.py"]),
        (r'"max_consecutive_rejections": 10', ["docs/manual/scenarios.md"]),
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
    "6": [
        (r'"vendor" / "agentsociety2"', [_RUNTIME]),
        (r"哈希全部生产 Python 源码与根依赖锁定文件。", [_RUNTIME]),
        (r"`vendor/agentsociety2` 的维护分支 `worldmock-life` \|", [_EXPERIMENT]),
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
    "1": [
        (r"max_same_moment_decisions", ["src/worldmock/life/scenario.py", "src/worldmock/life/semantics.py",
                                       "src/worldmock/life/motivation.py", _CONTRACTS,
                                       "docs/design/08-configuration.md", "docs/manual/scenarios.md"]),
        (r"activity_feedback_seconds: int = Field\(ge=60", ["src/worldmock/life/scenario.py"]),
        (r'NO_ACTIVE_ACTIVITY = "no_active_activity"', [_SCHEDULE]),
        (r'PLAN_REMINDER_DUE = "plan_reminder_due"', [_SCHEDULE]),
        (r'"no_active_activity"', [_FOOTPRINT_MODELS]),
        (r'"plan_reminder_due"', [_FOOTPRINT_MODELS]),
        (r"SAME_MOMENT_ACTION_RESTRICTED", ["src/worldmock/life/same_moment.py", _CONTRACTS]),
        (r"same_moment_rounds", [_MODELS, _CONTRACTS, _IO_FORMATS, _RECORDING]),
        (r"same_moment_stage", [_MODELS, "src/worldmock/life/observe.py", _AGENT, _CONTRACTS]),
        (r"same_moment_presentation", [_MODELS, "src/worldmock/life/state_commit.py", _IO_FORMATS]),
        (r"validate_decision_rhythm", ["src/worldmock/life/decision_start.py", "src/worldmock/life/rhythm.py"]),
        (r"CONTACT_REPLY_DELAY_SECONDS", ["src/worldmock/life/social.py", "src/worldmock/runtime.py", _CONTRACTS]),
        (r"def test_", ["tests/life/test_same_moment_decisions.py"]),
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
    "6": [
        (rf'AS2_LOCKED_SOURCE_SHA256 = "{_AS2_LOCKED_DIGEST}"', [_RUNTIME]),
        (rf"\| AS2 源码摘要 \| `{_AS2_LOCKED_DIGEST}` \|", [_EXPERIMENT]),
        (r'_AS2_PACKAGE = "agentsociety2"', [_RUNTIME]),
        (r"importlib\.util\.find_spec\(_AS2_PACKAGE\)", [_RUNTIME]),
        (r"AS2 runtime identity differs from the locked source", [_RUNTIME, _MANUAL_RUNTIME]),
        (r"require_locked_as2\(\)", [_RUNTIME, "src/worldmock/validation.py"]),
        (r"git_commit", [_MODELS, _ENGINE, _RUNTIME, _RECORDING, _EXPERIMENT]),
        (r"git_dirty", [_MODELS, _ENGINE, _RUNTIME, _RECORDING, _EXPERIMENT]),
        (r"--untracked-files=normal", [_RUNTIME]),
        (r"git submodule update --init vendor/agentsociety2 vendor/labelkit", ["README.md", _MANUAL_RUNTIME]),
        (r"\*\*锁定提交\*\*：`8768e14`（子模块指针，运行身份按它的源码摘要比对）", ["docs/dev/SPEC-as2-integration.md"]),
        (r"运行清单冻结的 `code_sha256` 覆盖", [_RECORDING]),
        (r"代码身份含实际导入的 AS2", [_MANUAL_RUNTIME]),
        (r"升级代码后旧检查点不能恢复", [_MANUAL_RUNTIME]),
    ],
}
