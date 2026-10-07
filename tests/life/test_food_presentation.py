"""@brief 实际商品候选资格、分页与按商品身份的呈现留痕。"""

from dataclasses import replace

import pytest

from worldmock.life.candidates import recall
from worldmock.life.discovery import DiscoveryContext
from worldmock.life.observe import _supply_entries, _presentation_stamps
from worldmock.life.presentation import PresentationContext, candidate_inputs, entry_sources, present
from tests.life.purchase_worlds import registered_world


def test_product_targets_use_current_store_or_real_delivery_apps():
    semantics, state = registered_world()
    entries = _supply_entries(DiscoveryContext(state, semantics.scenario, semantics, state.agents[1]))
    assert {entry.object_id for entry in entries} == {"beef-noodles", "chips", "lemon-tea"} and not any(entry.row["store_id"] == "noodle-2" for entry in entries)
    assert all(semantics.catalog.apps_by_store[entry.row["store_id"]] for entry in entries)


def test_real_food_candidates_page_without_gaps_using_the_actual_order_key():
    semantics, state = registered_world("noodle-2")
    agent = state.agents[1]
    entries = _supply_entries(DiscoveryContext(state, semantics.scenario, semantics, agent))
    inputs = candidate_inputs(PresentationContext(state, semantics.scenario, semantics, agent), entry_sources(entries))
    inputs = replace(inputs, presentation_limit=2)
    seen, cursor = [], None
    while True:
        window = present(entries, inputs, "product", cursor)
        seen.extend(window.presented)
        cursor = window.next_cursor
        if cursor is None:
            break
    assert len(seen) == len(entries) == len(set(seen))
    assert set(seen) == {entry.object_id for entry in entries}


def test_goods_exposure_stamps_are_shared_by_goods_id_across_stores():
    semantics, state = registered_world()
    stamps = _presentation_stamps(state, ("noodle-1:beef-noodles", "noodle-2:beef-noodles", "chips"),
                                  semantics.catalog)
    assert stamps == {"beef-noodles": state.current_time, "chips": state.current_time}


def test_actual_menu_sources_are_activated_and_never_claim_browse_history():
    semantics, state = registered_world()
    entries = _supply_entries(DiscoveryContext(state, semantics.scenario, semantics, state.agents[1]))
    sources = entry_sources(entries)
    assert set(sources) == {"on_site", "activated"}
    assert {entry.object_id for entry in sources["activated"]} == {entry.object_id for entry in entries}
    assert sources["on_site"] == ()
    context = PresentationContext(state, semantics.scenario, semantics, state.agents[1])
    page = recall(candidate_inputs(context, sources))
    assert page.source_counts == {"on_site": 0, "activated": len(entries)}


def test_real_goods_sources_partition_on_site_and_activated_without_duplication():
    """@brief 真实现场商品与菜单商品合并为完整商品集，各件只属于一个分区，不改世界或目录。"""
    semantics, state = registered_world("noodle-1")
    context = DiscoveryContext(state, semantics.scenario, semantics, state.agents[1])
    entries = _supply_entries(context)
    on_site = frozenset(entry.object_id for entry in entries if entry.row["store_id"] == state.agents[1].location_id)
    assert on_site
    before = state.model_dump_json()
    sources = entry_sources(entries, on_site)
    onsite_ids = {entry.object_id for entry in sources["on_site"]}
    activated_ids = {entry.object_id for entry in sources["activated"]}
    assert onsite_ids == on_site and onsite_ids.isdisjoint(activated_ids)
    assert onsite_ids | activated_ids == {entry.object_id for entry in entries}
    inputs = candidate_inputs(PresentationContext(state, semantics.scenario, semantics, state.agents[1]), sources)
    page = recall(inputs)
    assert len(page.generated) == len(entries) and page.total == len(entries)
    assert page.source_counts == {"on_site": len(onsite_ids), "activated": len(activated_ids)}
    assert state.model_dump_json() == before


def test_retired_carried_source_keyword_is_rejected_instead_of_ignored():
    """@brief 真实商品菜单不接受持有来源参数，即使对象已在目录中也不能恢复库存分区。"""
    semantics, state = registered_world()
    entries = _supply_entries(DiscoveryContext(state, semantics.scenario, semantics, state.agents[1]))
    before = state.model_dump_json()
    with pytest.raises(TypeError, match="unexpected keyword argument 'carried'"):
        entry_sources(entries, carried=frozenset(entry.object_id for entry in entries))
    assert state.model_dump_json() == before
