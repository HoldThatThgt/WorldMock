"""开始组经真实引擎与存储：两条开始调用同一决策登记、整组核验、整组原子、核验次序、登记协议与同刻预算。"""

from __future__ import annotations

import pytest
from agentsociety2.society import DirectToolCall

from tests.life.conftest import (
    MAIN_TOOL,
    SIDE_TOOL,
    action_identity,
    advance_until_ready,
    conclude_request,
    context,
    do_at,
    pause_request,
    purchase,
    report_request,
    sleep,
    stage_day_plan,
    travel,
    use_app,
)
from tests.life.environment_runtime import act, act_group, action_result, events
from worldmock.foundation.errors import ProtocolError
from worldmock.state.interaction.requests import REQUEST_MODELS, StartMainActivityRequest, StartSideActivityRequest

_SUFFIX = " Neither start request was started."
_REST = "进行测试休息（1月5日 08:00 起）"


def _poor(amount: int):
    """把人物的初始余额改成给定的分。"""
    def mutate(persona, config):
        persona["initial_balance_fen"] = amount
    return mutate


def _attentive(balance: int | None = None):
    """每五分钟到一次重估间隔，人物在一件 15 分钟的事做到一半时就有决策机会；可同时改初始余额。"""
    def mutate(persona, config):
        config["reconsideration_interval_seconds"] = 300
        if balance is not None:
            persona["initial_balance_fen"] = balance
    return mutate


def _frozen(engine) -> dict:
    """被拒的开始组不得改变的世界事实：活动、商品履约记录、账户、会话、预排事件、随机状态、位置与内容进度。"""
    state = engine.context.state.model_dump(mode="json")
    person = state["agents"]["1"]
    return {"activities": state["activities"], "deliveries": state["deliveries"], "accounts": state["accounts"],
            "sessions": state["sessions"], "pending_events": state["pending_events"],
            "random_state": state["random_state"], "location": person["location_id"],
            "content_progress": person["content_progress"]}


def _rejected_events(engine, label: str) -> list:
    """一个被拒开始组各动作的拒绝事件（按登记次序）。"""
    return [row for row in events(engine, "action_rejected") if row.action_id.startswith(f"decision-{label}-1:action:")]


def test_two_start_calls_of_one_decision_start_two_activities_at_the_same_moment(engine_factory):
    """登记两条动作、同一时刻开始两项活动、各有受理结果，活动回链各自的动作与请求事件。"""
    engine = engine_factory()
    _, (main, side) = act_group(engine, "pair", do_at("家", "测试休息"), purchase("牛肉面", "薯片"))
    assert (main.status, side.status) == ("in_progress", "in_progress")
    assert (main.message, side.message) == ("Activities started", "Activities started")
    requested = [row for row in events(engine, "action_requested")
                 if row.action_id.startswith("decision-pair-1:action:")]
    assert [row.data["tool_name"] for row in requested] == [MAIN_TOOL, SIDE_TOOL]
    assert len({row.decision_id for row in requested}) == 1
    activities = engine.context.state.activities
    started = (f"activity-{action_identity('pair', 1)}", f"activity-{action_identity('pair', 2)}-purchase")
    assert set(activities) == set(started)
    assert (main.activity_id, side.activity_id) == started
    rest, basket = activities[main.activity_id], activities[side.activity_id]
    assert (rest.resource_claims, basket.resource_claims) == ({"first"}, {"second"})
    assert rest.started_at == basket.started_at == engine.context.state.current_time
    for activity, request in zip((rest, basket), requested, strict=True):
        assert activity.origin_action_id == request.action_id
        assert activity.origin_request_event_id == request.event_id
        assert activity.origin_decision_id == request.decision_id
    assert [item["activity_id"] for item in main.data["activities"]] == [rest.activity_id]
    assert [item["activity_id"] for item in side.data["activities"]] == [basket.activity_id]
    assert main.data["orders"] == [] and len(side.data["orders"]) == 2
    for kind in ("action_accepted", "action_result"):
        written = [row.action_id for row in events(engine, kind) if row.action_id.startswith("decision-pair-1:action:")]
        assert written == [action_identity("pair", 1), action_identity("pair", 2)]
    session = engine.context.state.sessions[rest.session_ids[-1]]
    assert set(session.activity_ids) == set(activities) and basket.session_ids == rest.session_ids


