"""@brief 组合生活领域的封闭目录查找和活动受理规则。"""

from __future__ import annotations

from pathlib import Path
import logging

from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.catalog.models import App, Catalog, Content, WorkTopic
from worldmock.life.content import load_content_titles
from worldmock.life.activities import require_location_kinds
from worldmock.life.retrieval import item_kinds
from worldmock.life.models import ActivityKind, ActivityStartParams, AgentState
from worldmock.life.scenario import RuntimeScenario
from worldmock.life.travel import plan_trip, travel_activity_data
from worldmock.life.work import validate_work_start


class LifeRuleEngine:
    """@brief 使用有类型 Python 函数组装状态转换，不实现通用表达式语言。"""

    def __init__(self, scenario: RuntimeScenario, catalog: Catalog, corpus_root: Path):
        """@brief 绑定已经全量校验的场景和内容目录。"""
        self.scenario = scenario
        self.graph = catalog.graph
        self.corpus_root = Path(corpus_root).resolve()
        self.locations = {item.location_id: item for item in scenario.locations}
        self.activity_location_kinds = item_kinds(self.corpus_root)
        self.messages = {item.message_id: item for item in scenario.messages}
        self.foods = catalog.foods
        self.contents = catalog.contents
        self.apps = catalog.apps
        self.work_topics = catalog.work_topics
        self.contents_by_app = catalog.contents_by_app
        self.content_titles = load_content_titles(tuple(catalog.contents.values()))

    def validate_start(self, agent: AgentState, request: ActivityStartParams, now: int,
                       present: str | None) -> dict:
        """@brief 校验活动目标并返回结算所需的冻结身份；位置前置条件附带前往建议（可读地点名）。

        :param present: 人物的现场地点，途中（有进行中出行）为 None（docs/dev/SPEC-life-runtime.md#time-and-energy-决策不耗时时间只在做事时流动 第五章「并行准入」）
        。
        """
        kind = request.activity_type
        self.validate_activity_location(agent, kind, request.target_id, present)
        if kind == ActivityKind.WORK:
            return self._validate_work(agent, request, present)
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

    def _validate_work(self, agent: AgentState, request: ActivityStartParams, present: str | None) -> dict:
        """@brief 主题必须分配给本人且人在工作地点；不以时刻、技能或叙述决定资格。"""
        topic = self.work_topic(request.target_id, agent.agent_id)
        config = next(item for item in self.scenario.agents if item.agent_id == agent.agent_id)
        try:
            validate_work_start(config, topic, present)
        except PreconditionError as exc:
            if "required_location_id" in exc.unmet:
                exc.suggested = {"travel_to": self.locations[config.work_schedule.workplace_location_id].name}
            raise
        return {"topic_id": topic.topic_id}

    def validate_activity_location(self, agent: AgentState, kind: ActivityKind, target_id: str | None,
                                   present: str | None) -> None:
        """@brief 启动和恢复生活活动共用当前地图的活动环境资格判断；途中（present 为 None）不在任何地点。"""
        if kind != ActivityKind.LIFESTYLE:
            return
        if target_id not in self.activity_location_kinds:
            logging.getLogger(__name__).error("Lifestyle activity is not in the frozen corpus: %s", target_id)
            raise BusinessRuleError("LIFESTYLE_ACTIVITY_NOT_FOUND")
        location = self.locations.get(present) if present is not None else None
        place_kinds = frozenset({location.kind.value}) if location is not None else frozenset()
        require_location_kinds(place_kinds, self.activity_location_kinds[target_id])


    def work_topic(self, topic_id: str | None, agent_id: int | None = None) -> WorkTopic:
        """@brief 取得真实登记且在指定人物分配内的主题，未知或未分配明确拒绝。"""
        if topic_id not in self.work_topics:
            logging.error("Work topic is not registered: %s", topic_id)
            raise BusinessRuleError("WORK_TOPIC_NOT_FOUND")
        if agent_id is not None:
            config = next(item for item in self.scenario.agents if item.agent_id == agent_id)
            if topic_id not in config.work_topic_ids:
                logging.error("Work topic is not assigned to this agent: %s", topic_id)
                raise BusinessRuleError("WORK_TOPIC_NOT_FOUND")
        return self.work_topics[topic_id]

    def content(self, content_id: str) -> Content:
        """@brief 取得一个已经登记的内容。"""
        return self.contents[content_id]

    def app(self, app_id: str | None) -> App:
        """@brief 取得一个已经登记的 app，否则明确拒绝。"""
        if app_id not in self.apps:
            logging.error("Selected app is not registered: %s", app_id)
            raise BusinessRuleError("TARGET_NOT_FOUND")
        return self.apps[app_id]
