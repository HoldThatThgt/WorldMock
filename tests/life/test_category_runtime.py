"""冻结品类契约在真实 LifeEngine 路径上的集成回归。"""

from __future__ import annotations

import copy
import json
import hashlib
import os
import shutil
import sqlite3
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

import pytest

from agentsociety2.society import DirectToolCall

from tests.life.conftest import advance_next, advance_until_ready, context, stage_day_plan, travel_quote, wake_by_mail
from worldmock.categories import refresh_manifest_identity
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.engine import LifeEngine
from worldmock.life.models import (
    ConsumeItem,
    ConsumeRequest,
    FulfillmentKind,
    ModelIdentity,
    PurchaseItem,
    PurchaseOrderRequest,
    RunIdentity,
    SetActivityStateRequest,
    StartBasicActivityRequest,
    StartTravelRequest,
)
from worldmock.life.scenario import parse_scenario_document
from worldmock.life.semantics import load_runtime_scenario

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
    """复制真实地图语料并钉扎被修改文件，替换前断开硬链接保护正式数据。"""
    corpus = root / "corpora"
    shutil.copytree("corpora", corpus, copy_function=os.link)
    relative = f"zhangjiang-map/{kind}.json"
    data_path = corpus / relative
    data_path.unlink()
    _write_json(data_path, {"schema_version": 1, kind: rows})
    dataset_path = corpus / scenario["map_dataset"]["path"]
    dataset = _read_json(dataset_path)
    dataset[f"{kind}_file"]["sha256"] = hashlib.sha256(data_path.read_bytes()).hexdigest()
    dataset["version"] += 1
    dataset_path.unlink()
    _write_json(dataset_path, dataset)
    scenario["map_dataset"].update(version=dataset["version"],
        sha256=hashlib.sha256(dataset_path.read_bytes()).hexdigest())
    manifest_path = corpus / "manifest.json"
    manifest = _read_json(manifest_path)
    for entry in manifest["entries"]:
        if entry["path"] in {relative, scenario["map_dataset"]["path"]}:
            target = corpus / entry["path"]
            entry.update(sha256=hashlib.sha256(target.read_bytes()).hexdigest(), size_bytes=target.stat().st_size)
    manifest_path.unlink()
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
            provider="deepseek",
            base_url="https://api.deepseek.com/beta",
            requested_model="deepseek-flash",
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


def _consume(engine: LifeEngine, round_id: str, action_id: str = "recipe-action",
             quantity: float = 1):
    """通过公开工具协议提交一次配方使用。"""
    request = ConsumeRequest(
        items=(ConsumeItem(product_id=_INPUT_OBJECT, quantity=quantity),),
        use_mode="use",
        duration_seconds=300,
    )
    current = context(engine, round_id)
    engine.begin_decision_round([current])
    engine.observe(current)
    engine.submit_action(DirectToolCall(current, action_id, "consume", request))
    return request, engine.commit_decision_round(round_id)[0]


def _advance_to_completion(engine: LifeEngine, activity_id: str) -> None:
    """沿真实调度边界推进指定活动直到终态。"""
    while engine.state.activities[activity_id].status.value not in {"completed", "cancelled", "failed"}:
        advance_next(engine)


def _action(engine: LifeEngine, round_id: str, tool_name: str, request):
    """通过公开工具协议观察、提交并结算一个动作。"""
    current = context(engine, round_id)
    engine.begin_decision_round([current])
    engine.observe(current)
    engine.submit_action(DirectToolCall(
        current, f"action-{round_id}", tool_name, request))
    return engine.commit_decision_round(round_id)[0]


