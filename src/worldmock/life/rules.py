"""@brief 组合冻结目录与系统活动受理规则；数据集动作使用统一执行器。"""

from __future__ import annotations

import logging

from worldmock.errors import BusinessRuleError
from worldmock.catalog.models import App, Catalog, Location
from worldmock.exchange.types import ActionCategory, PLACE_KIND_ENVIRONMENTS
from worldmock.life.guidance import Refusal
from worldmock.life.content import load_content_titles
from worldmock.life.models import ActivityKind, ActivityStartParams, AgentState
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.travel import plan_trip, travel_activity_data


class LifeRuleEngine:
    """@brief 使用有类型 Python 函数组装状态转换，不实现通用表达式语言。"""

    def __init__(self, scenario: RuntimeScenario, catalog: Catalog):
        """@brief 绑定已全量校验的场景和目录。"""
        self.scenario = scenario
        self.graph = catalog.graph
        self.locations = {item.location_id: item for item in scenario.locations}
        self.messages = {item.message_id: item for item in scenario.messages}
        self.goods = catalog.goods
        self.contents = catalog.contents
        self.apps = catalog.apps
        self.actions = catalog.actions
        self.contents_by_action_app = catalog.contents_by_action_app
        self.content_titles = load_content_titles(tuple(catalog.contents.values()))

    def validate_start(self, agent: AgentState, request: ActivityStartParams, now: int,
                       present: str | None) -> dict:
        """@brief 系统活动只冻结真实路线身份，其余活动没有内容目标。"""
        if request.activity_type == ActivityKind.TRAVEL:
            return {**travel_activity_data(plan_trip(self.graph, agent.location_id,
                request.destination_id, request.travel_mode)), "navigation_app_id": request.navigation_app_id}
        return {}

    def action_location_refusal(self, category: ActionCategory | str, present: str | None) -> Refusal | None:
        """@brief 工具层与开始受理共用运动地点的同一固定判定。"""
        return action_location_refusal(category, self.locations.get(present))

    def app(self, app_id: str | None) -> App:
        """@brief 取得登记 app，未知身份明确拒绝。"""
        if app_id not in self.apps:
            logging.error("Selected app is not registered: %s", app_id)
            raise BusinessRuleError("TARGET_NOT_FOUND")
        return self.apps[app_id]


def action_location_refusal(category: ActionCategory | str, location: Location | None) -> Refusal | None:
    """@brief 运动只在固定 home、outdoor 或 fitness 环境进行，其他类别不受地点类型限制。"""
    if category != ActionCategory.EXERCISE:
        return None
    allowed = location is not None and PLACE_KIND_ENVIRONMENTS[location.kind] in {"home", "outdoor", "fitness"}
    if allowed:
        return None
    return Refusal("ELIGIBILITY_LOCATION_KIND_MISMATCH",
                   "The current location category does not admit this activity.", None)
