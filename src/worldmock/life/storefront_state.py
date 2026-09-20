"""@brief 管理门店供给的初始事实、当前报价、来源与恢复不变量。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from worldmock.errors import IntegrityError
from worldmock.life.map_models import DataSource
from worldmock.life.models import SupplyQuoteState, WorldState
from worldmock.life.opening_hours import quote_at

if TYPE_CHECKING:
    from worldmock.life.map_models import SupplyConfig
    from worldmock.scheduling.events import ScheduledEvent

_LOGGER = logging.getLogger(__name__)


def quote_state(declaration) -> SupplyQuoteState | None:
    """@brief 将适用声明转换为持久化报价，未定义区间保留空值。"""
    if declaration is None:
        return None
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


def current_quote(state: WorldState, supply_id: str) -> SupplyQuoteState | None:
    """@brief 观察、比较与结算共用世界中的实际报价，过期或尚未生效返回未知。"""
    return effective_quote(state.supply_quotes[supply_id], state.current_time)


def effective_quote(quote: SupplyQuoteState | None, now: int) -> SupplyQuoteState | None:
    """@brief 对只读世界切片执行与权威报价完全相同的半开时段判断。"""
    if quote is None or now < quote.effective_from:
        return None
    if quote.effective_to is not None and now >= quote.effective_to:
        return None
    return quote


def changed_quote(previous: SupplyQuoteState | None, supply: SupplyConfig,
                  event: ScheduledEvent) -> SupplyQuoteState:
    """@brief 调价投影与恢复核验共用版本、来源和声明边界规则，不修改输入状态。"""
    previous = effective_quote(previous, event.due_at)
    if previous is None:
        _fail("Scheduled price change requires an effective quote declaration")
    if previous.price_version == event.data["price_version"]:
        _fail("Scheduled price change must use a new quote version")
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


def expected_supply_quotes(scenario, now: int) -> dict[str, SupplyQuoteState | None]:
    """@brief 从运行起点按真实预排顺序推导报价，用于核验而不覆盖持久化世界。"""
    from worldmock.scheduling.compile import compile_schedule
    from worldmock.scheduling.events import ScheduledKind

    start = int(scenario.start_at.timestamp())
    supplies = {item.supply_id: item for item in scenario.supplies}
    quotes = {identity: quote_state(quote_at(supply, start)) for identity, supply in supplies.items()}
    for event in compile_schedule(scenario, start, int(scenario.end_at.timestamp())):
        if event.due_at > now:
            break
        if event.kind == ScheduledKind.SUPPLY_QUOTE_CHANGE:
            identity = event.data["supply_id"]
            quotes[identity] = quote_state(quote_at(supplies[identity], event.due_at))
        elif event.kind == ScheduledKind.PRICE_CHANGE:
            identity = event.data["supply_id"]
            quotes[identity] = changed_quote(quotes[identity], supplies[identity], event)
    return quotes


def map_provenance(scenario) -> dict:
    """@brief 运行与导出保留固定地图版本、文件身份和来源通知。"""
    dataset = scenario.map_dataset
    return {
        "dataset_id": dataset.dataset_id, "version": dataset.version,
        "coordinate_system": dataset.coordinate_system, "timezone": dataset.timezone,
        "prepared_at": dataset.prepared_at.isoformat(), "source": dataset.source.model_dump(mode="json"),
        "locations_file": dataset.locations_file.model_dump(mode="json"),
        "supplies_file": dataset.supplies_file.model_dump(mode="json"),
        "routes_file": dataset.routes_file.model_dump(mode="json"),
    }


def validate_storefront_state(state: WorldState, scenario, manifest: dict) -> None:
    """@brief 打开和恢复要求地图、库存、报价及人物可见供给与冻结身份一致。"""
    supplies = {item.supply_id for item in scenario.supplies}
    places = {item.location_id for item in scenario.locations}
    if set(state.supply_stock) != supplies or set(state.supply_quotes) != supplies:
        _fail("Runtime supply identities disagree with the frozen map")
    if set(state.opening_exceptions) != places:
        _fail("Runtime opening exception locations disagree with the frozen map")
    if manifest.get("map_dataset") != map_provenance(scenario):
        _fail("Runtime map identity mismatch")
    if state.supply_quotes != expected_supply_quotes(scenario, state.current_time):
        _fail("Runtime supply quotes disagree with the declared quote timeline")
    configurations = {item.agent_id: item for item in scenario.agents}
    for agent in state.agents.values():
        if agent.qualifications != configurations[agent.agent_id].qualifications:
            _fail("Runtime qualifications disagree with the declared initial qualifications")


def _fail(message: str) -> None:
    """@brief 身份损坏使用英文错误并停止恢复。"""
    _LOGGER.error(message)
    raise IntegrityError(message)
