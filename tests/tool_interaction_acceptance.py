"""工具交互的行为登记与隔离源码变异执行，验收不修改共享工作树。"""

from __future__ import annotations

import ast
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from dataclasses import dataclass

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SourceMutation:
    """@brief 一个非等价源码变异与必须杀死它的独立行为用例。"""

    name: str
    module: str
    source: str
    original: str
    replacement: str
    tests: tuple[str, ...]


BEHAVIOR = {
    '八点普通工作通知，八点五十五到公司': (
        (
            'tests/life/test_tool_interaction_runtime.py::test_ordinary_work_opens_at_first_notif'
            'ication_and_wrong_place_is_explicit'
        ),
    ),
    '提前收到十点预约会议': (
        (
            'tests/life/test_tool_interaction_runtime.py::test_removed_input_opening_is_rejected_'
            'and_appointment_is_explicit'
        ),
        (
            'tests/life/test_tool_interaction_runtime.py::test_appointment_wakes_only_at_real_ope'
            'ning_and_never_wakes_sleep'
        ),
    ),
    '未读、阅读与睡眠': (
        'tests/life/test_tool_interaction_runtime.py::test_notification_coverage_checks_every_notice',
        'tests/life/test_tool_interaction_runtime.py::test_mail_reading_keeps_actual_body_and_only_full_task_name',
        (
            'tests/life/test_tool_interaction_runtime.py::test_appointment_mail_reveals_only_assi'
            'gnee_without_false_opening'
        ),
    ),
    '全部接口错误入口': (
        'tests/life/test_tool_protocol.py::test_purchase_preference_closed_values_are_complete',
        'tests/life/test_tool_protocol.py::test_nonpurchase_preference_preserves_json_null',
        'tests/life/test_tool_protocol.py::test_strict_nested_type_positions_are_preserved',
        'tests/life/test_tool_protocol.py::test_engine_revalidates_constructed_bad_requests_with_complete_fields',
        'tests/life/test_tool_protocol.py::test_root_validator_keeps_argument_root_and_original_json',
        'tests/life/test_tool_protocol.py::test_cross_field_interval_and_goal_bounds_keep_explicit_locations',
        'tests/life/test_tool_protocol.py::test_native_purchase_routing_derives_way_without_action_field',
        'tests/life/test_tool_protocol.py::test_native_recipe_and_giving_aliases_keep_actual_route_conditions',
        'tests/life/test_tool_protocol.py::test_parser_reports_malformed_json_and_unknown_tool_complete_locations',
        (
            'tests/integration/test_host_failure_stages.py::test_model_service_failure_persists_s'
            'tage_without_parsing_exception_text'
        ),
        'tests/integration/test_host_failure_stages.py::test_evidence_write_failure_stops_with_structured_stage',
        (
            'tests/integration/test_host_failure_stages.py::test_tool_service_failure_points_to_r'
            'eal_call_without_claiming_execution'
        ),
        'tests/life/test_tool_interaction_runtime.py::test_plan_times_display_exact_declared_local_format',
    ),
    '原子批次': (
        'tests/life/test_tool_protocol.py::test_read_batch_reports_every_error_with_real_call_indices',
        'tests/life/test_tool_protocol.py::test_world_query_mixed_batches_are_atomic',
        'tests/life/test_tool_protocol.py::test_route_errors_return_null_only_and_all_independent_fields',
    ),
    '类别供给': (
        'tests/corpora/test_storefront_release.py::test_complete_assortments_have_real_stock_and_distinct_menus',
        'tests/corpora/test_storefront_release.py::test_every_published_product_can_really_be_bought_at_its_store',
        (
            'tests/corpora/test_storefront_release.py::test_repeated_menus_are_rejected_even_with'
            '_valid_complete_supplies'
        ),
        'tests/corpora/test_storefront_release.py::test_selected_store_must_keep_every_declared_supply',
    ),
    '地点定价': (
        (
            'tests/corpora/test_storefront_release.py::test_price_formula_is_reproducible_order_i'
            'ndependent_and_positioned'
        ),
        'tests/corpora/test_storefront_release.py::test_different_scenario_seeds_have_independently_frozen_quotes',
        'tests/corpora/test_storefront_release.py::test_out_of_range_frozen_quote_is_rejected',
        'tests/life/test_storefront_schedule.py::test_price_change_bounds_are_atomic_and_preserve_orders',
    ),
    '购买与食用': (
        'tests/life/test_tool_interaction_recipe.py::test_published_recipe_debits_input_and_creates_edible_output',
        'tests/life/test_tool_protocol.py::test_deleted_activity_fields_are_rejected',
        'tests/life/test_tool_protocol.py::test_new_schema_and_candidates_are_complete_and_strict',
        'tests/life/test_tool_protocol.py::test_consistent_store_source_preserves_success_note',
    ),
    '料理': (
        'tests/life/test_tool_interaction_recipe.py::test_published_recipe_debits_input_and_creates_edible_output',
        (
            'tests/life/test_tool_interaction_recipe.py::test_recipe_without_real_input_is_reject'
            'ed_and_narration_cannot_create_food'
        ),
        (
            'tests/life/test_tool_interaction_habits.py::test_every_frozen_habit_has_a_complete_e'
            'xplicit_native_fixture'
        ),
        (
            'tests/life/test_tool_interaction_habits.py::test_declared_habit_runs_with_real_prere'
            'quisites_and_settlement'
        ),
    ),
    '查询与预算': (
        (
            'tests/life/test_world_query.py::test_world_query_paginates_complete_frozen_set_and_p'
            'reserves_false_and_zero'
        ),
        'tests/life/test_world_query.py::test_world_query_offset_requires_nonnegative_json_integer',
        (
            'tests/life/test_world_query.py::test_world_query_real_engine_reads_all_goods_without'
            '_browse_or_world_changes'
        ),
        'tests/life/test_world_query.py::test_environment_read_query_keeps_world_clock_round_and_store_unchanged',
        'tests/life/test_world_query.py::test_world_query_does_not_return_internal_identity_or_private_evidence',
        'tests/life/test_tool_protocol.py::test_final_turn_and_truncation_retry_disable_every_query',
        (
            'tests/life/test_tool_interaction_runtime.py::test_read_queries_keep_actual_model_exc'
            'hange_without_progress_events'
        ),
    ),
    '操作备忘': (
        (
            'tests/life/test_tool_interaction_runtime.py::test_memo_is_derived_across_sessions_wi'
            'th_acceptance_and_clears_next_day'
        ),
        'tests/life/test_tool_interaction_runtime.py::test_memo_and_appointment_survive_persistent_reopen',
    ),
    '紧凑呈现': (
        (
            'tests/life/test_tool_interaction_runtime.py::test_compact_layer_preserves_false_zero'
            '_contract_and_control_tools'
        ),
        (
            'tests/life/test_tool_interaction_runtime.py::test_actual_frozen_tool_layer_and_promp'
            't_groups_restore_identically'
        ),
        (
            'tests/life/test_tool_interaction_runtime.py::test_compact_prompt_continuation_preser'
            'ves_head_and_exact_target_changes'
        ),
    ),
    '文档与身份': (
        'tests/test_documentation_gate.py::test_real_repository_documentation_links_resolve',
        (
            'tests/test_design_doc_build.py::test_repository_chapters_assemble_deterministically_'
            'and_the_committed_html_is_current'
        ),
    ),
}

