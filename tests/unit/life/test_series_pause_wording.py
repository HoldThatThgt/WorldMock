"""离这一集结束不足三分钟时的暂停拒绝：做完这一集还会接下一集的在地点做的事用续接的三句，不再接续的照旧用「快做完了」
的三句；判定与系列接续同一个 content_select.next_episode，轮数取当前这一集此刻的完成轮数加一。

准入上下文的 inputs 是 meal_environment 副本经 load_environment 读出的真实环境输入；世界与活动是手工造的，当前时刻
100，活动离计划结束 179 秒。
"""

from __future__ import annotations

import pytest

from tests.frozen_inputs import fixture_metadata
from tests.life.environment_inputs import meal_environment
from tests.unit.test_environment_engine_admission import occupied, running
from worldmock.life.activity_control import open_activities
from worldmock.environment import load_environment
from worldmock.life import activity_control
from worldmock.life.admission import Context
from worldmock.life.guidance import Refusal
from worldmock.life.models import ActivityKind, ContentProgress

_NAME = "进行做晚饭（1月5日 08:00 起）"
#: 会接下一集时的三句。
CONTINUING = Refusal("ACTIVITY_PAUSE_UNAVAILABLE",
                     f"{_NAME} cannot be paused while its current episode ends in under three minutes; "
                     "it then continues with the next episode.",
                     f"Conclude {_NAME}, or keep it running.",
                     fix="Conclude this activity or keep it running instead.")
#: 不再接续时今天的三句。
ENDING = Refusal("ACTIVITY_PAUSE_UNAVAILABLE", f"The activity {_NAME} ends in under three minutes and cannot be paused.",
                 f"Conclude {_NAME} or let it finish.", fix="Conclude this activity or let it finish instead.")


@pytest.fixture
def inputs(tmp_path):
    """meal_environment 副本的真实环境输入。"""
    return load_environment(meal_environment(tmp_path / "meal"), metadata_loader=fixture_metadata)


def _pause_refusal(inputs, content_id: str, rounds: dict[str, int]):
    """当前内容是给定内容、离这一集计划结束 179 秒的在地点做的事，人物各条内容的完成轮数如给定；点名它暂停的准入结论。"""
    activity = running("interact", ActivityKind.INTERACT, "first", presence=True)
    activity = activity.model_copy(update={"planned_end_at": 100 + 179, "planned_duration_seconds": 179,
                                           "data": {**activity.data, "content_id": content_id}})
    state = occupied(activity)
    progress = {key: ContentProgress(content_id=key, position_seconds=0.0, completed_rounds=value, last_consumed_at=1)
                for key, value in rounds.items()}
    state.agents[1] = state.agents[1].model_copy(update={"content_progress": progress}, deep=True)
    assert not activity_control.pausable(state, activity)
    context = Context(state, 1, open_activities(state, 1), {"home": "Home"}, {activity.activity_id: _NAME}, inputs)
    return activity_control.control_refusal(context, "pause_activity", activity)


@pytest.mark.parametrize("content_id,rounds", [
    ("dinner-1-cook", {}),
    ("dinner-2-eat", {"dinner-1-cook": 1}),
    ("dinner-1-cook", {"dinner-1-cook": 1, "dinner-2-eat": 1, "dinner-3-wash": 1}),
], ids=["first-episode", "middle-episode", "second-round"])
def test_a_location_series_with_a_next_episode_gets_the_continuing_refusal(inputs, content_id, rounds):
    """第一集（没有进度记录）、第二集（第一集轮数 1）、第二轮的第一集（三集轮数都是 1，按 2 去找，第二集轮数 1 小于 2）：
    做完这一集还会接下一集，拒绝是续接的三句。"""
    assert _pause_refusal(inputs, content_id, rounds) == CONTINUING


@pytest.mark.parametrize("content_id,rounds", [
    ("dinner-3-wash", {"dinner-1-cook": 1, "dinner-2-eat": 1}),
    ("c-rest", {}),
    ("dinner-1-cook", {"dinner-2-eat": 1, "dinner-3-wash": 1}),
], ids=["last-episode", "standalone-unit", "later-episodes-ahead"])
def test_an_activity_that_ends_keeps_the_ending_refusal(inputs, content_id, rounds):
    """系列最后一集（前两集轮数 1）、独立的「测试休息」、后面各集轮数都不低于这个数的系列（第一集没有进度记录，按 1
    去找，第二、三集轮数都是 1，不小于 1）：做完就结束，拒绝仍是今天的三句。最后一种钉住轮数恰是此刻的完成轮数加一，
    多加一轮就会把第二集当成下一集。"""
    assert _pause_refusal(inputs, content_id, rounds) == ENDING