def _stage_open_market(engine: LifeEngine) -> None:
    """把隔离运行推进到商家营业时段并显式保留浏览可见性。"""
    now = int(datetime.fromisoformat("2026-01-05T10:00:00+08:00").timestamp())
    staged = engine.state.model_copy(deep=True)
    staged.current_time = now
    staged.pending_events = [event for event in staged.pending_events if event.due_at > now]
    agent = staged.agents[1]
    visible = {item.supply_id for item in engine.scenario.supplies}
    staged.agents[1] = agent.model_copy(update={
        "visible_supply_ids": visible, "supply_browsed_at": {key: now for key in visible},
        "supply_browsed_location": {key: agent.location_id for key in visible}, "next_decision_at": now,
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


def test_recipe_completes_once_and_replayed_action_is_idempotent(tmp_path: Path) -> None:
    """真实使用活动结束时一次转换，重复 action_id 不再次转换或统计。"""
    path = _fixture(tmp_path)
    engine = _engine(tmp_path, path, "complete")
    try:
        request, started = _consume(engine, "start")
        assert started.status.value == "in_progress"
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 1.0}
        _advance_to_completion(engine, started.activity_id)
        inventory = engine.state.agents[1].inventory["home"]
        assert inventory[_INPUT_OBJECT] == 0
        assert inventory[_OUTPUT_OBJECT] == 1
        progress = [event for event in engine.store.read_events(engine.store.last_event_seq)
                    if event.event_type == "activity_progress"][-1]
        transform = progress.data["consumption"]["inventory_transform"]
        assert transform["rule_id"] == _RECIPE_RULE
        assert transform["resource_id"] == _RESOURCE

        current = context(engine, "replay")
        engine.begin_decision_round([current])
        engine.observe(current)
        engine.submit_action(DirectToolCall(current, "recipe-action", "consume", request))
        replayed = engine.commit_decision_round("replay")[0]
        assert replayed.status.value == "completed"
        assert engine.state.agents[1].inventory["home"] == inventory
        matches = [event.data["category_matches"]
                   for event in engine.store.read_events(engine.store.last_event_seq)
                   if event.event_type == "action_result"
                   and event.action_id == "recipe-action"
                   and "category_matches" in event.data]
        assert matches == [[{
            "category_id": _INPUT_CATEGORY,
            "rule_id": _RECIPE_RULE,
            "action": "consume:use",
        }]]
    finally:
        engine.close()


def test_recipe_pairing_coefficient_scales_both_inventory_sides(tmp_path: Path) -> None:
    """配对系数通过真实使用活动同比缩放闭合配方双侧数量。"""
    path = _fixture(tmp_path, held=2, coefficient=2)
    engine = _engine(tmp_path, path, "coefficient")
    try:
        _, started = _consume(engine, "coefficient")
        _advance_to_completion(engine, started.activity_id)
        inventory = engine.state.agents[1].inventory["home"]
        assert inventory[_INPUT_OBJECT] == 0
        assert inventory[_OUTPUT_OBJECT] == 2
    finally:
        engine.close()


def test_fractional_recipe_serving_scales_both_inventory_sides(tmp_path: Path) -> None:
    """原有小数份数协议通过真实使用动作同比转换输入输出。"""
    path = _fixture(tmp_path, held=1)
    engine = _engine(tmp_path, path, "fractional-serving")
    try:
        _, started = _consume(engine, "fractional-serving", quantity=0.5)
        _advance_to_completion(engine, started.activity_id)
        inventory = engine.state.agents[1].inventory["home"]
        assert inventory[_INPUT_OBJECT] == 0.5
        assert inventory[_OUTPUT_OBJECT] == 0.5
    finally:
        engine.close()


def test_zero_recipe_coefficient_rejects_before_run_artifacts(tmp_path: Path) -> None:
    """零系数不能把正数量配方静默降为无库存变化。"""
    path = _fixture(tmp_path, coefficient=0)
    run_dir = tmp_path / "zero-recipe-run"
    with pytest.raises(IntegrityError, match="recipe coefficient must be positive"):
        LifeEngine.create(path, run_dir, _identity(path, "zero-recipe"))
    assert not run_dir.exists()


def test_initial_inventory_undefined_rejects_before_run_artifacts(tmp_path: Path) -> None:
    """不在商品清单内的初始持有物仍须通过完整配对门禁。"""
    path = _fixture(tmp_path, input_state="undefined")
    run_dir = tmp_path / "undefined-holding-run"
    with pytest.raises(IntegrityError, match="undefined consume:use"):
        LifeEngine.create(path, run_dir, _identity(path, "undefined-holding"))
    assert not run_dir.exists()


@pytest.mark.parametrize("reachable", ("hidden_product", "future_release", "contract", "route"))
def test_all_reachable_pairings_reject_undefined_before_artifacts(
    tmp_path: Path, reachable: str,
) -> None:
    """隐藏商品、未来上架、合同和路线都由场景事实进入启动前门禁。"""
    path, catalog_dir, scenario = _formal_fixture(tmp_path)
    contract = _read_json(catalog_dir / "category-contract.json")
    expected = "undefined"
    if reachable in {"hidden_product", "future_release"}:
        product = scenario["products"][0]
        category_id = _isolate_hidden_product(catalog_dir, contract, scenario)
        _set_pairing_undefined(contract, category_id, "purchase")
        expected = "undefined purchase"
        if reachable == "future_release":
            scenario["product_releases"] = [{
                "release_id": "future-milk", "supply_id": f"supply-market-{product['product_id']}",
                "stock": 20, "source": _scenario_source(),
                "due_at": "2026-01-06T12:00:00+08:00",
            }]
    elif reachable == "contract":
        _set_pairing_undefined(contract, "contract-package", "work:settle")
        expected = "undefined work:settle"
    else:
        _set_pairing_undefined(contract, "travel-service", "travel:driving")
        expected = "undefined travel:driving"
    _write_json(catalog_dir / "category-contract.json", contract)
    _save_fixture(path, catalog_dir, scenario)
    run_dir = tmp_path / f"undefined-{reachable}-run"
    with pytest.raises(IntegrityError, match=expected):
        LifeEngine.create(path, run_dir, _identity(path, f"undefined-{reachable}"))
    assert not run_dir.exists()


def test_contract_rule_slot_must_bind_to_task_instance_before_artifacts(tmp_path: Path) -> None:
    """合同规则的命名金额槽不能换成任务类型未提供的另一个合法槽。"""
    path, catalog_dir, scenario = _formal_fixture(tmp_path)
    rules = _read_json(catalog_dir / "exchange-rules.json")
    package = next(item for item in rules["rules"] if item["rule_id"] == "settle-package")
    package["amount_slot"] = "work_rate_fen"
    _write_json(catalog_dir / "exchange-rules.json", rules)
    _save_fixture(path, catalog_dir, scenario)
    run_dir = tmp_path / "unbound-contract-slot-run"
    with pytest.raises(IntegrityError, match="does not bind rule slot work_rate_fen"):
        LifeEngine.create(path, run_dir, _identity(path, "unbound-contract-slot"))
    assert not run_dir.exists()


def test_purchase_transfer_must_debit_buyer_before_artifacts(tmp_path: Path) -> None:
    """购买转移规则只能从真实购买者账户扣款，其他合法角色在启动前拒绝。"""
    path, catalog_dir, scenario = _formal_fixture(tmp_path)
    rules = _read_json(catalog_dir / "exchange-rules.json")
    purchase = next(item for item in rules["rules"] if item["rule_id"] == "purchase-product")
    purchase["from_role"] = "employer"
    _write_json(catalog_dir / "exchange-rules.json", rules)
    _save_fixture(path, catalog_dir, scenario)
    run_dir = tmp_path / "non-buyer-purchase-run"
    with pytest.raises(IntegrityError, match="Purchase transfer roles must be buyer and merchant"):
        LifeEngine.create(path, run_dir, _identity(path, "non-buyer-purchase"))
    assert not run_dir.exists()


def test_purchase_coefficient_changes_real_life_engine_transfer(tmp_path: Path) -> None:
    """购买系数改变已编译报价，并由真实整单结算转移对应金额。"""
    baseline, _ = load_runtime_scenario(_SCENARIO)
    base_price = baseline.scenario.supplies[0].quotes[0].price_fen
    path, catalog_dir, scenario = _formal_fixture(tmp_path)
    contract = _read_json(catalog_dir / "category-contract.json")
    _set_pairing_coefficient(contract, "product", "purchase", 2)
    _write_json(catalog_dir / "category-contract.json", contract)
    _save_fixture(path, catalog_dir, scenario)
    engine = _engine(tmp_path, path, "purchase-coefficient")
    try:
        _stage_open_market(engine)
        product = engine.scenario.products[0]
        supply = next(item for item in engine.scenario.supplies
                      if item.product_id == product.product_id and item.location_id == "market")
        assert engine.semantics.purchase_amount(product.product_id, base_price, 1) == base_price * 2
        request = PurchaseOrderRequest(
            items=(PurchaseItem(supply_id=supply.supply_id, quantity=1,
                                price_version=engine.state.supply_quotes[supply.supply_id].price_version),),
            fulfillment=FulfillmentKind.DELIVERY, destination_id="home",
        )
        before = dict(engine.state.accounts)
        result = _action(engine, "purchase-coefficient", "purchase", request)
        assert result.data["total_price_fen"] == base_price * 2
        assert engine.state.accounts["agent:1"] == before["agent:1"] - base_price * 2
        assert engine.state.accounts[supply.seller_account_id] == (
            before[supply.seller_account_id] + base_price * 2)
    finally:
        engine.close()


def test_no_effect_purchase_loads_and_rejects_without_transfer(tmp_path: Path) -> None:
    """已审核无作用的商品可装配，但真实购买不扣款、不减库存、不建配送。"""
    path, catalog_dir, scenario = _formal_fixture(tmp_path)
    contract = _read_json(catalog_dir / "category-contract.json")
    contract["pairings"].append({
        "actor_category_id": "person", "target_category_id": "food-dairy",
        "action": "purchase", "state": "no_effect", "review": _review(),
    })
    _write_json(catalog_dir / "category-contract.json", contract)
    _save_fixture(path, catalog_dir, scenario)
    engine = _engine(tmp_path, path, "purchase-no-effect")
    try:
        _stage_open_market(engine)
        product = engine.scenario.products[0]
        supply = next(item for item in engine.scenario.supplies
                      if item.product_id == product.product_id and item.location_id == "market")
        request = PurchaseOrderRequest(
            items=(PurchaseItem(supply_id=supply.supply_id, quantity=1,
                                price_version=engine.state.supply_quotes[supply.supply_id].price_version),),
            fulfillment=FulfillmentKind.DELIVERY, destination_id="home",
        )
        before = (dict(engine.state.accounts), dict(engine.state.supply_stock),
                  dict(engine.state.deliveries))
        body = engine.state.agents[1].body.model_dump(mode="python")
        effects = set(engine.state.agents[1].active_effects)
        result = _action(engine, "purchase-no-effect", "purchase", request)
        assert result.status.value == "rejected"
        assert result.error_code == "CATEGORY_PAIRING_NO_EFFECT"
        assert (engine.state.accounts, engine.state.supply_stock,
                engine.state.deliveries) == before
        assert engine.state.agents[1].body.model_dump(mode="python") == body
        assert engine.state.agents[1].active_effects == effects
    finally:
        engine.close()


def test_food_coefficient_changes_real_life_engine_body_settlement(tmp_path: Path) -> None:
    """食品系数进入真实进食活动速率并改变完成后的身体能量。"""
    engines = []
    outcomes = []
    try:
        for label, coefficient in (("base", 1), ("scaled", 2)):
            path, catalog_dir, scenario = _formal_fixture(tmp_path / label)
            product_id = scenario["products"][0]["product_id"]
            scenario["agents"][0]["initial_inventory"] = {"home": {product_id: 1}}
            contract = _read_json(catalog_dir / "category-contract.json")
            _set_pairing_coefficient(contract, "food", "consume:eat", coefficient)
            _write_json(catalog_dir / "category-contract.json", contract)
            _save_fixture(path, catalog_dir, scenario)
            engine = _engine(tmp_path, path, f"food-{label}")
            engines.append(engine)
            product = engine.rules.items[product_id]
            request = ConsumeRequest(
                items=(ConsumeItem(product_id=product_id, quantity=1),),
                use_mode="eat", duration_seconds=product.use_duration_seconds,
            )
            started = _action(engine, f"food-{label}", "consume", request)
            activity = engine.state.activities[started.activity_id]
            rate = activity.data["food_energy_kcal_per_hour"]
            _advance_to_completion(engine, started.activity_id)
            outcomes.append((product.energy_kcal_per_unit, rate,
                             engine.state.agents[1].body.energy_kcal))
        assert outcomes[1][0] == outcomes[0][0] * 2
        assert outcomes[1][1] == outcomes[0][1] * 2
        assert outcomes[1][2] > outcomes[0][2]
    finally:
        for engine in engines:
            engine.close()


def test_durable_rule_effect_slot_drives_real_use_effect(tmp_path: Path) -> None:
    """耐用品规则改用另一个合法效果槽后，真实使用活动采用该槽值。"""
    path, catalog_dir, scenario = _formal_fixture(tmp_path)
    contract = _read_json(catalog_dir / "category-contract.json")
    category_index, durable = next(
        (index, item) for index, item in enumerate(contract["categories"])
        if item["category_id"] == "durable")
    durable["slots"]["verified_effect"] = {
        "schema": {"type": "string", "minLength": 1}, "required": True,
    }
    durable["values"]["absolute"]["verified_effect"] = "verified_weather_protection"
    contract["sources"].append(_source("verified-durable-effect", [
        f"/categories/{category_index}/slots/verified_effect/schema/minLength",
    ]))
    _write_json(catalog_dir / "category-contract.json", contract)
    rules = _read_json(catalog_dir / "exchange-rules.json")
    use_rule = next(item for item in rules["rules"] if item["rule_id"] == "use-durable")
    use_rule["effect_slot"] = "verified_effect"
    _write_json(catalog_dir / "exchange-rules.json", rules)
    scenario["agents"][0]["initial_inventory"] = {"home": {"item-umbrella": 1}}
    _save_fixture(path, catalog_dir, scenario)
    engine = _engine(tmp_path, path, "durable-effect-slot")
    try:
        item = engine.rules.items["item-umbrella"]
        assert item.effect == "verified_weather_protection"
        request = ConsumeRequest(
            items=(ConsumeItem(product_id=item.product_id, quantity=1),),
            use_mode="use", duration_seconds=item.use_duration_seconds,
        )
        started = _action(engine, "durable-effect-slot", "consume", request)
        _advance_to_completion(engine, started.activity_id)
        assert "verified_weather_protection" in engine.state.agents[1].active_effects
    finally:
        engine.close()


def test_food_rule_duration_slot_drives_real_consume_duration(tmp_path: Path) -> None:
    """食品规则改用另一个合法时长槽后，真实进食活动采用该槽值。"""
    path, catalog_dir, scenario = _formal_fixture(tmp_path)
    contract = _read_json(catalog_dir / "category-contract.json")
    category_index, food = next(
        (index, item) for index, item in enumerate(contract["categories"])
        if item["category_id"] == "food")
    food["slots"]["verified_duration_seconds"] = {
        "schema": {"type": "integer", "minimum": 1},
        "required": True, "unit": "second",
    }
    food["values"]["absolute"]["verified_duration_seconds"] = 321
    contract["sources"].append(_source("verified-food-duration", [
        f"/categories/{category_index}/slots/verified_duration_seconds/schema/minimum",
    ]))
    _write_json(catalog_dir / "category-contract.json", contract)
    rules = _read_json(catalog_dir / "exchange-rules.json")
    food_rule = next(item for item in rules["rules"] if item["rule_id"] == "consume-food")
    food_rule["duration_slot"] = "verified_duration_seconds"
    _write_json(catalog_dir / "exchange-rules.json", rules)
    product_id = scenario["products"][0]["product_id"]
    scenario["agents"][0]["initial_inventory"] = {"home": {product_id: 1}}
    _save_fixture(path, catalog_dir, scenario)
    engine = _engine(tmp_path, path, "food-duration-slot")
    try:
        item = engine.rules.items[product_id]
        assert item.use_duration_seconds == 321
        request = ConsumeRequest(
            items=(ConsumeItem(product_id=product_id, quantity=1),),
            use_mode="eat", duration_seconds=321,
        )
        started = _action(engine, "food-duration-slot", "consume", request)
        _advance_to_completion(engine, started.activity_id)
        activity = engine.state.activities[started.activity_id]
        assert activity.elapsed_seconds == 321
        assert engine.state.agents[1].inventory["home"][product_id] == 0
    finally:
        engine.close()


def test_travel_coefficient_changes_real_life_engine_payment(tmp_path: Path) -> None:
    """出行系数改变冻结路线报价并由真实启程动作预付对应金额。"""
    path, catalog_dir, scenario = _formal_fixture(tmp_path)
    routes = _read_json(Path("corpora/zhangjiang-map/routes.json"))["routes"]
    baseline = next(item for item in routes
                    if item["origin_id"] == "home" and item["destination_id"] == "office"
                    and item["mode"] == "driving")["fare_fen"]
    contract = _read_json(catalog_dir / "category-contract.json")
    _set_pairing_coefficient(contract, "travel-service", "travel:driving", 2)
    _write_json(catalog_dir / "category-contract.json", contract)
    rules = _read_json(catalog_dir / "exchange-rules.json")
    driving = next(item for item in rules["rules"] if item["rule_id"] == "travel-driving")
    for route in routes:
        if route["mode"] == "driving":
            distance = Decimal(str(route["distance_meters"]))
            amount = max(Decimal(driving["minimum_fen"]),
                         distance * Decimal(str(driving["fen_per_meter"]))) * 2
            route["fare_fen"] = int(amount.to_integral_value(rounding=ROUND_CEILING))
    _replace_map_file(path.parent.parent, scenario, "routes", routes)
    _save_fixture(path, catalog_dir, scenario)
    engine = _engine(tmp_path, path, "travel-coefficient")
    try:
        route = next(item for item in engine.scenario.routes
                     if item.origin_id == "home" and item.destination_id == "office"
                     and item.mode.value == "driving")
        before = dict(engine.state.accounts)
        request = StartTravelRequest(
            destination_id="office", travel_mode="driving",
            planned_duration_seconds=travel_quote(engine, "driving", route.duration_seconds),
        )
        result = _action(engine, "travel-coefficient", "start_travel", request)
        assert result.data["travel_payment"]["fare_fen"] == baseline * 2
        assert engine.state.accounts["agent:1"] == before["agent:1"] - baseline * 2
        assert engine.state.accounts[engine.scenario.transport_account_id] == (
            before[engine.scenario.transport_account_id] + baseline * 2)
    finally:
        engine.close()


@pytest.mark.asyncio
async def test_work_coefficient_changes_real_life_engine_payment(tmp_path: Path) -> None:
    """总包系数通过唯一提交路径改变真实 LifeEngine 的合同付款。"""
    from tests.life.test_work_review import (
        StaticReviewer,
        _at_task_start,
        _complete_required_work,
        _paraphrased_submission,
        _submit_with_review,
    )

    path, catalog_dir, scenario = _formal_fixture(tmp_path)
    contract = _read_json(catalog_dir / "category-contract.json")
    _set_pairing_coefficient(contract, "contract-package", "work:settle", 2)
    _write_json(catalog_dir / "category-contract.json", contract)
    _save_fixture(path, catalog_dir, scenario)
    engine = _engine(tmp_path, path, "work-coefficient")
    try:
        _at_task_start(engine)
        _complete_required_work(engine)
        task = engine.scenario.tasks[0]
        before = dict(engine.state.accounts)
        result = await _submit_with_review(
            engine, StaticReviewer(), "work-coefficient", _paraphrased_submission(task.task_id))
        assert result.data["payment_fen"] == task.pay_fen * 2
        assert engine.state.accounts["agent:1"] == before["agent:1"] + task.pay_fen * 2
        assert engine.state.accounts[task.employer_account_id] == before[task.employer_account_id] - task.pay_fen * 2
    finally:
        engine.close()


def test_recipe_cancel_and_insufficient_inventory_leave_no_conversion(tmp_path: Path) -> None:
    """取消和输入不足都不能产生输出库存。"""
    cancel_path = _fixture(tmp_path / "cancel")
    cancelled = _engine(tmp_path, cancel_path, "cancel")
    try:
        wake_by_mail(cancelled, offset=60)
        _, started = _consume(cancelled, "cancel-start")
        advance_next(cancelled)
        current = context(cancelled, "cancel-finish")
        cancelled.begin_decision_round([current])
        cancelled.observe(current)
        request = SetActivityStateRequest(activity_id=started.activity_id, operation="cancel")
        cancelled.submit_action(DirectToolCall(
            current, "cancel-recipe", "set_activity_state", request))
        result = cancelled.commit_decision_round("cancel-finish")[0]
        assert result.status.value == "completed"
        assert cancelled.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 1.0}
    finally:
        cancelled.close()

    short_path = _fixture(tmp_path / "short")
    short = _engine(tmp_path, short_path, "short")
    try:
        before = copy.deepcopy(short.state.agents[1].inventory)
        _, rejected = _consume(short, "insufficient", quantity=2)
        assert rejected.status.value == "rejected"
        assert rejected.error_code == "PRODUCT_NOT_HELD"
        assert short.state.agents[1].inventory == before
        assert short.state.activities == {}
    finally:
        short.close()


