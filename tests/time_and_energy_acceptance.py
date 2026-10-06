"""时间与能量规格的验收登记表（完整性守卫的数据源）。

@file     time_and_energy_acceptance.py
@brief    把 docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「验收台账」的每一行登记到其归属阶段与证据测试，
          并登记各阶段必须消失（REMOVED_SYMBOLS）的生产符号；新增功能直接由已有行为测试验证；
          判定由 tests/test_time_and_energy_completeness.py 执行。
@note     `stage` 取「完成后整行才可完整验证」的阶段，即台账行最后一个有贡献的阶段；
          贡献阶段可以并行合入时（如阶段 6 与阶段 1–5），取依赖图中依赖其全部贡献阶段的首个阶段；
          更早的阶段可以预先登记已经存在的测试。只有 `stage` 属于 STAGES_DONE 的行必须列出测试，
          但任何行列出的测试都必须能解析到存在且未被跳过的测试函数。
          删除符号检查覆盖 src 与 custom 的全部生产 Python 文件；不以单一旧文件或文档措辞为判定。
"""

from __future__ import annotations

#: 实施计划「实施阶段」中的全部阶段编号。
STAGE_IDS = frozenset(str(n) for n in range(10))

#: 已完成的阶段；每个阶段收尾时把自己的编号加入。
STAGES_DONE = frozenset({"0", "1", "2", "3", "4", "5", "6", "7"})

_GREEN_BASELINE_TESTS = [
    "tests/life/test_dayrun_inventory_regressions.py::"
    "test_meal_partitions_reach_zero_without_losing_quantity_or_energy",
    "tests/integration/test_dayrun_complete_days.py::"
    "test_complete_two_person_day_uses_real_tools_memory_and_restoration",
    "tests/integration/test_agent_branch_coverage.py::test_empty_interactive_layer_decision_continues_without_failure",
    "tests/life/test_grounded_observation.py::test_real_observation_during_active_activity_keeps_decision_open",
    "tests/life/test_session_state.py::test_world_schema_version_is_8",
    "tests/life/test_session_state.py::test_world_state_schema_version_7_is_rejected",
    "tests/life/test_session_state.py::test_run_with_version_7_world_state_is_rejected_on_open",
    "tests/footprints/test_verified_experiences.py::test_arrival_snapshot_at_origin_still_projects_arrived",
    "tests/life/test_travel_graph.py::test_travel_progress_snapshot_keeps_origin_and_arrival_is_verified",
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
        "test_rejected_start_stays_in_start_or_resume_and_marks_the_option",
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
        "test_restricted_observation_marks_closed_actions_unavailable",
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
    "tests/life/test_tool_layer.py::test_restricted_round_marks_closed_items_with_the_same_moment_reason",
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
    'tests/life/test_shift_edges.py::test_t10_app_content_boundary_after_shift_end_does_not_hide_the_edge',
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
    "tests/integration/test_runtime.py::test_imported_as2_matches_rebuildable_locked_source",
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
    'tests/life/test_shift_edges.py::test_t12_app_can_pause_at_shift_edge_and_content_completion_stays_quiet',
    "tests/life/test_engine_branch_coverage.py::test_next_body_boundary_skips_zero_rates_when_nothing_due",
]

#: 阶段 2 为「唤醒词表」行登记：activity_infeasible 不再产生，commitment_threatened 只来自临近截止与临近闭店；
#: 新成员、写入校验与冻结名称由阶段 1 登记在同一行。末项为阶段 9 补登：discovery_result 保留在闭集中而不产生，
#: 浏览结果随浏览结束回灌到随后的观察（第一章规则 6，验收标准测试矩阵「决策节律」行）。
_STAGE2_WAKE_VOCABULARY_TESTS = [
    "tests/life/test_wake_reasons.py::test_activity_infeasible_has_no_source_and_threat_only_from_deadline",
    "tests/life/test_wake_reasons.py::test_zero_energy_during_activity_raises_neither_infeasible_nor_threat",
    "tests/life/test_wake_reasons.py::test_engine_schedules_exact_task_warning_and_appends_threat_reason",
    "tests/life/test_wake_reasons.py::test_browse_results_return_in_observation_never_as_discovery_result",
]

#: 阶段 2 完成的「能量感知」行：两档低能量饥饿、阈值次序校验、提示不含身体数值、食品热量与路线费力标签。
_STAGE2_ENERGY_PERCEPTION_TESTS = [
    "tests/life/test_felt_states.py::TestLowEnergyAppetite::test_below_emergency_threshold_is_at_least_unbearable",
    "tests/life/test_felt_states.py::TestLowEnergyAppetite::test_below_starving_constant_is_starving",
    "tests/life/test_felt_states.py::TestLowEnergyAppetite::test_emergency_floor_ignores_meal_and_circadian_terms",
    "tests/life/test_felt_states.py::TestLowEnergyAppetite::test_starving_is_not_an_appetite_band",
    "tests/life/test_felt_states.py::TestLowEnergyAppetite::test_summary_applies_the_same_rules",
    "tests/life/test_engine_branch_coverage.py::test_body_projection_renders_five_band_texts_and_numeric_values",
    "tests/life/test_semantics.py::test_energy_thresholds_come_from_worldmock_body_constants",
    "tests/life/test_felt_states.py::test_body_config_rejects_emergency_threshold_not_above_starving",
    "tests/life/test_felt_states.py::test_scenario_with_emergency_threshold_at_starving_constant_fails_to_load",
    "tests/life/test_wake_reasons.py::test_low_energy_bands_match_the_decision_reference",
    "tests/life/test_public_observation_contract.py::test_low_energy_prompt_carries_levels_and_labels_but_no_body_numbers",
    "tests/life/test_public_observation_contract.py::test_food_energy_label_rounds_half_up_to_ten_kcal",
    "tests/life/test_public_observation_contract.py::test_products_and_inventory_show_food_energy_and_elsewhere_holdings",
    "tests/life/test_public_observation_contract.py::test_route_effort_bands_follow_travel_met",
    "tests/life/test_public_observation_contract.py::"
    "test_routes_carry_effort_and_only_reachable_routes_are_listed_at_zero_energy",
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
    "tests/life/test_dayrun_restaurants.py::test_food_paths_are_discoverable_as_tool_layer_targets",
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
    "tests/life/test_revision_contract.py::test_public_observation_excludes_private_fields_and_supply_expires",
]

