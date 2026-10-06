"""工具交互的行为登记与源码变异清单；变异经共用执行器在隔离子进程里生效，验收不修改共享工作树。"""

from __future__ import annotations

from pathlib import Path

from tests import mutation_runner
from tests.mutation_runner import CHILD_ENTRY, REPO_ROOT, RunSettings, SourceMutation

#: 本台账的回执目录与两种超时（基线、单个变异），取共用执行器上移之前写死的值。
RECEIPTS = REPO_ROOT / ".work/tool-interaction/mutation-receipts"
BASELINE_TIMEOUT_SECONDS = 600
MUTATION_TIMEOUT_SECONDS = 180


BEHAVIOR = {'八点普通工作通知，八点五十五到公司': ('tests/life/test_tool_interaction_runtime.py::test_ordinary_work_opens_at_first_notification_and_wrong_place_is_explicit',),
 '提前收到十点预约会议': ('tests/life/test_tool_interaction_runtime.py::test_removed_input_opening_is_rejected_and_appointment_is_explicit',
                'tests/life/test_tool_interaction_runtime.py::test_appointment_wakes_only_at_real_opening_and_never_wakes_sleep'),
 '未读、阅读与睡眠': ('tests/life/test_tool_interaction_runtime.py::test_notification_coverage_checks_every_notice',
              'tests/life/test_tool_interaction_runtime.py::test_mail_reading_keeps_actual_body_and_only_full_task_name',
              'tests/life/test_tool_interaction_runtime.py::test_appointment_mail_reveals_only_assignee_without_false_opening'),
 '全部接口错误入口': ('tests/life/test_tool_protocol.py::test_purchase_preference_closed_values_are_complete',
              'tests/life/test_tool_protocol.py::test_nonpurchase_preference_preserves_json_null',
              'tests/life/test_tool_protocol.py::test_strict_nested_type_positions_are_preserved',
              'tests/life/test_tool_protocol.py::test_engine_revalidates_constructed_bad_requests_with_complete_fields',
              'tests/life/test_tool_protocol.py::test_root_validator_keeps_argument_root_and_original_json',
              'tests/life/test_tool_protocol.py::test_cross_field_interval_and_goal_bounds_keep_explicit_locations',
              'tests/life/test_purchase_apps.py::test_native_purchase_routing_derives_way_without_action_field',
                    'tests/life/test_tool_protocol.py::test_parser_reports_malformed_json_and_unknown_tool_complete_locations',
              'tests/integration/test_host_failure_stages.py::test_model_service_failure_persists_stage_without_parsing_exception_text',
              'tests/integration/test_host_failure_stages.py::test_evidence_write_failure_stops_with_structured_stage',
              'tests/integration/test_host_failure_stages.py::test_tool_service_failure_points_to_real_call_without_claiming_execution',
              'tests/life/test_tool_interaction_runtime.py::test_plan_times_display_exact_declared_local_format'),
 '原子批次': ('tests/life/test_tool_protocol.py::test_read_batch_reports_every_error_with_real_call_indices',
          'tests/life/test_tool_protocol.py::test_world_query_mixed_batches_are_atomic',
          'tests/life/test_tool_protocol.py::test_route_errors_return_null_only_and_all_independent_fields'),
 '购买与食用': ('tests/life/test_commerce.py::test_no_app_store_pickup_is_immediate_local_holding',
           'tests/life/test_commerce.py::test_multiple_food_rows_pay_one_fee_one_transfer_and_one_delivery',
           'tests/life/test_rrs_food_body.py::test_food_split_pause_resume_and_repeated_progress_conserve_raw_intake'),
 '查询与预算': ('tests/life/test_world_query.py::test_world_query_paginates_complete_frozen_set_and_preserves_false_and_zero',
           'tests/life/test_world_query.py::test_world_query_offset_requires_nonnegative_json_integer',
           'tests/life/test_world_query.py::test_world_query_real_engine_reads_all_goods_without_browse_or_world_changes',
           'tests/life/test_world_query.py::test_environment_read_query_keeps_world_clock_round_and_store_unchanged',
           'tests/life/test_world_query.py::test_world_query_does_not_return_internal_identity_or_private_evidence',
           'tests/life/test_tool_protocol.py::test_final_turn_and_truncation_retry_disable_every_query',
           'tests/life/test_tool_interaction_runtime.py::test_read_queries_keep_actual_model_exchange_without_progress_events'),
 '操作备忘': ('tests/life/test_tool_interaction_runtime.py::test_memo_is_derived_across_sessions_with_acceptance_and_clears_next_day',
          'tests/life/test_tool_interaction_runtime.py::test_memo_and_appointment_survive_persistent_reopen'),
 '紧凑呈现': ('tests/life/test_tool_interaction_runtime.py::test_compact_layer_preserves_false_zero_contract_and_control_tools',
          'tests/life/test_tool_interaction_runtime.py::test_actual_frozen_tool_layer_and_prompt_groups_restore_identically',
          'tests/life/test_tool_interaction_runtime.py::test_compact_prompt_continuation_preserves_head_and_exact_target_changes'),
 '文档与身份': ('tests/test_documentation_gate.py::test_real_repository_documents_and_source_references_resolve',
           'tests/test_design_doc_build.py::test_repository_chapters_assemble_deterministically_and_the_committed_html_is_current')}

