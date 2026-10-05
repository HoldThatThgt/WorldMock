"""场景加载时就拒绝的世界缺陷与同名地点的分店标签（docs/dev/SPEC-day-plan-goals.md#dayrun-fidelity-加载校验与当日目标推进-场景加载时就该拒绝的问题 ）。

越界的地点类别（不在环境层 PLACE_TYPES）与重复的可见名称都经真实的 load_runtime_scenario 在加载时以 IntegrityError
拒绝并写明原因，不再等到运行中途由环境层或观察投影抛出；同名地点由地图字段 branch_label 消歧，显示名「名称（分店）」
进入工具层、提示与事件，并连同分店标签受可疑显示名检查（只警告）；没有 branch_label 的现有地图照常加载、名称不变。

人物工作安排的跨引用（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ）同样经真实的 load_runtime_scenario 在加载时拒绝：工作地点
不是所选地点，或场景时间窗内的日期与场景日历不一致，顶层英文明细写明人物、日期与字段；时间窗外的日期、加班机会日与
work_schedule 为 null 的人物不核对；班次形状错误由场景模型的校验器拒绝，明细在异常链里。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import date, datetime
from pathlib import Path

import pytest

from custom.agents.life_prompt import life_user_layers, user_message
from tests.engine_fixture import (_mutated_scenario)  # noqa: F401
from tests.life.conftest import (context, stage_day_plan)  # noqa: F401
from tests.life.work_schedule_fixtures import STANDARD_SHIFT, declare, weekday_schedule
from worldmock.catalog import load_catalog
from worldmock.corpora.storefront_build import build_storefront_supplies
from worldmock.errors import IntegrityError
from worldmock.life.engine import LifeEngine
from worldmock.life.environment_layer import PLACE_TYPES
from worldmock.life.load_checks import visible_object_labels
from worldmock.life.map_models import CategoryAssignment, LocationConfig
from worldmock.life.models import ModelIdentity, RunIdentity
from worldmock.life.world_setup import load_runtime_scenario
from worldmock.life.work_schedule import shift_on
from worldmock.run_setup import input_warnings

_ROOT = Path(__file__).resolve().parents[2]
_SCENARIO = "white-collar-12-weeks.json"
_MAP_SEED = 20260105
#: 仓库地图的三家餐馆（场景全选）：两家改成同名做消歧与拒绝样本，第三家改类别做越界样本。
_BONE_SOUP, _SICHUAN, _CLAYPOT = "poi-3538660453", "poi-3538682404", "poi-3538682408"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    """正式语料的一份隔离副本（本模块共用）：各用例只在其中另写地图变体文件，不改副本里的原文件。"""
    root = tmp_path_factory.mktemp("load-checks") / "corpora"
    shutil.copytree(_ROOT / "corpora", root)
    return root


def _sha(path: Path) -> str:
    """文件字节的 SHA-256。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    """@brief 保存隔离的真实地图声明，不修改正式语料。"""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _variant_manifest(corpus: Path, files: list[Path]) -> None:
    """@brief 地图变体的每份文件和假设来源都钉入该独立语料清单。"""
    changed = {path.relative_to(corpus).as_posix(): path for path in files}
    path = corpus / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for entry in manifest["entries"]:
        if entry["path"] in changed:
            source = changed[entry["path"]]
            entry.update(sha256=_sha(source), size_bytes=source.stat().st_size)
            entry["source"].update(kind="scenario", selector="Isolated load-check map fixture")
    _write_json(path, manifest)


