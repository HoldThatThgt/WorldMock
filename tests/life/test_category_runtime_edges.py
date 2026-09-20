"""品类运行公共接口的失败关闭与幂等边界测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from worldmock.categories import Action, CategoryError, InventoryTransformRule, PairingState
from worldmock.errors import BusinessRuleError, IntegrityError
from worldmock.life.category_contract import (
    RuntimeCategoryRule,
    optional_defined_rule,
    require_item_rules,
    require_runtime_rule,
)
from worldmock.life.inventory_recipe import (
    InventoryRecipePlan,
    RecipeContext,
    apply_inventory_recipe,
    prepare_inventory_recipe,
)
from worldmock.life.models import (
    ActivityState,
    AgentState,
    BodyState,
    ConsumeItem,
    ConsumeRequest,
)
from worldmock.life.scenario import InventoryItemConfig


class _Rules:
    """为配对边界提供最小规则查找表。"""

    def __init__(self, rule: object | None = None) -> None:
        self.rule = object() if rule is None else rule

    def require(self, _rule_id: str) -> object:
        return self.rule


class _Catalog:
    """精确控制一次配对解析结果或错误。"""

    def __init__(self, resolution: object | None = None,
                 error: CategoryError | None = None) -> None:
        self.resolution = resolution
        self.error = error
        self.exchange_rules = _Rules()

    def pairing(self, _actor: str, _target: str, _action: Action) -> object:
        if self.error is not None:
            raise self.error
        assert self.resolution is not None
        return self.resolution


class _CategoryContract:
    """只接受精确品类身份的配方测试契约。"""

    @staticmethod
    def is_descendant(category_id: str, ancestor_id: str) -> bool:
        return category_id == ancestor_id


def _resolution(state: PairingState, rule_id: str | None = None,
                coefficient: int | float | None = None) -> object:
    """形成可表达损坏 defined 状态的配对结果。"""
    return SimpleNamespace(state=state, rule_id=rule_id, coefficient=coefficient)


def _recipe_rule() -> InventoryTransformRule:
    """形成一对一封闭库存转换规则。"""
    return InventoryTransformRule.model_validate({
        "rule_id": "open-item",
        "category_id": "sealed",
        "action": "consume:use",
        "operator": "inventory_transform",
        "resource_id": "inventory_closed",
        "unit": "item",
        "conservation": "closed",
        "input_category_id": "sealed",
        "output_category_id": "opened",
        "input_quantity": 1,
        "output_quantity": 1,
        "inventory_unit": "item",
    })


def _item(object_id: str, category_id: str) -> InventoryItemConfig:
    """形成一个可在当前位置持有和使用的库存对象。"""
    return InventoryItemConfig(
        product_id=object_id,
        name=object_id,
        category_id=category_id,
        inventory_unit="item",
        use_duration_seconds=60,
        use_modes=("use",),
        consumable=True,
        effect="package_opened",
        delivery_delay_seconds=60,
        source_kind=f"test:{object_id}",
    )


def _agent(held: float = 1) -> AgentState:
    """形成带密封物品的人物状态。"""
    return AgentState(
        agent_id=1,
        name="tester",
        location_id="home",
        balance_account_id="agent:1",
        body=BodyState(energy_kcal=1000, entertainment_need=0),
        inventory={"home": {"sealed-item": held, "opened-item": 0}},
        next_decision_at=0,
    )


def _request() -> ConsumeRequest:
    """形成一次完整份数的使用请求。"""
    return ConsumeRequest(
        items=(ConsumeItem(product_id="sealed-item", quantity=1),),
        use_mode="use",
        duration_seconds=60,
    )


def _context(bind_output: bool = True) -> RecipeContext:
    """形成带显式输出绑定的配方上下文。"""
    products = {
        "sealed-item": _item("sealed-item", "sealed"),
        "opened-item": _item("opened-item", "opened"),
    }
    bindings = {"opened": "opened-item"} if bind_output else {}
    catalog = SimpleNamespace(category_contract=_CategoryContract())
    return RecipeContext(catalog, products, bindings)


def _match(coefficient: int | float = 1) -> RuntimeCategoryRule:
    """形成真实库存规则的运行配对。"""
    return RuntimeCategoryRule("sealed", Action.CONSUME_USE, coefficient, _recipe_rule())


@pytest.mark.parametrize(
    ("catalog", "error_type", "message"),
    (
        (
            _Catalog(error=CategoryError("PAIRING_AMBIGUOUS", "test ambiguity")),
            IntegrityError,
            "Runtime category pairing failed: PAIRING_AMBIGUOUS",
        ),
        (
            _Catalog(_resolution(PairingState.NO_EFFECT)),
            BusinessRuleError,
            "CATEGORY_PAIRING_NO_EFFECT",
        ),
        (
            _Catalog(_resolution(PairingState.UNDEFINED)),
            BusinessRuleError,
            "CATEGORY_PAIRING_UNDEFINED",
        ),
    ),
)
def test_runtime_pairing_failures_do_not_produce_a_rule(
    catalog: _Catalog, error_type: type[Exception], message: str,
) -> None:
    """歧义、无作用和未定义三态均以稳定原因失败关闭。"""
    with pytest.raises(error_type, match=message):
        require_runtime_rule(catalog, "durable", Action.CONSUME_USE)


def test_optional_pairing_wraps_resolution_error() -> None:
    """组装期配对歧义保持为完整性错误，不降级成未定义。"""
    catalog = _Catalog(error=CategoryError("PAIRING_AMBIGUOUS", "test ambiguity"))
    with pytest.raises(IntegrityError, match="Category pairing resolution failed: PAIRING_AMBIGUOUS"):
        optional_defined_rule(catalog, "durable", Action.CONSUME_USE)


def test_optional_defined_pairing_requires_rule_identity_and_coefficient() -> None:
    """损坏的 defined 结果不能进入组装视图。"""
    catalog = _Catalog(_resolution(PairingState.DEFINED))
    with pytest.raises(IntegrityError, match="Defined category pairing has no rule or coefficient"):
        optional_defined_rule(catalog, "durable", Action.CONSUME_USE)


def test_runtime_defined_pairing_requires_rule_identity_and_coefficient() -> None:
    """运行复查也拒绝缺少规则身份或系数的损坏 defined 结果。"""
    catalog = _Catalog(_resolution(PairingState.DEFINED))
    with pytest.raises(IntegrityError, match="Defined category pairing has no rule or coefficient"):
        require_runtime_rule(catalog, "durable", Action.CONSUME_USE)


def test_item_rule_batch_rejects_unknown_object_before_pairing() -> None:
    """批量结算含未知对象时整批拒绝，且不查询品类规则。"""
    catalog = _Catalog(error=AssertionError("pairing must not run"))
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_FOUND"):
        require_item_rules(catalog, {}, ("missing-item",), Action.CONSUME_USE)


@pytest.mark.parametrize(
    "payload",
    (
        {},
        {
            "rule_id": "open-item",
            "input_object_id": "sealed-item",
            "output_object_id": "opened-item",
            "input_quantity": "not-a-number",
            "output_quantity": "1",
            "resource_id": "inventory_closed",
            "inventory_unit": "item",
        },
    ),
)
def test_recipe_plan_rejects_incomplete_or_nonnumeric_persisted_payload(payload: dict) -> None:
    """缺字段或损坏数量的活动快照不能恢复为可提交计划。"""
    with pytest.raises(IntegrityError, match="Inventory recipe activity payload is invalid"):
        InventoryRecipePlan.from_payload(payload)


def test_prepare_recipe_turns_invalid_runtime_coefficient_into_business_rejection() -> None:
    """即使启动门后状态损坏为零系数，动作受理仍失败关闭。"""
    agent = _agent()
    before = agent.model_dump(mode="python")
    with pytest.raises(BusinessRuleError, match="EXCHANGE_INPUT_INVALID"):
        prepare_inventory_recipe(agent, _context(), _request(), _match(coefficient=0))
    assert agent.model_dump(mode="python") == before


def test_prepare_recipe_requires_explicit_output_binding() -> None:
    """运行复查不按名称猜测配方输出对象。"""
    agent = _agent()
    before = agent.model_dump(mode="python")
    with pytest.raises(BusinessRuleError, match="INVENTORY_RECIPE_OUTPUT_UNBOUND"):
        prepare_inventory_recipe(agent, _context(bind_output=False), _request(), _match())
    assert agent.model_dump(mode="python") == before


def test_prepare_recipe_rejects_insufficient_local_inventory_without_mutation() -> None:
    """输入不足时准备阶段不扣库存也不生成输出。"""
    agent = _agent(held=0.5)
    before = agent.model_dump(mode="python")
    with pytest.raises(BusinessRuleError, match="PRODUCT_NOT_HELD"):
        prepare_inventory_recipe(agent, _context(), _request(), _match())
    assert agent.model_dump(mode="python") == before


def test_apply_recipe_returns_same_state_when_activity_was_already_applied() -> None:
    """重复完成同一活动不再次读取计划或修改库存。"""
    agent = _agent()
    activity = ActivityState(
        activity_id="consume-1",
        agent_id=1,
        kind="consume",
        status="completed",
        started_at=1,
        ended_at=2,
        planned_end_at=2,
        last_settled_at=2,
        elapsed_seconds=1,
        planned_duration_seconds=1,
        origin_action_id="action-1",
        origin_decision_id="decision-1",
        origin_request_event_id="request-1",
        data={"recipe_applied": True},
    )
    result = apply_inventory_recipe(agent, {}, object(), activity)
    assert result is agent
    assert activity.data == {"recipe_applied": True}
