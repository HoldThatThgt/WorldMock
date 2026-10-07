"""@brief LifeEngine 的观察投影与浏览/动机上下文装配。

本模块从 engine.py 下沉（生产文件 ≤2000 行规范）：观察的七层数据（工具层、待办层、环境层、进度层、记忆层与看法偏好，
docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第一章）、私有绑定与证据、事件级工具支持行、活动品类摘要、呈现窗口、内部动机仪表上下文
与决策参照快照。ObservationBuilder 只持有唯一上下文并承担观察暂存。
"""

from __future__ import annotations
from worldmock.life.world_query import attach_world_query
import logging
from worldmock.life.models import ActivityKind, ActivityStatus
from worldmock.life.models import LIFESTYLE_TYPE_PREFIX
from worldmock.life.readable import _datetime
from math import ceil
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
    named_catalog,
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
from worldmock.life.work import work_totals
from worldmock.life.content import mail_headers
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
        以及按名调用的目录绑定与住处，named_catalog）、证据与事件级工具支持行。

        商品呈现经统一候选契约产出，呈现后由 `_presentation_stamps` 留痕轮转判据；留痕按人物暂存，同一秒各轮沿用该秒首轮的
        呈现窗口与置顶的浏览结果。进度层、progress_meta 只供宿主当次使用（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求「进度层」）。
        工具层另附只列名称的可调用索引（callable_index：生活活动窗口资格段保留的全部条目，此刻从这里能得到的
        商品，docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-只列名称的可调用索引 ）：它不是候选条目，不计入单次呈现上限，也不写呈现留痕。
        """
        config = agent_config(self.context.scenario, agent.agent_id)
        meal_windows = felt.MealWindows(lunch=config.lunch_window, dinner=config.dinner_window)
        rows = self._window_rows(agent)
        menu = self._menu_windows(agent)
        presented = rows["window"].presented + tuple(
            (identity for window in menu.values() for identity in window.presented)
        )
        self.pending_presentation[agent.agent_id] = SameMomentPresentation(
            stamps=_presentation_stamps(self.context.state, presented, self.context.semantics.catalog)
        )
        task_rows = tuple(self._visible_task_rows(agent))
        experiences = recent_verified_experiences(self.context.store, agent.agent_id, self.context.store.last_event_seq)
        inputs = projection.ProjectionInputs(
            state=self.context.state,
            scenario=self.context.scenario,
            agent=agent,
            task_rows=task_rows,
            meal_windows=meal_windows,
            extras=self._projection_extras(agent, rows["products"], menu),
        )
        data = projection.build_observation(inputs)
        context = self._layer_context(agent, task_rows, data["interaction_bindings"])
        data["interaction_bindings"] = context.bindings
        data.update(named_catalog.name_bindings(context, self._enabled_lifestyle_items(agent), config.home_location_id))
        memory = memory_layer.memory_view(context, self.context.store)
        data.update(self._name_bindings(agent, task_rows, context.names, memory.note_labels))
        index = callable_index.callable_index(context, callable_index.index_sources(menu))
        data.update(self._layer_data(context, data["body"]["levels"], memory, index))
        data.update(self._observation_evidence(agent, experiences))
        data.update(self._event_tool_support(agent, rows["window"].presented))
        attach_world_query(data, context)
        return data

    def _layer_context(self, agent, task_rows: tuple[dict, ...], bindings: list[dict]) -> LayerContext:
        """@brief 本次观察各层共用的只读上下文：活动名避开任务名、目标名（待办层的目标文字）与对象名，绑定动作的 note 取
        可用性原因。"""
        goals = [goal.goal for goal in visible_goals(self.context.state, agent.agent_id) or ()]
        taken = [f"工作：{row['topic']}" for row in task_rows] + goals + [row["object"] for row in bindings]
        context = LayerContext(
            state=self.context.state,
            scenario=self.context.scenario,
            rules=self.context.rules,
            semantics=self.context.semantics,
            agent_id=agent.agent_id,
            names=activity_control.visible_names(self.context.state, agent.agent_id, taken),
            bindings=bindings,
            menu=resolve_interaction_menu(self.context.scenario, agent.agent_id, agent.location_id),
            lifestyle_items=self.context.lifestyle_items,
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
        task_rows: tuple[dict, ...],
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
            "task_bindings": [{"task_name": f"工作：{row['topic']}", "topic_id": row["topic_id"]} for row in task_rows],
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
            **errands.bindings(agent, self.context.semantics.catalog.foods),
        }

    def _projection_extras(self, agent, product_rows: list[dict], menu: dict[str, PresentationWindow]) -> dict:
        """@brief 装配私有绑定的只读辅助输入：有效菜单（菜单外的能力不列出）、路线、可达地点、本轮呈现的食品与生活活动，以及活动控制行。"""
        return {
            "interaction_menu": resolve_interaction_menu(self.context.scenario, agent.agent_id, agent.location_id),
            "content_titles": self.context.rules.content_titles,
            "scenario_messages": self.context.rules.messages,
            "route_rows": self._route_rows(agent),
            "reachable_places": self._reachable_places(agent),
            "product_rows": product_rows,
            "activity_rows": self._activity_rows(agent),
            "lifestyle_rows": list(menu["lifestyle"].items),
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
        """@brief 固定菜单一次形成真实食品呈现窗口，候选排序不读取口味或报价。"""
        context = self._presentation_context(agent)
        entries = self._product_pool(agent)
        inputs = presentation.candidate_inputs(
            context, presentation.entry_sources(entries), moment=False,
            last_presented_at={entry.object_id: self._last_presented(agent.agent_id).get(
                entry.row["food_id"], 0) for entry in entries})
        window = presentation.present(entries, inputs, "product", None)
        return {"products": list(window.items), "window": window}

    def _menu_windows(self, agent) -> dict[str, PresentationWindow]:
        """@brief 内容、游戏与生活活动三类对象的本轮呈现窗口（交互对象层来源）。

        这三类没有在场与库存这样的当下事实，可见性由曝光轮转决定；它们与商品窗口
        共用同一候选契约，只有实际进入窗口的身份才更新呈现凭据（docs/dev/SPEC-life-runtime.md#spec-life-runtime-特性规格lifeenv-运行时行为与结算规则
        「曝光轮转」）。生活活动窗口另按人物当前地点施加地点类资格（只此一项，此刻不作排除：目录时间窗覆盖此刻只进
        相关度）：地点类不符的条目不进窗口，排除项记 ELIGIBILITY_LOCATION_KIND_MISMATCH（世界事实，不是软偏好；
        docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ）。各分区的排序带相关度（此刻、当日目标与习惯签名），轮转各按本分区对象的
        上次呈现时刻。
        旧的目录下钻工具已随工具面收敛删除，因此窗口是这三类对象成为工具层目标的唯一通道；工具层的可调用索引只列名称，
        取同一窗口资格段保留的全部条目（presented 与 deferred，life.callable_index），不是另一条呈现链路。
        """
        context = self._presentation_context(agent)
        last = self._last_presented(agent.agent_id)
        facts = self._relevance_facts(self.context.state, agent)
        windows: dict[str, PresentationWindow] = {}
        for kind, entries in (
            ("lifestyle", self._lifestyle_pool(agent)),
        ):
            inputs = presentation.candidate_inputs(
                context,
                presentation.entry_sources(entries),
                moment=False,
                last_presented_at=last,
                relevance=self._relevance(entries, facts),
            )
            if kind == "lifestyle":
                inputs = replace(inputs, apply_location_kinds=True)
            windows[kind] = presentation.present(entries, inputs, kind, None)
        return windows

    def _relevance(self, entries: tuple[PoolEntry, ...], facts: relevance.RelevanceFacts) -> dict[str, int]:
        """@brief 池内条目的相关度（docs/dev/SPEC-interaction-menu.md#dayrun-fidelity-呈现与浏览-生活活动的地点类资格与排序 ）：此刻落在目录时间窗内、属于当日未推进目标的
        活动家族、人设层习惯签名的反应文本命中条目名或品类；时间窗取生活活动目录的声明。"""
        windows = {
            item.activity_id: item.time_window for item in self.context.lifestyle_items if item.time_window is not None
        }
        return relevance.pool_relevance(entries, facts, windows)

    def _relevance_facts(self, state, agent) -> relevance.RelevanceFacts:
        """@brief 一次取候选的相关度原料：场景时区的本地时刻、该秒开始时当日未推进目标的活动家族、运行清单冻结的
        习惯签名反应文本（persona_habit_responses）。"""
        return relevance.RelevanceFacts(
            hour=projection.hour_of_day(state.current_time, self.context.scenario.timezone),
            goal_families=self._second_start_families(state, agent.agent_id),
            habit_texts=relevance.manifest_habit_texts(self.context.store.manifest, agent.agent_id),
        )

    def _second_start_families(self, state, agent_id: int) -> frozenset[str]:
        """@brief 该秒开始时当日未推进目标的活动家族。

        同一秒各轮沿用该秒首轮的呈现窗口（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第一章「同一秒内的呈现与「刚才」」），
        相关度同轮转判据一样取该秒开始时的值：本秒已有本人观察时，取其中最早一次待办层写出的今日计划（意图与是否已推进），
        本秒内修订计划或推进目标从下一秒起才进入排序；本秒首次观察取当前计划。
        """
        after = self.context.store.last_event_seq_before(state.current_time)
        first = next(iter(self.context.store.read_agent_events("observation", agent_id, after)), None)
        if first is None:
            return relevance.goal_families(visible_goals(state, agent_id))
        plan = first.data["todo_layer"]["day_plan"] or {"goals": ()}
        return relevance.intent_families((row["intent_kind"] for row in plan["goals"] if not row["progressed"]))

    def _event_tool_support(self, agent, presented: tuple[str, ...]) -> dict:
        """@brief 事件级工具支持与既有目录投影（私有证据，不进任何一层）。"""
        data: dict[str, Any] = {}
        menu = resolve_interaction_menu(self.context.scenario, agent.agent_id, agent.location_id)
        data["interaction_menu"] = menu.model_dump(mode="json")
        data["messages"] = mail_headers(agent, self.context.rules.messages)
        data["affordance_contract"] = self.context.affordance_contract
        context = self._discovery_context(agent)
        data["foods"] = [food.model_dump(mode="json") for food in self.context.semantics.catalog.foods.values()]
        data["menus"] = [entry.row for entry in _supply_entries(context)]
        data["activity_summary"] = self._activity_summary(agent)
        data["pending_deliveries"] = [
            item.model_dump(mode="json")
            for item in self.context.state.deliveries.values()
            if item.agent_id == agent.agent_id and item.status == "pending"
        ]
        data["inventory"] = agent.inventory
        data["available_inventory"] = agent.inventory.get(agent.location_id, {})
        data["held_foods"] = self._inventory_item_contracts(agent)
        data["work_topics"] = self._work_topic_rows(agent)
        data["routes"] = _route_catalog(
            self.context.scenario, self.context.state, agent.agent_id, self.context.semantics
        )
        catalog = self.context.semantics.catalog
        data["apps"] = [{"app_id": app.app_id, "name": app.name,
                         "content_count": len(catalog.contents_by_app[app.app_id])}
                        for app in catalog.apps.values()]
        data["contents"] = [{"content_id": item.content_id, "title": item.title}
                            for item in catalog.contents.values()]
        session = sessions.current_session(self.context.state, agent.agent_id)
        data["app_pushes"] = ({key: push.model_dump(mode="json") for key, push in session.app_pushes.items()}
                              if session is not None else {})
        data["calendar_context"] = _calendar_context(self.context.scenario, self.context.state.current_time)
        data["locations"] = _location_catalog(self.context.scenario)
        return data




    def _work_topic_rows(self, agent) -> list[dict]:
        """@brief 私有证据列出本人所有已分配主题与实际累计工时，不复制任务状态。"""
        config = agent_config(self.context.scenario, agent.agent_id)
        return [{"topic_id": topic_id, "topic": self.context.rules.work_topics[topic_id].topic,
                 "target_seconds": self.context.rules.work_topics[topic_id].target_seconds,
                 "worked_seconds": work_totals(self.context.state, agent.agent_id, topic_id)}
                for topic_id in sorted(config.work_topic_ids)]

    def _visible_task_rows(self, agent) -> list[dict]:
        """@brief 工具层从运行开始列出已分配且尚未完成的工作主题。"""
        return [row for row in self._work_topic_rows(agent) if row["worked_seconds"] < row["target_seconds"]]


    def _work_topic_pool(self, agent) -> tuple[PoolEntry, ...]:
        """@brief 品类摘要由尚未完成主题派生，工作不进入曝光轮转。"""
        config = agent_config(self.context.scenario, agent.agent_id)
        workplace = config.work_schedule.workplace_location_id if config.work_schedule is not None else None
        return tuple(PoolEntry(object_id=row["topic_id"], kind="task", minimum_seconds=60,
                               maximum_seconds=row["target_seconds"], required_location_id=workplace,
                               row=row, category=presentation.WORK_TOPIC_CATEGORY)
                     for row in self._visible_task_rows(agent))

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



    def _visible_product(self, food_id: str) -> dict:
        """@brief 食品事实原样读取唯一目录 Food。"""
        return self.context.semantics.catalog.foods[food_id].model_dump(mode="json")

    def _discovery_context(self, agent) -> discovery.DiscoveryContext:
        """@brief 装配当前观察的地图事实与分时解释入口。"""
        return discovery.DiscoveryContext(self.context.state, self.context.scenario, self.context.semantics, agent)

    def _inventory_item_contracts(self, agent) -> list[dict]:
        """@brief 当前地点实际持有的目录食品，不制造耐用品使用合同。"""
        return [self._visible_product(food_id) for food_id, quantity in sorted(
            agent.inventory.get(agent.location_id, {}).items()) if quantity > 0]





    def _activity_summary(self, agent) -> dict[str, int]:
        """@brief 一行品类计数：五类可选对象各按生成段的池规模报数，明细由下钻读取。

        计数与下钻同源：键闭集取同一组候选池（内容按品类、生活活动按品类、商品归
        `foods`、游戏与工作各占一键），因此模型看到的键一定可下钻。
        """
        pools = (
            self._lifestyle_pool(agent),
            self._product_pool(agent),
            self._work_topic_pool(agent),
        )
        summary: dict[str, int] = {}
        for pool in pools:
            for entry in pool:
                summary[entry.category] = summary.get(entry.category, 0) + 1
        return dict(sorted(summary.items()))

    def _enabled_lifestyle_items(self, agent) -> tuple:
        """@brief 返回人物启用声明过滤后的生活活动目录条目（None=全局启用）。"""
        config = next((item for item in self.context.scenario.agents if item.agent_id == agent.agent_id))
        enabled = config.lifestyle_activities
        if enabled is None:
            return self.context.lifestyle_items
        allowed = set(enabled)
        return tuple((item for item in self.context.lifestyle_items if item.activity_id in allowed))


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


    def _lifestyle_pool(self, agent) -> tuple[PoolEntry, ...]:
        """@brief 生活活动池：只含本人启用集内的目录条目（启用集是世界事实）。"""
        return tuple(
            (
                PoolEntry(
                    object_id=entry.activity_id,
                    kind="lifestyle",
                    minimum_seconds=60,
                    maximum_seconds=entry.typical_duration_minutes * 60,
                    location_kinds=self.context.rules.activity_location_kinds[entry.activity_id],
                    met=entry.met_value,
                    row={
                        "activity_id": entry.activity_id,
                        "activity_type": f"{LIFESTYLE_TYPE_PREFIX}{entry.activity_id}",
                        "name": entry.name,
                        "category": entry.category,
                        "typical_duration_minutes": entry.typical_duration_minutes,
                        "description": entry.description,
                        "met_value": entry.met_value,
                    },
                    category=entry.category,
                )
                for entry in sorted(self._enabled_lifestyle_items(agent), key=lambda item: item.activity_id)
            )
        )



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






def _recent_meal_counts(activities, agent_id: int, now: int) -> dict[str, int]:
    """@brief 从近一天实际结算的食用量推导商品重复进食次数。"""

    counts: dict[str, int] = {}
    for activity in activities:
        if activity.agent_id != agent_id or activity.kind != ActivityKind.CONSUME:
            continue
        if not 0 <= now - activity.started_at <= 86_400:
            continue
        if activity.data["request"]["use_mode"] != "eat":
            continue
        for product_id, consumed in activity.data["consumed"].items():
            if consumed > 0:
                counts[product_id] = counts.get(product_id, 0) + ceil(consumed)
    return counts




#: 浏览动作的目录对象身份闭集（交互对象层的 catalog 行与事件级支持行同源）。


#: 目录浏览完成后的提示句（说明窗口里的身份还要经购买时复核）。





def _supply_entries(context: discovery.DiscoveryContext) -> tuple[PoolEntry, ...]:
    """@brief 把真实固定菜单装成实际食品候选，食品价格只来自 Catalog。"""
    catalog = context.semantics.catalog
    selected = {location.location_id for location in context.scenario.locations}
    present = present_location(context.state, context.agent.agent_id)
    return tuple(PoolEntry(object_id=f"{store_id}:{food_id}", kind="product",
                           row=discovery.supply_row(context, store_id, catalog.foods[food_id]),
                           category=catalog.foods[food_id].category.value)
                 for store_id, foods in catalog.foods_by_store.items()
                 if store_id in selected and present is not None
                 and (store_id == present or catalog.apps_by_store[store_id]) for food_id in foods)




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
    food_ids = {f"{store_id}:{food_id}": food_id for store_id, foods in catalog.foods_by_store.items()
                for food_id in foods}
    return {food_ids[object_id] if object_id in food_ids else object_id: int(state.current_time)
            for object_id in presented}
