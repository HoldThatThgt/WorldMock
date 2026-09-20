"""@brief LifeEngine 的观察投影与浏览/动机上下文装配（Mixin 层）。

本模块从 engine.py 下沉（生产文件 ≤2000 行规范）：包含两层观察组装
（scene + facts + 事件级工具支持行）、活动品类摘要、browse_activities
下钻、内部动机仪表上下文与决策参照快照。engine 侧以 Mixin 继承接线，
`self` 即 LifeEngine；行为合同与注释见修订合同观察投影一节（docs/dev/SPEC-life-runtime.md「观察投影与提示合同」）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any

from worldmock.life import discovery, felt, presentation, projection
from worldmock.life.models import ActivityStatus, BrowseActivitiesRequest, ContentProgress, DiscoverPlacesRequest
from worldmock.life.motivation import MotivationContext, evaluate_motivation
from worldmock.life.body import maximum_energy_duration_seconds, minimum_energy_cost
from worldmock.life.models import ActivityKind
from worldmock.life.interaction_menu import resolve_interaction_menu
from worldmock.life.menu_execution import require_interaction_menu
from worldmock.life.presentation import PoolEntry, PresentationContext, PresentationWindow
from worldmock.life.candidates import Exclusion
from worldmock.life.work import work_totals
from worldmock.life.result_projection import public_supply_row


class ObservationMixin:
    """@brief 观察投影、浏览与动机仪表的引擎装配方法（self 为 LifeEngine）。"""

    def _observation_data(self, agent) -> dict:
        """@brief 两层观察投影：scene 场景输入 + facts 精简结构 + body 数值证据。

        黑名单字段不进 scene/facts 提示路径；事件级工具支持行（完整任务/路线/
        商品、能量可行性、可供性合同、游戏目录）供宿主工具 Schema 使用。商品
        事实清单经统一候选契约产出，呈现后由 `_presentation_stamps` 留痕轮转判据。
        """
        config = self._agent_config(agent.agent_id)
        meal_windows = felt.MealWindows(lunch=config.lunch_window, dinner=config.dinner_window)
        recent = self._recent_action_results(agent.agent_id)
        rows = self._window_rows(agent, recent)
        rows["products"] = _current_supply_facts(rows["products"], recent)
        self._pending_presentation = _presentation_stamps(
            self.state, agent.agent_id, rows["window"])
        self._pending_visible_supplies[agent.agent_id] = set(rows["window"].presented)
        inputs = projection.ProjectionInputs(
            state=self.state, scenario=self.scenario, agent=agent,
            recent_results=tuple(recent),
            task_rows=tuple(self._visible_task_rows(agent)),
            meal_windows=meal_windows,
            motivation_internal=self._motivation_internal(agent),
            extras={
                "scenario_messages": self.rules.messages,
                "route_rows": self._route_rows(agent),
                "reachable_places": self._reachable_places(agent),
                "product_rows": rows["products"],
                "activity_rows": self._activity_rows(agent),
            })
        data = projection.build_observation(inputs)
        data["paused_activities"] = [
            _paused_activity_observation(item, agent, self.semantics.body) for item in sorted(
                self.state.activities.values(), key=lambda value: value.activity_id)
            if item.agent_id == agent.agent_id and item.status == ActivityStatus.PAUSED]
        data["wake_reasons"] = list(agent.pending_wake_reasons)
        data["location_id"] = agent.location_id
        data["hour_of_day"] = projection.hour_of_day(
            self.state.current_time, self.scenario.timezone)
        data.update(self._event_tool_support(agent, recent, rows["window"].presented))
        return data

    def _window_rows(self, agent, recent: list[dict]) -> dict:
        """@brief 观察侧的商品呈现窗口与 facts 行（同一窗口，只算一次）。"""
        context = self._presentation_context(agent)
        entries = self._product_pool(agent)
        sources = presentation.entry_sources(entries, on_site=_on_site_supplies(self, agent))
        inputs = presentation.candidate_inputs(
            context, sources, moment=False, rotation_offset=self._rotation_offset(agent.agent_id),
            last_presented_at=self._last_presented(agent.agent_id))
        window = presentation.present(entries, inputs, "product", None)
        window = _shared_supply_window(self._discovery_context(agent), window, recent)
        products = [public_supply_row(row) for row in window.items]
        return {"products": products, "window": window}

    def _event_tool_support(self, agent, recent: list, presented: tuple[str, ...]) -> dict:
        """@brief 事件级工具支持与既有目录投影（不进 scene/facts 提示路径）。"""
        # 完整任务/路线/商品、能量可行性、可供性合同、游戏目录（docs/dev/SPEC-life-runtime.md「活动组合政策」「系统提示与身份前言」）。
        data: dict[str, Any] = {}
        data["interaction_menu"] = resolve_interaction_menu(
            self.scenario, agent.agent_id, agent.location_id).model_dump(mode="json")
        data["content_progress"] = self._content_progress(agent)
        data["games"] = {key: value.model_dump(mode="json")
                         for key, value in agent.game_sessions.items()}
        data["work_progress"] = self._work_progress_rows(agent)
        data["messages"] = _mail_headers(agent, self.rules.messages)
        data["energy_feasibility"] = _activity_energy_feasibility(
            agent, self.semantics.body, self._affordance_contract)
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
        data["routes"] = _route_catalog(self.scenario.routes, self.semantics.body,
                                        agent, self.state.current_time, self.semantics)
        data["game_catalog"] = _game_catalog(self.scenario)
        data["browse_targets"] = ["product_catalog"]
        data["calendar_context"] = _calendar_context(self.scenario, self.state.current_time)
        data["locations"] = _location_catalog(self.scenario)
        return data

    def _agent_config(self, agent_id: int):
        """@brief 读取人物场景配置（档案字段与联系人声明）。"""
        return next(item for item in self.scenario.agents if item.agent_id == agent_id)

    def _work_progress_rows(self, agent) -> list[dict]:
        """@brief 工作目录：已投入/有效/合同剩余工时（docs/dev/SPEC-life-runtime.md「活动组合政策」既有口径）。"""
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
        """@brief 可见任务的 facts 行：task_id/标题/截止/剩余合同工时。"""
        rows = []
        for task_id in sorted(agent.visible_task_ids):
            if task_id in self.state.paid_task_ids:
                continue
            task = self.rules.tasks[task_id]
            due = int(task.due_at.timestamp())
            if self.state.current_time > due:
                continue
            worked, _effective = work_totals(self.state, agent.agent_id, task_id)
            title = self.rules.content_titles.get(task_id)
            rows.append({"task_id": task_id, "title": title or task.task_id, "due_at": due,
                         "remaining_contract_seconds": max(0, task.maximum_duration_seconds - worked)})
        return rows

    def _route_rows(self, agent) -> list[dict]:
        """@brief 路线事实保留未知与不可达，只有已核实路线计算时长和能量。"""
        rows = _route_catalog(self.scenario.routes, self.semantics.body,
                              agent, self.state.current_time, self.semantics)
        return [{"destination": row["destination_id"], "mode": row["mode"],
                 "minutes": round(row["duration_seconds"] / 60) if row["status"] == "reachable" else None,
                 "fare": row["fare_fen"], "feasible": row["energy_feasible"],
                 "status": row["status"], "reason": row["reason"]} for row in rows]

    def _reachable_places(self, agent) -> list[dict]:
        """@brief 场景句只列实际可达地点，营业未知保持为未知。"""
        destination_ids = {route.destination_id for route in self.scenario.routes
                           if route.origin_id == agent.location_id and route.status == "reachable"}
        context = self._discovery_context(agent)
        rows = [discovery.place_row(context, location) for location in self.scenario.locations
                if location.location_id in destination_ids]
        return [{"name": row["name"], "opening_status": row["opening_status"]} for row in rows]

    def _product_rows(self, agent) -> list[dict]:
        """@brief facts 供给行与下钻共用实际呈现窗口，保留商品引用及完整购买比较事实。"""
        return self._window_rows(agent, self._recent_action_results(agent.agent_id))["products"]

    def _activity_rows(self, agent) -> dict:
        """@brief scene 活动进度行与 facts 控制身份（不含完整活动快照）。"""
        active = self.state.activities.get(agent.current_activity_id) if agent.current_activity_id else None
        paused = [item for item in sorted(self.state.activities.values(),
                                          key=lambda value: value.activity_id)
                  if item.agent_id == agent.agent_id and item.status == ActivityStatus.PAUSED]
        current = None
        control = None
        if active is not None:
            title = self._activity_title(active)
            progress = _activity_progress_phrase(active)
            current = {"title": title or active.kind.value, "progress": progress}
            control = {"activity_id": active.activity_id, "kind": active.kind.value,
                       "title": title, "progress": progress,
                       "elapsed_seconds": active.elapsed_seconds,
                       "planned_duration_seconds": active.planned_duration_seconds}
        return {"current": current, "control": control,
                "paused": [{"title": self._activity_title(item) or item.kind.value,
                            "progress": _activity_progress_phrase(item)} for item in paused]}

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
        """@brief 返回该人物上次观察后新产生的动作结算事实摘要。"""
        cutoff = self.store.last_agent_event_seq("observation", agent_id)
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
            active_activity=(self.state.activities.get(agent.current_activity_id)
                             if agent.current_activity_id else None),
            habit_events=habit,
            consecutive_rejections=agent.consecutive_rejections,
            max_consecutive_rejections=self.scenario.max_consecutive_rejections,
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
            recent_meal_counts=_recent_meal_counts(requested, now))

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

    def _discover_places(self, state, pending, request: DiscoverPlacesRequest):
        """@brief 地点查询经生活动作结算，只把实际返回供给授予下一轮可见凭据。"""
        from worldmock.life.models import ActionStatus, ActionResult

        require_interaction_menu(self.scenario, state, pending, request)
        agent = state.agents[pending.agent_id]
        data = discovery.discover_places(state, self.scenario, self.semantics, agent, request)
        visible = {supply["supply_id"] for place in data["places"] for supply in place["supplies"]}
        staged = discovery.stage_visible_supplies(state, agent.agent_id, visible, self.scenario)
        result = ActionResult(action_id=pending.action_id, agent_id=agent.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Places discovered", data=data)
        return staged, result

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
        data = {"tool": "browse_activities", "category": presentation.FOOD_CATEGORY,
                "returned": len(window.items), "total": window.total, "next_cursor": window.next_cursor,
                "note": _browse_note(presentation.FOOD_CATEGORY), "items": list(window.items),
                "excluded": [item.as_row() for item in window.excluded]}
        return staged, data

    def _browse_activities(self, state, pending, request: BrowseActivitiesRequest):
        """@brief 下钻读取一个活动品类目录：纯查询，不启动活动、不消耗内容。

        条目清单经统一候选契约（生成 → 资格 → 排序）产出，首屏的呈现顺序由曝光轮转
        决定；返回体按「下推返回体」合同给出本次条数、总数、下一页游标与被排除项
        （docs/CONTRACTS.md「候选与呈现合同」）。
        """
        from worldmock.errors import BusinessRuleError
        from worldmock.life.models import ActionStatus, ActionResult

        agent = state.agents[pending.agent_id]
        category = request.category
        if category not in self._activity_summary(agent):
            logging.error("Browse category is not declared: %s", category)
            raise BusinessRuleError("BROWSE_CATEGORY_NOT_FOUND")
        require_interaction_menu(self.scenario, state, pending, request)
        window = self._candidate_window(agent, category, request.cursor)
        result = ActionResult(
            action_id=pending.action_id, agent_id=agent.agent_id,
            status=ActionStatus.COMPLETED, occurred_at=state.current_time,
            message="Activity menu browsed",
            data={"tool": "browse_activities", "category": category,
                  "returned": len(window.items), "total": window.total,
                  "next_cursor": window.next_cursor, "note": _browse_note(category),
                  "items": list(window.items),
                  "excluded": [item.as_row() for item in window.excluded]})
        staged = _stage_presentation(state, agent.agent_id, window)
        visible = {row["supply_id"] for row in window.items if "supply_id" in row}
        return discovery.stage_visible_supplies(staged, agent.agent_id, visible, self.scenario), result

    def _candidate_window(self, agent, category: str,
                          cursor: str | None) -> PresentationWindow:
        """@brief 走统一候选契约取一个品类分区的呈现窗口（五类对象同一链路）。

        下钻是目录浏览：只施加目录级事实（库存、下架、启用集、时长窗口），不施加
        地点与占用这类当下可行性判定；呈现顺序首屏由曝光轮转给出。
        """
        kind = _kind_of_category(category, self.scenario)
        context = self._presentation_context(agent)
        entries = self._pool_entries(context, agent, kind, category)
        offset = (presentation.cursor_offset(cursor) if cursor
                  else self._rotation_offset(agent.agent_id))
        inputs = presentation.candidate_inputs(
            context, presentation.entry_sources(entries), moment=False,
            rotation_offset=offset, last_presented_at=self._last_presented(agent.agent_id))
        return presentation.present(entries, inputs, kind, cursor)

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

    def _pool_entries(self, context: PresentationContext, agent, kind: str,
                      category: str) -> tuple[PoolEntry, ...]:
        """@brief 按候选类别装配该分区的池条目（五类对象共用同一形状）。

        商品分区在图鉴下钻时读取全供给目录；观察投影读取在场与凭据有效的供给。
        两者共用供给行与候选管线，只有实际进入窗口的身份才更新呈现凭据。
        """
        if kind == "product":
            return self._product_pool(agent, menu=bool(category))
        if kind == "content":
            return tuple(entry for entry in self._content_pool(agent)
                         if category in ("", entry.category))
        if kind == "game":
            return self._game_pool(agent)
        if kind == "task":
            return self._task_pool(agent)
        return tuple(entry for entry in self._lifestyle_pool(agent)
                     if category in ("", entry.category))

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
            levels = projection.body_projection(agent, hour, windows)["levels"]
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


def _route_catalog(routes: tuple, body, agent, now: int, semantics) -> list[dict]:
    """@brief 返回人物当前位置出发的冻结路线身份和出发时刻报价（事件级）。"""
    fields = {"route_id", "origin_id", "destination_id", "mode", "status", "reason",
              "distance_meters", "duration_seconds", "fare_fen", "fare_source_kind"}
    result = []
    for item in routes:
        if item.origin_id != agent.location_id:
            continue
        data = item.model_dump(mode="json", include=fields)
        data.update(free_flow_duration_seconds=item.duration_seconds,
                    minimum_energy_cost_kcal=None, energy_feasible=False)
        if item.status == "reachable":
            planned = semantics.travel_planned_seconds(item.mode.value, item.duration_seconds, now)
            met = body.activity_met[f"travel:{item.mode.value}"]
            cost = minimum_energy_cost(ActivityKind.TRAVEL, planned, body, met)
            data.update(duration_seconds=planned, minimum_energy_cost_kcal=cost,
                        travel_time_factor=semantics.travel_time_factor(item.mode.value, now),
                        energy_feasible=cost <= agent.body.energy_kcal)
        result.append(data)
    return result


def _activity_energy_feasibility(agent, body, contract: dict) -> dict:
    """@brief 返回能量投影活动按当前能量可受理的最大整数墙钟秒数。"""
    maximums = {
        kind: maximum_energy_duration_seconds(
            ActivityKind(kind), agent.body.energy_kcal, body)
        for kind, entry in contract.items() if entry["energy_projection"]
    }
    return {"maximum_duration_seconds_by_kind": maximums}


def _paused_activity_observation(activity, agent, body) -> dict:
    """@brief 公开暂停活动本身及当前剩余时长的恢复能量可行性。"""
    remaining = max(0, activity.planned_duration_seconds - activity.elapsed_seconds)
    from worldmock.life.activities import activity_factors

    factors = activity_factors(activity, agent, body)
    cost = minimum_energy_cost(activity.kind, remaining, body, factors.met)
    from worldmock.life.body import ENERGY_RECOVERY_ACTIVITY_KINDS

    feasible = activity.kind in ENERGY_RECOVERY_ACTIVITY_KINDS or cost <= agent.body.energy_kcal
    data = activity.model_dump(mode="json")
    data.update({"remaining_duration_seconds": remaining,
                 "minimum_energy_cost_kcal": cost,
                 "resume_energy_feasible": feasible})
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


def _recent_meal_counts(requested_events, now: int) -> dict[str, int]:
    """@brief 近 24 小时各商品已进食次数（product_id -> n，docs/dev/SPEC-life-realism.md「动机层」）。

    从足迹 action_requested 的 consume 请求聚合（use_mode=eat）：每件按
    数量累计（一次吃两份计 2 次，Rolls 1981 感官特异性饱足按食用次数降权）。
    24 小时窗口为规格常数（docs/dev/SPEC-life-realism.md「动机层」"近 24 小时"）。
    """
    counts: dict[str, int] = {}
    for event in requested_events:
        data = event.data if isinstance(event.data, dict) else {}
        if data.get("tool_name") != "consume":
            continue
        if not 0 <= now - event.occurred_at <= 86_400:
            continue
        request = data.get("request")
        if not isinstance(request, dict) or request.get("use_mode") != "eat":
            continue
        items = request.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("product_id"), str):
                continue
            quantity = item.get("quantity")
            units = (quantity if isinstance(quantity, (int, float))
                     and not isinstance(quantity, bool) and quantity > 0 else 1)
            counts[item["product_id"]] = counts.get(item["product_id"], 0) + int(units)
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


#: 品类下钻的提示句（按分区给出可用动作，不描述内部口径）。
_BROWSE_NOTES = {
    presentation.FOOD_CATEGORY:
        "compare supply_id and current price_version; purchase rechecks stock, access, opening and fulfillment",
    presentation.GAME_CATEGORY: "game ids accept start_targeted_activity with activity_type play_game",
    presentation.TASK_CATEGORY: "work contracts accept start_targeted_activity with activity_type work",
    "content": "watch items accept start_targeted_activity with activity_type watch",
    "lifestyle": "lifestyle items accept start_basic_activity with activity_type lifestyle:<activity_id>",
}


def _kind_of_category(category: str, scenario) -> str:
    """@brief 品类下钻键到候选类别：内容品类、生活活动品类与三个固定键各归一类。"""
    if category == presentation.FOOD_CATEGORY:
        return "product"
    if category == presentation.GAME_CATEGORY:
        return "game"
    if category == presentation.TASK_CATEGORY:
        return "task"
    if category in {item.kind for item in scenario.contents}:
        return "content"
    return "lifestyle"


def _browse_note(category: str) -> str:
    """@brief 该品类的下钻提示句；内容品类与生活活动品类各取固定句。"""
    if category in _BROWSE_NOTES:
        return _BROWSE_NOTES[category]
    return _BROWSE_NOTES["content"] if category in _CONTENT_NOTE_KINDS \
        else _BROWSE_NOTES["lifestyle"]


#: 提示句归到内容句的内容品类尾名单（不在 `_BROWSE_NOTES` 里逐项出现的那些）。
_CONTENT_NOTE_KINDS = frozenset({"feature_film", "drama_series", "webnovel_excerpt",
                                 "news_feed", "joke_feed", "subtitle_experience"})


def _reported_supply_rows(recent: list[dict]) -> list[dict]:
    """@brief 读取本次发现与两种浏览回灌真正包含的供给行，不枚举未呈现目录。"""
    reported = []
    for result in recent:
        data = result["result"]["data"]
        if data.get("tool") == "discover_places":
            reported.extend(supply for place in data["places"] for supply in place["supplies"])
        if data.get("tool") == "browse_activities":
            reported.extend(item for item in data["items"] if "supply_id" in item)
        if "browse_result" in data:
            reported.extend(item for item in data["browse_result"]["items"] if "supply_id" in item)
    return reported


def _shared_supply_window(context: discovery.DiscoveryContext, window: PresentationWindow,
                          recent: list[dict]) -> PresentationWindow:
    """@brief 回灌与当前事实共用供给窗口，只有实际呈现身份更新凭据及轮转时间。"""
    identities = tuple(dict.fromkeys(row["supply_id"] for row in _reported_supply_rows(recent)))
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


def _current_supply_facts(current: list[dict], recent: list[dict]) -> list[dict]:
    """@brief 当前供给用完整行或引用本次回灌的差异事实表达，空值按值替换。"""
    historical = {}
    for result in recent:
        for row in _reported_supply_rows([result]):
            historical[row["supply_id"]] = (result["action_id"], public_supply_row(row))
    facts = []
    for row in current:
        if row["supply_id"] not in historical:
            facts.append(row)
            continue
        action_id, previous = historical[row["supply_id"]]
        changes = {key: value for key, value in row.items() if key not in previous or value != previous[key]}
        if changes:
            facts.append({"supply_id": row["supply_id"], "based_on_action_id": action_id, "changes": changes})
    return facts


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


def _presentation_stamps(state, agent_id: int, window: PresentationWindow) -> dict:
    """@brief 计算本次呈现要写入 `presentation_state` 的留痕（按人物与对象标识记时刻）。

    轮转的判据是「上次呈现时刻」，所以每次呈现都要留痕；留痕是新的派生量取值，但**不
    允许在观察边界直接落盘**——`store._validate_state` 只允许同版本追加事件，因此引擎
    把结果暂存到本属性，在下一个状态提交（决策轮或时钟推进）里随世界状态一起落盘。这
    里不改目录、不改对象状态、不产生下架事件（docs/dev/SPEC-life-runtime.md「曝光轮转」
    「禁止永久退役」）。

    @param state 当前世界状态。
    @param agent_id 被呈现的对象所属人物。
    @param window 本次呈现窗口。
    @return 写入后的 `presentation_state` 取值；本次没有呈现任何对象时返回空字典。
    """
    if not window.presented:
        return {}
    recorded = dict(state.presentation_state.get(agent_id, {}))
    for object_id in window.presented:
        recorded[object_id] = int(state.current_time)
    return {**state.presentation_state, agent_id: recorded}


def _stage_presentation(state, agent_id: int, window: PresentationWindow):
    """@brief 把本次呈现的留痕写进状态副本（下钻在提交路径上，故可当场落盘）。

    @param state 本次提交的世界状态。
    @param agent_id 被呈现的对象所属人物。
    @param window 本次呈现窗口。
    @return 写入留痕后的世界状态副本。
    """
    stamps = _presentation_stamps(state, agent_id, window)
    return state if not stamps else state.model_copy(
        update={"presentation_state": stamps}, deep=True)