@pytest.mark.parametrize("quantity", (1, 1e-10))
def test_recipe_ledger_failure_rolls_back_activity_inventory_and_footprint(tmp_path: Path, quantity: float) -> None:
    """闭合账本上界失败时推进事务不提交任何部分状态。"""
    path = _fixture(tmp_path, held=1, output_held=1, maximum=1)
    engine = _engine(tmp_path, path, "rollback")
    try:
        _, started = _consume(engine, "rollback-start", quantity=quantity)
        before_state = engine.state.model_dump(mode="json")
        before_seq = engine.store.last_event_seq
        with pytest.raises(BusinessRuleError, match="OUT_OF_BOUNDS"):
            _advance_to_completion(engine, started.activity_id)
        assert engine.state.model_dump(mode="json") == before_state
        assert engine.store.last_event_seq == before_seq
    finally:
        engine.close()


def test_no_effect_loads_but_runtime_use_rejects_without_resource_change(tmp_path: Path) -> None:
    """已验证不作用可装配，真实动作拒绝且库存和活动不变。"""
    path = _fixture(tmp_path, input_state="no_effect")
    engine = _engine(tmp_path, path, "no-effect")
    try:
        inventory = copy.deepcopy(engine.state.agents[1].inventory)
        accounts = dict(engine.state.accounts)
        body = engine.state.agents[1].body.model_dump(mode="python")
        effects = set(engine.state.agents[1].active_effects)
        _, rejected = _consume(engine, "no-effect")
        assert rejected.status.value == "rejected"
        assert rejected.error_code == "CATEGORY_PAIRING_NO_EFFECT"
        assert engine.state.agents[1].inventory == inventory
        assert engine.state.accounts == accounts
        assert engine.state.activities == {}
        assert engine.state.agents[1].body.model_dump(mode="python") == body
        assert engine.state.agents[1].active_effects == effects
        advance_next(engine)
        replay_body = engine.state.agents[1].body.model_dump(mode="python")
        _, replayed = _consume(engine, "no-effect-replay")
        assert replayed == rejected
        assert engine.state.agents[1].consecutive_rejections == 1
        assert engine.state.agents[1].body.model_dump(mode="python") == replay_body
        assert engine.state.agents[1].inventory == inventory
        assert engine.state.accounts == accounts
    finally:
        engine.close()


