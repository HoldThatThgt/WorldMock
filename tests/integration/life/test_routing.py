"""@brief 五字段应用、支付、社交对象和消息关联的纯路由证据。"""

import copy
from dataclasses import replace

import pytest

from tests.life.action_contract_world import contract_world
from tests.integration.life.test_name_resolution import start_payload
from worldmock.life.models import StartActivityRequest
from worldmock.life.name_resolution import name_book, resolve_start
from worldmock.life.routing import ROUTE_KEYS, combination_error, combination_issues, routed_selection, routing_names


def resolve(observation, payload):
    """@brief 宿主和环境消费同一个名称结果与同一冻结路由事实。"""
    outcome = resolve_start(name_book(observation), StartActivityRequest.model_validate(payload))
    assert not outcome.failures
    return outcome, routing_names(observation)


@pytest.mark.parametrize("target,way,values,code,field,allowed", [
    ("刷推荐", "进行", {}, "APP_REQUIRED", "app", ("哔哩哔哩", "抖音")),
    ("刷推荐", "进行", {"app": "Keep"}, "APP_NOT_OFFERED", "app", ("哔哩哔哩", "抖音")),
    ("安静休息", "进行", {"app": "微信"}, "APP_NOT_OFFERED", "app", ()),
    ("地点：公司", "步行", {"app": "美团"}, "APP_NOT_OFFERED", "app", ("高德地图",)),
    ("牛肉面", "购买", {"app": "抖音", "pay_app": "微信"}, "APP_NOT_OFFERED", "app", ("饿了么", "美团")),
    ("牛肉面", "购买", {"app": "美团"}, "PAYMENT_APP_REQUIRED", "pay_app", ("支付宝", "微信")),
    ("柠檬茶", "购买", {"app": "饿了么", "pay_app": "微信"}, "PAYMENT_APP_NOT_BOUND", "pay_app", ("支付宝",)),
    ("牛肉面", "购买", {"pay_app": "高德地图"}, "PAYMENT_APP_NOT_BOUND", "pay_app", ("支付宝", "微信")),
    ("聊天", "进行", {}, "COUNTERPART_REQUIRED", "to", ("人物：Second Test Person",)),
    ("语音通话", "进行", {"app": "微信"}, "COUNTERPART_REQUIRED", "to", ("联系人：王磊",)),
])
def test_explicit_route_errors_have_full_declared_names_and_complete_candidates(target, way, values, code, field, allowed):
    """@brief 应用、付款和对方从不默认猜测，候选只换错误字段并可严格重解析。"""
    _, observation = contract_world()
    outcome, names = resolve(observation, start_payload(target, way, **values))
    issues = combination_issues(outcome.request, names)
    issue = next(issue for issue in issues if issue.code == code and issue.field == field)
    assert issue.host_code == code and issue.allowed_values == allowed
    assert combination_error(outcome.request, names) == (issues[0].code, issues[0].detail)
    for candidate, value in zip(issue.candidates, allowed):
        item = candidate.arguments["items"][0]
        original = outcome.request.items[0]
        assert set(item) == {"target", "way", "app", "to", "pay_app"}
        assert item == {**{key: getattr(original, key) for key in item}, field: value}
        StartActivityRequest.model_validate(candidate.arguments)
    assert len(issue.candidates) == min(len(allowed), 3)


@pytest.mark.parametrize("target,way,values,fields", [
    ("自己", "等待", {"app": "微信", "to": "王磊", "pay_app": "支付宝"}, {"app", "to", "pay_app"}),
    ("自己", "睡觉", {"app": "微信", "to": "王磊", "pay_app": "支付宝"}, {"app", "to", "pay_app"}),
    ("地点：公司", "步行", {"to": "王磊", "pay_app": "支付宝"}, {"to", "pay_app"}),
    ("安静休息", "进行", {"to": "王磊", "pay_app": "支付宝"}, {"to", "pay_app"}),
])
def test_unused_fields_are_null_only_and_all_independent_errors_are_reported(target, way, values, fields):
    """@brief 方式不接受的键一次全报，位置与 JSON null 合法值保持精确。"""
    _, observation = contract_world()
    outcome, names = resolve(observation, start_payload(target, way, **values))
    issues = combination_issues(outcome.request, names)
    assert {issue.field for issue in issues} == fields
    assert all(issue.code == "ROUTE_FIELDS_INVALID" and issue.allowed_values == (None,) for issue in issues)


