"""已核验经历只以句子给模型（docs/dev/SPEC-time-and-energy.md 第七章「发给模型的内容不含内部身份」）。

观察的私有证据 data.verified_experiences 保留结构行与来源身份；facts.experiences 按同样五条窗口与顺序，用场景的
地点、商品与内容名称写成句子（例如「14:17 开车到了张江办公室」「13:57–14:17 吃了番茄炒蛋」），出行方式取自所属出行活动。
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from tests.life.conftest import conclude_pending, stage_day_plan
from tests.life.test_location_sessions import _depart, _preview, _started_ids, _until_ended, _wait
from worldmock.life import projection
from worldmock.life.models import ActivityStatus, TravelMode
from worldmock.life.narration import render_facts

_PODCAST = "podcast-nasa-space-habitat-excerpt"
_HEX = re.compile(r"[0-9a-f]{8,}")


def _clock(engine, epoch: int) -> str:
    """@brief 场景时区的 HH:MM。"""
    return f"{projection.local_datetime(epoch, engine.scenario.timezone):%H:%M}"


def _place(engine, location_id: str) -> str:
    """@brief 场景地点名。"""
    return next(item.name for item in engine.scenario.locations if item.location_id == location_id)


def test_observation_experience_sentences_follow_the_private_window(engine_factory) -> None:
    """真实观察：句子与私有窗口逐条对应、同序，不含任何内部身份；等待带起止时刻，到达带方式与地点名。"""
    engine = engine_factory()
    stage_day_plan(engine)
    waited = _wait(engine, "sentence-wait")
    _until_ended(engine, waited.activity_id, "sentence-wait")
    conclude_pending(engine, "sentence-wait-end")
    travel_id = _started_ids(_depart(engine, "sentence-walk"))[0]
    _until_ended(engine, travel_id, "sentence-walk")
    observed = _preview(engine, "sentences")
    rows, sentences = observed["verified_experiences"], observed["facts"]["experiences"]
    assert [row["kind"] for row in rows] == ["waited", "arrived"]
    assert len(sentences) == len(rows)
    wait_row, arrival_row = rows
    assert sentences[0].startswith(
        f"{_clock(engine, wait_row['interval_start'])}–{_clock(engine, wait_row['interval_end'])} 等待了")
    assert sentences[1] == f"{_clock(engine, arrival_row['occurred_at'])} 走路到了{_place(engine, 'office')}"
    for row, sentence in zip(rows, sentences):
        assert row["activity_id"] not in sentence and row["source_event_id"] not in sentence
        assert not _HEX.search(sentence) and "office" not in sentence
    rendered = render_facts(observed["facts"])
    assert sentences[1] in rendered and arrival_row["source_event_id"] not in rendered
    assert engine.state.activities[travel_id].status == ActivityStatus.COMPLETED
    assert conclude_pending(engine, "sentence-walk-end").status == "completed"


def test_every_experience_kind_is_written_as_a_sentence_without_identities(engine_factory) -> None:
    """四种经历的句式：等待与食用、播放写起止时刻，播客写「听了」、其余内容写「看了」，开车与到达被拒写明。"""
    engine = engine_factory()
    stage_day_plan(engine)
    travel_id = _started_ids(_depart(engine, "kinds-walk"))[0]
    driving = engine.state.activities[travel_id].model_copy(update={"travel_mode": TravelMode.DRIVING}, deep=True)
    state = engine.state.model_copy(update={"activities": {**engine.state.activities, "drive": driving}})
    now = engine.state.current_time
    product = engine.scenario.products[0]
    film = next(item for item in engine.scenario.contents if item.kind == "feature_film")
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
    titles = engine.rules.content_titles
    inputs = SimpleNamespace(scenario=engine.scenario, state=state,
                             extras={"verified_experiences": facts, "content_titles": titles})
    span = f"{_clock(engine, now - 600)}–{_clock(engine, now)}"
    office = _place(engine, "office")
    assert projection._experience_sentences(inputs) == [
        f"{span} 等待了约10分钟",
        f"{_clock(engine, now - 1200)}–{_clock(engine, now)} 吃了{product.name}",
        f"{_clock(engine, now - 60)}–{_clock(engine, now)} 听了《{titles[_PODCAST]}》",
        f"{_clock(engine, now - 60)}–{_clock(engine, now)} 看了《{titles[film.item_id]}》",
        f"{_clock(engine, now)} 开车到了{office}",
        f"{_clock(engine, now)} 走路到了{office}，但没能进去",
    ]
