"""人物 Prompt 七层规格的验收登记表（完整性守卫的数据源）。

@file     persona_prompt_acceptance.py
@brief    把 docs/dev/SPEC-persona-prompt.md 的「验收台账」各行、第六章两张表（七项程序错误与五项缺口）与「裁决记录」
          第二张表（#21–#38，按推荐定为已定）逐行登记到真正验证它的测试节点；并冻结本特性新增或改动的生产文件与新增测试文件，
          供延期标记扫描。判定由 tests/test_persona_prompt_acceptance.py 执行。
@note     登记的节点是走真实引擎、宿主或导出路径的行为测试，不是结构匹配；一行列多个节点时各覆盖该行的一部分契约。
          「真实复验（只报告）」按规格只报告、不作门禁：真实模型运行需要用户的模型账号，不在测试中执行；登记的是它所用的
          只读子命令 `scripts/inspect_run.py continuation` 的测试（REPORT_ONLY 写明）。
          「文档一致性」的文档同步作业属于实施计划阶段 8（实现后文档），登记的是在真实仓库上运行文档链接门禁的测试；
          「工程门禁」登记风格门禁与覆盖率、文档门禁本身的测试，覆盖率报告的重新生成与提交属于实施计划阶段 6。
          「把没有订单当作没有使用应用」中的报告措辞在数据集仓库（规格「范围外登记」），本仓库登记购买事实的三种口径。
"""

from __future__ import annotations

_SEVEN = "tests/integration/test_seven_layer_request.py::"
_CONTINUATION = "tests/integration/test_same_second_continuation.py::"
_AGENT = "tests/integration/test_life_agent_protocol.py::"
_BRANCHES = "tests/integration/test_agent_branch_coverage.py::"
_CONCLUDE = "tests/integration/test_conclude_memories.py::"
_DESCRIPTIONS = "tests/integration/test_tool_description_references.py::"
_PROTOCOL = "tests/integration/test_decision_context_protocol.py::"
_GROUNDED = "tests/integration/test_grounded_evidence.py::"
_STOREFRONT = "tests/integration/test_storefront_tools.py::"
_MENU = "tests/integration/test_interaction_menu.py::"
_MANIFEST = "tests/integration/test_run_manifest.py::"
_LAYERS = "tests/life/test_prompt_layers.py::"
_PROGRESS = "tests/life/test_progress_events.py::"
_MEMORY = "tests/life/test_memory_layer.py::"
_PERSONA = "tests/life/test_persona_layer.py::"
_TOOL = "tests/life/test_tool_layer.py::"
_ENGINE = "tests/life/test_engine_branch_coverage.py::"
_SAME_MOMENT = "tests/life/test_same_moment_decisions.py::"
_BUDGET = "tests/life/test_decision_context_projection.py::"
_MEAL = "tests/life/test_meal_outcome.py::"
_INVENTORY = "tests/life/test_dayrun_inventory_regressions.py::"
_NAMED = "tests/life/test_named_purchase.py::"
_GIVING = "tests/life/test_giving.py::"
_LINKING = "tests/life/test_request_linking.py::"
_CONTACTS = "tests/life/test_contact_exchange.py::"
_WORK = "tests/life/test_work_contracts.py::"
_EXHAUSTED = "tests/life/test_decision_budget.py::"
_READABLE = "tests/life/test_readable.py::"
_LOCATION = "tests/life/test_location_sessions.py::"
_METRICS = "tests/life/test_continuation_metrics.py::"
_IDENTITY = "tests/life/test_restored_model_identity.py::"
_BLACKLIST = "tests/life/test_narration.py::TestFieldBlacklist::"
_START = "tests/test_start_activity_contract.py::"
_PURCHASES = "tests/footprints/test_purchases.py::"
_EVENTS = "tests/footprints/test_event_fields.py::"
_QUALITY = "tests/footprints/test_quality.py::"
_WINDOWS = "tests/delivery/test_windows.py::"
_BEHAVIOR = "tests/evaluation/test_behavior_metrics.py::"
_DOC_GATE = "tests/test_documentation_gate.py::test_real_repository_documentation_links_resolve"
_ZERO_RECENT = "tests/integration/test_verified_memory.py::test_zero_recent_memory_limit_returns_no_recent_entries"

