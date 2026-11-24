"""@brief 唯一世界的公开层和私有名称、结算证据及沙盒即时重算投影。"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from worldmock.exchange_engine.conditions.activity_rules import ACTIVITY_ACTIONS
from worldmock.foundation.time.readable import local_date
from worldmock.recording_engine.projections.activities.experiences import recent_verified_experiences
from worldmock.recording_engine.storage.observation_parts import split_observation
from worldmock.resource_engine.catalog.travel_methods import travel_methods
from worldmock.simulation_engine.actions import admission
from worldmock.simulation_engine.scheduling.plans import errands
from worldmock.simulation_engine.activities.content.location_activities import location_units, unit_binding, unit_locked
from worldmock.simulation_engine.activities.lifecycle import activity_control
from worldmock.simulation_engine.body import felt
from worldmock.simulation_engine.observation.layers import memory_layer, progress_layer, projection
from worldmock.simulation_engine.observation.layers.environment_layer import environment_layer
from worldmock.simulation_engine.observation.layers.todo_layer import todo_layer, visible_goals
from worldmock.simulation_engine.observation.targets import callable_index, discovery, rotation
from worldmock.simulation_engine.observation.targets.presentation import subtype_of, tiebreak_digest
from worldmock.simulation_engine.observation.targets.tool_layer import (
    LayerContext,
    annotated_bindings,
    tool_layer,
    with_bindings,
)
from worldmock.state.interaction.actions import binding_action
from worldmock.state.world.kinds import ActivityStatus
from worldmock.state.world.world import WorldState

#: 只给宿主生成提示、不写入观察事件的观察键。
_HOST_ONLY_KEYS = frozenset({"progress_layer", "progress_meta"})


class ObservationBuilder:
    """@brief 纯只读观察构建器：按已提交世界或沙盒世界生成完整观察层，不维护可变呈现状态。"""

    def __init__(self, context) -> None:
        """@brief 绑定引擎唯一上下文。"""
        self.context = context

    def build_wakeup_observation(self, agent) -> tuple[dict[str, Any], dict[str, str], dict[str, int]]:
        """@brief 为单次唤醒构建深拷贝观察、观察分件与本次产品曝光时间戳。"""
        data, stamps = self._observation_bundle(agent, self.context.state)
        _, parts = self.stored_observation(data)
        return deepcopy(data), parts, stamps

    def sandbox_layers(self, state: WorldState, base_observation: Mapping[str, Any]) -> dict[str, Any]:
        """@brief 用同一投影公式计算沙盒状态的最新环境层、待办层与工具层及活动绑定。"""
        agent = state.agents[base_observation["agent_id"]]
        selected_products = [row for row in base_observation["interaction_bindings"] if row["kind"] == "supply"]
        catalog_bindings = self._catalog_bindings(agent, state)
        layer = self._layer_context(agent, catalog_bindings, state)
        rows = [*catalog_bindings[:self._location_count()], *selected_products,
                *catalog_bindings[self._location_count():]]
        layer = with_bindings(layer, rows)
        layer = with_bindings(layer, annotated_bindings(layer))
        body = self._body(agent, state)
        return {
            "tool_layer": tool_layer(layer, callable_index.callable_index(layer)),
            "todo_layer": todo_layer(layer, self.context.store),
            "environment_layer": environment_layer(layer, body["levels"]),
            "activity_bindings": self._activity_bindings(state, agent.agent_id, layer.names, {}),
        }

    def _observation_data(self, agent) -> dict[str, Any]:
        """@brief 保持入口兼容的无副作用单参数构建。"""
        return self._observation_bundle(agent, self.context.state)[0]

    def _observation_bundle(self, agent, state: WorldState) -> tuple[dict[str, Any], dict[str, int]]:
        """@brief 数值、身份和原始目录只放私有证据，五个公开层独立构建并返回本次曝光时间戳。"""
        catalog_bindings = self._catalog_bindings(agent, state)
        layer = self._layer_context(agent, catalog_bindings, state)
        products = discovery.product_bindings(layer)
        selected, stamps = self._product_window(agent, products, state)
        rows = [*catalog_bindings[:self._location_count()], *selected,
                *catalog_bindings[self._location_count():]]
        layer = with_bindings(layer, rows)
        layer = with_bindings(layer, annotated_bindings(layer))
        body = self._body(agent, state)
        memory = memory_layer.memory_view(layer, self.context.store)
        view = progress_layer.progress_view(layer, self.context.store, memory.published, memory.facts.kits)
        data = {"agent_id": agent.agent_id, "current_time": state.current_time,
                "location_id": agent.location_id,
                "home_location_id": self.context.inputs.home_location_ids[agent.agent_id],
                "body": body, "balance_fen": state.accounts[agent.balance_account_id],
                "interaction_bindings": layer.bindings, "catalog_bindings": catalog_bindings,
                "locations": [{"location_id": key, "name": row.display_name}
                              for key, row in self.context.catalog.locations.items()],
                "tool_layer": tool_layer(layer, callable_index.callable_index(layer)),
                "todo_layer": todo_layer(layer, self.context.store),
                "environment_layer": environment_layer(layer, body["levels"]),
                "memory_layer": memory.layer, "persona_views": memory_layer.persona_views(layer, self.context.store),
                "progress_layer": view.layer, "progress_meta": view.meta,
                "progress_event_ids": view.event_ids, "known_activity_actions": list(ACTIVITY_ACTIONS),
                **self._name_bindings(state, agent, layer.names, memory.note_labels), **self._evidence(agent, state)}
        return data, stamps

    def stored_observation(self, data: dict) -> tuple[dict, dict[str, str]]:
        """@brief 观察的存储形式与它的观察分件：去掉只给宿主的键并按活动开始日期分段。"""
        kept = {key: value for key, value in data.items() if key not in _HOST_ONLY_KEYS}
        activities, zone = self.context.state.activities, self.context.config.timezone
        dates = [local_date(activities[row["activity_id"]].started_at, zone)
                 for row in data["activity_bindings"]]
        return split_observation(kept, dates)

    def _location_count(self) -> int:
        """@brief 地点行完整呈现，不参加商品窗口轮转。"""
        return len(self.context.catalog.locations)

    def _catalog_bindings(self, agent, state: WorldState | None = None) -> list[dict]:
        """@brief 按停留或路上状态列出地点与内容 app 的私有动作绑定行。"""
        world = self.context.state if state is None else state
        locations = sorted(self.context.catalog.locations.values(), key=lambda row: row.location_id)
        locations.sort(key=lambda row: row.location_id == agent.location_id)
        stay = admission.present_location(world, agent.agent_id)
        travel = [{**binding_action("travel", method.name), "travel_method_id": method.travel_method_id}
                  for method in travel_methods(self.context.catalog)]
        result = []
        for location in locations:
            if location.location_id != agent.location_id:
                actions = [dict(row) for row in travel]
            elif location.location_id == stay:
                actions = [unit_binding(unit, unit_locked(unit.contents, agent))
                           for unit in location_units(self.context.catalog, location.kind)]
                actions.extend([binding_action("sleep")] if location.sleepable else [])
            else:
                actions = []
            result.append({"object": location.display_name, "kind": "location", "object_id": location.location_id,
                           "actions": actions})
        catalog = self.context.catalog
        result.extend({"object": app.name, "kind": "app", "object_id": app.app_id,
                       "actions": [binding_action("interact")]}
                      for _, app in sorted(catalog.apps.items())
                      if app.role == "content" and catalog.contents_by_app[app.app_id])
        return result

    def _product_window(self, agent, bindings: list[dict], state: WorldState) -> tuple[list[dict], dict[str, int]]:
        """@brief 商品曝光使用既有轮转算法，返回入选绑定行与待合并曝光时间戳。"""
        config = self.context.config
        day = projection.local_datetime(state.current_time, config.timezone).date().isoformat()
        members = tuple(rotation.RotationMember(row["goods_id"], subtype_of(
            "product", self.context.catalog.goods[row["goods_id"]].category, row["goods_id"]), (),
            (tiebreak_digest(self.context.catalog.manifest.environment_id, agent.agent_id, day, row["goods_id"]),))
            for row in bindings)
        decision = rotation.rotate_candidates(members, dict(state.presentation_state.get(agent.agent_id, {})),
            rotation.RotationConfig(config.candidate_presentation_limit, config.rotation_quota_ratio,
                                    config.rotation_stale_seconds, state.current_time))
        selected = [identity for identity, _ in decision.presented]
        stamps = {identity: state.current_time for identity in selected}
        by_id = {row["goods_id"]: row for row in bindings}
        return [by_id[key] for key in selected], stamps

    def _layer_context(self, agent, bindings: list[dict], state: WorldState | None = None) -> LayerContext:
        """@brief 活动与目标名称来自当前观察世界；本人的未了活动在这里算一次，整次观察共用。"""
        world = self.context.state if state is None else state
        goals = [goal.goal for goal in visible_goals(world, agent.agent_id) or ()]
        taken = [*goals, *(row["object"] for row in bindings)]
        names = activity_control.visible_names(world, agent.agent_id, taken)
        current = activity_control.open_activities(world, agent.agent_id)
        return LayerContext(self.context, agent.agent_id, names, bindings, current, state_override=state)

    def _activity_bindings(self, state: WorldState, agent_id: int, names: dict, labels: dict) -> list[dict]:
        """@brief 为指定状态构造成品 activity_bindings 列表。"""
        activities = state.activities
        return [{"name": name, "activity_id": key, "title": None,
                 "phase": activity_control.activity_phase(activities[key]),
                 "resumable": activity_control.resumable_here(state, activities[key]),
                 "note_labels": [label for label, fulls in labels.items() if name in fulls]}
                for key, name in names["activities"].items()]

    def _name_bindings(self, state: WorldState, agent, names: dict, labels: dict) -> dict:
        """@brief 私有活动、目标与个人事务绑定供沙盒和环境共同校验。"""
        return {"goal_bindings": [{"task_name": goal.goal, "intent_kind": goal.intent_kind}
                                  for goal in visible_goals(state, agent.agent_id) or ()],
                "activity_bindings": self._activity_bindings(state, agent.agent_id, names, labels),
                **errands.bindings(agent, self.context.catalog.goods)}

    def _evidence(self, agent, state: WorldState) -> dict:
        """@brief 已核验经历、唤醒原因与进行中的活动只作为宿主证据。"""
        return {"verified_experiences": recent_verified_experiences(
                    self.context.store, agent.agent_id, self.context.store.last_event_seq),
                "wake_reasons": list(agent.pending_wake_reasons),
                "active_activities": [deepcopy(item.model_dump(mode="json"))
                    for item in state.activities.values()
                    if item.agent_id == agent.agent_id and item.status == ActivityStatus.ACTIVE]}

    def _body(self, agent, state=None) -> dict:
        """@brief 观察和决策参照都用对应人物的四体感投影。"""
        state = self.context.state if state is None else state
        persona = self.context.persona_by_id[agent.agent_id]
        windows = felt.MealWindows(lunch=persona.lunch_window, dinner=persona.dinner_window)
        hour = projection.hour_of_day(state.current_time, self.context.config.timezone)
        return projection.body_projection(agent, hour, self.context.bodies[agent.agent_id], windows)

    def snapshot_decision_references(self, staged: WorldState, agent_ids: list[int]) -> WorldState:
        """@brief 提交落定后更新相关人物的同源体感参照，独立运行不互扰。"""
        agents = dict(staged.agents)
        for agent_id in agent_ids:
            agent = agents[agent_id]
            agents[agent_id] = agent.model_copy(update={"last_decision_time": staged.current_time,
                "felt_bands_at_last_decision": self._body(agent, staged)["levels"]}, deep=True)
        return staged.model_copy(update={"agents": agents}, deep=True)


def layer_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 比较前后层字典，只返回确实发生变化的顶层层对象。"""
    return {key: deepcopy(value) for key, value in after.items()
            if key in {"tool_layer", "todo_layer", "environment_layer", "memory_layer", "progress_layer"}
            and before.get(key) != value}
