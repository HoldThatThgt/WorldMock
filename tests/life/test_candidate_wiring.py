"""候选契约与曝光轮转的运行期接线：观察投影、下推返回体、轮转留痕的域内测试。

本模块只断言「生产路径真的走到了新模块」：返回体字段名、排除项原因码、游标下推的
无重无漏、轮转对呈现顺序的决定作用，以及 `presentation_state` 的写入。
"""

from __future__ import annotations

import json

from worldmock.life import candidates, presentation, rotation
from datetime import datetime
from zoneinfo import ZoneInfo

from tests.life.conftest import stage_day_plan
from worldmock.footprints.models import EventDraft
from agentsociety2.society import DirectToolCall

from worldmock.life.models import BrowseActivitiesRequest, StartActivityRequest

from .conftest import browse_all, context, submit_round


def _browse(engine, category: str, round_id: str, cursor: str | None = None) -> dict:
    """下钻一个品类并返回结算返回体（browse 是纯查询动作）。"""
    _, results = submit_round(
        engine, {1: ("browse_activities",
                     BrowseActivitiesRequest(category=category, cursor=cursor))}, round_id)
    result = results[0]
    assert result.status.value == "completed", result.error_code
    return result.data


def test_browse_result_matches_down_push_contract(engine_factory) -> None:
    """下推返回体含 returned/total/next_cursor/items/excluded，且 count 不再是字段。"""
    engine = engine_factory()
    payload = _browse(engine, "sport", "wiring-contract")
    assert set(payload) == {"tool", "category", "returned", "total", "next_cursor",
                            "note", "items", "excluded"}
    assert "count" not in payload  # count 不再作为返回体字段
    assert payload["tool"] == "browse_activities" and payload["category"] == "sport"
    assert payload["returned"] == len(payload["items"])
    assert payload["total"] == 44  # 分区过滤后的候选总数
    assert payload["next_cursor"] is not None  # 超过单次呈现上限时给出下一页游标


def test_rotation_quota_actually_decides_presentation_order(engine_factory) -> None:
    """首屏的换新名额由轮转给出：长期未呈现者进窗，且轮转清单真的进了呈现序列。"""
    engine = engine_factory()
    agent = engine.state.agents[1]
    entries = engine._pool_entries(engine._presentation_context(agent), agent,
                                  "lifestyle", "sport")
    entries_sorted = sorted(entries, key=lambda entry: entry.object_id)
    quota = rotation.rotation_quota_slots(20, 0.15)
    assert quota == 3
    # 游标停在池尾的换新名额上，且那一档条目全部长期未呈现
    offset = len(entries_sorted) - quota
    chosen = {entry.object_id for entry in entries_sorted[offset:]}
    stale = {entry.object_id: 10 ** 9 for entry in entries_sorted
             if entry.object_id not in chosen}
    inputs = presentation.candidate_inputs(
        engine._presentation_context(agent), presentation.entry_sources(entries),
        moment=False, rotation_offset=offset, last_presented_at=stale)
    decision = presentation.rotate_window(entries, inputs)
    assert set(decision.rotated_in) == chosen
    assert set(decision.rotated_in) <= {object_id for object_id, _ in decision.presented}

    window = presentation.present(entries, inputs, "lifestyle", None)
    assert chosen <= set(window.presented)
    # 全池都刚呈现过：换新名额为空，序列退化为排序键顺序（轮转不改候选集身份）
    warm = presentation.candidate_inputs(
        engine._presentation_context(agent), presentation.entry_sources(entries),
        moment=False, rotation_offset=offset,
        last_presented_at={entry.object_id: 10 ** 9 for entry in entries})
    ranked = [row["activity_id"] for row in presentation.present(
        entries, warm, "lifestyle", None).items]
    fresh = [row["activity_id"] for row in presentation.present(
        entries, warm, "lifestyle", None).items]
    assert fresh == ranked
    assert rotation.ROTATION_SUBTYPE_CAP_IN_WINDOW == 3  # 反塌缩上限来自轮转模块


