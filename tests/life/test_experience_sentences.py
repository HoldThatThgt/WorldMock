"""已核验经历只作私有证据，不写成提示句（docs/dev/SPEC-life-realism.md#persona-prompt-七层请求 第二章「删除与去重」）。

观察的私有证据 data.verified_experiences 保留结构行与来源身份（记忆写入证据依赖它），七层中没有经历句子；零点计划
输入仍用场景的地点、商品与内容名称把经历写成句子（settled_text 的共享写法，例如「14:17 开车到了张江办公室」
「13:57–14:17 吃了番茄炒蛋」），出行方式取自所属出行活动。
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from tests.life.conftest import conclude_pending, stage_day_plan
from tests.life.test_location_sessions import _depart, _preview, _started_ids, _until_ended, _wait
from worldmock.life import projection
from worldmock.life.readable import clock_phrase
from worldmock.life.settled_text import experience_sentence
from worldmock.life.models import ActivityStatus, TravelMode

_PODCAST = "podcast-nasa-space-habitat-excerpt"
_HEX = re.compile(r"[0-9a-f]{8,}")


def _clock(engine, epoch: int) -> str:
    """@brief 场景时区的 HH:MM。"""
    return f"{projection.local_datetime(epoch, engine.context.scenario.timezone):%H:%M}"


def _place(engine, location_id: str) -> str:
    """@brief 场景地点名。"""
    return next(item.name for item in engine.context.scenario.locations if item.location_id == location_id)


def test_observation_keeps_experiences_private_and_out_of_the_layers(engine_factory) -> None:
    """真实观察：私有窗口保留等待与到达两条结构行与来源身份；七层不含经历句子与来源身份；同一窗口写成的零点计划句子
    逐条对应、同序，等待带起止时刻，到达带方式与地点名。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _wait(engine, "sentence-wait")
    _until_ended(engine, waited.activity_id, "sentence-wait")
    conclude_pending(engine, "sentence-wait-end")
    travel_id = _started_ids(_depart(engine, "sentence-walk"))[0]
    _until_ended(engine, travel_id, "sentence-walk")
    observed = _preview(engine, "sentences")
    rows = observed["verified_experiences"]
    inputs = SimpleNamespace(scenario=engine.context.scenario, state=engine.context.state, rules=engine.context.rules)
    sentences = [experience_sentence(inputs, row) for row in rows]
    assert [row["kind"] for row in rows] == ["waited", "arrived"]
    assert len(sentences) == len(rows)
    wait_row, arrival_row = rows
    assert sentences[0].startswith(
        f"{_clock(engine, wait_row['interval_start'])}–{_clock(engine, wait_row['interval_end'])} 等待了")
    assert sentences[1] == f"{_clock(engine, arrival_row['occurred_at'])} 走路到了{_place(engine, 'office')}"
    for row, sentence in zip(rows, sentences):
        assert row["activity_id"] not in sentence and row["source_event_id"] not in sentence
        assert not _HEX.search(sentence) and "office" not in sentence
    rendered = json.dumps([observed[key] for key in ("tool_layer", "todo_layer", "environment_layer",
                                                     "memory_layer", "progress_layer")], ensure_ascii=False)
    assert sentences[1] not in rendered and arrival_row["source_event_id"] not in rendered
    assert engine.context.state.activities[travel_id].status == ActivityStatus.COMPLETED
    assert conclude_pending(engine, "sentence-walk-end").status == "completed"


def test_every_experience_kind_is_written_as_a_sentence_without_identities(engine_factory) -> None:
    """四种经历的句式：等待与食用、播放写起止时刻（跨日加「M月D日」），播客写「听了」、其余内容写「看了」，开车与到达
    被拒写明。"""
    engine = engine_factory()
    stage_day_plan(engine)
    travel_id = _started_ids(_depart(engine, "kinds-walk"))[0]
    driving = engine.context.state.activities[travel_id].model_copy(update={"travel_mode": TravelMode.DRIVING}, deep=True)
    state = engine.context.state.model_copy(update={"activities": {**engine.context.state.activities, "drive": driving}})
    now = engine.context.state.current_time
    product = engine.context.scenario.products[0]
    film = next(item for item in engine.context.scenario.contents if item.kind == "feature_film")
    common = {"agent_id": 1, "source_event_id": "event-0123456789abcdef", "event_seq": 1, "occurred_at": now}
    facts = [
        {**common, "kind": "waited", "activity_id": "wait", "interval_start": now - 600, "interval_end": now,
         "duration_seconds": 600, "reason": "wait"},
        {**common, "kind": "consumed", "activity_id": "eat", "product_id": product.product_id,
         "location_id": "home", "interval_quantity": 1.0, "cumulative_quantity": 1.0,
         "interval_start": now - 1200, "interval_end": now},
        {**common, "kind": "played", "activity_id": "listen", "content_id": _PODCAST,
         "position_before_seconds": 0, "position_after_seconds": 60, "interval_start": now - 60, "interval_end": now},
        {**common, "kind": "played", "activity_id": "watch", "content_id": film.item_id,
         "position_before_seconds": 0, "position_after_seconds": 60, "interval_start": now - 60, "interval_end": now},
        {**common, "kind": "arrived", "activity_id": "drive", "location_id": "office", "access_status": "allowed"},
        {**common, "kind": "arrived", "activity_id": travel_id, "location_id": "office", "access_status": "denied"},
    ]
    titles = engine.context.rules.content_titles
    inputs = SimpleNamespace(scenario=engine.context.scenario, state=state, rules=engine.context.rules)
    def clock(epoch: int) -> str:
        """@brief 共享句式里的钟点：与此刻不在同一本地日期时带「M月D日」。"""
        return clock_phrase(epoch, now, engine.context.scenario.timezone)

    span = f"{clock(now - 600)}–{clock(now)}"
    office = _place(engine, "office")
    assert [experience_sentence(inputs, fact) for fact in facts] == [
        f"{span} 等待了约 10 分钟",
        f"{clock(now - 1200)}–{clock(now)} 吃了{product.name}",
        f"{clock(now - 60)}–{clock(now)} 听了《{titles[_PODCAST]}》",
        f"{clock(now - 60)}–{clock(now)} 看了《{titles[film.item_id]}》",
        f"{clock(now)} 开车到了{office}",
        f"{clock(now)} 走路到了{office}，但没能进去",
    ]
