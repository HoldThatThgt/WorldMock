"""交互菜单声明、场景装配、交集与共享准入判断。"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from worldmock.catalog.affordances import default_affordances
from worldmock.errors import IntegrityError
from worldmock.life.interaction_menu import (
    PROTECTED_ACTIVITY_KINDS,
    PROTECTED_INTERACTION_TOOLS,
    InteractionMenu,
    menu_allows_action,
    menu_allows_activity,
    menu_allows_category,
    resolve_interaction_menu,
)
from worldmock.life.models import REQUEST_MODELS
from worldmock.life.scenario import LifeScenario, RuntimeScenario, load_scenario, parse_scenario_document

_FORMAL = Path("scenarios/white-collar-12-weeks.json")
_LIFE_SCENARIOS = (
    _FORMAL,
    Path("scenarios/multi-person.json"),
    Path("scenarios/multi-person-half-day.json"),
)
_ALL_TOOLS = tuple(REQUEST_MODELS)
_ALL_KINDS = tuple(default_affordances().kinds)


def _payload() -> dict:
    """读取正式生活场景的独立 JSON 对象。"""
    return json.loads(_FORMAL.read_text(encoding="utf-8"))


def _write_document(payload, tmp_path, name):
    """让真实加载器从临时场景引用既有冻结目录与地图。"""
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "catalog").symlink_to(Path("catalog").resolve())
    path = tmp_path / "scenarios" / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _menu(tools: set[str] | None = None, kinds: set[str] | None = None) -> InteractionMenu:
    """构造含全部保护项并按需增加权限的测试菜单。"""
    return InteractionMenu(
        allowed_tools=tuple(sorted(PROTECTED_INTERACTION_TOOLS | (tools or set()))),
        allowed_activity_kinds=tuple(sorted(PROTECTED_ACTIVITY_KINDS | (kinds or set()))),
    )


def _full_menu_payload() -> dict:
    """返回全权限 JSON 菜单声明。"""
    return {
        "allowed_tools": list(_ALL_TOOLS),
        "allowed_activity_kinds": list(_ALL_KINDS),
    }


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"allowed_activity_kinds": list(_ALL_KINDS)}, "Field required"),
        ({"allowed_tools": list(_ALL_TOOLS)}, "Field required"),
        ({"allowed_tools": [], "allowed_activity_kinds": list(_ALL_KINDS)}, "at least 1 item"),
        ({"allowed_tools": list(_ALL_TOOLS), "allowed_activity_kinds": []}, "at least 1 item"),
        ({**_full_menu_payload(), "extra": True}, "Extra inputs are not permitted"),
        ({"allowed_tools": "start_basic_activity", "allowed_activity_kinds": list(_ALL_KINDS)},
         "valid tuple"),
        ({"allowed_tools": list(_ALL_TOOLS), "allowed_activity_kinds": "wait"},
         "valid tuple"),
        ({"allowed_tools": [*_ALL_TOOLS, _ALL_TOOLS[0]],
          "allowed_activity_kinds": list(_ALL_KINDS)}, "must not contain duplicates"),
        ({"allowed_tools": list(_ALL_TOOLS),
          "allowed_activity_kinds": [*_ALL_KINDS, _ALL_KINDS[0]]},
         "must not contain duplicates"),
        ({"allowed_tools": [*_ALL_TOOLS, " "],
          "allowed_activity_kinds": list(_ALL_KINDS)}, "nonblank strings"),
        ({"allowed_tools": list(_ALL_TOOLS),
          "allowed_activity_kinds": [*_ALL_KINDS, " "]}, "nonblank strings"),
        ({"allowed_tools": [*_ALL_TOOLS, "unknown_tool"],
          "allowed_activity_kinds": list(_ALL_KINDS)}, "unknown tool names"),
        ({"allowed_tools": list(_ALL_TOOLS),
          "allowed_activity_kinds": [*_ALL_KINDS, "unknown_kind"]}, "unknown activity kind"),
        ({"allowed_tools": [name for name in _ALL_TOOLS if name != "plan_day"],
          "allowed_activity_kinds": list(_ALL_KINDS)}, "missing protected tools"),
        ({"allowed_tools": list(_ALL_TOOLS),
          "allowed_activity_kinds": [name for name in _ALL_KINDS if name != "sleep"]},
         "missing protected activity kinds"),
    ],
)
def test_interaction_menu_declaration_is_closed(payload: dict, message: str) -> None:
    """字段、名称、唯一性、非空集合和保护闭集都失败关闭。"""
    with pytest.raises(ValidationError, match=message):
        InteractionMenu.model_validate(payload)


@pytest.mark.parametrize("field,name", [
    ("allowed_tools", "start_basic_activity"),
    ("allowed_tools", "plan_day"),
    ("allowed_tools", "revise_day_plan"),
    ("allowed_tools", "set_activity_state"),
    ("allowed_tools", "continue_activity"),
    ("allowed_tools", "change_playback_speed"),
    ("allowed_tools", "game_action"),
    ("allowed_activity_kinds", "wait"),
    ("allowed_activity_kinds", "sleep"),
])
def test_every_specified_protection_must_be_declared(field, name, caplog) -> None:
    """按规格字面值逐项移除保护项，不从生产保护常量复制验收预期。"""
    payload = _full_menu_payload()
    payload[field].remove(name)
    with pytest.raises(ValidationError, match="missing protected"):
        InteractionMenu.model_validate(payload)
    assert "missing protected" in caplog.text


def test_formal_life_scenarios_declare_full_permissions_explicitly() -> None:
    """三份正式生活场景完整加载，地点类全覆盖且人物显式不追加限制。"""
    for path in _LIFE_SCENARIOS:
        raw = json.loads(path.read_text(encoding="utf-8"))
        scenario, digest = load_scenario(path)
        expected_kinds = {item.kind for item in scenario.locations}
        assert set(scenario.interaction_menus_by_location_kind) == expected_kinds
        assert all("interaction_menu" in item and item["interaction_menu"] is None
                   for item in raw["agents"])
        assert all(set(menu.allowed_tools) == set(_ALL_TOOLS)
                   for menu in scenario.interaction_menus_by_location_kind.values())
        assert all(set(menu.allowed_activity_kinds) == set(_ALL_KINDS)
                   for menu in scenario.interaction_menus_by_location_kind.values())
        assert digest == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.pop("interaction_menus_by_location_kind"), "Field required"),
        (lambda value: value["agents"][0].pop("interaction_menu"), "Field required"),
        (lambda value: value.update(interaction_menus_by_location_kind=None), "valid dictionary"),
        (lambda value: value["interaction_menus_by_location_kind"].update(home=None),
         "valid dictionary or instance of InteractionMenu"),
        (lambda value: value["interaction_menus_by_location_kind"].pop("office"),
         "exactly match declared location kinds"),
        (lambda value: value["interaction_menus_by_location_kind"].update(ghost=_full_menu_payload()),
         "exactly match declared location kinds"),
    ],
)
@pytest.mark.parametrize("model", [LifeScenario, RuntimeScenario])
def test_scenario_requires_exact_location_menu_coverage(mutate, message: str, model, tmp_path) -> None:
    """场景和人物字段均必填，地点类菜单既不能缺也不能多。"""
    payload = _payload() if model is LifeScenario else load_scenario(_FORMAL)[0].model_dump(mode="json")
    mutate(payload)
    if model is LifeScenario and message == "exactly match declared location kinds":
        payload["corpus_root"] = str(Path("corpora").resolve())
        path = _write_document(payload, tmp_path, "menu-map-references.json")
        with pytest.raises(IntegrityError, match=message):
            load_scenario(path)
    else:
        with pytest.raises(ValidationError, match=message):
            model.model_validate(payload)


def test_scenario_file_entry_logs_invalid_menu(tmp_path: Path, caplog) -> None:
    """文件入口把菜单准入失败包装为完整性错误并留下英文日志。"""
    payload = _payload()
    payload["interaction_menus_by_location_kind"].pop("office")
    payload["corpus_root"] = str(Path("corpora").resolve())
    path = _write_document(payload, tmp_path, "invalid-menu.json")
    with caplog.at_level(logging.ERROR), pytest.raises(IntegrityError, match="Scenario assembly failed"):
        load_scenario(path)
    assert "Scenario assembly failed" in caplog.text
    assert "interaction menu location kinds" in caplog.text


def test_runtime_assembly_preserves_menu_and_resolution_intersects_person() -> None:
    """运行时场景保留声明，解析结果为地点与人物权限的确定性交集。"""
    scenario, _ = load_scenario(_FORMAL)
    location_menu = scenario.interaction_menus_by_location_kind["home"]
    unrestricted = resolve_interaction_menu(scenario, scenario.agents[0].agent_id, "home")
    assert unrestricted == InteractionMenu(
        allowed_tools=tuple(sorted(location_menu.allowed_tools)),
        allowed_activity_kinds=tuple(sorted(location_menu.allowed_activity_kinds)),
    )
    person_menu = _menu(
        {"start_targeted_activity", "browse_activities", "submit_work"},
        {"work"},
    )
    first = scenario.agents[0].model_copy(update={"interaction_menu": person_menu})
    restricted = scenario.model_copy(update={"agents": (first, *scenario.agents[1:])})
    resolved = resolve_interaction_menu(restricted, first.agent_id, first.home_location_id)
    assert resolved.allowed_tools == tuple(sorted(person_menu.allowed_tools))
    assert resolved.allowed_activity_kinds == tuple(sorted(person_menu.allowed_activity_kinds))


def test_location_and_person_each_remove_the_other_sources_exclusive_permissions() -> None:
    """地点和人物各有独有权限时，两条轴都只留下交集并按名称排序。"""
    scenario, _ = load_scenario(_FORMAL)
    location_menu = _menu(
        {"start_targeted_activity", "browse_activities", "submit_work"}, {"work", "watch"})
    person_menu = _menu(
        {"start_targeted_activity", "browse_activities", "send_message"}, {"work", "play_game"})
    first = scenario.agents[0].model_copy(update={"interaction_menu": person_menu})
    restricted = scenario.model_copy(update={
        "agents": (first, *scenario.agents[1:]),
        "interaction_menus_by_location_kind": {
            **scenario.interaction_menus_by_location_kind, "home": location_menu},
    })
    resolved = resolve_interaction_menu(restricted, first.agent_id, "home")
    assert resolved.allowed_tools == tuple(sorted({
        "start_basic_activity", "plan_day", "revise_day_plan", "set_activity_state",
        "continue_activity", "change_playback_speed", "game_action",
        "start_targeted_activity", "browse_activities",
    }))
    assert resolved.allowed_activity_kinds == ("sleep", "wait", "work")


def test_resolution_rejects_missing_or_corrupt_runtime_identity() -> None:
    """损坏的人物、地点或人物菜单不会回退为全权限。"""
    scenario, _ = load_scenario(_FORMAL)
    with pytest.raises(IntegrityError, match="agent identity"):
        resolve_interaction_menu(scenario, 999, "home")
    with pytest.raises(IntegrityError, match="location identity"):
        resolve_interaction_menu(scenario, 1, "missing")
    broken_agent = scenario.agents[0].model_copy(update={"interaction_menu": "broken"})
    broken = scenario.model_copy(update={"agents": (broken_agent,)})
    with pytest.raises(IntegrityError, match="agent declaration"):
        resolve_interaction_menu(broken, 1, "home")
    no_mapping = scenario.model_copy(update={"interaction_menus_by_location_kind": None})
    with pytest.raises(IntegrityError, match="location mapping"):
        resolve_interaction_menu(no_mapping, 1, "home")
    bad_mapping = scenario.model_copy(
        update={"interaction_menus_by_location_kind": {"home": "broken"}},
    )
    with pytest.raises(IntegrityError, match="current location kind"):
        resolve_interaction_menu(bad_mapping, 1, "home")


def test_activity_permission_uses_registry_kind_and_existing_start_tool() -> None:
    """共享启动工具内的类型单独收窄，社交活动只依赖既有社交工具。"""
    menu = _menu(
        {"start_targeted_activity", "start_social_activity"},
        {"work", "lifestyle"},
    )
    assert menu_allows_activity(menu, "wait")
    assert menu_allows_activity(menu, "lifestyle:yoga")
    assert menu_allows_activity(menu, "work")
    assert menu_allows_activity(menu, "chat")
    assert menu_allows_activity(menu, "call")
    assert not menu_allows_activity(_menu(), "chat")
    assert not menu_allows_activity(menu, "watch")
    assert not menu_allows_activity(menu, "missing")


def test_category_permission_follows_existing_downstream_qualifications() -> None:
    """五类下钻参数按冻结规格映射，未知品类不由菜单创造。"""
    menu = _menu(
        {"browse_activities", "start_targeted_activity", "purchase"},
        {"play_game", "work", "watch", "lifestyle"},
    )
    for category in ("game_session", "work_contract", "feature_film", "sport", "foods"):
        assert menu_allows_category(menu, category)
    assert not menu_allows_category(menu, "missing")
    assert not menu_allows_category(_menu({"start_targeted_activity"}, {"watch"}), "feature_film")
    assert menu_allows_category(_menu({"browse_activities", "consume"}, {"consume"}), "foods")
    assert not menu_allows_category(_menu({"browse_activities"}), "foods")


@pytest.mark.parametrize(
    ("tool", "action_request", "activity_kind", "expected"),
    [
        ("start_basic_activity", {"activity_type": "wait"}, None, True),
        ("start_basic_activity", {"activity_type": "lifestyle:yoga"}, None, False),
        ("start_targeted_activity", {"activity_type": "work"}, None, True),
        ("start_targeted_activity", {"activity_type": "watch"}, None, False),
        ("start_travel", {}, None, True),
        ("consume", {}, None, True),
        ("browse_activities", {"category": "work_contract"}, None, True),
        ("set_activity_state", {"operation": "resume"}, "work", True),
        ("set_activity_state", {"operation": "resume"}, "watch", False),
        ("set_activity_state", {"operation": "resume"}, None, True),
        ("set_activity_state", {"operation": "cancel"}, "watch", True),
        ("submit_work", {}, None, True),
        ("purchase", {}, None, False),
        ("unknown", {}, None, False),
    ],
)
def test_action_permission_covers_tools_parameters_browse_and_resume(
    tool: str,
    action_request: dict,
    activity_kind: str | None,
    expected: bool,
) -> None:
    """统一入口精确处理工具、活动参数、下钻与恢复，原领域校验继续负责其他参数。"""
    menu = _menu(
        {"start_targeted_activity", "start_travel", "consume", "browse_activities", "submit_work"},
        {"work", "travel", "consume"},
    )
    assert menu_allows_action(menu, tool, action_request, activity_kind) is expected


def test_menu_change_alters_raw_scenario_identity(tmp_path: Path) -> None:
    """地点与人物菜单声明都参与原始场景字节哈希，不另造菜单版本。"""
    original = _payload()
    location_changed = _payload()
    person_changed = _payload()
    location_changed["interaction_menus_by_location_kind"]["office"]["allowed_tools"].remove("purchase")
    person_changed["agents"][0]["interaction_menu"] = _full_menu_payload()
    corpus_root = str(Path("corpora").resolve())
    documents = (original, location_changed, person_changed)
    paths = tuple(tmp_path / f"scenario-{index}.json" for index in range(len(documents)))
    for path, document in zip(paths, documents, strict=True):
        document["corpus_root"] = corpus_root
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    parsed = tuple(parse_scenario_document(path) for path in paths)
    assert parsed[0][0].interaction_menus_by_location_kind != parsed[1][0].interaction_menus_by_location_kind
    assert parsed[0][0].agents[0].interaction_menu != parsed[2][0].agents[0].interaction_menu
    assert len({digest for _, digest in parsed}) == len(parsed)
