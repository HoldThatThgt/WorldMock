"""@brief 场景七的严格字段、人物初值、主题和保留工作安排合同。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.data_paths import ENVIRONMENT_ROOT
from tests.life.work_schedule_fixtures import STANDARD_SHIFT, weekday_schedule
from worldmock.catalog import load_catalog
from worldmock.errors import IntegrityError
from worldmock.life.scenario import LifeScenario, RuntimeScenario, parse_scenario_document
from worldmock.life.semantics import assemble_semantics
from worldmock.life.world_setup import load_scenario

_FORMAL = ENVIRONMENT_ROOT / "scenarios/single-person.json"
_CATALOG = ENVIRONMENT_ROOT / "catalog"
_RETIRED_FIELDS = (
    "map_dataset", "products", "supplies", "tasks", "contents", "games", "price_changes", "product_releases",
    "recipe_output_bindings", "phone_simulation_config", "phone_simulation_config_sha256", "catalog_version",
    "retrieval_index_threshold",
)


def _payload() -> dict:
    """@brief 每个字段反例使用独立的真实人工场景对象。"""
    payload = json.loads(_FORMAL.read_text(encoding="utf-8"))
    payload["agents"][0].update(chronotype_midsleep=4.0, sleep_need_hours=7.5,
                               lunch_window=[12.0, 13.0], dinner_window=[18.5, 20.0], bedtime_window=[23.0, 24.0])
    return payload


def _replace(path: tuple, value: object) -> dict:
    """@brief 改写一条真实场景字段。"""
    payload = _payload()
    target = payload
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    return payload


def _assert_invalid(payload: dict, message: str) -> None:
    """@brief 场景模型在加载前直接拒绝无效声明。"""
    with pytest.raises(ValidationError, match=message):
        LifeScenario.model_validate(payload)


def _mutated_scenario(source: Path, mutate, target: Path) -> Path:
    """@brief 隔离复制真实环境后只修改场景文档。"""
    shutil.copytree(source.parent.parent, target)
    path = target / "scenarios" / source.name
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.mark.parametrize("field", _RETIRED_FIELDS)
def test_structure_seven_rejects_each_retired_scenario_field(field) -> None:
    """@brief 每个退役顶层字段分别按未知字段拒绝。"""
    _assert_invalid({**_payload(), field: {}}, "Extra inputs are not permitted")


@pytest.mark.parametrize("field", ["skill_levels", "qualifications"])
def test_structure_seven_rejects_retired_agent_fields(field) -> None:
    """@brief 画像声明不能继续携带技能或资格字段。"""
    _assert_invalid(_replace(("agents", 0, field), {}), "Extra inputs are not permitted")


def test_structure_seven_rejects_body_and_message_retired_fields() -> None:
    """@brief 身体规则不能由场景覆盖，邮件不能继续引用旧工作合同。"""
    _assert_invalid(_replace(("body", "entertainment_relief_preference_floor"), 0.5), "Extra inputs")
    message = {"message_id": "m", "thread_id": "t", "recipient_agent_id": 1,
               "sent_at": _payload()["start_at"], "subject": "test", "body": "test", "related_task_id": "task"}
    _assert_invalid(_replace(("messages",), [message]), "Extra inputs")


@pytest.mark.parametrize("version", [4, 5, 6])
def test_structure_seven_rejects_old_scenario_version(version) -> None:
    """@brief 旧结构号不会触发迁移或字段补齐。"""
    _assert_invalid(_replace(("schema_version",), version), "Input should be 7")


@pytest.mark.parametrize("action", ["watch", "play", "browse"])
def test_structure_seven_rejects_retired_menu_actions(action) -> None:
    """@brief 八种地点菜单只能声明新的固定动作。"""
    _assert_invalid(_replace(("interaction_menus_by_location_kind", "home", "allowed_actions"),
                             ["wait", "sleep", action]), "action")


@pytest.mark.parametrize("kind", ["merchant", "wet_market"])
def test_structure_seven_rejects_retired_menu_kinds(kind) -> None:
    """@brief 旧地点类别直接被固定枚举拒绝。"""
    _assert_invalid(_replace(("interaction_menus_by_location_kind", kind),
                             {"allowed_actions": ["wait", "sleep"]}), "Input should be")


def test_formal_and_multi_person_scenarios_are_valid() -> None:
    """@brief 三份人工场景走真实加载，固定规则和身体实例形成唯一运行视图。"""
    for path in sorted((ENVIRONMENT_ROOT / "scenarios").glob("*.json")):
        document, expected_digest = parse_scenario_document(path)
        scenario, digest = load_scenario(path)
        assert digest == expected_digest and len(digest) == 64
        assert scenario.schema_version == document.schema_version == 7
        assert scenario.body.basal_kcal_per_hour == 65
        assert scenario.body.source_kind == "worldmock:1"
        assert scenario.transport_account_id == "transport:service"
        assert scenario.agents[0].work_topic_ids == ("topic-sales",)


@pytest.mark.parametrize("holding", [{"home": {"chips": 0}}, {"home": {"chips": -1}},
                                      {"home": {"chips": float("inf")}}, {"unknown": {"chips": 1}}])
def test_initial_inventory_shape_rejects_invalid_holdings(holding) -> None:
    """@brief 初始食品持有不能包含非正份数或未知地点。"""
    _assert_invalid(_replace(("agents", 0, "initial_inventory"), holding), "inventory")


@pytest.mark.parametrize(("path", "value"), [(("body", "mass_kg"), 0),
    (("body", "initial_entertainment_need"), 1.5), (("body", "initial_energy_kcal"), -1),
    (("max_same_moment_decisions",), 0), (("activity_feedback_seconds",), 59)])
def test_body_instance_and_rejection_ranges_are_enforced(path, value) -> None:
    """@brief 人物初值和场景节律仍具有真实数值边界。"""
    _assert_invalid(_replace(path, value), "must|than")


@pytest.mark.parametrize(
    ("bedtime", "message"),
    [
        # 起点越界：负起点与 ≥24 起点（跨午夜口径只放宽末端，不放宽起点）。
        ((-1.0, 6.0), "0 <= start < 24"),
        ((24.0, 25.5), "0 <= start < 24"),
        # 末端越界：end ≤ start 的退化区间与 end > 30（次日 06:00 上限）。
        ((22.5, 22.5), "start < end <= 30"),
        ((23.0, 30.5), "start < end <= 30"),
        # 跨度病态区间：12.5h 超过 12h 上限（范围检查本身合法）。
        ((10.0, 22.5), "span must not exceed 12 hours"),
        # 压到晚餐窗口：陈曦晚餐 [18.5, 20.0]，就寝起点 19.5 < 20.0。
        ((19.5, 28.0), "must not overlap dinner window"),
    ],
)
def test_bedtime_window_bounds_and_overlap_are_rejected(
    bedtime: tuple[float, float], message: str
) -> None:
    """就寝窗口起点/末端越界、跨度超限与压到晚餐窗口均在文档校验层拒绝。"""
    _assert_invalid(_replace(("agents", 0, "bedtime_window"), list(bedtime)), message)


def test_cross_midnight_bedtime_window_is_accepted() -> None:
    """正例：合法跨午夜就寝窗口（23:30 入睡、末端 25.5=次日 01:30）通过校验。"""
    document = LifeScenario.model_validate(
        _replace(("agents", 0, "bedtime_window"), [23.5, 25.5]))
    assert document.agents[0].bedtime_window == (23.5, 25.5)


@pytest.mark.parametrize(
    ("windows", "message"),
    [
        # 下班、到家窗口与午餐、晚餐同单位同校验：负起点、末端越过 24（不跨午夜）、退化区间各一例。
        ({"leave_work_window": [-0.5, 18.0]}, "leave_work_window must satisfy 0 <= start < end <= 24"),
        ({"leave_work_window": [23.5, 24.5]}, "leave_work_window must satisfy 0 <= start < end <= 24"),
        ({"arrive_home_window": [19.0, 19.0]}, "arrive_home_window must satisfy 0 <= start < end <= 24"),
        ({"arrive_home_window": [23.0, 25.0]}, "arrive_home_window must satisfy 0 <= start < end <= 24"),
        # 两者都声明时下班起点不得晚于到家起点。
        ({"leave_work_window": [19.0, 19.5], "arrive_home_window": [18.5, 20.0]},
         "leave_work_window must not start after arrive_home_window"),
    ],
)
def test_work_window_bounds_and_order_are_rejected(windows: dict, message: str) -> None:
    """场景下班、到家窗口（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-场景作息覆盖 ）：越界、退化与下班晚于到家在文档校验层拒绝；正例为两者
    都声明且起点相等、与午餐、晚餐、就寝不设先后（下班窗口压在晚餐窗口里也合法），未声明时两字段为 None 且不算已设。"""
    payload = _payload()
    payload["agents"][0].update(windows)
    _assert_invalid(payload, message)
    legal = _payload()
    legal["agents"][0].update(leave_work_window=[18.5, 19.0], arrive_home_window=[18.5, 20.0])
    agent = LifeScenario.model_validate(legal).agents[0]
    assert (agent.leave_work_window, agent.arrive_home_window) == ((18.5, 19.0), (18.5, 20.0))
    assert agent.dinner_window[0] <= agent.leave_work_window[0] < agent.dinner_window[1]
    undeclared = LifeScenario.model_validate(_payload()).agents[0]
    assert (undeclared.leave_work_window, undeclared.arrive_home_window) == (None, None)
    assert {"leave_work_window", "arrive_home_window"}.isdisjoint(undeclared.model_fields_set)


def test_runtime_agents_keep_the_declared_rhythm_fields() -> None:
    """组装后的运行时人物只把场景显式写出的字段记为已设（缺省值照旧由模型补齐，值不变），场景作息覆盖卡片据此只覆盖
    显式声明的作息字段（docs/dev/SPEC-life-realism.md#dayrun-fidelity-人设与系统层-场景作息覆盖卡片抽取值 ）；声明全部五个字段的正式场景五个都记为已设。"""
    rhythm = {"chronotype_midsleep", "sleep_need_hours", "lunch_window", "dinner_window", "bedtime_window"}
    payload = _payload()
    for name in rhythm - {"chronotype_midsleep"}:
        del payload["agents"][0][name]
    document = LifeScenario.model_validate(payload)

    agent = assemble_semantics(document, load_catalog(_CATALOG), ENVIRONMENT_ROOT / "corpora").scenario.agents[0]

    assert agent.model_fields_set & rhythm == {"chronotype_midsleep"}
    assert (agent.sleep_need_hours, agent.lunch_window, agent.bedtime_window) == (7.5, None, None)
    assert agent.model_dump() == document.agents[0].model_dump()
    declared = LifeScenario.model_validate(_payload())
    assembled = assemble_semantics(declared, load_catalog(_CATALOG), ENVIRONMENT_ROOT / "corpora").scenario.agents[0]
    assert assembled.model_fields_set & rhythm == declared.agents[0].model_fields_set & rhythm