def _map_variant(corpus: Path, name: str, edit) -> tuple[Path, dict]:
    """@brief 完整复制地图后改写地点，按同一种子和逐店声明重建供给与全部引用身份。"""
    isolated = corpus.parent / name / "corpora"
    shutil.copytree(corpus, isolated)
    folder = isolated / "zhangjiang-map"
    payload = json.loads((folder / "locations.json").read_text(encoding="utf-8"))
    edit({row["location_id"]: row for row in payload["locations"]})
    for row in payload["locations"]:
        row["source"].update(kind="scenario", notice="Isolated labels and location kinds for load checks")
    locations = folder / "locations.json"
    _write_json(locations, payload)
    assignment_path = folder / "category-assignment.json"
    assignment = json.loads(assignment_path.read_text(encoding="utf-8"))
    kinds = {row["location_id"]: row["kind"] for row in payload["locations"]}
    for kind in set(kinds.values()) - set(assignment["kinds"]):
        assignment["kinds"][kind] = []
    assignment["locations"] = [row for row in assignment["locations"]
                               if assignment["kinds"][kinds[row["location_id"]]]]
    _write_json(assignment_path, assignment)
    declarations = CategoryAssignment.model_validate(assignment)
    catalog = load_catalog(_ROOT / "catalog")
    assumptions_path = folder / "supply-assumptions.json"
    assumptions = json.loads(assumptions_path.read_text(encoding="utf-8"))
    selling = {row.location_id for row in declarations.locations}
    assumptions["locations"] = [row for row in assumptions["locations"] if row["location_id"] in selling]
    _write_json(assumptions_path, assumptions)
    supplies = build_storefront_supplies(catalog, declarations,
                                        tuple(LocationConfig.model_validate(row) for row in payload["locations"]),
                                        assumptions, _MAP_SEED)
    supply_file = folder / str(_MAP_SEED) / "supplies.json"
    _write_json(supply_file, {"schema_version": 3, "supplies": [row.model_dump(mode="json") for row in supplies]})
    dataset = folder / str(_MAP_SEED) / "dataset.json"
    declaration = json.loads(dataset.read_text(encoding="utf-8"))
    declaration["locations_file"]["sha256"] = _sha(locations)
    declaration["supplies_file"]["sha256"] = _sha(supply_file)
    declaration["category_assignment_sha256"] = _sha(assignment_path)
    _write_json(dataset, declaration)
    _variant_manifest(isolated, [locations, assignment_path, assumptions_path, supply_file, dataset])
    return isolated, {"dataset_id": declaration["dataset_id"], "version": declaration["version"],
                      "path": f"zhangjiang-map/{_MAP_SEED}/dataset.json", "sha256": _sha(dataset)}


def _scenario(tmp_path: Path, corpus: Path, pin: tuple[Path, dict] | None = None, mutate=None) -> Path:
    """正式场景的副本：语料根指向隔离副本，可换地图钉扎、可交给 mutate(document) 改写；目录随场景复制。"""
    document = json.loads((_ROOT / "scenarios" / _SCENARIO).read_text(encoding="utf-8"))
    if pin is not None:
        corpus, reference = pin
        document["map_dataset"] = reference
        document["random_seed"] = _MAP_SEED
        available = json.loads((corpus / reference["path"]).read_text(encoding="utf-8"))
        supply_file = corpus / available["supplies_file"]["path"]
        identities = {row["supply_id"] for row in json.loads(supply_file.read_text(encoding="utf-8"))["supplies"]}
        document["supplies"] = [row for row in document["supplies"] if row["supply_id"] in identities]
    document["corpus_root"] = os.path.relpath(corpus, tmp_path / "scenarios")
    document["corpus_manifest_sha256"] = _sha(corpus / "manifest.json")
    if mutate is not None:
        mutate(document)
    (tmp_path / "scenarios").mkdir()
    shutil.copytree(_ROOT / "catalog", tmp_path / "catalog")
    path = tmp_path / "scenarios" / _SCENARIO
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _identity(document: dict, run_id: str) -> RunIdentity:
    """隔离运行的运行身份（模型身份只作清单字段）。"""
    return RunIdentity(
        run_id=run_id, source_group_id=f"group-{run_id}",
        agent_ids=tuple(item["agent_id"] for item in document["agents"]),
        start_at=document["start_at"], end_at=document["end_at"], timezone=document["timezone"],
        code_sha256="2" * 64,
        model_identity=ModelIdentity(provider="openai_compatible", base_url="https://api.deepseek.com/beta",
                                     requested_model="deepseek-flash", thinking="enabled", tool_choice="auto",
                                     max_retries=0, prompt_sha256="0" * 64, tool_schema_sha256="1" * 64))


def test_location_kind_outside_the_place_types_is_rejected_at_load(tmp_path: Path, corpus: Path) -> None:
    """地图把两家餐馆的类别写成 fuel、cafe，场景也声明了这两个菜单：加载时拒绝，原因逐一写明地点与类别，并列出允许的
    类别（环境层 PLACE_TYPES 的键，唯一来源）。"""
    def kinds(rows: dict) -> None:
        """两家餐馆改成场所类型表之外的类别。"""
        rows[_CLAYPOT]["kind"] = "fuel"
        rows[_SICHUAN]["kind"] = "cafe"

    def menus(document: dict) -> None:
        """场景为越界类别补声明菜单，使菜单键与地点类别一致（菜单校验不先拒绝）。"""
        declared = document["interaction_menus_by_location_kind"]
        declared.update(fuel=dict(declared["restaurant"]), cafe=dict(declared["restaurant"]))

    path = _scenario(tmp_path, corpus, _map_variant(corpus, "outside-kinds", kinds), menus)
    with pytest.raises(IntegrityError) as failure:
        load_runtime_scenario(path)
    assert str(failure.value) == (
        f"Selected locations use kinds that are not place types: {_SICHUAN} kind 'cafe'; {_CLAYPOT} kind 'fuel';"
        f" allowed kinds: {', '.join(sorted(PLACE_TYPES))}")
    assert sorted(PLACE_TYPES) == ["convenience_store", "fitness", "home", "merchant", "office", "park", "restaurant",
                                   "supermarket", "wet_market"]


