"""`location_kinds` 运行期强制语义测试（docs/dev/SPEC-life-realism.md
「`location_kinds` 的运行期强制语义」，docs/dev/SPEC-life-runtime.md「地点语义与检索分区」）。

判据三条：地点类交集为空即拒绝且原因码是地点不可行（不是资源冲突）；声明
`anywhere` 的条目在任意地点恒可行；地点不可行的条目在体验上应带原因码出现
（原因码与 `life.candidates` 的 `ELIGIBILITY_LOCATION_KIND_MISMATCH` 同一口径）。
"""

from __future__ import annotations

import pytest

from worldmock.errors import BusinessRuleError
from worldmock.life.activities import (
    ActivityOrigin,
    required_location_kinds,
    require_location_kinds,
    start_activity,
)
from worldmock.life.models import (
    ActivityKind,
    AgentState,
    BodyState,
    StartActivityRequest,
)
from worldmock.life.scenario import load_scenario

SCENARIO = "scenarios/white-collar-12-weeks.json"
#: 真实语料里需要 `venue` 与 `home` 的生活活动（corpora/lifestyle/items.json）。
VENUE_ACTIVITY = "3d-printing"
#: 真实语料里只在家可行的生活活动。
HOME_ACTIVITY = "wellness-morning-wash"


def _agent(location_id: str) -> AgentState:
    """@brief 建立足够能量的测试人物（地点可指定）。"""
    return AgentState(agent_id=1, name="worker", location_id=location_id,
                      balance_account_id="agent:1",
                      body=BodyState(energy_kcal=1600, sleep_pressure=0.0,
                                     sleep_deficit_hours=0, entertainment_need=0),
                      next_decision_at=0)


def _origin(name: str) -> ActivityOrigin:
    """@brief 形成可核对的测试动作来源。"""
    return ActivityOrigin(name, f"decision-{name}", f"request-{name}")


def _lifestyle_request(activity_id: str) -> StartActivityRequest:
    """@brief 构造一个生活活动请求（lifestyle 子类型经 target_id 指定条目）。"""
    return StartActivityRequest(activity_type=ActivityKind.LIFESTYLE,
                                planned_duration_seconds=3600, target_id=activity_id)


def test_required_location_kinds_reads_lifestyle_declarations():
    """生活活动的地点类声明来自条目 v2 字段；非生活活动不构成地点要求。"""
    assert required_location_kinds(_lifestyle_request(VENUE_ACTIVITY)) == frozenset(
        {"home", "venue"})
    assert required_location_kinds(_lifestyle_request(HOME_ACTIVITY)) == frozenset({"home"})
    assert required_location_kinds(
        StartActivityRequest(activity_type=ActivityKind.WAIT,
                             planned_duration_seconds=600)) == frozenset()
    assert required_location_kinds(_lifestyle_request("no-such-activity")) == frozenset()


def test_location_kind_mismatch_is_rejected_with_stable_reason_code():
    """地点类交集为空即拒绝，原因码是地点不可行（不是资源冲突）。"""
    request = _lifestyle_request(HOME_ACTIVITY)
    with pytest.raises(BusinessRuleError, match="ELIGIBILITY_LOCATION_KIND_MISMATCH"):
        require_location_kinds("poi-1868478037", request,
                               frozenset({"outdoor", "fitness"}))


def test_matching_location_kind_is_accepted():
    """声明含所在地点类时受理（交集非空即可行）。"""
    request = _lifestyle_request(HOME_ACTIVITY)
    require_location_kinds("home", request, frozenset({"home"}))
    require_location_kinds("poi-1868478037", request, frozenset({"home", "outdoor"}))


def test_anywhere_declaration_is_always_feasible():
    """声明 anywhere 的条目在任意地点恒可行（两侧任一含 anywhere 即放行）。"""
    request = _lifestyle_request(HOME_ACTIVITY)
    require_location_kinds("poi-1868478037", request, frozenset({"anywhere"}))
    anywhere_request = _lifestyle_request(VENUE_ACTIVITY)
    require_location_kinds("poi-1868478037", anywhere_request, frozenset({"venue"}))


def test_undeclared_place_does_not_block():
    """地点未声明地点类时不构成地点限制（未声明不是「哪里都去不了」）。

    缺省解析路径按地点语料取声明；今天地点语料尚无 `location_kinds` 声明，
    因此任何地点都按恒可行放行——这正是「运行期零读者」被补上后的当前实况。
    """
    request = _lifestyle_request("3d-printing")
    require_location_kinds("poi-1868478037", request)


def test_start_activity_rejects_infeasible_lifestyle_activity(monkeypatch):
    """受理路径上地点不可行即拒绝（地点侧声明存在时）。

    今天地点语料尚无 `location_kinds` 声明，因此缺省解析路径一律放行（见
    `test_undeclared_place_does_not_block`）。本用例显式给出地点侧声明，断言
    受理路径确实读它——声明补齐后不需要改受理路径即可生效。
    """
    from worldmock.life import activities as module

    monkeypatch.setattr(module, "place_kinds",
                        lambda *args, **kwargs: {"poi-1868478037": frozenset({"outdoor", "fitness"})})
    scenario = load_scenario(SCENARIO)[0]
    person = _agent("poi-1868478037")
    with pytest.raises(BusinessRuleError, match="ELIGIBILITY_LOCATION_KIND_MISMATCH"):
        start_activity(person, _lifestyle_request(VENUE_ACTIVITY), 100, _origin("a"),
                       scenario.body)


def test_start_activity_accepts_when_declared_place_matches(monkeypatch):
    """地点侧声明与条目侧声明交集非空时受理。"""
    from worldmock.life import activities as module

    monkeypatch.setattr(module, "place_kinds",
                        lambda *args, **kwargs: {"home": frozenset({"home"})})
    scenario = load_scenario(SCENARIO)[0]
    person, activity = start_activity(_agent("home"), _lifestyle_request(VENUE_ACTIVITY),
                                      100, _origin("a"), scenario.body)
    assert person.current_activity_id == activity.activity_id


def test_start_activity_differs_from_resource_conflict():
    """地点不可行与资源冲突必须是两个可区分的原因码。"""
    scenario = load_scenario(SCENARIO)[0]
    busy = _agent("home").model_copy(update={"current_activity_id": "activity-x"}, deep=True)
    with pytest.raises(BusinessRuleError, match="RESOURCE_CONFLICT"):
        start_activity(busy, _lifestyle_request(HOME_ACTIVITY), 100, _origin("a"), scenario.body)


def test_start_activity_accepts_feasible_lifestyle_activity():
    """在声明可行性的地点启动同一条目被受理。"""
    scenario = load_scenario(SCENARIO)[0]
    person, activity = start_activity(_agent("home"), _lifestyle_request(HOME_ACTIVITY),
                                      100, _origin("a"), scenario.body)
    assert activity.target_id == HOME_ACTIVITY
    assert person.current_activity_id == activity.activity_id