_LOCATION = "tests/life/test_location_sessions.py::"
_PROGRESS_LAYER = "tests/life/test_progress_layer_projection.py::"
_MEMORIES = "tests/integration/test_conclude_memories.py::"
_AGENT_BRANCHES = "tests/integration/test_agent_branch_coverage.py::"

#: 阶段 3 预先登记：同刻决策行中依赖名额与待收尾的各支（受限轮的收尾；暂停的活动不占名额，两项都暂停到上限后进入
#: 开始或恢复，同时做两件事规格阶段 1 起不再进入恢复或结束与只开放结束；只能收尾的轮次不消耗人物消息的机会）。
_SAME_MOMENT_TESTS += [
    _LOCATION + "test_just_ended_requires_conclusion_in_the_same_second",
    _LOCATION + "test_two_running_activities_block_a_third_until_one_is_paused",
    _LOCATION + "test_both_paused_redecide_same_second_until_one_resumes",
    "tests/life/test_tool_layer.py::test_pending_conclusion_marks_everything_but_conclusion_unavailable",
    "tests/life/test_tool_layer.py::test_held_elsewhere_and_paused_elsewhere_are_listed_with_reasons",
    "tests/life/test_todo_layer.py::test_resume_elsewhere_is_unavailable_and_names_the_place",
] + [
    _SAME_MOMENT + name for name in (
        "test_pending_conclusion_rejects_other_requests_until_concluded_within_limit_plus_three",
        "test_conclude_pending_stage_accepts_only_concluding_every_pending_activity",
        "test_both_paused_at_the_limit_open_start_or_resume",
        "test_concluding_a_paused_activity_in_start_or_resume_is_restricted",
        "test_rejected_resume_with_both_paused_stays_in_start_or_resume",
        "test_restricted_conclusions_endings_and_waits_pass_the_stage_check",
        "test_conclusion_branch_and_start_or_resume_of_the_ladder",
        "test_conclusion_only_round_does_not_use_up_message_opportunity",
    )
]

#: 阶段 3 预先登记：会话、活动与记忆行由阶段 4 完成（「接着看」不增加接触次数在阶段 4）。
_SESSIONS_AND_MEMORY_TESTS = [
    _LOCATION + name for name in (
        "test_run_start_opens_one_stay_session_per_agent",
        "test_departure_and_arrival_switch_sessions_and_end_road_activities",
        "test_other_activity_keeps_running_while_the_ended_one_is_concluded",
        "test_departure_with_running_activity_is_rejected_naming_it",
        "test_paused_activity_resumes_only_at_its_start_location",
        "test_paused_activity_can_be_concluded_elsewhere",
        "test_pausing_an_activity_on_the_road_is_unavailable",
        "test_ending_travel_midway_ends_road_activities_and_stays_at_origin",
        "test_arrival_denied_fails_travel_and_starts_stay_at_destination",
        "test_experiment_end_ends_every_session_without_conclusion",
        "test_same_named_activities_get_ordinals_instead_of_failing",
        "test_recall_by_task_and_activity_names_reaches_the_next_observation",
        "test_lifecycle_records_and_export_match_the_final_checkpoint",
        "test_slot_counting_durations_and_session_helpers_are_pure",
        "test_waking_with_the_sleep_awaiting_conclusion_offers_its_conclusion",
        "test_two_item_start_with_one_free_slot_says_only_one_slot_is_left",
        "test_travel_with_full_slots_is_rejected_for_slots_before_the_departure_check",
        "test_start_request_shape_errors_are_action_request_invalid",
        "test_experiment_end_export_matches_the_final_checkpoint_for_every_truncated_activity",
        "test_recall_of_content_started_with_a_walk_names_the_road",
    )
] + [
    _MEMORIES + name for name in (
        "test_life_tool_surface_never_offers_finish",
        "test_registered_world_request_ends_the_decision_and_memory_queries_do_not",
        "test_conclusion_memories_flatten_by_task_with_system_source",
        "test_only_conclude_activity_carries_memories",
        "test_conclusion_memory_limits_are_protocol_errors_retried_in_the_same_decision",
        "test_conclusion_memory_limits_accept_the_boundaries",
        "test_memory_runtime_accepts_only_system_sourced_conclusion_memories",
        "test_conclusion_sources_are_registered_by_the_system",
        "test_conclusion_rejected_at_settlement_keeps_its_memories",
        "test_nothing_sent_to_the_person_model_carries_an_internal_identity",
        "test_budget_exhausted_without_a_registered_world_request_fails_the_run",
        "test_readonly_finish_batched_with_another_tool_is_rejected",
        "test_threshold_consolidation_counts_conclusion_entries_but_not_bedtime_reflection",
    )
] + [
    "tests/life/test_experience_sentences.py::test_observation_keeps_experiences_private_and_out_of_the_layers",
    "tests/life/test_experience_sentences.py::test_every_experience_kind_is_written_as_a_sentence_without_identities",
    "tests/life/test_memory_evidence.py::test_write_points_follow_the_registered_conclusion_and_the_wake_from_sleep_round",
    "tests/life/test_memory_evidence.py::test_episodes_written_without_any_write_point_are_a_protocol_violation",
    "tests/life/test_memory_evidence.py::test_conclusion_memories_record_the_activity_conclusion_write_point",
    "tests/life/test_memory_evidence.py::test_rejected_conclusion_keeps_its_memories_as_self_report",
    "tests/life/test_memory_evidence.py::test_waking_round_lists_conclusion_then_reflection",
    "tests/footprints/test_quality.py::test_memory_quality_reports_conclusion_without_memory_update",
    "tests/footprints/test_quality.py::"
    "test_conclusion_and_reflection_in_one_step_are_each_matched_to_their_registration",
    "tests/footprints/test_quality.py::test_memory_update_whose_write_point_has_no_registration_has_no_source",
    "tests/life/test_export_integration.py::"
    "test_person_conclusion_keeps_memories_in_request_and_registers_private_sources",
    "tests/test_start_activity_contract.py::test_conclude_ends_only_selected_activities_with_own_summaries",
    "tests/test_start_activity_contract.py::test_conclude_naming_none_of_the_just_ended_activities_is_rejected",
    "tests/integration/test_dayrun_complete_days.py::"
    "test_complete_two_person_day_uses_real_tools_memory_and_restoration",
    _PROGRESS_LAYER + "test_recall_reads_task_and_activity_without_identities",
    _PROGRESS_LAYER + "test_conclude_marks_named_just_ended_activities_and_keeps_their_status",
    _PROGRESS_LAYER + "test_activity_targets_carry_only_readable_objective_progress",
    "tests/life/test_progress_events.py::test_arrival_session_is_time_bound_and_excludes_the_departure",
    "tests/life/test_todo_layer.py::test_paused_activities_are_ordered_by_pause_time_not_start_time",
    "tests/life/test_engine_feedback.py::test_pause_report_resume_and_conclude_name_the_activity",
    "tests/life/test_engine_branch_coverage.py::test_prepare_pending_work_review_while_the_wait_awaits_conclusion",
]