def _shift(**fields) -> dict:
    """标准班次（09:00–18:00，午休 12:00–13:30）改写给定字段后的副本。"""
    return {**STANDARD_SHIFT, **fields}


def _weekly(day: str, shift: dict):
    """把每周声明的某一天改成给定班次的改写函数。"""
    return lambda schedule: schedule["weekly"].update({day: shift})


_SHIFT_RANGE = "work shift must satisfy 0 <= starts < ends <= 1440"
_BREAK_INSIDE = "work shift breaks must lie within the shift"
_BREAK_ORDER = "work shift breaks must be ascending and must not overlap"
_SEVEN_DAYS = "work_schedule weekly must declare all seven weekdays"
_ISO_DATES = "work_schedule exception dates must use canonical ISO format"


@pytest.mark.parametrize(("edit", "message"), [
    (_weekly("0", _shift(starts=600, ends=600)), _SHIFT_RANGE),
    (_weekly("1", _shift(starts=1200, ends=1500, breaks=[])), _SHIFT_RANGE),
    (_weekly("2", _shift(breaks=[[500, 600]])), _BREAK_INSIDE),
    (_weekly("3", _shift(breaks=[[720, 810], [780, 840]])), _BREAK_ORDER),
    (_weekly("4", _shift(breaks=[[900, 930], [720, 810]])), _BREAK_ORDER),
    (lambda schedule: schedule["weekly"].pop("6"), _SEVEN_DAYS),
    (lambda schedule: schedule["weekly"].update({"7": None}), _SEVEN_DAYS),
    (lambda schedule: schedule["exceptions"].update({"20260214": None}), _ISO_DATES),
    (lambda schedule: schedule["exceptions"].update({"someday": None}), _ISO_DATES),
    (_weekly("0", _shift(lunch=[720, 810])), "Extra inputs are not permitted"),
    (_weekly("0", {"starts": 540, "ends": 1080}), "Field required"),
], ids=["starts-not-before-ends", "ends-after-midnight", "break-outside-shift", "overlapping-breaks",
        "descending-breaks", "weekly-missing-a-day", "weekly-extra-key", "non-canonical-date", "unparsable-date",
        "unknown-shift-key", "missing-breaks"])