def test_recursive_recipe_output_undefined_rejects_before_run_artifacts(tmp_path: Path) -> None:
    """配方输出仍可使用时递归闭包中的未定义在建运行前拒绝。"""
    path = _fixture(tmp_path, output_state="undefined")
    run_dir = tmp_path / "never-created"
    with pytest.raises(IntegrityError, match="undefined consume:use"):
        LifeEngine.create(path, run_dir, _identity(path, "undefined-output"))
    assert not run_dir.exists()


def test_tiny_recipe_quantity_changes_both_real_inventory_balances(tmp_path: Path) -> None:
    """合法微量配方完整结束后仍保留实际扣入和产出，不能固定小数位归零。"""
    path = _fixture(tmp_path)
    engine = _engine(tmp_path, path, "tiny-quantity")
    try:
        _, started = _consume(engine, "tiny-quantity", quantity=1e-10)
        assert started.status.value == "in_progress"
        _advance_to_completion(engine, started.activity_id)
        inventory = engine.state.agents[1].inventory["home"]
        assert inventory[_INPUT_OBJECT] == 1 - 1e-10
        assert inventory[_OUTPUT_OBJECT] == 1e-10
        assert engine.state.activities[started.activity_id].data["consumed"] == {_INPUT_OBJECT: 1e-10}
    finally:
        engine.close()