_WAKE = "tests/life/test_wake_reasons.py::"
_PROGRESS = "tests/life/test_progress_layer_projection.py::"

#: 阶段 3 预先登记：本机运行修复行中的播报、场景、进度与导出四项（其余在阶段 2、4、6）。
_LOCAL_RUN_FIX_TESTS = [
    _WAKE + "test_progress_report_due_counts_only_active_activities_from_their_anchor",
    _WAKE + "test_report_anchor_moves_on_start_report_and_resume_and_paused_activity_never_reports",
    _WAKE + "test_sleeping_person_gets_no_progress_report_wakeups",
    _PROGRESS + "test_progress_rows_follow_pause_resume_end_and_conclusion",
    _PROGRESS + "test_work_objective_progress_grows_with_settlement",
    "tests/life/test_prompt_layers.py::test_paused_activity_progress_appears_only_in_the_todo_layer",
    _LOCATION + "test_self_report_stays_out_of_the_tool_layer_and_is_only_a_call_argument",
    _LOCATION + "test_lifecycle_records_and_export_match_the_final_checkpoint",
    "tests/life/test_export_integration.py::test_lifecycle_records_and_export_match_the_final_checkpoint",
    _LOCATION + "test_experiment_end_export_matches_the_final_checkpoint_for_every_truncated_activity",
]

#: 阶段 3 完成交付行：带记忆的 conclude_activity 进入意图与 dreaming 窗口时不含 memories。
_DELIVERY_TESTS += [
    "tests/delivery/test_windows.py::test_conclude_activity_with_memories_does_not_leak_memory_text",
    "tests/life/test_export_integration.py::test_completed_activity_exports_terminal_outcome_and_training_windows",
]

#: 阶段 3 预先登记：同轮可见名称重复、到达被拒、途中结束出行与检查点恢复下无 run_failed。
_NO_NEW_FAULT_TESTS += [
    _LOCATION + "test_cross_day_duplicate_activity_names_get_ordinals",
    _LOCATION + "test_arrival_denied_fails_travel_and_starts_stay_at_destination",
    _LOCATION + "test_ending_travel_midway_ends_road_activities_and_stays_at_origin",
    _LOCATION + "test_sessions_and_pending_conclusion_survive_reopen",
]

_ADMISSION = "tests/life/test_parallel_admission.py::"
_SOCIAL_ROUNDS = "tests/life/test_social_rounds.py::"
_SHADOW = "tests/catalog/test_affordance_shadow.py::"
_DOMAIN = "tests/life/test_domain_branch_coverage.py::"
_MENU_INVARIANTS = "tests/life/test_interaction_menu_invariants.py::"
_CONTRACT = "tests/test_start_activity_contract.py::"
_WORK_REVIEW = "tests/life/test_work_review.py::"
_PARALLEL_REJECTION = "tests/integration/test_parallel_rejection_contract.py::"

#: 阶段 4 完成同刻决策行：本人世界请求的公开结果留在进度层（同秒各轮都在）；交谈对方同秒结束时，本人仍有其他进行中活动则原因为
#: planned_end，已达上限则按规则 3 的阶梯从收尾一支重新开始、每次至多另加三轮；业务拒绝后同秒再决策并带说明。
_SAME_MOMENT_TESTS += [
    _SAME_MOMENT + name for name in (
        "test_same_second_results_stay_in_the_progress_layer",
        "test_purchase_result_names_the_order_in_the_progress_layer",
        "test_partner_ended_chat_redecides_same_second_with_planned_end",
        "test_partner_ended_at_the_limit_restarts_the_ladder_from_conclusion",
    )
] + [
    _SOCIAL_ROUNDS + "test_one_side_ending_ends_the_partner_chat_in_the_same_second",
    _SOCIAL_ROUNDS + "test_awake_partner_gets_the_invite_in_the_same_second_and_a_sleeper_is_not_woken",
    "tests/life/test_resource_conflict_progress.py::"
    "test_active_work_is_listed_with_note_and_conflict_redecides_in_same_second",
]