def _outcome(engine) -> list[tuple]:
    """与动作身份无关的世界结果：各活动的种类、名称、槽位与起止，各商品履约记录的商品、价格与预计到达。"""
    state = engine.context.state
    activities = sorted((item.kind.value, item.name, sorted(item.resource_claims), item.started_at,
                         item.planned_end_at) for item in state.activities.values())
    orders = sorted((item.goods_id, item.total_price_fen, item.expected_at, item.destination_id)
                    for item in state.deliveries.values())
    return [activities, orders, state.accounts, state.agents[1].location_id]


def test_swapping_the_two_calls_gives_the_same_world(engine_factory):
    """派发次序固定，与两条调用在回复里的先后无关：交换先后，开始的活动、订单、车费与会话都相同。"""
    first, second = engine_factory(), engine_factory()
    act_group(first, "pair", travel("公司", "驾车"), purchase("牛肉面", "薯片"))
    act_group(second, "pair", purchase("牛肉面", "薯片"), travel("公司", "驾车"))
    purchases = [next(item for item in engine.context.state.activities.values() if item.kind == "purchase")
                 for engine in (first, second)]
    assert purchases[0].planned_duration_seconds and _outcome(first)[2] == _outcome(second)[2]
    assert [row[:2] for row in _outcome(first)[0]] == [row[:2] for row in _outcome(second)[0]]
    assert [row[0] for row in _outcome(first)[1]] == [row[0] for row in _outcome(second)[1]]
    for engine in (first, second):
        session = next(item for item in engine.context.state.sessions.values() if item.status == "active")
        assert session.kind == "transit" and len(session.activity_ids) == 2
        deliveries = engine.context.state.deliveries.values()
        assert {item.destination_id for item in deliveries} == {"home"}
        assert {item.fulfillment.value for item in deliveries} == {"delivery"}
    opened = [next(item for item in engine.context.state.sessions.values() if item.status == "active")
              .opened_by_action_id for engine in (first, second)]
    assert opened == [action_identity("pair", 1), action_identity("pair", 2)]


def test_commit_returns_one_result_per_decision_and_persists_both(engine_factory):
    """环境向框架只返回该决策第一条动作的结果，两条结果都已落库。"""
    engine = engine_factory()
    decision = context(engine, "single")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    side, main = use_app("哔哩哔哩"), do_at("家", "测试闲聊")
    side_id, main_id = action_identity("single", 1), action_identity("single", 2)
    receipts = [engine.submit_action(DirectToolCall(decision, side_id, SIDE_TOOL, side)),
                engine.submit_action(DirectToolCall(decision, main_id, MAIN_TOOL, main))]
    assert [receipt.action_id for receipt in receipts] == [side_id, main_id]
    assert engine.submit_action(DirectToolCall(decision, main_id, MAIN_TOOL, main)).action_id == main_id
    returned = engine.commit_decision_round("single")
    assert [result.action_id for result in returned] == [side_id]
    stored = engine.context.state.processed_actions
    assert {side_id, main_id} <= set(stored)
    assert stored[main_id].result["activity_id"] == f"activity-{main_id}"
    assert stored[side_id].result["activity_id"] == f"activity-{side_id}"
    notes = [row.data["self_note"] for row in events(engine, "action_requested")]
    assert notes == [None, None]


@pytest.mark.parametrize("requests,code", [
    ((report_request("x"), use_app("哔哩哔哩")), "second-control"),
    ((do_at("家", "测试休息"), do_at("家", "测试闲聊")), "same-tool"),
    ((purchase("薯片"), use_app("哔哩哔哩")), "same-tool"),
    ((do_at("家", "测试休息"), report_request("x")), "start-then-control"),
])
def test_second_registration_outside_a_start_pair_is_a_protocol_fault(engine_factory, requests, code):
    """同一决策的第二条非开始动作、同一个开始工具第二次，都以协议故障停止运行。"""
    from tests.life.conftest import tool_of

    engine = engine_factory()
    decision = context(engine, code)
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, action_identity(code, 1), tool_of(requests[0]), requests[0]))
    with pytest.raises(ProtocolError, match="Agent already submitted its decision round action"):
        engine.submit_action(DirectToolCall(decision, action_identity(code, 2), tool_of(requests[1]), requests[1]))