def test_new_tiny_recipe_request_after_exhaustion_is_rejected(tmp_path: Path) -> None:
    """先通过真实活动耗尽库存，再提交新身份微量请求时不能靠绝对容差放行。"""
    path = _fixture(tmp_path)
    engine = _engine(tmp_path, path, "tiny-after-empty")
    try:
        _, started = _consume(engine, "exhaust")
        _advance_to_completion(engine, started.activity_id)
        before = engine.state.model_dump(mode="python")
        _, rejected = _consume(engine, "after-empty", "new-tiny-action", quantity=1e-10)
        assert rejected.status.value == "rejected"
        assert rejected.error_code == "PRODUCT_NOT_HELD"
        assert engine.state.agents[1].inventory == before["agents"][1]["inventory"]
        assert engine.state.activities.keys() == before["activities"].keys()
    finally:
        engine.close()


def test_recipe_small_coefficient_checks_actual_scaled_inventory(tmp_path: Path) -> None:
    """一份请求乘半量系数只需要半件输入，真实完成后只产出半件。"""
    path = _fixture(tmp_path, held=0.5, coefficient=0.5)
    engine = _engine(tmp_path, path, "half-coefficient")
    try:
        _, started = _consume(engine, "half-coefficient")
        assert started.status.value == "in_progress"
        _advance_to_completion(engine, started.activity_id)
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 0, _OUTPUT_OBJECT: 0.5}
    finally:
        engine.close()


