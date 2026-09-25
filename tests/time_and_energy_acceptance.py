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
STAGES_DONE = frozenset({"0"})

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

#: 验收台账行（首列原文）→ 归属阶段与证据测试节点。
ACCEPTANCE: dict[str, dict[str, object]] = {
    "绿色基线": {"stage": "0", "tests": _GREEN_BASELINE_TESTS},
    "同刻决策": {"stage": "4", "tests": []},
    "候选时刻": {"stage": "2", "tests": []},
    "唤醒词表": {"stage": "2", "tests": []},
    "能量感知": {"stage": "2", "tests": []},
    "能量不拦人": {"stage": "4", "tests": []},
    "零点计划与提醒": {"stage": "5", "tests": []},
    "五个动词与并行": {"stage": "4", "tests": []},
    "常驻提示": {"stage": "5", "tests": []},
    "会话、活动与记忆": {"stage": "4", "tests": []},
    "本机运行修复": {"stage": "7", "tests": []},
    "交付": {"stage": "3", "tests": _DELIVERY_TESTS},
    "无新增故障": {"stage": "7", "tests": []},
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
}
