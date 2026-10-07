"""@brief LifeEngine 的观察投影与浏览/动机上下文装配。

本模块从 engine.py 下沉（生产文件 ≤2000 行规范）：观察的七层数据（工具层、待办层、环境层、进度层、记忆层与看法偏好，
docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第一章）、私有绑定与证据、事件级工具支持行、活动品类摘要、呈现窗口、内部动机仪表上下文
与决策参照快照。ObservationBuilder 只持有唯一上下文并承担观察暂存。
"""

from __future__ import annotations
from worldmock.life.world_query import attach_world_query
import logging
from worldmock.life.models import ActivityKind, ActivityStatus
from worldmock.life.readable import _datetime
from worldmock.life.models import ActivityKind

import logging
from copy import deepcopy
from dataclasses import replace
from typing import Any

from worldmock.life.engine_context import EngineContext
from worldmock.life.activity_titles import activity_title
from worldmock.life.world_ops import agent_config
from worldmock.footprints.experiences import recent_verified_experiences
from worldmock.life.admission import present_location
from worldmock.life import (
    activity_control,
    callable_index,
    discovery,
    errands,
    felt,
    memory_layer,
    presentation,
    progress_layer,
    projection,
    relevance,
    request_linking,
    sessions,
)
from worldmock.life.models import ActivityStatus, SameMomentPresentation, TravelMode
from worldmock.life.models import active_activities
from worldmock.life.interaction_menu import resolve_interaction_menu
from worldmock.life.recipients import person_bindings
from worldmock.life.presentation import PoolEntry, PresentationContext, PresentationWindow
from worldmock.life.candidates import Exclusion
from worldmock.life.content import visible_mail
from worldmock.life.todo_layer import todo_layer, visible_goals
from worldmock.life.tool_layer import LayerContext, annotated_bindings, tool_layer, with_bindings