@pytest.mark.parametrize(("labels", "shared"), [
    ({}, "菜饭骨头汤"),
    ({_BONE_SOUP: "张江路店", _SICHUAN: "张江路店"}, "菜饭骨头汤（张江路店）"),
], ids=["no-branch-labels", "same-branch-label"])
def test_same_named_locations_that_cannot_be_told_apart_are_rejected_at_load(
        tmp_path: Path, corpus: Path, labels: dict, shared: str) -> None:
    """两家餐馆同名且没有分店标签、或分店标签也相同：显示名重复，加载时拒绝并点名两处地点与消歧办法。"""
    def rename(rows: dict) -> None:
        """川菜馆改成与骨头汤同名，按用例加分店标签。"""
        rows[_SICHUAN]["name"] = "菜饭骨头汤"
        for identity, label in labels.items():
            rows[identity]["branch_label"] = label

    pin = _map_variant(corpus, f"same-name-{len(labels)}", rename)
    with pytest.raises(IntegrityError) as failure:
        load_runtime_scenario(_scenario(tmp_path, corpus, pin))
    assert str(failure.value) == (f"Selected locations share display names: {shared!r} ({_BONE_SOUP}, {_SICHUAN});"
                                  " declare distinct branch_label values in the map")


def test_branch_label_equal_to_another_name_suffix_is_still_a_duplicate(tmp_path: Path, corpus: Path) -> None:
    """一处原名已带括号「菜饭骨头汤（张江路店）」，另一处名称「菜饭骨头汤」加分店标签「张江路店」：显示名相同，仍拒绝。"""
    def collide(rows: dict) -> None:
        """一处原名带括号，另一处用分店标签拼出同一个显示名。"""
        rows[_BONE_SOUP]["name"] = "菜饭骨头汤（张江路店）"
        rows[_SICHUAN].update(name="菜饭骨头汤", branch_label="张江路店")

    pin = _map_variant(corpus, "suffix-collision", collide)
    with pytest.raises(IntegrityError, match="share display names: '菜饭骨头汤（张江路店）'"):
        load_runtime_scenario(_scenario(tmp_path, corpus, pin))


def _duplicate_task(document: dict) -> tuple[str, str]:
    """同一人物名下追加一项与第一项同名的工作；返回重复名称与两处来源。"""
    first = document["tasks"][0]
    document["tasks"].append({**first, "task_id": "task-duplicate-title"})
    notice = next(row for row in document["messages"] if row["related_task_id"] == first["task_id"])
    document["messages"].append({**notice, "message_id": "duplicate-title-notice",
                                 "thread_id": "duplicate-title-notice", "related_task_id": "task-duplicate-title",
                                 "subject": f"Duplicate work announcement — {first['title']}"})
    return f"工作：{first['title']}", f"task:{first['task_id']}, task:task-duplicate-title"


def _duplicate_contact(document: dict) -> tuple[str, str]:
    """人物通讯录追加一位与第一位同称谓的联系人；返回重复名称与两处来源。"""
    first = document["agents"][0]["contacts"][0]
    document["agents"][0]["contacts"].append({**first, "contact_id": "contact-duplicate-salutation"})
    return f"联系人：{first['salutation']}", f"contact:{first['contact_id']}, contact:contact-duplicate-salutation"


@pytest.mark.parametrize("duplicate", [_duplicate_task, _duplicate_contact], ids=["work-title", "contact-salutation"])
def test_duplicate_object_labels_are_rejected_at_load(tmp_path: Path, corpus: Path, duplicate) -> None:
    """同一人物名下两项工作同名、或两位联系人同称谓：人物会同时看到两个相同名称（运行中观察投影会因此崩溃），
    加载时拒绝并写明名称与两处来源。"""
    found = []
    path = _scenario(tmp_path, corpus, mutate=lambda document: found.append(duplicate(document)))
    label, sources = found[0]
    with pytest.raises(IntegrityError) as failure:
        load_runtime_scenario(path)
    assert str(failure.value) == f"Agent 1 would see non-unique object labels: {label!r} from {sources}"


