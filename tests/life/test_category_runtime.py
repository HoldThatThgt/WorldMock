"""冻结品类契约在真实 LifeEngine 路径上的集成回归。"""

from __future__ import annotations

import json
import hashlib
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, context, stage_day_plan
from worldmock.categories import refresh_manifest_identity
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (
    ModelIdentity,
    RunIdentity,
    StartActivityRequest,
)
from worldmock.life.scenario import parse_scenario_document

_SCENARIO = Path("scenarios/white-collar-12-weeks.json")
_INPUT_CATEGORY = "durable-sealed"
_OUTPUT_CATEGORY = "durable-opened"
_INPUT_OBJECT = "sealed-item"
_OUTPUT_OBJECT = "opened-item"
_RECIPE_RULE = "open-sealed-item"
_RESOURCE = "inventory_closed"


def _read_json(path: Path) -> dict:
    """读取测试夹具 JSON。"""
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: dict) -> None:
    """稳定写入测试夹具 JSON。"""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _review() -> dict:
    """形成隔离夹具的委托审查证据。"""
    return {
        "reviewer": "category runtime test agent",
        "reviewer_type": "delegated_agent",
        "evidence": "docs/dev/SPEC-category-contract.md",
        "reason": "verify inventory conversion runtime behavior",
        "authorization": "user requested delegated implementation and review",
    }


def _source(source_id: str, paths: list[str]) -> dict:
    """形成隔离夹具数值来源。"""
    return {
        "source_id": source_id,
        "type": "scenario",
        "producer": "human",
        "source": "isolated category runtime fixture",
        "paths": paths,
    }


def _category(category_id: str) -> dict:
    """形成耐用品子品类。"""
    return {
        "category_id": category_id,
        "name": category_id,
        "parent_id": "durable",
        "kind": "product",
        "slots": {},
        "values": {"absolute": {}, "relative": {}, "proportional": {}},
    }


def _raw_item(object_id: str, category_id: str, consumable: bool) -> dict:
    """形成只含对象差异的隔离库存对象。"""
    return {
        "object_id": object_id,
        "name": object_id,
        "category_id": category_id,
        "values": {
            "absolute": {
                "effect": "package_opened",
                "consumable": consumable,
            },
            "relative": {},
            "proportional": {},
        },
        "provenance": [_source(f"{object_id}-values", ["/values/absolute"])],
        "version": 1,
    }


