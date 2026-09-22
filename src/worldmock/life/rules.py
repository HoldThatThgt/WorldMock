"""@brief 组合生活领域的封闭目录查找和活动受理规则。"""

from __future__ import annotations

from pathlib import Path
import logging

from worldmock.errors import BusinessRuleError, PreconditionError
from worldmock.life.content import SubtitleCue, load_content_titles, load_subtitles
from worldmock.life.activities import require_location_kinds
from worldmock.life.retrieval import item_kinds
from worldmock.life.models import ActivityKind, ActivityStartParams, AgentState
from worldmock.life.scenario import (
    ContentItemConfig,
    GameConfig,
    InventoryItemConfig,
    LifeScenario,
    WorkTaskConfig,
)
from worldmock.life.travel import route_for
from worldmock.life.work import validate_work_start


class LifeRuleEngine:
    """@brief 使用有类型 Python 函数组装状态转换，不实现通用表达式语言。"""

    def __init__(self, scenario: LifeScenario, corpus_root: Path):
        """@brief 绑定已经全量校验的场景和内容目录。"""
        self.scenario = scenario
        self.corpus_root = Path(corpus_root).resolve()
        self.products = {item.product_id: item for item in scenario.products}
        self.items = {item.product_id: item
                      for item in (*scenario.products, *scenario.inventory_items)}
        self.supplies = {item.supply_id: item for item in scenario.supplies}
        self.locations = {item.location_id: item for item in scenario.locations}
        self.activity_location_kinds = item_kinds(self.corpus_root)
        self.tasks = {item.task_id: item for item in scenario.tasks}
        self.messages = {item.message_id: item for item in scenario.messages}
        self.contents = {item.item_id: item for item in scenario.contents}
        self.games = {item.game_id: item for item in scenario.games}
        self.subtitles = {item.item_id: load_subtitles(item, self.corpus_root)
                          for item in scenario.contents}
        self.content_titles = load_content_titles(scenario.contents, self.corpus_root)

    def validate_start(self, agent: AgentState, request: ActivityStartParams, now: int) -> dict:
        """@brief 校验活动目标并返回结算所需的冻结身份；位置前置条件附带前往建议。"""
        kind = request.activity_type
        self.validate_activity_location(agent, kind, request.target_id)
        if kind == ActivityKind.WORK:
            task = self.task(request.target_id)
            try:
                validate_work_start(agent, task, now)
            except PreconditionError as exc:
                if "required_location_id" in exc.unmet:
                    loc = self.locations.get(task.required_location_id)
                    exc.suggested = {
                        "action": "travel_to",
                        "destination_id": task.required_location_id,
                        "destination_name": loc.name if loc else task.required_location_id,
                    }
                raise
            if request.planned_duration_seconds > task.maximum_duration_seconds:
                raise BusinessRuleError("WORK_DURATION_EXCEEDS_CONTRACT")
            if now + request.planned_duration_seconds > int(task.due_at.timestamp()):
                raise BusinessRuleError("WORK_WOULD_MISS_DEADLINE")
            return {"task_id": task.task_id, "required_skill": task.required_skill,
                    "skill_level": agent.skill_levels[task.required_skill],
                    "work_notes_multiplier": self.scenario.body.work_notes_multiplier}
        if kind == ActivityKind.WATCH:
            item = self.content(request.target_id)
            position = agent.content_progress.get(item.item_id)
            repeat = bool(position and position.position_seconds >= item.duration_seconds)
            start = 0 if repeat or position is None else position.position_seconds
            if start + request.planned_duration_seconds * request.playback_speed > item.duration_seconds:
                raise BusinessRuleError("CONTENT_END_EXCEEDED")
            count = (position.exposure_count if position else 0) + 1
            return {"content_id": item.item_id, "repeat": repeat,
                    "start_position_seconds": start, "exposure_count": count,
                    "entertainment_relief_per_hour": item.entertainment_relief_per_hour}
        if kind == ActivityKind.PLAY_GAME:
            return {"game_id": self.game(request.target_id).game_id}
        if kind == ActivityKind.READ_MAIL:
            if request.target_id not in agent.visible_message_ids:
                raise BusinessRuleError("MESSAGE_NOT_VISIBLE")
            return {"message_id": request.target_id}
        if kind == ActivityKind.BROWSE:
            if request.target_id != "product_catalog":
                raise BusinessRuleError("BROWSE_TARGET_NOT_FOUND")
            return {"catalog": "all_products"}
        if kind == ActivityKind.TRAVEL:
            return {"route_id": route_for(self.scenario, agent.location_id, request).route_id}
        return {}

    def validate_activity_location(self, agent: AgentState, kind: ActivityKind, target_id: str | None) -> None:
        """@brief 启动和恢复生活活动共用当前地图的活动环境资格判断。"""
        if kind != ActivityKind.LIFESTYLE:
            return
        if target_id not in self.activity_location_kinds:
            logging.getLogger(__name__).error("Lifestyle activity is not in the frozen corpus: %s", target_id)
            raise BusinessRuleError("LIFESTYLE_ACTIVITY_NOT_FOUND")
        location = self.locations[agent.location_id]
        require_location_kinds(frozenset(location.location_kinds), self.activity_location_kinds[target_id])

    def product(self, product_id: str | None) -> InventoryItemConfig:
        """@brief 取得一个冻结可持有对象，否则业务拒绝。"""
        if product_id not in self.items:
            raise BusinessRuleError("PRODUCT_NOT_FOUND")
        return self.items[product_id]

    def task(self, task_id: str | None) -> WorkTaskConfig:
        """@brief 取得一个冻结工作任务，否则业务拒绝。"""
        if task_id not in self.tasks:
            raise BusinessRuleError("WORK_TASK_NOT_FOUND")
        return self.tasks[task_id]

    def content(self, item_id: str | None) -> ContentItemConfig:
        """@brief 取得一个冻结内容项目，否则业务拒绝。"""
        if item_id not in self.contents:
            raise BusinessRuleError("CONTENT_NOT_FOUND")
        return self.contents[item_id]

    def game(self, game_id: str | None) -> GameConfig:
        """@brief 取得一个冻结 TextWorld 游戏，否则业务拒绝。"""
        if game_id not in self.games:
            raise BusinessRuleError("GAME_NOT_FOUND")
        return self.games[game_id]

    def cues(self, item_id: str) -> tuple[SubtitleCue, ...]:
        """@brief 取得内容真实字幕时间轴。"""
        if item_id not in self.subtitles:
            raise BusinessRuleError("CONTENT_TIMELINE_NOT_FOUND")
        return self.subtitles[item_id]