@pytest.mark.asyncio
async def test_no_effect_work_loads_and_rejects_settlement_without_payment(tmp_path: Path) -> None:
    """已审核无作用的合同可创建运行，但真实提交不调用审核或支付报酬。"""
    from tests.life.test_work_review import (
        StaticReviewer, _at_task_start, _complete_required_work, _paraphrased_submission, _submit_with_review,
    )

    path, catalog_dir, scenario = _formal_fixture(tmp_path)
    contract = _read_json(catalog_dir / "category-contract.json")
    _set_pairing_undefined(contract, "contract-package", "work:settle")
    pairing = next(item for item in contract["pairings"]
                   if item["target_category_id"] == "contract-package" and item["action"] == "work:settle")
    pairing.update(state="no_effect", review=_review())
    _write_json(catalog_dir / "category-contract.json", contract)
    _save_fixture(path, catalog_dir, scenario)
    engine = _engine(tmp_path, path, "no-effect-work")
    try:
        _at_task_start(engine)
        _complete_required_work(engine)
        before = engine.state.model_dump(mode="python")
        reviewer = StaticReviewer()
        result = await _submit_with_review(
            engine, reviewer, "no-effect-work", _paraphrased_submission(engine.scenario.tasks[0].task_id))
        assert result.status.value == "rejected"
        assert result.error_code == "CATEGORY_PAIRING_NO_EFFECT"
        assert reviewer.calls == 0
        assert engine.state.accounts == before["accounts"]
        assert engine.state.paid_task_ids == before["paid_task_ids"]
        assert engine.state.agents[1].inventory == before["agents"][1]["inventory"]
        assert engine.state.agents[1].body.model_dump(mode="python") == before["agents"][1]["body"]
        assert engine.state.agents[1].active_effects == before["agents"][1]["active_effects"]
        assert engine.state.model_dump(mode="python")["activities"] == before["activities"]
    finally:
        engine.close()


@pytest.mark.parametrize(("kind", "expected"), (("hourly", 25_315), ("piece", 37_503)))
@pytest.mark.asyncio
async def test_hourly_and_piece_coefficients_change_real_submit_work_payment(
    tmp_path: Path, kind: str, expected: int,
) -> None:
    """非单位系数在实际工时与验收件数上生效，公开提交后逐一核对收付款账户。"""
    from tests.life.test_work_review import (
        StaticReviewer, _at_task_start, _complete_required_work, _paraphrased_submission, _submit_with_review,
    )

    path, catalog_dir, scenario = _formal_fixture(tmp_path)
    task_document = scenario["tasks"][0]
    task_document.update(contract_kind=kind, pay_fen=10_001,
                         maximum_duration_seconds=7_290, required_effective_seconds=60)
    contract = _read_json(catalog_dir / "category-contract.json")
    _set_pairing_coefficient(contract, f"contract-{kind}", "work:settle", 1.25)
    _write_json(catalog_dir / "category-contract.json", contract)
    _save_fixture(path, catalog_dir, scenario)
    engine = _engine(tmp_path, path, f"work-{kind}-coefficient")
    try:
        _at_task_start(engine)
        _complete_required_work(engine)
        task = engine.scenario.tasks[0]
        oracle = _read_json(Path(scenario["corpus_root"]) / task.oracle_path)
        assert oracle["piece_count"] == 3
        before = dict(engine.state.accounts)
        reviewer = StaticReviewer()
        result = await _submit_with_review(
            engine, reviewer, f"work-{kind}-coefficient", _paraphrased_submission(task.task_id))
        assert result.status.value == "completed"
        assert result.data["worked_seconds"] == 7_290
        assert result.data["payment_fen"] == expected
        assert reviewer.calls == 1
        assert engine.state.accounts["agent:1"] == before["agent:1"] + expected
        assert engine.state.accounts[task.employer_account_id] == before[task.employer_account_id] - expected
        assert sum(engine.state.accounts.values()) == sum(before.values())
    finally:
        engine.close()


