"""一日运行保真规格的验收登记表（完整性守卫的数据源）。

@file     dayrun_fidelity_acceptance.py
@brief    把 docs/dev/SPEC-life-runtime.md#dayrun-fidelity-身体与需要 的「验收台账」各行、「裁决记录」两张表中状态为已定的裁决逐行登记到真正验证它的
          测试节点；「输入警告」行另按警告码逐码登记一例；并冻结本特性新增或改动的生产文件与新增测试文件，供延期标记与空壳函数
          扫描。判定由 tests/test_dayrun_fidelity_acceptance.py 执行。
@note     登记的节点是走真实引擎、宿主、导出或加载路径的行为测试（文档登记类条款读真实文档），不是结构匹配；一行列多个节点时
          各覆盖该行的一部分契约。
          「输入警告」行写「十个警告码各一例」（个人事务与工作安排规格阶段 3 由「九个」更正）：警告码闭集是 1.3 的八个码加
          8.1 的 `DELIVERY_ROUTE_UNAVAILABLE`、`LOCATION_NAME_SUSPICIOUS` 共十个，WARNING_CODE_TESTS 逐码各登记一例。
          「复跑指标（只报告）」按规格只报告、不作门禁：WC 与 BIZ 全天复跑需要用户的模型账号，不在测试中执行；登记的是复跑
          要读的质量报告各节（输入警告、截断、时间线标签、联系人回复守卫、名称类拒绝、零点计划的非例行推进比例）的测试。
          「strict 端点」行的「探针在主站与 /beta 的结果写入验收记录」一半：登记探针脚本与端点切换的测试，真实探针结果由
          实现后文档写进本特性的验收记录。「更简单的购买请求」一行是冻结后补登的台账行（规格版本表 0.6），判据取 3.5 的验收句；
          实现复核后 3.5「收敛」第 2 条细化（版本表 0.7：「购买」不定交不到收货人手上的方式，都不行时只指向能到店买的出售门店，
          送给别人时先送到自己这里），该行与验收句同步，登记的测试随之覆盖细化后的行为。
          「文档一致性」行除文档链接门禁与设计说明书汇编外，另登记本守卫的两项文档判定：冻结时写下的临时待验收标记不再留在
          docs/，带锚点的文档链接都指向现有标题。
"""

from __future__ import annotations

_BILLING = "tests/life/test_sleep_billing.py::"
_RELIEF = "tests/life/test_relief_floor.py::"
_PREVIEW = "tests/life/test_motivation.py::TestReliefFloorPreview::"
_SCENARIO = "tests/life/test_scenario.py::"
_INJECTION = "tests/profile/test_injection.py::"
_WARNINGS = "tests/integration/test_input_warnings.py::"
_QUALITY = "tests/footprints/test_quality.py::"
_FELT = "tests/life/test_felt_states.py::"
_TWO_PROCESS = "tests/life/test_sleep_two_process.py::"
_PULSE = "tests/life/test_discomfort_and_sleep_messages.py::"
_SAME_MOMENT = "tests/life/test_same_moment_decisions.py::"
_SEVEN = "tests/integration/test_seven_layer_request.py::"
_PLANNING = "tests/life/test_day_planning.py::"
_DAY_PLAN = "tests/life/test_day_plan.py::"
_NAMES = "tests/life/test_name_resolution.py::"
_HOST_FEEDBACK = "tests/integration/test_host_feedback.py::"
_CONTROL_NAMES = "tests/life/test_control_name_rejections.py::"
_NAMED = "tests/life/test_named_purchase.py::"
_STAGES = "tests/life/test_purchase_stages.py::"
_CHANNELS = "tests/life/test_purchase_channels.py::"
_COVERAGE_APPS = "tests/profile/test_coverage_apps.py::"
_REPLAY = "tests/life/test_wc_named_purchase_replay.py::"
_GIVING = "tests/life/test_giving.py::"
_PURCHASES = "tests/footprints/test_purchases.py::"
_START = "tests/test_start_activity_contract.py::"
_SCHEMA = "tests/integration/test_activity_schema.py::"
_ERRORS = "tests/life/test_error_contract.py::"
_PRESENT = "tests/life/test_presentation_fidelity.py::"
_ROTATION = "tests/life/test_rotation.py::"
_CANDIDATES = "tests/life/test_candidates.py::"
_RELEVANCE = "tests/life/test_relevance.py::"
_INDEX = "tests/life/test_callable_index.py::"
_HINTS = "tests/life/test_browse_and_hints.py::"
_WAYFINDING = "tests/life/test_wayfinding.py::"
_CONCLUDE = "tests/integration/test_conclude_memories.py::"
_CONTROL = "tests/life/test_control_items.py::"
_EXCERPTS = "tests/life/test_work_review_excerpts.py::"
_TRUNCATION = "tests/integration/test_output_truncation.py::"
_GUARD = "tests/life/test_contact_reply_guard.py::"
_RENDER = "tests/footprints/test_render_day.py::"
_TITLES = "tests/life/test_activity_title_embedding.py::"
_LOAD = "tests/life/test_load_checks.py::"
_MAP = "tests/life/test_map_dataset.py::"
_AROUND = "tests/life/test_goal_around_time.py::"
_GOAL_METRICS = "tests/evaluation/test_goal_metrics.py::"
_GOAL_EXPORT = ("tests/footprints/test_goal_export.py::"
                "test_exported_quality_counts_only_non_routine_goals_and_agrees_with_engine_progress")
_PROBE = "tests/test_probe_deepseek_strict.py::"
_ENDPOINT = "tests/integration/test_strict_endpoint.py::"
_IDENTITY = "tests/integration/test_runtime.py::test_model_identity_freezes_complete_strict_endpoint_and_request"
_DOC_GATE = "tests/test_documentation_gate.py::test_real_repository_documents_and_source_references_resolve"
_DOCS_MARKERS = "tests/test_dayrun_fidelity_acceptance.py::test_docs_carry_no_pending_markers_of_this_feature"
_DOCS_ANCHORS = "tests/test_dayrun_fidelity_acceptance.py::test_anchored_links_in_docs_point_to_existing_headings"
_VIEWER = "tests/tools/test_run_viewer.py::test_run_viewer_recorded_names"

#: 「输入警告」行按警告码闭集逐码各一例（码 → 在测试体里断言该码的用例；闭集取 worldmock.profile.injection）。
WARNING_CODE_TESTS: dict[str, str] = {
    "TASTE_GENRES_UNMATCHED": _INJECTION + "test_wc_platform_word_taste_matches_no_genre_and_all_weights_are_zero",
    "CONTENT_WEIGHTS_ALL_ZERO": _INJECTION + "test_matching_tastes_raise_no_taste_warning",
    "CONTENT_EMPTY_WITH_MEDIA_TASTES": _INJECTION + "test_biz_has_no_content_but_the_card_and_habits_listen_and_scroll",
    "GAMES_WITHOUT_TASTE_WEIGHTS": (_INJECTION
                                    + "test_games_without_weight_entries_are_reported_from_the_explicit_table"),
    "COVERAGE_BELOW_THRESHOLD": _INJECTION + "test_coverage_below_threshold_reuses_the_validate_report",
    "CARD_FIELD_OVERRIDDEN": _INJECTION + "test_wc_scenario_rhythm_overrides_the_extracted_card_values",
    "CARD_FIELD_CONFLICT": _INJECTION + "test_out_of_order_window_keeps_the_card_value_and_records_a_conflict",
    "PERSONA_AGREEMENT_LOW": _INJECTION + "test_persona_compiler_agreement_warnings_are_passed_through",
    "DELIVERY_ROUTE_UNAVAILABLE": _INJECTION + "test_biz_undeliverable_supplies_are_grouped_by_store_and_destination",
    "LOCATION_NAME_SUSPICIOUS": (_INJECTION
                                 + "test_suspicious_display_names_are_reported_only_for_coordinates_and_identifiers"),
}

