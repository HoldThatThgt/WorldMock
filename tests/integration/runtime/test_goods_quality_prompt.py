"""到店拿到同一件商品后，人物的实际收尾请求与核验记忆只在这一份的品质上有差异。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from tests.engine_fixture import engine_factory_at
from tests.integration.runtime.current_contract_fixtures import ToolResponse, decide
from tests.life.conftest import conclude_request, context, purchase, stage_day_plan
from tests.life.environment_runtime import act, events, until_ended
from worldmock.agents.prompt.prompt_schemas import validate_layer


def _at_store(persona, config):
    persona["initial_location"] = "四季便利"


async def _receipt_request(create, sample, quality):
    engine = create(mutate=_at_store)
    stage_day_plan(engine, submitted=True)
    _, started = act(engine, "tea", purchase("柠檬茶"))
    shop = engine.context.catalog.locations["store"]
    before = engine.observe(context(engine, "in-progress"))["progress_layer"]
    quoted = next(row["result"]["details"]["purchases"][0] for row in before["events"] if row["type"] == "tool_call")
    assert (quoted["store"], quoted["rating"]) == (shop.display_name, shop.rating)
    assert not any(row["type"] == "receipt" for row in before["events"])
    assert not ({"quality", "weather_delay_seconds", "late_by_seconds"} & quoted.keys())
    with patch.object(Random, "gauss", return_value=sample) as draw:
        until_ended(engine, started.activity_id)
    draw.assert_called_once_with(shop.rating, 1)
    activity = engine.context.state.activities[started.activity_id]
    conclude = conclude_request(activity.name).model_dump(mode="json")
    observation, [request], _, [result] = await decide(engine, "after", [ToolResponse("conclude_activity", conclude)])
    assert result.status == "completed"
    captured = next(event.data["request"] for event in events(engine, "model_request")
                    if event.decision_id == "decision-after-1")
    assert captured["messages"] == request["messages"]
    progress = next(layer for layer in json.loads(request["messages"][1]["content"]) if layer["layer"] == "进度层")
    validate_layer(progress)
    [receipt] = [row for row in progress["events"] if row["type"] == "receipt"]
    assert (receipt["store"], receipt["product"], receipt["quality"]) == (shop.display_name, "柠檬茶", quality)
    [source] = [event for event in events(engine, "environment_event") if event.data["kind"] == "goods_received"]
    [fact] = [entry for entry in observation["verified_experiences"] if entry["kind"] == "received"]
    assert (fact["order_id"], fact["source_event_id"], fact["store_id"], fact["quality"]) == (
        source.data["order_id"], source.event_id, shop.location_id, quality)
    assert source.event_id in observation["progress_event_ids"]
    after = engine.observe(context(engine, "remember"))
    lines = [line for entry in after["memory_layer"]["今天"] for line in entry.get("核验", [])]
    assert any(f"{shop.display_name} · 柠檬茶：这份品质{quality}" in line for line in lines)
    assert not any(row["type"] == "receipt" for row in after["progress_layer"]["events"])
    return request


async def receipt_prompt_pair(create):
    """从真实收货与宿主决策生成同历史的好货、坏货两份待收尾输入。"""
    good = await _receipt_request(create, 4.0, "好")
    bad = await _receipt_request(create, 2.0, "坏")
    good_text = good["messages"][1]["content"]
    assert good_text.replace('"quality":"好"', '"quality":"坏"') == bad["messages"][1]["content"]
    assert good["tools"] == bad["tools"] and good["messages"][0] == bad["messages"][0]
    return {"good": good, "bad": bad}


@pytest.mark.asyncio
async def test_real_closure_model_requests_distinguish_the_receipts(engine_factory):
    await receipt_prompt_pair(engine_factory)


def save_requests(directory: Path) -> None:
    """复用上述同一份决策输入生成器，保存实际模型对照样例的请求。"""
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as temp, engine_factory_at(Path(temp), None) as create:
        pair = asyncio.run(receipt_prompt_pair(create))
    for name, request in pair.items():
        (directory / f"{name}-request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    import sys

    save_requests(Path(sys.argv[1]))
