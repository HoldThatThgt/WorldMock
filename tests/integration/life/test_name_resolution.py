"""@brief 新动作名称、七种方式与完整五键候选的纯解析证据。"""

import copy
import sys

import pytest

from tests.life.action_contract_world import contract_world
from tests.unit.catalog.test_supplement import add_stations
from worldmock.catalog.loader import load_catalog
from worldmock.life import name_resolution, vocabulary
from worldmock.life.models import StartActivityRequest
from worldmock.life.name_resolution import name_book, resolve_start


def start_payload(target="自己", way="等待", **values):
    """@brief 每个原生请求显式填写全部五个键。"""
    return {"items": [{"target": target, "way": way, "app": None, "to": None, "pay_app": None, **values}],
            "answering": None}


@pytest.mark.parametrize("retired", ["工作", "打开", "交谈", "通话", "阅读", "到店", "远程下单", "食用", "使用", "给予",
                                   "上班", "刷", "聊天", "打电话", "看邮件", "外卖", "到店购买", "吃"])
def test_retired_ways_and_their_synonyms_are_refused(retired):
    """@brief 旧方式和只为它们服务的同义写法直接删除，不再能反推动作。"""
    _, observation = contract_world()
    outcome = resolve_start(name_book(observation), StartActivityRequest.model_validate(start_payload(way=retired)))
    assert outcome.failures[0].item.error_code == "WAY_NOT_OFFERED"
    assert outcome.failures[0].item.allowed_values == tuple(vocabulary.WAYS)
    assert vocabulary.way_options(retired) == frozenset()


def test_ways_have_exactly_the_seven_declared_members_and_no_source_exports():
    """@brief 新闭集包含领取而不新增活动种类，旧来源名称 API 不再导出。"""
    assert tuple(vocabulary.WAYS) == ("步行", "驾车", "睡觉", "等待", "购买", "进行", "领取")
    assert {canonical for _, canonical in vocabulary.WAYS.values()} == {
        "travel_to", "sleep", "wait", "purchase", "do", "collect_parcel"}
    for name in ("PURCHASE_WAYS", "NAMED_PURCHASE_WAYS", "SYSTEM_PURCHASE_WAY"):
        assert not hasattr(vocabulary, name)
    for name in ("resolve_source", "delivery_sources", "near_source_names", "SourceMatch", "CATALOG_KINDS"):
        assert not hasattr(name_resolution, name)


def test_registered_action_name_is_exact_and_app_is_not_an_activity_target():
    """@brief 动作只用登记名称；近名返回候选，应用仅是五键中的显式应用值。"""
    _, observation = contract_world()
    book = name_book(observation)
    good = resolve_start(book, StartActivityRequest.model_validate(start_payload("刷推荐", "进行", app="哔哩哔哩")))
    assert not good.failures and good.rows[0]["object_id"] == "browse-feed"
    assert good.request.items[0].app == "哔哩哔哩"
    for name in ("刷推荐 ", "活动：刷推荐", "刷推荐视频", "哔哩哔哩", "办公主题"):
        result = resolve_start(book, StartActivityRequest.model_validate(start_payload(name, "进行")))
        assert result.failures[0].item.error_code == "NAME_NOT_FOUND"
    failure = resolve_start(book, StartActivityRequest.model_validate(start_payload("刷推荐视频", "进行",
                            app="抖音", to="人物：Second Test Person", pay_app="微信"))).failures[0]
    assert failure.item.candidates
    candidate = failure.item.candidates[0].arguments
    assert candidate == start_payload("刷推荐", "进行", app="抖音", to="人物：Second Test Person", pay_app="微信")
    assert set(candidate) == {"items", "answering"}
    assert set(candidate["items"][0]) == {"target", "way", "app", "to", "pay_app"}


def test_unlisted_registered_action_is_resolvable_even_when_owned_content_is_exhausted():
    """@brief 工具显示集合不限定动作名称解析，受理才检查本人是否有合格内容。"""
    _, observation = contract_world()
    observation["interaction_bindings"] = [row for row in observation["interaction_bindings"]
                                            if row["object"] != "办公"]
    result = resolve_start(name_book(observation), StartActivityRequest.model_validate(start_payload("办公", "进行")))
    assert not result.failures
    assert result.rows[0]["kind"] == "action" and result.rows[0]["object_id"] == "office-work"
    assert result.rows[0]["selected_action"]["canonical_action"] == "do"


