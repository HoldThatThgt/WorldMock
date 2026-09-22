"""交互菜单的品类映射、恢复边界与候选不变式反例。"""

from __future__ import annotations

from pathlib import Path

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (
    advance_until_ready,
    context,
    stage_day_plan,
    submit_round,
    wake_by_mail,
)
from tests.life.test_commerce import _observed_action
from tests.life.test_interaction_menu_runtime import _engine, _restrict, _travel_to_office
from worldmock.catalog.affordances import default_affordances
from worldmock.footprints.models import EventDraft
from worldmock.life.interaction_menu import InteractionMenu, menu_allows_category
from worldmock.life.models import (
    ActivityStatus,
    BrowseActivitiesRequest,
    SetActivityStateRequest,
    StartActivityRequest,
    StartSocialActivityRequest,
)
from worldmock.life.scenario import load_scenario


def _without(
    menu: InteractionMenu,
    *,
    tool: str | None = None,
    kind: str | None = None,
) -> InteractionMenu:
    """从有效完整菜单移除一个非保护资格。"""
    return InteractionMenu(
        allowed_tools=tuple(name for name in menu.allowed_tools if name != tool),
        allowed_activity_kinds=tuple(name for name in menu.allowed_activity_kinds if name != kind),
    )


def test_every_registry_content_and_lifestyle_category_uses_its_start_qualification() -> None:
    """注册表当前全部品类都服从内容、游戏或生活活动的冻结映射。"""
    scenario, _ = load_scenario(Path("scenarios/white-collar-12-weeks.json"))
    full = scenario.interaction_menus_by_location_kind["home"]
    registry = default_affordances()
    content = {
        item.name
        for item in registry.families["entertainment"].content_kinds
        if item.name != "game_session"
    }
    lifestyle = {item.name for item in registry.families["lifestyle"].content_kinds}

    assert content and lifestyle
    assert all(menu_allows_category(full, category) for category in content | lifestyle)
    assert menu_allows_category(full, "game_session")

    no_watch = _without(full, kind="watch")
    assert all(not menu_allows_category(no_watch, category) for category in content)
    assert menu_allows_category(no_watch, "game_session")

    no_game = _without(full, kind="play_game")
    assert not menu_allows_category(no_game, "game_session")
    assert all(menu_allows_category(no_game, category) for category in content)

    no_targeted_start = _without(full, tool="start_activity")
    assert all(not menu_allows_category(no_targeted_start, category) for category in content)
    assert not menu_allows_category(no_targeted_start, "game_session")

    no_lifestyle = _without(full, kind="lifestyle")
    assert all(not menu_allows_category(no_lifestyle, category) for category in lifestyle)


def test_resume_rejects_missing_start_tool_even_when_activity_kind_remains_allowed(tmp_path) -> None:
    """活动类型仍允许时，撤掉对应启动工具也必须在恢复副作用前拒绝。"""
    engine = _engine(
        tmp_path,
        lambda document: _restrict(
            document,
            tools=("start_activity",),
            location="office",
        ),
    )
    try:
        stage_day_plan(engine)
        _, browsed = _observed_action(
            engine,
            "starter-tool-watch-catalog",
            "browse_activities",
            BrowseActivitiesRequest(category="subtitle_experience", cursor=None),
        )
        assert browsed.error_code is None
        assert advance_until_ready(engine)
        _, started = _observed_action(
            engine,
            "starter-tool-watch-start",
            "start_activity",
            StartActivityRequest(
                activity_type="watch",
                target_id="sintel-en-subtitles",
                planned_duration_seconds=1260,
                playback_speed=0.5,
            ),
        )
        wake_by_mail(engine)
        assert advance_until_ready(engine)
        _, paused = _observed_action(
            engine,
            "starter-tool-watch-pause",
            "set_activity_state",
            SetActivityStateRequest(activity_id=started.activity_id, operation="pause"),
        )
        assert paused.error_code is None
        assert advance_until_ready(engine)
        _travel_to_office(engine)
        assert advance_until_ready(engine)

        observation = engine.observe(context(engine, "starter-tool-office"))
        assert "watch" in observation["interaction_menu"]["allowed_activity_kinds"]
        assert "start_activity" not in observation["interaction_menu"]["allowed_tools"]
        before = engine.state.activities[started.activity_id].model_dump(mode="json")
        _, denied = _observed_action(
            engine,
            "starter-tool-watch-resume",
            "set_activity_state",
            SetActivityStateRequest(activity_id=started.activity_id, operation="resume"),
        )
        assert denied.error_code == "INTERACTION_MENU_DENIED"
        assert engine.state.activities[started.activity_id].model_dump(mode="json") == before
    finally:
        engine.close()