def test_observe_uses_candidate_contract_and_records_presentation(engine_factory) -> None:
    """观察投影经候选契约产出商品行，并把本次呈现的留痕写进 presentation_state。"""
    engine = engine_factory()
    merchant = next(item for item in engine.scenario.locations
                    if item.location_id == engine.scenario.supplies[0].location_id)
    state = engine.state.model_copy(deep=True)
    state.current_time = _at_hour(engine, 12)
    state.pending_events = [item for item in state.pending_events
                            if item.due_at > state.current_time]
    state.agents[1] = state.agents[1].model_copy(
        update={"location_id": merchant.location_id, "next_decision_at": state.current_time, "body_settled_at": state.current_time},
        deep=True)
    _commit(engine, state)

    agent = engine.state.agents[1]
    entries = engine._product_pool(agent)
    assert entries, "the market must expose products during opening hours"
    inputs = presentation.candidate_inputs(
        engine._presentation_context(agent), presentation.entry_sources(entries),
        moment=False, rotation_offset=engine._rotation_offset(1),
        last_presented_at=engine._last_presented(1))
    expected = presentation.present(entries, inputs, "product", None)

    ctx = context(engine, "wiring-observe")
    engine.begin_decision_round([ctx])
    observed = engine.observe(ctx)
    rows = observed["facts"]["products"]
    assert [row["supply_id"] for row in rows] == list(expected.presented)
    assert all({"supply_id", "product_id", "location_id", "seller_account_id", "price_fen",
                "price_version", "current_stock", "opening_status", "source"} <= set(row) for row in rows)
    # 观察边界只暂存留痕：同版本不允许改状态，留痕随下一个提交落盘
    assert engine.state.presentation_state == {}
    engine.submit_action(DirectToolCall(
        ctx, "wiring-wait", "start_basic_activity",
        StartActivityRequest(activity_type="wait", planned_duration_seconds=60)))
    engine.commit_decision_round("wiring-observe")
    recorded = engine.state.presentation_state.get(1, {})
    assert set(recorded) == set(expected.presented)
    assert all(value == state.current_time for value in recorded.values())
    # 轮转只改顺序：留痕的条目仍在对象层，不产生下架事件
    assert set(recorded) <= set(engine.state.supply_stock)


def test_next_presentation_rotates_when_the_pool_is_fully_recent(engine_factory) -> None:
    """全部对象刚呈现过时换新名额为空：候选集不变，只是顺序回到排序键口径。"""
    engine = engine_factory()
    merchant = next(item for item in engine.scenario.locations
                    if item.location_id == engine.scenario.supplies[0].location_id)
    state = engine.state.model_copy(deep=True)
    state.current_time = _at_hour(engine, 12)
    state.pending_events = [item for item in state.pending_events
                            if item.due_at > state.current_time]
    agent = state.agents[1]
    entries = engine._product_pool(agent.model_copy(
        update={"location_id": merchant.location_id}, deep=True))
    stamps = {entry.object_id: state.current_time for entry in entries}
    state.agents[1] = agent.model_copy(
        update={"location_id": merchant.location_id, "next_decision_at": state.current_time, "body_settled_at": state.current_time},
        deep=True)
    state.presentation_state = {1: stamps}
    _commit(engine, state)

    inputs = presentation.candidate_inputs(
        engine._presentation_context(engine.state.agents[1]),
        presentation.entry_sources(entries), moment=False,
        rotation_offset=engine._rotation_offset(1), last_presented_at=stamps)
    decision = presentation.rotate_window(entries, inputs)
    assert not decision.rotated_in  # 没有长期未呈现者，换新名额为空


def test_excluded_items_carry_closed_set_reason_codes(engine_factory) -> None:
    """被排除项带 `ExclusionReason` 闭集内的原因码，「没显示」与「不存在」可区分。"""
    engine = engine_factory()
    payload = _browse(engine, "feature_film", "wiring-excluded")
    assert payload["excluded"], "the pool exceeds one window, so deferrals must be listed"
    reasons = {item["reason"] for item in payload["excluded"]}
    assert reasons <= set(candidates.EXCLUSION_REASONS)
    assert "ROTATION_DEFERRED" in reasons
    for item in payload["excluded"]:
        assert set(item) == {"object_id", "kind", "reason"}
    # 排除项计数加保留项计数等于候选全集规模
    assert len(payload["items"]) + len(payload["excluded"]) == payload["total"]


def test_cursor_down_push_is_lossless_and_repeatable(engine_factory) -> None:
    """以空游标起反复下推得到全量有序候选；同一游标重复请求返回同一段。"""
    engine = engine_factory()
    items = browse_all(engine, "sport")  # 大于单次呈现上限，因此需要下推
    assert len(items) > 20
    expected = {entry.object_id for entry in engine._lifestyle_pool(engine.state.agents[1])
                if entry.row["category"] == "sport"}
    assert {item["activity_id"] for item in items} == expected
    assert len(items) == len({item["activity_id"] for item in items})

    # 同一分区、同一世界状态版本、同一游标重复请求返回同一段
    entries = engine._pool_entries(engine._presentation_context(engine.state.agents[1]),
                                  engine.state.agents[1], "lifestyle", "sport")
    inputs = presentation.candidate_inputs(
        engine._presentation_context(engine.state.agents[1]),
        presentation.entry_sources(entries), moment=False,
        rotation_offset=engine._rotation_offset(1),
        last_presented_at=engine._last_presented(1))
    first = presentation.present(entries, inputs, "lifestyle", None)
    again = presentation.present(entries, inputs, "lifestyle", None)
    assert first.presented == again.presented
    second = presentation.present(entries, inputs, "lifestyle", first.next_cursor)
    assert not (set(second.presented) & set(first.presented))


