"""显示写法的唯一 Python 实现：两种语言共读的用例文件、三句英文故障与冻结目录的严格行模型。"""

import json
import logging
import re

import pytest

from tests.data_paths import ENVIRONMENT_ROOT
from tests.footprints.display_fixtures import freeze_environment, sample_display_names
from worldmock.catalog.errors import RegistrationError
from worldmock.catalog.loader import parse_catalog_model
from worldmock.catalog.models import App, Content, Goods, Location, TravelMethod
from worldmock.errors import IntegrityError
from worldmock.footprints.display_wording import (
    activity_phrase, display_name, display_names, event_content_id, goods_items, phone_phrase,
)
from worldmock.footprints.frozen_environment import frozen_display_names

#: 查看器的 Node 用例读同一份文件。
CASES = json.loads((ENVIRONMENT_ROOT.parent / "display-wording-cases.json").read_text(encoding="utf-8"))
#: 用例文件里五组目录行的键与各自的行模型，次序即 display_names 的入参次序。
_CATALOG_MODELS = (("apps", App), ("contents", Content), ("locations", Location), ("goods", Goods),
                   ("travel_methods", TravelMethod))


def _case_names():
    """用例文件 catalog 一组的目录行经登记时的行模型建出的名称表。"""
    catalog = CASES["catalog"]
    return display_names(*(tuple(parse_catalog_model(row, model) for row in catalog[key])
                           for key, model in _CATALOG_MODELS))


def _plain(names):
    """名称表写成用例文件 names 一组的形状。"""
    return {"apps": dict(names.apps), "goods": dict(names.goods), "locations": dict(names.locations),
            "travel_methods": dict(names.travel_methods),
            "contents": {key: {"title": value.title, "unit": value.unit} for key, value in names.contents.items()}}


def test_shared_cases_give_the_same_names_and_wording_as_the_viewer():
    """名称表等于 names；每条活动、每组事件和每条手机事件的写法等于期望；目录里没有的身份是带种类与身份的故障。"""
    names = _case_names()
    assert _plain(names) == CASES["names"]
    for case in CASES["activities"]:
        assert activity_phrase(names, case["snapshot"], case["content_ids"], case["orders"]) == case["text"], case
    for case in CASES["events"]:
        content_ids = [event_content_id(event["data"]) for event in case["events"]]
        snapshot = case["events"][0]["data"]["activity"]
        assert activity_phrase(names, snapshot, content_ids, {}) == case["text"], case["name"]
    for case in CASES["phone"]:
        assert phone_phrase(names, case["data"]) == case["text"], case["name"]
    for case in CASES["missing"]:
        expected = f"Displayed object is not registered in the frozen environment: {case['kind']} {case['identity']}"
        with pytest.raises(IntegrityError, match=f"^{re.escape(expected)}$"):
            activity_phrase(names, case["snapshot"], case["content_ids"], case["orders"])


def test_unregistered_identity_is_a_fault_with_an_english_log(caplog):
    """查不到的身份先写同文的英文错误日志再抛出，不返回任何降级文字；商品名列表把查名交给调用方的函数。"""
    names = sample_display_names()
    message = "Displayed object is not registered in the frozen environment: app douyin-lite"
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match=f"^{re.escape(message)}$"):
        display_name(names, "app", "douyin-lite")
    assert [record.getMessage() for record in caplog.records if record.levelno == logging.ERROR][0] == message
    assert display_name(names, "location", "home") == "家"
    assert display_name(names, "travel_method", "walking") == "步行"
    assert goods_items(["tissue", "chips", "tissue"], lambda key: names.goods[key]) == ["薯片", "纸巾×2"]


def test_unsupported_kind_and_missing_order_are_faults(caplog):
    """种类既没有专门写法也不在中文种类表里、购买活动的订单记录找不到：两句英文故障各自出现在日志和异常里。"""
    names = sample_display_names()
    unknown = {"kind": "levitate", "location_id": "home", "data": {}}
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="^Unsupported activity kind for "
                                                                                "display: levitate$"):
        activity_phrase(names, unknown, [None], {})
    purchase = {"kind": "purchase", "location_id": "home", "data": {"order_ids": ["order-gone"]}}
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="^Purchase activity has no recorded "
                                                                                "order: order-gone$"):
        activity_phrase(names, purchase, [None], {})
    logged = [record.getMessage() for record in caplog.records if record.levelno == logging.ERROR]
    assert "Unsupported activity kind for display: levitate" in logged
    assert "Purchase activity has no recorded order: order-gone" in logged


def test_frozen_rows_outside_the_current_row_models_are_refused(tmp_path):
    """摘要与字节数都对得上的冻结目录里，app 目录一行多一个字段：照登记时的行模型失败，原因带文件名与行号；
    同一份目录不改时读出的名称等于样例环境建出的名称。"""
    intact = freeze_environment(tmp_path / "intact")
    assert frozen_display_names(tmp_path / "intact", {"environment_assets": intact}) == sample_display_names()
    lines = (ENVIRONMENT_ROOT / "catalog" / "app-catalog.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[4])
    lines[4] = json.dumps({**row, "retired_field": 1}, ensure_ascii=False)
    altered = "".join(line + "\n" for line in lines).encode("utf-8")
    assets = freeze_environment(tmp_path / "altered", replaced={"catalog/app-catalog.jsonl": altered})
    with pytest.raises(RegistrationError, match=r"^ENVIRONMENT_KIND_INVALID: .* app-catalog\.jsonl:5 ") as raised:
        frozen_display_names(tmp_path / "altered", {"environment_assets": assets})
    assert "Extra inputs are not permitted" in str(raised.value)