#: 阶段 4 评审修复，同刻决策：同一轮里刚受理的交谈被对方结束，也在同一秒收尾，收尾前时钟不推进（第一章规则 1、2）。
_SAME_MOMENT_TESTS += [
    _SOCIAL_ROUNDS + "test_chat_accepted_and_ended_by_the_partner_in_one_round_is_concluded_before_the_clock_moves",
]

#: 变异补强，同刻决策：一项活动待收尾、另一项仍在进行时做别的被拒到同刻上限，人物仍在当秒就绪、时钟不推进，
#: 收尾后按决策节律推进（第一章规则 2、3，第七章「活动一结束就要结束它」）。
_SAME_MOMENT_TESTS += [
    _SAME_MOMENT + "test_pending_conclusion_beside_a_running_activity_holds_the_clock_past_the_limit",
]

#: 人物 Prompt 阶段 5，同刻决策：删 wait_only 及其升级，受限轮用完仍无进行中活动（或仍待收尾）时写
#: decision_budget_exhausted、不造等待活动、时钟推进、无 run_failed，质量报告计数（人物 Prompt 规格裁决 #25）。
_SAME_MOMENT_TESTS += [
    "tests/life/test_decision_budget.py::" + name for name in (
        "test_restricted_rounds_used_up_without_an_activity_pause_technically",
        "test_a_successful_last_restricted_round_carries_the_latest_rejection_code",
        "test_a_replayed_rejection_in_the_last_restricted_round_exhausts_the_budget",
        "test_budget_exhausted_with_a_pending_conclusion_beside_a_running_activity",
    )
]

#: 阶段 4 完成能量不拦人行：可供性注册表删 energy_projection，观察合同键为五个。
_STAGE4_ENERGY_NEVER_BLOCKS_TESTS = [
    'tests/test_single_source.py::test_current_registry_and_observation_contract_retire_energy_and_game_keys',
    # 评审修复：手边两件食品合成一餐照常开始，不因组内两项都占 hands 而被拒。
    _ADMISSION + "test_two_foods_are_one_meal_and_only_same_way_obtains_are_one_order",
]

#: 阶段 4 完成的「五个动词与并行」行：第五章验收（并行组合、途中投影与菜单、订单与取得的先后、共同身体只结算一次、
#: 非法组合整组拒绝、互斥只由注册表占用判定、交谈的接受与结束、三种反馈间隔、列出的动作都有可满足的状态），
#: 以及动作照常列出带说明、选错给 reason 与 next_step、引导性拒绝不计外部约束、86400 秒上限、请求数组校验、
#: 联系人回复策略与交谈缓解的双方同时秒数。
_FIVE_VERBS_TESTS = [
    _ADMISSION + name for name in (
        "test_driving_with_office_work_pickup_or_face_chat_fails_target_presence",
        "test_work_with_eating_watching_or_podcast_is_accepted_and_settles_one_shared_body",
        "test_overlapping_pairs_are_resource_conflicts",
        "test_driving_rejects_messages_remote_orders_and_submissions",
        "test_walking_accepts_messages_and_remote_orders_delivered_to_the_origin",
        "test_en_route_person_is_nowhere_with_nothing_at_hand",
        "test_departure_with_a_running_activity_names_it_in_reason_and_next_step",
        "test_zero_stock_supply_is_listed_with_its_ways_marked_sold_out",
        "test_listed_actions_are_menu_allowed_and_self_is_always_offered",
        "test_holding_lists_obtain_make_for_inventory_recipes_and_consume_use_otherwise",
    )
] + [
    _SOCIAL_ROUNDS + name for name in (
        "test_choosing_chat_with_the_inviter_accepts_without_a_new_invite",
        "test_repeated_chat_between_the_same_pair_is_chat_already_active",
        "test_one_side_ending_ends_the_partner_chat_in_the_same_second",
        "test_accepted_chat_relief_uses_seconds_both_chats_run_regardless_of_order",
        "test_people_on_the_road_are_not_present_and_cannot_be_invited",
        "test_none_policy_delivers_without_reply_and_template_policy_posts_a_placeholder",
        "test_chat_call_and_wait_run_without_failure_at_any_feedback_interval",
    )
] + [
    _CONTRACT + "test_ways_vocabulary_maps_each_way_to_exactly_one_life_verb",
    _CONTRACT + "test_unresolvable_selection_is_rejected_as_invalid_request",
    _CONTRACT + "test_two_offered_selections_resolve_item_by_item_to_internal_actions",
    _MENU_INVARIANTS + "test_every_way_maps_to_one_verb_and_covers_every_internal_action",
    _MENU_INVARIANTS + "test_binding_action_projects_verb_and_way_publicly_and_keeps_execution_private",
    _MENU_INVARIANTS + "test_merged_selection_combines_one_order_or_one_meal_only",
    _MENU_INVARIANTS + "test_selection_resolves_only_offered_verb_and_way_and_menu_judges_internal_actions",
    'tests/catalog/test_affordance_shadow.py::test_final_claim_slots_and_activity_kinds_are_closed',
    'tests/catalog/test_affordance_shadow.py::test_retired_claim_names_are_rejected',
    'tests/catalog/test_affordance_shadow.py::test_app_claims_match_activity_admission',
    'tests/life/test_app_ui.py::test_opened_app_habit_is_entertainment_without_app_candidates',
    'tests/life/test_app_ui.py::test_game_and_watch_ways_are_retired_and_open_synonyms_resolve',
    "tests/evaluation/test_goal_metrics.py::test_guided_rejections_count_as_other_rejections_not_external_constraints",
    "tests/evaluation/test_adapters.py::test_digital_interaction_follows_activity_type_regardless_of_claim_names",
    "tests/evaluation/test_adapters.py::test_start_items_follow_the_started_activity_kind_then_the_order_then_the_way",
    "tests/evaluation/test_behavior_metrics.py::test_wait_is_judged_by_the_started_activity_kind_not_the_request",
    'tests/catalog/test_affordance_shadow.py::test_driving_blocks_app_and_meals_but_allows_talk',
    "tests/life/test_activity_location_kinds.py::test_en_route_is_no_place_for_a_lifestyle_activity",
    _DOMAIN + "test_social_actions_choosing_chat_with_the_inviter_accepts",
    _DOMAIN + "test_social_actions_shared_chat_seconds_counts_the_reciprocal_overlap",
    _DOMAIN + "test_social_actions_ending_a_chat_ends_the_partner_chat_the_same_second",
    _DOMAIN + "test_social_actions_advance_applies_felt_ledger_by_shared_seconds",
    _DOMAIN + "test_validate_work_start_en_route_is_not_at_the_workplace",
    "tests/life/test_engine_branch_coverage.py::"
    "test_send_message_rejects_unknown_recipient_and_delivers_to_none_policy_contact",
    'tests/life/test_app_ui.py::test_app_duration_matches_tool_layer_and_admission',
    "tests/life/test_parallel_acceptance.py::test_incompatible_pair_is_rejected_without_partial_state",
    "tests/life/test_opslog.py::test_opslog_component_follows_the_internal_action_of_the_chosen_way",
    _PARALLEL_REJECTION + "test_conflicting_pair_is_rejected_as_a_whole_and_explained_to_the_person",
    _PARALLEL_REJECTION + "test_menu_denies_the_whole_group_when_one_item_is_outside_it",
    _PARALLEL_REJECTION + "test_location_menu_denies_actions_outside_its_closed_set",
    _PARALLEL_REJECTION + "test_protected_self_actions_remain_usable",
    "tests/integration/test_grounded_targets.py::test_start_rejects_invalid_pairs_before_dispatch",
    _AGENT_BRANCHES + "test_empty_interactive_layer_decision_continues_without_failure",
    _AGENT_BRANCHES + "test_visible_objects_freeze_verb_way_and_bound_internal_action",
    'tests/life/test_food_presentation.py::test_product_targets_use_current_store_or_real_delivery_apps',
    "tests/life/test_tool_layer.py::test_every_listed_item_matches_admission_at_run_start",
    "tests/life/test_interaction_menu_runtime.py::"
    "test_observation_exposes_semantic_choices_and_keeps_bindings_private",
    "tests/life/test_interaction_menu_runtime.py::test_menu_rejects_unoffered_semantic_action_before_travel",
    "tests/life/test_public_observation_contract.py::"
    "test_real_observation_uses_readable_objects_and_private_bindings",
    'tests/life/test_app_progress.py::test_real_admission_blocks_driving_and_same_app_kind_accepts_eating_parallel',
    "tests/life/test_parallel_acceptance.py::test_parallel_progress_uses_one_shared_body_interval",
    "tests/life/test_work_contracts.py::test_work_start_rejects_skill_location_and_deadline_boundaries",
    'tests/life/test_mail_reading.py::test_arrived_mail_is_read_through_its_object',
]