def test_branch_labels_make_same_named_locations_load_and_distinguishable_in_the_prompt(
        tmp_path: Path, corpus: Path) -> None:
    """两家餐馆同名、各带分店标签：场景照常加载，运行时地点名是显示名「名称（分店）」；在家观察时两家都是可达地点，
    工具层目标、发给模型的 user 消息与观察事件的地点目录都能区分两家；地图本身仍保留原名与分店标签。"""
    def branches(rows: dict) -> None:
        """两家同名餐馆各带不同的分店标签。"""
        rows[_BONE_SOUP]["branch_label"] = "紫薇路店"
        rows[_SICHUAN].update(name="菜饭骨头汤", branch_label="张江路店")

    path = _scenario(tmp_path, corpus, _map_variant(corpus, "branches", branches))
    semantics, _ = load_runtime_scenario(path)
    names = {item.location_id: item.name for item in semantics.scenario.locations}
    assert names[_BONE_SOUP] == "菜饭骨头汤（紫薇路店）" and names[_SICHUAN] == "菜饭骨头汤（张江路店）"
    assert all(item.branch_label is None for item in semantics.scenario.locations)
    raw = {item.location_id: item for item in semantics.scenario.map_dataset.locations}
    assert (raw[_SICHUAN].name, raw[_SICHUAN].branch_label) == ("菜饭骨头汤", "张江路店")
    document = json.loads(path.read_text(encoding="utf-8"))
    engine = LifeEngine.create(path, tmp_path / "run", _identity(document, "branch-labels"))
    try:
        stage_day_plan(engine)
        observed = engine.observe(context(engine, "branches"))
        targets = {target["name"] for target in observed["tool_layer"]["targets"]}
        expected = {"地点：菜饭骨头汤（紫薇路店）", "地点：菜饭骨头汤（张江路店）"}
        assert expected <= targets
        prompt = user_message(life_user_layers(observed, {"layer": "人设层"}))["content"]
        assert all(label in prompt for label in expected)
        event = engine.context.store.read_events(engine.context.store.last_event_seq)[-1]
        assert event.event_type == "observation"
        catalog = {row["location_id"]: row["name"] for row in event.data["locations"]}
        assert catalog[_BONE_SOUP] == "菜饭骨头汤（紫薇路店）" and catalog[_SICHUAN] == "菜饭骨头汤（张江路店）"
        bindings = {row["object"] for row in observed["interaction_bindings"]}
        assert bindings <= set(visible_object_labels(engine.context.scenario, engine.context.corpus_root)[1])
    finally:
        engine.close()


def test_branch_labels_are_checked_as_part_of_the_display_name(tmp_path: Path, corpus: Path) -> None:
    """可疑显示名查的是运行时地点名「名称（branch_label）」（规格 8.1，裁决 D19）：新建运行计算输入警告时，普通分店标签
    「张江店」「张江高科店」「B1层」不算可疑；标识形态的分店标签「sim-01」与坐标形态的「31.2, 121.5」随显示名各报一条
    场景级 LOCATION_NAME_SUSPICIOUS，只警告，场景照常加载。"""
    plain = {_BONE_SOUP: "张江店", "poi-3538682402": "张江高科店", "poi-3538763552": "B1层"}

    def branches(rows: dict) -> None:
        """五处地点各带一个分店标签：三个普通店名，一个标识形态，一个坐标形态。"""
        for identity, label in plain.items():
            rows[identity]["branch_label"] = label
        rows[_SICHUAN]["branch_label"] = "sim-01"
        rows[_CLAYPOT]["branch_label"] = "31.2, 121.5"

    path = _scenario(tmp_path, corpus, _map_variant(corpus, "suspicious-branches", branches))
    semantics, _ = load_runtime_scenario(path)
    raw = {item.location_id: item.name for item in semantics.scenario.map_dataset.locations}
    names = {item.location_id: item.name for item in semantics.scenario.locations}
    assert {identity: names[identity] for identity in plain} == {
        identity: f"{raw[identity]}（{label}）" for identity, label in plain.items()}

    warnings = [item for item in input_warnings(path, semantics.scenario, {}, ())
                if item["code"] == "LOCATION_NAME_SUSPICIOUS"]

    assert [(item["agent_id"], item["details"]) for item in warnings] == [
        (None, {"location_id": _SICHUAN, "name": f"{raw[_SICHUAN]}（sim-01）", "patterns": ["identifier"]}),
        (None, {"location_id": _CLAYPOT, "name": f"{raw[_CLAYPOT]}（31.2, 121.5）", "patterns": ["coordinates"]})]
    assert names[_SICHUAN] in warnings[0]["message"] and names[_CLAYPOT] in warnings[1]["message"]


def test_existing_map_without_branch_labels_loads_unchanged() -> None:
    """仓库地图没有任何 branch_label：三个正式场景照常加载，运行时地点与地图地点逐字段相同，名称不变。"""
    for name in ("white-collar-12-weeks.json", "multi-person.json", "multi-person-half-day.json"):
        semantics, _ = load_runtime_scenario(_ROOT / "scenarios" / name)
        raw = {item.location_id: item for item in semantics.scenario.map_dataset.locations}
        assert all(item.branch_label is None for item in raw.values())
        assert semantics.scenario.locations and all(
            item == raw[item.location_id] and item.name == raw[item.location_id].display_name
            for item in semantics.scenario.locations)