def test_third_action_and_retired_tool_are_protocol_faults(engine_factory):
    """第三条动作被拒；工具清单里没有 start_activity，带这个工具名的登记不被受理。"""
    engine = engine_factory()
    decision = context(engine, "third")
    engine.begin_decision_round([decision])
    engine.observe(decision)
    engine.submit_action(DirectToolCall(decision, action_identity("third", 1), MAIN_TOOL, do_at("家", "测试休息")))
    engine.submit_action(DirectToolCall(decision, action_identity("third", 2), SIDE_TOOL, use_app("哔哩哔哩")))
    with pytest.raises(ProtocolError, match="Agent already submitted its decision round action"):
        engine.submit_action(DirectToolCall(decision, action_identity("third", 3), SIDE_TOOL, purchase("薯片")))
    assert "start_activity" not in REQUEST_MODELS
    other = engine_factory()
    decision = context(other, "retired")
    other.begin_decision_round([decision])
    with pytest.raises(ProtocolError, match="Direct tool call request type does not match its tool"):
        other.submit_action(DirectToolCall(decision, action_identity("retired"), "start_activity",
                                           do_at("家", "测试休息")))


def test_side_failure_starts_neither_call_and_changes_nothing(engine_factory):
    """主调用成立而副调用付不起：两项都不开始、余额与订单不变、不消耗随机选择；两条拒绝内容相同。"""
    engine = engine_factory(mutate=_poor(1000))
    before = _frozen(engine)
    _, (main, side) = act_group(engine, "broke", do_at("家", "测试休息"), purchase("牛肉面", "薯片"))
    assert _frozen(engine) == before and not engine.context.state.activities
    assert (main.status, side.status) == ("rejected", "rejected")
    assert main.error_code == side.error_code == "INSUFFICIENT_FUNDS"
    reason = "The caller's available balance is below the actual purchase total."
    assert main.message == side.message == reason + _SUFFIX
    assert main.data == side.data and main.data["reason"] == reason + _SUFFIX
    assert main.data["fix"] is None and main.data["next_step"] is None
    assert main.data["items"] == [{
        "path": ["tool_calls", 1, "function", "arguments", "target"], "written": ["牛肉面", "薯片"],
        "error_code": "INSUFFICIENT_FUNDS", "allowed_values": [], "expected": reason, "why": reason, "candidates": []}]
    rejected = _rejected_events(engine, "broke")
    assert len(rejected) == 2 and rejected[0].data == rejected[1].data


def test_main_failure_starts_neither_call_and_points_at_the_main_call(engine_factory):
    """副调用成立而主调用不成立（槽位 1 被占）：哔哩哔哩没有开始，明细只落在主调用上，没有问题的调用不出明细。"""
    engine = engine_factory(mutate=_attentive())
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress" and advance_until_ready(engine)
    assert engine.context.state.activities[rest.activity_id].status == "active"
    before = _frozen(engine)
    _, (main, side) = act_group(engine, "leave", travel("公司", "步行"), use_app("哔哩哔哩"))
    assert _frozen(engine) == before and list(engine.context.state.activities) == [rest.activity_id]
    reason = f"The first activity slot is occupied by {_REST}."
    assert main.error_code == side.error_code == "RESOURCE_CONFLICT" and main.data == side.data
    assert main.data["reason"] == reason + _SUFFIX and main.data["fix"] is None
    assert main.data["next_step"] == f"Conclude or pause {_REST} first."
    assert main.data["unmet"] == ["resource_claims"]
    assert main.data["items"] == [{
        "path": ["tool_calls", 0, "function", "arguments", "target"], "written": "公司",
        "error_code": "RESOURCE_CONFLICT", "allowed_values": [], "expected": reason, "why": reason, "candidates": []}]