@pytest.mark.parametrize("app", ["wechat", " 微信 ", "应用：微信", "未登记应用"])
def test_app_names_require_exact_registered_name_instead_of_id_or_normalization(app):
    """@brief 应用名称不接受内部身份、类别标签或模糊归一。"""
    _, observation = contract_world()
    outcome, names = resolve(observation, start_payload("语音通话", "进行", app=app, to="王磊"))
    assert combination_issues(outcome.request, names)[0].code == "APP_NOT_OFFERED"


@pytest.mark.parametrize("mode", ["text", "voice"])
def test_social_category_and_explicit_app_binding_determine_counterpart_without_name_guess(mode):
    """@brief 同名动作明示文字或语音都用本人联系人，不从动作名称推断通信方式。"""
    _, observation = contract_world()
    action = next(row for row in observation["actions"] if row["action_id"] == "voice-call")
    action["communication_mode"] = mode
    outcome, names = resolve(observation, start_payload("语音通话", "进行", app="微信", to="王磊"))
    assert not combination_issues(outcome.request, names)
    routed = routed_selection(list(outcome.rows), outcome.request, names)[0]
    assert routed["app_id"] == routed["selected_action"]["app_id"] == "wechat"
    assert routed["counterpart"] == "contact-colleague"
    wrong, _ = resolve(observation, start_payload("语音通话", "进行", app="微信", to="Second Test Person"))
    assert combination_issues(wrong.request, names)[0].code == "CONTACT_NOT_FOUND"
    local, _ = resolve(observation, start_payload("聊天", "进行", to="Second Test Person"))
    assert not combination_issues(local.request, names)
    assert routed_selection(list(local.rows), local.request, names)[0]["counterpart"] == "agent:2"
    wrong, _ = resolve(observation, start_payload("聊天", "进行", to="王磊"))
    assert combination_issues(wrong.request, names)[0].code == "SOCIAL_PARTNER_NOT_FOUND"


def test_co_present_and_contact_candidates_are_distinct_and_absent_people_are_not_local_partners():
    """@brief 私有全世界收货人表不能让异地人物成为此刻面对面对象。"""
    _, observation = contract_world()
    observation["interaction_bindings"] = [row for row in observation["interaction_bindings"] if row["kind"] != "person"]
    outcome, names = resolve(observation, start_payload("聊天", "进行", to="Second Test Person"))
    assert "人物：Second Test Person" in names.people and not names.present_people
    assert combination_issues(outcome.request, names)[0].code == "SOCIAL_PARTNER_NOT_FOUND"
    missing, _ = resolve(observation, start_payload("聊天", "进行"))
    issue = combination_issues(missing.request, names)[0]
    assert issue.code == "COUNTERPART_REQUIRED" and issue.allowed_values == () and issue.candidates == ()


@pytest.mark.parametrize("app,pay_app,app_id,payment", [
    ("美团", "微信", "meituan", "wechat"), (None, "支付宝", None, "alipay"), (None, None, None, None),
])
def test_purchase_keeps_explicit_app_payment_and_recipient_id(app, pay_app, app_id, payment):
    """@brief 空值确指不用应用和本人收，点名值映射到真实身份而无店铺来源键。"""
    _, observation = contract_world("noodle-2")
    outcome, names = resolve(observation, start_payload("牛肉面", "购买", app=app, pay_app=pay_app))
    assert not combination_issues(outcome.request, names)
    row = routed_selection(list(outcome.rows), outcome.request, names)[0]
    assert row["app_id"] == app_id and row["payment_app_id"] == payment
    assert row["recipient_id"] == "agent:1" and row["to_self"]
    assert set(ROUTE_KEYS).issubset(row) and "from" not in row