#: 阶段 4 评审修复，五个动词与并行：合成一单或一餐只在方式相同时（「制作」不合并）；要停在一个地点做的事按整组开始
#: 之后人在哪里判定、与组内先后无关；到店与远程下单不按人物所在剪裁（不在店里、此地送不到时带说明并引导）；瞬时的
#: 取得可与持续活动同一请求（评估证据逐项对应）；驾车与远程下单同一请求「先下单，再出发」；接受时对方交谈快结束以
#: ACTIVITY_TOO_SHORT 引导；应用独立列出并显示当前会话推送；说明给时长或未满足的条件；公开原因只取引导性拒绝写明的
#: 那一句；交谈邀请、人物消息与联系人回复不是阅读对象；名称只从本决策唯一的观察解析、不缓存。
_FIVE_VERBS_TESTS += [
    _ADMISSION + name for name in (
        "test_two_foods_are_one_meal_and_only_same_way_obtains_are_one_order",
        "test_two_makes_are_not_merged_and_two_uses_are_refused_with_a_reason",
        "test_holding_or_lifestyle_with_a_walk_is_refused_in_either_order",
        "test_pickup_and_remote_order_stay_listed_away_from_the_store",
        "test_remote_order_where_it_cannot_be_delivered_is_noted_and_refused_naming_where_to_go",
        "test_an_instant_obtain_joins_a_timed_activity_in_one_request",
        "test_making_with_a_pickup_at_the_store_places_the_order_and_starts_the_making",
        "test_driving_with_a_remote_order_in_one_request_says_order_first",
        "test_malformed_request_gets_the_default_reason_without_the_validation_text",
        "test_a_repeated_observation_of_one_decision_is_ambiguous_on_every_read",
    )
] + [
    _SOCIAL_ROUNDS + "test_late_acceptance_is_noted_and_refused_with_a_reason_and_next_step",
    _SOCIAL_ROUNDS + "test_chat_accepted_and_ended_by_the_partner_in_one_round_is_concluded_before_the_clock_moves",
    "tests/integration/test_decision_context_protocol.py::"
    "test_start_activity_description_says_notes_give_length_or_unmet_condition",
    'tests/life/test_mail_reading.py::test_chat_invites_person_messages_and_contact_replies_stay_in_the_inbox_not_mail_objects',
]

#: 应用会话保持唯一全局进度，暂停恢复不增完成轮数，只有真实内容末尾增加轮数；
#: 名额未满而有进行中活动时选择出行，以 ACTIVITY_PRECONDITION_UNMET 点名要先处理的活动并给出下一步。
_SESSIONS_AND_MEMORY_TESTS += [
    'tests/life/test_app_progress.py::test_other_app_completed_round_resume_consumes_nothing_then_real_end_adds_once',
    'tests/life/test_app_progress.py::test_content_relief_is_fixed_for_each_kind_and_applied_to_real_body',
    _ADMISSION + "test_departure_with_a_running_activity_names_it_in_reason_and_next_step",
]

