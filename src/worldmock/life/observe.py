"""@brief LifeEngine 的观察投影与浏览/动机上下文装配（Mixin 层）。

本模块从 engine.py 下沉（生产文件 ≤2000 行规范）：包含两层观察组装
（scene + facts + 事件级工具支持行）、活动品类摘要、browse_activities
下钻、内部动机仪表上下文与决策参照快照。engine 侧以 Mixin 继承接线，
`self` 即 LifeEngine；行为合同与注释见修订合同观察投影一节（L1529-1548）。
"""

from __future__ import annotations

import json
from typing import Any

from worldmock.life import felt, projection
from worldmock.life.models import ActivityStatus, BrowseActivitiesRequest, ContentProgress
from worldmock.life.motivation import MotivationContext, evaluate_motivation
from worldmock.life.body import maximum_energy_duration_seconds, minimum_energy_cost
from worldmock.life.models import ActivityKind
from worldmock.life.work import work_totals


class ObservationMixin:
    """@brief 观察投影、浏览与动机仪表的引擎装配方法（self 为 LifeEngine）。"""

    def _observation_data(self, agent) -> dict:
        """@brief 两层观察投影：scene 场景输入 + facts 精简结构 + body 数值证据。

        黑名单字段不进 scene/facts 提示路径；事件级工具支持行（完整任务/路线/
        商品、能量可行性、可供性合同、游戏目录）供宿主工具 Schema 使用。
        """
        hour = projection.hour_of_day(self.state.current_time, self.scenario.timezone)
        config = self._agent_config(agent.agent_id)
        meal_windows = felt.MealWindows(lunch=config.lunch_window, dinner=config.dinner_window)
        recent = self._recent_action_results(agent.agent_id)
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
                "product_rows": self._product_rows(agent),
                "activity_rows": self._activity_rows(agent),
            })
        data = projection.build_observation(inputs)
        data["paused_activities"] = [
            _paused_activity_observation(item, agent, self.semantics.body) for item in sorted(
                self.state.activities.values(), key=lambda value: value.activity_id)
            if item.agent_id == agent.agent_id and item.status == ActivityStatus.PAUSED]
        data["wake_reasons"] = list(agent.pending_wake_reasons)
        data["location_id"] = agent.location_id
        data["hour_of_day"] = hour
        # 事件级工具支持与既有目录投影（不进 scene/facts 提示路径，L1019/L1542）。
        data["content_progress"] = self._content_progress(agent)
        data["games"] = {key: value.model_dump(mode="json")
                         for key, value in agent.game_sessions.items()}
        data["work_progress"] = self._work_progress_rows(agent)
        data["messages"] = _mail_headers(agent, self.rules.messages)
        data["energy_feasibility"] = _activity_energy_feasibility(
            agent, self.semantics.body, self._affordance_contract)
        data["affordance_contract"] = self._affordance_contract
        data["products"] = [self._visible_product(item) for item in sorted(
            projection.visible_products(self.state, agent, self.scenario))]
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
        data["tasks"] = [self._visible_task(item, agent.agent_id)
                         for item in sorted(agent.visible_task_ids)
                         if item not in self.state.paid_task_ids
                         and self.state.current_time <= int(
                             self.rules.tasks[item].due_at.timestamp())]
        data["routes"] = _route_catalog(self.scenario.routes, self.semantics.body,
                                        agent.location_id, agent.body.energy_kcal,
                                        self.state.current_time, self.semantics)
        data["game_catalog"] = _game_catalog(self.scenario)
        data["browse_targets"] = ["product_catalog"]
        data["calendar_context"] = _calendar_context(self.scenario, self.state.current_time)
        data["locations"] = _location_catalog(self.scenario)
        return data

    def _agent_config(self, agent_id: int):
        """@brief 读取人物场景配置（档案字段与联系人声明）。"""
        return next(item for item in self.scenario.agents if item.agent_id == agent_id)

    def _work_progress_rows(self, agent) -> list[dict]:
        """@brief 工作目录：已投入/有效/合同剩余工时（规格 L1019 既有口径）。"""
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
        """@brief facts 路线行：目的地/方式/分钟/票价/可行（五键白名单）。"""
        rows = []
        for route in self.scenario.routes:
            if route.origin_id != agent.location_id:
                continue
            planned = self.semantics.travel_planned_seconds(
                route.mode.value, route.duration_seconds, self.state.current_time)
            met = self.semantics.body.activity_met[f"travel:{route.mode.value}"]
            cost = minimum_energy_cost(ActivityKind.TRAVEL, planned, self.semantics.body, met)
            rows.append({"destination": route.destination_id, "mode": route.mode.value,
                         "minutes": round(planned / 60), "fare": route.fare_fen,
                         "feasible": cost <= agent.body.energy_kcal})
        return rows

    def _reachable_places(self, agent) -> list[dict]:
        """@brief scene 可达地点行：名称/营业状态/约分钟。"""
        open_hour = projection.hour_of_day(self.state.current_time, self.scenario.timezone)
        rows = []
        for route in self.scenario.routes:
            if route.origin_id != agent.location_id:
                continue
            location = next(item for item in self.scenario.locations
                            if item.location_id == route.destination_id)
            planned = self.semantics.travel_planned_seconds(
                route.mode.value, route.duration_seconds, self.state.current_time)
            rows.append({"name": location.name,
                         "open": location.open_hour <= open_hour < location.close_hour,
                         "minutes": round(planned / 60)})
        return rows

    def _product_rows(self, agent) -> list[dict]:
        """@brief facts 商品行：仅有效可见集，每件五键（L1537/L1545）。"""
        rows = []
        for product_id in projection.visible_products(self.state, agent, self.scenario):
            product = self.rules.products[product_id]
            rows.append({"product_id": product.product_id, "name": product.name,
                         "price_fen": product.price_fen,
                         "energy_kcal_per_unit": product.energy_kcal_per_unit,
                         "use_duration_seconds": product.use_duration_seconds})
        return rows

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
        """@brief 商店存在性常识（事件级，不进 scene/facts 提示）：营业/在售数/价格区间。"""
        from worldmock.life.engine import _datetime

        products = self.rules.products
        if not products:
            return None
        hour = _datetime(self.state.current_time, self.scenario.timezone).hour
        open_now = any(
            location.open_hour <= hour < location.close_hour
            for location in self.scenario.locations
            if location.location_id in {item.merchant_location_id for item in products.values()})
        prices = [item.price_fen for item in products.values()]
        return {"open": open_now, "product_count": len(products),
                "price_range_fen": [min(prices), max(prices)]}

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
        """@brief 返回商品公开合同和权威当前库存，不暴露初始库存。"""
        data = self.rules.products[product_id].model_dump(mode="json", exclude={"initial_stock"})
        data["current_stock"] = self.state.merchant_stock[product_id]
        return data

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

        修订合同 L1594-1596：评分器继续计算供评估与研究，但候选、名次与概率
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
        """@brief 从当前世界装配动机求值输入切片（全部只读）。

        修订合同三接线（L1598-1599/L1646）：taste_weights 为确定性口味权重
        唯一源——场景合同尚未持久化 PersonaCard（只存 MotivationProfile），
        引擎路径暂为空映射（motivation.py 文档口径：空=不抬升）；卡片入库后
        改为 compute_taste_weights(card.tastes.content_genres, contents)。
        lifestyle_enabled 直通人物启用集声明；recent_meal_counts 按足迹
        action_requested 的进食请求聚合近 24 小时各商品次数。
        """
        from worldmock.life.models import ActivityKind, ActivityStatus
        from worldmock.life.work import work_totals as totals
        from worldmock.life.engine import _datetime

        now = self.state.current_time
        config = self._agent_config(agent.agent_id)
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
        return MotivationContext(
            agent=agent,
            balance_fen=self.state.accounts[agent.balance_account_id],
            body=self.semantics.body,
            home_location_id=next(item.home_location_id for item in self.scenario.agents
                                  if item.agent_id == agent.agent_id),
            products=tuple(self.rules.products[item] for item in sorted(agent.visible_product_ids)),
            merchant_stock=self.state.merchant_stock,
            locations={item.location_id: item for item in self.scenario.locations},
            routes=self.scenario.routes,
            tasks=tuple(tasks),
            task_totals={task.task_id: totals(self.state, agent.agent_id, task.task_id)
                         for task in tasks},
            contents=_sample_contents(self.scenario.contents, profile, {}),
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
            planned_travel_seconds=lambda mode, base: self.semantics.travel_planned_seconds(
                mode, base, now),
            catalog_products=tuple(self.rules.products[item] for item in sorted(self.rules.products)),
            content_segments={item_id: tuple(cue.end_seconds for cue in cues)
                              for item_id, cues in self.rules.subtitles.items()},
            taste_weights={},
            lifestyle_enabled=config.lifestyle_activities,
            recent_meal_counts=_recent_meal_counts(requested, now))

    def _activity_summary(self, agent) -> dict[str, int]:
        """@brief 一行品类计数：观察只报各活动品类的在场数量，明细由下钻读取。"""
        summary: dict[str, int] = {}
        for item in self.scenario.contents:
            summary[item.kind] = summary.get(item.kind, 0) + 1
        for entry in self._enabled_lifestyle_items(agent):
            summary[entry.category] = summary.get(entry.category, 0) + 1
        if self.scenario.products:
            summary["foods"] = len(self.scenario.products)
        return dict(sorted(summary.items()))

    def _enabled_lifestyle_items(self, agent) -> tuple:
        """@brief 返回人物启用声明过滤后的生活活动目录条目（None=全局启用）。"""
        config = next(item for item in self.scenario.agents if item.agent_id == agent.agent_id)
        enabled = config.lifestyle_activities
        if enabled is None:
            return self.lifestyle_items
        allowed = set(enabled)
        return tuple(item for item in self.lifestyle_items if item.activity_id in allowed)

    def _browse_activities(self, state, pending, request: BrowseActivitiesRequest):
        """@brief 下钻读取一个活动品类目录：纯查询，不启动活动、不消耗内容。"""
        from worldmock.errors import BusinessRuleError
        from worldmock.life.models import ActionStatus, ActionResult, LIFESTYLE_TYPE_PREFIX

        agent = state.agents[pending.agent_id]
        category = request.category
        if category not in self._activity_summary(agent):
            raise BusinessRuleError("BROWSE_CATEGORY_NOT_FOUND")
        content_kinds = {item.kind for item in self.scenario.contents}
        if category in content_kinds:
            items = [row for row in _content_catalog(
                        self.scenario, self.rules.subtitles, self.rules.content_titles,
                        agent.content_progress)
                     if row["kind"] == category]
            note = "watch items accept start_targeted_activity with activity_type watch"
        elif category == "foods":
            items = [{"product_id": product.product_id,
                      "price_fen": product.price_fen,
                      "use_modes": list(product.use_modes),
                      "use_duration_seconds": product.use_duration_seconds,
                      "merchant_location_id": product.merchant_location_id}
                     for product in sorted(self.scenario.products,
                                           key=lambda item: item.product_id)]
            note = "menu-level view only; purchase contracts need the timed product catalog browse"
        else:
            items = [{"activity_id": entry.activity_id,
                      "activity_type": f"{LIFESTYLE_TYPE_PREFIX}{entry.activity_id}",
                      "name": entry.name, "category": entry.category,
                      "typical_duration_minutes": entry.typical_duration_minutes,
                      "description": entry.description, "met_value": entry.met_value}
                     for entry in sorted(self._enabled_lifestyle_items(agent),
                                         key=lambda item: item.activity_id)
                     if entry.category == category]
            note = "lifestyle items accept start_basic_activity with activity_type lifestyle:<activity_id>"
        result = ActionResult(action_id=pending.action_id, agent_id=agent.agent_id,
                              status=ActionStatus.COMPLETED, occurred_at=state.current_time,
                              message="Activity menu browsed",
                              data={"tool": "browse_activities", "category": category,
                                    "count": len(items), "note": note, "items": items})
        return state, result

    def _snapshot_decision_references(self, staged, agent_ids: list[int]):
        """@brief 决策落定后更新滞回参照：分级/在场人物/已知任务（L1524-1525）。"""
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


