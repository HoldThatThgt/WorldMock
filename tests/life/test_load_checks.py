"""场景加载时就拒绝的世界缺陷与同名地点的分店标签（docs/dev/SPEC-dayrun-fidelity.md 8.1）。

越界的地点类别（不在环境层 PLACE_TYPES）与重复的可见名称都经真实的 load_runtime_scenario 在加载时以 IntegrityError
拒绝并写明原因，不再等到运行中途由环境层或观察投影抛出；同名地点由地图字段 branch_label 消歧，显示名「名称（分店）」
进入工具层、提示与事件，并连同分店标签受可疑显示名检查（只警告）；没有 branch_label 的现有地图照常加载、名称不变。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from custom.agents.life_prompt import life_user_layers, user_message
from tests.life.conftest import context, stage_day_plan
from worldmock.errors import IntegrityError
from worldmock.life.engine import LifeEngine
from worldmock.life.environment_layer import PLACE_TYPES
from worldmock.life.load_checks import visible_object_labels
from worldmock.life.models import ModelIdentity, RunIdentity
from worldmock.life.semantics import load_runtime_scenario
from worldmock.runtime import input_warnings

_ROOT = Path(__file__).resolve().parents[2]
_SCENARIO = "white-collar-12-weeks.json"
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


def _map_variant(corpus: Path, name: str, edit) -> dict:
    """把地图地点目录交给 edit(rows) 改写，另存为 name 变体，并发布引用它的数据集声明；返回场景的地图钉扎。"""
    folder = corpus / "zhangjiang-map"
    payload = json.loads((folder / "locations.json").read_text(encoding="utf-8"))
    edit({row["location_id"]: row for row in payload["locations"]})
    locations = folder / f"locations-{name}.json"
    locations.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    declaration = json.loads((folder / "dataset.json").read_text(encoding="utf-8"))
    declaration["locations_file"] = {"path": f"zhangjiang-map/{locations.name}", "sha256": _sha(locations)}
    dataset = folder / f"dataset-{name}.json"
    dataset.write_text(json.dumps(declaration, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"dataset_id": declaration["dataset_id"], "version": declaration["version"],
            "path": f"zhangjiang-map/{dataset.name}", "sha256": _sha(dataset)}


def _scenario(tmp_path: Path, corpus: Path, pin: dict | None = None, mutate=None) -> Path:
    """正式场景的副本：语料根指向隔离副本，可换地图钉扎、可交给 mutate(document) 改写；目录随场景复制。"""
    document = json.loads((_ROOT / "scenarios" / _SCENARIO).read_text(encoding="utf-8"))
    document["corpus_root"] = str(corpus)
    if pin is not None:
        document["map_dataset"] = pin
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
        event = engine.store.read_events(engine.store.last_event_seq)[-1]
        assert event.event_type == "observation"
        catalog = {row["location_id"]: row["name"] for row in event.data["locations"]}
        assert catalog[_BONE_SOUP] == "菜饭骨头汤（紫薇路店）" and catalog[_SICHUAN] == "菜饭骨头汤（张江路店）"
        bindings = {row["object"] for row in observed["interaction_bindings"]}
        assert bindings <= set(visible_object_labels(engine.scenario, engine.corpus_root)[1])
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
    labels = set(visible_object_labels(engine.scenario, engine.corpus_root)[1])
    observed = engine.observe(context(engine, "labels"))
    objects = [row["object"] for row in observed["interaction_bindings"]]
    assert objects and len(objects) == len(set(objects)) and set(objects) <= labels
    kinds = {row["kind"] for row in observed["interaction_bindings"]}
    assert {"location", "contact", "catalog", "self"} <= kinds
