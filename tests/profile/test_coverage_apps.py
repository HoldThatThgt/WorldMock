"""@brief 购买渠道的派生投影（docs/dev/SPEC-dayrun-fidelity.md 3.3「渠道的来源」，裁决 S7）：最小场景投影与临时语料根。

渠道从场景钉定的手机配置派生，不新增场景字段：每个购买 App 一个渠道，channel_id 取 App 标识，names 取 app_display_names
的称呼，成员门店取映射到这个 App 的卖家在场景选入的地点里有供给的那些地点（按身份排序，去重）；场景没有钉定手机配置时
没有配置可派生（verified_phone_config 为 None），覆盖率的应用别名取同一份核验过的配置。
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.profile.test_verified_app_names import _CONFIG, _corpus, _scenario
from worldmock.profile.coverage_apps import (PurchaseChannel, purchase_channels, verified_app_names,
                                             verified_phone_config)

#: 三个卖家：两个映射到外卖 App（food），一个映射到生鲜 App（grocery）。
_SELLERS = {"seller-stocked": "food", "seller-noodles": "food", "seller-fresh": "grocery"}
_NAMES = {**_CONFIG["app_display_names"], "food": ["美团外卖", "美团"], "grocery": ["盒马App", "盒马"]}


def _supply(seller: str, location: str) -> SimpleNamespace:
    """一条有库存的供给：卖家与所在地点。"""
    return SimpleNamespace(seller_account_id=seller, location_id=location, initial_stock=3)


def test_channels_group_sellers_by_app_and_list_the_places_where_they_have_supplies(tmp_path) -> None:
    """同一 App 的几个卖家并成一个渠道，成员门店是这些卖家有供给的选入地点（去重、按身份排序），不在选入地点里的供给不算；
    渠道按 App 标识排序，称呼照配置的顺序。"""
    config = {**_CONFIG, "purchase_apps_by_seller": _SELLERS, "app_display_names": _NAMES}
    digest = _corpus(tmp_path, config)
    scenario = _scenario(digest, locations=[SimpleNamespace(location_id=name) for name in ("mall", "noodle", "fresh")],
                         supplies=[_supply("seller-stocked", "mall"), _supply("seller-noodles", "noodle"),
                                   _supply("seller-noodles", "noodle"), _supply("seller-fresh", "fresh"),
                                   _supply("seller-fresh", "unselected-warehouse")])
    verified = verified_phone_config(scenario, tmp_path)
    assert purchase_channels(verified, scenario) == (
        PurchaseChannel("food", ("美团外卖", "美团"), ("mall", "noodle")),
        PurchaseChannel("grocery", ("盒马App", "盒马"), ("fresh",)))
    assert "盒马App" in verified_app_names(scenario, tmp_path)


def test_a_scenario_without_a_pinned_phone_config_has_nothing_to_derive(tmp_path) -> None:
    """场景没有钉定手机配置：核验结果为 None（加载时因此没有渠道），覆盖率的应用别名为空。"""
    digest = _corpus(tmp_path)
    scenario = _scenario(digest, phone_simulation_config=None)
    assert verified_phone_config(scenario, tmp_path) is None
    assert verified_app_names(scenario, tmp_path) == ()