#: 阶段 4 评审修复，会话、活动与记忆：出发的同一请求不能做要停在一个地点做的事；瞬时的取得不占名额，活动按自己的绑定
#: 命名并归入会话。路上的活动不能暂停、在别处恢复暂停的活动带引导性原因与下一步，由已登记的会话用例
#: （test_pausing_an_activity_on_the_road_is_unavailable、test_paused_activity_resumes_only_at_its_start_location）断言。
_SESSIONS_AND_MEMORY_TESTS += [
    _ADMISSION + "test_holding_or_lifestyle_with_a_walk_is_refused_in_either_order",
    _ADMISSION + "test_an_instant_obtain_joins_a_timed_activity_in_one_request",
]

#: 阶段 4 对「本机运行修复」行（阶段 7 收尾）的预先登记：截止前不足 60 秒的开工带说明且选择时业务拒绝、
#: 「做不完」只在时长上界不足时出现、应用跨次暂停恢复沿唯一全局位置继续。
_STAGE4_LOCAL_RUN_FIX_TESTS = [
    _WORK_REVIEW + "test_work_start_under_one_minute_stays_listed_and_is_rejected_with_reason_and_next_step",
    _WORK_REVIEW + "test_completion_outlook_appears_only_when_the_time_upper_bound_is_short",
    'tests/life/test_app_progress.py::test_cross_app_pause_resumes_latest_global_position',
    "tests/life/test_grounded_observation.py::test_real_observation_lists_tail_media_as_restart_and_tail_work_with_note",
]

#: 人物 Prompt 阶段 5 对「本机运行修复」行的登记：未付款的逾期任务保留到截止当日结束、「已过截止」、提交以
#: WORK_SUBMISSION_LATE 业务拒绝且无 ProtocolError；工时达标、正在工作与能否提交分开说明。
_STAGE4_LOCAL_RUN_FIX_TESTS += [
    "tests/life/test_work_contracts.py::"
    "test_overdue_task_stays_listed_until_the_end_of_its_day_and_submission_is_a_business_rejection",
    "tests/life/test_work_contracts.py::test_target_met_while_still_working_states_the_three_facts_separately",
]

#: 阶段 4 对「无新增故障」行（阶段 7 收尾）的预先登记：交谈对方结束与反馈间隔为 60 秒时无 run_failed。
_STAGE4_NO_NEW_FAULT_TESTS = [
    _SAME_MOMENT + "test_partner_ended_chat_redecides_same_second_with_planned_end",
    _SOCIAL_ROUNDS + "test_chat_call_and_wait_run_without_failure_at_any_feedback_interval",
    # 评审修复：本轮才受理的交谈被对方结束、接受时对方交谈快结束，都不产生 run_failed。
    _SOCIAL_ROUNDS + "test_chat_accepted_and_ended_by_the_partner_in_one_round_is_concluded_before_the_clock_moves",
    _SOCIAL_ROUNDS + "test_late_acceptance_is_noted_and_refused_with_a_reason_and_next_step",
]

_PLANNING = "tests/life/test_day_planning.py::"
_RUNTIME_HOST = "tests/integration/test_runtime.py::"
_HOST_BRANCHES = "tests/integration/test_host_branch_coverage.py::"

#: 阶段 5 完成的「零点计划与提醒」行（第四章验收）：零点后台任务的启动路径、零点边界（按本地日期变更，含夏令时）
#: 与普通恢复挂点，同日只生成一次，输出不合法时附回重试、耗尽与传输失败留证后作运行故障（宿主经 Society.run 与
#: Society.init 登记），提醒只触发一次并可结束睡眠（睡眠中的候选与推进边界），修订保留、替换与取消提醒，
#: 呈现与输入口径（回顾时段、只取已受理的收尾），以及记录侧按 purpose 区分零点计划请求。
_DAY_PLANNING_TESTS = [
    _PLANNING + name for name in (
        "test_start_path_generates_the_day_plan_once_and_leaves_the_person_untouched",
        "test_mid_day_start_attaches_the_error_and_retries_invalid_output",
        "test_invalid_output_after_three_retries_is_a_run_fault_with_evidence",
        "test_system_entry_refuses_a_second_registration_and_an_invalid_plan",
        "test_transport_failure_is_a_run_fault_after_the_evidence_is_kept",
        "test_midnight_settles_the_old_day_then_plans_then_judges_the_second",
        "test_midnight_is_the_local_date_change_when_dst_skips_00_00",
        "test_sleep_crosses_midnight_and_the_new_reminder_ends_it_once",
        "test_sleep_ending_as_planned_at_the_reminder_keeps_planned_end",
        "test_sleep_keeps_the_reminder_among_the_next_decision_candidates",
        "test_reminder_staged_without_a_rhythm_pass_is_the_next_boundary_and_ends_the_sleep",
        "test_reminders_wake_once_merge_and_fire_after_the_goal_progressed",
        "test_revision_keeps_replaces_and_cancels_reminders",
        "test_plan_inputs_read_world_facts_and_self_reports_but_not_the_persons_mind",
        "test_plan_inputs_list_only_accepted_conclusions_once",
        "test_review_window_is_empty_at_a_midnight_start_and_the_old_day_at_a_midnight_boundary",
        "test_run_ending_at_midnight_runs_no_plan_day_and_nothing_follows_run_ended",
        "test_resume_and_counterfactual_branches_follow_plan_submitted_dates",
    )
] + [
    _RUNTIME_HOST + "test_life_env_checkpoint_plans_the_start_day_after_the_initial_checkpoint",
    _RUNTIME_HOST + "test_life_env_step_plans_midnight_before_the_step_checkpoint",
    _RUNTIME_HOST + "test_plain_resume_plans_the_missing_day_in_init_once",
    _RUNTIME_HOST + "test_counterfactual_init_waits_for_the_intervention_checkpoint",
    _RUNTIME_HOST + "test_plan_day_failure_during_resume_init_is_registered_before_raising",
    _RUNTIME_HOST + "test_plan_day_failure_at_a_midnight_step_is_one_run_failure_through_society_run",
    _RUNTIME_HOST + "test_plan_day_failure_at_the_initial_checkpoint_is_one_run_failure_through_society_init",
    _HOST_BRANCHES + "test_plan_day_client_enforces_manifest_identity",
    _HOST_BRANCHES + "test_plan_day_client_serializes_model_responses",
    _HOST_BRANCHES + "test_settle_pending_day_plans_builds_client_from_manifest_only_when_needed",
    "tests/integration/test_life_agent_protocol.py::test_plan_day_is_no_person_tool_and_revision_carries_remind_time",
    "tests/footprints/test_store.py::test_action_request_cutoff_counts_only_thinking_requests_with_the_action_tools",
    "tests/evaluation/test_behavior_metrics.py::test_plan_day_requests_are_not_decision_requests",
    "tests/footprints/test_quality.py::test_model_usage_counts_plan_day_and_lists_it_by_purpose",
    "tests/footprints/test_quality.py::test_plan_day_response_naming_memories_is_no_memory_problem",
    "tests/delivery/test_windows.py::test_plan_day_request_never_becomes_a_decision_prompt_or_window_input",
    "tests/evaluation/test_goal_metrics.py::test_zero_point_plan_event_registers_goals_that_revisions_keep",
    "tests/footprints/test_goal_export.py::test_real_engine_midnight_settlement_exports_old_day_goal_progress",
    "tests/life/test_day_plan.py::test_midnight_final_interval_progresses_old_plan_before_expiry",
    "tests/life/test_day_plan.py::test_empty_plan_is_registered_once_and_can_be_revised",
    "tests/life/test_revision_contract.py::test_plan_day_registration_and_retry_after_rejection",
    "tests/integration/test_activity_schema.py::"
    "test_revise_day_plan_schema_inlines_goal_with_required_nullable_remind_time",
    "tests/integration/test_grounded_targets.py::test_day_start_offers_the_normal_tools_and_readonly_keeps_its_gate",
    "tests/integration/test_env_lifecycle_coverage.py::test_full_round_drives_engine_interfaces",
]

