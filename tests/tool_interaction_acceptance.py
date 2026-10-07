"""工具交互的行为登记与源码变异清单；变异经共用执行器在隔离子进程里生效，验收不修改共享工作树。"""

from __future__ import annotations

from pathlib import Path

from tests import mutation_runner
from tests.mutation_runner import CHILD_ENTRY, REPO_ROOT, RunSettings, SourceMutation

#: 本台账的回执目录与两种超时（基线、单个变异），取共用执行器上移之前写死的值。
RECEIPTS = REPO_ROOT / ".work/tool-interaction/mutation-receipts"
BASELINE_TIMEOUT_SECONDS = 600
MUTATION_TIMEOUT_SECONDS = 180


BEHAVIOR = {'工作主题累计与地点': ('tests/life/test_action_run.py::test_assigned_work_requires_actual_workplace_and_unassigned_study_does_not',
               'tests/life/test_action_run.py::test_assigned_content_is_finite_and_completion_pulse_is_applied_once',
               'tests/life/test_action_guarantees.py::test_work_reports_never_change_real_content_seconds_or_claim_completion'),
 '普通邮件与睡眠': ('tests/life/test_tool_interaction_runtime.py::test_visible_arrived_mail_contains_only_actual_subject_and_body',
             'tests/integration/test_acs_engine_wave.py::test_real_arrived_mail_body_occurs_once_in_the_actual_host_model_request',
             'tests/life/test_wake_reasons.py::test_sleep_accepts_only_existing_sleep_reasons'),
 '全部接口错误入口': ('tests/life/test_tool_protocol.py::test_retired_purchase_preference_is_rejected_for_every_value',
              'tests/life/test_tool_protocol.py::test_unused_route_fields_preserve_json_null_in_feedback',
              'tests/life/test_tool_protocol.py::test_strict_nested_type_positions_are_preserved',
              'tests/life/test_tool_protocol.py::test_engine_revalidates_constructed_bad_requests_with_complete_fields',
              'tests/life/test_tool_protocol.py::test_root_validator_keeps_argument_root_and_original_json',
              'tests/life/test_tool_protocol.py::test_cross_field_interval_and_goal_bounds_keep_explicit_locations',
              'tests/life/test_tool_protocol.py::test_parser_reports_malformed_json_and_unknown_tool_complete_locations',
              'tests/integration/test_host_failure_stages.py::test_model_service_failure_persists_stage_without_parsing_exception_text',
              'tests/integration/test_host_failure_stages.py::test_evidence_write_failure_stops_with_structured_stage',
              'tests/integration/test_host_failure_stages.py::test_tool_service_failure_points_to_real_call_without_claiming_execution',
              'tests/life/test_tool_interaction_runtime.py::test_plan_times_display_exact_declared_local_format'),
 '原子批次': ('tests/life/test_tool_protocol.py::test_read_batch_reports_every_error_with_real_call_indices',
          'tests/life/test_tool_protocol.py::test_world_query_mixed_batches_are_atomic',
          'tests/life/test_tool_protocol.py::test_route_errors_return_null_only_and_all_independent_fields'),
 '查询与预算': ('tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',
           'tests/life/test_world_query.py::test_offset_is_a_strict_nonnegative_json_integer',
           'tests/life/test_world_query_filters.py::test_full_query_contains_goods_beyond_the_current_presentation_window',
           'tests/life/test_tool_protocol.py::test_final_turn_and_truncation_retry_disable_every_query',
           'tests/life/test_tool_interaction_runtime.py::test_read_queries_keep_actual_model_exchange_without_progress_events'),
 '操作备忘': ('tests/life/test_tool_interaction_runtime.py::test_memo_is_derived_across_sessions_with_acceptance_and_clears_next_day',
          'tests/life/test_tool_interaction_runtime.py::test_memo_survives_persistent_reopen'),
 '紧凑呈现': ('tests/life/test_tool_interaction_runtime.py::test_compact_layer_preserves_false_zero_current_action_and_control_tools',
          'tests/life/test_tool_interaction_runtime.py::test_actual_frozen_tool_layer_and_prompt_groups_restore_identically',
          'tests/life/test_tool_interaction_runtime.py::test_compact_prompt_continuation_preserves_head_and_exact_target_changes'),
 '文档与身份': ('tests/test_documentation_gate.py::test_real_repository_documents_and_source_references_resolve',
           'tests/test_design_doc_build.py::test_repository_chapters_assemble_deterministically_and_the_committed_html_is_current'),
 '购买与收货': ('tests/life/test_commerce.py::test_store_purchase_settles_energy_and_receipt_once_in_the_payment_result',
           'tests/life/test_commerce.py::test_two_goods_rows_pay_one_delivery_fee_and_one_transfer',
           'tests/life/test_commerce.py::test_pending_order_checkpoint_restore_receives_once_and_keeps_payment_once')}