def test_work_schedule_shape_errors_are_rejected(edit, message: str) -> None:
    """班次形状错误留在场景模型的校验器（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ）：班次须同日且 starts < ends（≤ 1440），
    休息时段落在班次内、升序、互不重叠；weekly 七天全写（周一为 "0"，多一天少一天都拒），例外日期是规范的 ISO 日期（基本
    格式与无法解析的都拒）；班次多出的键与缺少的 breaks 都被拒，不设缺省。每个用例对应两个校验器的一处拒绝。"""
    payload = _payload()
    schedule = weekday_schedule(payload)
    edit(schedule)
    payload["agents"][0]["work_schedule"] = schedule
    _assert_invalid(payload, message)


def test_every_agent_declares_work_schedule_and_structure_four_is_rejected(tmp_path: Path) -> None:
    """`work_schedule` 是人物配置的必填可空字段（4.3，裁决 W3）：删掉该键的文档经场景模型与真实入口
    （parse_scenario_document 与 load_scenario）都被拒、原因点名该字段，写 null 照常加载，合法的工作安排照常加载；场景
    结构版本为 6，键仍在而版本写回 4 的文档经同样两个真实入口按版本拒绝。"""
    missing = _payload()
    del missing["agents"][0]["work_schedule"]
    _assert_invalid(missing, r"agents\.0\.work_schedule\n\s+Field required")
    nullable = _payload()
    nullable["agents"][0]["work_schedule"] = None
    nullable["agents"][0]["work_topic_ids"] = []
    assert LifeScenario.model_validate(nullable).agents[0].work_schedule is None
    declared = _payload()
    declared["agents"][0]["work_schedule"] = weekday_schedule(declared)
    assert LifeScenario.model_validate(declared).agents[0].work_schedule.weekly["5"] is None

    keyless = _mutated_scenario(_FORMAL, lambda document: document["agents"][0].pop("work_schedule"),
                                tmp_path / "no-work-schedule")
    old = _mutated_scenario(_FORMAL, lambda document: document.update(schema_version=4), tmp_path / "structure-4")
    for path, field in ((keyless, "work_schedule"), (old, "schema_version")):
        for entry in (parse_scenario_document, load_scenario):
            with pytest.raises(IntegrityError, match="Scenario validation failed") as failure:
                entry(path)
            assert field in str(failure.value.__cause__)

    path = _mutated_scenario(_FORMAL, lambda document: document["agents"][0].update(work_schedule=None, work_topic_ids=[]),
                             tmp_path / "null-work-schedule")
    scenario, _ = load_scenario(path)
    assert scenario.schema_version == 7
    assert scenario.agents[0].work_schedule is None