def _route_catalog(routes: tuple, body, origin_id: str, energy_kcal: float,
                   departure_second: int, semantics) -> list[dict]:
    """@brief 返回人物当前位置出发的冻结路线身份和出发时刻报价（事件级）。"""
    fields = {"route_id", "origin_id", "destination_id", "mode", "distance_meters", "duration_seconds",
              "fare_fen", "fare_source_kind"}
    result = []
    for item in routes:
        if item.origin_id != origin_id:
            continue
        planned = semantics.travel_planned_seconds(
            item.mode.value, item.duration_seconds, departure_second)
        data = item.model_dump(mode="json", include=fields)
        data.update(duration_seconds=planned,
                    free_flow_duration_seconds=item.duration_seconds,
                    travel_time_factor=semantics.travel_time_factor(
                        item.mode.value, departure_second))
        met = body.activity_met[f"travel:{item.mode.value}"]
        cost = minimum_energy_cost(ActivityKind.TRAVEL, planned, body, met)
        data.update(minimum_energy_cost_kcal=cost, energy_feasible=cost <= energy_kcal)
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
    fields = {"location_id", "name", "kind", "longitude", "latitude", "open_hour", "close_hour"}
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
    """@brief 近 24 小时各商品已进食次数（product_id -> n，修订合同 L1599）。

    从足迹 action_requested 的 consume 请求聚合（use_mode=eat）：每件按
    数量累计（一次吃两份计 2 次，Rolls 1981 感官特异性饱足按食用次数降权）。
    24 小时窗口为规格常数（L1599"近 24 小时"）。
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

    权重唯一源是 taste_weights（确定性，修订合同 L1661-1662）；空映射时
    退化为按品类注意力排序（稳定 item_id 决胜），与评分器的零抬升口径一致。
    """

    def weight(item) -> float:
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
