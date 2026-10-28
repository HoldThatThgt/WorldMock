"""@brief 唯一世界的公开层和私有名称、结算证据投影。"""

from copy import deepcopy
from dataclasses import replace

from worldmock.recording_engine.projections.activities.experiences import recent_verified_experiences
from worldmock.recording_engine.storage.observation_parts import split_observation
from worldmock.simulation_engine.activities.lifecycle import activity_control
from worldmock.simulation_engine.actions import admission
from worldmock.simulation_engine.observation.targets import callable_index, discovery
from worldmock.simulation_engine.scheduling.plans import errands
from worldmock.simulation_engine.body import felt
from worldmock.simulation_engine.observation.layers import memory_layer
from worldmock.simulation_engine.observation.layers import progress_layer, projection
from worldmock.simulation_engine.observation.targets import rotation
from worldmock.simulation_engine.activities.lifecycle import sessions
from worldmock.exchange_engine.conditions.activity_rules import ACTIVITY_ACTIONS
from worldmock.state.interaction.actions import binding_action
from worldmock.simulation_engine.activities.content.location_activities import location_units, unit_binding, unit_locked
from worldmock.simulation_engine.observation.targets.presentation import subtype_of, tiebreak_digest
from worldmock.simulation_engine.observation.layers.environment_layer import environment_layer
from worldmock.state.world.kinds import ActivityStatus
from worldmock.state.decisions.state import SameMomentPresentation
from worldmock.foundation.time.readable import local_date
from worldmock.simulation_engine.observation.layers.todo_layer import todo_layer, visible_goals
from worldmock.simulation_engine.observation.targets.tool_layer import (
    LayerContext,
    annotated_bindings,
    tool_layer,
    with_bindings,
)
from worldmock.resource_engine.catalog.travel_methods import travel_methods

#: 只给宿主生成提示、不写入观察事件的观察键（docs/design/05-recording-engine.md「事件与来源」）。
_HOST_ONLY_KEYS = frozenset({"progress_layer", "progress_meta"})


