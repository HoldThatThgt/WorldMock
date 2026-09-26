"""@brief LifeEngine 的观察投影与浏览/动机上下文装配（Mixin 层）。

本模块从 engine.py 下沉（生产文件 ≤2000 行规范）：包含两层观察组装
（scene + facts + 事件级工具支持行）、活动品类摘要、呈现窗口、
内部动机仪表上下文与决策参照快照。engine 侧以 Mixin 继承接线，
`self` 即 LifeEngine；行为合同与注释见修订合同观察投影一节（docs/dev/SPEC-life-runtime.md「观察投影与提示合同」）。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import replace
from typing import Any

from worldmock.footprints.experiences import recent_verified_experiences
from worldmock.life import activity_control, discovery, felt, presentation, projection
from worldmock.life.models import ActivityStatus, ContentProgress, SameMomentPresentation
from worldmock.life.motivation import MotivationContext, evaluate_motivation
from worldmock.life.basket import delivery_expected_at
from worldmock.life.models import ActivityKind, active_activities, paused_activities
from worldmock.life.interaction_menu import resolve_interaction_menu
from worldmock.life.inventory_recipe import is_inventory_recipe
from worldmock.life.presentation import PoolEntry, PresentationContext, PresentationWindow
from worldmock.life.candidates import Exclusion
from worldmock.life.work import work_totals
from worldmock.life.result_projection import public_supply_row
from worldmock.life.dining import local_meal_sentence


class ObservationMixin:
    """@brief 观察投影、浏览与动机仪表的引擎装配方法（self 为 LifeEngine）。"""

    def _observation_data(self, agent) -> dict:
        """@brief 两层观察投影：scene 场景输入 + facts 精简结构 + body 数值证据。

        黑名单字段不进 scene/facts 提示路径；事件级工具支持行（完整任务/路线/
        商品、可供性合同、游戏目录）供宿主工具 Schema 使用。商品
        事实清单经统一候选契约产出，呈现后由 `_presentation_stamps` 留痕轮转判据；留痕按人物暂存，
        同一秒各轮沿用该秒首轮的呈现窗口与置顶的浏览结果（docs/dev/SPEC-time-and-energy.md 第一章
        「同一秒内的呈现与「刚才」」）。
        """
        config = self._agent_config(agent.agent_id)
        meal_windows = felt.MealWindows(lunch=config.lunch_window, dinner=config.dinner_window)
        recent = self._recent_action_results(agent.agent_id)
        pinned = self._pinned_supply_ids(agent.agent_id, recent)
        rows = self._window_rows(agent, pinned)
        menu = self._menu_windows(agent)
        presented = rows["window"].presented + tuple(
            identity for window in menu.values() for identity in window.presented)
        self._pending_presentation[agent.agent_id] = SameMomentPresentation(
            stamps=_presentation_stamps(self.state, presented), pinned_supply_ids=pinned)
        self._pending_visible_supplies[agent.agent_id] = set(rows["window"].presented)
        task_rows = tuple(self._visible_task_rows(agent))
        bindings = self._name_bindings(agent, task_rows)
        experiences = recent_verified_experiences(self.store, agent.agent_id, self.store.last_event_seq)
        context = {"visible_names": bindings.pop("names"), "verified_experiences": experiences,
                   "previous_request_result": self._previous_request_result(agent, recent)}
        inputs = projection.ProjectionInputs(
            state=self.state, scenario=self.scenario, agent=agent,
            recent_results=tuple(recent),
            task_rows=task_rows,
            meal_windows=meal_windows,
            motivation_internal=self._motivation_internal(agent),
            extras=self._projection_extras(agent, rows["products"], menu, context))
        data = projection.build_observation(inputs)
        data.update(bindings)
        data.update(self._observation_evidence(agent, experiences))
        data.update(self._event_tool_support(agent, recent, rows["window"].presented))
        return data

    def _name_bindings(self, agent, task_rows: tuple[dict, ...]) -> dict:
        """@brief 本轮可读名称到内部身份的私有绑定：任务、当日目标、活动与会话（活动与会话名重名时追加序号）。

        活动绑定另带 resumable：暂停的活动此刻停留在其开始地点、可以恢复（受限轮的恢复枚举只列这些活动）。
        """
        goals = [{"task_name": f"目标：{goal.intent_kind}：{goal.goal}", "intent_kind": goal.intent_kind}
                 for goal in agent.day_plan.goals] if agent.day_plan is not None else []
        names = activity_control.visible_names(
            self.state, agent.agent_id, [row["title"] for row in task_rows] + [row["task_name"] for row in goals])
        activities = self.state.activities
        return {
            "names": names,
            "task_bindings": [{"task_name": row["title"], "task_id": row["task_id"],
                               "submission_ready": row["submission_ready"]} for row in task_rows],
            "goal_bindings": goals,
            "activity_bindings": [{"name": name, "activity_id": activity_id,
                                   "phase": activity_control.activity_phase(activities[activity_id]),
                                   "resumable": activity_control.resumable_here(self.state, activities[activity_id])}
                                  for activity_id, name in names["activities"].items()],
            "session_bindings": [{"name": name, "session_id": session_id}
                                 for session_id, name in names["sessions"].items()],
        }

    def _projection_extras(self, agent, product_rows: list[dict],
                           menu: dict[str, PresentationWindow], context: dict) -> dict:
        """@brief 装配两层投影的只读辅助输入（交互对象层与场景句的行来源；context 为可见名称与已核验经历）。

        吃饭途径句要等投影得出饥饿分级后才生成，这里只备好它引用的本轮事实：原当地餐食句、
        配送预计送达时刻与浏览是否在有效菜单内（docs/dev/SPEC-time-and-energy.md「让人物趁早吃」）。
        交互对象层另取有效菜单（菜单外的能力不列出）、手边持有物中的库存转换配方（列「制作」）与同刻轮次
        上一轮本人世界请求的结果（「刚才」）。
        """
        interaction_menu = resolve_interaction_menu(self.scenario, agent.agent_id, agent.location_id)
        return {
            **context,
            "interaction_menu": interaction_menu,
            "recipe_product_ids": self._recipe_product_ids(agent),
            "content_titles": self.rules.content_titles,
            "scenario_messages": self.rules.messages,
            "route_rows": self._route_rows(agent),
            "reachable_places": self._reachable_places(agent),
            "product_rows": product_rows,
            "activity_rows": self._activity_rows(agent),
            "local_meal_sentence": local_meal_sentence(self._discovery_context(agent)),
            "delivery_expected_at": self._delivery_expected_at(agent, product_rows),
            "browse_allowed": "browse" in interaction_menu.allowed_actions,
            "content_rows": list(menu["content"].items),
            "game_rows": list(menu["game"].items),
            "lifestyle_rows": list(menu["lifestyle"].items),
            "lifestyle_location_kinds": self.rules.activity_location_kinds,
            "browse_targets": BROWSE_TARGETS,
        }

    def _recipe_product_ids(self, agent) -> frozenset[str]:
        """@brief 人物各地持有物中 consume:use 规则是库存转换配方的物品（交互对象层据此列 obtain 的「制作」）。"""
        held = {product_id for holding in agent.inventory.values() for product_id, quantity in holding.items()
                if quantity > 0 and product_id in self.rules.items}
        return frozenset(product_id for product_id in held
                         if is_inventory_recipe(self.semantics.catalog, self.rules.items[product_id]))

    def _previous_request_result(self, agent, recent: list[dict]) -> dict | None:
        """@brief 同刻轮次上一轮本人世界请求的结算结果（「刚才」的来源）；每秒首轮或本秒没有请求时为 None。"""
        if agent.same_moment_rounds == 0:
            return None
        cutoff = self.store.last_agent_progress_request_seq(agent.agent_id)
        requests = [event for event in self.store.read_agent_events("action_requested", agent.agent_id, after=cutoff)
                    if event.occurred_at == self.state.current_time]
        if not requests:
            return None
        last = requests[-1]
        return next((row for row in recent
                     if row["action_id"] == last.action_id and row["decision_id"] == last.decision_id), None)

    def _delivery_expected_at(self, agent, product_rows: list[dict]) -> dict[str, int | None]:
        """@brief 本轮呈现的可配送供给送到本人所在地点的预计送达时刻（与购买结算同一算法，送不到为 None）。"""
        supplies = {item.supply_id: item for item in self.scenario.supplies}
        return {row["supply_id"]: delivery_expected_at(self.semantics, supplies[row["supply_id"]],
                                                       agent.location_id, self.state.current_time)
                for row in product_rows if "delivery" in row["fulfillment_modes"]}

    def _observation_evidence(self, agent, experiences: list[dict]) -> dict:
        """@brief 观察的数值与身份证据段（不进提示，供事件、唤醒与宿主使用）。

        same_moment_stage 是同刻上限受限轮的阶段（docs/dev/SPEC-time-and-energy.md 第一章「实现约定」），
        宿主据此收窄世界请求工具与名称枚举；它不进 scene 与 facts。verified_experiences 是已核验经历的结构行，
        仍是私有证据；模型只见 facts.experiences 中由同一窗口写成的句子。
        """
        return {
            "paused_activities": [
                _paused_activity_observation(item) for item in sorted(
                    self.state.activities.values(), key=lambda value: value.activity_id)
                if item.agent_id == agent.agent_id and item.status == ActivityStatus.PAUSED],
            "occupied_resources": sorted({claim for item in active_activities(self.state, agent.agent_id)
                                          for claim in item.resource_claims}),
            "verified_experiences": experiences,
            "wake_reasons": list(agent.pending_wake_reasons),
            "same_moment_stage": agent.same_moment_stage.value,
            "location_id": agent.location_id,
            "hour_of_day": projection.hour_of_day(self.state.current_time, self.scenario.timezone),
        }

    def _pinned_supply_ids(self, agent_id: int, recent: list[dict]) -> tuple[str, ...]:
        """@brief 本秒置顶的浏览结果：同一秒后续各轮取该秒首轮的暂存，首轮取本次回灌的供给身份。"""
        staged = self.state.same_moment_presentation.get(agent_id)
        if staged is not None:
            return staged.pinned_supply_ids
        return tuple(dict.fromkeys(row["supply_id"] for row in _reported_supply_rows(recent)))

    def _window_rows(self, agent, pinned: tuple[str, ...]) -> dict:
        """@brief 观察侧的商品呈现窗口与 facts 行（同一窗口，只算一次）；pinned 为本秒置顶的浏览结果。"""
        context = self._presentation_context(agent)
        entries = self._product_pool(agent)
        sources = presentation.entry_sources(entries, on_site=_on_site_supplies(self, agent))
        inputs = presentation.candidate_inputs(
            context, sources, moment=False, rotation_offset=self._rotation_offset(agent.agent_id),
            last_presented_at=self._last_presented(agent.agent_id))
        window = presentation.present(entries, inputs, "product", None)
        window = _shared_supply_window(self._discovery_context(agent), window, pinned)
        products = [public_supply_row(row) for row in window.items]
        return {"products": products, "window": window}

    def _menu_windows(self, agent) -> dict[str, PresentationWindow]:
        """@brief 内容、游戏与生活活动三类对象的本轮呈现窗口（交互对象层来源）。

        这三类没有在场与库存这样的当下事实，可见性只由曝光轮转决定；它们与商品窗口
        共用同一候选契约，只有实际进入窗口的身份才更新呈现凭据（docs/dev/SPEC-life-runtime.md
        「曝光轮转」）。旧的目录下钻工具已随工具面收敛删除，因此窗口是模型看到
        这三类对象的唯一通道。
        """
        context = self._presentation_context(agent)
        offset = self._rotation_offset(agent.agent_id)
        last = self._last_presented(agent.agent_id)
        windows: dict[str, PresentationWindow] = {}
        for kind, entries in (("content", self._content_pool(agent)),
                              ("game", self._game_pool(agent)),
                              ("lifestyle", self._lifestyle_pool(agent))):
            inputs = presentation.candidate_inputs(
                context, presentation.entry_sources(entries), moment=False,
                rotation_offset=offset, last_presented_at=last)
            windows[kind] = presentation.present(entries, inputs, kind, None)
        return windows

    def _event_tool_support(self, agent, recent: list, presented: tuple[str, ...]) -> dict:
        """@brief 事件级工具支持与既有目录投影（不进 scene/facts 提示路径）。"""
        # 完整任务/路线/商品、可供性合同、游戏目录（docs/dev/SPEC-life-runtime.md「活动组合政策」「系统提示与身份前言」）。
        data: dict[str, Any] = {}
        data["interaction_menu"] = resolve_interaction_menu(
            self.scenario, agent.agent_id, agent.location_id).model_dump(mode="json")
        data["content_progress"] = self._content_progress(agent)
        data["work_progress"] = self._work_progress_rows(agent)
        data["messages"] = _mail_headers(agent, self.rules.messages)
        data["affordance_contract"] = self._affordance_contract
        visible = set(projection.visible_supplies(self.state, agent, self.scenario)) | set(presented)
        context = self._discovery_context(agent)
        data["supplies"] = [discovery.supply_row(context, supply) for supply in sorted(
            self.scenario.supplies, key=lambda item: item.supply_id) if supply.supply_id in visible]
        product_ids = {row["product_id"] for row in data["supplies"]}
        product_ids.update(product_id for holding in agent.inventory.values()
                           for product_id, count in holding.items() if count > 0)
        data["products"] = [self._visible_product(item) for item in sorted(product_ids)]
        data["discovery_catalog"] = {
            "product_ids": sorted(item.product_id for item in self.scenario.products),
            "business_kinds": sorted({item.business_kind for item in self.scenario.locations})}
        market_summary = self._market_summary()
        if market_summary is not None:
            data["market_summary"] = market_summary
        data["recent_action_results"] = list(recent)
        data["activity_summary"] = self._activity_summary(agent)
        data["pending_deliveries"] = [
            item.model_dump(mode="json") for item in self.state.deliveries.values()
            if item.agent_id == agent.agent_id and item.status == "pending"]
        data["inventory"] = agent.inventory
        data["available_inventory"] = agent.inventory.get(agent.location_id, {})
        data["inventory_items"] = self._inventory_item_contracts(agent)
        data["tasks"] = [self._visible_task(item, agent.agent_id)
                         for item in sorted(agent.visible_task_ids)
                         if item not in self.state.paid_task_ids
                         and self.state.current_time <= int(
                             self.rules.tasks[item].due_at.timestamp())]
        data["routes"] = _route_catalog(self.scenario.routes, self.state, agent.agent_id, self.semantics)
        data["game_catalog"] = _game_catalog(self.scenario)
        data["browse_targets"] = list(BROWSE_TARGETS)
        data["calendar_context"] = _calendar_context(self.scenario, self.state.current_time)
        data["locations"] = _location_catalog(self.scenario)
        return data

    def _agent_config(self, agent_id: int):
        """@brief 读取人物场景配置（档案字段与联系人声明）。"""
        return next(item for item in self.scenario.agents if item.agent_id == agent_id)

    def _work_progress_rows(self, agent) -> list[dict]:
        """@brief 工作目录：已投入/有效/合同剩余工时（docs/CONTRACTS.md「场景段落与事实清单」）。"""
        rows = []
        for task_id in sorted(agent.visible_task_ids):
            if task_id in self.state.paid_task_ids:
                continue
            task = self.rules.tasks[task_id]
            worked, effective = work_totals(self.state, agent.agent_id, task_id)
            rows.append({"task_id": task_id, "worked_seconds": worked,
                         "effective_seconds": effective,
                         "remaining_contract_seconds": max(
                             0, task.maximum_duration_seconds - worked)})
        return rows

    def _visible_task_rows(self, agent) -> list[dict]:
        """@brief 可见任务的 facts 行：task_id/标题/截止/剩余合同工时及五项前置条件字段。"""
        rows = []
        running = {activity.target_id for activity in active_activities(self.state, agent.agent_id)
                   if activity.kind == ActivityKind.WORK}
        for task_id in sorted(agent.visible_task_ids):
            if task_id in self.state.paid_task_ids:
                continue
            task = self.rules.tasks[task_id]
            due = int(task.due_at.timestamp())
            if self.state.current_time > due:
                continue
            worked, effective = work_totals(self.state, agent.agent_id, task_id)
            rows.append({
                "task_id": task_id, "title": task.title, "due_at": due,
                "remaining_contract_seconds": max(0, task.maximum_duration_seconds - worked),
                "submission_ready": effective >= task.required_effective_seconds and task_id not in running,
                "required_location_id": task.required_location_id,
                "required_skill": task.required_skill,
                "minimum_skill": task.minimum_skill,
                "maximum_duration_seconds": task.maximum_duration_seconds,
                "required_effective_seconds": task.required_effective_seconds,
            })
        return rows

    def _route_rows(self, agent) -> list[dict]:
        """@brief 路线事实保留未知与不可达，只有已核实路线计算时长；可达即 feasible，费力按出行方式给出。"""
        rows = _route_catalog(self.scenario.routes, self.state, agent.agent_id, self.semantics)
        met = self.semantics.body.activity_met
        return [{"destination": row["destination_id"], "mode": row["mode"],
                 "minutes": round(row["duration_seconds"] / 60) if row["status"] == "reachable" else None,
                 "fare": row["fare_fen"], "feasible": row["status"] == "reachable",
                 "effort": projection.route_effort(met[f"travel:{row['mode']}"]),
                 "status": row["status"], "reason": row["reason"]} for row in rows]

    def _reachable_places(self, agent) -> list[dict]:
        """@brief 场景句只列实际可达地点，营业未知保持为未知；含 location_id 供交互对象层使用。"""
        destination_ids = {route.destination_id for route in self.scenario.routes
                           if route.origin_id == agent.location_id and route.status == "reachable"}
        context = self._discovery_context(agent)
        rows = [discovery.place_row(context, location) for location in self.scenario.locations
                if location.location_id in destination_ids]
        return [{"location_id": row["location_id"], "name": row["name"],
                 "opening_status": row["opening_status"]} for row in rows]

    def _product_rows(self, agent) -> list[dict]:
        """@brief facts 供给行与下钻共用实际呈现窗口，保留商品引用及完整购买比较事实。"""
        return self._window_rows(agent, self._recent_action_results(agent.agent_id))["products"]

    def _activity_rows(self, agent) -> dict:
        """@brief 按共同活动集合生成独立场景进度与完整控制行；待收尾活动另列一组控制行（只接受收尾）。"""
        current = []
        for item in active_activities(self.state, agent.agent_id):
            label = "听取" if (item.kind == ActivityKind.WATCH
                               and self.rules.content(item.target_id).kind == "podcast_episode") else "进行"
            current.append({"title": self._activity_title(item) or item.kind.value,
                            "progress": _activity_progress_phrase(item), "verb": label})
        return {"current": current,
                "control": [self._control_row(item) for item in active_activities(self.state, agent.agent_id)],
                "pending": [self._control_row(item)
                            for item in activity_control.pending_conclusions(self.state, agent.agent_id)],
                "paused": [{"title": self._activity_title(item) or item.kind.value,
                            "progress": _activity_progress_phrase(item)}
                           for item in paused_activities(self.state, agent.agent_id)]}

    def _control_row(self, item) -> dict:
        """@brief 一项活动的八键控制行：身份、种类、实际状态、标题、进度短语、已做与计划秒数、数据深拷贝。"""
        return {"activity_id": item.activity_id, "kind": item.kind.value, "status": item.status.value,
                "title": self._activity_title(item), "progress": _activity_progress_phrase(item),
                "elapsed_seconds": item.elapsed_seconds, "planned_duration_seconds": item.planned_duration_seconds,
                "data": deepcopy(item.data)}

    def _market_summary(self) -> dict | None:
        """@brief 仅报告市场存在性，不从未呈现供给泄露报价或购买凭据。"""
        return {"supply_count": len(self.scenario.supplies)} if self.scenario.supplies else None

    def _visible_task(self, task_id: str, agent_id: int) -> dict:
        """@brief 返回公开合同、交付 Schema 和仅按实际工作揭示的正文。"""
        from worldmock.life.models import MeetingMinutes
        from worldmock.life.work import visible_work_material

        task = self.rules.tasks[task_id]
        data = task.model_dump(mode="json", exclude={"oracle_path", "oracle_sha256", "input_path"})
        worked, effective = work_totals(self.state, agent_id, task_id)
        data["deliverable_schema"] = MeetingMinutes.model_json_schema()
        data["material"] = visible_work_material(self.state, self.corpus_root, task, agent_id)
        data["worked_seconds"] = worked
        data["effective_seconds"] = effective
        data["remaining_contract_seconds"] = max(0, task.maximum_duration_seconds - worked)
        return data

    def _visible_product(self, product_id: str) -> dict:
        """@brief 商品只保留使用合同，报价和卖方只能从供给行取得。"""
        return self.rules.product(product_id).model_dump(mode="json")

    def _discovery_context(self, agent) -> discovery.DiscoveryContext:
        """@brief 装配当前观察的地图事实与分时解释入口。"""
        return discovery.DiscoveryContext(self.state, self.scenario, self.semantics, agent)

    def _inventory_item_contracts(self, agent) -> list[dict]:
        """@brief 投影当前地点正库存的使用合同，数量仍由现有库存字段唯一提供。"""
        fields = {"product_id", "name", "use_modes", "use_duration_seconds", "required_location_id"}
        holding = agent.inventory.get(agent.location_id, {})
        return [self.rules.product(product_id).model_dump(mode="json", include=fields)
                for product_id, quantity in sorted(holding.items()) if quantity > 0]

    def _content_progress(self, agent) -> dict[str, dict]:
        """@brief 返回人物已观看位置、接触次数、展示标题和当前媒体剩余时长。"""
        result = {}
        for item_id, progress in sorted(agent.content_progress.items()):
            item = self.rules.content(item_id)
            data = progress.model_dump(mode="json")
            data["remaining_media_seconds"] = max(0.0, item.duration_seconds - progress.position_seconds)
            title = self.rules.content_titles.get(item_id)
            if title is not None:
                data["title"] = title
            result[item_id] = data
        return result

    def _recent_action_results(self, agent_id: int) -> list[dict]:
        """@brief 返回上次实际发送进度提示后产生的动作结算事实。"""
        cutoff = self.store.last_agent_progress_request_seq(agent_id)
        events = self.store.read_agent_events("action_result", agent_id, after=cutoff)
        results = [event for event in events if event.action_id is not None
                   and event.visibility == "agent"]
        return [{"event_id": event.event_id, "action_id": event.action_id,
                 "decision_id": event.decision_id, "occurred_at": event.occurred_at,
                 "status": event.data.get("status"), "error_code": event.data.get("error_code"),
                 "message": event.data.get("message"), "result": event.data}
                for event in results]

    def _motivation_internal(self, agent) -> dict | None:
        """@brief 计算内部动机仪表（observation.data.motivation_internal，不进提示）。

        docs/dev/SPEC-life-realism.md「动机层」：评分器继续计算供评估与研究，但候选、名次与概率
        不再向模型展示。任何求值异常都吞掉并记日志（fail-open 于仪表）。
        """
        profile = next((item.motivation for item in self.scenario.agents
                        if item.agent_id == agent.agent_id and item.motivation is not None), None)
        transforms = self.semantics.motivation_transforms
        if profile is None or transforms is None:
            return None
        try:
            context = self._motivation_context(agent, profile)
            scored = evaluate_motivation(context, profile, transforms)
            if not scored:
                return None
            return {"schema_version": 1, "generated_at": self.state.current_time,
                    "candidates": [item.to_hint() for item in scored]}
        except Exception:
            import logging

            logging.warning("Motivation internal evaluation failed", exc_info=True)
            return None

    def _motivation_context(self, agent, profile) -> MotivationContext:
        """@brief 从当前世界装配只读动机事实，商品口味与供给报价分别传入。"""
        from worldmock.life.work import work_totals as totals

        now = self.state.current_time
        config = self._agent_config(agent.agent_id)
        tasks, habit, effective_total, requested = self._motivation_evidence(agent, now)
        weights = dict(agent.taste_weights)
        return MotivationContext(
            agent=agent,
            balance_fen=self.state.accounts[agent.balance_account_id],
            body=self.semantics.body,
            home_location_id=config.home_location_id,
            products=tuple(self.rules.items.values()),
            supplies=tuple(item for item in self.scenario.supplies if item.supply_id in
                           projection.visible_supplies(self.state, agent, self.scenario)),
            supply_stock=self.state.supply_stock,
            supply_quotes=self.state.supply_quotes,
            opening_exceptions=self.state.opening_exceptions,
            locations={item.location_id: item for item in self.scenario.locations},
            routes=self.scenario.routes,
            tasks=tuple(tasks),
            task_totals={task.task_id: totals(self.state, agent.agent_id, task.task_id)
                         for task in tasks},
            contents=_sample_contents(self.scenario.contents, profile, weights),
            content_category_counts={
                kind: sum(1 for item in self.scenario.contents if item.kind == kind)
                for kind in {item.kind for item in self.scenario.contents}},
            content_progress=agent.content_progress,
            active_activities=active_activities(self.state, agent.agent_id),
            habit_events=habit,
            consecutive_rejections=agent.consecutive_rejections,
            max_same_moment_decisions=self.scenario.max_same_moment_decisions,
            total_effective_seconds=effective_total,
            current_time=now,
            end_time=self.state.end_time,
            timezone=self.scenario.timezone,
            planned_travel_seconds=self.semantics.travel_planned_seconds,
            purchase_amount=self.semantics.purchase_amount,
            purchase_pairing_states={item.product_id: self.semantics.purchase_pairing(item.product_id).state.value
                                     for item in self.scenario.products},
            catalog_supplies=self.scenario.supplies,
            lifestyle_items=self.lifestyle_items, activity_location_kinds=self.rules.activity_location_kinds,
            content_segments={item_id: tuple(cue.end_seconds for cue in cues)
                              for item_id, cues in self.rules.subtitles.items()},
            taste_weights=weights,
            lifestyle_enabled=config.lifestyle_activities,
            recent_meal_counts=_recent_meal_counts(self.state.activities.values(), agent.agent_id, now))

    def _motivation_evidence(self, agent, now: int) -> tuple:
        """@brief 汇聚可见任务、习惯事件、请求足迹与有效工时总量（动机输入）。"""
        from worldmock.life.models import ActivityKind, ActivityStatus

        tasks = [self.rules.tasks[item] for item in sorted(agent.visible_task_ids)
                 if item not in self.state.paid_task_ids
                 and now <= int(self.rules.tasks[item].due_at.timestamp())]
        requested = self.store.read_agent_events("action_requested", agent.agent_id)
        habit = tuple((event.data.get("tool_name"),
                       event.data.get("request") if isinstance(event.data.get("request"), dict) else {})
                      for event in requested)
        effective_total = sum(item.effective_work_seconds for item in self.state.activities.values()
                              if item.agent_id == agent.agent_id and item.kind == ActivityKind.WORK
                              and item.status != ActivityStatus.FAILED)
        return tasks, habit, effective_total, requested

    def _activity_summary(self, agent) -> dict[str, int]:
        """@brief 一行品类计数：五类可选对象各按生成段的池规模报数，明细由下钻读取。

        计数与下钻同源：键闭集取同一组候选池（内容按品类、生活活动按品类、商品归
        `foods`、游戏与工作各占一键），因此模型看到的键一定可下钻。
        """
        pools = (self._content_pool(agent), self._lifestyle_pool(agent),
                 self._product_pool(agent, menu=True), self._game_pool(agent),
                 self._task_pool(agent))
        summary: dict[str, int] = {}
        for pool in pools:
            for entry in pool:
                summary[entry.category] = summary.get(entry.category, 0) + 1
        return dict(sorted(summary.items()))

    def _enabled_lifestyle_items(self, agent) -> tuple:
        """@brief 返回人物启用声明过滤后的生活活动目录条目（None=全局启用）。"""
        config = next(item for item in self.scenario.agents if item.agent_id == agent.agent_id)
        enabled = config.lifestyle_activities
        if enabled is None:
            return self.lifestyle_items
        allowed = set(enabled)
        return tuple(item for item in self.lifestyle_items if item.activity_id in allowed)

    def _complete_product_browse(self, state, agent) -> tuple:
        """@brief 有时长目录浏览完成后仍只呈现一个窗口，并为该窗口保存真实凭据。"""
        context = PresentationContext(state, self.scenario, self.semantics, agent)
        discovery_context = discovery.DiscoveryContext(state, self.scenario, self.semantics, agent)
        entries = _supply_entries(discovery_context, {item.supply_id for item in self.scenario.supplies})
        previous = state.presentation_state.get(agent.agent_id, {})
        inputs = presentation.candidate_inputs(context, presentation.entry_sources(entries), moment=False,
                                               rotation_offset=len(previous), last_presented_at=dict(previous))
        window = presentation.present(entries, inputs, "product", None)
        staged = _stage_presentation(state, agent.agent_id, window)
        staged = discovery.stage_visible_supplies(staged, agent.agent_id, set(window.presented), self.scenario)
        data = {"action": "browse", "category": presentation.FOOD_CATEGORY,
                "returned": len(window.items), "total": window.total, "next_cursor": window.next_cursor,
                "note": _BROWSE_NOTE, "items": list(window.items),
                "excluded": [item.as_row() for item in window.excluded]}
        return staged, data

    def _presentation_context(self, agent) -> PresentationContext:
        """@brief 装配一次呈现所需的只读世界切片。"""
        return PresentationContext(state=self.state, scenario=self.scenario,
                                   semantics=self.semantics, agent=agent)

    def _last_presented(self, agent_id: int) -> dict[str, int]:
        """@brief 该人物各对象的上次呈现时刻（轮转判据，不含任何偏好推断）。"""
        return dict(self.state.presentation_state.get(agent_id, {}))

    def _rotation_offset(self, agent_id: int) -> int:
        """@brief 该人物本次轮转的池内起点：取已留痕的呈现次数（单调前进、不折返）。

        游标只前进，因此任一对象的最坏等待间隔有界；它不记录「谁更受欢迎」，只记录
        绕池走到哪儿（docs/dev/SPEC-life-runtime.md「曝光轮转」）。
        """
        return len(self.state.presentation_state.get(agent_id, {}))

    def _product_pool(self, agent, menu: bool = False) -> tuple[PoolEntry, ...]:
        """@brief 购买候选以供给为身份，口味按商品读取，窗口外供给不授予购买资格。"""
        visible = (set(item.supply_id for item in self.scenario.supplies) if menu else
                   set(projection.visible_supplies(self.state, agent, self.scenario))
                   | set(_on_site_supplies(self, agent)))
        return _supply_entries(self._discovery_context(agent), visible)

    def _content_pool(self, agent) -> tuple[PoolEntry, ...]:
        """@brief 内容池：目录全量条目自带题材标签（排序键原料，不参与资格判定）。

        行的其余字段由 `_content_catalog` 同源补齐（分段、标题、进度），因此下钻行形状
        与既有一致；`category` 取内容品类，是它的下钻键。
        """
        rows = {row["item_id"]: row for row in _content_catalog(
            self.scenario, self.rules.subtitles, self.rules.content_titles,
            agent.content_progress)}
        return tuple(PoolEntry(
            object_id=item.item_id, kind="content", bucket=item.item_id,
            tags=tuple(getattr(item, "genre", ()) or ()),
            row=rows[item.item_id], category=item.kind)
            for item in sorted(self.scenario.contents, key=lambda value: value.item_id)
            if item.kind != presentation.GAME_CATEGORY)

    def _lifestyle_pool(self, agent) -> tuple[PoolEntry, ...]:
        """@brief 生活活动池：只含本人启用集内的目录条目（启用集是世界事实）。"""
        from worldmock.life.models import LIFESTYLE_TYPE_PREFIX

        return tuple(PoolEntry(
            object_id=entry.activity_id, kind="lifestyle",
            bucket=entry.activity_id, minimum_seconds=60,
            maximum_seconds=entry.typical_duration_minutes * 60,
            location_kinds=self.rules.activity_location_kinds[entry.activity_id], met=entry.met_value,
            row={"activity_id": entry.activity_id,
                 "activity_type": f"{LIFESTYLE_TYPE_PREFIX}{entry.activity_id}",
                 "name": entry.name, "category": entry.category,
                 "typical_duration_minutes": entry.typical_duration_minutes,
                 "description": entry.description, "met_value": entry.met_value},
            category=entry.category)
            for entry in sorted(self._enabled_lifestyle_items(agent),
                                key=lambda item: item.activity_id))

    def _game_pool(self, agent) -> tuple[PoolEntry, ...]:
        """@brief 游戏池：`game_session` 下钻键下的全部条目（内容侧会话 + 场景声明身份）。

        `game_session` 同时是内容品类与游戏身份的下钻键：两者共用同一池与同一键，否则该
        键下的一半条目没有链路可走（「某一品类走了不经过生成段的枚举路径」即违规）。
        """
        rows = {row["item_id"]: row for row in _content_catalog(
            self.scenario, self.rules.subtitles, self.rules.content_titles,
            agent.content_progress)}
        games = tuple(PoolEntry(object_id=item.game_id, kind="game", bucket=item.game_id,
                                row={"game_id": item.game_id, "seed": item.seed,
                                     "title": item.title,
                                     "category": presentation.GAME_CATEGORY},
                                category=presentation.GAME_CATEGORY)
                      for item in self.scenario.games)
        sessions = tuple(PoolEntry(
            object_id=item.item_id, kind="game", bucket=item.item_id,
            row=rows[item.item_id], category=presentation.GAME_CATEGORY)
            for item in sorted(self.scenario.contents, key=lambda value: value.item_id)
            if item.kind == presentation.GAME_CATEGORY)
        return (*games, *sessions)

    def _task_pool(self, agent) -> tuple[PoolEntry, ...]:
        """@brief 工作池：本人可见、未结清且未过截止的合同。"""
        rows = []
        for task_id in sorted(agent.visible_task_ids):
            if task_id in self.state.paid_task_ids:
                continue
            due = int(self.rules.tasks[task_id].due_at.timestamp())
            if self.state.current_time > due:
                continue
            rows.append(PoolEntry(object_id=task_id, kind="task", bucket=task_id,
                                  minimum_seconds=60,
                                  maximum_seconds=self.rules.tasks[task_id].maximum_duration_seconds,
                                  required_location_id=self.rules.tasks[task_id].required_location_id,
                                  row={"task_id": task_id,
                                       "category": presentation.TASK_CATEGORY},
                                  category=presentation.TASK_CATEGORY))
        return tuple(rows)

    def _snapshot_decision_references(self, staged, agent_ids: list[int]):
        """@brief 决策落定后更新滞回参照：分级/在场人物/已知任务（docs/dev/SPEC-life-runtime.md「滞回规则」）。"""
        agents = dict(staged.agents)
        for agent_id in agent_ids:
            agent = agents[agent_id]
            hour = projection.hour_of_day(staged.current_time, self.scenario.timezone)
            config = self._agent_config(agent_id)
            windows = felt.MealWindows(lunch=config.lunch_window, dinner=config.dinner_window)
            levels = projection.body_projection(agent, hour, self.semantics.body, windows)["levels"]
            people = frozenset(item["name"] for item in
                               projection.people_here_views(staged, agent))
            agents[agent_id] = agent.model_copy(update={
                "last_decision_time": staged.current_time,
                "felt_bands_at_last_decision": levels,
                "people_at_last_decision": people,
                "known_task_ids": set(agent.visible_task_ids)}, deep=True)
        return staged.model_copy(update={"agents": agents}, deep=True)


def _activity_progress_phrase(activity) -> str:
    """@brief 活动进度短语（不含完整快照，观察合同禁完整活动快照）。"""
    remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
    return f"还剩约{round(remaining / 60)}分钟"


def _mail_headers(agent, messages) -> list[dict]:
    """@brief 观察只展示已到达邮件的标题，已读邮件才展示正文（content 口径）。"""
    result = []
    for message_id in sorted(agent.visible_message_ids):
        message = messages[message_id]
        record = {"message_id": message_id, "thread_id": message.thread_id,
                  "subject": message.subject, "read": message_id in agent.read_message_ids}
        if record["read"]:
            record["body"] = message.body
        result.append(record)
    return result


def _route_catalog(routes: tuple, state, agent_id: int, semantics) -> list[dict]:
    """@brief 返回人物当前位置出发的冻结路线身份和出发时刻报价（事件级；路线与恢复都不查能量）。"""
    agent, now = state.agents[agent_id], state.current_time
    fields = {"route_id", "origin_id", "destination_id", "mode", "status", "reason",
              "distance_meters", "duration_seconds", "fare_fen", "fare_source_kind"}
    result = []
    for item in routes:
        if item.origin_id != agent.location_id:
            continue
        data = item.model_dump(mode="json", include=fields)
        data.update(free_flow_duration_seconds=item.duration_seconds)
        if item.status == "reachable":
            planned = semantics.travel_planned_seconds(item.mode.value, item.duration_seconds, now)
            data.update(duration_seconds=planned,
                        travel_time_factor=semantics.travel_time_factor(item.mode.value, now))
        result.append(data)
    return result


def _paused_activity_observation(activity) -> dict:
    """@brief 暂停活动的完整记录与剩余墙钟时长；恢复不查能量（docs/dev/SPEC-time-and-energy.md「删除的机制」）。"""
    data = activity.model_dump(mode="json")
    data["remaining_duration_seconds"] = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
    return data


def _calendar_context(scenario, epoch: int) -> dict:
    """@brief 返回当前本地日期已声明的日历机会与约束。"""
    from worldmock.life.engine import _datetime

    current_date = _datetime(epoch, scenario.timezone).date().isoformat()
    calendar = scenario.calendar
    return {"date": current_date,
            "spring_festival": current_date in calendar.spring_festival_dates,
            "makeup_workday": current_date in calendar.makeup_work_dates,
            "overtime_opportunity": current_date in calendar.overtime_opportunity_dates,
            "sick_leave": current_date == calendar.sick_leave_date,
            "personal_leave": current_date == calendar.personal_leave_date,
            "social_dinner_opportunity": current_date == calendar.social_dinner_date}


def _location_catalog(scenario) -> list[dict]:
    """@brief 返回人物能够规划到达和营业条件的地点目录（事件级）。"""
    fields = {"location_id", "name", "kind", "business_kind", "location_kinds", "longitude", "latitude"}
    return [item.model_dump(mode="json", include=fields) for item in scenario.locations]


def _game_catalog(scenario) -> list[dict]:
    """@brief 返回可新建 TextWorld 会话的稳定游戏身份（事件级）。"""
    return [{"game_id": item.game_id, "seed": item.seed} for item in scenario.games]


def _content_catalog(scenario, subtitles: dict, titles: dict,
                     progress: dict[str, ContentProgress]) -> list[dict]:
    """@brief 返回人物可选择内容及其分段结构，不暴露本地路径和哈希。"""
    from worldmock.life.content import SubtitleCue

    fields = {"item_id", "kind", "duration_seconds", "entertainment_relief_per_hour", "experience_label"}
    result = []
    for item in scenario.contents:
        data = item.model_dump(mode="json", include=fields)
        cues = subtitles.get(item.item_id, ())
        total = len(cues)
        data["segments_total"] = total
        data["typical_segment_seconds"] = (
            round(item.duration_seconds / total, 1) if total else float(item.duration_seconds))
        if item.item_id in titles:
            data["title"] = titles[item.item_id]
        record = progress.get(item.item_id)
        if record is not None:
            completed = sum(1 for cue in cues if cue.end_seconds <= record.position_seconds)
            data["progress"] = {
                "position_seconds": record.position_seconds,
                "remaining_seconds": max(0.0, item.duration_seconds - record.position_seconds),
                "segments_total": total,
                "segments_completed": completed,
                "segments_remaining": max(0, total - completed),
                "current_segment": min(completed + 1, total),
            }
        result.append(data)
    return result


def _recent_meal_counts(activities, agent_id: int, now: int) -> dict[str, int]:
    """@brief 从近一天实际结算的食用量推导商品重复进食次数。"""
    from math import ceil
    from worldmock.life.models import ActivityKind

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


def _sample_contents(contents, profile, taste_weights) -> tuple:
    """@brief 每内容品类按 attention_prior x 口味权重采 top-2，全局按权排序。

    权重唯一源是 taste_weights（确定性，docs/dev/SPEC-life-realism.md「画像编译」）；空映射时
    退化为按品类注意力排序（稳定 item_id 决胜），与评分器的零抬升口径一致。
    """

    def weight(item) -> float:
        """@brief 品类注意力与确定性口味权重的乘积排序键。"""
        return (profile.attention_prior.get(item.kind, 0)
                * taste_weights.get(item.item_id, 0.0))

    ranked = sorted(contents, key=lambda item: (-weight(item), item.item_id))
    per_kind: dict[str, int] = {}
    sample = []
    for item in ranked:
        taken = per_kind.get(item.kind, 0)
        if taken >= 2:
            continue
        per_kind[item.kind] = taken + 1
        sample.append(item)
    return tuple(sample)


#: 浏览动作的目录对象身份闭集（交互对象层的 catalog 行与事件级支持行同源）。
BROWSE_TARGETS = ("product_catalog",)

#: 目录浏览完成后的提示句（说明窗口里的身份还要经购买时复核）。
_BROWSE_NOTE = (
    "compare supply_id and current price_version; "
    "purchase rechecks stock, access, opening and fulfillment")


def _reported_supply_rows(recent: list[dict]) -> list[dict]:
    """@brief 读取本次浏览回灌真正包含的供给行，不枚举未呈现目录。"""
    reported = []
    for result in recent:
        data = result["result"]["data"]
        if "browse_result" in data:
            reported.extend(item for item in data["browse_result"]["items"] if "supply_id" in item)
    return reported


def _shared_supply_window(context: discovery.DiscoveryContext, window: PresentationWindow,
                          identities: tuple[str, ...]) -> PresentationWindow:
    """@brief 回灌与当前事实共用供给窗口，只有实际呈现身份更新凭据及轮转时间；identities 为置顶的浏览结果。"""
    if not identities:
        return window
    current = {entry.object_id: entry.row for entry in _supply_entries(context, set(identities))}
    items = [current[identity] for identity in identities]
    remaining = max(0, context.semantics.candidate_constants.presentation_limit - len(items))
    items.extend([row for row in window.items if row["supply_id"] not in current][:remaining])
    shown = tuple(row["supply_id"] for row in items)
    excluded = [item for item in window.excluded if item.object_id not in shown]
    excluded.extend(Exclusion(row["supply_id"], "product", "ROTATION_DEFERRED")
                    for row in window.items if row["supply_id"] not in shown)
    excluded.sort(key=lambda item: (item.kind, item.object_id))
    return replace(window, items=tuple(items), presented=shown, excluded=tuple(excluded),
                   deferred=tuple(item.object_id for item in excluded if item.reason == "ROTATION_DEFERRED"))


def _supply_entries(context: discovery.DiscoveryContext, visible: set[str]) -> tuple[PoolEntry, ...]:
    """@brief 把供给比较事实装成统一候选，供观察、下钻和有时长浏览复用。"""
    entries = []
    for supply in sorted(context.scenario.supplies, key=lambda item: item.supply_id):
        if supply.supply_id not in visible:
            continue
        row = discovery.supply_row(context, supply)
        row["category"] = presentation.FOOD_CATEGORY
        entries.append(PoolEntry(object_id=supply.supply_id, kind="product", bucket=supply.product_id,
                                  stock=context.state.supply_stock[supply.supply_id],
                                  required_location_id=supply.location_id, minimum_seconds=0,
                                  maximum_seconds=row["use_duration_seconds"], row=row,
                                  category=presentation.FOOD_CATEGORY))
    return tuple(entries)


def _on_site_supplies(engine, agent) -> frozenset[str]:
    """@brief 当前地点供给只作为生成候选；实际窗口决定购买凭据。"""
    return frozenset(item.supply_id for item in engine.scenario.supplies
                     if item.location_id == agent.location_id)


def _presentation_stamps(state, presented: tuple[str, ...]) -> dict[str, int]:
    """@brief 计算本次呈现的留痕：本人实际进入窗口的对象标识 → 当前时刻。

    轮转的判据是「上次呈现时刻」，所以每次呈现都要留痕；留痕是新的派生量取值，但**不
    允许在观察边界直接落盘**——`store._validate_state` 只允许同版本追加事件，因此引擎
    把结果按人物暂存，决策轮提交时写入世界状态的本秒暂存，时钟推进时才并入 `presentation_state`。
    这里不改目录、不改对象状态、不产生下架事件（docs/dev/SPEC-life-runtime.md「曝光轮转」
    「禁止永久退役」）。

    @param state 当前世界状态。
    @param presented 本轮实际进入窗口的对象身份（可跨多个分区窗口拼接）。
    @return 对象标识 → 当前时刻；本次没有呈现任何对象时为空字典。
    """
    return {object_id: int(state.current_time) for object_id in presented}


def _stage_presentation(state, agent_id: int, window: PresentationWindow):
    """@brief 把本次呈现的留痕并入该人物的 `presentation_state`（浏览完成在时钟推进路径上，故可当场落盘）。

    @param state 本次提交的世界状态。
    @param agent_id 被呈现的对象所属人物。
    @param window 本次呈现窗口。
    @return 写入留痕后的世界状态副本。
    """
    stamps = _presentation_stamps(state, window.presented)
    if not stamps:
        return state
    presented = {**state.presentation_state, agent_id: {**state.presentation_state.get(agent_id, {}), **stamps}}
    return state.model_copy(update={"presentation_state": presented}, deep=True)