def test_single_call_rejection_has_no_group_sentence(engine_factory):
    """只有一条调用时不追加组的那一句；占用者不能暂停时下一步只写结束。"""
    engine = engine_factory()
    _, trip = act(engine, "go", travel("公司", "步行"))
    assert trip.status == "in_progress" and advance_until_ready(engine)
    name = engine.context.state.activities[trip.activity_id].name
    _, result = act(engine, "again", travel("五里公园", "步行"))
    assert result.status == "rejected" and result.error_code == "RESOURCE_CONFLICT"
    assert result.message == f"The first activity slot is occupied by {name}."
    assert result.data["next_step"] == f"Conclude {name} first."
    assert [item["path"] for item in result.data["items"]] == [["tool_calls", 0, "function", "arguments", "target"]]


def test_earlier_check_decides_the_top_level_code(engine_factory):
    """同时触发多条检查时顶层码取次序在前的一条：待收尾先于槽位占用，槽位占用先于余额。"""
    engine = engine_factory(mutate=_attentive(1000))
    act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    _, (main, _) = act_group(engine, "busy", travel("公司", "步行"), purchase("牛肉面", "薯片"))
    assert main.error_code == "RESOURCE_CONFLICT"
    waiting = engine_factory(mutate=_poor(1000))
    _, nap = act(waiting, "nap", sleep(10))
    while waiting.context.state.activities[nap.activity_id].status == "active":
        from tests.life.conftest import advance_next
        advance_next(waiting)
    name = waiting.context.state.activities[nap.activity_id].name
    _, (first, second) = act_group(waiting, "late", do_at("家", "测试休息"), purchase("牛肉面", "薯片"))
    assert first.error_code == second.error_code == "ACTIVITY_CONCLUSION_REQUIRED"
    assert first.data["reason"] == f"Conclude the finished activities {name} first." + _SUFFIX
    assert first.data["next_step"] == f"Conclude {name}."
    assert [item["path"][1] for item in first.data["items"]] == [0, 1]


def test_sleep_with_a_side_call_is_refused_with_its_own_fix(engine_factory):
    """睡眠与副调用同组：整组拒绝，改法是单独提交主工具，两条调用都负责。"""
    engine = engine_factory()
    before = _frozen(engine)
    _, (main, side) = act_group(engine, "sleepy", sleep(60), use_app("哔哩哔哩"))
    assert _frozen(engine) == before
    assert main.error_code == side.error_code == "RESOURCE_CONFLICT"
    assert main.data["reason"] == "Sleep requires the second activity slot to be empty." + _SUFFIX
    assert main.data["fix"] == "Submit start_main_activity alone to sleep." and main.data["next_step"] is None
    assert main.data["unmet"] == [] and [item["path"][1] for item in main.data["items"]] == [0, 1]
    assert [item["written"] for item in main.data["items"]] == ["60", ["哔哩哔哩"]]


def test_sleep_with_slot_two_occupied_is_refused_with_its_own_next_step(engine_factory):
    """睡眠时槽位 2 已被占：下一步是先结束或暂停槽位 2 的活动，没有改法。"""
    engine = engine_factory()
    _, video = act(engine, "video", use_app("哔哩哔哩"))
    assert video.status == "in_progress" and advance_until_ready(engine)
    _, result = act(engine, "sleepy", sleep(60))
    assert result.status == "rejected" and result.error_code == "RESOURCE_CONFLICT"
    assert result.message == "Sleep requires the second activity slot to be empty."
    assert result.data["next_step"] == "Conclude or pause the second activity first."
    assert result.data["fix"] is None and result.data["unmet"] == []


