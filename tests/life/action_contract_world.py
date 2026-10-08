"""@brief 名称、路由和查询共用真实登记目录与严格状态的纯世界。"""

from dataclasses import replace

from tests.integration.life.test_content_select import registered_content_world
from worldmock.life.discovery import product_bindings
from worldmock.life.interaction_menu import binding_action, resolve_interaction_menu
from worldmock.life.object_labels import object_label
from worldmock.life.recipients import person_bindings
from worldmock.life.rules import LifeRuleEngine
from worldmock.life.tool_layer import LayerContext, annotated_bindings, tool_layer


def contract_world(location="home"):
    """@brief 冻结与实际工具呈现同形的观察，不构造引擎或写 SQLite。"""
    semantics, state = registered_content_world()
    agent = state.agents[1]
    agent.location_id = location
    state.sessions["stay-1"].location_id = location
    scenario, catalog = semantics.scenario, semantics.catalog
    context = LayerContext(state, scenario, LifeRuleEngine(scenario, catalog), semantics, 1,
                           {"activities": {}, "sessions": {}}, [], resolve_interaction_menu(scenario, 1, location))
    rows = [{"object": object_label("location", place.display_name), "kind": "location",
             "object_id": place.location_id,
             "actions": [binding_action("步行", "travel_to", travel_mode="walking"),
                         binding_action("驾车", "travel_to", travel_mode="driving")]}
            for place in scenario.locations if place.location_id != location]
    rows.extend(product_bindings(context))
    rows.extend({"object": action.name, "kind": "action", "object_id": action.action_id,
                 "category": action.category, "manner": action.manner, "app_ids": action.app_ids,
                 "communication_mode": action.communication_mode,
                 "actions": [binding_action("进行", "do", app_id=identity,
                                            category=action.category, manner=action.manner)
                             for identity in action.app_ids or (None,)]} for action in catalog.actions.values())
    rows.append({"object": "人物：Second Test Person", "kind": "person", "object_id": "agent:2", "actions": []})
    config = next(person for person in scenario.agents if person.agent_id == 1)
    rows.extend({"object": object_label("contact", item.salutation), "kind": "contact",
                 "object_id": item.contact_id, "actions": []} for item in config.contacts)
    rows.append({"object": "自己", "kind": "self", "object_id": "agent:1",
                 "actions": [binding_action("等待", "wait"), binding_action("睡觉", "sleep")]})
    context = replace(context, bindings=rows)
    value = {"agent_id": 1, "location_id": location, "home_location_id": "home",
             "locations": [{"location_id": place.location_id, "name": place.display_name, "kind": place.kind}
                           for place in scenario.locations],
             "actions": [action.model_dump(mode="json") for action in catalog.actions.values()],
             "apps": [app.model_dump(mode="json") for app in catalog.apps.values()],
             "person_bindings": person_bindings(state, 1), "message_bindings": [],
             "interaction_bindings": annotated_bindings(context), "tool_layer": tool_layer(context, {})}
    return context, value