def _append_jsonl(path: Path, value: dict) -> None:
    """向隔离目录追加一条 JSONL。"""
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def _add_recipe_catalog(catalog_dir: Path, input_state: str = "defined",
                        output_state: str = "no_effect",
                        maximum: float | None = None,
                        coefficient: float = 1) -> None:
    """向正式目录副本加入密封物到拆封物的一对一闭合配方。"""
    contract = _read_json(catalog_dir / "category-contract.json")
    durable = next(item for item in contract["categories"] if item["category_id"] == "durable")
    durable["slots"]["inventory_unit"] = {
        "schema": {"enum": ["item"]}, "required": True, "unit": "item"}
    durable["values"]["absolute"]["inventory_unit"] = "item"
    contract["categories"].extend((_category(_INPUT_CATEGORY), _category(_OUTPUT_CATEGORY)))
    pairing_index = len(contract["pairings"])
    input_pairing = {
        "actor_category_id": "person", "target_category_id": _INPUT_CATEGORY,
        "action": "consume:use", "state": input_state,
    }
    if input_state == "defined":
        input_pairing.update(rule_id=_RECIPE_RULE, coefficient=coefficient)
    elif input_state == "no_effect":
        input_pairing["review"] = _review()
    contract["pairings"].append(input_pairing)
    output = {
        "actor_category_id": "person", "target_category_id": _OUTPUT_CATEGORY,
        "action": "consume:use", "state": output_state,
    }
    if output_state == "no_effect":
        output["review"] = _review()
    contract["pairings"].append(output)
    if input_state == "defined":
        contract["sources"].append(
            _source("recipe-pairing-coefficient", [f"/pairings/{pairing_index}/coefficient"]))
    _write_json(catalog_dir / "category-contract.json", contract)

    rules = _read_json(catalog_dir / "exchange-rules.json")
    rule_index = len(rules["rules"])
    rules["rules"].append({
        "rule_id": _RECIPE_RULE,
        "category_id": _INPUT_CATEGORY,
        "action": "consume:use",
        "operator": "inventory_transform",
        "resource_id": _RESOURCE,
        "unit": "item",
        "conservation": "closed",
        "input_category_id": _INPUT_CATEGORY,
        "output_category_id": _OUTPUT_CATEGORY,
        "input_quantity": 1,
        "output_quantity": 1,
        "inventory_unit": "item",
    })
    rules["sources"].append(_source(
        "recipe-quantities",
        [f"/rules/{rule_index}/input_quantity", f"/rules/{rule_index}/output_quantity"],
    ))
    _write_json(catalog_dir / "exchange-rules.json", rules)

    _append_jsonl(catalog_dir / "resources.jsonl", {
        "resource_id": _RESOURCE,
        "kind": "level",
        "unit": "item",
        "owner": "agent_location",
        "bounds": {"min": 0, "max": maximum},
        "conservation": "closed",
        "alert_rules": [],
        "provenance": [{
            "part": "isolated one-to-one inventory domain",
            "paths": ["bounds.min"],
            "type": "scenario",
            "source": "category runtime test fixture",
        }],
        "version": 1,
    })
    _append_jsonl(catalog_dir / "objects.jsonl", _raw_item(_INPUT_OBJECT, _INPUT_CATEGORY, True))
    _append_jsonl(catalog_dir / "objects.jsonl", _raw_item(_OUTPUT_OBJECT, _OUTPUT_CATEGORY, False))


def _fixture(tmp_path: Path, *, input_state: str = "defined",
             output_state: str = "no_effect",
             held: float = 1, output_held: float = 0,
             maximum: float | None = None,
             coefficient: float = 1) -> Path:
    """建立目录、场景和语料身份完整的隔离配方夹具。"""
    root = tmp_path / "fixture"
    catalog_dir = root / "catalog"
    scenarios_dir = root / "scenarios"
    shutil.copytree("catalog", catalog_dir)
    scenarios_dir.mkdir(parents=True)
    _add_recipe_catalog(catalog_dir, input_state, output_state, maximum, coefficient)
    refresh_manifest_identity(catalog_dir)
    scenario = _read_json(_SCENARIO)
    scenario["scenario_id"] = f"category-runtime-{input_state}-{output_state}-{held}-{output_held}"
    scenario["corpus_root"] = str(Path("corpora").resolve())
    inventory = {_INPUT_OBJECT: held}
    if output_held:
        inventory[_OUTPUT_OBJECT] = output_held
    scenario["agents"][0]["initial_inventory"] = {"home": inventory}
    scenario["recipe_output_bindings"] = {_OUTPUT_CATEGORY: _OUTPUT_OBJECT}
    path = scenarios_dir / "scenario.json"
    _write_json(path, scenario)
    return path


def _formal_fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    """复制正式目录和场景，供启动前门禁变异测试。"""
    root = tmp_path / "formal-fixture"
    catalog_dir = root / "catalog"
    scenario_dir = root / "scenarios"
    shutil.copytree("catalog", catalog_dir)
    scenario_dir.mkdir(parents=True)
    scenario = _read_json(_SCENARIO)
    scenario["scenario_id"] = f"category-admission-{tmp_path.name}"
    scenario["corpus_root"] = str(Path("corpora").resolve())
    path = scenario_dir / "scenario.json"
    _write_json(path, scenario)
    return path, catalog_dir, scenario


def _save_fixture(path: Path, catalog_dir: Path, scenario: dict) -> None:
    """写回场景并刷新被变异目录的完整内容身份。"""
    _write_json(path, scenario)
    refresh_manifest_identity(catalog_dir)