#: 第六章「程序错误」表逐行：每行的回归用例（把修复改回旧代码时用例失败）。
PROGRAM_ERRORS: dict[str, list[str]] = {
    "直接消息截断，没有全文入口": [
        _PROGRESS + "test_direct_message_is_presented_in_full_and_marked_presented",
        _PROGRESS + "test_round_commit_reads_only_messages_presented_in_full",
        _SAME_MOMENT + "test_read_observed_messages_marks_only_presented_messages_of_open_rounds",
        _ENGINE + "test_presented_messages_come_from_the_decision_observation",
        _LAYERS + "test_direct_message_and_invite_text_appear_only_in_the_progress_layer",
        _MEMORY + "test_unanswered_message_crosses_midnight_counts_as_presented_and_a_reply_resolves_it",
    ],
    "恢复工作越过合同截止": [
        _WORK + "test_resume_plans_the_end_no_later_than_the_contract_deadline",
        _WORK + "test_work_ending_at_the_deadline_is_concluded_then_submitted_and_paid_once",
        _WORK + "test_resume_with_less_than_a_minute_to_the_deadline_is_a_business_rejection",
        _TOOL + "test_resume_within_a_minute_of_the_deadline_is_unavailable_with_the_admission_reason",
    ],
    "工时达标与提交状态不一致": [
        _WORK + "test_target_met_while_still_working_states_the_three_facts_separately",
        _TOOL + "test_submit_while_working_says_finish_the_work_first",
        "tests/life/test_work_review.py::test_paused_work_keeps_progress_and_has_an_independent_todo",
    ],
    "逾期工作从观察和工具中消失": [
        _WORK + "test_overdue_task_stays_listed_until_the_end_of_its_day_and_submission_is_a_business_rejection",
        _ENGINE + "test_visible_task_rows_keep_overdue_tasks_until_the_end_of_their_day",
    ],
    "不足一份的食物无法消费": [
        _MEAL + "test_less_than_one_portion_is_eaten_for_a_scaled_duration_and_energy",
        _MEAL + "test_meal_duration_is_validated_against_the_scaled_portion",
        _INVENTORY + "test_irregular_meal_progress_matches_one_settlement",
        _INVENTORY + "test_meal_rounding_tolerance_eats_only_the_opened_quantity",
    ],
    "混合动作的购买记录遗漏": [
        _PURCHASES + "test_mixed_eat_and_buy_in_progress_result_counts_its_order_once",
        _PURCHASES + "test_replayed_receipt_is_not_counted_again",
        _PURCHASES + "test_export_writes_purchase_facts_under_manifest_version_two",
        _PURCHASES + "test_validation_recomputes_purchase_facts_and_rejects_tampering",
    ],
    "零条最近记忆失效": [_ZERO_RECENT],
}

#: 第六章「缺口」表逐行。
GAPS: dict[str, list[str]] = {
    "进食提前结束只显示「已收尾」": [
        _MEAL + "test_ending_a_meal_at_300_of_1500_seconds_eats_0_2_discards_0_8_and_keeps_3",
        _MEAL + "test_conclusion_outcome_names_failure_and_skips_other_activities",
    ],
    "提示菜单与受限工具不一致": [
        _TOOL + "test_every_listed_item_matches_admission_at_run_start",
        _TOOL + "test_restricted_round_marks_closed_items_with_the_same_moment_reason",
        _TOOL + "test_pending_conclusion_marks_everything_but_conclusion_unavailable",
        _BRANCHES + "test_restricted_rounds_do_not_narrow_the_host_tool_surface",
        _SAME_MOMENT + "test_start_or_resume_schema_matches_engine_dispatch",
    ],
    "未完成浏览被写成采购完成": [
        _MEAL + "test_unfinished_browse_concludes_without_a_browse_result",
        _MEMORY + "test_unfinished_browse_writes_no_purchase_sentence",
    ],
    "联系人回复缺少双方历史": [
        _CONTACTS + "test_messages_to_contacts_go_to_the_outbox_and_the_reply_request_carries_both_sides",
        _CONTACTS + "test_reply_history_keeps_the_latest_twenty_messages_in_time_order",
        _CONTACTS + "test_received_lists_delivered_orders_and_gifts_to_the_contact",
        "tests/integration/test_host_branch_coverage.py::test_prepare_contact_reply_builds_disabled_thinking_request",
        "tests/life/test_social.py::test_model_reply_request_and_finalize_validate_200_chars",
    ],
    "把没有订单当作没有使用应用": [
        _PURCHASES + "test_browse_only_counts_as_app_browse",
        _PURCHASES + "test_rejected_order_counts_as_order_attempt",
        _PURCHASES + "test_mixed_eat_and_buy_in_progress_result_counts_its_order_once",
    ],
}

