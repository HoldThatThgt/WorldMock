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
STAGES_DONE = frozenset({"0", "1", "6"})

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

#: 阶段 1 预先登记：候选时刻行由阶段 2 完成（困意、饥饿预测与餐时窗口）。
_CANDIDATE_TIME_TESTS = [
    _SAME_MOMENT + "test_first_decision_includes_reconsideration_candidate",
    _SAME_MOMENT + "test_idle_interval_only_while_waiting_without_commitments",
    _SAME_MOMENT + "test_game_start_follows_decision_rhythm",
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
    "会话、活动与记忆": {"stage": "4", "tests": []},
    "本机运行修复": {"stage": "7", "tests": _RUN_IDENTITY_TESTS},
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
_RUNTIME = "src/worldmock/runtime.py"
_EXPERIMENT = "docs/EXPERIMENT.md"
_MANUAL_RUNTIME = "docs/manual/runtime.md"
_AS2_LOCKED_DIGEST = "65a902ec66df42296d4e4c85e424bce3512e437133fc6be9fa597766fc30a334"

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