#: 「更简单的购买请求」（3.5，裁决 S10）的判据测试：请求形状、方式「购买」、from 四种写法、按 prefer 选定与分段匹配的跳段。
_SIMPLER_PURCHASE = [
    _SCHEMA + "test_start_activity_uses_readable_frozen_objects_and_paired_actions",
    _START + "test_start_activity_schema_rejects_model_supplied_calculation",
    _START + "test_ways_vocabulary_maps_each_way_to_exactly_one_life_verb",
    _NAMED + "test_several_servable_matches_follow_prefer_and_never_report_ambiguity",
    _NAMED + "test_same_name_in_several_stores_is_chosen_by_prefer_unless_a_store_is_named",
    _CHANNELS + "test_member_store_ties_break_by_landed_price_then_store_identity",
    _CHANNELS + "test_several_member_stores_choose_the_nearest_and_note_it",
    _NAMED + "test_a_store_whose_price_is_unknown_is_never_the_cheapest",
    _STAGES + "test_system_purchase_goes_in_store_when_here_and_remote_when_deliverable",
    _STAGES + "test_system_purchase_on_the_road_is_not_buying_in_the_store_just_left",
    _STAGES + "test_system_purchase_that_can_neither_pick_up_nor_deliver_names_the_nearest_store",
    _STAGES + "test_system_purchase_next_step_names_the_nearest_store_that_sells_it_in_store",
    _STAGES + "test_goods_sold_by_several_stores_point_to_the_store_with_the_shortest_route",
    _GIVING + "test_system_purchase_for_someone_goes_in_store_only_when_they_are_there",
    _GIVING + "test_system_purchase_for_someone_it_cannot_reach_says_to_buy_for_oneself_first",
    _GIVING + "test_system_purchase_for_someone_of_goods_sold_only_in_store_cannot_reach_them",
    _STAGES + "test_system_purchase_is_accepted_for_listed_and_named_goods_and_never_listed",
    "tests/integration/test_storefront_tools.py::test_model_chooses_named_offer_and_fulfillment_without_echoing_quote",
    "tests/life/test_prompt_layers.py::test_schema_enums_are_literal_copies_of_the_source_closed_sets",
    _NAMED + "test_unlisted_obtain_selection_is_a_named_placeholder_other_verbs_are_not",
    _NAMED + "test_host_lets_unlisted_obtain_names_through_and_corrects_other_verbs",
    _CHANNELS + "test_each_from_form_resolves_to_its_source_scope",
    _CHANNELS + "test_an_unknown_source_is_source_not_found_with_near_stores_and_apps",
    _INDEX + "test_a_name_several_stores_deliver_is_called_by_name_and_chosen_by_prefer",
    _GIVING + "test_named_purchase_takes_from_as_the_store_and_the_recipient_place_as_destination",
    _STAGES + "test_a_way_the_matching_store_does_not_offer_is_fulfillment_not_supported",
    _STAGES + "test_system_purchase_and_a_null_from_each_skip_only_their_own_stage",
    _NAMED + "test_label_scope_decides_before_the_way_and_names_the_mismatch",
    _REPLAY + "test_goods_that_cannot_reach_the_receiving_place_say_why_and_where_the_store_delivers",
    _PURCHASES + "test_order_ways_and_browse_kind_are_the_interaction_menu_values",
]

