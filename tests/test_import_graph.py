"""生产依赖方向的完整 AST 守卫与有意破坏边界的反例。"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from importlib.util import resolve_name

import pytest

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION = frozenset({
    "worldmock.life.models", "worldmock.life.vocabulary", "worldmock.life.object_labels",
    "worldmock.life.readable", "worldmock.life.errand_state", "worldmock.life.schedule",
    "worldmock.life.social", "worldmock.catalog.affordances", "worldmock.errors",
    "worldmock.model_endpoint", "worldmock.scheduling.events", "worldmock.base_models",
    "worldmock.footprints.intervals",
    "worldmock.exchange.types",
})
# 运行配置尚未初始化前不能导入的重依赖与命令行启动边界。
DELAYED_IMPORTS: dict[tuple[str, str, str], str] = {('worldmock.catalog.admission', '_check_actions', 'worldmock.life.vocabulary'): '目录登记名称碰撞时读取唯一公开方式词声明',
 ('worldmock.runtime', '_prepare_resume', 'worldmock.life.models'): '恢复发布前按现行世界模型拒绝退役身份，不提前加载完整引擎',
 ('worldmock.life.observe', 'ObservationBuilder._layer_data', 'worldmock.life.environment_layer'): '出行纯观察不提前装载完整环境层',
 ('worldmock.life.relevance', 'entry_family', 'worldmock.life.candidates'): '纯清单加载不导入候选执行模块',
 ('worldmock.life.relevance', 'entry_family', 'worldmock.life.schedule'): '纯清单加载不导入活动执行模块',
 ('worldmock.life.relevance', 'intent_families', 'worldmock.life.schedule'): '纯清单加载不导入活动执行模块',
 ('worldmock.model_endpoint', 'IndependentModelClient.__init__', 'agentsociety2.config'): 'AS2 重依赖在正式运行配置完成后加载',
 ('worldmock.cli', '_compile_once', 'worldmock.profile.compile'): '命令行按所选命令延迟加载执行入口',
 ('worldmock.cli', '_dispatch', 'worldmock.validation'): '命令行按所选命令延迟加载执行入口',
 ('worldmock.cli', '_evaluate_command', 'worldmock.evaluation.runner'): '命令行按所选命令延迟加载执行入口',
 ('worldmock.cli', '_export_command', 'worldmock.footprints.export'): '命令行按所选命令延迟加载执行入口',
 ('worldmock.cli', '_export_command', 'worldmock.footprints.store'): '命令行按所选命令延迟加载执行入口',
 ('worldmock.cli', '_profile_check_report', 'worldmock.profile.models'): '命令行按所选命令延迟加载执行入口',
 ('worldmock.cli', '_profile_check_report', 'worldmock.profile.textgen'): '命令行按所选命令延迟加载执行入口',
 ('worldmock.cli', '_profile_command', 'worldmock.profile.extractor'): '命令行按所选命令延迟加载执行入口',
 ('worldmock.cli', '_profile_command', 'worldmock.profile.textgen'): '命令行按所选命令延迟加载执行入口',
 ('worldmock.cli', '_profile_command', 'worldmock.validation'): '命令行按所选命令延迟加载执行入口',
 ('worldmock.cli', '_run_command', 'worldmock.runtime'): '命令行按所选命令延迟加载执行入口',
 ('worldmock.cli', '_run_replay', 'worldmock.life.models'): '命令行按所选命令延迟加载执行入口',
 ('worldmock.cli', '_run_replay', 'worldmock.runtime'): '命令行按所选命令延迟加载执行入口',
 ('worldmock.life.decision_coordinator', 'DecisionCoordinator._validate_call', 'agentsociety2.society'): 'AS2、Ray '
                                                                                                         '或 '
                                                                                                         'TextWorld '
                                                                                                         '重依赖在正式运行配置完成后加载',
 ('worldmock.life.decision_coordinator', 'DecisionCoordinator.validate_context', 'agentsociety2.society'): 'AS2、Ray '
                                                                                                           '或 '
                                                                                                           'TextWorld '
                                                                                                           '重依赖在正式运行配置完成后加载',
 ('worldmock.life.read_query_runtime', 'life_env_actor_class', 'ray'): 'AS2、Ray 或 TextWorld 重依赖在正式运行配置完成后加载',
 ('worldmock.life.time_advance', 'TimeAdvanceCoordinator.scheduling_state', 'agentsociety2.society'): 'AS2、Ray 或 '
                                                                                                      'TextWorld '
                                                                                                      '重依赖在正式运行配置完成后加载',
 ('worldmock.run_setup', '_identity_problems', 'agentsociety2.config.llm_dispatcher'): 'AS2、Ray 或 TextWorld '
                                                                                       '重依赖在正式运行配置完成后加载',
 ('worldmock.run_setup', '_model_identity', 'custom.agents.life_agent'): 'AS2、Ray 或 TextWorld 重依赖在正式运行配置完成后加载',
 ('worldmock.run_setup', '_model_identity', 'worldmock.agents.life_prompt'): 'AS2、Ray 或 TextWorld 重依赖在正式运行配置完成后加载',
 ('worldmock.run_setup', '_require_loaded_config', 'agentsociety2.config.config'): 'AS2、Ray 或 TextWorld '
                                                                                   '重依赖在正式运行配置完成后加载',
 ('worldmock.run_setup', 'stage_model_identity', 'agentsociety2.config.llm_dispatcher'): 'AS2、Ray 或 TextWorld '
                                                                                         '重依赖在正式运行配置完成后加载',
 ('worldmock.run_setup', 'stage_model_identity', 'custom.agents.life_agent'): 'AS2、Ray 或 TextWorld '
                                                                              '重依赖在正式运行配置完成后加载',
 ('worldmock.run_setup', 'validate_stage_model_identity', 'custom.agents.life_agent'): 'AS2、Ray 或 TextWorld '
                                                                                       '重依赖在正式运行配置完成后加载',
 ('worldmock.runtime', '_build_society', 'agentsociety2.agent.service_proxy'): 'AS2、Ray 或 TextWorld '
                                                                               '重依赖在正式运行配置完成后加载',
 ('worldmock.runtime', '_build_society', 'agentsociety2.config.llm_dispatcher'): 'AS2、Ray 或 TextWorld '
                                                                                 '重依赖在正式运行配置完成后加载',
 ('worldmock.runtime', '_build_society', 'agentsociety2.env.direct_env_actor'): 'AS2、Ray 或 TextWorld '
                                                                                '重依赖在正式运行配置完成后加载',
 ('worldmock.runtime', '_build_society', 'agentsociety2.society'): 'AS2、Ray 或 TextWorld 重依赖在正式运行配置完成后加载',
 ('worldmock.runtime', '_cleanup_runtime', 'ray'): 'AS2、Ray 或 TextWorld 重依赖在正式运行配置完成后加载',
 ('worldmock.runtime', '_execute', 'agentsociety2.config.llm_dispatcher'): 'AS2、Ray 或 TextWorld 重依赖在正式运行配置完成后加载',
 ('worldmock.runtime', '_execute', 'ray'): 'AS2、Ray 或 TextWorld 重依赖在正式运行配置完成后加载',
 ('worldmock.runtime', '_require_custom_modules', 'agentsociety2.registry'): 'AS2、Ray 或 TextWorld 重依赖在正式运行配置完成后加载',
 ('worldmock.runtime', '_terminate_failed_actor', 'ray'): 'AS2、Ray 或 TextWorld 重依赖在正式运行配置完成后加载',
 ('worldmock.runtime', '_validate_agent_workspace', 'agentsociety2.agent.memory'): 'AS2、Ray 或 TextWorld '
                                                                                   '重依赖在正式运行配置完成后加载',
 ('worldmock.cli', '_environment_command', 'worldmock.catalog.errors'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.cli', '_environment_command', 'worldmock.catalog.loader'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.cli', '_environment_command', 'worldmock.catalog.mapgen'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.cli', '_environment_command', 'worldmock.catalog.models'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.engine_context', 'EngineContext.__post_init__', 'worldmock.life.activity_titles'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.engine_context', 'EngineContext.__post_init__', 'worldmock.life.rules'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.engine_context', 'EngineContext.validate', 'worldmock.life.activity_validation'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.world_setup', '_initial_agent', 'worldmock.life.body'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.world_setup', '_initial_agent', 'worldmock.life.readable'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.world_setup', '_initial_state', 'worldmock.life.sessions'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.world_setup', '_initial_state', 'worldmock.life.settlement'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.world_setup', '_initial_state', 'worldmock.scheduling.compile'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.world_setup', '_run_manifest', 'worldmock.life.relevance'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.world_setup', '_run_manifest', 'worldmock.profile.injection'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.world_setup', '_run_manifest', 'worldmock.run_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.world_setup', 'create_world', 'worldmock.footprints.models'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.world_setup', 'create_world', 'worldmock.footprints.store'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.run_setup', '_agent_specs', 'worldmock.profile.injection'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.run_setup', '_agent_specs', 'worldmock.profile.models'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.run_setup', '_compile_persona', 'worldmock.profile.compile'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.run_setup', '_compiled_parts', 'worldmock.profile.models'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.run_setup', '_coverage_targets', 'worldmock.profile.coverage_targets'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.run_setup', '_render_card', 'worldmock.profile.textgen'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.run_setup', 'input_warnings', 'worldmock.profile.coverage'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.run_setup', 'input_warnings', 'worldmock.profile.injection'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', 'CounterfactualRequest.__post_init__', 'worldmock.life.models'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_admit_scenario', 'worldmock.catalog.admission'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_build_society', 'worldmock.life.read_query_runtime'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_call_plan_day', 'worldmock.life.day_planning'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_commit_contact_reply', 'worldmock.footprints.models'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_commit_contact_reply', 'worldmock.life.social'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_commit_contact_reply', 'worldmock.scheduling.events'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_generate_day_plan', 'worldmock.life.day_plan_inputs'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_generate_day_plan', 'worldmock.life.day_planning'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_guarded_contact_reply', 'worldmock.life.reply_guard'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_intervention_sha256', 'worldmock.footprints.models'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_ops_log', 'worldmock.opslog'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_pending_reply_requests', 'worldmock.life.social'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_prepare_new_run', 'worldmock.life.world_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_prepare_new_run', 'worldmock.run_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_prepare_resume', 'worldmock.footprints.store'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_prepare_resume', 'worldmock.life.continuation'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_prepare_resume', 'worldmock.life.run_identity'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_prepare_resume', 'worldmock.life.world_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_require_runtime_identity', 'worldmock.life.continuation'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_require_runtime_identity', 'worldmock.run_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_restore_agents', 'worldmock.footprints.checkpoint'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_restore_agents', 'worldmock.life.continuation'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_restore_branch', 'worldmock.footprints.checkpoint'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_restore_branch', 'worldmock.footprints.store'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_run_identity', 'worldmock.life.models'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_run_identity', 'worldmock.run_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_society_specs', 'worldmock.opslog'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_society_specs', 'worldmock.run_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_validate_agent_workspace', 'worldmock.run_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_validate_persona_card_struct', 'worldmock.profile.injection'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', '_validate_persona_card_struct', 'worldmock.profile.models'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', 'replay_world', 'worldmock.run_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', 'resume_world', 'worldmock.run_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', 'run_world', 'worldmock.run_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', 'settle_pending_day_plans', 'worldmock.life.day_planning'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.runtime', 'settle_pending_model_replies', 'worldmock.life.social'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.validation', '_compile_persona', 'worldmock.profile.compile'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.validation', '_environment_failure', 'worldmock.catalog.errors'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.validation', '_validate_environment_scenarios', 'worldmock.life.world_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.validation', 'validate_environment', 'worldmock.catalog.loader'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.validation', 'validate_environment', 'worldmock.catalog.mapgen'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.validation', 'validate_persona_coverage', 'worldmock.catalog.loader'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.validation', 'validate_persona_coverage', 'worldmock.life.world_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.validation', 'validate_persona_coverage', 'worldmock.profile.coverage'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.validation', 'validate_persona_coverage', 'worldmock.profile.coverage_targets'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.validation', 'validate_persona_coverage', 'worldmock.run_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.validation', 'validate_project', 'worldmock.life.world_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.validation', 'validate_project', 'worldmock.run_setup'): '纯加载不触发完整引擎；实际执行入口按冻结波次加载职责模块',
 ('worldmock.life.world_setup', 'create_world', 'worldmock.life.models'): '纯世界校验在实际持久化入口执行，不在收集期加载引擎',
 ('worldmock.runtime', '_call_plan_day', 'worldmock.life.models'): '纯世界校验在实际持久化入口执行，不在收集期加载引擎',
 ('worldmock.runtime', '_commit_contact_reply', 'worldmock.life.models'): '纯世界校验在实际持久化入口执行，不在收集期加载引擎',
 ('worldmock.runtime', '_generate_day_plan', 'worldmock.life.models'): '纯世界校验在实际持久化入口执行，不在收集期加载引擎',
 ('worldmock.validation', 'validate_persona_coverage', 'worldmock.life.semantics'): '纯画像校验读取真实目录与场景语义，不装载完整引擎',
 ('worldmock.life.basket', '_validate_recipients', 'worldmock.life.order_routing'): '购买收货人拒绝共用当前配送引导；延迟导入避免购买与引导相互依赖',
 ('worldmock.life.named_purchase', '_request_candidate', 'worldmock.life.tool_layer'): '指名购买只在实际请求校验时读取工具层可用商品候选',
 ('worldmock.life.world_ops', '_activity_origin', 'worldmock.life.activities'): '世界事件只在构造真实活动来源时读取活动详情'}


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
    for folder, prefix in (("src/worldmock", "worldmock"), ("custom", "custom")):
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
        if edge.target not in available:
            continue
        graph[edge.source].add(edge.target)
        if edge.source in foundation and edge.target not in foundation:
            problems.append(f"Foundation dependency: {edge.source} -> {edge.target}")
        if edge.target == "worldmock.life.engine" and edge.source != "custom.envs.life_env":
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
    _package(tmp_path, {"worldmock.life.models": "import worldmock.life.actions", "worldmock.life.actions": ""})
    assert any("Foundation dependency" in item for item in violations(tmp_path))


def test_only_environment_can_import_facade(tmp_path):
    _package(tmp_path, {"worldmock.life.engine": "", "worldmock.life.reader": "import worldmock.life.engine"})
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
    ("worldmock.life.interaction_menu", {"WAYS", "PURCHASE_WAYS", "GIVE_WAY", "VERB_WAY_SEPARATOR"}),
    ("worldmock.life.name_resolution", {"way_options", "way_verb", "HOME_WORDS", "_keys", "bare_name",
                                       "resolve_name", "names_store", "written_place"}),
    ("worldmock.life.social_actions", {"CHAT_MET", "CALL_MET", "SOCIAL_RESOURCE_CLAIMS"}),
    ("worldmock.life.engine", {"_failure_draft", "affordance_contract", "AFFORDANCE_CONTRACT_KEYS"}),
    ("worldmock.runtime", {"MAX_REACT_TURNS", "_configure_process", "_model_identity", "stage_model_identity",
                           "runtime_code_sha256", "_git_state", "creation_inputs", "_agent_specs", "_compile_persona",
                           "validate_manifest_stage_identity"}),
    ("worldmock.validation", {"build_coverage_targets", "validate_manifest_stage_identity"}),
    ("worldmock.footprints.export", {"_load_render_day"}),
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
    tree = ast.parse(modules(ROOT)["worldmock.footprints.store"].read_text(encoding="utf-8"))
    store = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "FootprintStore")
    assert not any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "restore"
                   for node in store.body)
