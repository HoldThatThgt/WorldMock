"""@brief 七种方式、四个内部动词与统一动作菜单的当前闭集。"""

import pytest
from pydantic import ValidationError

from tests.life.action_contract_world import contract_world
from tests.life.test_name_resolution import start_payload
from worldmock.catalog.affordances import START_ACTIVITY_ACTIONS
from worldmock.errors import IntegrityError
from worldmock.life.interaction_menu import InteractionMenu, activity_phrase, binding_action, menu_allows_action
from worldmock.life.interaction_menu import merged_selection, public_action
from worldmock.life.models import REQUEST_MODELS, StartActivityRequest
from worldmock.life.name_resolution import name_book, resolve_start
from worldmock.life.vocabulary import ACTION_WAYS, LIFE_VERBS, VERB_WAY_SEPARATOR, WAYS


def test_every_way_maps_to_one_verb_and_covers_every_internal_action():
    assert {canonical for _,canonical in WAYS.values()} == START_ACTIVITY_ACTIONS
    assert {verb for verb,_ in WAYS.values()} == set(LIFE_VERBS)
    assert LIFE_VERBS == ("travel","interact","obtain","sleep")
    assert set(WAYS)=={"步行","驾车","睡觉","等待","购买","进行","领取"}
    for action,way in ACTION_WAYS.items():
        assert WAYS[way][1]==action
    for retired in ("到店购买","远程下单","食用","打开","游玩","制作"):
        assert retired not in WAYS


def test_binding_action_projects_verb_and_way_publicly_and_keeps_execution_private():
    drive=binding_action("驾车","travel_to",travel_mode="driving")
    assert drive == {"verb":"travel","label":"驾车","canonical_action":"travel_to",
                     "travel_mode":"driving","note":None}
    assert public_action(drive)=={"action":f"travel{VERB_WAY_SEPARATOR}驾车","note":None}
    assert public_action(binding_action("购买","purchase"))=={"action":"obtain：购买","note":None}
    assert public_action(binding_action("进行","do"))["action"]=="interact：进行"
    with pytest.raises(IntegrityError):
        binding_action("步行","wait")
    assert activity_phrase(drive)=="驾车前往"
    assert activity_phrase(binding_action("进行","do"))=="进行"


def test_merged_selection_combines_only_purchase_and_keeps_other_actions_independent():
    assert merged_selection(["do"],["进行"])
    assert merged_selection(["purchase","purchase"],["购买","购买"])
    for actions,ways in ((["do","do"],["进行","进行"]),
                         (["travel_to","do"],["步行","进行"]),
                         (["wait","wait"],["等待","等待"])):
        assert not merged_selection(actions,ways)


def test_scenario_menus_admit_only_current_start_actions():
    context,_=contract_world()
    for menu in context.scenario.interaction_menus_by_location_kind.values():
        assert set(menu.allowed_actions) <= START_ACTIVITY_ACTIONS
        assert {"wait","sleep"} <= set(menu.allowed_actions)
    assert "send_message" in REQUEST_MODELS and "submit_work" not in REQUEST_MODELS


def test_selection_resolves_exact_action_and_way_synonym_and_menu_judges_canonical_actions():
    _,observed=contract_world()
    book=name_book(observed)
    resolved=resolve_start(book,StartActivityRequest.model_validate(start_payload("公司","步行前往")))
    assert not resolved.failures and resolved.rows[0]["selected_action"]["canonical_action"]=="travel_to"
    assert resolved.request.items[0].way=="步行"
    assert resolve_start(book,StartActivityRequest.model_validate(start_payload("刷推荐","打开"))).failures
    menu=InteractionMenu(allowed_actions=("sleep","wait"))
    assert not menu_allows_action(menu,"start_activity",["travel_to"])
    assert not menu_allows_action(menu,"start_activity",["wait","travel_to"])
    assert not menu_allows_action(menu,"start_activity",[])
    assert menu_allows_action(menu,"start_activity",["wait"])
    assert menu_allows_action(menu,"send_message",[])
    with pytest.raises(ValidationError):
        InteractionMenu(allowed_actions=("sleep","submit","wait"))
    with pytest.raises(ValidationError):
        InteractionMenu(allowed_actions=("travel_to","wait"))