#: 「验收台账」逐行：首列契约点 → 验证它的测试节点。
LEDGER: dict[str, list[str]] = {
    "睡眠计费": [
        _BILLING + name for name in (
            "test_sleep_completing_at_a_boundary_leaves_social_need_unchanged",
            "test_sleep_ended_early_by_a_reminder_leaves_social_need_unchanged",
            "test_first_awake_interval_after_waking_accrues_a_quarter_hour",
            "test_social_ledger_awake_classification_follows_the_settlement_activity_ids",
            "test_meal_finished_exactly_at_the_boundary_still_counts_as_shared",
            "test_a_call_ending_at_the_boundary_is_billed_once_by_the_call_ledger",
            "test_ledger_without_a_draft_falls_back_to_activities_ending_now",
        )
    ],
    "下限缓解": [
        _RELIEF + name for name in (
            "test_disliked_content_is_relieved_at_the_floor_without_awake_accrual",
            "test_committed_body_settlement_relieves_without_accrual",
            "test_relief_preference_has_floor_at_zero_identity_at_one_and_is_strictly_monotone",
            "test_actual_relief_rises_strictly_with_taste_weight",
            "test_runtime_computes_game_weights_with_the_content_algorithm",
            "test_games_without_genre_declarations_fall_to_the_floor",
            "test_zero_weight_sorts_last_although_it_is_relieved_at_the_floor",
            "test_single_and_shared_paths_settle_the_same_net_effect",
            "test_parallel_relieving_activities_add_up_in_the_shared_settlement",
            "test_watching_from_full_need_reaches_the_lower_band_in_the_specified_time",
            "test_a_watch_session_creates_no_upward_entertainment_band_boundary",
            "test_plain_awake_time_still_predicts_the_upward_entertainment_crossing",
        )
    ] + [
        _PREVIEW + "test_watch_candidate_preview_equals_engine_settlement",
        _PREVIEW + "test_continue_preview_equals_engine_settlement",
        _PREVIEW + "test_zero_rate_content_previews_awake_accrual_like_the_engine",
        _PREVIEW + "test_continue_watch_without_relief_evidence_is_rejected",
        "tests/integration/test_host_branch_coverage.py::test_build_society_create_mode_injects_taste_weights",
        _SCENARIO + "test_relief_preference_floor_field_states_value_meaning_and_hypothesis",
        _SCENARIO + "test_relief_preference_floor_is_registered_once_in_the_experiment_hypotheses",
        _SCENARIO + "test_relief_preference_floor_defaults_to_half_without_changing_scenario_files",
        _SCENARIO + "test_declared_relief_preference_floor_is_assembled_into_body_config",
        _SCENARIO + "test_relief_preference_floor_outside_the_open_unit_interval_is_rejected",
    ],
    "输入警告": [
        _INJECTION + "test_the_warning_code_set_is_the_closed_ten",
        *WARNING_CODE_TESTS.values(),
        _INJECTION + "test_a_habit_whose_trigger_watches_or_scrolls_counts_as_a_media_habit",
        _INJECTION + "test_a_real_scenario_route_that_turns_unknown_is_reported_and_reachable_ones_are_not",
        _INJECTION + "test_frozen_warnings_keep_the_closed_shape_and_null_means_not_computed",
        _WARNINGS + "test_run_creation_freezes_input_warnings_into_the_manifest_ops_log_and_quality_report",
        _WARNINGS + "test_map_problems_are_frozen_as_scenario_warnings_and_creation_still_succeeds",
        _WARNINGS + "test_a_phone_config_that_fails_verification_never_fails_run_creation",
        _WARNINGS + "test_validate_prints_the_same_warnings_the_run_froze",
        _WARNINGS + "test_validate_still_fails_below_the_coverage_threshold_after_printing_the_warnings",
        _QUALITY + "test_input_warnings_section_counts_the_frozen_manifest_warnings",
        "tests/integration/test_run_manifest.py::test_engine_direct_runs_keep_input_warnings_null_and_unrecorded",
        "tests/profile/test_coverage.py::test_app_name_substrings_no_longer_count_as_coverage_hits",
    ],
    "困意档界": [
        _FELT + "test_sleepiness_band_names_and_count_are_unchanged",
        _FELT + "test_sleepiness_level_boundaries",
        _TWO_PROCESS + "TestSleepinessGradeBands::test_grade_boundaries",
        _TWO_PROCESS + "TestSleepinessGradeBands::test_sleep_grades_and_felt_bands_are_one_table",
        _TWO_PROCESS + "TestSleepinessGradeBands::test_band_edges_sit_on_the_kss_equivalent_cuts",
        _TWO_PROCESS + "TestWeekdaySteadyStateOnsets::test_band_onsets_match_the_spec",
        _TWO_PROCESS + "TestWeekdaySteadyStateOnsets::test_the_same_steady_state_reproduces_the_retired_edges_row",
        _TWO_PROCESS + "TestWeekdaySteadyStateOnsets::test_weekday_sleep_against_the_declared_need",
        _TWO_PROCESS + "TestAnchorTrajectory::test_anchor_trajectory_reads_awake_awake_tired_sleepy",
        _TWO_PROCESS + "TestAnchorTrajectory::test_band_onsets_under_the_default_assumption",
        _TWO_PROCESS + "TestSleepinessCrossingPrediction::"
        "test_prediction_from_current_pressure_is_not_later_than_actual",
    ],
    "不适脉冲与消息": [
        _PULSE + name for name in (
            "test_hunger_pulse_starts_after_two_hours_and_bills_only_what_it_applies",
            "test_each_boundary_is_billed_only_for_its_new_hours_past_the_threshold",
            "test_discomfort_counts_only_the_hungry_and_sleepy_bands",
            "test_messages_delivered_during_sleep_are_read_at_the_first_boundary_after_waking",
            "test_the_boundary_decision_segment_reads_no_message_while_asleep",
            "test_a_round_observed_while_asleep_marks_no_message_read",
        )
    ] + [
        _FELT + "TestMood::test_discomfort_pulse_is_linear_in_billed_hours",
        _SAME_MOMENT + "test_sleeping_recipient_is_not_woken_by_person_message",
        _SAME_MOMENT + "test_contact_reply_arrival_filtered_during_sleep",
    ],
    "场景作息": [
        _INJECTION + "test_only_explicitly_declared_fields_are_overridden",
        _INJECTION + "test_wc_scenario_rhythm_overrides_the_extracted_card_values",
        _INJECTION + "test_out_of_order_window_keeps_the_card_value_and_records_a_conflict",
        _INJECTION + "test_values_are_quantised_to_the_card_grid",
        _INJECTION + "test_windows_that_only_fit_together_are_both_applied_and_the_result_is_a_fixed_point",
        _INJECTION + "test_unrepresentable_and_after_midnight_windows",
        _WARNINGS + "test_a_scenario_declaring_only_the_midpoint_changes_only_the_midpoint_in_the_request",
        _WARNINGS + "test_a_conflicting_scenario_window_keeps_the_card_value_and_is_frozen_as_a_conflict",
        _WARNINGS + "test_body_settlement_day_plan_inputs_and_the_persona_layer_read_the_same_rhythm",
        _WARNINGS + "test_restore_verifies_the_frozen_card_rhythm_against_the_scenario",
        _WARNINGS + "test_validate_prints_the_same_warnings_the_run_froze",
        _SCENARIO + "test_runtime_agents_keep_the_declared_rhythm_fields",
        "tests/integration/test_runtime.py::test_agent_specs_inject_compiled_persona_card_text",
        "tests/life/test_persona_layer.py::test_persona_card_struct_is_saved_on_new_runs_and_validated_on_restore",
        "tests/life/test_persona_upgrade.py::"
        "test_memory_view_identity_uses_the_card_text_or_the_scenario_persona_fields",
    ],
    "系统层": [
        _SEVEN + "test_system_layers_are_the_contract_registration_verbatim",
        _SEVEN + "test_rule_14_makes_the_routine_the_default_and_the_day_plan_override_it",
        _SEVEN + "test_felt_bands_named_by_the_rules_are_the_felt_band_texts",
        _SEVEN + "test_recorded_prompt_fingerprint_is_recomputed_from_the_eighteen_rules_and_the_around_time_schema",
        _PLANNING + "test_plan_template_is_the_contract_text_and_asks_only_for_differences_with_around_times",
        _PLANNING + "test_wake_time_noted_at_conclusion_still_becomes_a_reminder_that_ends_the_sleep",
        _DAY_PLAN + "test_empty_plan_is_registered_once_and_can_be_revised",
    ],
    "名称解析": [
        _NAMES + name for name in (
            "test_exact_normalised_and_candidate_paths_through_host_and_engine",
            "test_each_prefix_and_the_mail_stamp_are_added_or_stripped",
            "test_start_suffix_task_prefix_and_recall_names_resolve_for_the_other_tools",
            "test_resolve_name_accepts_only_a_unique_lossless_match",
            "test_home_words_map_to_the_home_location_and_other_spellings_only_get_candidates",
            "test_way_determines_action_and_unsupported_way_reports_offered_values",
            "test_way_synonym_is_accepted_only_on_a_unique_hit",
            "test_a_synonym_hitting_two_listed_ways_is_not_accepted",
            "test_candidates_are_feasible_first_and_submittable_verbatim",
            "test_host_acceptance_implies_environment_acceptance",
            "test_every_name_failure_of_a_two_item_request_is_reported",
            "test_unlisted_catalog_objects_are_callable_by_name_with_the_usual_checks",
            "test_held_item_candidates_keep_null_self_route_and_actual_ways",
            "test_self_route_fields_must_be_null_at_every_current_location",
            "test_simulated_marks_and_other_brackets_are_normalised_away",
            "test_a_mail_written_in_other_brackets_is_that_mail_and_a_bare_subject_is_ambiguous",
            "test_the_verb_and_way_decide_the_prefix_so_a_shared_bare_name_stays_unique",
            "test_candidates_for_unlisted_activities_carry_the_catalog_duration_as_their_note",
            "test_places_without_a_listed_route_are_never_candidates",
        )
    ] + [
        _GIVING + "test_a_recipient_written_without_the_prefix_is_resolved_to_that_person",
        _GIVING + "test_a_pickup_candidate_for_someone_not_at_the_store_is_not_ranked_as_feasible",
        "tests/life/test_request_linking.py::test_an_answering_name_written_without_spaces_is_resolved_to_that_message",
        _HOST_FEEDBACK + "test_all_name_errors_are_reported_and_other_conditions_report_the_first",
        _HOST_FEEDBACK + "test_every_unknown_relationship_view_is_reported_at_its_own_item",
        _HOST_FEEDBACK + "test_an_unknown_activity_name_does_not_hide_an_unknown_view_in_another_item",
        _CONTROL_NAMES + "test_two_unknown_activity_names_are_both_reported_in_one_rejection",
        _CONTROL_NAMES + "test_an_ambiguous_and_an_unknown_name_are_reported_with_their_own_codes",
        _CONTROL_NAMES + "test_the_restricted_round_recheck_reports_every_unknown_name_too",
        _CONTROL_NAMES + "test_a_resolved_name_that_is_not_the_persons_activity_is_refused_without_items",
        "tests/life/test_interaction_menu_invariants.py::"
        "test_selection_resolves_only_offered_verb_and_way_and_menu_judges_internal_actions",
        _START + "test_unresolvable_selection_is_rejected_as_invalid_request",
        "tests/life/test_engine_feedback.py::test_business_rejection_is_reported_in_progress_and_run_continues",
    ],
    "指名购买": [
        _NAMED + "test_full_label_with_another_store_in_from_is_a_source_mismatch",
        _START + "test_listed_label_with_another_known_place_in_from_is_a_source_mismatch",
        _STAGES + "test_goods_from_a_store_that_does_not_sell_them_are_supply_not_at_store",
        _CHANNELS + "test_an_unknown_source_is_source_not_found_with_near_stores_and_apps",
        _NAMED + "test_unknown_name_is_rejected_with_near_candidates_and_nothing_is_substituted",
        _STAGES + "test_a_way_the_matching_store_does_not_offer_is_fulfillment_not_supported",
        _ERRORS + "test_named_item_candidates_use_a_way_the_supply_supports",
        _NAMED + "test_named_resolution_follows_the_chosen_way_and_never_matches_internal_ids",
        _NAMED + "test_remote_orders_no_selling_store_can_deliver_say_so_like_listed_ones",
        _REPLAY + "test_goods_that_cannot_reach_the_receiving_place_say_why_and_where_the_store_delivers",
        _GIVING + "test_named_remote_order_for_someone_it_cannot_reach_says_why_like_a_listed_one",
        _NAMED + "test_label_scope_decides_before_the_way_and_names_the_mismatch",
        _STAGES + "test_the_name_stage_is_judged_before_the_source",
        _STAGES + "test_an_unservable_source_offers_the_same_goods_that_can_be_served_elsewhere",
        _STAGES + "test_multi_item_purchases_report_every_item_and_stay_atomic",
        _STAGES + "test_a_name_class_item_leads_the_merged_rejection_even_when_it_comes_second",
        _ERRORS + "test_second_named_item_failing_is_reported_at_index_one_and_the_group_stays_atomic",
        _ERRORS + "test_every_failing_named_item_is_reported_in_one_rejection",
        _REPLAY + "test_wc_named_purchase_requests_are_accepted_in_an_offline_replay",
        _NAMED + "test_unlisted_obtain_selection_is_a_named_placeholder_other_verbs_are_not",
        _NAMED + "test_host_lets_unlisted_obtain_names_through_and_corrects_other_verbs",
        _NAMED + "test_near_candidates_are_deterministic_term_similarity",
        _NAMED + "test_rotation_hidden_supply_is_bought_by_name_at_the_current_quote",
        _NAMED + "test_named_purchase_at_a_closed_store_is_a_business_rejection_and_nothing_is_substituted",
        _NAMED + "test_named_purchase_beyond_the_balance_is_a_business_rejection_and_nothing_is_substituted",
    ],
    "购买渠道": [
        _CHANNELS + name for name in (
            "test_each_from_form_resolves_to_its_source_scope",
            "test_a_channel_name_is_compared_without_spaces_or_case",
            "test_an_unknown_source_is_source_not_found_with_near_stores_and_apps",
            "test_a_full_label_accepts_a_channel_that_contains_its_store_and_rejects_one_that_does_not",
            "test_a_listed_label_with_an_app_in_from_is_resolved_against_that_channel",
            "test_several_member_stores_choose_the_nearest_and_note_it",
            "test_member_store_ties_break_by_landed_price_then_store_identity",
            "test_an_app_name_without_a_phone_config_is_source_not_found",
            "test_channels_are_derived_from_the_pinned_phone_config_at_load",
            "test_channel_admission_violations_are_rejected_at_load",
            "test_channel_admission_requires_selected_member_stores_with_supplies",
            "test_a_phone_config_that_fails_verification_derives_no_channels",
        )
    ] + [
        _PURCHASES + "test_purchase_rows_carry_the_order_channel_or_no_channel",
        _REPLAY + "test_wc_named_purchase_requests_are_accepted_in_an_offline_replay",
        _COVERAGE_APPS + "test_channels_group_sellers_by_app_and_list_the_places_where_they_have_supplies",
        _COVERAGE_APPS + "test_a_scenario_without_a_pinned_phone_config_has_nothing_to_derive",
    ],
    "名称类预算": [
        _NAMES + "test_two_name_rejections_in_one_second_are_free_and_the_third_counts",
        _NAMES + "test_each_person_has_their_own_two_free_name_rejections",
        _NAMES + "test_the_two_free_name_rejections_start_again_in_the_next_second",
        _NAMES + "test_a_replayed_name_rejection_always_counts_a_round",
        _NAMES + "test_a_name_rejection_beside_other_rejections_follows_the_same_count",
        _NAMES + "test_host_and_environment_share_one_name_class_closed_set",
        _QUALITY + "test_name_rejections_are_counted_apart_from_other_protocol_errors",
    ],
    "更简单的购买请求": _SIMPLER_PURCHASE,
    "错误合同": [
        _ERRORS + name for name in (
            "test_every_business_rejection_code_raised_in_the_source_has_a_specific_reason",
            "test_host_codes_are_the_closed_set_with_a_reason_and_a_fix",
            "test_error_structure_keys_and_candidate_shape_are_closed",
            "test_second_named_item_failing_is_reported_at_index_one_and_the_group_stays_atomic",
            "test_item_reasons_and_fixes_carry_the_one_based_item_number_only_for_several_items",
            "test_a_mixed_item_rejection_takes_the_first_items_code_and_next_step",
            "test_guidance_passes_fix_items_resolved_and_notes_from_the_refusal",
            "test_accepted_results_pass_resolved_and_notes_into_public_details",
        )
    ] + [
        _HOST_FEEDBACK + name for name in (
            "test_each_host_code_has_a_concrete_reason_through_the_host",
            "test_a_second_item_failing_is_reported_at_index_one_with_fill_in_candidates",
            "test_a_view_about_an_unknown_person_in_the_second_item_is_reported_at_index_one",
            "test_a_candidate_submitted_verbatim_is_accepted_through_the_host_and_the_engine",
            "test_protocol_feedback_is_the_shared_structure_with_the_fixed_instruction",
            "test_argument_errors_are_written_per_field_without_any_validation_dump",
            "test_feedback_rejects_bare_strings_instead_of_reconstructing_positions",
            "test_feedback_structure_survives_copying_and_evidence_stays_a_plain_string",
        )
    ] + [
        _CONTROL_NAMES + "test_a_known_and_an_unknown_name_report_only_the_unknown_second_item",
        _NAMES + "test_candidates_are_feasible_first_and_submittable_verbatim",
        "tests/life/test_parallel_admission.py::"
        "test_malformed_request_gets_the_default_reason_without_the_validation_text",
        _SCHEMA + "test_all_action_tools_use_closed_strict_schemas_from_one_model",
    ],
    "呈现": [
        _PRESENT + name for name in (
            "test_lifestyle_window_excludes_location_kind_mismatches_with_the_reason_code",
            "test_zero_taste_order_is_not_alphabetical_and_changes_with_the_date",
            "test_relevance_puts_time_window_goal_family_and_habit_hits_ahead",
            "test_goal_families_keep_the_plan_at_the_start_of_the_second",
            "test_refresh_quota_goes_to_the_longest_unpresented_lifestyle_entries",
            "test_rotation_has_no_shared_offset_across_partitions",
            "test_the_ceiling_n_over_k_bound_holds_per_partition",
            "test_order_key_version_is_two",
        )
    ] + [
        _ROTATION + name for name in (
            "test_rotation_criterion_is_last_presented_at_not_exposure_count",
            "test_quota_goes_to_the_longest_unpresented_members_first",
            "test_bound_holds_per_partition_regardless_of_other_partitions",
            "test_equally_stale_members_are_released_in_order_key_order",
            "test_rotation_has_no_cursor_and_depends_only_on_the_stamps",
            "test_quota_is_capped_by_subtype_count_only_not_by_tag_similarity",
        )
    ] + [
        _CANDIDATES + "TestClosedSets::test_order_key_version_is_two_and_enters_the_cursor_identity",
        _CANDIDATES + "TestExclusionDiscipline::test_catalog_view_applies_location_kinds_only_when_asked",
        _CANDIDATES + "TestOrdering::test_hash_of_scenario_person_date_and_object_breaks_ties",
        _CANDIDATES + "TestOrdering::test_relevance_orders_after_taste_and_before_the_hash",
        _RELEVANCE + "test_points_are_the_three_adopted_terms",
        _RELEVANCE + "test_entry_relevance_adds_time_window_goal_family_and_habit_hits",
        _RELEVANCE + "test_goal_families_come_from_unprogressed_goals_through_the_intent_table",
        "tests/integration/test_presentation_habits.py::"
        "test_run_creation_freezes_habit_responses_and_ranks_the_hit_entry_first",
        "tests/life/test_coverage_closure_projection.py::"
        "test_continuation_pages_follow_the_cursor_without_a_rotation_offset",
        _SAME_MOMENT + "test_same_second_rounds_keep_presentation_window_and_rotation",
    ] + [
        _INDEX + name for name in (
            "test_every_name_in_the_index_is_callable_by_name_and_accepted",
            "test_every_index_name_resolves_by_name_in_the_host_at_several_places",
            "test_goods_named_in_a_store_are_bought_in_store",
            "test_a_name_several_stores_deliver_is_called_by_name_and_chosen_by_prefer",
            "test_the_index_leaves_out_activities_excluded_by_location_kind",
            "test_the_index_is_not_a_target_and_the_presentation_cap_ignores_it",
            "test_the_interaction_menu_decides_what_can_be_done_here",
            "test_goods_are_only_what_can_be_obtained_from_here_now",
            "test_counts_and_up_to_three_examples_per_category",
        )
    ] + [
        _SEVEN + "test_callable_index_rides_in_the_tool_layer_and_counts_toward_capacity",
        _SEVEN + "test_prompt_fingerprint_binds_the_callable_index_schema",
        "tests/life/test_prompt_layers.py::test_callable_index_schema_takes_names_counts_and_up_to_three_examples",
        "tests/life/test_tool_layer.py::test_tool_layer_carries_the_callable_index_beside_the_targets",
    ],
    "浏览与提示": [
        _HINTS + name for name in (
            "test_browse_results_list_full_store_labels_and_nothing_else",
            "test_goods_seen_in_a_browse_can_be_ordered_by_name_after_leaving",
            "test_pickup_hint_goes_direct_via_a_reachable_place_or_says_the_store_cannot_be_reached",
            "test_remote_order_outside_the_delivery_area_names_reachable_places_to_order_from",
            "test_remote_order_without_a_delivery_route_says_so_in_its_own_sentence",
            "test_remote_order_hint_skips_reachable_places_the_store_cannot_deliver_to",
            "test_held_elsewhere_hint_also_points_only_to_reachable_places",
            "test_unreachable_places_stay_unlisted_while_the_hint_explains_them",
        )
    ] + [
        _WAYFINDING + "test_go_step_points_only_to_places_reachable_from_here",
        _WAYFINDING + "test_delivery_step_lists_reachable_served_places_then_a_via_then_nowhere",
        _WAYFINDING + "test_delivery_step_skips_places_without_a_working_delivery_route",
        _WAYFINDING + "test_delivery_codes_keep_exact_default_reasons_and_current_place_reasons",
        _GIVING + "test_remote_order_for_someone_outside_the_delivery_area_is_a_destination_refusal",
        _GIVING + "test_remote_order_for_someone_without_a_delivery_route_is_a_route_refusal",
        "tests/life/test_prompt_layers.py::test_browse_supplies_live_in_the_tool_layer_and_the_result_lists_names_only",
        "tests/life/test_tool_layer.py::test_permanently_impossible_actions_are_not_listed_and_the_host_corrects_them",
    ],
    "控制工具": [
        _CONCLUDE + "test_conclusion_memory_limits_are_protocol_errors_retried_in_the_same_decision",
        _CONCLUDE + "test_conclusion_memory_limits_accept_the_boundaries",
        _SCHEMA + "test_control_summaries_are_limited_to_500_characters_by_the_local_check",
        _SCHEMA + "test_control_tool_fields_carry_format_and_source_descriptions",
        _START + "test_control_requests_pair_each_task_with_its_summary",
        "tests/life/test_progress_layer_projection.py::test_activity_control_takes_one_object_per_activity",
        _CONCLUDE + "test_views_persist_as_memories_byte_identical_to_the_subject_shape",
        _CONCLUDE + "test_conclusion_sources_are_registered_by_the_system",
        "tests/life/test_persona_layer.py::"
        "test_subject_is_required_for_relationship_and_preference_and_stripped_before_memory",
        _CONTROL + "test_current_shape_items_carry_views_as_subject_memories",
        _CONTROL + "test_recorded_parallel_array_requests_are_rejected",
        _CONTROL + "test_current_requests_preserve_each_items_content_and_order",
        _CONTROL + "test_readers_project_current_items_events_consistently",
        _CONTROL + "test_unrecognised_or_malformed_requests_fail_as_integrity_errors",
        _VIEWER,
        "tests/delivery/test_windows.py::test_retired_control_request_shapes_are_rejected_by_delivery",
        _QUALITY + "test_memory_quality_reads_current_control_items_and_structured_errors",
        "tests/life/test_progress_events.py::test_conclude_arguments_omit_relationship_and_preference_memories",
        "tests/life/test_memory_evidence.py::test_write_points_read_current_control_items",
    ],
    "中文摘录": [
        _EXCERPTS + name for name in (
            "test_five_han_characters_are_rejected_and_six_accepted",
            "test_one_changed_character_does_not_match",
            "test_spaces_beside_han_are_removed_and_punctuation_is_ignored",
            "test_alphanumeric_runs_and_han_characters_add_up",
            "test_kana_count_like_han_and_nfkc_folds_width",
            "test_ellipsis_joined_segments_are_checked_one_by_one",
            "test_business_day_run_reviews_recomputed",
            "test_business_reviews_failed_under_the_old_word_count",
            "test_english_normalization_and_check_are_bit_for_bit_unchanged",
            "test_white_collar_english_reviews_are_unchanged",
            "test_reviewer_acceptance_still_requires_the_excerpt_check",
            "test_every_assessment_excerpt_is_checked_whatever_the_reviewer_decides",
        )
    ],
    "截断处理": [
        _TRUNCATION + name for name in (
            "test_a_truncated_turn_is_dropped_and_retried_once_without_thinking",
            "test_only_a_reply_that_ran_out_while_thinking_is_a_truncation",
            "test_identical_corrections_replace_each_other_across_consecutive_truncations",
            "test_a_retry_that_is_truncated_again_is_not_retried_again",
            "test_a_retry_still_without_a_tool_call_follows_the_turn_budget",
            "test_a_world_request_from_the_retry_ends_the_decision_and_breaks_the_same_second_chain",
            "test_a_rejected_retry_turn_is_dropped_so_a_continued_decision_keeps_every_thought",
            "test_a_truncation_in_a_continued_decision_leaves_the_chain_prefix_untouched",
            "test_readonly_questions_keep_their_parameters_and_a_truncated_answer_is_retried_with_finish_only",
            "test_the_exported_quality_report_counts_the_truncations_the_run_recorded",
        )
    ] + [
        _QUALITY + "test_truncations_count_length_responses_with_their_output_and_reasoning_tokens",
        _QUALITY + "test_truncation_tokens_missing_from_a_response_stay_unknown_instead_of_zero",
        _QUALITY + "test_truncations_match_the_recorded_day_runs",
    ],
    "联系人回复": [
        _GUARD + name for name in (
            "test_real_overreach_reply_hits_its_registered_categories",
            "test_overreach_reply_is_retried_once_in_the_same_settlement",
            "test_real_harmless_reply_passes_the_guard",
            "test_each_registered_alternative_catches_its_short_form",
            "test_samples_cover_every_category_and_the_fallback_passes",
            "test_fallback_reply_appears_only_when_both_attempts_hit",
            "test_reply_request_carries_sender_name_now_presence_and_receive_location",
            "test_each_incoming_message_gets_one_reply_event_even_after_a_retry",
            "test_contract_registers_the_template_guard_closed_set_and_fallback_verbatim",
            "test_quality_report_counts_guard_hits_retries_and_fallbacks",
        )
    ] + [
        _QUALITY + "test_contact_reply_guard_counts_attempts_and_replies_without_evidence_separately",
    ],
    "时间线": [
        "tests/life/test_timeline_labels.py::test_exported_timeline_names_chats_calls_and_work_in_chinese",
        _RENDER + "test_unresolvable_targets_show_only_the_chinese_kind_name",
        _RENDER + "test_work_call_and_chat_rows_name_the_task_contact_and_person",
        _RENDER + "test_real_biz_run_rows_name_the_calls_and_the_work_task",
        _TITLES + "test_work_title_is_the_task_title",
        _TITLES + "test_call_title_is_the_contact_salutation",
        _TITLES + "test_chat_title_is_the_partner_name",
        "tests/life/test_readable.py::test_chinese_kind_tables_have_a_single_source",
        _QUALITY + "test_timeline_labels_count_english_kind_names_and_internal_ids_in_the_rendered_timeline",
        _QUALITY + "test_timeline_labels_count_hexadecimal_and_hash_identifiers_as_internal_ids",
    ],
    "加载校验": [
        _LOAD + name for name in (
            "test_location_kind_outside_the_place_types_is_rejected_at_load",
            "test_same_named_locations_that_cannot_be_told_apart_are_rejected_at_load",
            "test_branch_label_equal_to_another_name_suffix_is_still_a_duplicate",
            "test_duplicate_object_labels_are_rejected_at_load",
            "test_branch_labels_make_same_named_locations_load_and_distinguishable_in_the_prompt",
            "test_branch_labels_are_checked_as_part_of_the_display_name",
            "test_existing_map_without_branch_labels_loads_unchanged",
            "test_projection_labels_are_the_load_time_labels",
        )
    ] + [
        _MAP + "test_old_map_structure_version_is_rejected",
        _MAP + "test_branch_label_must_be_nonblank_when_declared",
        _MAP + "test_selected_locations_use_display_names_and_the_map_keeps_branch_labels",
        "tests/life/test_tool_layer.py::test_repeated_readable_object_names_fail_closed_before_any_binding",
        _WARNINGS + "test_map_problems_are_frozen_as_scenario_warnings_and_creation_still_succeeds",
        WARNING_CODE_TESTS["DELIVERY_ROUTE_UNAVAILABLE"],
        WARNING_CODE_TESTS["LOCATION_NAME_SUSPICIOUS"],
    ],
    "目标推进": [
        _AROUND + "test_sleep_begun_before_midnight_does_not_progress_a_sleep_goal_around_2330",
        _AROUND + "test_a_morning_browse_does_not_progress_an_evening_entertainment_goal",
        _AROUND + "test_a_lunch_goal_around_noon_is_progressed_by_eating_from_1020_not_from_0930",
        _DAY_PLAN + "test_progress_with_an_around_time_requires_the_activity_to_run_near_it",
        _DAY_PLAN + "test_around_time_skips_a_goal_outside_its_window_and_needs_the_actual_start",
        _DAY_PLAN + "test_around_time_is_required_nullable_hh_mm_and_needs_no_later_than_now",
        _DAY_PLAN + "test_revision_compares_four_fields_and_changing_only_around_keeps_registration",
        _DAY_PLAN + "test_routine_intents_are_the_four_routine_kinds_of_the_closed_set",
        _PLANNING + "test_revision_carries_around_time_and_changing_only_it_keeps_registration",
        _PLANNING + "test_zero_hour_plan_items_carry_around_time_and_a_malformed_one_is_retried",
        "tests/life/test_todo_layer.py::test_day_plan_lists_eight_goals_in_plan_order_with_progress_and_reminders",
        _GOAL_METRICS + "test_goal_with_an_around_time_counts_only_activity_that_ran_near_it",
        _GOAL_METRICS + "test_changing_only_the_around_time_keeps_the_instance_and_moves_its_window",
        _GOAL_METRICS + "test_routine_goals_are_reported_apart_from_the_progress_ratio",
        _GOAL_EXPORT,
    ],
    "strict 端点": [
        _PROBE + name for name in (
            "test_strict_probe_functions_bypass_the_local_schema_check",
            "test_report_sends_every_probe_request_and_classifies_the_results",
            "test_conclusions_need_the_specific_outcomes_not_just_any_failure",
            "test_requests_use_the_host_conclude_tool_and_stage_parameters",
            "test_enum_violations_count_invalid_json_and_keep_missing_calls_and_failures_apart",
            "test_argument_value_reads_one_key_of_the_named_call",
            "test_strict_response_outcomes",
            "test_host_rejection_is_separate_from_shape_errors",
            "test_main_reads_the_worldmock_key_pool_prints_and_writes_the_report",
            "test_tool_call_classification_edges",
            "test_default_transport_posts_json_and_records_failures",
            "test_main_reports_a_missing_key_without_sending_requests",
        )
    ] + [
        _ENDPOINT + "test_validate_checks_a_stage_identity_on_the_strict_endpoint",
        _ENDPOINT + "test_validate_refuses_the_main_site",
        _ENDPOINT + "test_resume_refuses_a_parent_run_frozen_on_the_main_site",
        _IDENTITY,
        "tests/life/test_coverage_closure_models.py::test_model_identity_accepts_only_the_configured_deepseek_endpoint",
        "tests/profile/test_extractor.py::test_request_payload_shape",
    ],
    "复跑指标（只报告）": [
        _QUALITY + "test_input_warnings_section_counts_the_frozen_manifest_warnings",
        _QUALITY + "test_truncations_count_length_responses_with_their_output_and_reasoning_tokens",
        _QUALITY + "test_truncations_match_the_recorded_day_runs",
        _QUALITY + "test_timeline_labels_count_english_kind_names_and_internal_ids_in_the_rendered_timeline",
        _QUALITY + "test_contact_reply_guard_counts_attempts_and_replies_without_evidence_separately",
        _QUALITY + "test_name_rejections_are_counted_apart_from_other_protocol_errors",
        _QUALITY + "test_memory_quality_reads_current_control_items_and_structured_errors",
        _QUALITY + "test_report_sections_are_the_documented_grouping_without_a_language_count",
        _GOAL_METRICS + "test_routine_goals_are_reported_apart_from_the_progress_ratio",
        _GOAL_EXPORT,
        _PURCHASES + "test_real_run_export_counts_orders_written_with_way_synonyms",
        _PURCHASES + "test_order_attempts_classify_written_ways_through_the_way_synonym_table",
    ],
    "文档一致性": [
        _DOC_GATE,
        "tests/test_design_doc_build.py::"
        "test_repository_chapters_assemble_deterministically_and_the_committed_html_is_current",
        _DOCS_MARKERS,
        _DOCS_ANCHORS,
    ],
    "工程门禁": [
        "tests/test_style_gate.py::test_style_gate_admits_no_new_violations",
        "tests/test_coverage_gate.py::test_branch_pass_and_fail_states",
        "tests/test_coverage_gate.py::test_uncovered_public_interface_detection",
        _DOC_GATE,
    ],
}