#: 阶段 5 完成的「常驻提示」行（第六章验收）：两阶段同一节「怎样过一天」、只读不含；
#: 「路径可发现、选择有据」在阶段 8 的真实复验中观察。
_RESIDENT_PROMPT_TESTS = [
    "tests/integration/test_life_agent_protocol.py::"
    "test_how_to_live_a_day_is_one_resident_section_of_both_decision_stages",
]

#: 阶段 5 预先登记：提醒与活动结束同秒、零点计划与决策同秒、终点为零点时无 run_failed。
_NO_NEW_FAULT_TESTS += [
    _PLANNING + "test_sleep_ending_as_planned_at_the_reminder_keeps_planned_end",
    _PLANNING + "test_midnight_settles_the_old_day_then_plans_then_judges_the_second",
    _PLANNING + "test_run_ending_at_midnight_runs_no_plan_day_and_nothing_follows_run_ended",
]

#: 阶段 7 对「本机运行修复」行的收尾登记：查看器按最终事件形状显示（经 Node 执行 tests/tools/run_viewer.test.cjs：
#: 动词与方式、结束活动的记忆条数、结束原因、零点计划与活动控制的环境事件、拒绝行的同刻轮次、决策的唤醒原因、
#: 地点会话与零点计划模型请求）。
_STAGE7_LOCAL_RUN_FIX_TESTS = [
    "tests/tools/test_run_viewer.py::test_run_viewer_recorded_names",
]

_RERUN = "tests/life/test_rerun_metrics.py::"

#: 阶段 7 对「真实复验」行（阶段 8 只报告）的预先登记：inspect_run 的 rerun 子命令在合成运行库上只读计算八项指标。
_RERUN_TESTS = [
    _RERUN + "test_rerun_metrics_on_a_real_engine_day",
    _RERUN + "test_rerun_metrics_on_a_stored_run_with_gaps_driving_only_and_missed_meals",
    _RERUN + "test_rerun_command_reports_not_applicable_metrics_for_a_run_without_records",
]

#: 阶段 7 对「无新增故障」行的收尾登记：台账所列情形中此前只登记在其他行的几种，补入本行，使每种情形在本行都有用例——
#: 能量为 0（出行、工作与恢复照常受理）、对象全被挡（工作、驾车、浏览进行中交互对象层为空时决策继续）、
#: 出发与到达同秒（到达当秒出行与路上的活动一并结束并切换会话，收尾前时钟不推进）、同轮可见名称重复（同名活动追加序号）。
_STAGE7_NO_NEW_FAULT_TESTS = [
    "tests/life/test_energy_feasibility.py::test_zero_energy_travel_and_work_are_accepted",
    "tests/life/test_energy_feasibility.py::test_zero_energy_resume_is_accepted",
    _AGENT_BRANCHES + "test_empty_interactive_layer_decision_continues_without_failure",
    "tests/life/test_grounded_observation.py::test_real_observation_during_active_activity_keeps_decision_open",
    _LOCATION + "test_departure_and_arrival_switch_sessions_and_end_road_activities",
    _SAME_MOMENT + "test_pending_conclusion_rejects_other_requests_until_concluded_within_limit_plus_three",
    _LOCATION + "test_same_named_activities_get_ordinals_instead_of_failing",
]