def _scheduled_payload(edit) -> dict:
    """正式场景文档的副本：人物 1 写上与场景日历一致的工作安排，再交给 edit(schedule) 改写。"""
    payload = _payload()
    schedule = weekday_schedule(payload)
    edit(schedule)
    payload["agents"][0]["work_schedule"] = schedule
    return payload


@pytest.mark.parametrize(("shift", "message"), [
    (_shift(starts=-60, ends=540, breaks=[]), _SHIFT_RANGE),
    (_shift(breaks=[[810, 720]]), _BREAK_INSIDE),
    (_shift(breaks=[[720, 720]]), _BREAK_INSIDE),
], ids=["starts-before-midnight", "inverted-break", "empty-break"])
def test_a_shift_starting_before_midnight_and_an_inverted_or_empty_break_are_rejected(shift: dict, message: str):
    """班次是自当地零点起的分钟、落在同一天，休息时段是班次内的半开区间（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ；接口合同
    「工作安排声明」：0 ≤ starts < ends ≤ 1440）：周一的班次从前一天 23:00 开始（starts 为负）时被场景模型拒绝；休息时段
    两端都在班次内而止点早于或等于起点（13:30 到 12:00、12:00 到 12:00）时同样被拒，原因分别是班次范围与休息时段须落在
    班次内。同一份文档不改周一班次时照常通过。"""
    assert LifeScenario.model_validate(_scheduled_payload(lambda schedule: None)).agents[0].work_schedule is not None
    _assert_invalid(_scheduled_payload(_weekly("0", shift)), message)