def test_recipe_completion_sql_failure_keeps_engine_and_store_unchanged(tmp_path: Path) -> None:
    """配方已计算完而真实 SQL 提交失败时，内存、库存、活动与事件序号全部回滚。"""
    path = _fixture(tmp_path)
    engine = _engine(tmp_path, path, "sql-rollback")
    try:
        _, started = _consume(engine, "sql-rollback")
        engine.observe(context(engine, "recipe-before-failed-advance"))
        engine._pending_presentation = {1: {_INPUT_OBJECT: engine.state.current_time}}
        engine._pending_visible_supplies = {1: {engine.scenario.supplies[0].supply_id}}
        before_visible = copy.deepcopy(engine._pending_visible_supplies)
        before_state = engine.state.model_dump(mode="json")
        before_store = copy.deepcopy(engine.store.state)
        before_seq = engine.store.last_event_seq
        before_events = engine.store.read_events(before_seq)
        before_presentation = copy.deepcopy(engine._pending_presentation)
        engine.store.connection.executescript("""
            CREATE TRIGGER fail_recipe_state_update BEFORE UPDATE ON world_state
            WHEN json_extract(NEW.body, '$.agents."1".inventory.home."opened-item"') = 1
            BEGIN SELECT RAISE(ABORT, 'Injected recipe commit failure'); END;
        """)
        with pytest.raises(sqlite3.IntegrityError, match="Injected recipe commit failure"):
            _advance_to_completion(engine, started.activity_id)
        assert engine.state.model_dump(mode="json") == before_state
        assert engine.store.state == before_store
        assert engine.store.last_event_seq == before_seq
        assert engine.store.read_events(before_seq) == before_events
        assert engine._pending_presentation == before_presentation
        assert engine._pending_visible_supplies == before_visible
        engine.store.connection.execute("DROP TRIGGER fail_recipe_state_update")
        _advance_to_completion(engine, started.activity_id)
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 0, _OUTPUT_OBJECT: 1}
    finally:
        engine.close()


def test_recipe_pause_wait_resume_produces_output_only_once_at_completion(tmp_path: Path) -> None:
    """真实暂停保留部分进度，等待不产出，恢复完整结束才一次完成转换。"""
    path = _fixture(tmp_path)
    engine = _engine(tmp_path, path, "pause-resume")
    try:
        wake_by_mail(engine, offset=60)
        _, started = _consume(engine, "pause-start")
        advance_next(engine)
        before = copy.deepcopy(engine.state.agents[1].inventory)
        assert engine.state.activities[started.activity_id].elapsed_seconds == 60
        result = _action(engine, "pause-recipe", "set_activity_state", SetActivityStateRequest(
            activity_id=started.activity_id, operation="pause"))
        assert result.status.value == "completed"
        assert engine.state.agents[1].inventory == before
        assert advance_until_ready(engine)
        waited = _action(engine, "wait-paused-recipe", "start_basic_activity", StartBasicActivityRequest(
            activity_type="wait", planned_duration_seconds=600))
        _advance_to_completion(engine, waited.activity_id)
        paused = engine.state.activities[started.activity_id]
        assert paused.status.value == "paused" and paused.elapsed_seconds == 60
        assert engine.state.agents[1].inventory == before
        assert not paused.data.get("recipe_applied") and paused.data["consumed"] == {}
        resumed = _action(engine, "resume-recipe", "set_activity_state", SetActivityStateRequest(
            activity_id=started.activity_id, operation="resume"))
        assert resumed.status.value == "completed"
        assert engine.state.agents[1].inventory == before
        _advance_to_completion(engine, started.activity_id)
        finished = engine.state.activities[started.activity_id]
        assert finished.elapsed_seconds == 300 and finished.data["recipe_applied"] is True
        assert finished.data["consumed"] == {_INPUT_OBJECT: 1.0}
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 0, _OUTPUT_OBJECT: 1}
        transformations = [event for event in engine.store.read_events(engine.store.last_event_seq)
                           if event.event_type == "activity_progress" and event.activity_id == started.activity_id
                           and event.data.get("consumption", {}).get("inventory_transform")]
        assert len(transformations) == 1
    finally:
        engine.close()


