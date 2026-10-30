"""生产依赖方向的完整 AST 守卫与有意破坏边界的反例。"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from importlib.util import resolve_name

import pytest

ROOT = Path(__file__).resolve().parents[3]
FOUNDATION = frozenset({
    'worldmock.state.world.world', 'worldmock.simulation_engine.observation.targets.object_labels',
    'worldmock.foundation.time.readable', 'worldmock.state.planning.errands', 'worldmock.state.planning.schedule',
    'worldmock.foundation.errors', 'worldmock.configuration.inputs.models', 'worldmock.exchange_engine.conditions.activity_rules', 'worldmock.simulation_engine.body.sleep_plan',
    'worldmock.foundation.randomness',
    'worldmock.configuration.model.model_metadata', 'worldmock.state.planning.events', 'worldmock.foundation.models',
    'worldmock.foundation.time.intervals',
    'worldmock.exchange_engine.definitions.types',
    'worldmock.exchange_engine.definitions.constants',
}) | frozenset(
    "worldmock." + path.relative_to(ROOT / "src/worldmock").with_suffix("").as_posix().replace("/", ".")
    for folder in ("state", "foundation") for path in (ROOT / "src/worldmock" / folder).rglob("*.py")
    if path.name != "__init__.py"
)
# 运行配置尚未初始化前不能导入的重依赖与命令行启动边界。
DELAYED_IMPORTS: dict[tuple[str, str, str], str] = {
    ('worldmock.cli', '_dispatch', 'worldmock.configuration.inputs.validation'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.cli', '_export_command', 'worldmock.recording_engine.export.export'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.cli', '_export_command', 'worldmock.recording_engine.storage.store'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.cli', '_run_command', 'worldmock.runtime.lifecycle.runner'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.cli', '_run_replay', 'worldmock.runtime.lifecycle.runner'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.cli', '_run_replay', 'worldmock.state.decisions.results'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.inputs.environment', '_resolve_persona_locations', 'worldmock.resource_engine.catalog.models'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.inputs.environment', 'load_environment', 'worldmock.configuration.model.model_metadata'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.inputs.environment', 'load_environment', 'worldmock.resource_engine.catalog.loader'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.inputs.environment', 'load_environment', 'worldmock.resource_engine.registration.admission'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.inputs.validation', '_check_locations', 'worldmock.configuration.inputs.environment'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.inputs.validation', '_check_metadata', 'worldmock.configuration.model.model_metadata'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.inputs.validation', 'validate_environment', 'worldmock.configuration.inputs.environment'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.inputs.validation', 'validate_environment', 'worldmock.resource_engine.catalog.loader'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.inputs.validation', 'validate_environment', 'worldmock.resource_engine.registration.admission'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.model.model_metadata', 'IndependentModelClient.__init__', 'agentsociety2.config'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.model.model_metadata', 'IndependentModelClient.__init__', 'worldmock.foundation.errors'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.model.model_metadata', 'IndependentModelClient.call', 'worldmock.foundation.errors'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.model.model_metadata', 'frozen_metadata_loader', 'worldmock.foundation.errors'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.configuration.model.model_metadata', 'load_model_metadata', 'worldmock.foundation.errors'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.recording_engine.events.models', 'EventDraft.validate_decision_started_evidence', 'worldmock.recording_engine.events.environment_events'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.recording_engine.events.models', 'validate_decision_rhythm', 'worldmock.state.planning.schedule'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.recording_engine.reports.quality', '_name_rejection_quality', 'worldmock.exchange_engine.conditions.rejections'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.recording_engine.reports.quality', '_wake_category_distribution', 'worldmock.state.planning.schedule'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.recording_engine.reports.quality', '_wake_reason_distribution', 'worldmock.state.planning.schedule'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.resource_engine.catalog.models', 'Catalog.nearest_parcel_station', 'worldmock.resource_engine.registration.errors'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.framework.read_query', 'life_env_actor_class', 'ray'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.framework.setup', '_model_identity', 'custom.agents.life_agent'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.framework.setup', '_model_identity', 'worldmock.agents.prompt.life_prompt'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.framework.setup', '_require_loaded_config', 'agentsociety2.config.config'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.framework.setup', 'creation_inputs', 'worldmock.configuration.persona.persona_card'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.framework.setup', 'creation_inputs', 'worldmock.configuration.persona.persona_text'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.framework.setup', 'stage_model_identity', 'custom.agents.life_agent'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.framework.setup', 'validate_stage_model_identity', 'custom.agents.life_agent'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.framework.setup', 'validate_stage_model_identity', 'pydantic'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', 'CounterfactualRequest.__post_init__', 'worldmock.state.identity'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', 'CounterfactualRequest.__post_init__', 'worldmock.state.decisions.results'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_build_society', 'agentsociety2.agent.service_proxy'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_build_society', 'agentsociety2.config.llm_dispatcher'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_build_society', 'agentsociety2.env.direct_env_actor'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_build_society', 'agentsociety2.society'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_build_society', 'worldmock.runtime.framework.read_query'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_call_plan_day', 'agentsociety2.config.llm_dispatcher'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_call_plan_day', 'worldmock.simulation_engine.scheduling.plans.day_planning'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_call_memory_digest', 'agentsociety2.config.llm_dispatcher'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_call_memory_digest', 'worldmock.simulation_engine.evidence.memory_digest'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_cleanup_runtime', 'ray'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_execute', 'agentsociety2.config.llm_dispatcher'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_execute', 'ray'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_generate_day_plan', 'worldmock.simulation_engine.scheduling.plans.day_plan_inputs'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_generate_day_plan', 'worldmock.simulation_engine.scheduling.plans.day_planning'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_generate_memory_digest', 'worldmock.simulation_engine.evidence.memory_digest'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_intervention_sha256', 'worldmock.foundation.serialization'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_log_memory_digest_call', 'worldmock.recording_engine.storage.opslog'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_log_plan_day_call', 'worldmock.recording_engine.storage.opslog'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_ops_log', 'worldmock.recording_engine.storage.opslog'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_prepare_new_run', 'worldmock.configuration.inputs.environment'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_prepare_new_run', 'worldmock.runtime.framework.setup'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_prepare_resume', 'worldmock.configuration.inputs.environment'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_prepare_resume', 'worldmock.recording_engine.storage.store'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_prepare_resume', 'worldmock.runtime.framework.setup'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_prepare_resume', 'worldmock.simulation_engine.decisions.continuation'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_prepare_resume', 'worldmock.simulation_engine.world.run_identity'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_prepare_resume', 'worldmock.state.world.world'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_require_custom_modules', 'agentsociety2.registry'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_require_runtime_identity', 'worldmock.runtime.framework.setup'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_require_runtime_identity', 'worldmock.simulation_engine.decisions.continuation'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_restore_agents', 'worldmock.recording_engine.storage.checkpoint'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_restore_agents', 'worldmock.simulation_engine.decisions.continuation'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_restore_branch', 'worldmock.recording_engine.storage.checkpoint'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_restore_branch', 'worldmock.recording_engine.storage.store'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_restored_specs', 'worldmock.runtime.framework.setup'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_run_identity', 'worldmock.runtime.framework.setup'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_run_identity', 'worldmock.state.identity'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_society_specs', 'worldmock.recording_engine.storage.opslog'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_society_specs', 'worldmock.runtime.framework.setup'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_terminate_failed_actor', 'ray'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_validate_agent_workspace', 'agentsociety2.agent.memory'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_validate_agent_workspace', 'worldmock.runtime.framework.setup'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', '_validate_persona_card_struct', 'worldmock.configuration.persona.persona_card'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', 'settle_pending_day_plans', 'worldmock.simulation_engine.scheduling.plans.day_planning'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.runtime.lifecycle.runner', 'settle_pending_memory_digests', 'worldmock.simulation_engine.evidence.memory_digest'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.actions.dispatcher', 'ActionHandlers._resume_at_start_location', 'worldmock.simulation_engine.activities.content.interact'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.activities.content.interact', 'InteractivityRuntime._collect', 'worldmock.simulation_engine.commerce.purchase.storefront_runtime'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.commerce.fulfillment.receipt', '_food_effect', 'worldmock.simulation_engine.body.body'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.decisions.decision_coordinator', 'DecisionCoordinator._action_callbacks', 'worldmock.simulation_engine.scheduling.callbacks.callbacks'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.decisions.decision_coordinator', 'DecisionCoordinator._consumed_on_start', 'worldmock.simulation_engine.scheduling.callbacks.callbacks'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.decisions.decision_coordinator', 'DecisionCoordinator._same_moment_callbacks', 'worldmock.simulation_engine.scheduling.callbacks.callbacks'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.decisions.decision_coordinator', 'DecisionCoordinator._same_moment_callbacks', 'worldmock.state.planning.schedule'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.decisions.decision_coordinator', 'DecisionCoordinator._validate_call', 'agentsociety2.society'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.decisions.decision_coordinator', 'DecisionCoordinator.commit_decision_round', 'worldmock.simulation_engine.scheduling.callbacks.callbacks'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.decisions.decision_coordinator', 'DecisionCoordinator.validate_context', 'agentsociety2.society'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.observation.layers.environment_layer', '_duty', 'worldmock.configuration.persona.work_schedule'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.observation.layers.environment_layer', '_tomorrow', 'worldmock.configuration.persona.work_schedule'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.scheduling.clock.effects', '_deliver_orders', 'worldmock.simulation_engine.commerce.fulfillment.commerce'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.scheduling.clock.effects', 'apply_scheduled_effect', 'worldmock.simulation_engine.scheduling.callbacks.app_push'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.scheduling.clock.time_advance', 'TimeAdvanceCoordinator._advance_one_activity', 'worldmock.simulation_engine.scheduling.callbacks.callbacks'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.scheduling.clock.time_advance', 'TimeAdvanceCoordinator._apply_activity_effect', 'worldmock.simulation_engine.activities.content.interact'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.scheduling.clock.time_advance', 'TimeAdvanceCoordinator._apply_activity_effect', 'worldmock.simulation_engine.commerce.purchase.storefront_runtime'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.scheduling.clock.time_advance', 'TimeAdvanceCoordinator._commit_advance', 'worldmock.simulation_engine.scheduling.callbacks.callbacks'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.scheduling.clock.time_advance', 'TimeAdvanceCoordinator._run_stop', 'worldmock.simulation_engine.scheduling.callbacks.callbacks'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.scheduling.clock.time_advance', 'TimeAdvanceCoordinator._truncate', 'worldmock.simulation_engine.commerce.purchase.storefront_runtime'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.scheduling.clock.time_advance', 'TimeAdvanceCoordinator._wake_callbacks', 'worldmock.simulation_engine.scheduling.callbacks.callbacks'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.scheduling.clock.time_advance', 'TimeAdvanceCoordinator._wake_callbacks', 'worldmock.state.planning.schedule'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.scheduling.clock.time_advance', 'TimeAdvanceCoordinator.scheduling_state', 'agentsociety2.society'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.world.context', 'EngineContext.validate', 'worldmock.simulation_engine.activities.lifecycle.activity_validation'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.world.context', 'EngineContext.validate', 'worldmock.simulation_engine.scheduling.callbacks.callbacks'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.world.world_setup', '_initial_agent', 'datetime'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.world.world_setup', '_initial_agent', 'worldmock.simulation_engine.body.body'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.world.world_setup', '_initial_agent', 'worldmock.simulation_engine.body.body_config'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.world.world_setup', '_initial_state', 'worldmock.simulation_engine.activities.lifecycle.sessions'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.world.world_setup', '_initial_state', 'worldmock.simulation_engine.scheduling.clock.weather'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.world.world_setup', '_run_start_callbacks', 'worldmock.simulation_engine.scheduling.callbacks.callbacks'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.world.world_setup', '_run_start_callbacks', 'worldmock.simulation_engine.scheduling.clock.effects'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.world.world_setup', '_validate_creation_model', 'worldmock.runtime.framework.setup'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.world.world_setup', 'create_world', 'worldmock.simulation_engine.scheduling.clock.compile'): "按原调用时机延迟加载，保持输入纯加载边界",
    ('worldmock.simulation_engine.world.world_updates', '_activity_origin', 'worldmock.simulation_engine.activities.lifecycle.activities'): "按原调用时机延迟加载，保持输入纯加载边界",
}


@dataclass(frozen=True)
class ImportEdge:
    """一条运行期依赖，同时保留局部导入的登记位置。"""

    source: str
    target: str
    scope: str
    local: bool


def modules(root: Path) -> dict[str, Path]:
    """枚举固定的两个项目包根。"""
    found = {}
    for folder, prefix in (("src/worldmock", "worldmock"), ("custom", "custom"), ("tools/analysis", "tools.analysis")):
        for path in sorted((root / folder).rglob("*.py")):
            parts = list(path.relative_to(root / folder).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            found[".".join([prefix, *parts])] = path
    return found


def _loader_kind(node: ast.AST, bindings: dict[str, str]) -> str | None:
    """识别真实导入函数、模块对象及其静态别名，反射无法解析时拒绝隐藏依赖。"""
    if ast.unparse(node) in bindings:
        return bindings[ast.unparse(node)]
    if isinstance(node, ast.Attribute):
        owner = _loader_kind(node.value, bindings)
        if owner == "sys" and node.attr == "modules":
            return "module_namespace"
        if owner == "importlib" and node.attr == "import_module" or owner == "builtins" and node.attr in {
                "__import__", "getattr", "eval", "exec", "vars"}:
            return node.attr
        if owner in {"importlib", "builtins"} and node.attr == "__dict__":
            return owner + "_namespace"
        if owner in {"importlib", "builtins", "importlib_namespace", "builtins_namespace",
                     "module_namespace", "global_namespace"} and node.attr in {
                "__getattribute__", "__getattr__", "get"}:
            return "unresolved"
        if owner in {"importlib_namespace", "builtins_namespace", "module_namespace", "global_namespace"}:
            return "unresolved"
    if isinstance(node, ast.Subscript):
        owner = _loader_kind(node.value, bindings)
        if owner in {"module_namespace", "global_namespace"}:
            if isinstance(node.slice, ast.Constant):
                member = node.slice.value
                if owner == "module_namespace" and member in {"importlib", "builtins"}:
                    return member
                if owner == "global_namespace" and member == "__builtins__":
                    return "builtins"
            return "unresolved"
        if owner in {"importlib_namespace", "builtins_namespace", "builtins"}:
            if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
                member = ast.Attribute(value=ast.Name(id=owner.removesuffix("_namespace")), attr=node.slice.value)
                return _loader_kind(member, bindings)
            return "unresolved"
    if isinstance(node, ast.Call):
        function = _loader_kind(node.func, bindings)
        if function in {"globals", "locals"}:
            return "global_namespace"
        if function == "vars":
            return "unresolved" if node.args else "global_namespace"
        if function in {"import_module", "__import__"} and node.args and isinstance(node.args[0], ast.Constant):
            if node.args[0].value in {"importlib", "builtins"}:
                return node.args[0].value
        if function != "getattr" or not node.args:
            return None
        owner = _loader_kind(node.args[0], bindings)
        if owner in {"importlib", "builtins"}:
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
                member = ast.Attribute(value=node.args[0], attr=node.args[1].value)
                return _loader_kind(member, bindings) or "unresolved"
            return "unresolved"
    return None


def _loader_bindings(tree: ast.AST) -> dict[str, str]:
    """跟踪模块别名、函数对象别名和显式对象成员赋值。"""
    bindings = {name: name for name in ("__import__", "importlib", "builtins", "getattr", "eval", "exec",
                                        "sys", "globals", "locals", "vars")}
    bindings["__builtins__"] = "builtins"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                if name.name in {"importlib", "builtins", "sys"}:
                    bindings[name.asname or name.name] = name.name
        elif isinstance(node, ast.ImportFrom):
            for name in node.names:
                if node.module == "sys" and name.name == "modules":
                    bindings[name.asname or name.name] = "module_namespace"
                if node.module == "importlib" and name.name == "*":
                    bindings["import_module"] = "import_module"
                if node.module == "importlib" and name.name == "import_module" or node.module == "builtins" and name.name in {
                        "__import__", "getattr", "eval", "exec", "globals", "locals", "vars"}:
                    bindings[name.asname or name.name] = name.name
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
                kind = _loader_kind(node.value, bindings)
                if kind is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    name = ast.unparse(target)
                    if name not in bindings:
                        bindings[name] = kind
                        changed = True
    return bindings


def _dynamic_target(node: ast.Call, kind: str, package: str) -> str:
    """解析完整的动态导入调用；运行时拼接、未知相对根或未知层级失败关闭。"""
    first = node.args[0] if node.args else next((item.value for item in node.keywords if item.arg == "name"), None)
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str) or kind == "unresolved":
        return "<unresolved dynamic import>"
    target = first.value
    if kind == "__import__":
        level = node.args[4] if len(node.args) > 4 else next(
            (item.value for item in node.keywords if item.arg == "level"), ast.Constant(0))
        if not isinstance(level, ast.Constant) or not isinstance(level.value, int) or level.value < 0:
            return "<unresolved dynamic import>"
        target = "." * level.value + target
        if level.value:
            declared = node.args[1] if len(node.args) > 1 else next(
                (item.value for item in node.keywords if item.arg == "globals"), None)
            if isinstance(declared, ast.Dict):
                selected = next((value for key, value in zip(declared.keys, declared.values)
                                 if isinstance(key, ast.Constant) and key.value == "__package__"), None)
                if not isinstance(selected, ast.Constant) or not isinstance(selected.value, str):
                    return "<unresolved dynamic import>"
                package = selected.value
            elif declared is not None and not (isinstance(declared, ast.Call)
                                               and ast.unparse(declared.func) == "globals"):
                return "<unresolved dynamic import>"
    else:
        explicit = node.args[1] if len(node.args) > 1 else next(
            (item.value for item in node.keywords if item.arg == "package"), None)
        if target.startswith(".") and explicit is not None:
            if isinstance(explicit, ast.Constant) and isinstance(explicit.value, str):
                package = explicit.value
            elif not isinstance(explicit, ast.Name) or explicit.id != "__package__":
                return "<unresolved dynamic import>"
    if target.startswith("."):
        try:
            target = resolve_name(target, package)
        except (ImportError, ValueError):
            return "<unresolved dynamic import>"
    return target


def imports(module: str, path: Path, available: dict[str, Path]) -> list[ImportEdge]:
    """解析相对、别名、成员模块及显式动态项目导入，忽略纯类型分支。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = module if path.name == "__init__.py" else module.rsplit(".", 1)[0]
    dynamic = _loader_bindings(tree)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    type_names = {"TYPE_CHECKING", "typing.TYPE_CHECKING"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for name in node.names:
                if name.name == "typing":
                    type_names.add(f"{name.asname or name.name}.TYPE_CHECKING")
        elif isinstance(node, ast.ImportFrom):
            for name in node.names:
                if node.module == "typing" and name.name == "TYPE_CHECKING":
                    type_names.add(name.asname or name.name)
    edges = []

    def visit(node, scope="", local=False):
        if isinstance(node, ast.If) and ast.unparse(node.test) in type_names:
            for child in node.orelse:
                visit(child, scope, local)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = ".".join(filter(None, (scope, node.name)))
            local = local or not isinstance(node, ast.ClassDef)
        targets = []
        if isinstance(node, ast.Import):
            targets = [name.name for name in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join([*package.split(".")[:len(package.split(".")) - node.level + 1],
                                 *([base] if base else [])])
            targets = [f"{base}.{name.name}" if f"{base}.{name.name}" in available else base
                       for name in node.names]
        elif isinstance(node, ast.Call):
            kind = _loader_kind(node.func, dynamic)
            if kind in {"eval", "exec"}:
                targets = ["<unresolved dynamic import>"]
            elif kind in {"import_module", "__import__", "unresolved"}:
                targets = [_dynamic_target(node, kind, package)]
        if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript, ast.Call)):
            kind = _loader_kind(node, dynamic)
            parent = parents.get(node)
            called = isinstance(parent, ast.Call) and parent.func is node
            assigned = isinstance(parent, (ast.Assign, ast.AnnAssign, ast.NamedExpr)) and parent.value is node
            if kind in {"import_module", "__import__", "unresolved", "getattr", "eval", "exec"}:
                if not called and not assigned:
                    targets.append("<unresolved dynamic import>")
            elif kind in {"importlib", "builtins", "importlib_namespace", "builtins_namespace",
                          "module_namespace", "global_namespace"}:
                member = isinstance(parent, (ast.Attribute, ast.Subscript)) and parent.value is node
                reflected = isinstance(parent, ast.Call) and _loader_kind(parent.func, dynamic) == "getattr"
                if not (assigned or member or reflected or isinstance(parent, ast.Expr)):
                    targets.append("<unresolved dynamic import>")
        for target in sorted(set(targets)):
            edges.append(ImportEdge(module, target, scope, local))
        for child in ast.iter_child_nodes(node):
            visit(child, scope, local)

    visit(tree)
    return edges