def _scenario_source() -> dict:
    """形成有时态和许可边界的显式测试假设。"""
    return {"kind": "scenario", "url": "scenario://category-integration/test-assumption",
            "retrieved_at": "2026-09-20T00:00:00+08:00", "license": "WorldMock-project",
            "notice": "Controlled category integration assumption", "fact_cutoff": "2026-09-20",
            "applicability": "Explicit simulation assumption; not an observed retail fact"}


def _replace_map_file(root: Path, scenario: dict, kind: str, rows: list[dict]) -> None:
    """复制真实地图语料，修改隔离文件并刷新内容身份。"""
    corpus = root / "corpora"
    shutil.copytree("corpora", corpus)
    relative = f"zhangjiang-map/{kind}.json"
    data_path = corpus / relative
    _write_json(data_path, {"schema_version": 1, kind: rows})
    dataset_path = corpus / scenario["map_dataset"]["path"]
    dataset = _read_json(dataset_path)
    dataset[f"{kind}_file"]["sha256"] = hashlib.sha256(data_path.read_bytes()).hexdigest()
    dataset["version"] += 1
    _write_json(dataset_path, dataset)
    scenario["map_dataset"].update(version=dataset["version"],
        sha256=hashlib.sha256(dataset_path.read_bytes()).hexdigest())
    manifest_path = corpus / "manifest.json"
    manifest = _read_json(manifest_path)
    for entry in manifest["entries"]:
        if entry["path"] in {relative, scenario["map_dataset"]["path"]}:
            target = corpus / entry["path"]
            entry.update(sha256=hashlib.sha256(target.read_bytes()).hexdigest(), size_bytes=target.stat().st_size)
    _write_json(manifest_path, manifest)
    scenario.update(corpus_root=str(corpus), corpus_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest())


def _isolate_hidden_product(catalog_dir: Path, contract: dict, scenario: dict) -> str:
    """让唯一零库存商品独占子品类，其他可见商品不能代替它触发准入失败。"""
    category_id = "food-hidden-reachable"
    category = _category(category_id)
    category["parent_id"] = "food-dairy"
    contract["categories"].append(category)
    product = scenario["products"][0]
    objects_path = catalog_dir / "objects.jsonl"
    objects = [json.loads(line) for line in objects_path.read_text().splitlines()]
    for item in objects:
        if item["object_id"] == product["product_id"]:
            item["category_id"] = category_id
    assert [item["object_id"] for item in objects if item["category_id"] == category_id] == [product["product_id"]]
    objects_path.write_text("".join(json.dumps(item) + "\n" for item in objects))
    supplies = _read_json(Path("corpora/zhangjiang-map/supplies.json"))["supplies"]
    targets = {item["supply_id"] for item in supplies if item["product_id"] == product["product_id"]}
    for item in scenario["supplies"]:
        item["initial_stock"] = 0 if item["supply_id"] in targets else 1
    assert targets and all(item["initial_stock"] == 0 for item in scenario["supplies"]
                           if item["supply_id"] in targets)
    return category_id


def _set_pairing_undefined(contract: dict, category_id: str, action: str) -> None:
    """以显式未定义遮蔽目标品类的既有或祖先规则。"""
    matches = [(index, item) for index, item in enumerate(contract["pairings"])
               if item["actor_category_id"] == "person"
               and item["target_category_id"] == category_id and item["action"] == action]
    if matches:
        index, declaration = matches[0]
        declaration.clear()
        declaration.update({
            "actor_category_id": "person", "target_category_id": category_id,
            "action": action, "state": "undefined",
        })
        numeric_path = f"/pairings/{index}/coefficient"
        for source in contract["sources"]:
            source["paths"] = [path for path in source["paths"] if path != numeric_path]
        contract["sources"] = [source for source in contract["sources"] if source["paths"]]
        return
    contract["pairings"].append({
        "actor_category_id": "person", "target_category_id": category_id,
        "action": action, "state": "undefined",
    })