MUTATIONS = (
    SourceMutation(
        'pagination_tail',
        'worldmock.life.world_query',
        'src/worldmock/life/world_query.py',
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
        'worldmock.life.world_query',
        'src/worldmock/life/world_query.py',
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
        'worldmock.life.world_query',
        'src/worldmock/life/world_query.py',
        'entries = observation["world_query_bindings"]',
        'entries = observation["world_query_bindings"][:observation["world_query_page_size"]]',
        (
            (
                'tests/life/test_world_query.py::test_world_query_paginates_complete_frozen_set_and_p'
                'reserves_false_and_zero'
            ),
        ),
    ),
    SourceMutation(
        'recipe_no_input_debit',
        'worldmock.life.inventory_recipe',
        'src/worldmock/life/inventory_recipe.py',
        'local[plan.input_object_id] = balances[(plan.resource_id, input_owner)]',
        'local[plan.input_object_id] = agent.inventory[agent.location_id][plan.input_object_id]',
        (
            (
                'tests/life/test_tool_interaction_recipe.py::test_published_recipe_debits_input_and_c'
                'reates_edible_output'
            ),
        ),
    ),
    SourceMutation(
        'recipe_no_output_credit',
        'worldmock.life.inventory_recipe',
        'src/worldmock/life/inventory_recipe.py',
        'local[plan.output_object_id] = balances[(plan.resource_id, output_owner)]',
        'local[plan.output_object_id] = 0',
        (
            (
                'tests/life/test_tool_interaction_recipe.py::test_published_recipe_debits_input_and_c'
                'reates_edible_output'
            ),
        ),
    ),
)