MUTATIONS = (
    SourceMutation(
        'pagination_tail',
        'worldmock.life.world_query::query_world',
        '"next_offset": end if end < len(entries) else None',
        '"next_offset": None',
        (
            (
                'tests/life/test_world_query.py::test_world_query_paginates_complete_frozen_set_and_p'
                'reserves_false_and_zero'
            ),
        ),
    ),
    SourceMutation(
        'pagination_offset',
        'worldmock.life.world_query::query_world',
        '"next_offset": end if end < len(entries) else None',
        '"next_offset": end + 1 if end < len(entries) else None',
        (
            (
                'tests/life/test_world_query.py::test_world_query_paginates_complete_frozen_set_and_p'
                'reserves_false_and_zero'
            ),
        ),
    ),
    SourceMutation(
        'pagination_supply_window',
        'worldmock.life.world_query::_matching_entries.entries',
        'entries = observation["world_query_bindings"]',
        'entries = observation["world_query_bindings"][:observation["world_query_page_size"]]',
        (
            (
                'tests/life/test_world_query.py::test_world_query_paginates_complete_frozen_set_and_p'
                'reserves_false_and_zero'
            ),
        ),
    ),


)

MUTATIONS += (










    SourceMutation(
        'habit_media_progress_not_persisted',
        'worldmock.life.content::reveal_watch.changed',
        'changed = agent.model_copy(update={"content_progress": mapping}, deep=True)',
        'changed = agent.model_copy(deep=True)',
        (
            (
                'tests/life/test_tool_interaction_habits.py::test_declared_habit_runs_with_real_prere'
                'quisites_and_settlement[lunch-break-media]'
            ),
        ),
    ),
    SourceMutation(
        'work_delay_nine',
        'worldmock.life.scenario::runtime_tasks.opening',
        'min(item.sent_at for item in notices) if task.appointment is None',
        'min(item.sent_at for item in notices).replace(hour=9) if task.appointment is None',
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_ordinary_work_opens_at_first_notif'
                'ication_and_wrong_place_is_explicit'
            ),
        ),
    ),
    SourceMutation(
        'duplicate_input_opening',
        'worldmock.life.scenario::WorkTaskConfig.appointment',
        '    appointment: WorkAppointment | None\n',
        '    appointment: WorkAppointment | None\n    available_at: datetime\n',
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_removed_input_opening_is_rejected_'
                'and_appointment_is_explicit'
            ),
        ),
    ),
    SourceMutation(
        'appointment_mail_false_opening',
        'worldmock.scheduling.compile::compile_schedule',
        'due = int(task.available_at.timestamp())',
        (
            'due = int(min(mail.sent_at for mail in scenario.messages if mail.related_task_id == '
            'task.task_id).timestamp())'
        ),
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_appointment_mail_reveals_only_assi'
                'gnee_without_false_opening'
            ),
        ),
    ),
    SourceMutation(
        'cross_person_task_leak',
        'worldmock.scheduling.effects::_reveal_event',
        (
            'agents[agent.agent_id] = agent\n    changed = state.model_copy(update={"agents": agen'
            'ts}, deep=True)\n    return ScheduledOutcome(changed, dict(event.data))'
        ),
        (
            'agents[2] = agent\n    changed = state.model_copy(update={"agents": agents}, deep=Tru'
            'e)\n    return ScheduledOutcome(changed, dict(event.data))'
        ),
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_appointment_mail_reveals_only_assi'
                'gnee_without_false_opening'
            ),
        ),
    ),
    SourceMutation(
        'reminder_changes_appointment_opening',
        'worldmock.life.scenario::runtime_tasks.opening',
        'else task.appointment.starts_at',
        'else max(item.sent_at for item in notices)',
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_removed_input_opening_is_rejected_'
                'and_appointment_is_explicit'
            ),
        ),
    ),
    SourceMutation(
        'read_mail_body_lost',
        'worldmock.life.result_projection::_read_mail.mail',
        'mail = {"subject": revealed["subject"], "body": revealed["body"]}',
        'mail = {"subject": revealed["subject"], "body": ""}',
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_mail_reading_keeps_actual_body_and'
                '_only_full_task_name'
            ),
        ),
    ),
    SourceMutation(
        'memo_lost',
        'worldmock.life.progress_layer::rejected_today.latest',
        'latest = sorted(rows.values(), key=lambda item: item["sequence"], reverse=True)[:8]',
        'latest = []',
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_memo_is_derived_across_sessions_wi'
                'th_acceptance_and_clears_next_day'
            ),
        ),
    ),
    SourceMutation(
        'memo_cross_day_leak',
        'worldmock.life.progress_layer::rejected_today.events',
        'and local_date(event.occurred_at, context.scenario.timezone) == day]',
        'and True]',
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_memo_is_derived_across_sessions_wi'
                'th_acceptance_and_clears_next_day'
            ),
        ),
    ),
    SourceMutation(
        'compact_drops_false',
        'worldmock.life.tool_groups::compact',
        'if item is not None and item != [] and item != {}',
        'if item and item != [] and item != {}',
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_compact_layer_preserves_false_zero'
                '_contract_and_control_tools'
            ),
        ),
    ),
    SourceMutation(
        'compact_drops_control',
        'worldmock.life.tool_groups::compact_tool_layer.actions',
        'actions = target["actions"] or [None]',
        'actions = target["actions"]',
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_compact_layer_preserves_false_zero'
                '_contract_and_control_tools'
            ),
        ),
    ),
    SourceMutation(
        'appointment_newly_known_false_wake',
        'worldmock.life.rhythm::_wake_snapshot',
        'task_newly_visible="task_available" in flags,',
        'task_newly_visible=bool(set(agent.visible_task_ids) - agent.known_task_ids) or "task_available" in flags,',
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_appointment_wakes_only_at_real_ope'
                'ning_and_never_wakes_sleep'
            ),
        ),
    ),
    SourceMutation(
        'memo_limit_omitted',
        'worldmock.life.progress_layer::rejected_today.latest',
        'latest = sorted(rows.values(), key=lambda item: item["sequence"], reverse=True)[:8]',
        'latest = sorted(rows.values(), key=lambda item: item["sequence"], reverse=True)',
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_memo_caps_recent_entries_and_reaso'
                'n_without_truncating_audit'
            ),
        ),
    ),
    SourceMutation(
        'query_results_leak_into_progress',
        'worldmock.life.progress_layer::_session_records.records',
        '    records = _tool_records(context, store, lower)',
        (
            '    records = _tool_records(context, store, lower)\n    for response in store.read_ag'
            'ent_events("model_response", context.agent_id, after=lower):\n        for call in _to'
            'ol_calls(response):\n            name = call.get("function", {}).get("name")\n        '
            '    if name not in {"memory_recent", "memory_search", "memory_time_range"}:\n        '
            '        continue\n            moment = iso_time(response.occurred_at, context.scenari'
            'o.timezone)\n            result = {"occurred_at": moment, "status": "completed", "err'
            'or_code": None,\n                      "message": "Memory query returned 0 episodes",'
            ' "details": {"episodes": 0}}\n            record = {"occurred_at": moment, "type": "t'
            'ool_call", "tool": name,\n                      "arguments": {}, "result": result}\n  '
            '          records.append(_Record(record, (response,), _event_key(response.occurred_a'
            't, [response])))'
        ),
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_read_queries_keep_actual_model_exc'
                'hange_without_progress_events'
            ),
        ),
    ),
    SourceMutation(
        'continuation_reads_removed_prompt_targets',
        'custom.agents.life_continuation::_view.tools',
        '    tools = expand_tool_layer(compact_render(by_name["工具层"]))',
        '    tools = by_name["工具层"]',
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_compact_prompt_continuation_preser'
                'ves_head_and_exact_target_changes'
            ),
        ),
    ),
    SourceMutation(
        'plan_display_noncanonical_around_time',
        'worldmock.life.todo_layer::_day_plan.rows',
        '"around_time": epoch_clock(goal.around_at, zone) if goal.around_at is not None else None',
        '"around_time": f"约 {epoch_clock(goal.around_at, zone)}" if goal.around_at is not None else None',
        (
            'tests/life/test_tool_interaction_runtime.py::test_plan_times_display_exact_declared_local_format',
        ),
    ),
    SourceMutation(
        'consistent_store_source_note_omitted',
        'worldmock.life.name_resolution::resolve_start',
        '_source_notes(book, converted))',
        '())',
        (
            'tests/life/test_tool_protocol.py::test_consistent_store_source_preserves_success_note',
        ),
    ),
    SourceMutation(
        'model_service_stage_omitted',
        'custom.agents.life_agent::LifeAgent._record_model_evidence',
        'if evidence.phase == "failure":',
        'if evidence.phase == "never":',
        (
            (
                'tests/integration/test_host_failure_stages.py::test_model_service_failure_persists_s'
                'tage_without_parsing_exception_text'
            ),
        ),
    ),
    SourceMutation(
        'quality_discards_structured_code',
        'worldmock.footprints.quality::_name_rejection_quality.code',
        'code = report["error_code"]',
        'code = "NAME_NOT_FOUND"',
        (
            (
                'tests/footprints/test_quality.py::test_protocol_quality_uses_structured_evidence_wit'
                'hout_log_inference'
            ),
        ),
    ),
    SourceMutation(
        'enum_values_lost',
        'worldmock.life.error_contract::ErrorItem.payload',
        '"allowed_values": list(self.allowed_values)',
        '"allowed_values": []',
        (
            'tests/life/test_tool_protocol.py::test_purchase_preference_closed_values_are_complete',
        ),
    ),
    SourceMutation(
        'nested_index_lost',
        'worldmock.life.error_contract::ErrorItem.payload',
        'return {"path": list(path),',
        'return {"path": [part for part in path if not isinstance(part, int)],',
        (
            'tests/life/test_tool_protocol.py::test_strict_nested_type_positions_are_preserved',
        ),
    ),
    SourceMutation(
        'null_becomes_string',
        'worldmock.life.error_contract::ErrorItem.payload',
        '"allowed_values": list(self.allowed_values)',
        '"allowed_values": ["null" if value is None else value for value in self.allowed_values]',
        (
            'tests/life/test_tool_protocol.py::test_nonpurchase_preference_preserves_json_null',
        ),
    ),
    SourceMutation(
        'batch_error_omitted',
        'custom.agents.life_agent::LifeAgent.validate_react_decisions',
        'for index, decision in enumerate(decisions):',
        'for index, decision in enumerate(decisions[:1]):',
        (
            'tests/life/test_tool_protocol.py::test_read_batch_reports_every_error_with_real_call_indices',
        ),
    ),
    SourceMutation(
        'final_query_enabled',
        'custom.agents.life_agent::LifeAgent._request_tool_models.queries',
        'queries = _QUERY_TOOL_MODELS if self._remaining_turns() > 1 and not retry else {}',
        'queries = _QUERY_TOOL_MODELS',
        (
            'tests/life/test_tool_protocol.py::test_final_turn_and_truncation_retry_disable_every_query',
        ),
    ),
    SourceMutation(
        'query_writes_world',
        'custom.envs.life_env::LifeEnv.read_query',
        '            return error.payload() if error else query_world(observed, request)',
        (
            '            engine.state.version += 1\n            return error.payload() if error el'
            'se query_world(observed, request)'
        ),
        (
            (
                'tests/life/test_world_query.py::test_environment_read_query_keeps_world_clock_round_'
                'and_store_unchanged'
            ),
        ),
    ),
)


