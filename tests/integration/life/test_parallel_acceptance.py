"""@brief 当前目录动作与原生行为的组合准入和共同结算验收。"""

import pytest

from tests.life.conftest import advance_next, stage_day_plan
from tests.integration.life.test_acs_amended_runtime import perform
from tests.integration.life.test_acs_runtime import request
from worldmock.catalog.affordances import engine_affordances
from worldmock.life.models import StartActivityRequest


def _engine(engine_factory):
    """@brief 声明通话联系人使两个独立数字动作真实争用手机。"""
    def contacts(document):
        document["agents"][0]["contacts"] = [{"contact_id": "colleague", "salutation": "同事", "relation": "同事",
            "closeness": 16, "channels": ["phone"], "reply_policy": "none"}]
    engine = engine_factory(mutate=contacts)
    stage_day_plan(engine)
    return engine


def _pair(partner):
    """@brief 请求明确声明实际动作、应用和社交对象，不猜测默认应用。"""
    partners = {"wait": request("自己", None, "等待"), "do": request("安静休息", None),
        "travel_to": request("公司", None, "步行"), "sleep": request("自己", None, "睡觉"),
        "use_app": request("语音通话", "微信", to="联系人：同事")}
    return StartActivityRequest(items=[*request().items, *partners[partner].items], answering=None)


def _claims(activity):
    """@brief 实际活动的种类、类别、方式通过唯一占用表计算。"""
    return engine_affordances().resource_claims(activity.kind.value, travel_mode=activity.travel_mode.value if activity.travel_mode else None,
        category=activity.data.get("category"), manner=activity.data.get("manner"))


@pytest.mark.parametrize("partner", ["wait", "do", "travel_to"])
def test_app_can_share_time_with_compatible_activity(engine_factory, partner):
    """@brief 手机娱乐与等待、日常照护或步行占用不重合，各自保存活动身份。"""
    engine = _engine(engine_factory)
    result = perform(engine, f"app-{partner}", _pair(partner))
    assert result.status == "in_progress", result.data
    activities = [item for item in engine.context.state.activities.values() if item.origin_action_id == result.action_id]
    assert len(activities) == 2 and len({item.activity_id for item in activities}) == 2
    assert all(item.status == "active" for item in activities)
    claims = [_claims(item) for item in activities]
    assert claims[0] == {"entertainment", "use_app"} and not claims[0] & claims[1]
    assert [frozenset(item.resource_claims) for item in activities] == claims
    assert len(result.data["activities"]) == 2


@pytest.mark.parametrize("partner", ["sleep", "use_app"])
def test_incompatible_pair_is_rejected_without_partial_state(engine_factory, partner):
    """@brief 睡眠或另一段手机通话争用占用，整组拒绝不改活动、账户及会话。"""
    engine = _engine(engine_factory)
    before = engine.context.state.model_dump(mode="json")
    result = perform(engine, f"app-{partner}-conflict", _pair(partner))
    assert result.status == "rejected" and result.error_code == "RESOURCE_CONFLICT", result.data
    assert result.data["reason"] and result.data["next_step"] == "Choose only one of the conflicting activities."
    assert result.data["unmet"] == ["resource_claims"]
    assert engine.context.state.activities == {}
    sessions = list(engine.context.state.sessions.values())
    assert [(item.kind, item.status, item.activity_ids) for item in sessions] == [("stay", "active", [])]
    assert engine.context.state.model_dump(mode="json")["sessions"] == before["sessions"]
    assert engine.context.state.accounts == before["accounts"]


def test_parallel_progress_uses_one_shared_body_interval(engine_factory):
    """@brief 两个实际活动分别写进度，同一时间段只结算一笔身体。"""
    engine = _engine(engine_factory)
    started = perform(engine, "shared-body", _pair("wait"))
    assert started.status == "in_progress", started.data
    before = engine.context.store.last_event_seq
    advance_next(engine)
    events = [item for item in engine.context.store.read_events(engine.context.store.last_event_seq)
              if item.event_seq > before]
    progress = [item for item in events if item.event_type == "activity_progress" and item.action_id == started.action_id]
    assert len(progress) == 2
    assert len({item.data["body_settlement_event_id"] for item in progress}) == 1
    body = [item for item in events if item.data.get("kind") == "body_settlement" and item.agent_id == 1]
    assert len(body) == 1