def test_bypassing_the_host_gets_the_shared_error_shape(engine_factory):
    """绕过宿主直接提交的错误请求：形状问题与名称问题各合并成一次 ACTION_REQUEST_INVALID，路径带真实调用位置。"""
    engine = engine_factory()
    bad_main = StartMainActivityRequest.model_construct(action="travel", target="公司", detail=None)
    bad_side = StartSideActivityRequest.model_construct(action="interact", target=["哔哩哔哩", "抖音"], prefer=None)
    _, (main, side) = act_group(engine, "shape", bad_main, bad_side)
    assert main.error_code == side.error_code == "ACTION_REQUEST_INVALID" and main.data == side.data
    assert main.data["fix"] == "Correct every reported parameter."
    assert [(item["path"], item["why"]) for item in main.data["items"]] == [
        (["tool_calls", 0, "function", "arguments", "detail"], "detail is required when action is travel"),
        (["tool_calls", 1, "function", "arguments", "target"],
         "target takes exactly one app name when action is interact")]
    assert main.data["reason"] == ("detail is required when action is travel; "
                                   "target takes exactly one app name when action is interact" + _SUFFIX)
    names = engine_factory()
    _, (first, second) = act_group(names, "names", purchase("牛肉面"), do_at("公司", "季度销售数据整理"))
    assert first.error_code == "ACTION_REQUEST_INVALID" and first.data == second.data
    item = first.data["items"][0]
    assert item["path"] == ["tool_calls", 1, "function", "arguments", "action"]
    assert item["error_code"] == "ACTION_NOT_OFFERED" and item["allowed_values"] == ["travel"]
    assert item["candidates"][0] == {"tool": MAIN_TOOL, "note": "约 32 分钟",
                                     "arguments": {"action": "travel", "target": "公司", "detail": "步行"}}
    assert first.data["fix"] == "Use one of the object's offered actions." and not names.context.state.activities


def test_unknown_goods_name_is_refused_as_supply_not_found(engine_factory):
    """购买数组里未展示的名称在全量商品中找不到唯一商品：SUPPLY_NOT_FOUND，负责的是副调用。"""
    engine = engine_factory()
    _, (main, side) = act_group(engine, "ghost", do_at("家", "测试休息"), purchase("牛肉面", "不存在的商品"))
    assert main.error_code == side.error_code == "SUPPLY_NOT_FOUND" and not engine.context.state.activities
    assert main.data["reason"] == "No goods exist for the written name; changing spelling cannot create goods." + _SUFFIX
    assert main.data["fix"] == "Use an exact known goods name."
    assert main.data["next_step"] == "Choose a known product or candidate request."
    assert [item["path"] for item in main.data["items"]] == [["tool_calls", 1, "function", "arguments", "target"]]
    named = engine_factory()
    _, result = act(named, "named", purchase("平台笔记本"))
    assert result.status == "in_progress" and [order["goods_id"] for order in result.data["orders"]] == ["online-notebook"]


def test_group_counts_one_same_moment_round_when_accepted_or_rejected(engine_factory):
    """同刻预算按组计：一个开始组受理或整组被拒都只加一轮。"""
    engine = engine_factory()
    act_group(engine, "sleepy", sleep(60), use_app("哔哩哔哩"))
    assert engine.context.state.agents[1].same_moment_rounds == 1
    _, started = act_group(engine, "pair", do_at("家", "测试休息"), use_app("哔哩哔哩"))
    assert [item.status for item in started] == ["in_progress", "in_progress"]
    assert engine.context.state.agents[1].same_moment_rounds == 2
    refused = engine_factory()
    _, results = act_group(refused, "refused", sleep(60), use_app("哔哩哔哩"))
    assert [item.status for item in results] == ["rejected", "rejected"]
    assert refused.context.state.agents[1].same_moment_rounds == 1


def test_name_class_rejections_are_free_per_group(engine_factory):
    """名称类拒绝每人每秒前两次免计的规则按组计：两个被拒的开始组（四条拒绝结果）都不计轮数，第三组才计。"""
    engine = engine_factory()
    for label in ("one", "two"):
        act_group(engine, label, do_at("家", "测试休息"), purchase("不存在的商品"))
        assert engine.context.state.agents[1].same_moment_rounds == 0
    assert engine.context.state.agents[1].body.relaxation_need == pytest.approx(0.2)
    act_group(engine, "three", do_at("家", "测试休息"), purchase("不存在的商品"))
    assert engine.context.state.agents[1].same_moment_rounds == 1
    assert engine.context.state.agents[1].body.relaxation_need == pytest.approx(0.25)


