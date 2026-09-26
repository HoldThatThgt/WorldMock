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
STAGES_DONE = frozenset({"0", "1", "3"})

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
        "test_sleeping_recipient_is_not_woken_by_person_message",
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
]

#: 阶段 1 预先登记：候选时刻行由阶段 2 完成（困意、饥饿预测与餐时窗口）。
_CANDIDATE_TIME_TESTS = [
    _SAME_MOMENT + "test_first_decision_includes_reconsideration_candidate",
    _SAME_MOMENT + "test_idle_interval_only_while_waiting_without_commitments",
    "tests/life/test_wake_reasons.py::test_commitments_exclude_messages_history_and_completed_activities",
]

#: 阶段 1 预先登记：唤醒词表行由阶段 2 完成（activity_infeasible 不再产生、commitment_threatened 的来源）。
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

_LOCATION = "tests/life/test_location_sessions.py::"
_PROGRESS_LAYER = "tests/life/test_progress_layer_projection.py::"
_MEMORIES = "tests/integration/test_conclude_memories.py::"
_AGENT_BRANCHES = "tests/integration/test_agent_branch_coverage.py::"

#: 阶段 3 预先登记：同刻决策行中依赖名额与待收尾的各支（受限轮的收尾、恢复或结束与只开放结束）。
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
    "tests/life/test_export_integration.py::test_lifecycle_records_and_mid_run_export_match_the_final_checkpoint",
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

#: 验收台账行（首列原文）→ 归属阶段与证据测试节点。
ACCEPTANCE: dict[str, dict[str, object]] = {
    "绿色基线": {"stage": "0", "tests": _GREEN_BASELINE_TESTS},
    "同刻决策": {"stage": "4", "tests": _SAME_MOMENT_TESTS},
    "候选时刻": {"stage": "2", "tests": _CANDIDATE_TIME_TESTS},
    "唤醒词表": {"stage": "2", "tests": _WAKE_VOCABULARY_TESTS},
    "能量感知": {"stage": "2", "tests": []},
    "能量不拦人": {"stage": "4", "tests": []},
    "零点计划与提醒": {"stage": "5", "tests": []},
    "五个动词与并行": {"stage": "4", "tests": []},
    "常驻提示": {"stage": "5", "tests": []},
    "会话、活动与记忆": {"stage": "4", "tests": _SESSIONS_AND_MEMORY_TESTS},
    "本机运行修复": {"stage": "7", "tests": _LOCAL_RUN_FIX_TESTS},
    "交付": {"stage": "3", "tests": _DELIVERY_TESTS},
    "无新增故障": {"stage": "7", "tests": _NO_NEW_FAULT_TESTS},
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
}
