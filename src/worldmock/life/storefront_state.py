"""@brief 管理门店供给的初始事实、当前报价、来源与恢复不变量。"""

from __future__ import annotations
from worldmock.scheduling.events import ScheduledKind

import logging
from typing import TYPE_CHECKING

from worldmock.errors import IntegrityError
from worldmock.life.models import DataSource
from worldmock.life.models import WorldState
from worldmock.life.opening_hours import quote_at

if TYPE_CHECKING:
    from worldmock.scheduling.events import ScheduledEvent

_LOGGER = logging.getLogger(__name__)


def quote_state(declaration):
    """@brief 将适用声明转换为持久化报价，未定义区间保留空值。"""
    if declaration is None:
        return None
    from worldmock.life.models import SupplyQuoteState

    end = declaration.effective_to
    return SupplyQuoteState(
        price_fen=declaration.price_fen, price_version=declaration.price_version,
        effective_from=int(declaration.effective_from.timestamp()),
        effective_to=int(end.timestamp()) if end is not None else None,
        source=declaration.source.model_dump(mode="json"))


def initial_supply_state(scenario, now: int) -> dict:
    """@brief 初始库存和报价按供给身份建立，营业覆盖单独保存世界事实。"""
    return {
        "supply_stock": {item.supply_id: item.initial_stock for item in scenario.supplies},
        "supply_quotes": {item.supply_id: quote_state(quote_at(item, now)) for item in scenario.supplies},
        "opening_exceptions": {item.location_id: {} for item in scenario.locations},
    }


def current_quote(state: WorldState, supply_id: str):
    """@brief 观察、比较与结算共用世界中的实际报价，过期或尚未生效返回未知。"""
    return effective_quote(state.supply_quotes[supply_id], state.current_time)


def effective_quote(quote, now: int):
    """@brief 对只读世界切片执行与权威报价完全相同的半开时段判断。"""
    if quote is None or now < quote.effective_from:
        return None
    if quote.effective_to is not None and now >= quote.effective_to:
        return None
    return quote


def changed_quote(previous, supply, event: ScheduledEvent, product):
    """@brief 调价投影与恢复核验共用版本、来源和声明边界规则，不修改输入状态。"""
    from worldmock.life.models import SupplyQuoteState

    previous = effective_quote(previous, event.due_at)
    if previous is None:
        _fail("Scheduled price change requires an effective quote declaration")
    if previous.price_version == event.data["price_version"]:
        _fail("Scheduled price change must use a new quote version")
    validate_price_bounds(event.data["amount_fen"], product)
    source = DataSource.model_validate(event.data["source"])
    if source.kind != "scenario":
        _fail("Scheduled supply changes require a scenario source")
    boundaries = [int(quote.effective_from.timestamp()) for quote in supply.quotes
                  if quote.effective_from.timestamp() > event.due_at]
    if previous.effective_to is not None:
        boundaries.append(previous.effective_to)
    return SupplyQuoteState(
        price_fen=event.data["amount_fen"], price_version=event.data["price_version"],
        effective_from=event.due_at, effective_to=min(boundaries) if boundaries else None,
        source=source.model_dump(mode="json"))


def expected_supply_quotes(scenario, now: int) -> dict:
    """@brief 从运行起点按真实预排顺序推导报价，用于核验而不覆盖持久化世界。"""
    from worldmock.scheduling.compile import compile_schedule

    start = int(scenario.start_at.timestamp())
    supplies = {item.supply_id: item for item in scenario.supplies}
    products = {item.product_id: item for item in scenario.products}
    quotes = {identity: quote_state(quote_at(supply, start)) for identity, supply in supplies.items()}
    for event in compile_schedule(scenario, start, int(scenario.end_at.timestamp())):
        if event.due_at > now:
            break
        if event.kind == ScheduledKind.SUPPLY_QUOTE_CHANGE:
            identity = event.data["supply_id"]
            quotes[identity] = quote_state(quote_at(supplies[identity], event.due_at))
        elif event.kind == ScheduledKind.PRICE_CHANGE:
            identity = event.data["supply_id"]
            supply = supplies[identity]
            quotes[identity] = changed_quote(quotes[identity], supply, event, products[supply.product_id])
    return quotes


def validate_storefront_state(state: WorldState, scenario, manifest: dict) -> None:
    """@brief 打开和恢复核对既有库存、报价及人物可见供给，环境身份由运行身份入口核对。"""
    supplies = {item.supply_id for item in scenario.supplies}
    places = {item.location_id for item in scenario.locations}
    if set(state.supply_stock) != supplies or set(state.supply_quotes) != supplies:
        _fail("Runtime supply identities disagree with the frozen map")
    if set(state.opening_exceptions) != places:
        _fail("Runtime opening exception locations disagree with the frozen map")
    if state.supply_quotes != expected_supply_quotes(scenario, state.current_time):
        _fail("Runtime supply quotes disagree with the declared quote timeline")
    products = {item.product_id: item for item in scenario.products}
    for supply in scenario.supplies:
        quote = state.supply_quotes[supply.supply_id]
        if quote is not None:
            validate_price_bounds(quote.price_fen, products[supply.product_id])
    configurations = {item.agent_id: item for item in scenario.agents}
    for agent in state.agents.values():
        if agent.qualifications != configurations[agent.agent_id].qualifications:
            _fail("Runtime qualifications disagree with the declared initial qualifications")


def _fail(message: str) -> None:
    """@brief 身份损坏使用英文错误并停止恢复。"""
    _LOGGER.error(message)
    raise IntegrityError(message)


def validate_price_bounds(amount: int, product) -> None:
    """@brief 调价和恢复只接受当前商品范围内的整数分，不包括独立配送费。"""
    if type(amount) is not int or not product.purchase_price_fen_min <= amount <= product.purchase_price_fen_max:
        _fail("Supply price is outside its product price bounds")