def test_control_tool_cannot_resume_through_a_start_tool_and_slot_blocks_resume(engine_factory):
    """暂停的地点活动用 report_status 恢复回到槽位 1；槽位被占时恢复被拒；暂停让出槽位 1 后可以点名另一件事。"""
    engine = engine_factory(mutate=_attentive())
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    name = engine.context.state.activities[rest.activity_id].name
    _, paused = act(engine, "pause", pause_request(name), "pause_activity")
    assert paused.status == "completed" and engine.context.state.activities[rest.activity_id].status == "paused"
    _, chat = act(engine, "chat", do_at("家", "测试闲聊"))
    assert chat.status == "in_progress" and advance_until_ready(engine)
    _, blocked = act(engine, "resume-blocked", report_request(name), "report_status")
    assert blocked.status == "rejected" and blocked.error_code == "RESOURCE_CONFLICT"
    other = engine.context.state.activities[chat.activity_id].name
    _, done = act(engine, "end-chat", conclude_request(other), "conclude_activity")
    assert done.status == "completed"
    _, resumed = act(engine, "resume", report_request(name), "report_status")
    assert resumed.status == "completed"
    activity = engine.context.state.activities[rest.activity_id]
    assert activity.status == "active" and activity.resource_claims == {"first"}
    assert action_result(engine, action_identity("resume")).data["resumed"][0]["name"] == name


def test_travel_departs_while_an_app_is_already_in_use(engine_factory):
    """出行占槽位 1：出发前已在用的内容 app（槽位 2）可以带着走，两项活动同时进行。"""
    engine = engine_factory(mutate=_attentive())
    _, video = act(engine, "video", use_app("哔哩哔哩"))
    assert video.status == "in_progress" and advance_until_ready(engine)
    assert engine.context.state.activities[video.activity_id].status == "active"
    _, trip = act(engine, "leave", travel("公司", "步行"))
    assert trip.status == "in_progress", trip.message
    claims = {item.kind.value: item.resource_claims for item in engine.context.state.activities.values()}
    assert claims == {"interact": {"second"}, "travel": {"first"}}
    session = next(item for item in engine.context.state.sessions.values() if item.status == "active")
    assert session.kind == "transit" and trip.activity_id in session.activity_ids


def test_app_starts_while_a_location_activity_is_running(engine_factory):
    """在地点做事占槽位 1：做到一半时可以再打开内容 app（槽位 2），地点活动不中断。"""
    engine = engine_factory(mutate=_attentive())
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert rest.status == "in_progress" and advance_until_ready(engine)
    assert engine.context.state.activities[rest.activity_id].status == "active"
    _, video = act(engine, "video", use_app("哔哩哔哩"))
    assert video.status == "in_progress", video.message
    activities = engine.context.state.activities
    assert activities[rest.activity_id].resource_claims == {"first"}
    assert activities[video.activity_id].resource_claims == {"second"}
    assert activities[rest.activity_id].status == activities[video.activity_id].status == "active"


def test_fare_shortfall_alone_is_refused_as_travel_funds_and_the_row_says_the_same(engine_factory):
    """没有同组购买而余额少于车费：INSUFFICIENT_TRAVEL_FUNDS，负责的是主调用；工具层那一行的原因一字不差。"""
    engine = engine_factory(mutate=_poor(1000))
    observed, result = act(engine, "taxi", travel("公司", "打车"))
    reason = "The caller's available balance is below the route fare."
    assert result.status == "rejected" and result.error_code == "INSUFFICIENT_TRAVEL_FUNDS"
    assert result.message == reason and result.data["fix"] is None and result.data["next_step"] is None
    assert [item["path"] for item in result.data["items"]] == [["tool_calls", 0, "function", "arguments", "target"]]
    rows = {row["detail"]: row for row in next(
        target for target in observed["tool_layer"]["targets"] if target["name"] == "公司")["actions"]}
    assert (rows["打车"]["available"], rows["打车"]["reason"]) == (False, reason)
    assert rows["步行"]["available"] is True and rows["驾车"]["available"] is True
    assert engine.context.state.accounts["agent:1"] == 1000 and not engine.context.state.activities