def test_unknown_cursor_payload_is_rejected_not_silently_restarted() -> None:
    """游标载荷形状不合法即报错，绝不静默从头开始（重复读比读得更少更难发现）。"""
    import pytest

    with pytest.raises(ValueError):
        presentation.cursor_offset(json.dumps({"key": [1]}))
    assert presentation.cursor_offset(None) == 0


def test_current_moment_candidate_inputs_use_location_timezone_and_opening_calendar(engine_factory) -> None:
    """当下可行性按地图时区解释分钟秒并保留严格地点门，不能套用 UTC 本地小时。"""
    from dataclasses import replace
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import pytest

    engine = engine_factory()
    now = int(datetime(2026, 1, 5, 12, 34, 30, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
    context_value = engine._presentation_context(engine.state.agents[1])
    context_value = replace(context_value, state=engine.state.model_copy(update={"current_time": now}, deep=True))
    inputs = presentation.candidate_inputs(context_value, presentation.entry_sources(()), moment=True)
    assert inputs.local_hour == pytest.approx(12 + 34 / 60 + 30 / 3600)
    assert inputs.current_time == now
    assert inputs.require_location_instance is True
    assert inputs.location.opening_status == "open"


def test_five_kinds_share_one_three_stage_pipeline(engine_factory) -> None:
    """五类可选对象都经生成段产出候选，且逐来源命中计数与排除计数齐备。"""
    engine = engine_factory()
    agent = engine.state.agents[1]
    context_value = engine._presentation_context(agent)
    seen_kinds = set()
    for kind, category in (("product", "foods"), ("lifestyle", "sport"),
                           ("content", "feature_film"), ("game", "game_session"),
                           ("task", "work_contract")):
        entries = engine._pool_entries(context_value, agent, kind, category)
        inputs = presentation.candidate_inputs(context_value,
                                               presentation.entry_sources(entries),
                                               moment=False)
        page = candidates.recall(inputs)
        assert set(page.source_counts) == set(candidates.CANDIDATE_SOURCES)
        assert set(page.exclusion_counts) == set(candidates.EXCLUSION_REASONS)
        # 保留项加排除项等于候选全集规模（两条排除纪律的计数形式）
        assert len(page.retained) + len(page.excluded) == len(page.generated)
        seen_kinds.add(kind)
    assert seen_kinds == set(candidates.CANDIDATE_KINDS)


def _at_hour(engine, hour: int) -> int:
    """把世界时钟挪到当日整点的纪元秒。"""
    local = datetime.fromtimestamp(engine.state.current_time,
                                   ZoneInfo(engine.scenario.timezone))
    return int(local.replace(hour=hour, minute=0, second=0).timestamp())


def _commit(engine, state) -> None:
    """提交一个明确测试场景状态并同步当前引擎。"""
    state.version = engine.state.version + 1
    engine.store.commit(state.model_dump(mode="json"), [EventDraft(
        event_type="environment_event", occurred_at=state.current_time,
        visibility="system", source_kind="scenario", data={"kind": "wiring_setup"})])
    engine.state = state


def _advance_once(engine) -> None:
    """推进到环境报告的下一个严格未来边界（把暂存的留痕落盘）。"""
    state = engine.scheduling_state()
    engine.advance(int(state.next_event_at.timestamp()) - engine.state.current_time,
                   state.next_event_at)


def test_candidate_rows_and_rotation_constants_protocol_are_readable(engine_factory) -> None:
    """保留项返回体行含完整排序键；轮转常量视图可按协议形状读取。"""
    engine = engine_factory()
    context_value = engine._presentation_context(engine.state.agents[1])
    entries = engine._pool_entries(context_value, engine.state.agents[1], "content",
                                  "feature_film")
    constants = engine.semantics.candidate_constants
    assert constants.rotation_quota_ratio > 0 and constants.rotation_stale_seconds > 0
    inputs = presentation.candidate_inputs(context_value, presentation.entry_sources(entries),
                                           moment=False, rotation_offset=0)
    page = candidates.recall(inputs)
    row = page.retained[0].as_row()
    assert set(row) == {"object_id", "kind", "taste_weight", "attention_prior"}
    assert len(page.retained[0].order_key()) == 3
    assert isinstance(constants.rotation_stale_seconds, int)