#: 只报告的台账行：规格写明不作门禁；登记的测试只验证它所读的质量报告各节，行本身的真实运行不在测试中执行。
REPORT_ONLY: dict[str, str] = {
    "复跑指标（只报告）": "WC 与 BIZ 全天复跑按规格只报告、不作门禁，需要用户的模型账号与修复后的数据集；登记的测试验证复跑要读的"
                        "质量报告各节（输入警告、截断、时间线标签、联系人回复守卫、名称类拒绝、收尾形状类协议错误与零点计划的"
                        "非例行推进比例）与导出的 purchases.jsonl 购买事实（按方式同义表计入的尝试下单，「规格消费全部可成交」据此核对）。",
}

#: 「裁决记录」两张表中状态为已定、定下了要建之物的裁决：编号 → 验证其定下的做法（推荐列或结论列）的测试节点。
RULINGS: dict[str, list[str]] = {
    "D1": [
        WARNING_CODE_TESTS["TASTE_GENRES_UNMATCHED"],
        _RELIEF + "test_runtime_computes_game_weights_with_the_content_algorithm",
        _RELIEF + "test_disliked_content_is_relieved_at_the_floor_without_awake_accrual",
        "tests/profile/test_coverage.py::test_app_name_substrings_no_longer_count_as_coverage_hits",
    ],
    "U1": [
        _BILLING + "test_sleep_completing_at_a_boundary_leaves_social_need_unchanged",
        _BILLING + "test_sleep_ended_early_by_a_reminder_leaves_social_need_unchanged",
        _BILLING + "test_social_ledger_awake_classification_follows_the_settlement_activity_ids",
    ],
    "U2": [
        _NAMES + "test_exact_normalised_and_candidate_paths_through_host_and_engine",
        _NAMES + "test_candidates_are_feasible_first_and_submittable_verbatim",
        _NAMES + "test_unlisted_catalog_objects_are_callable_by_name_with_the_usual_checks",
        _NAMED + "test_unknown_name_is_rejected_with_near_candidates_and_nothing_is_substituted",
        _INDEX + "test_every_name_in_the_index_is_callable_by_name_and_accepted",
    ],
    "U3": [
        _SEVEN + "test_rule_14_makes_the_routine_the_default_and_the_day_plan_override_it",
        _SEVEN + "test_system_layers_are_the_contract_registration_verbatim",
    ],
    "D2": [
        _BILLING + "test_social_ledger_awake_classification_follows_the_settlement_activity_ids",
        _BILLING + "test_sleep_completing_at_a_boundary_leaves_social_need_unchanged",
        _BILLING + "test_a_call_ending_at_the_boundary_is_billed_once_by_the_call_ledger",
        _BILLING + "test_ledger_without_a_draft_falls_back_to_activities_ending_now",
    ],
    "D3": [
        _RELIEF + "test_single_and_shared_paths_settle_the_same_net_effect",
        _RELIEF + "test_committed_body_settlement_relieves_without_accrual",
        _RELIEF + "test_watching_from_full_need_reaches_the_lower_band_in_the_specified_time",
        _SCENARIO + "test_relief_preference_floor_defaults_to_half_without_changing_scenario_files",
        _SCENARIO + "test_declared_relief_preference_floor_is_assembled_into_body_config",
        _SCENARIO + "test_relief_preference_floor_is_registered_once_in_the_experiment_hypotheses",
    ],
    "D4": [
        _WARNINGS + "test_validate_still_fails_below_the_coverage_threshold_after_printing_the_warnings",
        _WARNINGS + "test_run_creation_freezes_input_warnings_into_the_manifest_ops_log_and_quality_report",
        WARNING_CODE_TESTS["COVERAGE_BELOW_THRESHOLD"],
    ],
    "D5": [
        _TWO_PROCESS + "TestSleepinessGradeBands::test_grade_boundaries",
        _TWO_PROCESS + "TestSleepinessGradeBands::test_band_edges_sit_on_the_kss_equivalent_cuts",
        _TWO_PROCESS + "TestWeekdaySteadyStateOnsets::test_band_onsets_match_the_spec",
        _TWO_PROCESS + "TestWeekdaySteadyStateOnsets::test_the_same_steady_state_reproduces_the_retired_edges_row",
        _FELT + "test_sleepiness_band_names_and_count_are_unchanged",
    ],
    "D6": [
        _SEVEN + "test_rule_14_makes_the_routine_the_default_and_the_day_plan_override_it",
        _SEVEN + "test_felt_bands_named_by_the_rules_are_the_felt_band_texts",
    ],
    "D7": [
        _NAMES + "test_two_name_rejections_in_one_second_are_free_and_the_third_counts",
        _NAMES + "test_each_person_has_their_own_two_free_name_rejections",
        _NAMES + "test_a_name_rejection_beside_other_rejections_follows_the_same_count",
        _NAMES + "test_host_and_environment_share_one_name_class_closed_set",
    ],
    "D8": [
        _CHANNELS + "test_each_from_form_resolves_to_its_source_scope",
        _CHANNELS + "test_a_channel_name_is_compared_without_spaces_or_case",
        _CHANNELS + "test_several_member_stores_choose_the_nearest_and_note_it",
        _CHANNELS + "test_member_store_ties_break_by_landed_price_then_store_identity",
        _REPLAY + "test_wc_named_purchase_requests_are_accepted_in_an_offline_replay",
    ],
    "D9": [
        "tests/life/test_progress_layer_projection.py::test_activity_control_takes_one_object_per_activity",
        _CONCLUDE + "test_views_persist_as_memories_byte_identical_to_the_subject_shape",
        _CONCLUDE + "test_conclusion_memory_limits_are_protocol_errors_retried_in_the_same_decision",
        _CONTROL + "test_readers_project_current_items_events_consistently",
    ],
    "D10": [
        _TRUNCATION + "test_a_truncated_turn_is_dropped_and_retried_once_without_thinking",
        _TRUNCATION + "test_identical_corrections_replace_each_other_across_consecutive_truncations",
        _TRUNCATION + "test_a_retry_that_is_truncated_again_is_not_retried_again",
        _TRUNCATION + "test_the_exported_quality_report_counts_the_truncations_the_run_recorded",
    ],
    "D11": [
        _GUARD + "test_overreach_reply_is_retried_once_in_the_same_settlement",
        _GUARD + "test_fallback_reply_appears_only_when_both_attempts_hit",
        _GUARD + "test_reply_request_carries_sender_name_now_presence_and_receive_location",
        _GUARD + "test_contract_registers_the_template_guard_closed_set_and_fallback_verbatim",
    ],
    "D12": [
        _AROUND + "test_sleep_begun_before_midnight_does_not_progress_a_sleep_goal_around_2330",
        _AROUND + "test_a_lunch_goal_around_noon_is_progressed_by_eating_from_1020_not_from_0930",
        _DAY_PLAN + "test_around_time_is_required_nullable_hh_mm_and_needs_no_later_than_now",
        _PLANNING + "test_revision_carries_around_time_and_changing_only_it_keeps_registration",
    ],
    "D14": [
        _PROBE + "test_report_sends_every_probe_request_and_classifies_the_results",
        _ENDPOINT + "test_validate_checks_a_stage_identity_on_the_strict_endpoint",
        _ENDPOINT + "test_validate_refuses_the_main_site",
        _IDENTITY,
    ],
    "D15": [
        _PULSE + "test_messages_delivered_during_sleep_are_read_at_the_first_boundary_after_waking",
        _PULSE + "test_the_boundary_decision_segment_reads_no_message_while_asleep",
        _PULSE + "test_a_round_observed_while_asleep_marks_no_message_read",
    ],
    "D16": [
        WARNING_CODE_TESTS["CARD_FIELD_OVERRIDDEN"],
        _INJECTION + "test_only_explicitly_declared_fields_are_overridden",
        _WARNINGS + "test_a_scenario_declaring_only_the_midpoint_changes_only_the_midpoint_in_the_request",
        _WARNINGS + "test_body_settlement_day_plan_inputs_and_the_persona_layer_read_the_same_rhythm",
    ],
    "D17": [
        _HINTS + "test_goods_seen_in_a_browse_can_be_ordered_by_name_after_leaving",
        _HINTS + "test_browse_results_list_full_store_labels_and_nothing_else",
    ],
    "D18": [
        _SEVEN + "test_the_system_layer_asks_for_no_particular_language",
        _QUALITY + "test_report_sections_are_the_documented_grouping_without_a_language_count",
        _SEVEN + "test_system_layers_are_the_contract_registration_verbatim",
    ],
    "D19": [
        _LOAD + "test_branch_labels_make_same_named_locations_load_and_distinguishable_in_the_prompt",
        _LOAD + "test_same_named_locations_that_cannot_be_told_apart_are_rejected_at_load",
        _WARNINGS + "test_map_problems_are_frozen_as_scenario_warnings_and_creation_still_succeeds",
        WARNING_CODE_TESTS["LOCATION_NAME_SUSPICIOUS"],
        WARNING_CODE_TESTS["DELIVERY_ROUTE_UNAVAILABLE"],
    ],
    "S1": [
        _RELIEF + "test_runtime_computes_game_weights_with_the_content_algorithm",
        _RELIEF + "test_games_without_genre_declarations_fall_to_the_floor",
        "tests/integration/test_host_branch_coverage.py::test_build_society_create_mode_injects_taste_weights",
    ],
    "S3": [
        _SEVEN + "test_rule_14_makes_the_routine_the_default_and_the_day_plan_override_it",
        _SEVEN + "test_system_layers_are_the_contract_registration_verbatim",
    ],
    "S4": [
        _PRESENT + "test_relevance_puts_time_window_goal_family_and_habit_hits_ahead",
        _PRESENT + "test_zero_taste_order_is_not_alphabetical_and_changes_with_the_date",
        _RELEVANCE + "test_points_are_the_three_adopted_terms",
        _CANDIDATES + "TestOrdering::test_hash_of_scenario_person_date_and_object_breaks_ties",
    ],
    "S5": [
        _INDEX + "test_every_name_in_the_index_is_callable_by_name_and_accepted",
        _INDEX + "test_the_index_leaves_out_activities_excluded_by_location_kind",
        _INDEX + "test_the_index_is_not_a_target_and_the_presentation_cap_ignores_it",
        _SEVEN + "test_callable_index_rides_in_the_tool_layer_and_counts_toward_capacity",
    ],
    "S6": [
        _PROBE + "test_report_sends_every_probe_request_and_classifies_the_results",
        _PROBE + "test_strict_probe_functions_bypass_the_local_schema_check",
        _PROBE + "test_conclusions_need_the_specific_outcomes_not_just_any_failure",
        _ENDPOINT + "test_validate_checks_a_stage_identity_on_the_strict_endpoint",
    ],
    "S7": [
        _CHANNELS + "test_channels_are_derived_from_the_pinned_phone_config_at_load",
        _COVERAGE_APPS + "test_channels_group_sellers_by_app_and_list_the_places_where_they_have_supplies",
        _PURCHASES + "test_purchase_rows_carry_the_order_channel_or_no_channel",
        _CHANNELS + "test_a_phone_config_that_fails_verification_derives_no_channels",
    ],
    "S8": [
        _PRESENT + "test_lifestyle_window_excludes_location_kind_mismatches_with_the_reason_code",
        _NAMES + "test_unlisted_catalog_objects_are_callable_by_name_with_the_usual_checks",
        _INDEX + "test_the_index_leaves_out_activities_excluded_by_location_kind",
    ],
    "S9": [
        _HINTS + "test_unreachable_places_stay_unlisted_while_the_hint_explains_them",
        _HINTS + "test_pickup_hint_goes_direct_via_a_reachable_place_or_says_the_store_cannot_be_reached",
        "tests/life/test_tool_layer.py::test_permanently_impossible_actions_are_not_listed_and_the_host_corrects_them",
    ],
    "S10": [
        _NAMED + "test_several_servable_matches_follow_prefer_and_never_report_ambiguity",
        _STAGES + "test_system_purchase_goes_in_store_when_here_and_remote_when_deliverable",
        _STAGES + "test_system_purchase_that_can_neither_pick_up_nor_deliver_names_the_nearest_store",
        _STAGES + "test_system_purchase_next_step_names_the_nearest_store_that_sells_it_in_store",
        _GIVING + "test_system_purchase_for_someone_goes_in_store_only_when_they_are_there",
        _GIVING + "test_system_purchase_for_someone_it_cannot_reach_says_to_buy_for_oneself_first",
        _STAGES + "test_system_purchase_is_accepted_for_listed_and_named_goods_and_never_listed",
        _STAGES + "test_system_purchase_and_a_null_from_each_skip_only_their_own_stage",
        _SCHEMA + "test_start_activity_uses_readable_frozen_objects_and_paired_actions",
    ],
    "S11": [
        _PLANNING + "test_plan_template_is_the_contract_text_and_asks_only_for_differences_with_around_times",
        _PLANNING + "test_wake_time_noted_at_conclusion_still_becomes_a_reminder_that_ends_the_sleep",
        _GOAL_METRICS + "test_routine_goals_are_reported_apart_from_the_progress_ratio",
        _SEVEN + "test_rule_14_makes_the_routine_the_default_and_the_day_plan_override_it",
    ],
}

