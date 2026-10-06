"""@brief 组合生活领域的封闭目录查找和活动受理规则。"""

from __future__ import annotations

from pathlib import Path
import logging

from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.catalog.graph import Graph
from worldmock.catalog.models import App, Content
from worldmock.life.content import load_content_titles
from worldmock.life.activities import require_location_kinds
from worldmock.life.retrieval import item_kinds
from worldmock.life.models import ActivityKind, ActivityStartParams, AgentState
from worldmock.life.scenario import (
    LifeScenario,
    RuntimeWorkTask,
)
from worldmock.life.travel import plan_trip, travel_activity_data
from worldmock.life.work import validate_work_start


class LifeRuleEngine:
    """@brief 使用有类型 Python 函数组装状态转换，不实现通用表达式语言。"""

    def __init__(self, scenario: LifeScenario, corpus_root: Path, graph: Graph):
        """@brief 绑定已经全量校验的场景和内容目录。"""
        self.scenario = scenario
        self.graph = graph
        self.corpus_root = Path(corpus_root).resolve()
        self.locations = {item.location_id: item for item in scenario.locations}
        self.activity_location_kinds = item_kinds(self.corpus_root)
        self.tasks = {item.task_id: item for item in scenario.tasks}
        self.messages = {item.message_id: item for item in scenario.messages}
        self.contents = {item.content_id: item for item in scenario.contents}
        self.apps = {item.app_id: item for item in scenario.apps}
        self.contents_by_app = {app_id: tuple(sorted(item.content_id for item in scenario.contents
                                                    if app_id in item.app_ids)) for app_id in self.apps}
        self.content_titles = load_content_titles(scenario.contents)

    def validate_start(self, agent: AgentState, request: ActivityStartParams, now: int,
                       present: str | None) -> dict:
        """@brief 校验活动目标并返回结算所需的冻结身份；位置前置条件附带前往建议（可读地点名）。

        :param present: 人物的现场地点，途中（有进行中出行）为 None（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「并行准入」）
        。
        """
        kind = request.activity_type
        self.validate_activity_location(agent, kind, request.target_id, present)
        if kind == ActivityKind.WORK:
            return self._validate_work(agent, request, now, present)
        if kind == ActivityKind.USE_APP:
            app = self.app(request.target_id)
            if not self.contents_by_app[app.app_id]:
                logging.error("Selected app has no content: %s", app.app_id)
                raise BusinessRuleError("APP_CONTENT_EMPTY")
            return {"app_id": app.app_id}
        if kind == ActivityKind.READ_MAIL:
            if request.target_id not in agent.visible_message_ids:
                raise BusinessRuleError("MESSAGE_NOT_VISIBLE")
            return {"message_id": request.target_id}
        if kind == ActivityKind.TRAVEL:
            return travel_activity_data(plan_trip(self.graph, agent.location_id,
                                                  request.destination_id, request.travel_mode))
        return {}

    def _validate_work(self, agent: AgentState, request: ActivityStartParams, now: int, present: str | None) -> dict:
        """@brief 开工校验：可见、有效期内、技能达标且人在指定地点；位置不符时建议前往该地（只写地点名）。"""
        task = self.task(request.target_id)
        try:
            validate_work_start(agent, task, now, present)
        except PreconditionError as exc:
            if "required_location_id" in exc.unmet:
                loc = self.locations.get(task.required_location_id)
                exc.suggested = {"travel_to": loc.name if loc else "指定地点"}
            raise
        if request.planned_duration_seconds > task.maximum_duration_seconds:
            raise BusinessRuleError("WORK_DURATION_EXCEEDS_CONTRACT")
        if now + request.planned_duration_seconds > int(task.due_at.timestamp()):
            raise BusinessRuleError("WORK_WOULD_MISS_DEADLINE")
        return {"task_id": task.task_id, "required_skill": task.required_skill,
                "skill_level": agent.skill_levels[task.required_skill],
                "work_notes_multiplier": self.scenario.body.work_notes_multiplier}

    def validate_activity_location(self, agent: AgentState, kind: ActivityKind, target_id: str | None,
                                   present: str | None) -> None:
        """@brief 启动和恢复生活活动共用当前地图的活动环境资格判断；途中（present 为 None）不在任何地点。"""
        if kind != ActivityKind.LIFESTYLE:
            return
        if target_id not in self.activity_location_kinds:
            logging.getLogger(__name__).error("Lifestyle activity is not in the frozen corpus: %s", target_id)
            raise BusinessRuleError("LIFESTYLE_ACTIVITY_NOT_FOUND")
        location = self.locations.get(present) if present is not None else None
        place_kinds = frozenset(location.location_kinds) if location is not None else frozenset()
        require_location_kinds(place_kinds, self.activity_location_kinds[target_id])


    def task(self, task_id: str | None) -> RuntimeWorkTask:
        """@brief 取得一个冻结工作任务，否则业务拒绝。"""
        if task_id not in self.tasks:
            raise BusinessRuleError("WORK_TASK_NOT_FOUND")
        return self.tasks[task_id]

    def content(self, content_id: str) -> Content:
        """@brief 取得一个已经登记的内容。"""
        return self.contents[content_id]

    def app(self, app_id: str | None) -> App:
        """@brief 取得一个已经登记的 app，否则明确拒绝。"""
        if app_id not in self.apps:
            logging.error("Selected app is not registered: %s", app_id)
            raise BusinessRuleError("TARGET_NOT_FOUND")
        return self.apps[app_id]