class ObservationBuilder:
    """@brief 观察投影与仅供下次提交使用的呈现暂存。"""

    def __init__(self, context: EngineContext):
        """@brief 绑定唯一状态并独立创建观察暂存。"""
        self.context = context
        self.pending_presentation: dict[int, SameMomentPresentation] = {}

    def _observation_data(self, agent) -> dict:
        """@brief 一次观察：七层数据（工具层、待办层、环境层、进度层、记忆层）、私有绑定（含可见直接消息的 message_bindings，
        以及按名调用的目录绑定与住处）、证据与事件级工具支持行。

        商品呈现经统一候选契约产出，呈现后由 `_presentation_stamps` 留痕轮转判据；留痕按人物暂存，同一秒各轮沿用该秒首轮的
        呈现窗口与置顶的浏览结果。进度层、progress_meta 只供宿主当次使用（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「进度层」）。
        工具层另附只列名称的可调用索引（callable_index：生活活动窗口资格段保留的全部条目，此刻从这里能得到的
        商品，docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-只列名称的可调用索引 ）：它不是候选条目，不计入单次呈现上限，也不写呈现留痕。
        """
        config = agent_config(self.context.scenario, agent.agent_id)
        meal_windows = felt.MealWindows(lunch=config.lunch_window, dinner=config.dinner_window)
        rows = self._window_rows(agent)
        presented = rows["window"].presented
        self.pending_presentation[agent.agent_id] = SameMomentPresentation(
            stamps=_presentation_stamps(self.context.state, presented, self.context.semantics.catalog)
        )
        experiences = recent_verified_experiences(self.context.store, agent.agent_id, self.context.store.last_event_seq)
        inputs = projection.ProjectionInputs(
            state=self.context.state,
            scenario=self.context.scenario,
            agent=agent,
            meal_windows=meal_windows,
            extras=self._projection_extras(agent, rows["products"]),
        )
        data = projection.build_observation(inputs)
        context = self._layer_context(agent, data["interaction_bindings"])
        data["interaction_bindings"] = context.bindings
        data["home_location_id"] = config.home_location_id
        memory = memory_layer.memory_view(context, self.context.store)
        data.update(self._name_bindings(agent, context.names, memory.note_labels))
        index = callable_index.callable_index(context)
        data.update(self._layer_data(context, data["body"]["levels"], memory, index))
        data.update(self._observation_evidence(agent, experiences))
        data.update(self._event_tool_support(agent, rows["window"].presented))
        attach_world_query(data, context)
        return data

    def _layer_context(self, agent, bindings: list[dict]) -> LayerContext:
        """@brief 本次观察各层共用的只读上下文：活动名避开任务名、目标名（待办层的目标文字）与对象名，绑定动作的 note 取
        可用性原因。"""
        goals = [goal.goal for goal in visible_goals(self.context.state, agent.agent_id) or ()]
        taken = goals + [row["object"] for row in bindings]
        context = LayerContext(
            state=self.context.state,
            scenario=self.context.scenario,
            rules=self.context.rules,
            semantics=self.context.semantics,
            agent_id=agent.agent_id,
            names=activity_control.visible_names(self.context.state, agent.agent_id, taken),
            bindings=bindings,
            menu=resolve_interaction_menu(self.context.scenario, agent.agent_id, agent.location_id),
        )
        return with_bindings(context, annotated_bindings(context))

    def _layer_data(
        self, context: LayerContext, levels: dict[str, str], memory: memory_layer.MemoryView, index: dict
    ) -> dict:
        """@brief 环境侧构建的层数据与私有配套：工具层（附只列名称的可调用索引 index）、记忆层（memory，由已提交事件派生）
        与看法偏好，全文已呈现的消息含进度层与记忆层两处；answering 可关联的直接消息（message_bindings）只含进度层
        message_arrival 与记忆层中未回复的，按收到先后。"""
        from worldmock.life.environment_layer import environment_layer

        view = progress_layer.progress_view(context, self.context.store, memory.facts)
        presented = list(dict.fromkeys([*view.presented_message_ids, *memory.presented_message_ids]))
        answerable = [*memory.open_message_ids, *view.presented_message_ids]
        return {
            "message_bindings": request_linking.message_bindings(context, self.context.store, answerable),
            "tool_layer": tool_layer(context, index),
            "todo_layer": todo_layer(context, self.context.store),
            "environment_layer": environment_layer(context, levels),
            "memory_layer": memory.layer,
            "persona_views": memory_layer.persona_views(context, self.context.store),
            "progress_layer": view.layer,
            "progress_meta": view.meta,
            "progress_event_ids": view.event_ids,
            "presented_message_ids": presented,
        }

    def _name_bindings(
        self,
        agent,
        names: dict[str, dict[str, str]],
        note_labels: dict[str, tuple[str, ...]],
    ) -> dict:
        """@brief 本轮可读名称到内部身份的私有绑定：任务（名称同工具层任务目标）、当日目标（名称就是待办层写出的目标文字，
        不另拼前缀；两个目标文字相同时回看按重名处理）、活动与同世界其他人物（会话名不在七层中出现，不绑定）。

        活动绑定另带 resumable：暂停的活动此刻停留在其开始地点、可以恢复；note_labels：记忆层 self_notes 中这项活动去掉
        开始时刻后缀的标注（只作回看名称，两个条目的同一标注指不同活动时两项都带，回看按重名处理）。人物绑定不论是否同处
        （recipients.person_bindings）。个人事务另有两类绑定（errands.bindings：本人未完成事务与全部商品名），事务名不是
        回看名称（docs/dev/SPEC-life-runtime.md#errands-workday-个人事务-校验与错误 ）。
        """
        goals = [
            {"task_name": goal.goal, "intent_kind": goal.intent_kind}
            for goal in visible_goals(self.context.state, agent.agent_id) or ()
        ]
        activities = self.context.state.activities
        labels = {
            full: [label for (label, fulls) in note_labels.items() if full in fulls]
            for full in names["activities"].values()
        }
        return {
            "goal_bindings": goals,
            "activity_bindings": [
                {
                    "name": name,
                    "activity_id": activity_id,
                    "phase": activity_control.activity_phase(activities[activity_id]),
                    "resumable": activity_control.resumable_here(self.context.state, activities[activity_id]),
                    "note_labels": labels[name],
                }
                for (activity_id, name) in names["activities"].items()
            ],
            "person_bindings": person_bindings(self.context.state, agent.agent_id),
            **errands.bindings(agent, self.context.semantics.catalog.goods),
        }

    def _projection_extras(self, agent, product_rows: list[dict]) -> dict:
        """@brief 装配私有绑定的只读辅助输入：有效菜单（菜单外的能力不列出）、路线、可达地点、本轮呈现的食品与生活活动，以及活动控制行。"""
        return {
            "interaction_menu": resolve_interaction_menu(self.context.scenario, agent.agent_id, agent.location_id),
            "content_titles": self.context.rules.content_titles,
            "scenario_messages": self.context.rules.messages,
            "route_rows": self._route_rows(agent),
            "reachable_places": self._reachable_places(agent),
            "product_rows": product_rows,
            "activity_rows": self._activity_rows(agent),
            "actions": tuple(self.context.semantics.catalog.actions.values()),
        }


    def _observation_evidence(self, agent, experiences: list[dict]) -> dict:
        """@brief 观察的数值与身份证据段（不进提示，供事件、唤醒与宿主使用）。

        same_moment_stage 是同刻上限受限轮的阶段（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「实现约定」），受限只由
        工具层的 available 与 reason 表达；verified_experiences 是已核验经历的结构行，只作私有证据（记忆写入证据依赖它）。
        """
        return {
            "paused_activities": [
                _paused_activity_observation(item)
                for item in sorted(self.context.state.activities.values(), key=lambda value: value.activity_id)
                if item.agent_id == agent.agent_id and item.status == ActivityStatus.PAUSED
            ],
            "occupied_resources": sorted(
                {
                    claim
                    for item in active_activities(self.context.state, agent.agent_id)
                    for claim in item.resource_claims
                }
            ),
            "verified_experiences": experiences,
            "wake_reasons": list(agent.pending_wake_reasons),
            "same_moment_stage": agent.same_moment_stage.value,
            "location_id": agent.location_id,
            "hour_of_day": projection.hour_of_day(self.context.state.current_time, self.context.scenario.timezone),
        }


    def _window_rows(self, agent) -> dict:
        """@brief 固定菜单形成真实商品窗口，习惯相关度与轮转、哈希决胜共用候选排序。"""
        context = self._presentation_context(agent)
        entries = self._product_pool(agent)
        inputs = presentation.candidate_inputs(
            context, presentation.entry_sources(entries), moment=False,
            last_presented_at={entry.object_id: self._last_presented(agent.agent_id).get(
                entry.row["goods_id"], 0) for entry in entries},
            relevance=relevance.pool_relevance(entries, relevance.manifest_habit_texts(
                self.context.store.manifest, agent.agent_id)))
        window = presentation.present(entries, inputs, "product", None)
        return {"products": list(window.items), "window": window}





    def _event_tool_support(self, agent, presented: tuple[str, ...]) -> dict:
        """@brief 事件级工具支持与既有目录投影（私有证据，不进任何一层）。"""
        data: dict[str, Any] = {}
        menu = resolve_interaction_menu(self.context.scenario, agent.agent_id, agent.location_id)
        data["interaction_menu"] = menu.model_dump(mode="json")
        data["messages"] = visible_mail(agent, self.context.rules.messages)
        data["affordance_contract"] = self.context.affordance_contract
        context = self._discovery_context(agent)
        data["goods"] = [food.model_dump(mode="json") for food in self.context.semantics.catalog.goods.values()]
        data["menus"] = [entry.row for entry in _supply_entries(context)]
        data["activity_summary"] = self._activity_summary(agent)
        data["pending_deliveries"] = [
            item.model_dump(mode="json")
            for item in self.context.state.deliveries.values()
            if item.status in {"pending", "ready_for_pickup"}
            and (item.agent_id == agent.agent_id or item.recipient_id == f"agent:{agent.agent_id}")
        ]
        data["pending_alarms"] = [event.model_dump(mode="json") for event in self.context.state.pending_events
                                  if event.kind.value == "alarm_ring" and event.agent_id == agent.agent_id]
        data["routes"] = _route_catalog(
            self.context.scenario, self.context.state, agent.agent_id, self.context.semantics
        )
        catalog = self.context.semantics.catalog
        data["apps"] = [{"app_id": app.app_id, "name": app.name,
                         "purposes": list(app.purposes), "payment_app_ids": list(app.payment_app_ids)}
                        for app in catalog.apps.values()]
        data["actions"] = [item.model_dump(mode="json") for item in catalog.actions.values()]
        data["contents"] = [{"content_id": item.content_id, "action_id": item.action_id, "title": item.title}
                            for item in catalog.contents.values()]
        session = sessions.current_session(self.context.state, agent.agent_id)
        data["app_pushes"] = ({key: push.model_dump(mode="json") for key, push in session.app_pushes.items()}
                              if session is not None else {})
        data["calendar_context"] = _calendar_context(self.context.scenario, self.context.state.current_time)
        data["locations"] = _location_catalog(self.context.scenario)
        return data








    def _route_rows(self, agent) -> list[dict]:
        """@brief 地点目标的出行方式来自登记图，工具层使用相同行程计算路费、时长与费力。"""
        rows = _route_catalog(self.context.scenario, self.context.state, agent.agent_id, self.context.semantics)
        return [{"destination": row["destination_id"], "mode": row["mode"]} for row in rows]

    def _reachable_places(self, agent) -> list[dict]:
        """@brief 场景句列出登记连通图中场景所选的其他地点，只包含身份、名称和类型。"""
        destination_ids = set(self.context.semantics.catalog.graph.shortest_paths(agent.location_id))
        destination_ids.remove(agent.location_id)
        context = self._discovery_context(agent)
        return [
            discovery.place_row(context, location)
            for location in self.context.scenario.locations
            if location.location_id in destination_ids
        ]

    def _activity_rows(self, agent) -> dict:
        """@brief 活动控制行：进行中活动与待收尾活动各一组（观察证据 active_activities 与 pending_conclusions）。"""
        return {
            "control": [self._control_row(item) for item in active_activities(self.context.state, agent.agent_id)],
            "pending": [
                self._control_row(item)
                for item in activity_control.pending_conclusions(self.context.state, agent.agent_id)
            ],
        }

    def _control_row(self, item) -> dict:
        """@brief 一项活动的八键控制行：身份、种类、实际状态、标题、客观进度、已做与计划秒数、数据深拷贝。"""
        return {
            "activity_id": item.activity_id,
            "kind": item.kind.value,
            "status": item.status.value,
            "title": activity_title(item, self.context.titles),
            "progress": activity_control.objective_progress(item),
            "elapsed_seconds": item.elapsed_seconds,
            "planned_duration_seconds": item.planned_duration_seconds,
            "data": deepcopy(item.data),
        }



    def _visible_product(self, goods_id: str) -> dict:
        """@brief 食品事实原样读取唯一目录 Food。"""
        return self.context.semantics.catalog.goods[goods_id].model_dump(mode="json")

    def _discovery_context(self, agent) -> discovery.DiscoveryContext:
        """@brief 装配当前观察的地图事实与分时解释入口。"""
        return discovery.DiscoveryContext(self.context.state, self.context.scenario, self.context.semantics, agent)






    def _activity_summary(self, agent) -> dict[str, int]:
        """@brief 一行品类计数：五类可选对象各按生成段的池规模报数，明细由下钻读取。

        计数与下钻同源：键闭集取同一组候选池（内容按品类、生活活动按品类、商品归
        `foods`、游戏与工作各占一键），因此模型看到的键一定可下钻。
        """
        pools = (self._product_pool(agent),)
        summary: dict[str, int] = {}
        for pool in pools:
            for entry in pool:
                summary[entry.category] = summary.get(entry.category, 0) + 1
        return dict(sorted(summary.items()))



    def _presentation_context(self, agent) -> PresentationContext:
        """@brief 装配一次呈现所需的只读世界切片。"""
        return PresentationContext(
            state=self.context.state, scenario=self.context.scenario, semantics=self.context.semantics, agent=agent
        )

    def _last_presented(self, agent_id: int) -> dict[str, int]:
        """@brief 该人物各对象的上次呈现时刻（轮转的唯一判据，逐对象记录，不含任何偏好推断；不设共享偏移量或分区
        游标，docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「曝光轮转」）。"""
        return dict(self.context.state.presentation_state.get(agent_id, {}))

    def _product_pool(self, agent) -> tuple[PoolEntry, ...]:
        """@brief 已选门店的固定食品菜单，没有浏览凭据或旧库存状态。"""
        return _supply_entries(self._discovery_context(agent))





    def _snapshot_decision_references(self, staged, agent_ids: list[int]):
        """@brief 决策落定后更新滞回参照：分级/在场人物/已知任务（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「滞回规则」）
        。"""
        agents = dict(staged.agents)
        for agent_id in agent_ids:
            agent = agents[agent_id]
            hour = projection.hour_of_day(staged.current_time, self.context.scenario.timezone)
            config = agent_config(self.context.scenario, agent_id)
            windows = felt.MealWindows(lunch=config.lunch_window, dinner=config.dinner_window)
            levels = projection.body_projection(agent, hour, self.context.semantics.body, windows)["levels"]
            people = frozenset((item["name"] for item in projection.people_here_views(staged, agent)))
            agents[agent_id] = agent.model_copy(
                update={
                    "last_decision_time": staged.current_time,
                    "felt_bands_at_last_decision": levels,
                    "people_at_last_decision": people,
                },
                deep=True,
            )
        return staged.model_copy(update={"agents": agents}, deep=True)


