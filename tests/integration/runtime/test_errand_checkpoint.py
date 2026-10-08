"""共同检查点保存未完成的个人事务，恢复后不重复记下、完成与提醒（docs/design/04-simulation-engine.md  末条）。

经真实引擎、FootprintStore 与 SQLite：正式场景建立的 LifeEngine 上，人物以收尾请求记下带提醒钟点的事务，到店买下一份
（累计 1 份）、远程下单的另一份仍在途，此时创建共同检查点并恢复成新的运行分支（engine.checkpoint → store.restore →
LifeEngine.open）。恢复后的世界状态里事务逐字段相同，同一时刻的待办层逐字节相同；父运行与分支各自接着过下去，在途订单
各送达一次、事务各办成一次、提醒各交付一次，分支里没有第二条记下事件，两边检查点之后的事务事件逐条相同。
"""

from __future__ import annotations

from tests.data_paths import ARTIFICIAL_ENVIRONMENT_ROOT
from worldmock.footprints.checkpoint import restore_branch

import json
from pathlib import Path

from tests.engine_fixture import (engine_factory)  # noqa: F401
from tests.life.conftest import (errand_add)  # noqa: F401
from tests.life.routing_fixtures import (
    FOOD_STORE,
    SNACK_ID,
    SNACK_NAME,
    buy_goods,
    current_errand,
    delivery_arrival,
    errand_events,
    errand_note,
    observe,
    place,
    run_until,
    single_person_home,
)
from tests.integration.life.test_errand_reminder import moment
from worldmock.life.engine import LifeEngine

#: 单人白领场景（engine_factory 的缺省场景）。
_SCENARIO = ARTIFICIAL_ENVIRONMENT_ROOT / "scenarios/single-person.json"


def _rendered(layer: dict) -> bytes:
    """待办层渲染成的字节（与观察存储同一种紧凑 JSON 写法）。"""
    return json.dumps(layer, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _after(engine, seq: int) -> list[tuple]:
    """事件序号大于 seq 的人物 1 事务事件：（kind，时刻，载荷）。"""
    return [(event.data["kind"], event.occurred_at, event.data) for event in errand_events(engine)
            if event.event_seq > seq]


def _finish(engine, order_id: str) -> None:
    """接着过日子，直到在途订单送达、事务办成。"""
    run_until(engine, lambda current: current.context.state.deliveries[order_id].status == "delivered"
              and not current.context.state.agents[1].errands)


def test_a_restored_branch_shows_the_same_todo_layer_and_repeats_nothing(
        engine_factory, tmp_path) -> None:  # noqa: F811
    """12:31 记下「取得纯牛奶 2 份」（备注、12:35 提醒），远程下单一份送到家（在途），再到集市店里买下一份（累计 1 份），
    随后创建共同检查点并恢复成分支。分支的事务与父运行逐字段相同，同一时刻的待办层逐字节相同（已取得 1 份、提醒 12:35）。
    父运行与分支各自接着过下去：12:35 各提醒一次，订单送达的那次提交各办成一次；分支只有父运行带来的那一条记下事件和一条
    累计事件，检查点之后两边的事务事件逐条相同（提醒、办成各一条），送达事件各一条。"""
    engine = single_person_home(engine_factory)
    errand_note(engine, "note", [errand_add("囤两份牛奶", SNACK_NAME, quantity=2, memo="店里买一份，再送一份到家",
                                     remind_time="12:35")])
    ordered = buy_goods(engine, "order", app="美团", pay_app="微信")
    [order] = ordered.data["orders"]
    place(engine, 1, FOOD_STORE)
    buy_goods(engine, "buy")
    noted = current_errand(engine)
    assert (noted.progress, noted.remind_at, noted.reminded_at) == (1.0, moment(engine, "2026-01-05 12:35"), None)
    assert engine.context.state.deliveries[order["order_id"]].status == "pending"
    before = observe(engine, "before")["todo_layer"]
    assert [(row["progress"], row["remind_time"]) for row in before["errands"]] == [("已取得 1 份", "12:35")]
    snapshots = {"agent:1": engine.context.store.assets.put_json({"agent_id": 1}, {"kind": "errand_checkpoint_test"})}
    checkpoint = engine.checkpoint(snapshots)
    restore_branch(engine.context.store, checkpoint.checkpoint_id, tmp_path / 'branch').close()
    branch = LifeEngine.open(_SCENARIO, tmp_path / "branch")
    try:
        assert branch.context.state.agents[1].errands == engine.context.state.agents[1].errands
        assert _rendered(observe(branch, "restored")["todo_layer"]) == _rendered(before)
        _finish(engine, order["order_id"])
        _finish(branch, order["order_id"])
        continued = _after(branch, checkpoint.last_event_seq)
        assert [kind for kind, _, _ in continued] == ["errand_reminded", "errand_completed"]
        assert continued == _after(engine, checkpoint.last_event_seq)
        assert [event.data["kind"] for event in errand_events(branch)] == [
            "errand_registered", "errand_progressed", "errand_reminded", "errand_completed"]
        arrival = delivery_arrival(branch, order["order_id"])
        arrivals = [event for event in branch.context.store.read_agent_events("environment_event", 1)
                    if event.data.get("kind") == "delivery_arrival"]
        assert arrivals == [arrival] and continued[-1][2]["errand"]["progress"] == 2.0
        assert "inventory" not in branch.context.state.agents[1].model_dump()
        received = [event for event in branch.context.store.read_agent_events("environment_event", 1)
                    if event.data.get("kind") == "goods_received"]
        assert len(received) == 2 and len({event.data["order_id"] for event in received}) == 2
    finally:
        branch.close()
