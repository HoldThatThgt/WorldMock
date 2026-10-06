"""@brief 实际食品候选资格、分页与按食品身份的呈现留痕。"""

from dataclasses import replace
from worldmock.life.discovery import DiscoveryContext
from worldmock.life.observe import _supply_entries, _presentation_stamps
from worldmock.life.presentation import PresentationContext, candidate_inputs, entry_sources, present
from tests.life.purchase_worlds import registered_world


def test_product_targets_use_current_store_or_real_delivery_apps():
    semantics, state = registered_world()
    entries = _supply_entries(DiscoveryContext(state, semantics.scenario, semantics, state.agents[1]))
    assert len(entries) == 6 and not any(entry.row["store_id"] == "noodle-2" for entry in entries)
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


def test_food_exposure_stamps_are_shared_by_food_id_across_stores():
    semantics, state = registered_world()
    stamps = _presentation_stamps(state, ("noodle-1:beef-noodles", "noodle-2:beef-noodles", "lifestyle-walk"),
                                  semantics.catalog)
    assert stamps == {"beef-noodles": state.current_time, "lifestyle-walk": state.current_time}


def test_actual_menu_sources_are_activated_and_never_claim_browse_history():
    semantics, state = registered_world()
    entries = _supply_entries(DiscoveryContext(state, semantics.scenario, semantics, state.agents[1]))
    sources = entry_sources(entries)
    assert "browsed" not in sources
    assert {entry.object_id for entry in sources["activated"]} == {entry.object_id for entry in entries}
    assert sources["on_site"] == sources["carried"] == ()
