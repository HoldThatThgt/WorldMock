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
STAGES_DONE = frozenset({"0", "1", "2", "3", "5", "6"})

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

_LOCATION = "tests/life/test_location_sessions.py::"
_PROGRESS_LAYER = "tests/life/test_progress_layer_projection.py::"
_MEMORIES = "tests/integration/test_conclude_memories.py::"
_AGENT_BRANCHES = "tests/integration/test_agent_branch_coverage.py::"

#: 阶段 3 预先登记：同刻决策行中依赖名额与待收尾的各支（受限轮的收尾、恢复或结束与只开放结束；只能收尾的轮次
#: 不消耗人物消息的机会）。
_SAME_MOMENT_TESTS += [
    _LOCATION + "test_just_ended_requires_conclusion_in_the_same_second",
    _LOCATION + "test_two_slots_full_block_new_activities_until_conclusion",
    _LOCATION + "test_both_paused_redecide_same_second_until_one_resumes",
    _AGENT_BRANCHES + "test_conclude_pending_stage_requires_concluding_every_just_ended_activity",
    _AGENT_BRANCHES + "test_resume_or_conclude_stage_offers_resume_or_conclusion_only",
    _AGENT_BRANCHES + "test_conclude_only_stage_offers_only_conclusion_of_slot_activities",
    _AGENT_BRANCHES + "test_paused_activity_elsewhere_is_not_resumable_in_restricted_rounds",
] + [
    _SAME_MOMENT + name for name in (
        "test_pending_conclusion_rejects_other_requests_until_concluded_within_limit_plus_three",
        "test_conclude_pending_stage_accepts_only_concluding_every_pending_activity",
        "test_both_slots_paused_open_resume_or_conclude",
        "test_other_request_in_resume_or_conclude_narrows_to_conclude_only_then_wait_only",
        "test_rejected_resume_with_full_slots_opens_conclude_only_then_wait_only",
        "test_restricted_conclusions_endings_and_waits_pass_the_stage_check",
        "test_conclusion_and_slot_branches_of_the_ladder",
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
        "test_recall_by_task_activity_and_session_names_reaches_the_next_observation",
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
    "tests/life/test_experience_sentences.py::test_observation_experience_sentences_follow_the_private_window",
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
    _PROGRESS_LAYER + "test_recall_reads_task_activity_and_session_without_identities",
    _PROGRESS_LAYER + "test_conclude_marks_named_just_ended_activities_and_keeps_their_status",
    _PROGRESS_LAYER + "test_sessions_are_ordered_by_start_and_group_activities_under_their_latest_session",
    "tests/life/test_narration.py::TestFieldBlacklist::test_progress_layer_activity_rows_render",
    "tests/life/test_narration.py::TestFieldBlacklist::test_activities_outside_progress_sessions_are_still_rejected",
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
    "tests/life/test_narration.py::TestRenderScene::test_scene_never_renders_session_rows_or_self_reports",
    _LOCATION + "test_self_report_stays_out_of_the_scene_and_is_marked_unverified",
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
    _LOCATION + "test_cross_day_duplicate_session_and_activity_names_get_ordinals",
    _LOCATION + "test_arrival_denied_fails_travel_and_starts_stay_at_destination",
    _LOCATION + "test_ending_travel_midway_ends_road_activities_and_stays_at_origin",
    _LOCATION + "test_sessions_and_pending_conclusion_survive_reopen",
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
    "tests/footprints/test_store.py::test_progress_request_cutoff_skips_system_requests_with_a_purpose",
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

#: 阶段 5 完成的「常驻提示」行（第六章验收）：两阶段同一节「怎样过一天」、只读不含、禁用词与中文句子占比照旧；
#: 「路径可发现、选择有据」在阶段 8 的真实复验中观察。
_RESIDENT_PROMPT_TESTS = [
    "tests/integration/test_life_agent_protocol.py::"
    "test_how_to_live_a_day_is_one_resident_section_of_both_decision_stages",
    "tests/integration/test_life_agent_protocol.py::test_system_prompt_is_identity_first_with_word_share",
]

#: 阶段 5 预先登记：提醒与活动结束同秒、零点计划与决策同秒、终点为零点时无 run_failed。
_NO_NEW_FAULT_TESTS += [
    _PLANNING + "test_sleep_ending_as_planned_at_the_reminder_keeps_planned_end",
    _PLANNING + "test_midnight_settles_the_old_day_then_plans_then_judges_the_second",
    _PLANNING + "test_run_ending_at_midnight_runs_no_plan_day_and_nothing_follows_run_ended",
]

#: 验收台账行（首列原文）→ 归属阶段与证据测试节点。
ACCEPTANCE: dict[str, dict[str, object]] = {
    "绿色基线": {"stage": "0", "tests": _GREEN_BASELINE_TESTS},
    "同刻决策": {"stage": "4", "tests": _SAME_MOMENT_TESTS},
    "候选时刻": {"stage": "2", "tests": _CANDIDATE_TIME_TESTS + _STAGE2_CANDIDATE_TIME_TESTS},
    "唤醒词表": {"stage": "2", "tests": _WAKE_VOCABULARY_TESTS + _STAGE2_WAKE_VOCABULARY_TESTS},
    "能量感知": {"stage": "2", "tests": _STAGE2_ENERGY_PERCEPTION_TESTS},
    "能量不拦人": {"stage": "4", "tests": _STAGE2_ENERGY_NEVER_BLOCKS_TESTS},
    "零点计划与提醒": {"stage": "5", "tests": _DAY_PLANNING_TESTS},
    "五个动词与并行": {"stage": "4", "tests": []},
    "常驻提示": {"stage": "5", "tests": _RESIDENT_PROMPT_TESTS},
    "会话、活动与记忆": {"stage": "4", "tests": _SESSIONS_AND_MEMORY_TESTS},
    "本机运行修复": {"stage": "7",
               "tests": _RUN_IDENTITY_TESTS + _STAGE2_LOCAL_RUN_FIX_TESTS + _LOCAL_RUN_FIX_TESTS},
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
    "3": [
        (r"class FinishRequest|LifeMemoryEpisode|MemoryReadRequest|_validate_finish\b", ["custom"]),
        (r'"memory_read"', ["src", "custom"]),
        (r"_life_visible_refs|_episode_ids\(|_environment_source_ids|_bind_finish_reference_ids", ["custom"]),
        (r"decision_observation", ["src", "custom"]),
        (r"finish_without_memory_update|memory_update_without_successful_finish|unknown_memory_reference",
         ["src"]),
        (r"_response_tool_calls|_memory_references\(|references_checked", ["src/worldmock/footprints/quality.py"]),
        (r"\bsession_name\b|\bcurrent_sessions\b|_session_sentences|_active_session_scene_rows", ["src", "custom"]),
        (r"_validate_visible_binding_names", ["src/worldmock/life/observe.py"]),
        (r"compact_by_task|_terminal_sessions", ["src/worldmock/life/prompt_capacity.py"]),
        (r"SessionStatus\.(?:PAUSED|CONCLUDED|ABORTED)|invested_seconds|preconditions_unmet", ["src", "custom"]),
        (r"_remember_terminal_activity|_terminal_after_action", ["src"]),
        (r'"purchase" in actions', [_ENGINE, _AGENT]),
        (r'_SAME_MOMENT_TOOLS|"(?:conclude_pending|start_or_resume|resume_or_conclude|conclude_only|wait_only)"',
         [_AGENT]),
        (r"finish\.memories 仍为零至八条|会话退出通道|\| refs \|", [_CONTRACTS]),
        (r"动作后为记忆查询与 `finish`", [_CONTRACTS]),
    ],
    "5": [
        (r'"plan_day"', [_AGENT, "custom/envs/life_env.py", _MODELS, _ENGINE]),
        (r"_DAY_START_WAKE_REASONS|_plan_required_before_other_actions|_visible_plan_day_contract", ["custom"]),
        (r"def _plan_day\b|PLAN_ALREADY_SUBMITTED", ["src", "custom"]),
        (r"based_on_action_id|Do not provide times or durations", [_AGENT]),
        (r"九个工具|九个人物工具", ["custom/envs/life_env.py", _AGENT]),
        (r"\| `PLAN_ALREADY_SUBMITTED` \||\| `plan_day` \| `goals:|九个模型工具名", [_CONTRACTS]),
        (r"\| `plan_day`、`revise_day_plan` \|", [_CONTRACTS]),
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
        # 目标回看按意图解析；阶段 3 把回看改写为按任务、目标、活动与会话分派后，判定仍按 intent_kind。
        (r'item\.intent_kind == row\["intent_kind"\]', [_SESSION_ACTIONS]),
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
    "3": [
        (r"SLOT_LIMIT = 2\b", ["src/worldmock/life/activity_control.py"]),
        (r"ACTIVITY_CONCLUSION_REQUIRED", ["src/worldmock/life/activity_control.py", _CONTRACTS]),
        (r"ACTIVITY_PAUSE_UNAVAILABLE", [_SESSION_ACTIONS, _CONTRACTS]),
        (r"SESSION_LIMIT_EXCEEDED", ["src/worldmock/life/activity_control.py", _CONTRACTS]),
        (r"class SessionKind", [_MODELS]),
        (r"ROUTE_ENDED = \"route_ended\"", ["src/worldmock/life/sessions.py"]),
        (r"route_ended", [_CONTRACTS, _IO_FORMATS]),
        (r"concluded_at", [_MODELS, _IO_FORMATS, _RECORDING]),
        (r"report_anchor_at", [_MODELS, "src/worldmock/life/rhythm.py", "src/worldmock/life/activities.py",
                               "docs/dev/SPEC-life-runtime.md"]),
        (r"memories: Annotated\[", [_MODELS]),
        (r"memory_sources", [_SESSION_ACTIONS, _CONTRACTS, _RECORDING]),
        (r"MEMORY_WRITE_POINTS = \(\"activity_conclusion\", \"reflection\"\)", ["src/worldmock/life/memory_evidence.py"]),
        (r"conclusion_without_memory_update", ["src/worldmock/footprints/quality.py", _RECORDING]),
        (r"memory_update_without_source", ["src/worldmock/footprints/quality.py", _RECORDING]),
        (r"activity_concluded", ["src/worldmock/life/activity_control.py", _RECORDING,
                                 "docs/dev/SPEC-agent-interaction.md"]),
        (r"activity_truncated", ["src/worldmock/life/activity_control.py", _RECORDING,
                                 "docs/dev/SPEC-agent-interaction.md", _LIFE_RUNTIME]),
        (r"async def _run_react_turn", [_AGENT]),
        (r"_consolidation_due", ["custom/agents/life_memory.py"]),
        (r"pending_conclusions", ["src/worldmock/life/projection.py", _CONTRACTS]),
        (r"just_ended", ["src/worldmock/life/projection.py", "src/worldmock/life/narration.py", _CONTRACTS]),
        (r"ALLOWED_NESTED_KEYS", ["src/worldmock/life/narration.py"]),
        (r"def request_kind", ["src/worldmock/life/same_moment.py"]),
        (r"STAGE_TOOLS", ["src/worldmock/life/same_moment.py", _AGENT]),
        (r'!= "memories"', ["src/worldmock/delivery/windows.py"]),
        (r"\| `sessions` \| dict\[str, SessionState\]", [_IO_FORMATS]),
        (r"\| 地点会话 \| `sessions`", [_CONTRACTS]),
        (r"def test_", ["tests/life/test_location_sessions.py", "tests/integration/test_conclude_memories.py"]),
    ],
    "5": [
        (r"async def settle_pending_day_plans\(engine: Any, client: Any \| None = None\)", [_RUNTIME]),
        (r"class PlanDayClient", [_RUNTIME]),
        (r"settle_pending_day_plans", ["custom/envs/life_env.py"]),
        (r"def register_day_plan|def decide_midnight_rhythm", [_ENGINE]),
        (r'PLAN_DAY_PURPOSE = "plan_day"', ["src/worldmock/life/day_planning.py"]),
        (r"PLAN_DAY_VALIDATION_RETRIES = 3\b", ["src/worldmock/life/day_planning.py"]),
        (r'"kind": "day_plan_generated"', ["src/worldmock/life/day_planning.py"]),
        (r"day_plan_generated", ["src/worldmock/evaluation/goal_metrics.py"]),
        (r"PLAN_REMIND_TIME_INVALID", [_ENGINE]),
        (r"remind_at: int \| None|reminded_at: int \| None", [_SCHEDULE]),
        (r"remind_time: Annotated\[str, Field\(pattern=", [_MODELS]),
        (r"plan_reminder_times", [_SCHEDULE, _RHYTHM]),
        (r'"plan_reminder"', ["src/worldmock/life/activities.py"]),
        (r"def boundary_candidates|def defers_rhythm|def alarm_goals", ["src/worldmock/life/reminders.py"]),
        (r"怎样过一天：", [_AGENT]),
        (r"\$\.data\.purpose'\) IS NULL", ["src/worldmock/footprints/store.py"]),
        (r'not event\.get\("data", \{\}\)\.get\("purpose"\)', ["src/worldmock/evaluation/behavior_metrics.py"]),
        (r'"by_purpose"', ["src/worldmock/footprints/quality.py"]),
        (r"def test_", ["tests/life/test_day_planning.py"]),
        (r"PLAN_REMIND_TIME_INVALID", [_CONTRACTS]),
        (r"day_plan_generated", [_CONTRACTS, _RECORDING, "docs/dev/SPEC-agent-interaction.md"]),
        (r"怎样过一天", [_CONTRACTS]),
        (r"submit_day_plan", [_CONTRACTS]),
        (r"2026-09-25-time-and-energy-stage-5", [_CONTRACTS, _LIFE_RUNTIME]),
        (r"remind_at", [_IO_FORMATS]),
        (r"plan_submitted_dates", [_IO_FORMATS, _MANUAL_RUNTIME]),
        (r"plan_reminder_due", [_LIFE_RUNTIME]),
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