def test_projection_labels_are_the_load_time_labels(engine_factory) -> None:
    """观察投影实际给出的对象名称都在加载时核对过的名称集合里（两处同一写法），覆盖地点、联系人、商品目录与自己。"""
    engine = engine_factory()
    stage_day_plan(engine)
    labels = set(visible_object_labels(engine.context.scenario, engine.context.corpus_root)[1])
    observed = engine.observe(context(engine, "labels"))
    objects = [row["object"] for row in observed["interaction_bindings"]]
    assert objects and len(objects) == len(set(objects)) and set(objects) <= labels
    kinds = {row["kind"] for row in observed["interaction_bindings"]}
    assert {"location", "contact", "catalog", "self"} <= kinds


_FORMAL = _ROOT / "scenarios" / _SCENARIO
_MULTI = _ROOT / "scenarios" / "multi-person.json"
_NOT_ADMISSIBLE = "Work schedules are not admissible: "
_MAKEUP = "calendar.makeup_work_dates requires a shift but work_schedule declares none"
_OFF_DUTY = "must be off duty but work_schedule declares a shift"


def _scheduled(root: Path, source: Path, *changes) -> Path:
    """仓库场景的副本（语料取正式语料，目录随场景复制到 root），依次交给 changes 里的各个 mutate(document) 改写。"""
    def mutate(document: dict) -> None:
        """按给出的顺序执行每个改写。"""
        for change in changes:
            change(document)

    return _mutated_scenario(source, mutate, root)


def _rejection(path: Path) -> str:
    """真实加载入口拒绝该场景时的顶层英文明细。"""
    with pytest.raises(IntegrityError) as failure:
        load_runtime_scenario(path)
    return str(failure.value)


def _off_duty(day: str, field: str) -> str:
    """必须不当班的日历日期却声明了班次时的一条明细（人物 1）。"""
    return f"agent 1 date {day} calendar.{field} {_OFF_DUTY}"


def _clear_exceptions(document: dict) -> None:
    """去掉人物 1 工作安排的全部日期例外，只留每周声明（周一至周五当班、周末不当班）。"""
    document["agents"][0]["work_schedule"]["exceptions"] = {}


def test_work_schedule_workplace_must_be_a_selected_location(tmp_path: Path) -> None:
    """工作地点必须是场景所选地点（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ）：写成所选地点之外的身份时加载即拒绝，顶层
    明细写明人物与字段；写成所选的办公室时照常加载。"""
    path = _scheduled(tmp_path / "elsewhere", _FORMAL, declare(workplace="nowhere-office"))
    assert _rejection(path) == (
        _NOT_ADMISSIBLE + "agent 1 work_schedule.workplace_location_id 'nowhere-office' is not a selected location")
    semantics, _ = load_runtime_scenario(_scheduled(tmp_path / "office", _FORMAL, declare()))
    assert semantics.scenario.agents[0].work_schedule.workplace_location_id == "office"


@pytest.mark.parametrize(("changes", "problems"), [
    ((declare(exceptions={"2026-02-14": None}),), [f"agent 1 date 2026-02-14 {_MAKEUP}"]),
    ((declare(exceptions={"2026-02-16": STANDARD_SHIFT}),), [_off_duty("2026-02-16", "spring_festival_dates")]),
    ((declare(exceptions={"2026-03-12": STANDARD_SHIFT}),), [_off_duty("2026-03-12", "sick_leave_date")]),
    ((declare(exceptions={"2026-03-18": STANDARD_SHIFT}),), [_off_duty("2026-03-18", "personal_leave_date")]),
    ((declare(), _clear_exceptions), [
        f"agent 1 date 2026-02-14 {_MAKEUP}",
        *(_off_duty(f"2026-02-{day}", "spring_festival_dates") for day in ("16", "17", "18", "19", "20", "23")),
        f"agent 1 date 2026-02-28 {_MAKEUP}",
        _off_duty("2026-03-12", "sick_leave_date"), _off_duty("2026-03-18", "personal_leave_date")]),
], ids=["makeup-day-without-shift", "spring-festival-weekday-with-shift", "sick-leave-with-shift",
        "personal-leave-with-shift", "weekly-table-only"])
def test_work_schedule_must_agree_with_the_calendar_inside_the_window(
        tmp_path: Path, changes: tuple, problems: list[str]) -> None:
    """十二周场景的时间窗含全部日历日期：补班日必须有班次，春节、病假、事假日必须不当班（4.3）；不一致时加载即拒绝，
    每条明细写明人物、日期与日历字段，按日期升序一次列全。只写每周声明时，两个补班的周六、落在工作日的六个春节日与
    病假、事假日都被点名，落在周末的春节日不当班、照常通过。"""
    assert _rejection(_scheduled(tmp_path, _FORMAL, *changes)) == _NOT_ADMISSIBLE + "; ".join(problems)