#: 「验收台账」逐行：首列契约点 → 验证它的测试节点。「程序错误」行取第六章七行的全部回归用例。
LEDGER: dict[str, list[str]] = {
    "七层请求": [
        _SEVEN + "test_user_content_is_one_json_array_in_the_fixed_layer_order",
        _SEVEN + "test_system_layers_persona_memory_and_tools_validate_against_the_layer_schemas",
        _AGENT + "test_react_messages_are_system_layer_and_six_layer_user",
        _AGENT + "test_how_to_live_a_day_is_one_resident_section_of_both_decision_stages",
        _AGENT + "test_blacklisted_observation_fields_never_enter_messages",
        _GROUNDED + "test_model_request_persists_exact_grounded_tools",
        _CONCLUDE + "test_nothing_sent_to_the_person_model_carries_an_internal_identity",
        _LAYERS + "test_schema_enums_are_literal_copies_of_the_source_closed_sets",
        _LAYERS + "test_memory_layer_and_persona_views_are_empty_at_run_start",
        _LAYERS + "test_env_layers_validate_through_purchase_rejection_pause_travel_and_arrival",
        "tests/life/test_revision_contract.py::test_public_observation_excludes_private_fields_and_supply_expires",
        "tests/life/test_grounded_observation.py::test_observation_keeps_current_time_without_plan_conversion_anchors",
        _READABLE + "test_iso_time_and_local_date_use_the_scene_timezone_with_offset",
        _READABLE + "test_duration_phrase_rounds_minutes_then_half_hours",
        _READABLE + "test_money_and_portion_phrases_keep_at_most_two_decimals",
    ],
    "唯一出现": [
        _LAYERS + "test_direct_message_and_invite_text_appear_only_in_the_progress_layer",
        _LAYERS + "test_paused_activity_progress_appears_only_in_the_todo_layer",
        _LAYERS + "test_work_contract_appears_only_in_the_task_target",
        _LAYERS + "test_browse_supplies_live_in_the_tool_layer_and_the_result_lists_names_only",
        _LAYERS + "test_just_ended_browse_target_states_the_end_reason_without_the_browse_result",
        _PROGRESS + "test_conclude_arguments_omit_relationship_and_preference_memories",
        _PROGRESS + "test_successful_memory_query_is_recorded_with_its_count_and_failures_are_not",
        _CONCLUDE + "test_memory_queries_and_the_reply_body_reach_the_next_progress_layer",
        _MEMORY + "test_departure_and_arrival_attribute_same_second_calls_to_the_right_session",
        _LOCATION + "test_self_report_stays_out_of_the_tool_layer_and_is_only_a_call_argument",
        _MEMORY + "test_recalled_summary_still_in_the_memory_layer_is_not_repeated",
    ],
    "系统层与工具": [
        _SEVEN + "test_system_layer_is_byte_identical_across_turns_and_decisions",
        _SEVEN + "test_system_layers_are_the_contract_registration_verbatim",
        _SEVEN + "test_native_tools_are_stable_purpose_only_and_drop_memory_only_on_the_last_turn",
        _CONTINUATION + "test_the_continued_request_starts_with_the_previous_request_byte_for_byte",
        _MENU + "test_actual_tool_schema_is_stable_whatever_the_menu",
        _BRANCHES + "test_restricted_rounds_do_not_narrow_the_host_tool_surface",
        _DESCRIPTIONS + "test_every_native_tool_has_a_purpose_only_description",
        _DESCRIPTIONS + "test_interaction_rules_live_only_in_the_system_layer",
        _DESCRIPTIONS + "test_description_scan_has_teeth",
        _DESCRIPTIONS + "test_parameter_descriptions_are_purpose_only",
    ],
    "单一行动阶段": [
        _SEVEN + "test_a_decision_without_memory_queries_sends_exactly_one_request_and_no_prelude",
        _SEVEN + "test_decision_started_is_committed_with_and_before_the_first_action_request",
        _SEVEN + "test_non_action_requests_leave_the_start_to_the_engine_driven_fallback",
        _SEVEN + "test_abort_flushes_the_pending_start_without_model_evidence",
        _EVENTS + "test_decision_started_data_is_the_closed_four_field_set",
        _EVENTS + "test_retired_decision_statement_field_is_rejected_on_event_drafts",
        _ENGINE + "test_first_action_request_pops_the_pending_start_once",
        _BRANCHES + "test_tool_messages_follow_memory_queries_only",
        _AGENT + "test_native_react_submits_one_host_bound_life_action",
        _PROTOCOL + "test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user",
    ],
    "回复正文": [
        _SEVEN + "test_self_note_is_the_latest_reply_body_of_the_decision",
        _SEVEN + "test_self_note_ignores_other_decisions_and_failed_calls",
        _ENGINE + "test_response_self_note_is_the_stripped_reply_body_or_none",
        _PROGRESS + "test_self_note_is_copied_from_the_request_and_omitted_when_empty",
        _CONCLUDE + "test_memory_queries_and_the_reply_body_reach_the_next_progress_layer",
        "tests/integration/test_decision_context_roundtrip.py::"
        "test_real_context_roundtrip_preserves_evidence_and_delays_results",
        _MEMORY + "test_reply_body_stays_in_its_session_progress_and_never_enters_the_memory_layer",
    ],
    "同秒续接": [
        _CONTINUATION + name for name in (
            "test_the_continued_request_starts_with_the_previous_request_byte_for_byte",
            "test_a_three_decision_chain_keeps_every_prefix_and_reports_each_change_once",
            "test_another_persons_same_second_message_appears_once_in_the_whole_request",
            "test_after_time_advances_the_request_has_no_native_turns_or_earlier_thinking",
            "test_a_same_second_departure_breaks_the_chain",
            "test_a_counterfactual_branch_breaks_the_chain",
            "test_missing_thinking_breaks_the_chain",
            "test_missing_model_evidence_breaks_the_chain",
            "test_recorded_evidence_decides_whether_the_chain_holds",
            "test_a_plain_resume_from_a_mid_second_checkpoint_sends_the_same_request",
            "test_switch_off_every_decisions_first_request_has_exactly_two_messages",
            "test_the_switch_is_frozen_into_the_run_manifest_and_the_agent_profile",
            "test_resume_inherits_the_parent_switch_and_rejects_a_diverged_profile",
            "test_the_pre_send_check_fails_when_the_current_decision_lacks_thinking",
            "test_the_pre_send_check_breaks_a_chain_whose_assistant_lacks_thinking",
        )
    ],
    "可用性": [
        _TOOL + "test_every_listed_item_matches_admission_at_run_start",
        _TOOL + "test_pending_conclusion_marks_everything_but_conclusion_unavailable",
        _TOOL + "test_restricted_round_marks_closed_items_with_the_same_moment_reason",
        _TOOL + "test_road_activity_cannot_be_paused_and_matches_admission",
        _TOOL + "test_held_elsewhere_and_paused_elsewhere_are_listed_with_reasons",
        _SAME_MOMENT + "test_restricted_observation_marks_closed_actions_unavailable",
        _BRANCHES + "test_restricted_rounds_do_not_narrow_the_host_tool_surface",
        _BRANCHES + "test_visible_objects_freeze_verb_way_and_bound_internal_action",
        _AGENT + "test_incomplete_work_stays_submittable_for_business_rejection",
    ],
    "进度层": [
        _PROGRESS + name for name in (
            "test_run_start_session_is_time_bound_and_keeps_every_result",
            "test_started_activity_record_shows_the_latest_result_of_the_request",
            "test_same_second_departure_opens_a_session_bounded_by_its_result",
            "test_arrival_session_is_time_bound_and_excludes_the_departure",
            "test_en_route_conclusion_opens_a_session_bounded_by_the_conclusion_result",
            "test_direct_message_is_presented_in_full_and_marked_presented",
            "test_delivery_appears_once_with_order_label_and_message",
            "test_task_deadline_records_due_soon_and_expired_once_each",
            "test_plan_reminder_appears_once_and_precedes_same_second_events",
            "test_records_after_and_result_object_share_the_progress_projection",
        )
    ] + [
        _CONTINUATION + "test_another_persons_same_second_message_appears_once_in_the_whole_request",
        _SAME_MOMENT + "test_same_second_results_stay_in_the_progress_layer",
        "tests/footprints/test_store.py::"
        "test_action_request_cutoff_counts_only_thinking_requests_with_the_action_tools",
    ],
    "记忆层": [
        _MEMORY + name for name in (
            "test_departure_and_arrival_attribute_same_second_calls_to_the_right_session",
            "test_departure_request_belongs_wholly_to_the_old_session",
            "test_en_route_conclusion_keeps_the_conclusion_in_the_transit_session",
            "test_denied_arrival_ends_the_trip_and_the_failed_trip_is_concluded_in_the_new_stay",
            "test_experiment_end_closes_the_last_session_into_the_memory_layer",
            "test_failures_replays_recalls_queries_and_empty_sessions_leave_no_entry",
            "test_sentences_write_portions_money_and_cross_day_clocks",
            "test_paused_interval_appears_in_the_concluded_activity_sentence",
            "test_unresolved_order_crosses_midnight_into_earlier_days_and_disappears_when_delivered",
            "test_unanswered_message_crosses_midnight_counts_as_presented_and_a_reply_resolves_it",
            "test_new_work_is_a_world_event_sentence",
            "test_reflection_date_is_the_local_date_six_hours_before_sleep_start",
            "test_bedtime_reflection_lands_on_the_date_of_the_sleep_it_follows",
            "test_nap_reflection_stays_out_until_the_next_day",
            "test_window_deletes_oldest_reflections_then_shrinks_oldest_today_entries_and_keeps_exemptions",
            "test_memory_layer_is_byte_identical_after_restore_and_the_cache_only_accelerates",
        )
    ],
    "人设层": [
        _PERSONA + name for name in (
            "test_card_path_renders_card_fields_and_merges_relationships_by_exact_name",
            "test_quarter_hour_times_are_written_as_quarters",
            "test_no_card_person_keeps_the_name_only_path_and_view_only_people_get_an_item",
            "test_six_hundred_hanzi_cap_checks_only_card_derived_fields",
            "test_persona_card_struct_is_saved_on_new_runs_and_validated_on_restore",
            "test_later_view_replaces_earlier_while_events_keep_both",
            "test_first_purchase_writes_no_preference_and_leaves_no_trace_after_delivery_and_midnight",
            "test_consolidation_is_kept_but_its_summary_never_reaches_the_prompt",
        )
    ] + [
        "tests/integration/test_persona_profile.py::"
        "test_frozen_persona_identity_reaches_the_native_agent_prompt_as_the_persona_layer",
        "tests/life/test_persona_upgrade.py::"
        "test_persona_layer_from_the_scenario_profile_reaches_the_native_llm_prompt",
    ],
    "预算": [
        _BUDGET + name for name in (
            "test_below_threshold_returns_the_request_untouched",
            "test_self_notes_are_removed_oldest_first_one_at_a_time_and_protected_notes_stay",
            "test_oldest_unprotected_run_folds_into_one_condensed_record_then_the_next",
            "test_an_adjacent_earlier_condensed_record_is_merged_into_the_next_fold",
            "test_exactly_85_percent_triggers_condensation",
            "test_capacity_failure_keeps_protected_records_and_logs_every_part",
            "test_host_request_condenses_progress_from_observation_metadata",
        )
    ] + [
        _PROGRESS + "test_meta_marks_records_after_the_last_action_request_as_protected_and_writes_lines",
        _CONTINUATION + "test_budget_step_zero_ends_the_continuation_before_any_condensation",
        _CONTINUATION + "test_budget_step_zero_starts_the_decision_from_a_fresh_snapshot_for_every_turn",
        _MEMORY + "test_window_deletes_oldest_reflections_then_shrinks_oldest_today_entries_and_keeps_exemptions",
    ],
    "来源与去向": [
        _START + "test_legal_from_to_answering_combinations_route_each_item",
        _START + "test_illegal_combinations_are_protocol_errors_and_invalid_requests",
        _START + "test_from_and_to_need_one_item_per_target",
        _START + "test_unknown_recipient_is_recipient_not_found_before_admission",
        _STOREFRONT + "test_host_checks_from_to_and_answering_against_private_bindings",
        _GIVING + "test_direct_delivery_to_a_person_goes_into_their_holdings_and_nobody_eats",
        _GIVING + "test_self_receipt_then_giving_transfers_exactly_once",
        _GIVING + "test_same_product_for_two_recipients_splits_orders_by_destination",
        _GIVING + "test_two_gives_in_one_request_hand_over_real_holdings_only",
        _GIVING + "test_giving_to_a_colocated_person_moves_one_portion_and_nobody_eats",
        _GIVING + "test_giving_to_a_person_who_left_is_refused_as_not_together",
    ],
    "进食": [
        _MEAL + name for name in (
            "test_ending_a_meal_at_300_of_1500_seconds_eats_0_2_discards_0_8_and_keeps_3",
            "test_pause_keeps_the_opened_remainder_and_resume_eats_it_after_another_meal_took_the_stock",
            "test_less_than_one_portion_is_eaten_for_a_scaled_duration_and_energy",
            "test_experiment_cutoff_truncation_discards_nothing",
            "test_portion_duration_and_meal_portion_share_one_basis",
            "test_meal_duration_is_validated_against_the_scaled_portion",
            "test_open_meal_moves_the_portion_out_of_stock_only_for_eating",
            "test_conclusion_outcome_names_failure_and_skips_other_activities",
        )
    ] + [
        _INVENTORY + "test_meal_partitions_reach_zero_without_losing_quantity_or_energy",
        _INVENTORY + "test_eating_progress_ignores_stock_taken_while_the_meal_was_paused",
    ],
    "指名购买": [
        _NAMED + name for name in (
            "test_rotation_hidden_supply_is_bought_by_name_at_the_current_quote",
            "test_unknown_name_is_rejected_with_near_candidates_and_nothing_is_substituted",
            "test_same_name_in_several_stores_is_ambiguous_until_a_store_is_named",
            "test_named_resolution_follows_the_chosen_way_and_never_matches_internal_ids",
            "test_near_candidates_are_deterministic_term_similarity",
            "test_unlisted_obtain_selection_is_a_named_placeholder_other_verbs_are_not",
            "test_host_lets_unlisted_obtain_names_through_and_corrects_other_verbs",
        )
    ] + [
        _ENGINE + "test_purchase_by_unlisted_name_resolves_only_existing_supplies",
        _STOREFRONT + "test_unoffered_supply_name_is_resolved_by_the_environment_not_the_host",
    ],
    "代点关联": [
        _LINKING + "test_answering_links_only_items_sent_to_the_sender_and_shows_the_link",
        _LINKING + "test_resubmitting_the_same_request_is_rejected_and_points_to_the_order",
        _LINKING + "test_a_new_message_is_a_new_request",
        _LINKING + "test_message_bindings_name_visible_direct_messages_by_sender_and_arrival",
    ],
    "最小完整路径": [
        "tests/integration/test_minimum_complete_path.py::"
        "test_requested_order_goes_to_the_colleague_and_is_not_placed_twice",
    ],
    "程序错误": list(dict.fromkeys(node for nodes in PROGRAM_ERRORS.values() for node in nodes)),
    "强制等待与预算": [
        _EXHAUSTED + name for name in (
            "test_restricted_rounds_used_up_without_an_activity_pause_technically",
            "test_a_successful_last_restricted_round_carries_the_latest_rejection_code",
            "test_a_replayed_rejection_in_the_last_restricted_round_exhausts_the_budget",
            "test_budget_exhausted_with_a_pending_conclusion_beside_a_running_activity",
            "test_budget_exhaustion_units",
        )
    ] + [
        _SAME_MOMENT + "test_rejected_start_stays_in_start_or_resume_and_marks_the_option",
        _SAME_MOMENT + "test_restricted_stage_check_and_ladder_units",
        _QUALITY + "test_decision_rhythm_counts_silent_boundaries_and_wake_reasons",
    ],
    "下游": [
        _EVENTS + "test_retired_decision_statement_field_is_rejected_on_event_drafts",
        _EVENTS + "test_persisted_decision_started_is_validated_when_reading_events",
        _QUALITY + "test_decision_rhythm_counts_silent_boundaries_and_wake_reasons",
        _BEHAVIOR + "test_observation_gate_allows_same_second_continuation_chain_reuse",
        _BEHAVIOR + "test_observation_gate_fails_on_cross_second_or_cross_agent_duplicate",
        _WINDOWS + "test_dreaming_window_reads_self_note_and_layer_json_without_decision_started",
        _WINDOWS + "test_action_and_intent_windows_show_layers_and_self_note_but_never_decision_started",
        _WINDOWS + "test_continued_same_second_request_is_kept_verbatim_in_action_and_intent_windows",
        "tests/delivery/test_publication.py::"
        "test_delivery_gate_is_world_schema_eight_and_rejects_version_seven_exports",
        _PURCHASES + "test_export_writes_purchase_facts_under_manifest_version_two",
        _PURCHASES + "test_validation_recomputes_purchase_facts_and_rejects_tampering",
        "tests/tools/test_run_viewer.py::test_run_viewer_recorded_names",
        _METRICS + "test_the_subcommand_opens_the_run_read_only",
        _METRICS + "test_the_baseline_comparison_refuses_runs_that_are_not_comparable",
        _MANIFEST + "test_retired_stage_is_rejected_even_beside_the_current_stage",
        _MANIFEST + "test_engine_create_rejects_retired_stage_before_writing_run_artifacts",
        _MANIFEST + "test_runtime_restore_rejects_retired_stage_even_when_code_identity_matches",
        _IDENTITY + "test_environment_lifecycles_reject_invalid_saved_stage_identity",
        _IDENTITY + "test_direct_engine_open_rejects_retired_stage",
        _IDENTITY + "test_new_run_rejects_a_retired_stage_identity",
        _IDENTITY + "test_resume_and_replay_identity_rejects_a_retired_stage_before_comparing",
        _IDENTITY + "test_environment_validation_rejects_a_retired_stage",
        "tests/footprints/test_render_day.py::test_resolves_names_from_seven_layer_observation_evidence_keys",
    ],
    "真实复验（只报告）": [
        _METRICS + "test_a_real_run_reports_chain_positions_reasoning_cache_cost_and_topics",
        _METRICS + "test_the_subcommand_opens_the_run_read_only",
        _METRICS + "test_errors_are_classified_with_400_separate_and_partial_tokens_fall_back_to_chars",
        _METRICS + "test_the_baseline_comparison_refuses_runs_that_are_not_comparable",
    ],
    "文档一致性": [_DOC_GATE],
    "工程门禁": [
        "tests/test_style_gate.py::test_style_gate_admits_no_new_violations",
        "tests/test_coverage_gate.py::test_branch_pass_and_fail_states",
        "tests/test_coverage_gate.py::test_uncovered_public_interface_detection",
        _DOC_GATE,
    ],
}

