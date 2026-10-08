"""@brief 真实源码变异及其行为测试；不登记历史阶段和文档条款。"""

from __future__ import annotations
from pathlib import Path
from tests import mutation_runner
from tests.mutation_runner import CHILD_ENTRY, REPO_ROOT, RunSettings, SourceMutation


MUTATIONS = (SourceMutation(name='decision_failure_swallowed_after_log',
                symbol='custom.agents.life_agent::LifeAgent.step',
                original='            logging.exception("LifeAgent decision failed for agent %s", self.id)\n'
                         '            raise\n',
                replacement='            logging.exception("LifeAgent decision failed for agent %s", self.id)\n'
                            '            return ""\n',
                tests=('tests/integration/runtime/test_life_agent_protocol.py::test_decision_failure_is_logged_once_and_reraised_unchanged',
                       'tests/integration/runtime/test_host_failure_stages.py::test_model_service_failure_on_the_real_engine_is_logged_once_and_reraised_unchanged')),
 SourceMutation(name='tool_dispatch_failure_loses_stage_code',
                symbol='custom.agents.life_agent::LifeAgent._execute_react_tool',
                original='            feedback = _stage_feedback("TOOL_SERVICE_FAILED", ("tool_calls", index))\n'
                         '            raise RuntimeError(feedback) from error\n',
                replacement='            feedback = _stage_feedback("TOOL_SERVICE_FAILED", ("tool_calls", '
                            'index))\n'
                            '            raise\n',
                tests=('tests/integration/runtime/test_host_failure_stages.py::test_tool_service_failure_points_to_real_call_without_claiming_execution',
                       'tests/integration/runtime/test_host_failure_stages.py::test_tool_service_failure_on_the_real_engine_is_logged_and_raised_with_its_stage')),
 SourceMutation(name='plan_duplicates_report_only_a_generic_error',
                symbol='worldmock.life.models::_validate_plan_goals',
                original='        raise ValidationError.from_exception_data(model_name, errors)\n',
                replacement='        raise ValueError("day goals must contain at most eight unique intents")\n',
                tests=('tests/integration/life/test_revise_day_plan_json.py::test_engine_rejects_a_duplicate_revision_locating_first_and_later_entries',
                       'tests/integration/life/test_plan_day_duplicate_feedback.py::test_plan_day_duplicate_intents_are_fed_back_with_every_position_then_registered')),
 SourceMutation(name='plan_duplicates_locate_only_later_entries',
                symbol='worldmock.life.models::_validate_plan_goals.repeated',
                original='if len(indexes) > 1 for index in indexes)\n',
                replacement='if len(indexes) > 1 for index in indexes[1:])\n',
                tests=('tests/integration/life/test_revise_day_plan_json.py::test_engine_rejects_a_duplicate_revision_locating_first_and_later_entries',
                       'tests/integration/life/test_plan_day_duplicate_feedback.py::test_plan_day_duplicate_intents_are_fed_back_with_every_position_then_registered')),
 SourceMutation(name='plan_over_capacity_returns_before_duplicates',
                symbol='worldmock.life.models::_validate_plan_goals',
                original='        errors.append({"type": "value_error", "loc": (), "input": '
                         '[goal.model_dump(mode="json") for goal in goals],\n'
                         '                       "ctx": {"error": ValueError("day goals require at most eight '
                         'entries")}})\n',
                replacement='        raise ValueError("day goals require at most eight entries")\n',
                tests=('tests/integration/life/test_revise_day_plan_json.py::test_engine_reports_over_capacity_and_duplicates_in_the_same_rejection',
                       'tests/integration/life/test_plan_day_duplicate_feedback.py::test_plan_day_reports_over_capacity_together_with_duplicates')),
 SourceMutation(name='assembly_proxy_reverts_to_the_as2_class',
                symbol='worldmock.runtime::_build_society.env',
                original='        env = LifeEnvProxy(actor, run_dir=prepared.run_dir, '
                         'env_module_types=["LifeEnv"])\n',
                replacement='        from agentsociety2.env.env_router_proxy import EnvRouterProxy\n'
                            '\n'
                            '        env = EnvRouterProxy(actor, run_dir=prepared.run_dir, '
                            'env_module_types=["LifeEnv"])\n',
                tests=('tests/integration/runtime/test_read_query_runtime.py::test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query',)),
 SourceMutation(name='assembly_service_proxy_reverts_to_the_as2_class',
                symbol='worldmock.runtime::_build_society.service',
                original='        service = build_service_proxy(env, run_dir=prepared.run_dir, trace=False, '
                         'replay=False)\n',
                replacement='        from agentsociety2.env.env_router_proxy import EnvRouterProxy\n'
                            '\n'
                            '        service = build_service_proxy(\n'
                            '            EnvRouterProxy(actor, run_dir=prepared.run_dir, '
                            'env_module_types=["LifeEnv"]),\n'
                            '            run_dir=prepared.run_dir, trace=False, replay=False)\n',
                tests=('tests/integration/runtime/test_read_query_runtime.py::test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query',)),
 SourceMutation(name='assembly_actor_reverts_to_the_as2_class',
                symbol='worldmock.runtime::_build_society.actor',
                original='    actor = life_env_actor_class().remote(spec)\n',
                replacement='    from agentsociety2.env.direct_env_actor import get_direct_env_actor_class\n'
                            '\n'
                            '    actor = get_direct_env_actor_class().remote(spec)\n',
                tests=('tests/integration/runtime/test_read_query_runtime.py::test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query',)),
 SourceMutation(name='proxy_returns_without_awaiting_the_remote_result',
                symbol='worldmock.life.read_query_runtime::LifeEnvProxy.read_query',
                original='        return await self._actor.read_query.remote(context, tool, request)\n',
                replacement='        return self._actor.read_query.remote(context, tool, request)\n',
                tests=('tests/integration/runtime/test_read_query_runtime.py::test_runtime_assembly_builds_life_actor_and_a_proxy_with_read_query',)),
 SourceMutation(name='actor_answers_without_the_environment',
                symbol='worldmock.life.read_query_runtime::LifeEnvActor.read_query',
                original='        return await self._router._environment().read_query(context, tool, request)\n',
                replacement='        return {}\n',
                tests=('tests/integration/runtime/test_read_query_runtime.py::test_unwrapped_actor_read_query_equals_direct_environment_call',)),
 SourceMutation(name='actor_class_allows_concurrent_calls',
                symbol='worldmock.life.read_query_runtime::life_env_actor_class',
                original='    return ray.remote(max_concurrency=1)(LifeEnvActor)\n',
                replacement='    return ray.remote(max_concurrency=2)(LifeEnvActor)\n',
                tests=('tests/integration/runtime/test_read_query_runtime.py::test_life_env_actor_class_fixes_single_concurrency_without_parameters',)),
 SourceMutation(name='reflection_accepts_free_text',
                symbol='worldmock.agents.life_reflection::reflection_insights.arguments',
                original='    arguments = _tool_arguments(response)\n',
                replacement='    try:\n'
                            '        arguments = _tool_arguments(response)\n'
                            '    except ReflectionOutputError:\n'
                            '        arguments = json.dumps({"insights": '
                            'json.loads(response.choices[0].message.content)})\n',
                tests=('tests/integration/runtime/test_strict_reflection.py::test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection',)),
 SourceMutation(name='reflection_accepts_another_tool',
                symbol='worldmock.model_feedback::tool_arguments',
                original='        if _field(function, "name") != tool:\n',
                replacement='        if False:\n',
                tests=('tests/integration/runtime/test_strict_reflection.py::test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection',)),
 SourceMutation(name='reflection_skips_unknown_reference_check',
                symbol='worldmock.agents.life_reflection::reflection_insights',
                original='    if problems:\n',
                replacement='    if False:\n',
                tests=('tests/integration/runtime/test_strict_reflection.py::test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection',)),
 SourceMutation(name='reflection_count_bound_widened',
                symbol='worldmock.agents.life_reflection::ReflectionRequest.require_insights',
                original='        if not 1 <= len(insights) <= MEMORY_REFLECTION_MAX_INSIGHTS:\n',
                replacement='        if not 1 <= len(insights) <= MEMORY_REFLECTION_MAX_INSIGHTS + 1:\n',
                tests=('tests/integration/runtime/test_strict_reflection.py::test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection',)),
 SourceMutation(name='reflection_text_validation_skipped',
                symbol='worldmock.agents.life_reflection::ReflectionInsight.validate_insight',
                original='        return validate_memory_text(insight)\n',
                replacement='        return insight\n',
                tests=('tests/integration/runtime/test_strict_reflection.py::test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection',)),
 SourceMutation(name='reflection_retry_bound_zero',
                symbol='worldmock.agents.life_reflection::REFLECTION_VALIDATION_RETRIES',
                original='REFLECTION_VALIDATION_RETRIES = 3\n',
                replacement='REFLECTION_VALIDATION_RETRIES = 0\n',
                tests=('tests/integration/runtime/test_strict_reflection.py::test_runtime_feeds_each_contract_violation_back_and_stores_only_the_corrected_reflection',)),
 SourceMutation(name='reflection_retries_without_a_bound',
                symbol='worldmock.agents.life_memory::WorldMockMemoryRuntime._validated_reflection',
                original='                if attempt > REFLECTION_VALIDATION_RETRIES:\n',
                replacement='                if False:\n',
                tests=('tests/integration/runtime/test_strict_reflection.py::test_runtime_stops_with_a_run_fault_after_three_validation_retries_and_stores_no_reflection',)),
 SourceMutation(name='reflection_failure_returns_an_empty_list',
                symbol='worldmock.agents.life_memory::WorldMockMemoryRuntime._validated_reflection',
                original='                    raise ProtocolError(\n'
                         '                        f"memory reflection output remained invalid after three '
                         'retries: {error}") from error\n',
                replacement='                    return []\n',
                tests=('tests/integration/runtime/test_strict_reflection.py::test_runtime_stops_with_a_run_fault_after_three_validation_retries_and_stores_no_reflection',
                       'tests/integration/runtime/test_strict_reflection.py::test_exhausted_reflection_leaves_evidence_but_no_memory_update_in_the_run_database')),
 SourceMutation(name='reflection_correction_accumulates_messages',
                symbol='worldmock.agents.life_memory::WorldMockMemoryRuntime._validated_reflection',
                original='                messages, attempt = reflection_correction(original, response, error), '
                         'attempt + 1\n',
                replacement='                messages, attempt = reflection_correction(messages, response, '
                            'error), attempt + 1\n',
                tests=('tests/integration/runtime/test_strict_reflection.py::test_runtime_stops_with_a_run_fault_after_three_validation_retries_and_stores_no_reflection',)),
 SourceMutation(name='version_8_world_state_accepted',
                symbol='worldmock.life.models::WorldState.schema_version',
                original='    schema_version: Literal[WORLD_SCHEMA_VERSION]\n',
                replacement='    schema_version: Literal[WORLD_SCHEMA_VERSION, 8]\n',
                tests=('tests/integration/life/test_session_state.py::test_world_state_schema_version_8_is_rejected',
                       'tests/integration/life/test_session_state.py::test_run_with_version_8_world_state_is_rejected_on_open')),
 SourceMutation(name='todo_layer_omits_errands',
                symbol='worldmock.life.todo_layer::todo_layer',
                original='            "errands": errands.todo_rows(agent, names, context.scenario.timezone),\n',
                replacement='            "errands": [],\n',
                tests=('tests/integration/life/test_errand_requests.py::test_each_control_tool_records_errand_changes_with_one_event_per_change_and_an_ordered_receipt',)),
 SourceMutation(name='errand_changes_applied_before_rejection',
                symbol='worldmock.life.session_actions::ActionHandlers._conclude_activity.changes',
                original='        changes = self._errand_changes(pending, "conclude_activity", request)\n',
                replacement='        changes = self._errand_changes(pending, "conclude_activity", request)\n'
                            '        state.agents[pending.agent_id] = self._apply_errands(state, pending, '
                            'changes)[0].agents[\n'
                            '            pending.agent_id]\n',
                tests=('tests/integration/life/test_errand_requests.py::test_a_rejected_request_registers_no_errand_and_writes_no_errand_event',)),
 SourceMutation(name='request_can_mark_errand_completed',
                symbol='worldmock.life.models::ErrandChange.change',
                original='    change: Literal["新增", "更新", "取消"] = Field(description="这条变更做什么，从列出的三种里选一种。")\n',
                replacement='    change: Literal["新增", "更新", "取消", "完成"] = '
                            'Field(description="这条变更做什么，从列出的三种里选一种。")\n',
                tests=('tests/integration/life/test_errand_requests.py::test_a_request_cannot_mark_an_errand_completed',)),
 SourceMutation(name='items_without_errands_accepted',
                symbol='worldmock.life.control_items::control_items',
                original='        _list(_field(body, "errands", tool), tool)\n',
                replacement='',
                tests=('tests/integration/life/test_control_items.py::test_a_current_request_without_errands_is_rejected',)),
 SourceMutation(name='errand_name_takes_first_candidate',
                symbol='worldmock.life.errands::_open_name',
                original='    return _named_rows(written, rows)\n',
                replacement='    found, matches = _named_rows(written, rows)\n'
                            '    fallback = [*matches, *near_names(written, [row["name"] for row in rows])]\n'
                            '    if len(found) != 1 and fallback:\n'
                            '        found = [row for row in rows if row["name"] == fallback[0]][:1]\n'
                            '    return found, matches\n',
                tests=('tests/integration/life/test_errand_names.py::test_unknown_or_ambiguous_errand_name_lists_every_open_name_and_substitutes_nothing',)),
 SourceMutation(name='duplicates_judged_before_cancels',
                symbol='worldmock.life.errands::_plan.remaining',
                original='    remaining = [row for row in rows if row["errand_id"] not in cancelled]\n',
                replacement='    remaining = list(rows)\n',
                tests=('tests/integration/life/test_errand_names.py::test_cancelling_and_adding_again_with_the_original_name_and_a_new_quantity_is_accepted',)),
 SourceMutation(name='errand_named_twice_accepted',
                symbol='worldmock.life.errands::_plan_targets',
                original='        elif found[0]["errand_id"] in named:\n',
                replacement='        elif False:\n',
                tests=('tests/integration/life/test_errand_names.py::test_one_errand_named_twice_and_two_additions_with_one_outcome_and_product_are_rejected',)),
 SourceMutation(name='added_twins_with_one_product_accepted',
                symbol='worldmock.life.errands::_added_conflicts.doubled',
                original='    doubled = [other for other in earlier if other in plan.products\n',
                replacement='    doubled = [other for other in () if other in plan.products\n',
                tests=('tests/integration/life/test_errand_names.py::test_one_errand_named_twice_and_two_additions_with_one_outcome_and_product_are_rejected',)),
 SourceMutation(name='pending_order_counts_as_obtained',
                symbol='worldmock.life.errands::evidence_rows',
                original='    if event_type != "environment_event" or data.get("kind") != "goods_received":\n'
                         '        return []',
                replacement='    if event_type == "action_result":\n'
                            '        orders = (data.get("data") or {}).get("orders", [])\n'
                            '        return [Evidence(int(order["recipient_id"].split(":", 1)[1]), '
                            'order["goods_id"], float(order["quantity"]))\n'
                            '                for order in orders if order["recipient_id"].startswith("agent:") '
                            'and order["status"] == "pending"]\n'
                            '    if event_type != "environment_event" or data.get("kind") != "goods_received":\n'
                            '        return []',
                tests=('tests/integration/life/test_food_errand_evidence.py::test_actual_payment_orders_are_not_errand_evidence_even_when_delivered',)),
 SourceMutation(name='accepted_event_counts_as_evidence',
                symbol='worldmock.life.errands::evidence_rows',
                original='    if event_type != "environment_event" or data.get("kind") != "goods_received":\n'
                         '        return []',
                replacement='    if event_type in {"action_result", "action_accepted"}:\n'
                            '        orders = (data.get("data") or {}).get("orders", [])\n'
                            '        return [Evidence(int(order["recipient_id"].split(":", 1)[1]), '
                            'order["goods_id"], float(order["quantity"]))\n'
                            '                for order in orders if order["recipient_id"].startswith("agent:")]\n'
                            '    if event_type != "environment_event" or data.get("kind") != "goods_received":\n'
                            '        return []',
                tests=('tests/integration/life/test_food_errand_evidence.py::test_actual_payment_orders_are_not_errand_evidence_even_when_delivered',)),
 SourceMutation(name='progressed_goal_counts_as_completion',
                symbol='worldmock.life.errands::evidence_rows',
                original='data.get("kind") != "goods_received"',
                replacement='data.get("kind") not in {"goods_received", "goal_progressed"}',
                tests=('tests/integration/life/test_food_errand_evidence.py::test_retired_giving_consumption_and_self_reports_cannot_progress_errands',)),
 SourceMutation(name='self_report_counts_as_completion',
                symbol='worldmock.life.errands::evidence_rows',
                original='data.get("kind") != "goods_received"',
                replacement='data.get("kind") not in {"goods_received", "errand_claimed"}',
                tests=('tests/integration/life/test_food_errand_evidence.py::test_retired_giving_consumption_and_self_reports_cannot_progress_errands',)),
 SourceMutation(name='evidence_before_noting_event_counts',
                symbol='worldmock.life.errands::settle',
                original='            if errand is None or noted.get(errand.errand_id, -1) > index:\n',
                replacement='            if errand is None:\n',
                tests=('tests/integration/life/test_food_errand_evidence.py::test_receipt_before_registration_in_same_commit_does_not_count_but_after_it_does',)),
 SourceMutation(name='ownership_follows_event_agent',
                symbol='worldmock.life.errands::evidence_rows',
                original='return [Evidence(agent_id, data["goods_id"], float(data["quantity"]))]',
                replacement='return [Evidence(data["payer_id"], data["goods_id"], float(data["quantity"]))]',
                tests=('tests/integration/life/test_food_errand_evidence.py::test_actual_pickup_credits_only_receipt_recipient_and_repeat_settlement_emits_no_evidence',)),
 SourceMutation(name='round_commit_settles_twice',
                symbol='worldmock.life.decision_coordinator::DecisionCoordinator.commit_decision_round',
                original='        (staged, settled) = errands.settle(staged, drafts)\n',
                replacement='        (staged, settled) = errands.settle(staged, drafts)\n'
                            '        staged, again = errands.settle(staged, drafts)\n'
                            '        settled = settled + again\n',
                tests=('tests/integration/life/test_errand_completion.py::'
                       'test_quantity_two_with_one_received_stays_open_and_a_second_receipt_completes',)),
 SourceMutation(name='advance_commit_settles_twice',
                symbol='worldmock.life.time_advance::TimeAdvanceCoordinator.advance',
                original='        (staged, credited) = errands.settle(staged, drafts)\n',
                replacement='        (staged, credited) = errands.settle(staged, drafts)\n'
                            '        staged, again = errands.settle(staged, drafts)\n'
                            '        credited = credited + again\n',
                tests=('tests/integration/life/test_errand_reminder.py::'
                       'test_partial_progress_after_the_reminder_does_not_remind_again',)),
 SourceMutation(name='condensation_drops_completion_line',
                symbol='worldmock.life.settled_text::record_lines',
                original='        return [f"{_clock(context, record[\'occurred_at\'])} '
                         '办成「{record[\'errand\']}」"]\n',
                replacement='        return []\n',
                tests=('tests/integration/life/test_progress_events.py::test_an_errand_completed_in_the_session_is_one_record_and_folds_to_one_line',)),
 SourceMutation(name='cancelled_errand_stays_armed',
                symbol='worldmock.life.errands::_apply_one',
                original='        errands.pop(plan.targets[index]["errand_id"])\n',
                replacement='        errands.get(plan.targets[index]["errand_id"])\n',
                tests=('tests/integration/life/test_errand_reminder.py::test_completed_or_cancelled_errands_are_not_reminded',)),
 SourceMutation(name='reminder_completes_errand',
                symbol='worldmock.life.errands::deliver_reminders',
                original='        errands[errand.errand_id] = reminded\n',
                replacement='        errands.pop(errand.errand_id)\n',
                tests=('tests/integration/life/test_errand_reminder.py::test_an_awake_person_is_reminded_once_exactly_on_time_and_the_errand_is_unchanged',)),
 SourceMutation(name='errand_reminder_wakes_sleep',
                symbol='worldmock.life.schedule::SLEEP_ALLOWED_REASONS',
                original='WakeReason.PLAN_REMINDER_DUE, WakeReason.ALARM_RING}',
                replacement='WakeReason.PLAN_REMINDER_DUE, WakeReason.ALARM_RING, WakeReason.ERRAND_REMINDER_DUE}',
                tests=('tests/integration/life/test_errand_reminder.py::test_a_sleeper_gets_no_boundary_no_delivery_and_no_consumption_and_is_reminded_at_the_wake_boundary',)),
 SourceMutation(name='open_errands_are_commitments',
                symbol='worldmock.life.rhythm::_has_commitments',
                original='    if (\n        plan is not None\n',
                replacement='    if agent.errands or (\n        plan is not None\n',
                tests=('tests/integration/life/test_errand_reminder.py::test_open_errands_do_not_change_the_reconsideration_interval',)),
 SourceMutation(name='errand_wake_reason_missing_from_vocabulary',
                symbol='worldmock.footprints.models::WAKE_REASON_NAMES',
                original='    "progress_report_due", "no_active_activity", "plan_reminder_due", '
                         '"errand_reminder_due",\n',
                replacement='    "progress_report_due", "no_active_activity", "plan_reminder_due",\n',
                tests=('tests/integration/life/test_errand_reminder.py::test_an_awake_person_is_reminded_once_exactly_on_time_and_the_errand_is_unchanged',)),
 SourceMutation(name='sleeper_reminder_time_is_a_boundary',
                symbol='worldmock.life.errands::boundary_times',
                original='    return [moment for agent_id, agent in state.agents.items() if not _asleep(state, '
                         'agent_id)\n',
                replacement='    return [moment for agent_id, agent in state.agents.items()\n',
                tests=('tests/integration/life/test_errand_reminder.py::test_a_sleeper_gets_no_boundary_no_delivery_and_no_consumption_and_is_reminded_at_the_wake_boundary',)),
 SourceMutation(name='reminder_recorded_without_delivery',
                symbol='worldmock.life.rhythm::_deliver_errand_reminders',
                original='        if rhythm_schedule.WakeReason.ERRAND_REMINDER_DUE.value in '
                         'verdict.wake_reasons:\n',
                replacement='        if True:\n',
                tests=('tests/integration/life/test_errand_reminder.py::test_a_sleeper_gets_no_boundary_no_delivery_and_no_consumption_and_is_reminded_at_the_wake_boundary',)),
 SourceMutation(name='simultaneous_reminders_write_one_event',
                symbol='worldmock.life.errands::deliver_reminders',
                original='    return state.model_copy(update={"agents": agents}), drafts\n',
                replacement='    return state.model_copy(update={"agents": agents}), drafts[:1]\n',
                tests=('tests/integration/life/test_errand_reminder.py::test_errands_due_together_are_one_opportunity_with_one_event_each',)),
 SourceMutation(name='midnight_clears_errands',
                symbol='worldmock.life.time_advance::TimeAdvanceCoordinator.advance',
                original='            staged = _put_agent(staged, '
                         'staged.agents[agent_id].model_copy(update={"day_plan": None}, deep=True))\n',
                replacement='            staged = _put_agent(staged, staged.agents[agent_id].model_copy(\n'
                            '                update={"day_plan": None, "errands": {}}, deep=True))\n',
                tests=('tests/integration/life/test_errand_overnight.py::test_an_errand_stays_in_the_todo_layer_after_midnight_plan_generation_and_plan_revision',)),
 SourceMutation(name='plan_revision_drops_errands',
                symbol='worldmock.life.session_actions::ActionHandlers._revise_day_plan.changed_agent',
                original='        changed_agent = agent.model_copy(update={"day_plan": plan}, deep=True)\n',
                replacement='        changed_agent = agent.model_copy(update={"day_plan": plan, "errands": {}}, '
                            'deep=True)\n',
                tests=('tests/integration/life/test_errand_overnight.py::test_an_errand_stays_in_the_todo_layer_after_midnight_plan_generation_and_plan_revision',)),
 SourceMutation(name='condensation_drops_todo_errands',
                symbol='worldmock.life.prompt_capacity::fit_request',
                original='            layers[index] = {**layers[index], "events": records}\n',
                replacement='            layers[index] = {**layers[index], "events": records}\n'
                            '            layers = [{**layer, "errands": []} if layer.get("layer") == "待办层" else '
                            'layer\n'
                            '                      for layer in layers]\n',
                tests=('tests/integration/life/test_errand_overnight.py::test_a_condensed_request_still_carries_the_todo_layer_errands',)),
 SourceMutation(name='memory_overflow_drops_todo_errands',
                symbol='worldmock.life.observe::ObservationBuilder._layer_data',
                original='            "todo_layer": todo_layer(context, self.context.store),\n',
                replacement='            "todo_layer": {**todo_layer(context, self.context.store), **(\n'
                            '                {"errands": []} if len(str(memory.layer)) > '
                            'memory_layer.MEMORY_WINDOW_CHARS // 2\n'
                            '                else {})},\n',
                tests=('tests/integration/life/test_errand_overnight.py::test_an_errand_stays_when_the_memory_layer_overflows_its_window',)),
 SourceMutation(name='checkpoint_omits_errands',
                symbol='worldmock.life.models::AgentState.errands',
                original='    errands: dict[str, ErrandState] = Field(default_factory=dict)\n',
                replacement='    errands: dict[str, ErrandState] = Field(default_factory=dict, exclude=True)\n',
                tests=('tests/integration/runtime/test_errand_checkpoint.py::test_a_restored_branch_shows_the_same_todo_layer_and_repeats_nothing',)),
 SourceMutation(name='progress_arguments_carry_errands',
                symbol='worldmock.life.progress_layer::_arguments',
                original='    return control_request(tool, request)\n',
                replacement='    return {**control_request(tool, request), "errands": request["errands"]}\n',
                tests=('tests/integration/runtime/test_errand_prompt_separation.py::test_errand_changes_write_no_memory_and_stay_out_of_self_notes_and_progress_arguments',)),
 SourceMutation(name='self_notes_carry_errand_text',
                symbol='worldmock.life.memory_layer::_self_notes',
                original='            notes.extend((item.activity_name, text) for text in texts if '
                         'text.strip())\n',
                replacement='            notes.extend((item.activity_name, text) for text in texts if '
                            'text.strip())\n'
                            '            from worldmock.life.control_items import control_errands\n'
                            '            notes.extend((item.activity_name, change[key]) for change in '
                            'control_errands(\n'
                            '                request.data["tool_name"], request.data["request"]) for key in '
                            '("name", "memo") if change[key])\n',
                tests=('tests/integration/runtime/test_errand_prompt_separation.py::test_errand_changes_write_no_memory_and_stay_out_of_self_notes_and_progress_arguments',)),
 SourceMutation(name='errand_reminder_prompt_wakes_sleepers',
                symbol='worldmock.agents.life_prompt::LIFE_SYSTEM_LAYER',
                original='到提醒时刻只提醒一次，不会叫醒睡着的你',
                replacement='到提醒时刻只提醒一次，会叫醒睡着的你',
                tests=('tests/integration/runtime/test_seven_layer_request.py::test_rules_1_4_6_divide_errands_summaries_and_memories',)),
 SourceMutation(name='errand_agent_events_written_as_system',
                symbol='worldmock.life.errands::_draft',
                original='                      visibility="agent" if kind in AGENT_VISIBLE_KINDS else '
                         '"system",\n',
                replacement='                      visibility="system",\n',
                tests=('tests/integration/life/test_errand_reminder.py::test_an_awake_person_is_reminded_once_exactly_on_time_and_the_errand_is_unchanged',)),
 SourceMutation(name='settlement_event_inherits_evidence_identity',
                symbol='worldmock.life.errands::settle',
                original='            settled.append(_credit(open_sets[row.owner_id], errand, row, head))\n',
                replacement='            settled.append(_credit(open_sets[row.owner_id], errand, row, '
                            'head).model_copy(\n'
                            '                update={"decision_id": draft.decision_id, "action_id": '
                            'draft.action_id}))\n',
                tests=('tests/integration/life/test_errand_completion.py::test_receipt_completion_tolerance_has_both_sides',)),
 SourceMutation(name='control_projection_carries_errands',
                symbol='worldmock.life.control_items::control_request',
                original='    return {"items": rows}\n',
                replacement='    return {"items": rows, "errands": list(control_errands(tool, request))}\n',
                tests=('tests/integration/life/test_control_items.py::test_current_shape_items_carry_views_as_subject_memories',)),
 SourceMutation(name='control_projection_trims_first_item_only',
                symbol='worldmock.life.control_items::control_request',
                original='    for item in control_items(tool, request):\n'
                         '        row: dict[str, Any] = {"activity_name": item.activity_name, "summary": '
                         'item.summary}\n',
                replacement='    for index, item in enumerate(control_items(tool, request)):\n'
                            '        row: dict[str, Any] = {"activity_name": item.activity_name, "summary": '
                            'item.summary}\n'
                            '        if index:\n'
                            '            row["views"] = list(item.views)\n',
                tests=('tests/integration/life/test_control_items.py::test_current_shape_items_carry_views_as_subject_memories',)),
 SourceMutation(name='errand_metrics_use_goal_denominator',
                symbol='worldmock.evaluation.errand_metrics::errand_metrics.registered',
                original='    registered = len(lives)\n',
                replacement='    registered = sum(len(event["data"]["day_plan"]["goals"]) for event in events\n'
                            '                     if event["data"].get("kind") == "day_plan_generated")\n',
                tests=('tests/integration/evaluation/test_errand_metrics.py::test_the_denominator_is_registered_errands_not_goals',)),
 SourceMutation(name='extraction_drops_stated_work_anchor',
                symbol='worldmock.profile.extractor::_work_anchors',
                original='        anchors.leave_home, anchors.bedtime, parsed["leave_work"], '
                         'parsed["arrive_home"])\n',
                replacement='        anchors.leave_home, anchors.bedtime, None, parsed["arrive_home"])\n',
                tests=('tests/unit/profile/test_extractor.py::test_stated_work_anchors_are_extracted_into_the_card',)),
 SourceMutation(name='extraction_drops_stated_arrive_home',
                symbol='worldmock.profile.extractor::_work_anchors',
                original='        anchors.leave_home, anchors.bedtime, parsed["leave_work"], '
                         'parsed["arrive_home"])\n',
                replacement='        anchors.leave_home, anchors.bedtime, parsed["leave_work"], None)\n',
                tests=('tests/unit/profile/test_extractor.py::test_stated_work_anchors_are_extracted_into_the_card',)),
 SourceMutation(name='leave_work_takes_a_default',
                symbol='worldmock.profile.extractor::_work_anchors.parsed',
                original='None if raw[name] is None else TimeWindow.from_clock_range(raw[name])',
                replacement='TimeWindow.from_clock_range(raw[name] or "18:00-18:30")',
                tests=('tests/unit/profile/test_extractor.py::test_unmentioned_work_anchors_stay_null_without_toml_defaults',)),
 SourceMutation(name='work_anchors_join_wraparound',
                symbol='worldmock.profile.models::TimeAnchors.normalize_wraparound.names',
                original='        names = ("wake", "leave_home", "lunch", "dinner", "bedtime")\n',
                replacement='        names = ("wake", "leave_home", "lunch", "dinner", "bedtime", "leave_work", '
                            '"arrive_home")\n',
                tests=('tests/unit/profile/test_extractor.py::test_null_work_anchors_keep_the_after_midnight_bedtime_normalised',)),
 SourceMutation(name='out_of_order_work_anchor_fails_the_card',
                symbol='worldmock.profile.extractor::_work_anchors',
                original='    leave_work, arrive_home, problems = settle_work_anchors(\n'
                         '        anchors.leave_home, anchors.bedtime, parsed["leave_work"], '
                         'parsed["arrive_home"])\n',
                replacement='    leave_work, arrive_home, problems = parsed["leave_work"], parsed["arrive_home"], '
                            '()\n',
                tests=('tests/unit/profile/test_extractor.py::test_out_of_order_consensus_demotes_leave_work_then_arrive_home_and_still_compiles',)),
 SourceMutation(name='persona_layer_drops_work_anchor',
                symbol='worldmock.agents.life_persona::_card_fields',
                original='"leave_work": _optional_window(anchors.leave_work)',
                replacement='"leave_work": None',
                tests=('tests/integration/life/test_persona_layer.py::test_rhythm_lists_the_work_anchors_in_contract_order_and_null_when_absent',)),
 SourceMutation(name='persona_layer_drops_arrive_home',
                symbol='worldmock.agents.life_persona::_card_fields',
                original='"arrive_home": _optional_window(anchors.arrive_home)',
                replacement='"arrive_home": None',
                tests=('tests/integration/life/test_persona_layer.py::test_rhythm_lists_the_work_anchors_in_contract_order_and_null_when_absent',)),
 SourceMutation(name='host_renders_work_schedule',
                symbol='worldmock.agents.life_persona::persona_layer.layer',
                original='        "preferences": [dict(item) for item in views["preferences"]],\n',
                replacement='        "preferences": [dict(item) for item in views["preferences"]],\n'
                            '        "work_schedule": profile.get("work_schedule"),\n',
                tests=('tests/integration/life/test_persona_layer.py::test_a_declared_work_schedule_is_not_rendered_in_the_persona_layer',)),
 SourceMutation(name='scenario_work_windows_missing_from_plan_input',
                symbol='worldmock.life.day_plan_inputs::_persona.fields',
                original='"下班时段": _hours(config.leave_work_window),\n'
                         '        "到家时段": _hours(config.arrive_home_window),',
                replacement='"下班时段": None,\n        "到家时段": None,',
                tests=('tests/integration/runtime/test_input_warnings.py::test_scenario_declared_work_windows_reach_the_card_the_persona_layer_and_the_plan_input',)),
 SourceMutation(name='override_skips_null_card_slot',
                symbol='worldmock.profile.injection::_window_overrides',
                original='        if field not in declared:\n            continue\n',
                replacement='        if field not in declared or getattr(anchors, slot) is None:\n'
                            '            continue\n',
                tests=('tests/integration/runtime/test_input_warnings.py::test_scenario_declared_work_windows_reach_the_card_the_persona_layer_and_the_plan_input',)),
 SourceMutation(name='calendar_cross_check_skipped',
                symbol='worldmock.life.load_checks::check_work_schedules.problems',
                original='problems = [*_workplace_problems(scenario), *_calendar_problems(scenario)]',
                replacement='problems = [*_workplace_problems(scenario)]',
                tests=('tests/integration/life/test_load_checks.py::test_work_schedule_must_agree_with_the_calendar_inside_the_window',)),
 SourceMutation(name='scenario_structure_four_admitted',
                symbol='worldmock.life.scenario::LifeScenario',
                original='    schema_version: Literal[8]\n',
                replacement='    schema_version: Literal[4, 8]\n',
                tests=('tests/integration/life/test_scenario.py::test_structure_eight_rejects_old_scenario_version',)),
 SourceMutation(name='rest_day_inferred_from_weekday',
                symbol='worldmock.life.work_schedule::shift_on',
                original='return schedule.weekly[str(day.weekday())]',
                replacement='return None if day.weekday() >= 5 else next(item for item in '
                            'schedule.weekly.values() if item is not None)',
                tests=('tests/integration/life/test_work_schedule.py::test_rest_days_come_from_weekly_null_and_exception_null_and_an_exception_shift_overrides_weekly',)),
 SourceMutation(name='exception_dates_ignored',
                symbol='worldmock.life.work_schedule::shift_on',
                original='if key in schedule.exceptions:',
                replacement='if False:',
                tests=('tests/integration/life/test_work_schedule.py::test_rest_days_come_from_weekly_null_and_exception_null_and_an_exception_shift_overrides_weekly',)),
 SourceMutation(name='break_shown_as_on_duty',
                symbol='worldmock.life.work_schedule::duty_status',
                original='if any(begin <= now < finish for begin, finish in break_spans(shift, day, zone)):',
                replacement='if False:',
                tests=('tests/integration/life/test_work_schedule.py::test_duty_follows_the_clock_through_a_working_day',)),
 SourceMutation(name='everyone_gets_a_shift',
                symbol='worldmock.life.environment_layer::_duty.config',
                original='if item.agent_id == context.agent_id)',
                replacement='if item.work_schedule is not None)',
                tests=('tests/integration/life/test_work_schedule.py::test_people_without_a_schedule_have_no_duty_while_others_keep_theirs',)),
 SourceMutation(name='duty_vanishes_after_plan_revision',
                symbol='worldmock.life.environment_layer::environment_layer',
                original='"duty": _duty(context)',
                replacement='"duty": None if (context.state.agents[context.agent_id].day_plan\n'
                            '                              and '
                            'context.state.agents[context.agent_id].day_plan.goals) else _duty(context)',
                tests=('tests/integration/life/test_work_schedule.py::test_revising_the_day_plan_does_not_change_duty',)),
 SourceMutation(name='duty_vanishes_after_topic_completion',
                symbol='worldmock.life.environment_layer::environment_layer',
                original='"duty": _duty(context)',
                replacement='"duty": None if any(item.kind.value == "action" and item.status.value == "completed"\n'
                            '                              and item.data.get("category") == "work_study"\n'
                            '                              for item in context.state.activities.values()) '
                            'else _duty(context)',
                tests=('tests/integration/life/test_work_schedule.py::test_duty_stays_on_duty_after_topic_completion',)),
 SourceMutation(name='plan_input_carries_duty_status',
                symbol='worldmock.life.work_schedule::day_arrangement',
                original='"breaks": [_span_text(begin, finish) for begin, finish in shift.breaks]}',
                replacement='"breaks": [_span_text(begin, finish) for begin, finish in shift.breaks], "status": '
                            'STATUS_ON_DUTY}',
                tests=('tests/integration/life/test_day_planning.py::test_plan_input_carries_the_plan_dates_duty_arrangement_without_status',)),
 SourceMutation(name='query_compaction_drops_false_and_zero',
                symbol='worldmock.life.tool_groups::compact',
                original='if item is not None and item != [] and item != {}',
                replacement='if item and item != [] and item != {}',
                tests=('tests/integration/life/test_tool_interaction_runtime.py::'
                       'test_compact_layer_preserves_false_zero_current_action_and_control_tools',)),
 SourceMutation(name='query_app_filter_ignored',
                symbol='worldmock.life.world_query::_matching_entries',
                original='and (request.app is None or any(action["app"] == request.app for action in '
                         'entry["match"]["actions"]))',
                replacement='and True',
                tests=('tests/integration/life/test_world_query_filters.py::test_shopping_app_filters_the_real_goods_target_without_store_expansion',)),
 SourceMutation(name='query_environment_branch_writes_world',
                symbol='custom.envs.life_env::LifeEnv.read_query',
                original='            return error.payload() if error else query_world(observed, request)\n',
                replacement='            account = engine.context.state.agents[context.agent_id].balance_account_id\n'
                            '            engine.context.state.accounts[account] += 1\n'
                            '            return error.payload() if error else query_world(observed, request)\n',
                tests=('tests/integration/life/test_tool_interaction_runtime.py::'
                       'test_read_queries_keep_actual_model_exchange_without_progress_events',)),
 SourceMutation(name='query_new_keys_get_defaults',
                symbol='worldmock.life.world_query::QueryWorldRequest',
                original='    app: str | None\n    available_only: bool\n',
                replacement='    app: str | None = None\n    available_only: bool = False\n',
                tests=('tests/integration/life/test_world_query.py::test_every_query_field_is_required',)),
 SourceMutation(name='query_unknown_app_returns_empty_page',
                symbol='worldmock.life.world_query::_filter_issues',
                original='if app is not None and app not in _app_names(observation):',
                replacement='if False:',
                tests=('tests/integration/life/test_world_query.py::test_unknown_app_and_store_names_have_the_exact_new_error_contract',)),
 SourceMutation(name='available_filter_applied_after_paging',
                symbol='worldmock.life.world_query::query_world',
                original='    entries = _matching_entries(observation, request)\n'
                         '    end = min(request.offset + observation["world_query_page_size"], len(entries))\n'
                         '    return {"matches": [compact(entry["match"]) for entry in '
                         'entries[request.offset:end]],',
                replacement='    entries = _matching_entries(observation, '
                            'request.model_copy(update={"available_only": False}))\n'
                            '    end = min(request.offset + observation["world_query_page_size"], len(entries))\n'
                            '    page = [entry for entry in entries[request.offset:end]\n'
                            '            if not request.available_only or _executable(entry["match"], '
                            '_query_ways(request.way))]\n'
                            '    return {"matches": [compact(entry["match"]) for entry in page],',
                tests=('tests/integration/life/test_world_query_filters.py::test_available_only_is_specific_to_the_selected_app_and_way',)),
 SourceMutation(name='available_judged_on_unmatched_way_actions',
                symbol='worldmock.life.world_query::_executable',
                original='if (ways is None or action["way"] in ways) and (app is None or action["app"] == app)',
                replacement='if True',
                tests=('tests/integration/life/test_world_query_filters.py::test_available_only_is_specific_to_the_selected_app_and_way',)),
 SourceMutation(name='available_without_way_ignores_tools',
                symbol='worldmock.life.world_query::_executable',
                original='    return any(row["available"] for row in (*match["actions"], *match["tools"]))\n',
                replacement='    return any(row["available"] for row in match["actions"])\n',
                tests=('tests/integration/life/test_world_query_filters.py::test_available_only_is_specific_to_the_selected_app_and_way',)),
 SourceMutation(name='query_total_count_before_filtering',
                symbol='worldmock.life.world_query::query_world',
                original='"total_count": len(entries),',
                replacement='"total_count": len(observation["world_query_bindings"]),',
                tests=('tests/integration/life/test_world_query.py::'
                       'test_complete_pages_are_sorted_disjoint_and_defensive_copies_of_real_frozen_targets',)),
 SourceMutation(name='query_app_candidates_include_places',
                symbol='worldmock.life.world_query::_app_issue',
                original='chosen = close if close else names[:MAX_CANDIDATES]',
                replacement='chosen = [entry["match"]["name"] for entry in observation["world_query_bindings"]\n'
                            '              if entry["match"]["kind"] == "location"][:MAX_CANDIDATES]',
                tests=('tests/integration/life/test_world_query.py::test_unknown_app_and_store_names_have_the_exact_new_error_contract',)))


RECEIPTS = REPO_ROOT / ".work/errands-workday/mutation-receipts"


BASELINE_TIMEOUT_FLOOR_SECONDS = 600


BASELINE_TIMEOUT_SECONDS_PER_NODE = 120


MUTATION_TIMEOUT_SECONDS = 600


def run_behavior(nodes: tuple[str, ...], directory: Path, mutation: SourceMutation | None = None) -> dict:
    """@brief 以本台账的回执目录与超时调用共用变异执行器：不带变异是基线，带变异是一次杀死尝试。"""
    timeout = MUTATION_TIMEOUT_SECONDS
    if mutation is None:
        timeout = max(BASELINE_TIMEOUT_FLOOR_SECONDS, BASELINE_TIMEOUT_SECONDS_PER_NODE * len(nodes))
    return mutation_runner.run_behavior(nodes, directory, mutation, RunSettings(CHILD_ENTRY, RECEIPTS, timeout))