@pytest.mark.parametrize("mismatch", ("category", "unit"))
def test_valid_output_object_with_wrong_recipe_binding_rejects_before_artifacts(
    tmp_path: Path, mismatch: str,
) -> None:
    """输出对象本身可加载，但错误品类或库存单位的绑定必须在创建运行前拒绝。"""
    from worldmock.catalog import load_catalog

    path = _fixture(tmp_path)
    catalog_dir = path.parent.parent / "catalog"
    scenario = _read_json(path)
    if mismatch == "category":
        scenario["recipe_output_bindings"][_OUTPUT_CATEGORY] = "item-umbrella"
        message = "does not belong to durable-opened"
    else:
        contract = _read_json(catalog_dir / "category-contract.json")
        durable = next(item for item in contract["categories"] if item["category_id"] == "durable")
        durable["slots"]["inventory_unit"]["schema"]["enum"].append("box")
        _write_json(catalog_dir / "category-contract.json", contract)
        objects_path = catalog_dir / "objects.jsonl"
        objects = [json.loads(line) for line in objects_path.read_text().splitlines()]
        output = next(item for item in objects if item["object_id"] == _OUTPUT_OBJECT)
        output["values"]["absolute"]["inventory_unit"] = "box"
        objects_path.write_text("".join(json.dumps(item) + "\n" for item in objects))
        message = "uses another inventory unit"
    _save_fixture(path, catalog_dir, scenario)
    catalog = load_catalog(catalog_dir)
    bound = scenario["recipe_output_bindings"][_OUTPUT_CATEGORY]
    assert bound in catalog.objects
    if mismatch == "unit":
        assert catalog.objects[bound].attribute("inventory_unit") == "box"
    run_dir = tmp_path / f"bad-output-{mismatch}"
    with pytest.raises(IntegrityError, match=message):
        LifeEngine.create(path, run_dir, _identity(path, f"bad-output-{mismatch}"))
    assert not run_dir.exists()


@pytest.mark.parametrize(("quantity", "coefficient", "output_held"), (
    (1e-100, 1e-300, 0), (1e-20, 1, 0), (1e-10, 1, 1e30),
))
def test_unrepresentable_recipe_rejects_without_free_effect(
    tmp_path: Path, quantity: float, coefficient: float, output_held: float,
) -> None:
    """拒绝下溢以及无法改变任一真实余额的正配方，不引入固定最小数量。"""
    path = _fixture(tmp_path, coefficient=coefficient, output_held=output_held)
    engine = _engine(tmp_path, path, "unrepresentable")
    try:
        before = engine.state.agents[1].model_dump(mode="python")
        _, result = _consume(engine, "unrepresentable", quantity=quantity)
        assert result.status.value == "rejected"
        assert result.error_code == "INVENTORY_RECIPE_QUANTITY_UNREPRESENTABLE"
        assert engine.state.agents[1].inventory == before["inventory"]
        assert engine.state.agents[1].active_effects == before["active_effects"]
        assert engine.state.activities == {}
    finally:
        engine.close()


def test_recipe_completion_rechecks_inventory_without_quantity_tolerance(tmp_path: Path) -> None:
    """活动受理后库存耗尽，完成时再次严格复查，不允许容差掩盖新的不足。"""
    from worldmock.footprints.models import EventDraft

    path = _fixture(tmp_path)
    engine = _engine(tmp_path, path, "completion-shortage")
    try:
        _, started = _consume(engine, "completion-shortage", quantity=1e-10)
        staged = engine.state.model_copy(deep=True)
        staged.agents[1].inventory["home"][_INPUT_OBJECT] = 0
        staged.version += 1
        engine.store.commit(staged.model_dump(mode="json"), [EventDraft(
            event_type="environment_event", occurred_at=staged.current_time,
            visibility="system", data={"kind": "test_inventory_exhausted"})])
        engine.state = staged
        before = engine.state.model_dump(mode="json")
        before_seq = engine.store.last_event_seq
        with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_HELD"):
            _advance_to_completion(engine, started.activity_id)
        assert engine.state.model_dump(mode="json") == before
        assert engine.store.state == before
        assert engine.store.last_event_seq == before_seq
    finally:
        engine.close()


def test_recipe_scales_once_and_preserves_representable_subnormal_quantity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配方仅在受理时缩放一次，现有浮点库存可表达的极小数量仍可正常转换。"""
    from worldmock.life import inventory_recipe

    original = inventory_recipe.scale_inventory_recipe
    calls = []

    def record_scale(rule, servings, coefficient):
        calls.append((servings, coefficient))
        return original(rule, servings, coefficient)

    monkeypatch.setattr(inventory_recipe, "scale_inventory_recipe", record_scale)
    path = _fixture(tmp_path, held=1e-320)
    engine = _engine(tmp_path, path, "subnormal")
    try:
        _, started = _consume(engine, "subnormal", quantity=1e-320)
        assert started.status.value == "in_progress"
        _advance_to_completion(engine, started.activity_id)
        assert calls == [(1e-320, 1)]
        assert engine.state.agents[1].inventory["home"] == {_INPUT_OBJECT: 0, _OUTPUT_OBJECT: 1e-320}
    finally:
        engine.close()


def test_runtime_undefined_use_rejects_without_body_or_resource_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行复查发现未定义时保持身体和资源原值，不触发通用服务拒绝情绪。"""
    from types import SimpleNamespace

    from worldmock.categories import PairingState

    path = _fixture(tmp_path)
    engine = _engine(tmp_path, path, "runtime-undefined")
    catalog_type = type(engine.semantics.catalog)
    original = catalog_type.pairing

    def undefined_input(self, actor, target, action):
        if target == _INPUT_CATEGORY and action.value == "consume:use":
            return SimpleNamespace(state=PairingState.UNDEFINED)
        return original(self, actor, target, action)

    monkeypatch.setattr(catalog_type, "pairing", undefined_input)
    try:
        before = engine.state.model_dump(mode="python")
        _, result = _consume(engine, "runtime-undefined")
        assert result.status.value == "rejected" and result.error_code == "CATEGORY_PAIRING_UNDEFINED"
        assert engine.state.agents[1].body.model_dump(mode="python") == before["agents"][1]["body"]
        assert engine.state.agents[1].inventory == before["agents"][1]["inventory"]
        assert engine.state.agents[1].active_effects == before["agents"][1]["active_effects"]
        assert engine.state.accounts == before["accounts"]
        assert engine.state.activities == {}
    finally:
        engine.close()