def test_back_to_back_breaks_are_admitted_and_a_one_minute_overlap_is_not() -> None:
    """休息时段互不重叠按半开区间判定（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ）：前一段的止点等于后一段的起点不算重叠，
    12:00–13:00 紧接 13:00–13:30 的班次经场景模型照常加载、两段原样保留；后一段早开始一分钟才是重叠，被拒。"""
    adjacent = _scheduled_payload(_weekly("0", _shift(breaks=[[720, 780], [780, 810]])))
    shift = LifeScenario.model_validate(adjacent).agents[0].work_schedule.weekly["0"]
    assert (shift.starts, shift.ends, shift.breaks) == (540, 1080, ((720, 780), (780, 810)))
    _assert_invalid(_scheduled_payload(_weekly("0", _shift(breaks=[[720, 780], [779, 810]]))), _BREAK_ORDER)


@pytest.mark.parametrize("key", ["workplace_location_id", "weekly", "exceptions"])
def test_work_schedule_requires_all_three_keys_without_defaults(key: str) -> None:
    """工作安排的三个键都必填、没有缺省（接口合同「工作安排声明」，裁决 W3）：删掉工作地点、每周声明或日期例外中的任意
    一个键，场景模型都按缺字段拒绝并点名该键；没有日期例外须显式写空对象，写 {} 照常加载。"""
    _assert_invalid(_scheduled_payload(lambda schedule: schedule.pop(key)),
                    rf"agents\.0\.work_schedule\.{key}\n\s+Field required")
    empty = _scheduled_payload(lambda schedule: schedule.update(exceptions={}))
    assert LifeScenario.model_validate(empty).agents[0].work_schedule.exceptions == {}


def test_work_schedule_rejects_an_unknown_key_such_as_a_timezone() -> None:
    """工作安排不带时区字段（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ，复用场景唯一的 timezone），未知键拒绝（接口合同「工作
    安排声明」）：多写 timezone 的安排被场景模型按未知字段拒绝并点名该键，不被悄悄丢弃；不多写时同一份安排照常加载，
    加载出的安排只有三个键。"""
    extra = _scheduled_payload(lambda schedule: schedule.update(timezone="Europe/Berlin"))
    _assert_invalid(extra, r"agents\.0\.work_schedule\.timezone\n\s+Extra inputs are not permitted")
    loaded = LifeScenario.model_validate(_scheduled_payload(lambda schedule: None)).agents[0].work_schedule
    assert set(loaded.model_dump()) == {"workplace_location_id", "weekly", "exceptions"}