#: 只断言状态、不登记测试的裁决：D13 登记为范围外（工作材料正文另立规格）、S2 撤回（并入 S11）、U4 已定为不为小睡另加机制
#: （没有要建之物）。编号 → 状态列必须保持的开头。
STATUS_ONLY: dict[str, str] = {
    "U4": "已定",
    "D13": "登记",
    "S2": "撤回",
}

#: 本特性新增或改动的生产文件：撰写时由 `git diff --name-only --diff-filter=AM 26d626b -- src custom scripts tools` 得出后
#: 冻结（26d626b 是本特性规格初稿的提交，代码与 4a5b0e6 相同）；另一会话的设计文档汇编提交（14a6cfe、185cad7）新增的
#: scripts/build_design_doc.py 不属于本特性，不列入。延期标记扫描逐个检查这些文件的全文。
FEATURE_PRODUCTION_FILES: tuple[str, ...] = (
    "custom/agents/life_agent.py",
    "custom/agents/life_feedback.py",
    "custom/agents/life_memory.py",
    "custom/agents/life_prompt.py",
    "custom/agents/life_truncation.py",
    "custom/envs/life_env.py",
    "scripts/probe_deepseek_strict.py",
    "scripts/render_day.py",
    "src/worldmock/corpora/map_prepare.py",
    "src/worldmock/delivery/windows.py",
    "src/worldmock/evaluation/goal_metrics.py",
    "src/worldmock/evaluation/public_evidence.py",
    "src/worldmock/footprints/purchases.py",
    "src/worldmock/footprints/quality.py",
    "src/worldmock/footprints/timeline_labels.py",
    "src/worldmock/life/activities.py",
    "src/worldmock/life/activity_control.py",
    "src/worldmock/life/activity_titles.py",
    "src/worldmock/life/admission.py",
    "src/worldmock/life/basket.py",
    "src/worldmock/life/body.py",
    "src/worldmock/life/callable_index.py",
    "src/worldmock/life/candidates.py",
    "src/worldmock/life/contact_exchange.py",
    "src/worldmock/life/content.py",
    "src/worldmock/life/control_items.py",
    "src/worldmock/life/day_plan_inputs.py",
    "src/worldmock/life/day_planning.py",
    "src/worldmock/life/engine.py",
    "src/worldmock/life/environment_layer.py",
    "src/worldmock/life/error_contract.py",
    "src/worldmock/life/felt.py",
    "src/worldmock/life/guidance.py",
    "src/worldmock/life/interaction_menu.py",
    "src/worldmock/life/load_checks.py",
    "src/worldmock/life/map_data.py",
    "src/worldmock/life/map_models.py",
    "src/worldmock/life/memory_evidence.py",
    "src/worldmock/life/memory_layer.py",
    "src/worldmock/life/models.py",
    "src/worldmock/life/motivation.py",
    "src/worldmock/life/name_resolution.py",
    "src/worldmock/life/named_catalog.py",
    "src/worldmock/life/named_purchase.py",
    "src/worldmock/life/narration.py",
    "src/worldmock/life/object_labels.py",
    "src/worldmock/life/observe.py",
    "src/worldmock/life/order_routing.py",
    "src/worldmock/life/presentation.py",
    "src/worldmock/life/progress_layer.py",
    "src/worldmock/life/projection.py",
    "src/worldmock/life/prompt_capacity.py",
    "src/worldmock/life/prompt_schemas.py",
    "src/worldmock/life/purchase_offers.py",
    "src/worldmock/life/readable.py",
    "src/worldmock/life/relevance.py",
    "src/worldmock/life/reply_guard.py",
    "src/worldmock/life/result_projection.py",
    "src/worldmock/life/rhythm.py",
    "src/worldmock/life/rotation.py",
    "src/worldmock/life/routing.py",
    "src/worldmock/life/same_moment.py",
    "src/worldmock/life/scenario.py",
    "src/worldmock/life/schedule.py",
    "src/worldmock/life/semantics.py",
    "src/worldmock/life/session_actions.py",
    "src/worldmock/life/settled_text.py",
    "src/worldmock/life/sleep.py",
    "src/worldmock/life/social.py",
    "src/worldmock/life/storefront_runtime.py",
    "src/worldmock/life/todo_layer.py",
    "src/worldmock/life/tool_layer.py",
    "src/worldmock/life/wayfinding.py",
    "src/worldmock/life/work.py",
    "src/worldmock/life/work_review.py",
    "src/worldmock/model_endpoint.py",
    "src/worldmock/profile/__init__.py",
    "src/worldmock/profile/coverage.py",
    "src/worldmock/profile/coverage_apps.py",
    "src/worldmock/profile/extractor.py",
    "src/worldmock/profile/injection.py",
    "src/worldmock/profile/taste.py",
    "src/worldmock/runtime.py",
    "src/worldmock/validation.py",
    "tools/run-viewer.html",
)