MUTATIONS += (
    SourceMutation(
        'assortment_full_pool',
        'worldmock.corpora.storefront',
        'src/worldmock/corpora/storefront.py',
        'return frozenset(expanded | set(assortment.object_ids))',
        'return frozenset(obj.object_id for obj in catalog.objects.values() if obj.object_kind.value == "product")',
        (
            (
                'tests/corpora/test_storefront_release.py::test_complete_assortments_have_real_stock_'
                'and_distinct_menus'
            ),
        ),
    ),
    SourceMutation(
        'restaurant_menus_identical',
        'worldmock.corpora.storefront',
        'src/worldmock/corpora/storefront.py',
        '    _validate_menus(menus)',
        '    pass',
        (
            (
                'tests/corpora/test_storefront_release.py::test_repeated_menus_are_rejected_even_with'
                '_valid_complete_supplies'
            ),
        ),
    ),
    SourceMutation(
        'assortment_window_cut',
        'worldmock.corpora.storefront',
        'src/worldmock/corpora/storefront.py',
        'return frozenset(expanded | set(assortment.object_ids))',
        'return frozenset(sorted(expanded | set(assortment.object_ids))[:20])',
        (
            (
                'tests/corpora/test_storefront_release.py::test_complete_assortments_have_real_stock_'
                'and_distinct_menus'
            ),
        ),
    ),
    SourceMutation(
        'category_inheritance_exact_only',
        'worldmock.categories.models',
        'src/worldmock/categories/models.py',
        'return ancestor_id in category.lineage',
        'return ancestor_id == category_id',
        (
            (
                'tests/corpora/test_storefront_release.py::test_complete_assortments_have_real_stock_'
                'and_distinct_menus'
            ),
        ),
    ),
    SourceMutation(
        'price_position_ignored',
        'worldmock.corpora.storefront',
        'src/worldmock/corpora/storefront.py',
        'percent = position + digest_integer % 11 - 5',
        'percent = 50 + digest_integer % 11 - 5',
        (
            (
                'tests/corpora/test_storefront_release.py::test_price_formula_is_reproducible_order_i'
                'ndependent_and_positioned'
            ),
        ),
    ),
    SourceMutation(
        'price_outside_bounds',
        'worldmock.corpora.storefront',
        'src/worldmock/corpora/storefront.py',
        'return lower + ((upper - lower) * percent + 50) // 100',
        'return upper + 1',
        (
            (
                'tests/corpora/test_storefront_release.py::test_price_formula_is_reproducible_order_i'
                'ndependent_and_positioned'
            ),
        ),
    ),
    SourceMutation(
        'quote_bound_check_removed',
        'worldmock.corpora.storefront',
        'src/worldmock/corpora/storefront.py',
        'if not supply.quotes or any(not lower <= quote.price_fen <= upper for quote in supply.quotes):',
        'if not supply.quotes:',
        (
            'tests/corpora/test_storefront_release.py::test_out_of_range_frozen_quote_is_rejected',
        ),
    ),
    SourceMutation(
        'selection_incomplete_allowed',
        'worldmock.life.map_data',
        'src/worldmock/life/map_data.py',
        'if supply_ids != expected:',
        'if False:',
        (
            'tests/corpora/test_storefront_release.py::test_selected_store_must_keep_every_declared_supply',
        ),
    ),
    SourceMutation(
        'reprice_bound_check_removed',
        'worldmock.life.storefront_state',
        'src/worldmock/life/storefront_state.py',
        '    validate_price_bounds(event.data["amount_fen"], product)',
        '    pass',
        (
            'tests/life/test_storefront_schedule.py::test_price_change_bounds_are_atomic_and_preserve_orders',
        ),
    ),
    SourceMutation(
        'reprice_changes_accepted_orders',
        'worldmock.scheduling.effects',
        'src/worldmock/scheduling/effects.py',
        (
            'changed = state.model_copy(update={"supply_quotes": quotes}, deep=True)\n    return S'
            'cheduledOutcome(changed, {**event.data, "quote_before": previous.model_dump(mode="js'
            'on"),'
        ),
        (
            'changed = state.model_copy(update={"supply_quotes": quotes, "deliveries": {key: orde'
            'r.model_copy(update={"total_price_fen": quote.price_fen}) for key, order in state.de'
            'liveries.items()}}, deep=True)\n    return ScheduledOutcome(changed, {**event.data, "'
            'quote_before": previous.model_dump(mode="json"),'
        ),
        (
            'tests/life/test_storefront_schedule.py::test_price_change_bounds_are_atomic_and_preserve_orders',
        ),
    ),
    SourceMutation(
        'effective_product_price_range_reversed',
        'worldmock.categories.contract',
        'src/worldmock/categories/contract.py',
        'if lower is not None and upper is not None and lower > upper:',
        'if False:',
        (
            'tests/corpora/test_storefront_release.py::test_effective_price_range_rejects_reversed_object_override',
        ),
    ),
    SourceMutation(
        'habit_media_progress_not_persisted',
        'worldmock.life.content',
        'src/worldmock/life/content.py',
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
        'worldmock.life.scenario',
        'src/worldmock/life/scenario.py',
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
        'worldmock.life.scenario',
        'src/worldmock/life/scenario.py',
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
        'worldmock.scheduling.compile',
        'src/worldmock/scheduling/compile.py',
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
        'wrong_notification_recipient',
        'worldmock.life.scenario',
        'src/worldmock/life/scenario.py',
        'if message.recipient_agent_id != recipient:',
        'if False:',
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_notification_coverage_checks_every'
                '_notice[wrong_recipient]'
            ),
        ),
    ),
    SourceMutation(
        'cross_person_task_leak',
        'worldmock.scheduling.effects',
        'src/worldmock/scheduling/effects.py',
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
        'worldmock.life.scenario',
        'src/worldmock/life/scenario.py',
        'else task.appointment.starts_at)',
        'else max(item.sent_at for item in notices))',
        (
            (
                'tests/life/test_tool_interaction_runtime.py::test_removed_input_opening_is_rejected_'
                'and_appointment_is_explicit'
            ),
        ),
    ),
    SourceMutation(
        'read_mail_body_lost',
        'worldmock.life.result_projection',
        'src/worldmock/life/result_projection.py',
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
        'worldmock.life.progress_layer',
        'src/worldmock/life/progress_layer.py',
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
        'worldmock.life.progress_layer',
        'src/worldmock/life/progress_layer.py',
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
        'worldmock.life.tool_groups',
        'src/worldmock/life/tool_groups.py',
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
        'worldmock.life.tool_groups',
        'src/worldmock/life/tool_groups.py',
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
        'worldmock.life.rhythm',
        'src/worldmock/life/rhythm.py',
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
        'work_material_unchecked',
        'worldmock.life.semantics',
        'src/worldmock/life/semantics.py',
        '    _check_work_material(semantics.scenario, root)',
        '    pass',
        (
            'tests/life/test_tool_interaction_runtime.py::test_work_material_is_validated_before_first_world_save',
        ),
    ),
    SourceMutation(
        'memo_limit_omitted',
        'worldmock.life.progress_layer',
        'src/worldmock/life/progress_layer.py',
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
        'worldmock.life.progress_layer',
        'src/worldmock/life/progress_layer.py',
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
        'custom.agents.life_continuation',
        'custom/agents/life_continuation.py',
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
        'worldmock.life.todo_layer',
        'src/worldmock/life/todo_layer.py',
        '"around_time": epoch_clock(goal.around_at, zone) if goal.around_at is not None else None',
        '"around_time": f"约 {epoch_clock(goal.around_at, zone)}" if goal.around_at is not None else None',
        (
            'tests/life/test_tool_interaction_runtime.py::test_plan_times_display_exact_declared_local_format',
        ),
    ),
    SourceMutation(
        'consistent_store_source_note_omitted',
        'worldmock.life.name_resolution',
        'src/worldmock/life/name_resolution.py',
        '_source_notes(book, converted))',
        '())',
        (
            'tests/life/test_tool_protocol.py::test_consistent_store_source_preserves_success_note',
        ),
    ),
    SourceMutation(
        'model_service_stage_omitted',
        'custom.agents.life_agent',
        'custom/agents/life_agent.py',
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
        'worldmock.footprints.quality',
        'src/worldmock/footprints/quality.py',
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
        'worldmock.life.error_contract',
        'src/worldmock/life/error_contract.py',
        '"allowed_values": list(self.allowed_values)',
        '"allowed_values": []',
        (
            'tests/life/test_tool_protocol.py::test_purchase_preference_closed_values_are_complete',
        ),
    ),
    SourceMutation(
        'nested_index_lost',
        'worldmock.life.error_contract',
        'src/worldmock/life/error_contract.py',
        'return {"path": list(path),',
        'return {"path": [part for part in path if not isinstance(part, int)],',
        (
            'tests/life/test_tool_protocol.py::test_strict_nested_type_positions_are_preserved',
        ),
    ),
    SourceMutation(
        'null_becomes_string',
        'worldmock.life.error_contract',
        'src/worldmock/life/error_contract.py',
        '"allowed_values": list(self.allowed_values)',
        '"allowed_values": ["null" if value is None else value for value in self.allowed_values]',
        (
            'tests/life/test_tool_protocol.py::test_nonpurchase_preference_preserves_json_null',
        ),
    ),
    SourceMutation(
        'batch_error_omitted',
        'custom.agents.life_agent',
        'custom/agents/life_agent.py',
        'for index, decision in enumerate(decisions):',
        'for index, decision in enumerate(decisions[:1]):',
        (
            'tests/life/test_tool_protocol.py::test_read_batch_reports_every_error_with_real_call_indices',
        ),
    ),
    SourceMutation(
        'final_query_enabled',
        'custom.agents.life_agent',
        'custom/agents/life_agent.py',
        'queries = _QUERY_TOOL_MODELS if self._remaining_turns() > 1 and not retry else {}',
        'queries = _QUERY_TOOL_MODELS',
        (
            'tests/life/test_tool_protocol.py::test_final_turn_and_truncation_retry_disable_every_query',
        ),
    ),
    SourceMutation(
        'query_writes_world',
        'custom.envs.life_env',
        'custom/envs/life_env.py',
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
        'worldmock.life.engine',
        'src/worldmock/life/engine.py',
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
        'worldmock.life.routing',
        'src/worldmock/life/routing.py',
        'purchase = verb == "obtain" and "制作" not in options',
        'purchase = False',
        (
            'tests/life/test_tool_protocol.py::test_native_purchase_routing_derives_way_without_action_field',
        ),
    ),
)