def _stage_paused_social_activity_at_office(engine, activity_id: str) -> None:
    """把真实创建的社交活动冻结成可恢复状态，并持久化人物的办公室位置。"""
    staged = engine.state.model_copy(deep=True)
    activity = staged.activities[activity_id]
    staged.activities[activity_id] = activity.model_copy(
        update={
            "status": ActivityStatus.PAUSED,
            "boundary_type": "paused",
            "next_interaction_at": None,
        },
        deep=True,
    )
    now = staged.current_time
    staged.agents[1] = staged.agents[1].model_copy(
        update={"current_activity_id": None, "location_id": "office", "next_decision_at": now},
        deep=True,
    )
    for agent_id in set(staged.agents) - {1}:
        staged.agents[agent_id] = staged.agents[agent_id].model_copy(
            update={"next_decision_at": now + 3600},
            deep=True,
        )
    staged.version += 1
    engine.store.commit(
        staged.model_dump(mode="json"),
        [EventDraft(
            event_type="environment_event",
            occurred_at=now,
            visibility="system",
            data={"kind": "test_social_activity_paused_at_office"},
        )],
    )
    engine.state = staged


def _direct_resume(engine, activity_id: str, round_id: str):
    """不读取暂停社交投影，直接走真实登记与共同结算边界。"""
    decision = context(engine, round_id)
    engine.begin_decision_round([decision])
    request = SetActivityStateRequest(activity_id=activity_id, operation="resume")
    engine.submit_action(DirectToolCall(decision, f"action-{round_id}", "set_activity_state", request))
    return engine.commit_decision_round(round_id)[0]


@pytest.mark.parametrize("kind", ["chat", "call"])
def test_social_resume_requires_start_social_activity_permission(tmp_path, kind: str) -> None:
    """交谈与通话的暂停恢复都只引用社交启动工具，不虚构注册表活动类型。"""
    base = "multi-person.json" if kind == "chat" else "white-collar-12-weeks.json"
    engine = _engine(
        tmp_path,
        lambda document: _restrict(
            document,
            tools=("start_social_activity",),
            location="office",
        ),
        base,
    )
    try:
        if kind == "chat":
            requests = {
                1: (
                    "start_social_activity",
                    StartSocialActivityRequest(
                        kind="chat",
                        partner_id="agent:2",
                        duration_minutes=30,
                    ),
                ),
                2: (
                    "start_activity",
                    StartActivityRequest(activity_type="wait", planned_duration_seconds=3600),
                ),
            }
            _, results = submit_round(engine, requests, "social-chat-start")
            started = next(result for result in results if result.agent_id == 1)
        else:
            _, started = _observed_action(
                engine,
                "social-call-start",
                "start_social_activity",
                StartSocialActivityRequest(
                    kind="call",
                    partner_id="contact-chenxi-mother",
                    duration_minutes=30,
                ),
            )
        assert started.error_code is None
        _stage_paused_social_activity_at_office(engine, started.activity_id)
        assert engine.scheduling_state().ready_agent_ids == (1,)
        before = engine.state.activities[started.activity_id].model_dump(mode="json")

        denied = _direct_resume(engine, started.activity_id, f"social-{kind}-resume")

        assert denied.error_code == "INTERACTION_MENU_DENIED"
        assert engine.state.activities[started.activity_id].model_dump(mode="json") == before
    finally:
        engine.close()


def test_menu_changes_do_not_change_candidate_exclusions_or_reason_counts(tmp_path) -> None:
    """同一世界只改菜单时，各真实候选分区的完整排除账逐项相同。"""
    unrestricted = _engine(tmp_path / "unrestricted", lambda document: None)
    restricted = _engine(
        tmp_path / "restricted",
        lambda document: _restrict(
            document,
            tools=("purchase", "send_message"),
            kinds=("watch", "play_game", "lifestyle"),
        ),
    )
    try:
        full_agent = unrestricted.state.agents[1]
        limited_agent = restricted.state.agents[1]
        categories = unrestricted._activity_summary(full_agent)
        assert categories == restricted._activity_summary(limited_agent)
        for category in categories:
            full = unrestricted._candidate_window(full_agent, category, None)
            limited = restricted._candidate_window(limited_agent, category, None)
            assert full.items == limited.items, category
            assert [item.as_row() for item in full.excluded] == [
                item.as_row() for item in limited.excluded
            ], category
            assert full.exclusion_counts == limited.exclusion_counts, category
            assert full.source_counts == limited.source_counts, category
            assert full.generated == limited.generated, category
            assert full.total == limited.total, category
    finally:
        unrestricted.close()
        restricted.close()