#: 本特性新增的 Python 测试文件（`git diff --name-only --diff-filter=A 26d626b -- tests` 中的 .py，除去设计文档汇编提交新增的
#: tests/test_design_doc_build.py）与本守卫、共用判定模块自身。改动的既有测试文件不扫描（其中有平台条件跳过的豁免文件）；
#: tests/fixtures/ 下新增的 JSON 是从真实运行复制的记录，不是代码，也不扫描。
FEATURE_TEST_FILES: tuple[str, ...] = (
    "tests/acceptance_guard.py",
    "tests/dayrun_fidelity_acceptance.py",
    "tests/integration/test_host_feedback.py",
    "tests/integration/test_input_warnings.py",
    "tests/integration/test_output_truncation.py",
    "tests/integration/test_presentation_habits.py",
    "tests/integration/test_strict_endpoint.py",
    "tests/life/purchase_worlds.py",
    "tests/life/test_browse_and_hints.py",
    "tests/life/test_callable_index.py",
    "tests/life/test_contact_reply_guard.py",
    "tests/life/test_control_items.py",
    "tests/life/test_control_name_rejections.py",
    "tests/life/test_discomfort_and_sleep_messages.py",
    "tests/life/test_error_contract.py",
    "tests/life/test_goal_around_time.py",
    "tests/life/test_load_checks.py",
    "tests/life/test_name_resolution.py",
    "tests/life/test_presentation_fidelity.py",
    "tests/life/test_purchase_channels.py",
    "tests/life/test_purchase_stages.py",
    "tests/life/test_relevance.py",
    "tests/life/test_relief_floor.py",
    "tests/life/test_sleep_billing.py",
    "tests/life/test_timeline_labels.py",
    "tests/life/test_wayfinding.py",
    "tests/life/test_wc_named_purchase_replay.py",
    "tests/life/test_work_review_excerpts.py",
    "tests/profile/test_coverage_apps.py",
    "tests/profile/test_injection.py",
    "tests/test_dayrun_fidelity_acceptance.py",
    "tests/test_probe_deepseek_strict.py",
)