MUTATIONS += (
    SourceMutation(
        'invalid_request_purchase_projection',
        'worldmock.footprints.purchases',
        'src/worldmock/footprints/purchases.py',
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


def mutated_source(mutation: SourceMutation) -> tuple[str, str]:
    """@brief 只接受唯一且可编译的生产源码替换，返回原文与变异源码。"""
    source = (REPO_ROOT / mutation.source).read_text(encoding="utf-8")
    if source.count(mutation.original) != 1:
        raise ValueError(f"Mutation source must occur exactly once: {mutation.name}")
    changed = source.replace(mutation.original, mutation.replacement, 1)
    if source == changed:
        raise ValueError(f"Mutation must change production source: {mutation.name}")
    ast.parse(changed, filename=mutation.source)
    return source, changed


def run_behavior(nodes: tuple[str, ...], directory: Path, mutation: SourceMutation | None = None) -> dict:
    """@brief 在新解释器执行行为用例；收集失败、超时或基础失败不能冒充杀死变异。"""
    command = [sys.executable, "-m", "tests.tool_interaction_acceptance", "--child"]
    original = changed = ""
    if mutation is not None:
        original, changed = mutated_source(mutation)
        path = directory / "mutation.json"
        path.write_text(json.dumps({"module": mutation.module, "path": mutation.source, "source": changed}),
                        encoding="utf-8")
        command.extend(["--source", str(path)])
    command.extend(nodes)
    command.extend(["--basetemp", str(directory / "child")])
    environment = {**os.environ, "AGENTSOCIETY_LLM_API_KEY": "test-only",
                   "MEM0_TELEMETRY": "False", "ANONYMIZED_TELEMETRY": "False",
                   "LITELLM_LOCAL_MODEL_COST_MAP": "True", "PYTHONUTF8": "1"}
    result = subprocess.run(command, cwd=REPO_ROOT, env=environment, capture_output=True, text=True,
                            encoding="utf-8", timeout=600 if mutation is None else 180)
    receipt = {"name": mutation.name if mutation else "baseline", "exit_code": result.returncode,
               "tests": list(nodes), "stdout": result.stdout, "stderr": result.stderr,
               "original_sha256": hashlib.sha256(original.encode()).hexdigest() if mutation else None,
               "mutated_sha256": hashlib.sha256(changed.encode()).hexdigest() if mutation else None}
    evidence = REPO_ROOT / ".work/tool-interaction/mutation-receipts"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / f"{receipt['name']}.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
    return receipt


class _MutatedLoader(importlib.abc.Loader):
    """@brief 在模块首次导入时执行变异源码，避免旧函数引用掩盖变异。"""

    def __init__(self, case: dict):
        self.case = case

    def create_module(self, spec):
        """@brief 由 Python 创建正常模块对象。"""
        return None

    def exec_module(self, module):
        """@brief 真实源码经编译后在正常模块命名空间执行。"""
        path = str(REPO_ROOT / self.case["path"])
        module.__file__ = path
        exec(compile(self.case["source"], path, "exec"), module.__dict__)


class _MutationFinder(importlib.abc.MetaPathFinder):
    """@brief 仅替换指定生产模块，其余导入保持正常依赖解析。"""

    def __init__(self, case: dict):
        self.case = case

    def find_spec(self, fullname, path=None, target=None):
        """@brief 精确定位目标模块，不替换行为测试或其他生产模块。"""
        if fullname == self.case["module"]:
            return importlib.util.spec_from_loader(fullname, _MutatedLoader(self.case))
        return None


def _child() -> int:
    """@brief 独立测试解释器入口，先安装源码加载器再收集行为测试。"""
    arguments = sys.argv[2:]
    if arguments[:1] == ["--source"]:
        case = json.loads(Path(arguments[1]).read_text(encoding="utf-8"))
        sys.meta_path.insert(0, _MutationFinder(case))
        arguments = arguments[2:]
    import pytest

    return pytest.main([*arguments, "-q", "--tb=short", "--disable-warnings"])


if __name__ == "__main__":
    raise SystemExit(_child())
