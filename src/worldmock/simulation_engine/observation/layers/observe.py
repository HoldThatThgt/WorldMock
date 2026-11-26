"""@brief 唯一世界的公开层和私有名称、结算证据及沙盒即时重算投影。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from worldmock.agents.prompt.tool_groups import compact
from worldmock.exchange_engine.conditions.activity_rules import ACTIVITY_ACTIONS
from worldmock.foundation.time.readable import local_date
from worldmock.recording_engine.projections.activities.experiences import recent_verified_experiences
from worldmock.recording_engine.storage.observation_parts import split_observation
from worldmock.resource_engine.catalog.travel_methods import travel_methods
from worldmock.simulation_engine.actions import admission
from worldmock.simulation_engine.activities.content.location_activities import location_units, unit_binding, unit_locked
from worldmock.simulation_engine.activities.lifecycle import activity_control
from worldmock.simulation_engine.body import felt
from worldmock.simulation_engine.observation.layers import memory_layer, progress_layer, projection
from worldmock.simulation_engine.observation.layers.environment_layer import environment_layer
from worldmock.simulation_engine.observation.layers.todo_layer import todo_layer, visible_goals
from worldmock.simulation_engine.observation.targets import callable_index, discovery
from worldmock.simulation_engine.observation.targets.tool_layer import (
    LayerContext,
    _target_visible,
    annotated_bindings,
    tool_layer,
    with_bindings,
)
from worldmock.simulation_engine.scheduling.plans import errands
from worldmock.state.decisions.day_session import FIVE_LAYER_KEYS
from worldmock.state.interaction.actions import binding_action
from worldmock.state.world.kinds import ActivityStatus
from worldmock.state.world.world import WorldState

#: 只给宿主生成提示、不写入观察事件的观察键。
_HOST_ONLY_KEYS = frozenset({"progress_layer", "progress_meta"})
#: 内部观察键与公开中文层名映射，供层差量投影按固定次序输出。
_LAYER_SPECS: tuple[tuple[str, str], ...] = (
    ("environment_layer", "环境层"),
    ("memory_layer", "记忆层"),
    ("todo_layer", "待办层"),
    ("progress_layer", "进度层"),
    ("tool_layer", "工具层"),
)


class ObservationBuilder:
    """@brief 纯只读观察构建器：按已提交世界或沙盒世界生成完整观察层，不维护可变呈现状态。"""

    def __init__(self, context) -> None:
        """@brief 绑定引擎唯一上下文。"""
        self.context = context

    def build_wakeup_observation(
        self, agent, loaded_target_keys: Sequence[tuple[str, str]] = (),
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """@brief 为单次唤醒构建深拷贝观察与观察分件，不再维护商品轮转曝光戳。"""
        data = self._observation_bundle(agent, self.context.state, loaded_target_keys)
        _, parts = self.stored_observation(data)
        return deepcopy(data), parts

    def sandbox_layers(
        self, state: WorldState, base_observation: Mapping[str, Any],
        loaded_target_keys: Sequence[tuple[str, str]] = (),
    ) -> dict[str, Any]:
        """@brief 用同一投影公式计算沙盒状态的最新环境层、待办层与工具层及活动绑定。"""
        agent = state.agents[base_observation["agent_id"]]
        layer, _ = self._annotated_layer(agent, state, loaded_target_keys)
        body = self._body(agent, state)
        return {
            "tool_layer": tool_layer(layer, callable_index.callable_index(layer)),
            "todo_layer": todo_layer(layer, self.context.store),
            "environment_layer": environment_layer(layer, body["levels"]),
            "activity_bindings": self._activity_bindings(state, agent.agent_id, layer.names, {}),
        }

    @staticmethod
    def layer_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
        """@brief 计算五层之间的精细子键级差量并映射为公开中文层键。"""
        return layer_changes(before, after)

    def _observation_data(self, agent, loaded_target_keys: Sequence[tuple[str, str]] = ()) -> dict[str, Any]:
        """@brief 保持入口兼容的无副作用单参数构建。"""
        return self._observation_bundle(agent, self.context.state, loaded_target_keys)

    def _annotated_layer(
        self, agent, state: WorldState, loaded_target_keys: Sequence[tuple[str, str]] = (),
    ) -> tuple[LayerContext, list[dict]]:
        """@brief 构建含全部目录绑定与当前可见（核心常驻及已载入）目标的带标注层上下文。"""
        loaded_set = frozenset((kind, object_id) for kind, object_id in loaded_target_keys)
        base_bindings = self._catalog_bindings(agent, state)
        base_layer = self._layer_context(agent, base_bindings, state, loaded_set)
        all_products = discovery.product_bindings(base_layer)
        loc_count = self._location_count()
        catalog_bindings = [*base_bindings[:loc_count], *all_products, *base_bindings[loc_count:]]
        visible_rows = [row for row in catalog_bindings if _target_visible(base_layer, row)]
        annotated = annotated_bindings(with_bindings(base_layer, visible_rows))
        return with_bindings(base_layer, annotated), catalog_bindings

    def _observation_bundle(
        self, agent, state: WorldState, loaded_target_keys: Sequence[tuple[str, str]] = (),
    ) -> dict[str, Any]:
        """@brief 数值、身份和原始目录只放私有证据，五个公开层独立构建。"""
        layer, catalog_bindings = self._annotated_layer(agent, state, loaded_target_keys)
        body = self._body(agent, state)
        memory = memory_layer.memory_view(layer, self.context.store)
        view = progress_layer.progress_view(layer, self.context.store, memory.published, memory.facts.kits)
        return {
            "agent_id": agent.agent_id, "current_time": state.current_time,
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
            **self._name_bindings(state, agent, layer.names, memory.note_labels), **self._evidence(agent, state),
        }

    def stored_observation(self, data: dict) -> tuple[dict, dict[str, str]]:
        """@brief 观察的存储形式与它的观察分件：去掉只给宿主的键并按活动开始日期分段。"""
        kept = {key: value for key, value in data.items() if key not in _HOST_ONLY_KEYS}
        activities, zone = self.context.state.activities, self.context.config.timezone
        dates = [local_date(activities[row["activity_id"]].started_at, zone)
                 for row in data["activity_bindings"]]
        return split_observation(kept, dates)

    def _location_count(self) -> int:
        """@brief 完整目录中的地点条数，用于分隔地点与内容应用绑定。"""
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

    def _layer_context(
        self, agent, bindings: list[dict], state: WorldState | None = None,
        loaded_target_keys: frozenset[tuple[str, str]] = frozenset(),
    ) -> LayerContext:
        """@brief 活动与目标名称来自当前观察世界；本人的未了活动在这里算一次，整次观察共用。"""
        world = self.context.state if state is None else state
        goals = [goal.goal for goal in visible_goals(world, agent.agent_id) or ()]
        taken = [*goals, *(row["object"] for row in bindings)]
        names = activity_control.visible_names(world, agent.agent_id, taken)
        current = activity_control.open_activities(world, agent.agent_id)
        return LayerContext(
            self.context, agent.agent_id, names, bindings, current,
            state_override=state, loaded_target_keys=loaded_target_keys,
        )

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
    """@brief 比较前后层字典，按公开中文层键返回发生实质变化的精细子键差量。"""
    differs = {
        "environment_layer": _environment_changes,
        "memory_layer": _memory_changes,
        "todo_layer": _todo_changes,
        "progress_layer": _progress_changes,
        "tool_layer": _tool_changes,
    }
    changes: dict[str, Any] = {}
    for key, public_name in _LAYER_SPECS:
        if key not in FIVE_LAYER_KEYS or key not in after:
            continue
        prev = before.get(key)
        curr = after[key]
        if prev == curr or not isinstance(curr, Mapping):
            continue
        delta = differs[key](prev if isinstance(prev, Mapping) else {}, curr)
        if delta:
            changes[public_name] = delta
    return changes


def _environment_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 计算环境层子键差量；body 仅输出发生分级变化的体感子键。"""
    delta: dict[str, Any] = {}
    for key in ("observed_at", "body", "location", "weather", "duty", "tomorrow"):
        if key not in after or before.get(key) == after[key]:
            continue
        if key == "body" and isinstance(before.get("body"), Mapping) and isinstance(after["body"], Mapping):
            body_diff = {sub: deepcopy(val) for sub, val in after["body"].items()
                         if before["body"].get(sub) != val}
            if body_diff:
                delta["body"] = body_diff
        else:
            delta[key] = deepcopy(after[key])
    return delta