MUTATIONS = (
    SourceMutation(name='pagination_tail', symbol='worldmock.life.world_query::query_world', original='"next_offset": end if end < len(entries) else None', replacement='"next_offset": None', tests=('tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',)),
    SourceMutation(name='pagination_offset', symbol='worldmock.life.world_query::query_world', original='"next_offset": end if end < len(entries) else None', replacement='"next_offset": end + 1 if end < len(entries) else None', tests=('tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',)),
    SourceMutation(name='pagination_supply_window', symbol='worldmock.life.world_query::_matching_entries.entries', original='entries = observation["world_query_bindings"]', replacement='entries = observation["world_query_bindings"][:observation["world_query_page_size"]]', tests=('tests/life/test_world_query.py::test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',)),
    SourceMutation(name='memo_lost', symbol='worldmock.life.progress_layer::rejected_today.latest', original='latest = sorted(rows.values(), key=lambda item: item["sequence"], reverse=True)[:8]', replacement='latest = []', tests=('tests/life/test_tool_interaction_runtime.py::test_memo_is_derived_across_sessions_with_acceptance_and_clears_next_day',)),
    SourceMutation(name='memo_cross_day_leak', symbol='worldmock.life.progress_layer::rejected_today.events', original='and local_date(event.occurred_at, context.scenario.timezone) == day]', replacement='and True]', tests=('tests/life/test_tool_interaction_runtime.py::test_memo_is_derived_across_sessions_with_acceptance_and_clears_next_day',)),
    SourceMutation(name='compact_drops_false', symbol='worldmock.life.tool_groups::compact', original='if item is not None and item != [] and item != {}', replacement='if item and item != [] and item != {}', tests=('tests/life/test_tool_interaction_runtime.py::test_compact_layer_preserves_false_zero_current_action_and_control_tools',)),
    SourceMutation(name='compact_drops_control', symbol='worldmock.life.tool_groups::compact_tool_layer.actions', original='actions = target["actions"] or [None]', replacement='actions = target["actions"]', tests=('tests/life/test_tool_interaction_runtime.py::test_compact_layer_preserves_false_zero_current_action_and_control_tools',)),
    SourceMutation(name='memo_limit_omitted', symbol='worldmock.life.progress_layer::rejected_today.latest', original='latest = sorted(rows.values(), key=lambda item: item["sequence"], reverse=True)[:8]', replacement='latest = sorted(rows.values(), key=lambda item: item["sequence"], reverse=True)', tests=('tests/life/test_tool_interaction_runtime.py::test_memo_caps_recent_entries_and_reason_without_truncating_audit',)),
    SourceMutation(name='query_results_leak_into_progress', symbol='worldmock.life.progress_layer::_session_records.records', original='    records = _tool_records(context, store, lower)', replacement='    records = _tool_records(context, store, lower)\n    for response in store.read_agent_events("model_response", context.agent_id, after=lower):\n        for call in _tool_calls(response):\n            name = call.get("function", {}).get("name")\n            if name not in {"memory_recent", "memory_search", "memory_time_range"}:\n                continue\n            moment = iso_time(response.occurred_at, context.scenario.timezone)\n            result = {"occurred_at": moment, "status": "completed", "error_code": None,\n                      "message": "Memory query returned 0 episodes", "details": {"episodes": 0}}\n            record = {"occurred_at": moment, "type": "tool_call", "tool": name,\n                      "arguments": {}, "result": result}\n            records.append(_Record(record, (response,), _event_key(response.occurred_at, [response])))', tests=('tests/life/test_tool_interaction_runtime.py::test_read_queries_keep_actual_model_exchange_without_progress_events',)),
    SourceMutation(name='continuation_reads_removed_prompt_targets', symbol='worldmock.agents.life_continuation::_view.tools', original='    tools = expand_tool_layer(compact_render(by_name["工具层"]))', replacement='    tools = expand_tool_layer(compact_render(by_name["工具层"]))\n    if "groups" in by_name["工具层"]:\n        for target in tools["targets"]:\n            target["actions"] = []', tests=('tests/life/test_tool_interaction_runtime.py::test_compact_prompt_continuation_preserves_head_and_exact_target_changes',)),
    SourceMutation(name='plan_display_noncanonical_around_time', symbol='worldmock.life.todo_layer::_day_plan.rows', original='"around_time": epoch_clock(goal.around_at, zone) if goal.around_at is not None else None', replacement='"around_time": f"约 {epoch_clock(goal.around_at, zone)}" if goal.around_at is not None else None', tests=('tests/life/test_tool_interaction_runtime.py::test_plan_times_display_exact_declared_local_format',)),
    SourceMutation(name='model_service_stage_omitted', symbol='custom.agents.life_agent::LifeAgent._record_model_evidence', original='_stage_feedback("MODEL_SERVICE_FAILED", ("tool_calls",))', replacement='_stage_feedback("TOOL_SERVICE_FAILED", ("tool_calls",))', tests=('tests/integration/test_host_failure_stages.py::test_model_service_failure_persists_stage_without_parsing_exception_text',)),
    SourceMutation(name='quality_discards_structured_code', symbol='worldmock.footprints.quality::_name_rejection_quality.code', original='code = report["error_code"]', replacement='code = "NAME_NOT_FOUND"', tests=('tests/footprints/test_quality.py::test_protocol_quality_uses_structured_evidence_without_log_inference',)),
    SourceMutation(name='enum_values_lost', symbol='worldmock.life.error_contract::ErrorItem.payload', original='"allowed_values": list(self.allowed_values)', replacement='"allowed_values": []', tests=('tests/life/test_tool_protocol.py::test_strict_nested_type_positions_are_preserved',)),
    SourceMutation(name='nested_index_lost', symbol='worldmock.life.error_contract::ErrorItem.payload', original='return {"path": list(path),', replacement='return {"path": [part for part in path if not isinstance(part, int)],', tests=('tests/life/test_tool_protocol.py::test_strict_nested_type_positions_are_preserved',)),
    SourceMutation(name='null_becomes_string', symbol='worldmock.life.error_contract::ErrorItem.payload', original='"allowed_values": list(self.allowed_values)', replacement='"allowed_values": ["null" if value is None else value for value in self.allowed_values]', tests=('tests/life/test_tool_protocol.py::test_unused_route_fields_preserve_json_null_in_feedback',)),
    SourceMutation(name='batch_error_omitted', symbol='custom.agents.life_agent::LifeAgent.validate_react_decisions', original='for index, decision in enumerate(decisions):', replacement='for index, decision in enumerate(decisions[:1]):', tests=('tests/life/test_tool_protocol.py::test_read_batch_reports_every_error_with_real_call_indices',)),
    SourceMutation(name='final_query_enabled', symbol='custom.agents.life_agent::LifeAgent._request_tool_models.queries', original='queries = _QUERY_TOOL_MODELS if self._remaining_turns() > 1 and not retry else {}', replacement='queries = _QUERY_TOOL_MODELS', tests=('tests/life/test_tool_protocol.py::test_final_turn_and_truncation_retry_disable_every_query',)),
    SourceMutation(name='query_writes_world', symbol='custom.envs.life_env::LifeEnv.read_query', original='            return error.payload() if error else query_world(observed, request)', replacement='            account = engine.context.state.agents[context.agent_id].balance_account_id\n            engine.context.state.accounts[account] += 1\n            return error.payload() if error else query_world(observed, request)', tests=('tests/life/test_tool_interaction_runtime.py::test_read_queries_keep_actual_model_exchange_without_progress_events',)),
    SourceMutation(name='constructed_validation_items_lost', symbol='worldmock.life.session_actions::ActionHandlers.dispatch', original='fix=report.fix or "", items=report.items)', replacement='fix=report.fix or "", items=())', tests=('tests/life/test_tool_protocol.py::test_engine_revalidates_constructed_bad_requests_with_complete_fields',)),
    SourceMutation(name='native_purchase_route_loses_way', symbol='worldmock.life.routing::_item_errors', original='return _purchase_issues(request, index, names)', replacement='return []', tests=('tests/life/test_routing.py::test_explicit_route_errors_have_full_declared_names_and_complete_candidates', 'tests/test_start_activity_contract.py::test_illegal_combinations_keep_the_same_host_and_environment_business_code')),
    SourceMutation(name='invalid_request_purchase_projection', symbol='worldmock.footprints.purchases::_order_attempts', original='if result.get("error_code") == "ACTION_REQUEST_INVALID":', replacement='if result.get("error_code") == "ACTION_REQUEST_INVALID":\n            rows.append({"fact_kind": "order_attempt", "agent_id": event["agent_id"], "action_id": event["action_id"],\n                         "request_event_id": event["event_id"], "way": "购买", "status": result.get("status"),\n                         "error_code": result.get("error_code"), "occurred_at": event["occurred_at"]})', tests=('tests/life/test_tool_protocol.py::test_engine_revalidates_constructed_bad_requests_with_complete_fields',)),
)


def run_behavior(nodes: tuple[str, ...], directory: Path, mutation: SourceMutation | None = None) -> dict:
    """@brief 以本台账的回执目录与超时调用共用变异执行器。"""
    timeout = BASELINE_TIMEOUT_SECONDS if mutation is None else MUTATION_TIMEOUT_SECONDS
    return mutation_runner.run_behavior(nodes, directory, mutation, RunSettings(CHILD_ENTRY, RECEIPTS, timeout))