def _route_catalog(scenario, state, agent_id: int, semantics) -> list[dict]:
    """@brief 返回当前地点到场景所选地点的完整图行程，各方式均使用固定计时计费。"""
    agent = state.agents[agent_id]
    context = discovery.DiscoveryContext(state, scenario, semantics, agent)
    destinations = sorted(item.location_id for item in scenario.locations if item.location_id != agent.location_id)
    return [discovery.route_row(context, destination, mode.value)
            for destination in destinations for mode in TravelMode]


def _paused_activity_observation(activity) -> dict:
    """@brief 暂停活动的完整记录与剩余墙钟时长；恢复不查能量（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动「删除的机制」）。"""
    data = activity.model_dump(mode="json")
    data["remaining_duration_seconds"] = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
    return data


def _calendar_context(scenario, epoch: int) -> dict:
    """@brief 返回当前本地日期已声明的日历机会与约束。"""

    current_date = _datetime(epoch, scenario.timezone).date().isoformat()
    calendar = scenario.calendar
    return {
        "date": current_date,
        "spring_festival": current_date in calendar.spring_festival_dates,
        "makeup_workday": current_date in calendar.makeup_work_dates,
        "overtime_opportunity": current_date in calendar.overtime_opportunity_dates,
        "sick_leave": current_date == calendar.sick_leave_date,
        "personal_leave": current_date == calendar.personal_leave_date,
        "social_dinner_opportunity": current_date == calendar.social_dinner_date,
    }