#: 阶段 7 完成的「文档一致性」行：文档门禁在真实仓库上通过；各阶段登记的文档符号（旧工具面、生活决策的 finish、
#: 能量闸门、一秒边界等表述已删，新条款已写入）由本守卫逐条判定。冻结条款修订与文档同步作业的逐条核对、
#: 「标题」引用与锚点的一次性核对结果写在本特性验收记录（docs/VERIFICATION.md#verification-时间与能量特性门禁）。
_DOCUMENT_CONSISTENCY_TESTS = [
    "tests/test_documentation_gate.py::test_real_repository_documents_and_source_references_resolve",
    "tests/test_time_and_energy_completeness.py::test_removed_symbols_of_done_stages_are_absent",
]

#: 阶段 7 完成的「工程门禁」行：风格门禁零新增违规、文档门禁通过、覆盖率门禁的阈值与未覆盖公共接口判定、查看器回归；
#: 覆盖率门禁在最终代码上的运行结果（重新生成并提交的 docs/dev/coverage-report.json）写在本特性验收记录。
_ENGINEERING_GATE_TESTS = [
    "tests/test_style_gate.py::test_style_gate_admits_no_new_violations",
    "tests/test_documentation_gate.py::test_real_repository_documents_and_source_references_resolve",
    "tests/test_coverage_gate.py::test_branch_threshold_boundary",
    "tests/test_coverage_gate.py::test_uncovered_public_interface_detection",
    "tests/tools/test_run_viewer.py::test_run_viewer_recorded_names",
]

#: 验收台账行（首列原文）→ 归属阶段与证据测试节点。
ACCEPTANCE: dict[str, dict[str, object]] = {
    "绿色基线": {"stage": "0", "tests": _GREEN_BASELINE_TESTS},
    "同刻决策": {"stage": "4", "tests": _SAME_MOMENT_TESTS},
    "候选时刻": {"stage": "2", "tests": _CANDIDATE_TIME_TESTS + _STAGE2_CANDIDATE_TIME_TESTS},
    "唤醒词表": {"stage": "2", "tests": _WAKE_VOCABULARY_TESTS + _STAGE2_WAKE_VOCABULARY_TESTS},
    "能量感知": {"stage": "2", "tests": _STAGE2_ENERGY_PERCEPTION_TESTS},
    "能量不拦人": {"stage": "4", "tests": _STAGE2_ENERGY_NEVER_BLOCKS_TESTS + _STAGE4_ENERGY_NEVER_BLOCKS_TESTS},
    "零点计划与提醒": {"stage": "5", "tests": _DAY_PLANNING_TESTS},
    "五个动词与并行": {"stage": "4", "tests": _FIVE_VERBS_TESTS},
    "常驻提示": {"stage": "5", "tests": _RESIDENT_PROMPT_TESTS},
    "会话、活动与记忆": {"stage": "4", "tests": _SESSIONS_AND_MEMORY_TESTS},
    "本机运行修复": {"stage": "7",
               "tests": _RUN_IDENTITY_TESTS + _STAGE2_LOCAL_RUN_FIX_TESTS + _LOCAL_RUN_FIX_TESTS
               + _STAGE4_LOCAL_RUN_FIX_TESTS + _STAGE7_LOCAL_RUN_FIX_TESTS},
    "交付": {"stage": "3", "tests": _DELIVERY_TESTS},
    "无新增故障": {"stage": "7",
              "tests": _NO_NEW_FAULT_TESTS + _STAGE2_NO_NEW_FAULT_TESTS + _STAGE4_NO_NEW_FAULT_TESTS
              + _STAGE7_NO_NEW_FAULT_TESTS},
    "真实复验": {"stage": "8", "tests": _RERUN_TESTS},
    "文档一致性": {"stage": "7", "tests": _DOCUMENT_CONSISTENCY_TESTS},
    "工程门禁": {"stage": "7", "tests": _ENGINEERING_GATE_TESTS},
}

#: 阶段 → 完成后必须从列出路径中消失的符号（正则, 路径或目录列表）。
REMOVED_SYMBOLS: dict[str, list[str]] = {
    "1": ["max_consecutive_rejections", "maximum_rejections", "_after_unknown", "_put_agent_and_defer_feedback"],
    "2": ["INSUFFICIENT_ENERGY", "energy_feasibility", "ELIGIBILITY_ENERGY_INSUFFICIENT", "_require_energy_plan",
          "energy_check", "_remaining_activity_energy", "validate_activity_energy", "active_energy_entries",
          "combined_energy_cost", "maximum_combined_duration", "shared_energy_cost", "maximum_shared_energy_duration",
          "energy_projection", "energy_feasible", "minimum_energy_cost_kcal", "resume_energy_feasible"],
    "3": ["FinishRequest", "LifeMemoryEpisode", "MemoryReadRequest", "_validate_finish", "memory_read",
          "_life_visible_refs", "_environment_source_ids", "_bind_finish_reference_ids", "decision_observation",
          "finish_without_memory_update", "memory_update_without_successful_finish", "unknown_memory_reference",
          "session_name", "current_sessions", "_session_sentences", "_active_session_scene_rows",
          "_validate_visible_binding_names", "compact_by_task", "_terminal_sessions", "invested_seconds",
          "preconditions_unmet", "_remember_terminal_activity", "_terminal_after_action"],
    "4": ["energy_limit_kinds", "ACTION_LABELS",
          "preconditions_met", "chat_settlement", "SocialRelief", "chat_accepted", "CONTACT_REPLY_NOT_MODEL",
          "CALL_DURATION_OUT_OF_RANGE", "_OBSERVATION_CACHE_SIZE", "_frozen_observations", "_menu_actions",
          "_resolve_interaction_bindings"],
    "5": ["_DAY_START_WAKE_REASONS", "_plan_required_before_other_actions", "_visible_plan_day_contract", "_plan_day",
          "PLAN_ALREADY_SUBMITTED", "based_on_action_id"],
}

#: 已删除函数仅按真实定义检查，现有字典字段 canonical_action 保留。
REMOVED_DEFINITIONS = frozenset({"canonical_action", "action_label", "purchase_action_label"})