#: 只报告的台账行：规格写明不作门禁；登记的测试只验证它所用的工具，行本身的真实运行不在测试中执行。
REPORT_ONLY: dict[str, str] = {
    "真实复验（只报告）": "白领全天与双人半日的真实模型运行按规格只报告、不作门禁，需要用户的模型账号；"
                         "登记的测试验证续接对比所用的只读子命令 scripts/inspect_run.py continuation。",
}

#: 「裁决记录」第二张表 #21–#38（按推荐定为已定）：编号 → 验证推荐做法的测试节点。
RULINGS: dict[int, list[str]] = {
    21: [
        _PROTOCOL + "test_followup_requests_hide_receipts_and_keep_memory_pairing_and_initial_user",
        _BLACKLIST + "test_internal_identity_cannot_hide_inside_a_progress_result",
        "tests/life/test_progress_layer_projection.py::test_fresh_result_preserves_feedback_without_action_id",
        _BRANCHES + "test_tool_messages_follow_memory_queries_only",
    ],
    22: [
        _START + "test_start_activity_schema_rejects_model_supplied_calculation",
        _START + "test_ways_vocabulary_maps_each_way_to_exactly_one_life_verb",
        _START + "test_legal_from_to_answering_combinations_route_each_item",
        _START + "test_from_and_to_need_one_item_per_target",
        _GIVING + "test_direct_delivery_to_a_contact_goes_to_their_receive_location",
    ],
    23: [
        _LINKING + "test_answering_links_only_items_sent_to_the_sender_and_shows_the_link",
        _LINKING + "test_resubmitting_the_same_request_is_rejected_and_points_to_the_order",
        _LINKING + "test_a_new_message_is_a_new_request",
    ],
    24: [
        _GIVING + "test_giving_to_a_contact_needs_being_at_their_receive_location",
        _GIVING + "test_giving_to_a_contact_at_their_receive_location_goes_into_contact_holdings",
        _GIVING + "test_direct_delivery_to_a_contact_goes_to_their_receive_location",
        _GIVING + "test_direct_delivery_to_a_contact_who_cannot_receive_is_refused",
        _CONTACTS + "test_messages_to_contacts_go_to_the_outbox_and_the_reply_request_carries_both_sides",
        _CONTACTS + "test_received_lists_delivered_orders_and_gifts_to_the_contact",
    ],
    25: [
        _EXHAUSTED + "test_restricted_rounds_used_up_without_an_activity_pause_technically",
        _EXHAUSTED + "test_budget_exhaustion_units",
        _SAME_MOMENT + "test_rejected_start_stays_in_start_or_resume_and_marks_the_option",
    ],
    26: [
        _MENU + "test_actual_tool_schema_is_stable_whatever_the_menu",
        _SEVEN + "test_native_tools_are_stable_purpose_only_and_drop_memory_only_on_the_last_turn",
        _BRANCHES + "test_restricted_rounds_do_not_narrow_the_host_tool_surface",
        _AGENT + "test_life_agent_schema_is_closed_and_host_fields_are_hidden",
        _GROUNDED + "test_grounded_tool_uses_unique_readable_names_without_internal_ids",
    ],
    27: [
        _CONTINUATION + name for name in (
            "test_the_continued_request_starts_with_the_previous_request_byte_for_byte",
            "test_a_three_decision_chain_keeps_every_prefix_and_reports_each_change_once",
            "test_layer_changes_overlay_earlier_changes_and_keep_the_latest",
            "test_continuation_messages_read_earlier_changes_and_skip_memory_and_feedback_tool_messages",
            "test_a_rejected_request_continues_with_the_rejection_reason",
        )
    ],
    28: [
        _SEVEN + "test_observe_stores_the_body_without_the_host_only_progress_keys",
        _BLACKLIST + "test_private_observation_keys_are_registered_as_forbidden",
        _CONTINUATION + "test_recorded_evidence_decides_whether_the_chain_holds",
        _CONTINUATION + "test_missing_model_evidence_breaks_the_chain",
        _CONTINUATION + "test_a_missing_result_of_the_previous_request_breaks_the_chain",
    ],
    29: [
        _CONTINUATION + "test_a_plain_resume_from_a_mid_second_checkpoint_sends_the_same_request",
        _CONTINUATION + "test_a_counterfactual_branch_breaks_the_chain",
    ],
    30: [
        _CONTINUATION + "test_budget_step_zero_ends_the_continuation_before_any_condensation",
        _CONTINUATION + "test_budget_step_zero_starts_the_decision_from_a_fresh_snapshot_for_every_turn",
    ],
    31: [
        _CONTINUATION + name for name in (
            "test_the_stage_identity_requires_a_boolean_switch",
            "test_the_switch_is_frozen_into_the_run_manifest_and_the_agent_profile",
            "test_life_env_freezes_the_switch_into_the_manifest",
            "test_resume_and_replay_cannot_override_the_switch",
            "test_resume_inherits_the_parent_switch_and_rejects_a_diverged_profile",
            "test_switch_off_every_decisions_first_request_has_exactly_two_messages",
        )
    ],
    32: [
        _MEMORY + "test_memory_layer_is_byte_identical_after_restore_and_the_cache_only_accelerates",
        _LAYERS + "test_memory_layer_and_persona_views_are_empty_at_run_start",
    ],
    33: [
        _PERSONA + "test_subject_is_required_for_relationship_and_preference_and_stripped_before_memory",
        _PERSONA + "test_later_view_replaces_earlier_while_events_keep_both",
        _PROGRESS + "test_conclude_arguments_omit_relationship_and_preference_memories",
    ],
    34: [
        _MEMORY + "test_reflection_date_is_the_local_date_six_hours_before_sleep_start",
        _MEMORY + "test_bedtime_reflection_lands_on_the_date_of_the_sleep_it_follows",
        _MEMORY + "test_nap_reflection_stays_out_until_the_next_day",
        "tests/life/test_memory_evidence.py::test_reflection_snapshots_carry_their_source_and_both_checks_accept_it",
    ],
    35: [
        _PERSONA + "test_card_path_renders_card_fields_and_merges_relationships_by_exact_name",
        _PERSONA + "test_no_card_person_keeps_the_name_only_path_and_view_only_people_get_an_item",
        _SEVEN + "test_no_card_persona_layer_keeps_the_name_and_scene_contacts",
    ],
    36: [
        _PERSONA + "test_consolidation_is_kept_but_its_summary_never_reaches_the_prompt",
        _CONCLUDE + "test_threshold_consolidation_counts_conclusion_entries_but_not_bedtime_reflection",
    ],
    37: [
        _AGENT + "test_life_agent_schema_is_closed_and_host_fields_are_hidden",
        _SEVEN + "test_native_tools_are_stable_purpose_only_and_drop_memory_only_on_the_last_turn",
    ],
    38: [
        _PERSONA + "test_readonly_questions_use_the_latest_decision_snapshot",
        _PROTOCOL + "test_readonly_loop_uses_only_persona_memory_and_question",
    ],
}