def test_overtime_dates_and_dates_outside_the_window_are_not_checked(tmp_path: Path) -> None:
    """只核对落在场景时间窗内的日历日期（4.3）：加班机会日不要求班次（两个周六不当班照常加载），时间窗外的例外日期
    （2025-12-31、2027-01-01）不拒绝；多人场景的时间窗只有 2026-01-05 一天，日历里的补班日与春节日都在窗外，只写每周
    声明、即使与那些日期相反也照常加载。"""
    formal = _scheduled(tmp_path / "formal", _FORMAL,
                        declare(exceptions={"2025-12-31": STANDARD_SHIFT, "2027-01-01": None}))
    scenario = load_runtime_scenario(formal)[0].scenario
    schedule = scenario.agents[0].work_schedule
    assert [shift_on(schedule, date.fromisoformat(day)) for day in scenario.calendar.overtime_opportunity_dates] == [
        None, None]
    assert {"2025-12-31", "2027-01-01"} <= set(schedule.exceptions)

    assert weekday_schedule(json.loads(_MULTI.read_text(encoding="utf-8")))["exceptions"] == {}
    weekly = load_runtime_scenario(_scheduled(tmp_path / "multi", _MULTI, declare(0), declare(1)))[0].scenario
    assert all(agent.work_schedule.exceptions == {} for agent in weekly.agents)
    assert "2026-02-16" in weekly.calendar.spring_festival_dates
    assert shift_on(weekly.agents[0].work_schedule, date(2026, 2, 16)) is not None
    contrary = declare(exceptions={"2026-02-14": None, "2026-02-16": STANDARD_SHIFT, "2026-03-12": STANDARD_SHIFT})
    outside = load_runtime_scenario(_scheduled(tmp_path / "outside", _MULTI, contrary))[0].scenario
    assert shift_on(outside.agents[0].work_schedule, date(2026, 2, 14)) is None


def test_a_null_work_schedule_is_not_cross_checked_and_others_still_are(tmp_path: Path) -> None:
    """work_schedule 为 null 的人物不核对，声明了的人物照常核对（4.3）：多人场景人物 2 为 null、人物 1 的工作地点不是所选
    地点时只点名人物 1；两人都写错时按人物身份升序各一条；人物 1 写对时照常加载，人物 2 仍是 null。"""
    message = _rejection(_scheduled(tmp_path / "one", _MULTI, declare(0, workplace="nowhere-office")))
    assert message == (
        _NOT_ADMISSIBLE + "agent 1 work_schedule.workplace_location_id 'nowhere-office' is not a selected location")
    both = _rejection(_scheduled(tmp_path / "both", _MULTI, declare(1, workplace="nowhere-b"),
                                 declare(0, workplace="nowhere-a")))
    assert both == (_NOT_ADMISSIBLE + "agent 1 work_schedule.workplace_location_id 'nowhere-a' is not a selected "
                    "location; agent 2 work_schedule.workplace_location_id 'nowhere-b' is not a selected location")
    loaded = load_runtime_scenario(_scheduled(tmp_path / "right", _MULTI, declare(0)))[0].scenario
    assert [(agent.agent_id, agent.work_schedule is None) for agent in loaded.agents] == [(1, False), (2, True)]


@pytest.mark.parametrize(("shift", "message"), [
    ({"starts": 600, "ends": 540, "breaks": []}, "work shift must satisfy 0 <= starts < ends <= 1440"),
    ({"starts": 540, "ends": 1080, "breaks": [[1020, 1110]]}, "work shift breaks must lie within the shift"),
], ids=["starts-after-ends", "break-outside-shift"])
def test_a_malformed_shift_or_break_is_rejected_by_the_real_loader(tmp_path: Path, shift: dict, message: str) -> None:
    """时段与休息时段非法各一例经真实加载入口被拒（4.3）：班次形状错误由场景模型的校验器拒绝，顶层是「Scenario validation
    failed」，校验器的英文原因在异常链里。"""
    with pytest.raises(IntegrityError, match="Scenario validation failed") as failure:
        load_runtime_scenario(_scheduled(tmp_path, _FORMAL, declare(shift=shift)))
    assert message in str(failure.value.__cause__)


