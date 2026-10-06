"""@brief 固定目录来源名称按单一定义解析，食品可直接指名。"""

import pytest
from worldmock.life.name_resolution import delivery_sources, resolve_source, channel_key
from tests.life.purchase_worlds import menu_context


@pytest.mark.parametrize("name,identity,stores", [("美团", "meituan", {"cafe", "noodle-1"}),
                                              ("饿了么", "eleme", {"cafe"})])
def test_actual_app_name_resolves_to_its_real_registered_stores(name, identity, stores):
    context = menu_context()
    places = {place.location_id: place.display_name for place in context.scenario.locations}
    source = resolve_source(name, places, "home", delivery_sources(context.semantics.catalog))
    assert source.app_id == identity and source.store_ids == frozenset(stores)


def test_source_resolution_recognizes_actual_store_home_and_rejects_unknown_source():
    context = menu_context()
    places = {place.location_id: place.display_name for place in context.scenario.locations}
    apps = delivery_sources(context.semantics.catalog)
    assert resolve_source("二味小炒", places, "home", apps).store_ids == frozenset({"noodle-2"})
    assert resolve_source("家", places, "home", apps).store_ids == frozenset({"home"})
    assert resolve_source("未登记的渠道", places, "home", apps) is None
    assert channel_key("美团") == channel_key(" 美团 ")