def _set_pairing_coefficient(contract: dict, category_id: str,
                             action: str, coefficient: float) -> None:
    """修改隔离目录中一个既有精确配对的显式系数。"""
    matches = [item for item in contract["pairings"]
               if item["actor_category_id"] == "person"
               and item["target_category_id"] == category_id and item["action"] == action]
    assert len(matches) == 1 and matches[0]["state"] == "defined"
    matches[0]["coefficient"] = coefficient


def _identity(scenario_path: Path, run_id: str) -> RunIdentity:
    """形成隔离 LifeEngine 的运行身份。"""
    scenario, _ = parse_scenario_document(scenario_path)
    return RunIdentity(
        run_id=run_id,
        source_group_id=run_id,
        agent_ids=tuple(item.agent_id for item in scenario.agents),
        start_at=scenario.start_at.isoformat(),
        end_at=scenario.end_at.isoformat(),
        timezone=scenario.timezone,
        code_sha256="4" * 64,
        model_identity=ModelIdentity(
            provider="openai_compatible",
            base_url="https://openrouter.ai/api/v1",
            requested_model="deepseek-v4.1-flash",
            thinking="enabled",
            tool_choice="auto",
            max_retries=0,
            prompt_sha256="0" * 64,
            tool_schema_sha256="1" * 64,
        ),
    )


def _engine(tmp_path: Path, scenario_path: Path, suffix: str) -> LifeEngine:
    """新建已提交日计划的隔离运行。"""
    engine = LifeEngine.create(
        scenario_path, tmp_path / f"run-{suffix}", _identity(scenario_path, f"recipe-{suffix}"))
    stage_day_plan(engine)
    return engine


def _consume(engine: LifeEngine, round_id: str, action_id: str = "recipe-action"):
    """人物只选择可读持有物与使用，领域推导数量和时长。"""
    current = context(engine, round_id)
    engine.begin_decision_round([current])
    observed = engine.observe(current)
    binding = next(item for item in observed["interaction_bindings"]
                   if item["kind"] == "holding" and item["object_id"] == _INPUT_OBJECT)
    request = StartActivityRequest(targets=[binding["object"]], actions=["使用"])
    engine.submit_action(DirectToolCall(current, action_id, "start_activity", request))
    return request, engine.commit_decision_round(round_id)[0]


def _advance_to_completion(engine: LifeEngine, activity_id: str) -> None:
    """沿真实调度边界推进指定活动直到终态。"""
    while engine.state.activities[activity_id].status.value not in {"completed", "cancelled", "failed"}:
        advance_next(engine)


def _start(engine: LifeEngine, round_id: str, target_kind: str, action: str,
           object_id: str | None = None):
    """从冻结观察选择可读对象和动作并提交当前启动工具。"""
    current = context(engine, round_id)
    engine.begin_decision_round([current])
    observed = engine.observe(current)
    binding = next(item for item in observed["interaction_bindings"]
                   if item["kind"] == target_kind and
                   (object_id is None or item["object_id"] == object_id)
                   and any(row["label"] == action for row in item["actions"]))
    request = StartActivityRequest(targets=[binding["object"]], actions=[action])
    engine.submit_action(DirectToolCall(
        current, f"action-{round_id}", "start_activity", request))
    return observed, engine.commit_decision_round(round_id)[0]


def _stage_open_market(engine: LifeEngine) -> None:
    """把隔离运行推进到商家营业时段并显式保留浏览可见性。"""
    opening = int(datetime.fromisoformat("2026-01-05T10:00:00+08:00").timestamp())
    now = max(engine.state.current_time, opening)
    staged = engine.state.model_copy(deep=True)
    staged.current_time = now
    staged.pending_events = [event for event in staged.pending_events if event.due_at > now]
    agent = staged.agents[1]
    visible = {item.supply_id for item in engine.scenario.supplies}
    staged.agents[1] = agent.model_copy(update={
        "visible_supply_ids": visible, "supply_browsed_at": {key: now for key in visible},
        "supply_browsed_location": {key: agent.location_id for key in visible}, "next_decision_at": now,
        "body_settled_at": now,
    }, deep=True)
    staged.version += 1
    from worldmock.footprints.models import EventDraft

    event = EventDraft(event_type="environment_event", occurred_at=now, visibility="system",
                       source_kind="scenario", data={"kind": "test_open_market"})
    engine.store.commit(staged.model_dump(mode="json"), [event])
    engine.state = staged