def _window(start: str | None = None, end: str | None = None):
    """返回 mutate(document)：把场景时间窗改成从 start 到 end（没给的一端不变），并丢掉落在新时间窗之外的任务及其通知、
    结束时刻之后才发出的邮件、不在窗内到期的账单与工资，使场景照常通过其余加载检查；日历与人物不动。"""
    def mutate(document: dict) -> None:
        """改写两端时刻，再按新的时间窗筛掉窗外的条目。"""
        document["start_at"] = start or document["start_at"]
        document["end_at"] = end or document["end_at"]
        first, last = (datetime.fromisoformat(document[key]) for key in ("start_at", "end_at"))
        document["tasks"] = [item for item in document["tasks"]
                             if first < datetime.fromisoformat(item["due_at"]) <= last]
        kept = {item["task_id"] for item in document["tasks"]}
        document["messages"] = [item for item in document["messages"]
                                if item.get("related_task_id") in kept
                                or (item.get("related_task_id") is None
                                    and datetime.fromisoformat(item["sent_at"]) < last)]
        for key in ("bills", "payrolls"):
            document[key] = [item for item in document.get(key, ())
                             if first < datetime.fromisoformat(item["due_at"]) < last]

    return mutate


#: 仓库地图里向全部地点配送的三家门店（集市、超市、便利店）；其余门店都不配送。
_DELIVERING_STORES = ("market", "poi-3538763552", "poi-3538682402")


def _deselect_delivering_stores(document: dict) -> None:
    """让场景只选地图的一个真子集：去掉三家配送门店，连同它们的供给与只属于它们的三类地点菜单。其余门店都不配送，
    留下的供给不再引用被去掉的地点，场景仍通过世界准入。"""
    corpus = _ROOT / "corpora"
    dataset = json.loads((corpus / document["map_dataset"]["path"]).read_text(encoding="utf-8"))
    rows = json.loads((corpus / dataset["supplies_file"]["path"]).read_text(encoding="utf-8"))["supplies"]
    gone = {row["supply_id"] for row in rows if row["location_id"] in _DELIVERING_STORES}
    assert gone and all(not row["delivery_destination_ids"] for row in rows if row["supply_id"] not in gone)
    document["locations"] = [row for row in document["locations"] if row["location_id"] not in _DELIVERING_STORES]
    document["supplies"] = [row for row in document["supplies"] if row["supply_id"] not in gone]
    for kind in ("merchant", "supermarket", "convenience_store"):
        del document["interaction_menus_by_location_kind"][kind]


def test_a_workplace_in_the_map_but_not_selected_by_the_scenario_is_rejected(tmp_path: Path) -> None:
    """工作地点必须是场景所选的地点，只在地图里有还不够（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ）：场景只选地图的一个真
    子集（不选集市、超市与便利店）时，工作地点写办公室照常加载，集市仍在地图里而不在所选地点里；工作地点写集市时
    加载即拒绝，顶层明细点名人物、字段与该地点。"""
    office = _scheduled(tmp_path / "subset", _FORMAL, _deselect_delivering_stores, declare())
    loaded = load_runtime_scenario(office)[0].scenario
    selected = {item.location_id for item in loaded.locations}
    assert "market" in {item.location_id for item in loaded.map_dataset.locations}
    assert "market" not in selected and "office" in selected
    assert loaded.agents[0].work_schedule.workplace_location_id == "office"
    market = _scheduled(tmp_path / "unselected", _FORMAL, _deselect_delivering_stores, declare(workplace="market"))
    assert _rejection(market) == (
        _NOT_ADMISSIBLE + "agent 1 work_schedule.workplace_location_id 'market' is not a selected location")


def test_the_window_starts_with_the_local_date_of_the_start_instant(tmp_path: Path) -> None:
    """时间窗内的日期从开始时刻的当地日期算起、含当日（接口合同「工作安排声明」：不早于开始时刻的当地日期）：场景改在
    补班日 2026-02-14 的 07:30 开始，这一天当地零点早于开始时刻，仍在窗内、必须有班次。按日历写了班次例外时照常加载；
    把这一天写成不当班时加载即拒绝，明细点名人物、日期与日历字段。"""
    late = _window(start="2026-02-14T07:30:00+08:00")
    scenario = load_runtime_scenario(_scheduled(tmp_path / "agreeing", _FORMAL, late, declare()))[0].scenario
    assert shift_on(scenario.agents[0].work_schedule, date(2026, 2, 14)) is not None
    resting = _scheduled(tmp_path / "resting", _FORMAL, late, declare(exceptions={"2026-02-14": None}))
    assert _rejection(resting) == _NOT_ADMISSIBLE + f"agent 1 date 2026-02-14 {_MAKEUP}"