#: 本特性新增或改动的生产文件：撰写时由 `git diff --name-only --diff-filter=AM c3b24da -- src custom tools scripts`
#: 得出后冻结（c3b24da 是本特性实施前的主线提交）。延期标记扫描逐个检查这些文件的全文。
FEATURE_PRODUCTION_FILES: tuple[str, ...] = (
    "custom/agents/life_agent.py",
    "custom/agents/life_context.py",
    "custom/agents/life_continuation.py",
    "custom/agents/life_memory.py",
    "custom/agents/life_persona.py",
    "custom/agents/life_prompt.py",
    "custom/envs/life_env.py",
    "scripts/inspect_run.py",
    "src/worldmock/cli.py",
    "src/worldmock/delivery/pipeline.py",
    "src/worldmock/delivery/windows.py",
    "src/worldmock/evaluation/behavior_metrics.py",
    "src/worldmock/evaluation/validation.py",
    "src/worldmock/footprints/export.py",
    "src/worldmock/footprints/models.py",
    "src/worldmock/footprints/purchases.py",
    "src/worldmock/footprints/quality.py",
    "src/worldmock/footprints/store.py",
    "src/worldmock/footprints/validation.py",
    "src/worldmock/life/activities.py",
    "src/worldmock/life/activity_control.py",
    "src/worldmock/life/activity_evidence.py",
    "src/worldmock/life/admission.py",
    "src/worldmock/life/basket.py",
    "src/worldmock/life/commerce.py",
    "src/worldmock/life/contact_exchange.py",
    "src/worldmock/life/continuation.py",
    "src/worldmock/life/day_plan_inputs.py",
    "src/worldmock/life/decision_start.py",
    "src/worldmock/life/engine.py",
    "src/worldmock/life/environment_layer.py",
    "src/worldmock/life/giving.py",
    "src/worldmock/life/guidance.py",
    "src/worldmock/life/interaction_menu.py",
    "src/worldmock/life/memory_evidence.py",
    "src/worldmock/life/memory_layer.py",
    "src/worldmock/life/models.py",
    "src/worldmock/life/named_purchase.py",
    "src/worldmock/life/narration.py",
    "src/worldmock/life/observe.py",
    "src/worldmock/life/order_routing.py",
    "src/worldmock/life/progress_layer.py",
    "src/worldmock/life/projection.py",
    "src/worldmock/life/prompt_capacity.py",
    "src/worldmock/life/prompt_schemas.py",
    "src/worldmock/life/readable.py",
    "src/worldmock/life/recipients.py",
    "src/worldmock/life/request_linking.py",
    "src/worldmock/life/result_projection.py",
    "src/worldmock/life/rhythm.py",
    "src/worldmock/life/routing.py",
    "src/worldmock/life/same_moment.py",
    "src/worldmock/life/scenario.py",
    "src/worldmock/life/session_actions.py",
    "src/worldmock/life/sessions.py",
    "src/worldmock/life/settled_text.py",
    "src/worldmock/life/social.py",
    "src/worldmock/life/storefront_runtime.py",
    "src/worldmock/life/todo_layer.py",
    "src/worldmock/life/tool_layer.py",
    "src/worldmock/life/work.py",
    "src/worldmock/profile/models.py",
    "src/worldmock/profile/textgen.py",
    "src/worldmock/runtime.py",
    "src/worldmock/validation.py",
    "tools/run-viewer-context-view.js",
    "tools/run-viewer-context.js",
    "tools/run-viewer.html",
)

#: 本特性新增的测试文件（`git diff --name-only --diff-filter=A c3b24da -- tests`）与本守卫自身。
FEATURE_TEST_FILES: tuple[str, ...] = (
    "tests/footprints/test_purchases.py",
    "tests/integration/test_minimum_complete_path.py",
    "tests/integration/test_same_second_continuation.py",
    "tests/integration/test_seven_layer_request.py",
    "tests/life/routing_fixtures.py",
    "tests/life/test_contact_exchange.py",
    "tests/life/test_continuation_metrics.py",
    "tests/life/test_decision_budget.py",
    "tests/life/test_environment_layer.py",
    "tests/life/test_giving.py",
    "tests/life/test_meal_outcome.py",
    "tests/life/test_memory_layer.py",
    "tests/life/test_named_purchase.py",
    "tests/life/test_persona_layer.py",
    "tests/life/test_progress_events.py",
    "tests/life/test_prompt_layers.py",
    "tests/life/test_readable.py",
    "tests/life/test_request_linking.py",
    "tests/life/test_todo_layer.py",
    "tests/life/test_tool_layer.py",
    "tests/persona_prompt_acceptance.py",
    "tests/test_persona_prompt_acceptance.py",
)