def _location_catalog(scenario) -> list[dict]:
    """@brief 事件级地点目录只保存登记地点的身份、名称与类型。"""
    return [{"location_id": item.location_id, "name": item.display_name, "kind": item.kind.value}
            for item in scenario.locations]






#: 浏览动作的目录对象身份闭集（交互对象层的 catalog 行与事件级支持行同源）。


#: 目录浏览完成后的提示句（说明窗口里的身份还要经购买时复核）。





def _supply_entries(context: discovery.DiscoveryContext) -> tuple[PoolEntry, ...]:
    """@brief 商品呈现与完整查询共享同一商品集合。"""
    return tuple(PoolEntry(row["goods_id"], "product", row, category=row["category"])
                 for row in discovery.product_rows(context))

def _presentation_stamps(state, presented: tuple[str, ...], catalog) -> dict[str, int]:
    """@brief 计算本次呈现的留痕：本人实际进入窗口的对象标识 → 当前时刻。

    轮转的判据是「上次呈现时刻」，所以每次呈现都要留痕；留痕是新的派生量取值，但**不
    允许在观察边界直接落盘**——`store._validate_state` 只允许同版本追加事件，因此引擎
    把结果按人物暂存，决策轮提交时写入世界状态的本秒暂存，时钟推进时才并入 `presentation_state`。
    这里不改目录、不改对象状态、不产生下架事件（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则「曝光轮转」
    「禁止永久退役」）。

    @param state 当前世界状态。
    @param presented 本轮实际进入窗口的对象身份（可跨多个分区窗口拼接）。
    @return 对象标识 → 当前时刻；本次没有呈现任何对象时为空字典。
    """
    goods_ids = {f"{store_id}:{goods_id}": goods_id for store_id, goods in catalog.goods_by_store.items()
                for goods_id in goods}
    return {goods_ids[object_id] if object_id in goods_ids else object_id: int(state.current_time)
            for object_id in presented}