def test_calendar_dates_before_the_window_are_not_checked(tmp_path: Path) -> None:
    """早于时间窗的日历日期不核对（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ）：场景改在 2026-03-01 开始，两个补班的周六与
    整个春节都在开始之前。工作安排只为窗内的病假、事假日写例外，对窗前的日期与日历相反（补班日 02-14 不当班、春节
    02-16 当班），照常加载。"""
    path = _scheduled(tmp_path, _FORMAL, _window(start="2026-03-01T00:00:00+08:00"), declare())
    scenario = load_runtime_scenario(path)[0].scenario
    schedule = scenario.agents[0].work_schedule
    assert sorted(schedule.exceptions) == ["2026-03-12", "2026-03-18"]
    assert "2026-02-14" in scenario.calendar.makeup_work_dates and shift_on(schedule, date(2026, 2, 14)) is None
    assert "2026-02-16" in scenario.calendar.spring_festival_dates
    assert shift_on(schedule, date(2026, 2, 16)) is not None


def test_a_date_whose_local_midnight_is_the_end_instant_is_outside_the_window(tmp_path: Path) -> None:
    """日期在时间窗内须当地零点早于结束时刻（接口合同「工作安排声明」）：场景在补班日 2026-02-14 的零点整结束时，这一天
    不在窗内，只写每周声明（周六不当班）的工作安排照常加载；结束时刻晚一秒，这一天就在窗内，同样的工作安排因补班日没有
    班次被拒。"""
    until_midnight = _scheduled(tmp_path / "ends-at-midnight", _FORMAL, _window(end="2026-02-14T00:00:00+08:00"),
                                declare())
    schedule = load_runtime_scenario(until_midnight)[0].scenario.agents[0].work_schedule
    assert schedule.exceptions == {} and shift_on(schedule, date(2026, 2, 14)) is None
    one_second_in = _scheduled(tmp_path / "one-second-in", _FORMAL, _window(end="2026-02-14T00:00:01+08:00"),
                               declare(), _clear_exceptions)
    assert _rejection(one_second_in) == _NOT_ADMISSIBLE + f"agent 1 date 2026-02-14 {_MAKEUP}"


def test_the_calendar_binds_every_person_who_declares_a_schedule(tmp_path: Path) -> None:
    """场景日历是场景级的，对所有声明了班次的人物同时生效（docs/dev/SPEC-life-realism.md#errands-workday-作息补全与工作安排-工作安排声明 ）：多人场景的时间窗延到
    2026-02-15 零点，补班日 02-14 落在窗内。两人都按日历声明时照常加载；人物 1 照旧、人物 2 把补班日写成不当班时加载即
    拒绝，明细只点名人物 2。"""
    longer = _window(end="2026-02-15T00:00:00+08:00")
    agreeing = load_runtime_scenario(_scheduled(tmp_path / "agreeing", _MULTI, longer, declare(0), declare(1)))
    assert [sorted(agent.work_schedule.exceptions) for agent in agreeing[0].scenario.agents] == [["2026-02-14"]] * 2
    second_rests = _scheduled(tmp_path / "second-rests", _MULTI, longer, declare(0),
                              declare(1, exceptions={"2026-02-14": None}))
    assert _rejection(second_rests) == _NOT_ADMISSIBLE + f"agent 2 date 2026-02-14 {_MAKEUP}"


def test_problems_are_listed_by_ascending_agent_id_whatever_the_document_order(tmp_path: Path) -> None:
    """明细按人物编号升序列出，与文档里人物的先后无关（接口合同「工作安排声明」）：多人场景把人物列表倒过来写（人物 2
    在前），两人的工作地点都不是所选地点时，顶层明细仍先写人物 1、再写人物 2。"""
    def reverse(document: dict) -> None:
        """把人物列表倒过来写。"""
        document["agents"].reverse()

    path = _scheduled(tmp_path, _MULTI, reverse, declare(0, workplace="nowhere-b"), declare(1, workplace="nowhere-a"))
    written = json.loads(path.read_text(encoding="utf-8"))["agents"]
    assert [(agent["agent_id"], agent["work_schedule"]["workplace_location_id"]) for agent in written] == [
        (2, "nowhere-b"), (1, "nowhere-a")]
    assert _rejection(path) == (
        _NOT_ADMISSIBLE + "agent 1 work_schedule.workplace_location_id 'nowhere-a' is not a selected location; "
        "agent 2 work_schedule.workplace_location_id 'nowhere-b' is not a selected location")


def test_workplace_problems_are_listed_before_calendar_problems(tmp_path: Path) -> None:
    """同一份工作安排既写错工作地点又与日历不一致时，顶层明细先列工作地点、再列日历（接口合同「工作安排声明」），一次
    列全：工作地点不是所选地点，补班日 2026-02-14 写成不当班。"""
    path = _scheduled(tmp_path, _FORMAL, declare(workplace="nowhere-office", exceptions={"2026-02-14": None}))
    assert _rejection(path) == (
        _NOT_ADMISSIBLE + "agent 1 work_schedule.workplace_location_id 'nowhere-office' is not a selected location; "
        f"agent 1 date 2026-02-14 {_MAKEUP}")