def cycles(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """深度遍历报告闭合路径，包含自环。"""
    active, visited, found = [], set(), []

    def visit(module):
        if module in active:
            found.append(tuple(active[active.index(module):] + [module]))
            return
        if module in visited:
            return
        active.append(module)
        for target in sorted(graph[module]):
            visit(target)
        active.pop()
        visited.add(module)

    for module in sorted(graph):
        visit(module)
    return found


ENGINE_BOUNDARIES = {
    "worldmock.foundation": ("worldmock.state", "worldmock.resource_engine", "worldmock.exchange_engine",
                             "worldmock.recording_engine", "worldmock.simulation_engine", "worldmock.agents",
                             "worldmock.configuration", "worldmock.runtime"),
    "worldmock.state": ("worldmock.recording_engine", "worldmock.simulation_engine", "worldmock.agents",
                        "worldmock.runtime"),
    "worldmock.resource_engine": ("worldmock.recording_engine", "worldmock.simulation_engine", "worldmock.agents",
                                  "worldmock.runtime"),
    "worldmock.exchange_engine": ("worldmock.state", "worldmock.resource_engine", "worldmock.recording_engine",
                                  "worldmock.simulation_engine", "worldmock.agents", "worldmock.configuration",
                                  "worldmock.runtime"),
    "worldmock.recording_engine": ("worldmock.simulation_engine", "worldmock.agents", "worldmock.runtime"),
}


def _belongs(module: str, root: str) -> bool:
    """按完整包名匹配，不让相似前缀误判为同一职责。"""
    return module == root or module.startswith(root + ".")


def _engine_boundary(edge: ImportEdge) -> str | None:
    """四个引擎的基础职责与分析工具保持明确依赖方向。"""
    if edge.source.startswith(("worldmock.", "custom.")) and edge.target.startswith(("tools.", "scripts.", "tests.")):
        return f"Engine boundary: {edge.source} -> {edge.target}"
    for owner, forbidden in ENGINE_BOUNDARIES.items():
        if _belongs(edge.source, owner) and any(_belongs(edge.target, target) for target in forbidden):
            return f"Engine boundary: {edge.source} -> {edge.target}"
    return None


def violations(root: Path, registry=None, foundation=FOUNDATION) -> list[str]:
    """检查完整无环、准确延迟登记、基础依赖和门面消费者。"""
    available = modules(root)
    edges = [edge for module, path in available.items() for edge in imports(module, path, available)]
    graph = {module: set() for module in available}
    problems = []
    local = set()
    for edge in edges:
        if edge.target == "<unresolved dynamic import>":
            problems.append(f"Unresolved dynamic import: {edge.source} {edge.scope}")
            continue
        if edge.local:
            local.add((edge.source, edge.scope, edge.target))
        if problem := _engine_boundary(edge):
            problems.append(problem)
        if edge.target not in available:
            continue
        graph[edge.source].add(edge.target)
        if edge.source in foundation and edge.target not in foundation:
            problems.append(f"Foundation dependency: {edge.source} -> {edge.target}")
        if edge.target == 'worldmock.simulation_engine.engine' and edge.source != "custom.envs.life_env":
            problems.append(f"Facade consumer: {edge.source}")
    allowed = DELAYED_IMPORTS if registry is None else registry
    for item in sorted(local - set(allowed)):
        problems.append(f"Unregistered local import: {item}")
    for item in sorted(set(allowed) - local):
        problems.append(f"Stale local import registration: {item}")
    for cycle in cycles(graph):
        problems.append(f"Dependency cycle: {' -> '.join(cycle)}")
    return problems


def test_production_import_graph_obeys_all_boundaries():
    assert violations(ROOT) == []


@pytest.mark.parametrize("source,target", [
    ("worldmock.resource_engine.catalog.loader", "worldmock.simulation_engine.engine"),
    ("worldmock.exchange_engine.ledger.ledger", "worldmock.state.world.world"),
    ("worldmock.recording_engine.storage.store", "worldmock.simulation_engine.world.context"),
    ("worldmock.state.world.world", "worldmock.runtime.lifecycle.runner"),
    ("worldmock.foundation.serialization", "worldmock.recording_engine.events.models"),
    ("worldmock.recording_engine.reports.quality", "tools.analysis.behavior.metrics"),
])
def test_engine_boundaries_reject_reverse_imports(tmp_path, source, target):
    """在隔离源码中注入反向依赖，证明包规则会实际拒绝。"""
    _package(tmp_path, {source: "import " + target, target: ""})
    assert any("Engine boundary" in item for item in violations(tmp_path, {}))


def _package(root: Path, source: dict[str, str]):
    for name, body in source.items():
        path = root / "src" / Path(*name.split(".")).with_suffix(".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


@pytest.mark.parametrize("body", [
    "import worldmock.b as peer", "from . import b as peer", "from worldmock import b as peer",
    "def use():\n    import worldmock.b",
    "import importlib as loader\nloader.import_module('worldmock.b')",
    "from importlib import import_module as load\nload('worldmock.b')",
    "__import__('worldmock.b')",
    "import importlib\ngetattr(importlib, 'import_module')('worldmock.b')",
    "import importlib as library\nload = library.import_module\nload('worldmock.b')",
    "import importlib\nlibrary = importlib\nload = getattr(library, 'import_module')\nload('worldmock.b')",
    "load = __import__\nload('worldmock.b')",
    "from builtins import __import__ as load\nload('worldmock.b')",
    "import builtins as library\ngetattr(library, '__import__')('worldmock.b')",
    "import importlib\nproxy.load = importlib.import_module\nproxy.load('worldmock.b')",
    "import importlib\nload = importlib.__dict__['import_module']\nload('worldmock.b')",
    "from builtins import getattr as member\nimport importlib\nmember(importlib, 'import_module')('worldmock.b')",
    "getattr(__import__('importlib'), 'import_module')('worldmock.b')",
    "from importlib import *\nimport_module('worldmock.b')",
    "import sys\nsys.modules['importlib'].import_module('worldmock.b')",
    "import sys\nsys.modules['builtins'].__import__('worldmock.b')",
    "globals()['__builtins__'].__import__('worldmock.b')",
    "globals()['__builtins__']['__import__']('worldmock.b')",
    "from sys import modules as loaded\nloaded['importlib'].import_module('worldmock.b')",
])
def test_cycle_guard_covers_every_import_form(tmp_path, body):
    _package(tmp_path, {"worldmock.a": body, "worldmock.b": "from worldmock import a"})
    assert any("Dependency cycle" in item for item in violations(tmp_path))


def test_type_checking_edges_do_not_create_runtime_cycles(tmp_path):
    _package(tmp_path, {
        "worldmock.a": "from typing import TYPE_CHECKING as checking\nif checking:\n    import worldmock.b",
        "worldmock.b": "import worldmock.a",
    })
    assert violations(tmp_path, {}) == []


def test_local_registry_rejects_missing_and_stale_entries(tmp_path):
    _package(tmp_path, {"worldmock.a": "def use():\n    import json"})
    key = ("worldmock.a", "use", "json")
    assert any("Unregistered" in item for item in violations(tmp_path))
    assert violations(tmp_path, {key: "CLI startup"}) == []
    assert any("Stale" in item for item in violations(tmp_path, {("worldmock.a", "old", "json"): "CLI"}))


def test_foundation_cannot_depend_on_execution(tmp_path):
    _package(tmp_path, {'worldmock.state.world.world': "import worldmock.simulation_engine.actions", 'worldmock.simulation_engine.actions': ""})
    assert any("Foundation dependency" in item for item in violations(tmp_path))


def test_only_environment_can_import_facade(tmp_path):
    _package(tmp_path, {'worldmock.simulation_engine.engine': "", 'worldmock.simulation_engine.reader': "import worldmock.simulation_engine.engine"})
    assert any("Facade consumer" in item for item in violations(tmp_path))


@pytest.mark.parametrize("body", [
    "import importlib as loader\nloader.import_module('..b', package='worldmock.branch')",
    "from importlib import import_module as load\nload('.b', 'worldmock')",
    "from importlib import import_module as load\nload('..b', __package__)",
    "__import__('b', globals(), locals(), [], 2)",
    "__import__('b', level=2)",
    "load = __import__\nload('b', globals={'__package__': 'worldmock.branch'}, level=2)",
])
def test_dynamic_relative_imports_resolve_the_real_package(tmp_path, body):
    _package(tmp_path, {"worldmock.branch.a": body, "worldmock.b": "import worldmock.branch.a"})
    assert any("Dependency cycle" in item for item in violations(tmp_path, {}))


@pytest.mark.parametrize("body", [
    "import importlib\nimportlib.import_module(target)",
    "import importlib\ngetattr(importlib, member)('worldmock.b')",
    "import importlib\nproxy = {'load': importlib.import_module}\nproxy['load']('worldmock.b')",
    "import importlib\ndef loader():\n    return importlib.import_module\nloader()('worldmock.b')",
    "import importlib\nimportlib.import_module('.b', package=selected_package)",
    "load = __import__\nload('b', level=selected_level)",
    "load = __import__\nload('b', globals=selected_globals, level=1)",
    "import importlib\neval('importlib.import_module')('worldmock.b')",
    "exec('import worldmock.b')",
    "import operator, importlib\noperator.methodcaller('import_module', 'worldmock.b')(importlib)",
    "import importlib\nload = importlib.__getattribute__('import_module')\nload('worldmock.b')",
    "import importlib\nload = importlib.__dict__.get('import_module')\nload('worldmock.b')",
    "import sys\nsys.modules[selected_library].import_module('worldmock.b')",
    "globals()[selected_library].import_module('worldmock.b')",
    "import sys\nsys.modules.get('importlib').import_module('worldmock.b')",
    "from sys import modules as loaded\nloaded.get('importlib').import_module('worldmock.b')",
    "globals().get('__builtins__').__import__('worldmock.b')",
    "import importlib\nvars(importlib)['import_module']('worldmock.b')",
    "import builtins\nvars(builtins)['__import__']('worldmock.b')",
    "from builtins import vars as members\nimport importlib\nmembers(importlib)['import_module']('worldmock.b')",
])
def test_unknown_dynamic_import_targets_or_object_proxies_fail_closed(tmp_path, body):
    _package(tmp_path, {"worldmock.a": body, "worldmock.b": "import worldmock.a"})
    assert any("Unresolved dynamic import" in item for item in violations(tmp_path, {}))


@pytest.mark.parametrize("module, retired", [
    ('worldmock.simulation_engine.actions.name_resolution', {"way_options", "way_verb", "HOME_WORDS", "_keys", "bare_name",
                                       "resolve_name", "names_store", "written_place"}),
    ('worldmock.simulation_engine.engine', {"_failure_draft", "affordance_contract", "AFFORDANCE_CONTRACT_KEYS"}),
    ('worldmock.runtime.lifecycle.runner', {"MAX_REACT_TURNS", "_configure_process", "_model_identity", "stage_model_identity",
                           "runtime_code_sha256", "_git_state", "creation_inputs", "_agent_specs", "_compile_persona",
                           "validate_manifest_stage_identity"}),
    ('worldmock.configuration.inputs.validation', {"build_coverage_targets", "validate_manifest_stage_identity"}),
    ('worldmock.recording_engine.export.export', {"_load_render_day"}),
    ('tools.analysis.behavior.metrics', {"_request_tool_names"}),
])
def test_moved_declarations_have_no_same_name_binding_at_old_modules(module, retired):
    tree = ast.parse(modules(ROOT)[module].read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(item.asname or item.name.split(".")[0] for item in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(item.id for target in targets for item in ast.walk(target) if isinstance(item, ast.Name))
    assert names.isdisjoint(retired | {"__getattr__"})


def test_footprint_store_has_no_branch_restoration_compatibility_method():
    tree = ast.parse(modules(ROOT)['worldmock.recording_engine.storage.store'].read_text(encoding="utf-8"))
    store = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "FootprintStore")
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "restore"
                   for node in store.body)