def _memory_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 计算记忆层子键差量：今天优先输出严格前缀追加或同日尾部覆写。"""
    delta: dict[str, Any] = {}
    for key in ("可查", "近三天"):
        if key in after and before.get(key) != after[key]:
            delta[key] = deepcopy(after[key])
    if "今天" in after and before.get("今天") != after["今天"]:
        prev, curr = before.get("今天"), after["今天"]
        if isinstance(prev, list) and isinstance(curr, list):
            if len(prev) < len(curr) and curr[:len(prev)] == prev:
                delta["今天_append"] = deepcopy(curr[len(prev):])
            else:
                prefix = _common_prefix_len(prev, curr)
                if prefix > 0:
                    delta["今天_tail"] = {"from_index": prefix, "entries": deepcopy(curr[prefix:])}
                else:
                    delta["今天"] = deepcopy(curr)
        else:
            delta["今天"] = deepcopy(curr)
    return delta


def _todo_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 计算待办层子键差量。"""
    return {
        key: deepcopy(after[key])
        for key in ("paused_activities", "errands", "orders", "day_plan")
        if key in after and before.get(key) != after[key]
    }


def _progress_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 计算进度层子键差量：events 与 rejected_today 在严格前缀追加时输出 _append。"""
    delta: dict[str, Any] = {}
    if "session_started_at" in after and before.get("session_started_at") != after["session_started_at"]:
        delta["session_started_at"] = deepcopy(after["session_started_at"])
    for key in ("events", "rejected_today"):
        if key not in after or before.get(key) == after[key]:
            continue
        prev, curr = before.get(key), after[key]
        if isinstance(prev, list) and isinstance(curr, list) and len(prev) < len(curr) and curr[:len(prev)] == prev:
            delta[f"{key}_append"] = deepcopy(curr[len(prev):])
        else:
            delta[key] = deepcopy(curr)
    return delta


def _tool_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    """@brief 计算工具层子键差量：gate、callable_index 与显式 compact 后的 group_deltas。"""
    delta: dict[str, Any] = {}
    if "gate" in after and before.get("gate") != after["gate"]:
        delta["gate"] = deepcopy(after["gate"])
    before_list = before.get("targets", ())
    after_list = after.get("targets", ())
    before_map = {(item["kind"], item["name"]): item for item in before_list}
    after_keys = {(item["kind"], item["name"]) for item in after_list}
    added = [compact(item) for item in after_list if (item["kind"], item["name"]) not in before_map]
    updated = [
        compact(item) for item in after_list
        if (k := (item["kind"], item["name"])) in before_map and before_map[k] != item
    ]
    removed = [
        {"kind": item["kind"], "name": item["name"]}
        for item in before_list if (item["kind"], item["name"]) not in after_keys
    ]
    group_deltas = {name: rows for name, rows in (("added", added), ("updated", updated), ("removed", removed)) if rows}
    if group_deltas:
        delta["group_deltas"] = group_deltas
    if "callable_index" in after and before.get("callable_index") != after["callable_index"]:
        delta["callable_index"] = deepcopy(after["callable_index"])
    return delta


def _common_prefix_len(left: Sequence[Any], right: Sequence[Any]) -> int:
    """@brief 计算两个序列的最大公共前缀长度。"""
    count = 0
    for a, b in zip(left, right):
        if a != b:
            break
        count += 1
    return count