class ObservationBuilder:
    """@brief 观察只暂存曝光留痕，不创建第二份世界。"""

    def __init__(self, context):
        """@brief 绑定引擎唯一上下文。"""
        self.context = context
        self.pending_presentation: dict[int, SameMomentPresentation] = {}

    def _observation_data(self, agent) -> dict:
        """@brief 数值、身份和原始目录只放私有证据，五个公开层独立构建。"""
        catalog_bindings = self._catalog_bindings(agent)
        layer = self._layer_context(agent, catalog_bindings)
        products = discovery.product_bindings(layer)
        selected = self._product_window(agent, products)
        rows = [*catalog_bindings[:self._location_count()], *selected,
                *catalog_bindings[self._location_count():]]
        layer = with_bindings(layer, rows)
        layer = with_bindings(layer, annotated_bindings(layer))
        body = self._body(agent)
        memory = memory_layer.memory_view(layer, self.context.store)
        view = progress_layer.progress_view(layer, self.context.store, memory.facts)
        return {"agent_id": agent.agent_id, "current_time": self.context.state.current_time,
                "location_id": agent.location_id, "home_location_id": self.context.inputs.home_location_id,
                "body": body, "balance_fen": self.context.state.accounts[agent.balance_account_id],
                "interaction_bindings": layer.bindings, "catalog_bindings": catalog_bindings,
                "locations": [{"location_id": key, "name": row.display_name}
                              for key, row in self.context.catalog.locations.items()],
                "tool_layer": tool_layer(layer, callable_index.callable_index(layer)),
                "todo_layer": todo_layer(layer, self.context.store),
                "environment_layer": environment_layer(layer, body["levels"]),
                "memory_layer": memory.layer, "persona_views": memory_layer.persona_views(layer, self.context.store),
                "progress_layer": view.layer, "progress_meta": view.meta,
                "progress_event_ids": view.event_ids, "known_activity_actions": list(ACTIVITY_ACTIONS),
                **self._name_bindings(agent, layer.names, memory.note_labels), **self._evidence(agent)}

    def stored_observation(self, data: dict) -> tuple[dict, dict[str, str]]:
        """@brief 观察的存储形式与它的观察分件：去掉只给宿主的键，activity_bindings 按所指活动开始时刻（started_at）的
        场景本地日期分段；data 不变，宿主照旧用它生成提示。"""
        kept = {key: value for key, value in data.items() if key not in _HOST_ONLY_KEYS}
        activities, zone = self.context.state.activities, self.context.config.timezone
        dates = [local_date(activities[row["activity_id"]].started_at, zone)
                 for row in data["activity_bindings"]]
        return split_observation(kept, dates)

    def _location_count(self) -> int:
        """@brief 地点行完整呈现，不参加商品窗口轮转。"""
        return len(self.context.catalog.locations)

    def _catalog_bindings(self, agent) -> list[dict]:
        """@brief 地点全量绑定与能打开的 app：别处的地点每种出行方式一条动作行（步行在前，其余按身份升序）；
        人物停留的地点逐单元列出能点名的事，标了可睡时再列睡眠；人在路上时不列任何地点的做事与睡眠。
        每个单元都在私有绑定里，按本人此刻的回调状态写 locked：凭状态而未开放的为真，工具层跳过这样的行。

        能打开的 app 是登记了内容的内容 app，按 app_id 升序各一行 interact；没有登记内容、只用于下单和查询的
        内容 app 不在其中，只出现在工具层的 prefer_apps 里。"""
        locations = sorted(self.context.catalog.locations.values(), key=lambda row: row.location_id)
        locations.sort(key=lambda row: row.location_id == agent.location_id)
        stay = admission.present_location(self.context.state, agent.agent_id)
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

    def _product_window(self, agent, bindings: list[dict]) -> list[dict]:
        """@brief 商品曝光使用既有轮转算法，排序只读取自己实际留痕。"""
        config, state = self.context.config, self.context.state
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
        self.pending_presentation[agent.agent_id] = SameMomentPresentation(stamps=stamps)
        by_id = {row["goods_id"]: row for row in bindings}
        return [by_id[key] for key in selected]

    def _layer_context(self, agent, bindings: list[dict]) -> LayerContext:
        """@brief 活动与目标名称来自同一世界；本人的未了活动在这里算一次，整次观察共用。"""
        goals = [goal.goal for goal in visible_goals(self.context.state, agent.agent_id) or ()]
        taken = [*goals, *(row["object"] for row in bindings)]
        names = activity_control.visible_names(self.context.state, agent.agent_id, taken)
        current = activity_control.open_activities(self.context.state, agent.agent_id)
        return LayerContext(self.context, agent.agent_id, names, bindings, current)

    def _name_bindings(self, agent, names: dict, labels: dict) -> dict:
        """@brief 私有活动、目标与个人事务绑定供本地和环境共同校验。"""
        activities = self.context.state.activities
        return {"goal_bindings": [{"task_name": goal.goal, "intent_kind": goal.intent_kind}
                                  for goal in visible_goals(self.context.state, agent.agent_id) or ()],
                "activity_bindings": [{"name": name, "activity_id": key,
                    "title": None,
                    "phase": activity_control.activity_phase(activities[key]),
                    "resumable": activity_control.resumable_here(self.context.state, activities[key]),
                    "note_labels": [label for label, fulls in labels.items() if name in fulls]}
                    for key, name in names["activities"].items()],
                **errands.bindings(agent, self.context.catalog.goods)}

    def _evidence(self, agent) -> dict:
        """@brief 已核验经历、唤醒原因与进行中的活动只作为宿主证据。"""
        return {"verified_experiences": recent_verified_experiences(
                    self.context.store, agent.agent_id, self.context.store.last_event_seq),
                "wake_reasons": list(agent.pending_wake_reasons),
                "same_moment_stage": agent.same_moment_stage.value,
                "active_activities": [deepcopy(item.model_dump(mode="json"))
                    for item in self.context.state.activities.values()
                    if item.agent_id == agent.agent_id and item.status == ActivityStatus.ACTIVE]}

    def _body(self, agent, state=None) -> dict:
        """@brief 观察和决策参照都用同一四体感投影。"""
        state = self.context.state if state is None else state
        windows = felt.MealWindows(lunch=self.context.persona.lunch_window, dinner=self.context.persona.dinner_window)
        hour = projection.hour_of_day(state.current_time, self.context.config.timezone)
        return projection.body_projection(agent, hour, self.context.body, windows)

    def _snapshot_decision_references(self, staged, agent_ids: list[int]):
        """@brief 决策落定更新同源体感参照，单人世界不保存他人在场状态。"""
        agents = dict(staged.agents)
        for agent_id in agent_ids:
            agent = agents[agent_id]
            agents[agent_id] = agent.model_copy(update={"last_decision_time": staged.current_time,
                "felt_bands_at_last_decision": self._body(agent, staged)["levels"]}, deep=True)
        return staged.model_copy(update={"agents": agents}, deep=True)