def test_navigation_is_the_explicit_app_and_collection_rejects_all_route_values():
    """@brief 导航写到出行选择参数，领取始终不带导航、付款或收货人。"""
    _, observation = contract_world()
    outcome, names = resolve(observation, start_payload("地点：公司", "步行", app="高德地图"))
    assert not combination_issues(outcome.request, names)
    row = routed_selection(list(outcome.rows), outcome.request, names)[0]
    assert row["selected_action"]["navigation_app_id"] == "amap"
    collected = replace(outcome.request, items=(replace(outcome.request.items[0], way="领取",
                        app="美团", to="人物：Second Test Person", pay_app="微信"),))
    assert {issue.field for issue in combination_issues(collected, names)} == {"app", "to", "pay_app"}


def test_multi_app_action_selects_the_written_app_in_both_execution_and_outer_route():
    """@brief 多应用动作不能沿用绑定第一行的应用，执行参数也必须是人物明确点名值。"""
    _, observation = contract_world()
    for app, identity in (("抖音", "douyin"), ("哔哩哔哩", "bilibili")):
        outcome, known = resolve(observation, start_payload("刷推荐", "进行", app=app))
        assert not combination_issues(outcome.request, known)
        row = routed_selection(list(outcome.rows), outcome.request, known)[0]
        assert row["app"] == app and row["app_id"] == row["selected_action"]["app_id"] == identity


def test_message_association_retains_original_sender_condition_and_is_pure():
    """@brief 回应仅链接给真正消息发件人的项，两个独立项不能误挂同一消息。"""
    _, observation = contract_world()
    observation["message_bindings"] = [{"name": "购买请求", "message_id": "message-1", "sender_id": "agent:2"}]
    payload = start_payload("牛肉面", "购买", app="美团", pay_app="微信", to="Second Test Person")
    payload["items"].append({"target": "薯片", "way": "购买", "app": "美团", "to": None, "pay_app": "微信"})
    payload["answering"] = "购买请求"
    outcome, names = resolve(observation, payload)
    before = copy.deepcopy(observation)
    assert not combination_issues(outcome.request, names)
    first, second = routed_selection(list(outcome.rows), outcome.request, names)
    assert first["request_message_id"] == "message-1" and second["request_message_id"] is None
    assert first["answering"] == "购买请求" and second["answering"] is None
    assert observation == before
    wrong = replace(outcome.request, answering="不存在的消息")
    assert combination_issues(wrong, names)[0].field == "answering"
    wrong = replace(outcome.request, items=(outcome.request.items[1],))
    assert combination_issues(wrong, names)[0].field == "answering"


def test_route_candidates_preserve_the_full_batch_and_answering_when_only_one_field_changes():
    _, observation = contract_world()
    observation["message_bindings"] = [{"name": "王磊 10:35 的消息", "message_id": "m", "sender_id": "contact-colleague"}]
    payload = {"items": [start_payload("牛肉面", "购买", app="美团", to="王磊", pay_app="微信")["items"][0],
                          start_payload("刷推荐", "进行", app="微信")["items"][0]], "answering": "王磊 10:35 的消息"}
    outcome, names = resolve(observation, payload)
    issue = next(issue for issue in combination_issues(outcome.request, names) if issue.field == "app")
    assert issue.index == 1
    for candidate in issue.candidates:
        assert len(candidate.arguments["items"]) == 2
        assert candidate.arguments["answering"] == payload["answering"]
        assert candidate.arguments["items"][0] == {**payload["items"][0], "to": "联系人：王磊"}
        assert candidate.arguments["items"][1] == {**payload["items"][1], "app": candidate.arguments["items"][1]["app"]}
        assert not combination_issues(StartActivityRequest.model_validate(candidate.arguments), names)
