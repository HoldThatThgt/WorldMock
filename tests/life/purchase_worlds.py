"""@brief 固定食品菜单购买的真实登记环境和严格世界模型。"""

from pathlib import Path

from worldmock.life.models import AgentState, BodyState, WorldState
from worldmock.life.world_setup import load_runtime_scenario

ENVIRONMENT = Path(__file__).resolve().parents[1] / "fixtures/environment"


def registered_world(location="home", balance=30000, scenario_path=None):
    """@brief 使用生产加载器读取实际目录、场景与账户，直接构造纯结算世界。"""
    semantics, digest = load_runtime_scenario(scenario_path or ENVIRONMENT / "scenarios/single-person.json")
    now = int(semantics.scenario.start_at.timestamp())
    agents = {}
    for config in semantics.scenario.agents:
        agents[config.agent_id] = AgentState(
            agent_id=config.agent_id, name=config.name, location_id=location if config.agent_id == 1 else config.home_location_id,
            balance_account_id=f"agent:{config.agent_id}", body_settled_at=now, next_decision_at=now,
            body=BodyState(energy_kcal=semantics.body.initial_energy_kcal, sleep_pressure=0,
                           sleep_deficit_hours=0, entertainment_need=0), inventory=config.initial_inventory)
    accounts = dict(semantics.accounts)
    accounts["agent:1"] = balance
    state = WorldState(schema_version=10, version=1, current_time=now,
                       end_time=int(semantics.scenario.end_at.timestamp()), run_id="pure-food",
                       timezone=semantics.scenario.timezone, scenario_sha256=digest,
                       content_manifest_sha256=semantics.document.corpus_manifest_sha256,
                       agents=agents, accounts=accounts, random_seed=7, random_state="pure-fixed")
    return semantics, state


def at_time(state, moment):
    """@brief 形成已到期的严格纯状态，身体时钟与世界时钟一致。"""
    agents = {identity: agent.model_copy(update={"body_settled_at": moment}) for identity, agent in state.agents.items()}
    return WorldState.model_validate(state.model_copy(update={"current_time": moment, "agents": agents}).model_dump())


def menu_context(location="home", balance=30000):
    """@brief 实际目录菜单经过生产发现、投影与菜单解析后组成工具上下文。"""
    from worldmock.life import admission, activity_control
    from worldmock.life.discovery import DiscoveryContext
    from worldmock.life.observe import _supply_entries
    from worldmock.life.projection import ProjectionInputs, interaction_bindings
    from worldmock.life.interaction_menu import resolve_interaction_menu
    from worldmock.life.tool_layer import LayerContext
    semantics, state = registered_world(location, balance)
    scenario, agent = semantics.scenario, state.agents[1]
    menu = resolve_interaction_menu(scenario, 1, location)
    entries = _supply_entries(DiscoveryContext(state, scenario, semantics, agent))
    inputs = ProjectionInputs(state, scenario, agent,
        extras={"product_rows": [entry.row for entry in entries], "interaction_menu": menu, "activity_rows": []})
    bindings = interaction_bindings(inputs)
    facts = admission.WorldFacts(semantics.catalog.work_topics, semantics.catalog.foods)
    names = activity_control.visible_names(state, 1, {})
    return LayerContext(state, scenario, facts, semantics, 1, names, bindings, menu)