def test_location_home_and_current_ways_are_normalized_without_app_guess():
    """@brief 住处与现存方式同义词沿用无损解析，应用原值不能被名称归一自动改写。"""
    _, observation = contract_world("office")
    result = resolve_start(name_book(observation), StartActivityRequest.model_validate(start_payload("家", "走路",
                           app=" 高德地图 ")))
    assert not result.failures
    assert result.request.items[0].target == "地点：家"
    assert result.request.items[0].way == "步行"
    assert result.request.items[0].app == " 高德地图 "
    assert result.request.items[0].to is None and result.request.items[0].pay_app is None
    assert result.resolved == ({"written": "家", "as": "地点：家"}, {"written": "走路", "as": "步行"})


def test_unlisted_goods_are_forwarded_to_single_purchase_resolution():
    """@brief 未展示的商品名称可购买，商品身份和履约由唯一购买解析入口决定。"""
    _, observation = contract_world()
    result = resolve_start(name_book(observation), StartActivityRequest.model_validate(start_payload("未展示商品", "买",
                           app="美团", pay_app="微信")))
    assert not result.failures
    assert result.rows[0]["kind"] == "named_supply"
    assert result.rows[0]["object"] == "未展示商品"
    assert result.request.items[0].way == "购买"
    assert result.request.items[0].app == "美团" and result.request.items[0].pay_app == "微信"


def test_people_are_only_counterpart_candidates_and_old_targets_have_no_offers():
    """@brief 人物和联系人不能作为开始目标提供交谈或通话动作。"""
    _, observation = contract_world()
    for target in ("人物：Second Test Person", "联系人：王磊"):
        result = resolve_start(name_book(observation), StartActivityRequest.model_validate(start_payload(target, "进行")))
        assert result.failures[0].item.error_code == "WAY_NOT_OFFERED"
        assert not result.failures[0].item.allowed_values
        assert not result.failures[0].item.candidates


def test_counterpart_and_answering_use_existing_lossless_name_resolution():
    """@brief 对方实名映射到冻结标签，消息关联仍按可见名称解析。"""
    _, observation = contract_world()
    observation["message_bindings"] = [{"name": "来自同事的请求", "message_id": "message-1", "sender_id": "agent:2"}]
    payload = start_payload("聊天", "进行", to="Second Test Person")
    payload["answering"] = "来自 同事 的请求"
    result = resolve_start(name_book(observation), StartActivityRequest.model_validate(payload))
    assert not result.failures
    assert result.request.items[0].to == "人物：Second Test Person"
    assert result.request.answering == "来自同事的请求"


def test_parcel_station_allows_collection_but_other_places_do_not(catalog_dir):
    """@brief 领取只绑定真实驿站地点，仍为零秒原生行为的私有绑定。"""
    _, observation = contract_world()
    add_stations(catalog_dir)
    station = load_catalog(catalog_dir).locations["parcel"]
    observation["locations"].append({"location_id": station.location_id, "name": station.name, "kind": station.kind})
    result = resolve_start(name_book(observation), StartActivityRequest.model_validate(start_payload("地点：测试驿站", "领取")))
    assert not result.failures and result.rows[0]["object_id"] == "parcel"
    assert result.rows[0]["selected_action"]["canonical_action"] == "collect_parcel"
    wrong = resolve_start(name_book(observation), StartActivityRequest.model_validate(start_payload("地点：公司", "领取")))
    assert wrong.failures[0].item.error_code == "WAY_NOT_OFFERED"


def test_name_resolution_is_pure_and_does_not_import_life_engine():
    """@brief 重复解析相同冻结观察逐字相同，原始观察和请求保持不变。"""
    engine_before = sys.modules.get("worldmock.life.engine")
    _, observation = contract_world()
    payload = start_payload("刷推荐", "进行", app="抖音")
    before = copy.deepcopy(observation)
    first = resolve_start(name_book(observation), StartActivityRequest.model_validate(payload))
    second = resolve_start(name_book(observation), StartActivityRequest.model_validate(payload))
    assert first == second and observation == before
    assert sys.modules.get("worldmock.life.engine") is engine_before