MUTATIONS += (
    SourceMutation(
        'constructed_validation_items_lost',
        'worldmock.life.session_actions::ActionHandlers.dispatch',
        'fix=report.fix or "", items=report.items)',
        'fix=report.fix or "", items=())',
        (
            (
                'tests/life/test_tool_protocol.py::test_engine_revalidates_constructed_bad_requests_w'
                'ith_complete_fields'
            ),
        ),
    ),
)


MUTATIONS += (
    SourceMutation(
        'native_purchase_route_loses_way',
        'worldmock.life.routing::_item_errors.purchase',
        'purchase = verb == "obtain"',
        'purchase = False',
        (
            'tests/life/test_purchase_apps.py::test_native_purchase_routing_derives_way_without_action_field',
        ),
    ),
)


MUTATIONS += (
    SourceMutation(
        'invalid_request_purchase_projection',
        'worldmock.footprints.purchases::_order_attempts',
        'if result.get("error_code") == "ACTION_REQUEST_INVALID":',
        'if False:',
        (
            (
                'tests/life/test_tool_protocol.py::test_engine_revalidates_constructed_bad_requests_w'
                'ith_complete_fields'
            ),
        ),
    ),
)


def run_behavior(nodes: tuple[str, ...], directory: Path, mutation: SourceMutation | None = None) -> dict:
    """@brief 以本台账的回执目录与超时调用共用变异执行器。"""
    timeout = BASELINE_TIMEOUT_SECONDS if mutation is None else MUTATION_TIMEOUT_SECONDS
    return mutation_runner.run_behavior(nodes, directory, mutation, RunSettings(CHILD_ENTRY, RECEIPTS, timeout))
