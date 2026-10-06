"""登记应用内容经真实人物请求形成连续、可恢复的数字足迹。"""

from __future__ import annotations

from tests.life.conftest import context, pause_request, report_request, stage_day_plan, start_activity, wake_by_mail
from tests.life.routing_fixtures import decision_round, run_activity_out
from worldmock.exchange.constants import APP_USE_DEFAULT_SECONDS
from worldmock.life.activity_titles import activity_snapshot
from worldmock.life.models import ActivityKind, ActivityStatus

APP_ID = "douyin"
APP_NAME = "抖音"


def _open(engine, label: str = "open-app"):
    """@brief 通过真实工具层打开固定应用。"""
    observed, result = decision_round(
        engine,
        label,
        "start_activity",
        start_activity("打开", f"应用：{APP_NAME}"),
    )
    assert result.status == "in_progress", result.error_code
    return observed, result


def test_registered_apps_expose_closed_content_catalog_without_legacy_feed_fields(engine_factory) -> None:
    """@brief 内容身份、标题、类别、摘要、时长和应用绑定只来自登记 Catalog。"""
    engine = engine_factory()
    catalog = engine.context.semantics.catalog
    assert set(catalog.apps) == {"bilibili", "douyin", "eleme", "meituan"}
    assert set(catalog.contents_by_app[APP_ID]) == {"c-live", "c-long", "c-short", "c-short-2"}
    for content_id in catalog.contents_by_app[APP_ID]:
        content = catalog.contents[content_id]
        assert content.title and content.summary and content.duration_seconds > 0
        assert set(type(content).model_fields) == {
            "content_id", "title", "kind", "summary", "summary_source",
            "duration_seconds", "app_ids", "series",
        }


def test_opening_an_app_keeps_app_and_content_titles_separate(engine_factory) -> None:
    """@brief 使用活动始终叫应用名，当前内容标题在独立快照字段。"""
    engine = engine_factory()
    stage_day_plan(engine)
    observed, opened = _open(engine)
    activity = engine.context.state.activities[opened.activity_id]
    content = engine.context.semantics.catalog.contents[activity.data["content_id"]]
    assert activity.kind == ActivityKind.USE_APP
    assert activity.name.startswith(f"打开{APP_NAME}（")
    assert activity.target_id == APP_ID
    assert activity.planned_duration_seconds == APP_USE_DEFAULT_SECONDS == 1800
    assert activity.resource_claims == {"use_app"}
    row = next(item for item in observed["tool_layer"]["targets"] if item["name"] == f"应用：{APP_NAME}")
    assert row["state"] == "4 条内容"
    snapshot = activity_snapshot(activity, engine.context.titles, engine.context.lifestyle_items)
    assert snapshot["title"] == APP_NAME
    assert snapshot["content_title"] == content.title


def test_pause_and_resume_continue_from_the_latest_global_content_position(engine_factory) -> None:
    """@brief 暂停与恢复不重抽内容，也不倒退人物全局内容位置。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, opened = _open(engine, "open-resume")
    activity_id = opened.activity_id
    wake_by_mail(engine, offset=60)
    engine.advance(60, engine.scheduling_state().next_event_at)
    activity = engine.context.state.activities[activity_id]
    content_id = activity.data["content_id"]
    before = engine.context.state.agents[1].content_progress[content_id].position_seconds
    _, paused = decision_round(
        engine,
        "pause-app",
        "pause_activity",
        pause_request(activity.name, summary="先停一下。"),
    )
    assert paused.status == "completed"
    frozen = engine.context.state.activities[activity_id]
    assert frozen.status == ActivityStatus.PAUSED
    assert frozen.data["content_id"] == content_id
    _, resumed = decision_round(
        engine,
        "resume-app",
        "report_status",
        report_request(activity.name, summary="继续看。"),
    )
    assert resumed.status == "completed"
    current = engine.context.state.activities[activity_id]
    assert current.status == ActivityStatus.ACTIVE
    assert current.data["content_id"] == content_id
    assert current.media_position_seconds == before


def test_short_content_continues_silently_until_the_fixed_app_plan_ends(engine_factory) -> None:
    """@brief 内容完成只更新全局进度并静默抽取下一条，应用计划仍持续到 1800 秒。"""
    engine = engine_factory()
    stage_day_plan(engine)
    _, opened = _open(engine, "open-full-plan")
    run_activity_out(engine, opened.activity_id, "app-plan")
    ended = engine.context.state.activities[opened.activity_id]
    assert ended.status == ActivityStatus.COMPLETED
    assert ended.elapsed_seconds == APP_USE_DEFAULT_SECONDS
    assert ended.boundary_type == "planned_end"
    assert ended.data["played"]
    for row in ended.data["played"]:
        progress = engine.context.state.agents[1].content_progress[row["content_id"]]
        assert progress.completed_rounds >= 1


def test_an_app_without_content_is_listed_but_refused(engine_factory) -> None:
    """@brief 无内容应用仍在工具层，动作明确不可用且提交返回固定错误。"""
    engine = engine_factory()
    stage_day_plan(engine)
    observed = engine.observe(context(engine, "empty-app-preview"))
    row = next(item for item in observed["tool_layer"]["targets"] if item["name"] == "应用：美团")
    assert row["state"] == "0 条内容"
    assert row["actions"] == [{
        "action": "interact",
        "way": "打开",
        "available": False,
        "reason": "This app has no content to use.",
        "price": None,
        "duration": "约 30 分钟",
        "quantity": None,
        "note": None,
    }]
    _, rejected = decision_round(engine, "open-empty", "start_activity", start_activity("打开", "应用：美团"))
    assert (rejected.status, rejected.error_code) == ("rejected", "APP_CONTENT_EMPTY")