def test_fare_joins_the_purchase_total_and_both_calls_are_responsible(engine_factory):
    """同组出行的车费计入合计：各自付得起而合计不足时整组以 INSUFFICIENT_FUNDS 拒绝，两条调用都有明细。"""
    engine = engine_factory(mutate=_poor(2000))
    before = _frozen(engine)
    _, (main, side) = act_group(engine, "both", travel("公司", "打车"), purchase("薯片"))
    reason = "The caller's available balance is below the actual purchase total."
    assert _frozen(engine) == before
    assert main.error_code == side.error_code == "INSUFFICIENT_FUNDS" and main.data == side.data
    assert main.data["reason"] == reason + _SUFFIX
    assert [(item["path"][1], item["written"], item["why"]) for item in main.data["items"]] == [
        (0, "公司", reason), (1, ["薯片"], reason)]
    _, alone = act(engine, "walk-and-buy", purchase("薯片"))
    assert alone.status == "in_progress", alone.message


def test_start_without_a_frozen_observation_is_not_settled(engine_factory):
    """决策没有唯一的冻结观察时不能核验名称与报价：开始组不结算，世界不变。"""
    from worldmock.foundation.errors import BusinessRuleError

    engine = engine_factory()
    decision = context(engine, "blind")
    engine.begin_decision_round([decision])
    engine.submit_action(DirectToolCall(decision, action_identity("blind"), MAIN_TOOL, do_at("家", "测试休息")))
    with pytest.raises(BusinessRuleError, match="OBSERVATION_NOT_FOUND"):
        engine.commit_decision_round("blind")
    assert not engine.context.state.activities


def test_rejected_start_group_is_remembered_once_and_corrected_only_on_its_own_tool(engine_factory):
    """被拒的开始组在进度层 rejected_today 里每条负责的调用一行，同一决策的两条拒绝只计一次；之后受理副工具的一条
    调用，只有明细路径指向副调用的那一行记下改正后的写法。"""
    engine = engine_factory()
    _, (main, side) = act_group(engine, "sleepy", sleep(60), use_app("哔哩哔哩"))
    assert (main.status, side.status) == ("rejected", "rejected")
    observed, video = act(engine, "video", use_app("抖音"))
    reason = "Sleep requires the second activity slot to be empty."
    remembered = sorted(observed["progress_layer"]["rejected_today"], key=lambda row: str(row["written"]))
    assert [(row["written"], row["why"], row["times"], row["accepted_as"]) for row in remembered] == [
        ("60", reason, 1, None), (["哔哩哔哩"], reason, 1, None)]
    assert video.status == "in_progress", video.error_code
    later = engine.observe(context(engine, "later"))["progress_layer"]["rejected_today"]
    corrected = {str(row["written"]): (row["times"], row["accepted_as"]) for row in later}
    assert corrected == {"60": (1, None), str(["哔哩哔哩"]): (1, ["抖音"])}



def test_resuming_two_paused_activities_checks_the_second_against_the_first_resumed(engine_factory):
    engine = engine_factory(mutate=_attentive())
    stage_day_plan(engine)
    _, bilibili = act(engine, "bilibili", use_app("哔哩哔哩"))
    assert advance_until_ready(engine)
    bilibili_name = engine.context.state.activities[bilibili.activity_id].name
    _, paused = act(engine, "pause-bilibili", pause_request(bilibili_name), "pause_activity")
    assert paused.status == "completed"
    _, rest = act(engine, "rest", do_at("家", "测试休息"))
    assert advance_until_ready(engine)
    rest_name = engine.context.state.activities[rest.activity_id].name
    _, paused = act(engine, "pause-rest", pause_request(rest_name), "pause_activity")
    assert paused.status == "completed"
    _, douyin = act(engine, "douyin", use_app("抖音"))
    assert advance_until_ready(engine)
    douyin_name = engine.context.state.activities[douyin.activity_id].name
    _, refused = act(engine, "resume-both", report_request(rest_name, bilibili_name), "report_status")
    assert refused.status == "rejected"
    assert refused.error_code == "SESSION_LIMIT_EXCEEDED"
    assert rest_name in refused.message and douyin_name in refused.message
    assert engine.context.state.activities[rest.activity_id].status == "paused"
