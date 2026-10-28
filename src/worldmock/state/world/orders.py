"""@brief 共享状态与接口数据的独立定义。"""


from __future__ import annotations
from worldmock.exchange_engine.definitions.types import GoodsCategory
from worldmock.foundation.models import StrictModel
from typing import Literal
from pydantic import Field, model_validator

from worldmock.state.world.kinds import FulfillmentKind
from worldmock.state.logging import _LOGGER


class DeliveryState(StrictModel):
    """@brief 商品履约记录：一份商品一条，保存它的配送组、所属订单的付款、配送与恰好一次收货状态。

    一次购买里 app_id 相同的记录是一张订单，付款字段（payment_app_id、payment_duration_seconds、
    payment_started_at、payment_ended_at）在同一张订单的记录上取值相同；group_key 相同的记录是一个配送组。
    """

    order_id: str = Field(min_length=1)
    agent_id: int = Field(gt=0)
    goods_id: str = Field(min_length=1)
    category: GoodsCategory
    store_id: str | None
    #: 下单的内容 app；到店当场买为空。同一次购买里取值相同的记录是一张订单。
    app_id: str | None
    #: 这张订单抽到的支付 app；空表示直付。
    payment_app_id: str | None
    #: 配送组的键，运费按它合并。
    group_key: str = Field(min_length=1)
    #: 这张订单的付款时长：app_id 与 payment_app_id 都为空的到店直付为 0，其余为 10 至 20 秒。
    payment_duration_seconds: int = Field(strict=True, ge=0, le=20)
    #: 付款段真实开始的时刻；付款时长为 0 的订单恒为空。
    payment_started_at: int | None = Field(default=None, ge=0)
    #: 付款段真实走完的时刻；付款时长为 0 的订单恒为空，被截断的付款段也为空。
    payment_ended_at: int | None = Field(default=None, ge=0)
    quantity: int = Field(strict=True, ge=1, le=1)
    unit_price_fen: int = Field(gt=0)
    #: 运费：每个配送组的第一条记录承担整组运费，其余为 0。
    delivery_fee_fen: int = Field(ge=0)
    total_price_fen: int = Field(ge=0)
    destination_id: str = Field(min_length=1)
    delivery_address_id: str = Field(min_length=1)
    fulfillment: FulfillmentKind
    created_at: int = Field(ge=0)
    #: 真实扣款时刻：一次购买的全部记录同时写入，等于购买完成的时刻。
    paid_at: int | None = Field(default=None, ge=0)
    #: 预计到手或到达的时刻：到店记录是购买开始时刻加实际购买时长，外卖和快递是受理时冻结的预计到达。
    expected_at: int = Field(ge=0)
    arrived_at: int | None = Field(default=None, ge=0)
    received_at: int | None = Field(default=None, ge=0)
    status: Literal["cancelled", "pending", "ready_for_pickup", "delivered"]
    path: tuple[str, ...]
    trip_id: str | None
    distance_meters: int = Field(ge=0)
    prep_seconds: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_payment_phase(self) -> "DeliveryState":
        """@brief 付款时长为 0 只属于到店直付，其余订单为 10 至 20 秒；没有付款段的记录不带付款时刻。"""
        seconds = self.payment_duration_seconds
        direct_at_store = self.app_id is None and self.payment_app_id is None
        valid = seconds == 0 if direct_at_store else 10 <= seconds <= 20
        if not valid:
            _LOGGER.error("Order payment duration disagrees with its payment method")
            raise ValueError("Order payment duration disagrees with its payment method")
        if seconds == 0 and (self.payment_started_at is not None or self.payment_ended_at is not None):
            _LOGGER.error("Order without a payment phase carries payment times")
            raise ValueError("Order without a payment phase carries payment times")
        return self

    @model_validator(mode="after")
    def validate_receipt_time(self) -> "DeliveryState":
        """@brief 未支付和取消无收货，已送达具有真实付款及收货时刻。"""
        shape = (self.arrived_at is not None, self.received_at is not None)
        required = {"cancelled": (False, False), "pending": (False, False),
                    "ready_for_pickup": (True, False), "delivered": (True, True)}
        paid_required = self.status in {"ready_for_pickup", "delivered"}
        invalid_payment = (paid_required and self.paid_at is None
                           or self.status == "cancelled" and self.paid_at is not None)
        if shape != required[self.status] or invalid_payment:
            _LOGGER.error("Order status and payment or receipt timestamp disagree")
            raise ValueError("Order status and payment or receipt timestamp disagree")
        if self.paid_at is not None and self.paid_at < self.created_at:
            _LOGGER.error("Order payment precedes creation")
            raise ValueError("Order payment precedes creation")
        if self.arrived_at is not None and self.arrived_at < self.paid_at:
            _LOGGER.error("Order arrival precedes payment")
            raise ValueError("Order arrival precedes payment")
        if self.received_at is not None and self.received_at < self.arrived_at:
            _LOGGER.error("Order receipt precedes arrival")
            raise ValueError("Order receipt precedes arrival")
        if self.status == "ready_for_pickup" and self.fulfillment != FulfillmentKind.STATION_PICKUP:
            _LOGGER.error("Only station pickup orders may be ready for pickup")
            raise ValueError("Only station pickup orders may be ready for pickup")
        return self