def test_recipe_fixture_loads_with_output_category_binding(tmp_path: Path) -> None:
    """场景以输出品类绑定对象并装配真实库存语义。"""
    path = _fixture(tmp_path)
    engine = _engine(tmp_path, path, "load")
    try:
        assert engine.scenario.recipe_output_bindings == {_OUTPUT_CATEGORY: _OUTPUT_OBJECT}
        assert {_INPUT_OBJECT, _OUTPUT_OBJECT} <= set(engine.rules.items)
        assert {_INPUT_OBJECT, _OUTPUT_OBJECT}.isdisjoint(engine.rules.products)
    finally:
        engine.close()


def test_recipe_completes_once_after_readable_use(tmp_path: Path) -> None:
    """人物选择使用后，配方在活动结束时恰好转换一次。"""
    path = _fixture(tmp_path)
    engine = _engine(tmp_path, path, "complete")
    try:
        request, started = _consume(engine, "start")
        assert request.model_dump() == {"targets": ["持有物：sealed-item"], "actions": ["使用"]}
        assert started.status.value == "in_progress"
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 1.0}
        _advance_to_completion(engine, started.activity_id)
        inventory = engine.state.agents[1].inventory["home"]
        assert inventory == {_INPUT_OBJECT: 0, _OUTPUT_OBJECT: 1}
        transforms = [
            event.data["consumption"]["inventory_transform"]
            for event in engine.store.read_events(engine.store.last_event_seq)
            if event.event_type == "activity_progress"
            and event.activity_id == started.activity_id
            and event.data.get("consumption", {}).get("inventory_transform")
        ]
        assert len(transforms) == 1
        assert transforms[0]["rule_id"] == _RECIPE_RULE
        assert transforms[0]["resource_id"] == _RESOURCE
    finally:
        engine.close()


def test_recipe_pairing_coefficient_scales_actual_inventory(tmp_path: Path) -> None:
    """单件使用由引擎推导，配对系数仍同比作用于输入输出。"""
    path = _fixture(tmp_path, held=2, coefficient=2)
    engine = _engine(tmp_path, path, "coefficient")
    try:
        _, started = _consume(engine, "coefficient")
        _advance_to_completion(engine, started.activity_id)
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 0, _OUTPUT_OBJECT: 2}
    finally:
        engine.close()


@pytest.mark.parametrize("state,match", [
    ("undefined", "undefined consume:use"),
    ("defined-zero", "recipe coefficient must be positive"),
])
def test_invalid_recipe_refuses_before_run_artifacts(tmp_path: Path, state: str, match: str) -> None:
    """未定义使用和零配方系数不能创建运行。"""
    options = {"input_state": "undefined"} if state == "undefined" else {"coefficient": 0}
    path = _fixture(tmp_path, **options)
    run_dir = tmp_path / "invalid-recipe"
    with pytest.raises(IntegrityError, match=match):
        LifeEngine.create(path, run_dir, _identity(path, state))
    assert not run_dir.exists()


@pytest.mark.parametrize("action,category", [
    ("purchase", "food-dairy"),
    ("work:settle", "contract-package"),
    ("travel:driving", "travel-service"),
])
def test_reachable_undefined_category_pairing_refuses_before_run(
    tmp_path: Path, action: str, category: str,
) -> None:
    """商品、合同和路线的可达品类都在建运行前校验。"""
    path, directory, scenario = _formal_fixture(tmp_path)
    contract = _read_json(directory / "category-contract.json")
    _set_pairing_undefined(contract, category, action)
    _write_json(directory / "category-contract.json", contract)
    _save_fixture(path, directory, scenario)
    run_dir = tmp_path / "undefined-pairing"
    with pytest.raises(IntegrityError, match=f"undefined {action}"):
        LifeEngine.create(path, run_dir, _identity(path, f"undefined-{action}"))
    assert not run_dir.exists()


def test_purchase_transfer_requires_buyer_account(tmp_path: Path) -> None:
    """品类转移规则必须从真实购买者账户扣款。"""
    path, directory, scenario = _formal_fixture(tmp_path)
    rules = _read_json(directory / "exchange-rules.json")
    purchase = next(row for row in rules["rules"] if row["rule_id"] == "purchase-product")
    purchase["from_role"] = "employer"
    _write_json(directory / "exchange-rules.json", rules)
    _save_fixture(path, directory, scenario)
    run_dir = tmp_path / "wrong-buyer"
    with pytest.raises(IntegrityError, match="Purchase transfer roles must be buyer and merchant"):
        LifeEngine.create(path, run_dir, _identity(path, "wrong-buyer"))
    assert not run_dir.exists()


def test_food_coefficient_changes_body_settlement(tmp_path: Path) -> None:
    """食品品类系数经可读食用动作进入真实身体结算。"""
    outcomes = []
    for label, coefficient in (("base", 1), ("scaled", 2)):
        path, directory, scenario = _formal_fixture(tmp_path / label)
        product_id = scenario["products"][0]["product_id"]
        scenario["agents"][0]["initial_inventory"] = {"home": {product_id: 1}}
        contract = _read_json(directory / "category-contract.json")
        _set_pairing_coefficient(contract, "food", "consume:eat", coefficient)
        _write_json(directory / "category-contract.json", contract)
        _save_fixture(path, directory, scenario)
        engine = _engine(tmp_path, path, f"food-{label}")
        try:
            _, started = _start(engine, f"food-{label}", "holding", "食用", product_id)
            rate = engine.state.activities[started.activity_id].data["food_energy_kcal_per_hour"]
            _advance_to_completion(engine, started.activity_id)
            outcomes.append((rate, engine.state.agents[1].body.energy_kcal))
        finally:
            engine.close()
    assert outcomes[1][0] == outcomes[0][0] * 2
    assert outcomes[1][1] > outcomes[0][1]


def test_no_effect_use_rejects_without_inventory_change(tmp_path: Path) -> None:
    """已审核无作用的耐用品可装配，但使用不改变库存。"""
    path = _fixture(tmp_path, input_state="no_effect")
    engine = _engine(tmp_path, path, "no-effect")
    try:
        before = engine.state.agents[1].inventory.copy()
        _, result = _consume(engine, "no-effect")
        assert result.status.value == "rejected"
        assert result.error_code == "CATEGORY_PAIRING_NO_EFFECT"
        assert engine.state.agents[1].inventory == before
        assert engine.state.activities == {}
    finally:
        engine.close()


def test_recipe_completion_rechecks_inventory(tmp_path: Path) -> None:
    """受理后库存耗尽，活动结算仍原子拒绝转换。"""
    from worldmock.footprints.models import EventDraft

    path = _fixture(tmp_path)
    engine = _engine(tmp_path, path, "completion-shortage")
    try:
        _, started = _consume(engine, "completion-shortage")
        staged = engine.state.model_copy(deep=True)
        staged.agents[1].inventory["home"][_INPUT_OBJECT] = 0
        staged.version += 1
        engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
            event_type="environment_event", occurred_at=staged.current_time,
            visibility="system", data={"kind": "test_inventory_exhausted"},
        )])
        engine.state = staged
        before = engine.state.model_dump(mode="json")
        with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_HELD"):
            _advance_to_completion(engine, started.activity_id)
        assert engine.state.model_dump(mode="json") == before
        assert engine.store.state == before
    finally:
        engine.close()
